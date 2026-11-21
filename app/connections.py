"""Provider connections + BYOK lifecycle (Master Build V3 Part XXII).

Golden rule (§106): a user's key is visible only at create/rotate time.
After that the API returns masked metadata only; the plaintext is
decrypted server-side for a single gateway call and never lands in
events, logs, artifacts or durable task payloads.

Connection states (§105.2): draft → validating → active | degraded |
auth_failed | rate_limited | disabled | revoked | deleted.
Revocation crypto-shreds the stored secret. Every lifecycle change is
written to the tenant audit log (without secret material).
"""
from __future__ import annotations

import uuid
from typing import Optional

from .gateway_client import GatewayClient, GatewayError
from .llm_adapter import _is_local_endpoint
from .netpolicy import UrlPolicyError, validate_endpoint_url
from .store import Store, now_iso
from .vault import Vault, mask_secret

CONNECTION_KINDS = ("omniroute_gateway", "direct_provider", "local_endpoint",
                    "whatsapp")
# local_endpoint: no key required (e.g. a local Ollama); others require one.
# "whatsapp" (v18) is NOT creatable through the generic connections API:
# it is minted only by the Embedded Signup exchange
# (app/whatsapp_onboard.py), which verifies the token's scopes against
# Meta before storing it. Its metadata (WABA id, phone number id,
# display number) rides in data_policy["whatsapp"], and model-catalog
# sync skips the kind entirely.
KEY_REQUIRED = {"omniroute_gateway": True, "direct_provider": True,
                "local_endpoint": False, "whatsapp": True}

STATE_TRANSITIONS = {
    "draft": {"validating", "disabled", "revoked"},
    "validating": {"active", "auth_failed", "rate_limited", "degraded", "draft"},
    "active": {"validating", "degraded", "auth_failed", "rate_limited", "disabled", "revoked"},
    "degraded": {"validating", "active", "disabled", "revoked"},
    "auth_failed": {"validating", "disabled", "revoked"},
    "rate_limited": {"validating", "active", "disabled", "revoked"},
    "disabled": {"validating", "active", "revoked"},
    "revoked": set(),
}


def new_connection_id() -> str:
    return "conn_" + uuid.uuid4().hex[:12]


LOCAL_TARGET_HOSTED_CATEGORY = "local_target_in_hosted_mode"


def _hosted_mode() -> bool:
    """Accounts/hosted deployment? The mode-aware policy switch for
    audit F7, read fresh from the environment (the channel_config
    precedent in app/telegram.py) so it follows the deployment."""
    from .config import load_settings
    return load_settings().auth_mode == "accounts"


def _url_is_local(url: str, kind: str = "direct_provider") -> bool:
    """The netpolicy locality verdict for a stored URL. A stored
    connection passed validation at create; if re-validation itself
    fails now, locality is not the refusal to invent — return False
    and let the probe surface the real problem."""
    try:
        return bool(validate_endpoint_url(url, kind=kind)["is_local"])
    except UrlPolicyError:
        return False


def _refuse_local_in_hosted(checked_is_local: bool) -> None:
    """Audit F7: in accounts/hosted mode a connection URL targeting
    loopback/LAN makes validate/sync/self-test an internal-service
    oracle (distinct states + latency + model lists map the host's
    internal HTTP services). The v21.3 locality allowance stands in
    the local product — a user's own Ollama IS the point there —
    but hosted tenants never get to aim the server at itself. No
    tenant-admin exception: connection creation is already
    admin-gated, and a tenant admin is exactly who the oracle
    would serve."""
    if checked_is_local and _hosted_mode():
        raise UrlPolicyError(
            LOCAL_TARGET_HOSTED_CATEGORY,
            "loopback/LAN connection targets are not allowed in "
            "accounts (hosted) mode")


_HEADER_NAME_OK = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
MAX_CUSTOM_HEADERS = 20


