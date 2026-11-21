"""AgentQon — model connection state (v20).

Generation requires a REAL model. This module is the single place that
decides whether one is connected, and from where — in priority order:

  1. `byok`      a tenant's own connection (Phase 3 vault + catalog):
                 an active connection with at least one catalogued model;
  2. `env`       the operator-configured OpenAI-compatible endpoint
                 (AGENTQON_LLM_BASE_URL / _API_KEY / _MODEL);
  3. `omniroute` an OmniRoute sidecar auto-detected on its default
                 local port (http://127.0.0.1:20128/v1);
  4. `ollama`    a local Ollama auto-detected on its default port
                 (http://127.0.0.1:11434/v1) — zero config.

Local endpoints are PROBED (GET /models, short timeout, result cached
for a few seconds): a configured-but-dead local model is not
"connected". A cloud endpoint configured with a real key is trusted
until a call proves otherwise — and a failed call then fails the
mission honestly; nothing silently substitutes canned output, because
there is none.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import Settings
from .llm_adapter import _is_local_endpoint

OLLAMA_URL = "http://127.0.0.1:11434/v1"
OMNIROUTE_URL = "http://127.0.0.1:20128/v1"
PROBE_TIMEOUT_S = 1.5
PROBE_CACHE_TTL_S = 10.0

NOT_CONNECTED_MESSAGE = (
    "No model is connected, so nothing can be generated. Connect a model "
    "any of these ways: add your own API key in ⚙ Advanced → Connections; "
    "set AGENTQON_LLM_BASE_URL / AGENTQON_LLM_API_KEY / AGENTQON_LLM_MODEL "
    "for the server; start an OmniRoute sidecar; or run a local model with "
    "Ollama (auto-detected on its default port).")


@dataclass(frozen=True)
class ModelState:
    connected: bool
    source: Optional[str] = None      # byok | env | omniroute | ollama
    model: Optional[str] = None
    base_url: Optional[str] = None
    message: str = ""

    def as_dict(self) -> dict:
        return {"connected": self.connected, "source": self.source,
                "model": self.model, "base_url": self.base_url}


NOT_CONNECTED = ModelState(connected=False, message=NOT_CONNECTED_MESSAGE)

_probe_cache: dict[str, tuple[float, Optional[list[str]]]] = {}


def probe_models(base_url: str, api_key: str = "",
                 timeout: float = PROBE_TIMEOUT_S) -> Optional[list[str]]:
    """Model ids served by an OpenAI-compatible endpoint, or None when
    it does not answer. Cached briefly so health checks stay cheap and
    a dead local endpoint costs one short timeout per TTL window."""
    key = base_url.rstrip("/")
    now = time.monotonic()
    hit = _probe_cache.get(key)
    if hit and now - hit[0] < PROBE_CACHE_TTL_S:
        return hit[1]
    result: Optional[list[str]] = None
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.get(f"{key}/models", headers=headers)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            result = [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]
    except Exception:
        result = None
    _probe_cache[key] = (now, result)
    return result


def _pick_model(settings: Settings, models: Optional[list[str]]) -> str:
    if models:
        if settings.llm_model in models:
            return settings.llm_model
        return models[0]
    return settings.llm_model


def platform_state(settings: Settings) -> ModelState:
    """Model state from operator configuration + local auto-detection
    (no tenant involved): env endpoint, then OmniRoute, then Ollama."""
    if settings.llm_base_url_explicit:
        url = settings.llm_base_url
        if _is_local_endpoint(url):
            models = probe_models(url, settings.llm_api_key)
            if models is None:
                return ModelState(connected=False, source="env", base_url=url,
                                  message=(f"The configured model endpoint {url} is not "
                                           f"answering. {NOT_CONNECTED_MESSAGE}"))
            return ModelState(connected=True, source="env",
                              model=_pick_model(settings, models), base_url=url)
        if settings.llm_api_key_explicit or (
                settings.llm_api_key and settings.llm_api_key != "ollama"):
            return ModelState(connected=True, source="env",
                              model=settings.llm_model, base_url=url)
        return ModelState(connected=False, source="env", base_url=url,
                          message=(f"The configured model endpoint {url} has no API key. "
                                   f"Set AGENTQON_LLM_API_KEY. {NOT_CONNECTED_MESSAGE}"))
    for url, source in ((OMNIROUTE_URL, "omniroute"), (OLLAMA_URL, "ollama")):
        models = probe_models(url, settings.llm_api_key)
        if models is not None:
            return ModelState(connected=True, source=source,
                              model=_pick_model(settings, models), base_url=url)
    return NOT_CONNECTED


def resolve_state(settings: Settings, store=None,
                  tenant_id: Optional[str] = None) -> ModelState:
    """Full resolution for a tenant: their own BYOK connection first,
    then the platform state."""
    if store is not None and tenant_id:
        try:
            active = {c["id"]: c for c in store.list_connections(tenant_id)
                      if c.get("state") == "active"}
            if active:
                entries = [(entry, active[entry.get("connection_id")])
                           for entry in store.list_catalog(tenant_id)
                           if entry.get("connection_id") in active]
                if entries:
                    entry, conn = _pick_catalog_entry(settings, entries)
                    return ModelState(connected=True, source="byok",
                                      model=entry.get("model_id"),
                                      base_url=conn.get("base_url"))
        except Exception:
            pass  # a broken catalog must not mask the platform sources
    return platform_state(settings)


def _pick_catalog_entry(settings: Settings, entries: list) -> tuple:
    """Choose which catalogued model represents the BYOK connection.

    Audit 2026-10-12: the old code returned the FIRST catalog row,
    which for a gateway connection is whatever the provider lists
    first alphabetically — e.g. an AI-Horde image model — so
    /v1/model-state (and the UI chip) claimed the user's connected
    model was an image generator while /health named the real chat
    model. Preference order: the configured default model (exact id
    or provider-prefixed suffix), then a model served by a local
    endpoint connection, then a chat-advertised entry, then the
    first entry."""
    wanted = (settings.llm_model or "").strip()
    if wanted:
        for entry, conn in entries:
            mid = entry.get("model_id") or ""
            if mid == wanted or mid.endswith("/" + wanted):
                return entry, conn
    for entry, conn in entries:
        if conn.get("kind") == "local_endpoint":
            return entry, conn
    for entry, conn in entries:
        caps = entry.get("capabilities") or {}
        if caps.get("chat") == "advertised":
            return entry, conn
    return entries[0]
