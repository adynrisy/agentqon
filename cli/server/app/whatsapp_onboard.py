"""AgentQon — WhatsApp self-serve onboarding (v18, Embedded Signup).

The product intent, in the owner's words: "the user just has to
signup or login on Facebook with the AgentQon." Everything the
operator did by hand in the Meta console for v17 (find the WABA id,
the phone number id, mint a token, subscribe the webhook) collapses
into ONE browser flow for each tenant:

  1. The ⚙ Advanced → WhatsApp panel asks GET
     /v1/whatsapp/onboard/public-config whether the owner enabled
     one-click connect (AGENTQON_META_APP_ID +
     AGENTQON_WHATSAPP_CONFIG_ID set — both are public values).
  2. The "Connect WhatsApp with Facebook" button loads the Facebook
     JS SDK, runs FB.login with that configuration id
     (response_type "code"), and listens for the WA_EMBEDDED_SIGNUP
     session-info message, which carries the user's chosen
     waba_id + phone_number_id.
  3. The browser POSTs {code, waba_id, phone_number_id} to
     /v1/whatsapp/onboard/exchange. THIS module takes it from there:
       a. code -> short-lived user token      (Graph oauth/access_token)
       b. short-lived -> long-lived token     (fb_exchange_token, ~60d)
       c. debug_token verifies the token is valid AND carries BOTH
          whatsapp_business_management and whatsapp_business_messaging
          — anything less is a 422 and NOTHING is stored (a token
          that can read but not send would strand the tenant later);
       d. POST /{waba_id}/subscribed_apps subscribes this app to the
          WABA's webhooks, so Meta actually delivers the number's
          messages to the shared webhook endpoint;
       e. GET /{phone_number_id} reads the display identity.
     The long-lived token is stored ENCRYPTED in the tenant's BYOK
     vault as a connection of kind "whatsapp" (app/connections.py
     machinery — same table, same crypto-shred on delete), with the
     ids as metadata in data_policy["whatsapp"]. Reconnecting
     replaces the previous connection.
  4. From then on the tenant's sends resolve to ITS number + token
     (app/whatsapp_client.resolve_client), inbound webhook deliveries
     route by metadata.phone_number_id (tenant_for_phone_number_id
     below), and DELETE /v1/whatsapp/connection disconnects: a
     best-effort unsubscribe, then the stored credential is shredded.

HONEST GATE (also in the README): until Meta grants this app
Advanced Access for the two WhatsApp permissions via App Review
(plus business verification), only people holding a role ON THE APP
can complete Embedded Signup — Meta refuses everyone else at the
Facebook login step. The code here is complete either way; the v17
manual path remains the fallback for tenants who cannot (yet) use
the popup.

Every Graph call goes through app/whatsapp_client.py (WhatsAppGraph)
with the environment's API base, so tests point the whole exchange
at a local stub (AGENTQON_WHATSAPP_API_BASE) — no real Meta, no real
secrets, ever, in tests.
"""
from __future__ import annotations

from typing import Optional

from .config import Settings, load_settings
from .connections import delete_connection, get_secret_for_call, new_connection_id
from .domain import now_iso
from .vault import mask_secret
from .whatsapp_client import (WhatsAppError, WhatsAppGraph,
                               connection_metadata,
                               find_whatsapp_connection)

REQUIRED_SCOPES = ("whatsapp_business_management",
                   "whatsapp_business_messaging")