def validate_custom_headers(raw) -> dict:
    """Per-connection custom headers (2026-10-23, fix-list item 5).

    A small string map, validated like an HTTP header block must
    be: token names, printable values, no CR/LF (a value must never
    smuggle a second header into a request), bounded count/size.
    Values are SECRETS in the same sense as the API key: they are
    stored on the connection record (itself inside the encrypted
    data home), never written to audit logs or events, and the
    public view only ever shows them masked.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("headers must be an object of name: value pairs")
    if len(raw) > MAX_CUSTOM_HEADERS:
        raise ValueError(f"at most {MAX_CUSTOM_HEADERS} custom headers")
    out = {}
    for name, value in raw.items():
        name = str(name).strip()
        value = str(value).strip()
        if not name or any(c not in _HEADER_NAME_OK for c in name):
            raise ValueError(f"invalid header name: {name!r}")
        if not value or len(value) > 512 or any(
                ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"invalid value for header {name!r}")
        out[name] = value
    return out


def public_view(conn: dict) -> dict:
    """The only representation of a connection that may leave the server."""
    return {
        "id": conn["id"], "kind": conn["kind"], "provider_key": conn["provider_key"],
        "label": conn["label"], "base_url": conn["base_url"], "state": conn["state"],
        "masked_key": conn.get("masked_key", ""), "allowed_models": conn.get("allowed_models", []),
        "headers_masked": {k: mask_secret(v)
                           for k, v in (conn.get("headers") or {}).items()},
        "blocked_models": conn.get("blocked_models", []),
        "budget_limit_usd": conn.get("budget_limit_usd"),
        "routing_mode": conn.get("routing_mode", "balanced"),
        "data_policy": conn.get("data_policy", {}),
        "last_validated_at": conn.get("last_validated_at"),
        "last_error_category": conn.get("last_error_category"),
        "models_count": conn.get("models_count", 0),
        "created_at": conn["created_at"], "updated_at": conn["updated_at"],
    }


def create_connection(store: Store, vault: Vault, tenant_id: str, user_id: str,
                      payload: dict) -> dict:
    kind = payload["kind"]
    if kind not in CONNECTION_KINDS:
        raise ValueError(f"unknown connection kind: {kind}")
    checked = validate_endpoint_url(payload["base_url"], kind=kind)
    _refuse_local_in_hosted(checked["is_local"])
    api_key = (payload.get("api_key") or "").strip()
    if KEY_REQUIRED[kind] and not api_key:
        raise ValueError(f"an API key is required for {kind} connections")
    conn = {
        "id": new_connection_id(), "tenant_id": tenant_id, "owner_user_id": user_id,
        "kind": kind, "provider_key": payload.get("provider_key") or kind,
        "label": payload.get("label", kind), "base_url": checked["url"],
        "state": "draft", "masked_key": mask_secret(api_key),
        "headers": validate_custom_headers(payload.get("headers")),
        "allowed_models": payload.get("allowed_models", []),
        "blocked_models": payload.get("blocked_models", []),
        "budget_limit_usd": payload.get("budget_limit_usd"),
        "routing_mode": payload.get("routing_mode", "balanced"),
        "data_policy": payload.get("data_policy", {}),
        "last_validated_at": None, "last_error_category": None, "models_count": 0,
        "created_at": now_iso(), "updated_at": now_iso(), "deleted_at": None,
    }
    secret = None
    if api_key:
        version, token = vault.encrypt(api_key)
        secret = {"key_version": version, "ciphertext": token}
    store.put_connection(tenant_id, conn, secret)
    store.audit(tenant_id, user_id, "GatewayConnectionCreated", "connection", conn["id"],
                {"kind": kind, "base_url": checked["url"]})
    return public_view(conn)


def get_secret_for_call(store: Store, vault: Vault, tenant_id: str,
                        connection_id: str) -> str:
    """Server-only: decrypt a connection's secret for one gateway call.
    Raises for revoked/shredded/missing secrets — callers must treat that
    as a hard stop, never as 'use another key instead'."""
    row = store.get_secret(tenant_id, connection_id)
    if not row or not row["ciphertext"] or row["key_version"] == "shredded":
        conn = store.get_connection(tenant_id, connection_id)
        if conn and conn["kind"] == "local_endpoint":
            return ""  # keyless local endpoint
        raise GatewayError("auth_failed", "connection has no usable credential")
    return vault.decrypt(row["key_version"], row["ciphertext"])


def _set_state(store: Store, tenant_id: str, conn: dict, state: str,
               error_category: Optional[str] = None) -> dict:
    allowed = STATE_TRANSITIONS.get(conn["state"], set())
    if state != conn["state"] and state not in allowed:
        raise ValueError(f"illegal connection transition {conn['state']} -> {state}")
    conn["state"] = state
    conn["last_error_category"] = error_category
    conn["updated_at"] = now_iso()
    store.put_connection(tenant_id, conn)
    return conn


async def validate_connection(store: Store, vault: Vault, tenant_id: str, user_id: str,
                              connection_id: str, client: Optional[GatewayClient] = None) -> dict:
    """Bounded validation: a model-list call only — no billable generation
    (§110.6). On success the connection becomes active and the catalog is
    refreshed by the caller."""
    conn = store.get_connection(tenant_id, connection_id)
    if not conn:
        raise KeyError(connection_id)
    if _hosted_mode() and _url_is_local(conn["base_url"], conn["kind"]):
        # Audit F7: never even probe an internal target in hosted
        # mode — the probe itself is the oracle. Mark the connection
        # degraded with the policy category (best-effort: a state the
        # machine won't leave, e.g. revoked, keeps its state and the
        # refusal still stands).
        try:
            _set_state(store, tenant_id, conn, "validating")
            _set_state(store, tenant_id, conn, "degraded",
                       error_category=LOCAL_TARGET_HOSTED_CATEGORY)
        except ValueError:
            pass
        store.audit(tenant_id, user_id, "GatewayConnectionDegraded",
                    "connection", connection_id,
                    {"category": LOCAL_TARGET_HOSTED_CATEGORY})
        return public_view(conn)
    _set_state(store, tenant_id, conn, "validating")
    try:
        secret = get_secret_for_call(store, vault, tenant_id, connection_id)
        gw = client or GatewayClient(base_url=conn["base_url"], api_key=secret, timeout=30,
                                     connection_id=conn["id"],
                                     headers=conn.get("headers") or {})
        models = await gw.list_models()
        if not models and (conn["kind"] == "local_endpoint"
                           or _is_local_endpoint(conn["base_url"])):
            # Ollama-native tag list for local servers whose /v1/models
            # is disabled — keyed off the stored kind OR a local URL
            # (v21.3: a BYOK-kind connection to a local server too).
            models = await gw.list_models_ollama_native()
    except GatewayError as exc:
        state = {"auth_failed": "auth_failed", "rate_limited": "rate_limited"}.get(
            exc.category, "degraded")
        _set_state(store, tenant_id, conn, state, error_category=exc.category)
        store.audit(tenant_id, user_id, "GatewayConnectionDegraded", "connection",
                    connection_id, {"category": exc.category})
        return public_view(conn)
    except Exception:
        # Audit B7: the secret fetch and client build used to sit
        # OUTSIDE the GatewayError guard — a vault/secret-layer
        # failure escaped with the connection left in "validating",
        # a state no surface picks up (not testable, not pinnable,
        # not selectable) until a human re-validated. Any failure in
        # this segment now lands the connection in a terminal state
        # before the error propagates to the caller.
        try:
            _set_state(store, tenant_id, conn, "degraded",
                       error_category="validation_error")
        except Exception:
            pass  # the state write must never mask the real failure
        raise
    conn["models_count"] = len(models)
    conn["last_validated_at"] = now_iso()
    _set_state(store, tenant_id, conn, "active")
    store.audit(tenant_id, user_id, "GatewayConnectionValidated", "connection",
                connection_id, {"models": len(models)})
    return public_view(conn)


def rotate_connection(store: Store, vault: Vault, tenant_id: str, user_id: str,
                      connection_id: str, new_api_key: str) -> dict:
    conn = store.get_connection(tenant_id, connection_id)
    if not conn:
        raise KeyError(connection_id)
    version, token = vault.encrypt(new_api_key.strip())
    store.put_secret(tenant_id, connection_id, version, token)
    conn["masked_key"] = mask_secret(new_api_key.strip())
    if conn["state"] in ("auth_failed", "revoked"):
        conn["state"] = "draft"  # must re-validate before use
    conn["updated_at"] = now_iso()
    store.put_connection(tenant_id, conn)
    store.audit(tenant_id, user_id, "CredentialRotated", "connection", connection_id, {})
    return public_view(conn)


def revoke_connection(store: Store, tenant_id: str, user_id: str, connection_id: str) -> dict:
    conn = store.get_connection(tenant_id, connection_id)
    if not conn:
        raise KeyError(connection_id)
    _set_state(store, tenant_id, conn, "revoked")
    store.shred_secret(tenant_id, connection_id)
    store.audit(tenant_id, user_id, "GatewayConnectionRevoked", "connection", connection_id, {})
    return public_view(conn)


def delete_connection(store: Store, tenant_id: str, user_id: str, connection_id: str) -> None:
    conn = store.get_connection(tenant_id, connection_id)
    if not conn:
        raise KeyError(connection_id)
    store.shred_secret(tenant_id, connection_id)
    conn["deleted_at"] = now_iso()
    conn["state"] = "revoked"
    conn["updated_at"] = now_iso()
    store.put_connection(tenant_id, conn)
    # A deleted connection's models must not outlive it in /v1/models
    # (the Models table renders catalog rows unfiltered), and combos
    # pointing at it must stop being offered. Catalog rows are hard-
    # deleted: deprecating would still surface them. Combos are
    # pruned, not silently destroyed: a combo left with no targets is
    # disabled (the picker only offers enabled combos, and
    # _resolve_combo already refuses a disabled combo honestly
    # instead of substituting another model), so the user can see it
    # and delete or repair it.
    store.delete_catalog_for_connection(tenant_id, connection_id)
    for combo in store.list_combos(tenant_id):
        targets = [t for t in combo.get("targets", [])
                   if t.get("connection_id") != connection_id]
        if len(targets) != len(combo.get("targets", [])):
            combo["targets"] = targets
            if not targets:
                combo["enabled"] = False
            store.put_combo(tenant_id, combo)
    store.audit(tenant_id, user_id, "GatewayConnectionDeleted", "connection", connection_id, {})
