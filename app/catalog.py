"""Dynamic model catalog (Master Build V3 §105.1, §113).

The catalog is discovered from live gateway/provider APIs — never
hardcoded. Every entry records where its metadata came from and when it
was last seen; consumers must treat stale entries and unknown prices as
unknown, never as zero/free (§105.8).

Capability honesty: a model appearing in /models proves it is *listed*,
not that any specific capability works. We record chat as advertised by
the endpoint type and mark structured output / tool calling "unknown"
unless the platform has actually run the model (the agent runtime marks
`verified_chat_json` after a successful structured run).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .gateway_client import GatewayClient, GatewayError
from .llm_adapter import _is_local_endpoint
from .store import Store, now_iso

CATALOG_TTL_SECONDS = 900  # entries older than this are labelled stale


def _parse_price(raw: dict) -> tuple[Optional[float], Optional[float], Optional[str], str]:
    """Extract per-1M-token prices when the gateway publishes them
    (OpenRouter-style `pricing` blocks, OmniRoute catalog fields).
    Anything unrecognized → unknown, never zero."""
    pricing = raw.get("pricing") or {}
    def _f(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    pin = _f(pricing.get("prompt")) if pricing else _f(raw.get("price_in"))
    pout = _f(pricing.get("completion")) if pricing else _f(raw.get("price_out"))
    if pricing and pin is not None:
        # OpenRouter-style prices are per-token; normalize to per-1M.
        if pin < 0.001:
            pin = pin * 1_000_000
        if pout is not None and pout < 0.001:
            pout = pout * 1_000_000
    if pin is not None:
        return pin, pout, "gateway", "known"
    return None, None, None, "unknown"


def normalize_model(raw: dict, *, kind: str) -> Optional[dict]:
    model_id = raw.get("id") or raw.get("name")
    if not model_id:
        return None
    pin, pout, psrc, pstatus = _parse_price(raw)
    if raw.get("price_estimated") and pin is not None:
        # An explicitly estimated price is shown as an estimate
        # wherever it appears (W6 M2) — never as a known price.
        pstatus = "estimated"
    if kind == "local_endpoint":
        # Local compute: cost is genuinely zero provider dollars; that is
        # a known fact about the deployment, not a pricing guess.
        pin, pout, psrc, pstatus = 0.0, 0.0, "local", "known"
    caps = {
        "chat": "advertised",
        "structured_output": "unknown",
        "tool_calling": "unknown",
        "streaming": "unknown",
        "listing": "gateway",
    }
    ctx = raw.get("context_length") or raw.get("context_window")
    if isinstance(ctx, int) and ctx > 0:
        caps["context_window"] = ctx
    return {
        "model_id": str(model_id),
        "provider": str(raw.get("owned_by") or raw.get("provider") or ""),
        "display_name": str(raw.get("name") or model_id),
        "capabilities": caps, "price_in": pin, "price_out": pout,
        "price_source": psrc, "price_status": pstatus, "health": "ok",
    }


async def sync_connection_catalog(store: Store, tenant_id: str, conn: dict,
                                  secret: str, client: Optional[GatewayClient] = None) -> dict:
    """Refresh one connection's catalog. Failures keep the last-good
    snapshot and record a failed sync run (§113) — the catalog is never
    silently cleared."""
    run_id = store.start_sync_run(tenant_id, conn["id"])
    gw = client or GatewayClient(base_url=conn["base_url"], api_key=secret, timeout=30,
                                 connection_id=conn["id"],
                                 headers=conn.get("headers") or {})
    try:
        raw_models = await gw.list_models()
        if not raw_models and (conn["kind"] == "local_endpoint"
                               or _is_local_endpoint(conn["base_url"])):
            raw_models = await gw.list_models_ollama_native()
    except GatewayError as exc:
        store.finish_sync_run(run_id, "failed", 0, error=exc.category)
        return {"sync_run_id": run_id, "status": "failed", "error": exc.category, "models": 0}
    seen = set()
    for raw in raw_models:
        entry = normalize_model(raw, kind=conn["kind"])
        if entry:
            store.upsert_catalog_model(tenant_id, conn["id"], entry)
            seen.add(entry["model_id"])
    # User-declared models: some gateways route models they do not list
    # (observed with OmniRoute v3.8.51: ollama-local chat models answer
    # /v1/chat/completions but are absent from /v1/models). A model the
    # user explicitly allowlisted is catalogued as DECLARED — provenance
    # is honest: capabilities unverified, price unknown — so routing can
    # select it and the first successful run verifies it in practice.
    declared = 0
    for model_id in conn.get("allowed_models") or []:
        if model_id not in seen:
            store.upsert_catalog_model(tenant_id, conn["id"], {
                "model_id": model_id, "provider": "user-declared",
                "display_name": model_id,
                "capabilities": {"chat": "declared", "structured_output": "unknown",
                                 "tool_calling": "unknown", "streaming": "unknown",
                                 "listing": "user_declared"},
                "price_in": None, "price_out": None, "price_source": None,
                "price_status": "unknown", "health": "unknown"})
            seen.add(model_id)
            declared += 1
    store.deprecate_missing_models(tenant_id, conn["id"], seen)
    store.finish_sync_run(run_id, "completed", len(seen))
    return {"sync_run_id": run_id, "status": "completed", "models": len(seen),
            "declared": declared}


def catalog_freshness(entry: dict, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    try:
        seen = datetime.fromisoformat(entry["last_seen"])
    except (KeyError, ValueError):
        return "unknown"
    age = (now - seen).total_seconds()
    return "fresh" if age <= CATALOG_TTL_SECONDS else "stale"


def public_catalog_entry(entry: dict) -> dict:
    out = dict(entry)
    out["freshness"] = catalog_freshness(entry)
    if out["freshness"] == "stale" and out.get("price_status") == "known":
        # A stale price is not a price (§105.8): downgrade, don't hide.
        out["price_status"] = "stale"
    # An estimate stays an estimate: it is never upgraded to known
    # by freshness, and an unknown/stale price never gains a number.
    from .model_registry import price_display
    out["price_display"] = price_display(out)
    return out


async def refresh_models_for_connection(store: Store, tenant_id: str, conn: dict,
                                        secret: str,
                                        client: Optional[GatewayClient] = None) -> dict:
    """Model-list refresh when a provider configuration changes (W6
    A5): re-run the connection's catalog sync, then remove stale
    entries — models the provider no longer lists, and models the
    connection no longer allowlists (when an allowlist is set).
    Failures keep the last-good snapshot (sync's own contract) and
    are reported, never hidden: the caller decides whether to surface
    them. Returns the sync result plus a `removed` count."""
    result = await sync_connection_catalog(store, tenant_id, conn, secret,
                                           client=client)
    removed = 0
    if result.get("status") == "completed":
        allowed = conn.get("allowed_models") or []
        if allowed:
            before = {e["model_id"] for e in store.list_catalog(tenant_id, conn["id"])
                      if not e.get("deprecated")}
            store.deprecate_missing_models(tenant_id, conn["id"], set(allowed))
            after = {e["model_id"] for e in store.list_catalog(tenant_id, conn["id"])
                     if not e.get("deprecated")}
            removed = len(before - after)
    result["removed_stale"] = removed
    return result