class OnboardError(Exception):
    """A user-visible onboarding failure: HTTP status + plain message.

    Raised BEFORE anything is stored for every exchange failure, so a
    failed signup leaves no half-connection behind."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def onboard_settings() -> Settings:
    """Live settings (load_settings parses the environment on every
    call — the same pattern as the v17 channel, so tests can repoint
    everything with monkeypatched env vars)."""
    return load_settings()


def public_config(cfg: Optional[Settings] = None) -> dict:
    """What the browser needs to offer one-click connect. app_id and
    config_id are PUBLIC identifiers (they ship in every Facebook
    login page); the app secret never leaves the server."""
    cfg = cfg or onboard_settings()
    return {"configured": bool(cfg.meta_app_id and cfg.whatsapp_config_id),
            "app_id": cfg.meta_app_id or None,
            "config_id": cfg.whatsapp_config_id or None}


def exchange_configured(cfg: Settings) -> bool:
    """Server-side readiness for the exchange itself: the public pair
    PLUS the app secret (the code swap and debug_token both need it)."""
    return bool(cfg.meta_app_id and cfg.whatsapp_config_id
                and cfg.whatsapp_app_secret)


def _graph(cfg: Settings) -> WhatsAppGraph:
    return WhatsAppGraph(cfg.meta_app_id, cfg.whatsapp_app_secret,
                         cfg.whatsapp_api_base, cfg.whatsapp_api_version)


def _graph_step(what: str, fn) -> object:
    """Run one Graph step, translating a Graph refusal into a 502
    OnboardError (Meta's message included; nothing stored yet)."""
    try:
        return fn()
    except WhatsAppError as exc:
        raise OnboardError(
            502, f"Meta refused the {what} step: {exc}") from exc


def exchange_signup(store, vault, tenant_id: str, user_id: str,
                    code: str, waba_id: str, phone_number_id: str,
                    cfg: Optional[Settings] = None) -> dict:
    """Complete an Embedded Signup for this tenant (module docstring,
    step 3). Returns the connection status. Raises OnboardError —
    with NOTHING stored — for any failure along the way."""
    cfg = cfg or onboard_settings()
    if vault is None:
        raise OnboardError(503, "the credential vault is not configured "
                                "on this server, so a token cannot be "
                                "stored safely")
    graph = _graph(cfg)
    token = _graph_step("code exchange", lambda: graph.exchange_code(code))
    token = _graph_step("long-lived exchange",
                        lambda: graph.exchange_long_lived(token))
    info = _graph_step("token inspection", lambda: graph.debug_token(token))
    if not info.get("is_valid"):
        raise OnboardError(422, "Facebook returned a token that is not "
                                "valid — please run the connect flow again")
    scopes = set(info.get("scopes") or [])
    missing = [s for s in REQUIRED_SCOPES if s not in scopes]
    if missing:
        raise OnboardError(
            422, "the Facebook login did not grant " + ", ".join(missing)
            + " — both permissions are required to connect WhatsApp, "
              "and nothing was stored. Re-run the flow and accept all "
              "permissions.")
    _graph_step("webhook subscription",
                lambda: graph.subscribe_app(waba_id, token))
    number = _graph_step("phone number lookup",
                         lambda: graph.phone_number_info(phone_number_id,
                                                         token))
    meta = {"waba_id": str(waba_id),
            "phone_number_id": str(phone_number_id),
            "display_phone_number": number.get("display_phone_number"),
            "verified_name": number.get("verified_name")}

    # Replace any previous WhatsApp connection: one business number
    # per tenant keeps routing unambiguous. The old credential is
    # crypto-shredded by delete_connection.
    existing = find_whatsapp_connection(store, tenant_id)
    if existing is not None:
        delete_connection(store, tenant_id, user_id, existing["id"])
    label = "WhatsApp"
    if meta["verified_name"]:
        label = f"WhatsApp · {meta['verified_name']}"
    elif meta["display_phone_number"]:
        label = f"WhatsApp · {meta['display_phone_number']}"
    conn = {
        "id": new_connection_id(), "tenant_id": tenant_id,
        "owner_user_id": user_id, "kind": "whatsapp",
        "provider_key": "meta_whatsapp", "label": label,
        "base_url": cfg.whatsapp_api_base, "state": "active",
        "masked_key": mask_secret(token),
        "allowed_models": [], "blocked_models": [],
        "budget_limit_usd": None, "routing_mode": "balanced",
        "data_policy": {"whatsapp": meta},
        "last_validated_at": now_iso(), "last_error_category": None,
        "models_count": 0,
        "created_at": now_iso(), "updated_at": now_iso(),
        "deleted_at": None,
    }
    version, ciphertext = vault.encrypt(token)
    store.put_connection(tenant_id, conn,
                         {"key_version": version, "ciphertext": ciphertext})
    store.audit(tenant_id, user_id, "whatsapp.onboard_connected",
                "connection", conn["id"],
                {"waba_id": meta["waba_id"],
                 "phone_number_id": meta["phone_number_id"]})
    return connection_status(store, tenant_id)


def connection_status(store, tenant_id: str) -> dict:
    """The tenant's WhatsApp connection for the UI: connected?, and
    as which business number. Never includes the token (masked only,
    via the connection row)."""
    conn = find_whatsapp_connection(store, tenant_id)
    if conn is None:
        return {"connected": False, "waba_id": None,
                "phone_number_id": None, "display_phone_number": None,
                "verified_name": None, "connected_at": None}
    meta = connection_metadata(conn)
    return {"connected": True,
            "waba_id": meta.get("waba_id"),
            "phone_number_id": meta.get("phone_number_id"),
            "display_phone_number": meta.get("display_phone_number"),
            "verified_name": meta.get("verified_name"),
            "connected_at": conn.get("created_at")}


def disconnect(store, vault, tenant_id: str, user_id: str,
               cfg: Optional[Settings] = None) -> dict:
    """Disconnect the tenant's WhatsApp business number: best-effort
    unsubscribe at Meta (failures ignored — the local removal is what
    stops OUR sends), then delete the connection, which
    crypto-shreds the stored token."""
    cfg = cfg or onboard_settings()
    conn = find_whatsapp_connection(store, tenant_id)
    if conn is None:
        return {"disconnected": False}
    meta = connection_metadata(conn)
    if vault is not None and meta.get("waba_id"):
        try:
            token = get_secret_for_call(store, vault, tenant_id, conn["id"])
            _graph(cfg).unsubscribe_app(meta["waba_id"], token)
        except Exception:
            pass  # best effort by design — see docstring
    delete_connection(store, tenant_id, user_id, conn["id"])
    store.audit(tenant_id, user_id, "whatsapp.onboard_disconnected",
                "connection", conn["id"],
                {"waba_id": meta.get("waba_id"),
                 "phone_number_id": meta.get("phone_number_id")})
    return {"disconnected": True}


def tenant_for_phone_number_id(store, phone_number_id) -> Optional[str]:
    """Webhook routing (v18): which tenant owns this business phone
    number id, per the stored WhatsApp connections? None = nobody
    onboarded with it (the caller ignores the delivery)."""
    if not phone_number_id:
        return None
    for conn in store.list_connections_all():
        if conn.get("kind") != "whatsapp" or conn.get("state") != "active":
            continue
        if connection_metadata(conn).get("phone_number_id") == str(phone_number_id):
            return conn["tenant_id"]
    return None
