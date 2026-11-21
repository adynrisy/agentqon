"""AgentQon — mission orchestrator (Ch.8, 9, 10, 15; Master Build V3
§104/§105.4/§114 for routing + budget reservations).

Pipeline: plan -> two independent research runs -> one bounded critique
round -> evaluation -> candidate comparison. Independent first passes are
structural: each research agent receives only the mission contract.

Phase 3 (V3) additions:
  - per-role model routing through the catalog + router, with persisted
    RouteDecision records; a mission may pin a tenant BYOK connection —
    its key is decrypted server-side per call and NEVER falls back to a
    platform key silently (auth failure fails the mission);
  - v20: generation requires a connected REAL model (app/modelstate.py).
    With none connected, a run fails fast with `model_not_connected`;
    a failed model call fails the mission. There is no canned-output
    fallback of any kind.
  - race-safe budget reservations around every model call: reserve ->
    call -> reconcile (or release on failure). Unknown prices reserve a
    conservative configured amount; they are never treated as free;
  - pause: the worker requeues the job and the mission waits in `paused`
    until resumed; cancellation semantics unchanged;
  - revision rounds: a candidate can be revised (bounded, versioned)
    against its critiques + user instructions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .adapters import AgentRequest
from .config import Settings, load_settings
from .domain import (Candidate, MissionStatus, StateConflict,
                     assert_transition, event_envelope,
                     MISSION_BUDGET_CEILING_USD, MISSION_TRANSITIONS)
from .artifacts import final_artifacts_from_events
from .evaluator import evaluate_candidate
from .gateway import ToolGateway
from .gateway_client import GatewayClient, GatewayError
from .memory import extract_from_mission, memory_context
from .modes import ensure_pack, get_mode, resolve_phases
from .llm_adapter import LLM_ADAPTER_VERSION, LLMCriticAdapter, LLMResearchAdapter
from .modelstate import ModelState, platform_state, resolve_state
from .planner import build_plan
from .registry import rank_agents
from .router import NoEligibleModel, RouteRequest, route
from .store import now_iso

MAX_ROUNDS = 2       # Ch.8/10: debate is bounded, always
MAX_REVISIONS = 2    # per candidate (V3 §7: bounded iteration)


class MissionCancelled(Exception):
    """Raised when a mission is cancelled mid-run (Ch.19)."""


class MissionPaused(Exception):
    """Raised when a mission is paused mid-run; the worker requeues."""


class BudgetExceeded(Exception):
    """Raised when a budget reservation cannot be satisfied (§114.6)."""


class ModelNotConnected(Exception):
    """Raised when generation is attempted with no real model connected
    (v20). Carries the human-readable fixes; the API maps it to HTTP 503
    with error code `model_not_connected`, and a mission that hits it
    ends FAILED with that reason — never completed with canned output."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "model_not_connected"


class ChatModelPinUnresolvable(Exception):
    """Raised when a POST /v1/chat model pin cannot be honored (v22):
    the pinned model is neither a catalogued model on one of the
    tenant's ACTIVE connections nor the currently connected default
    model. The API maps it to HTTP 422 with error code
    `model_pin_unresolvable` — the CLI's tested contract for a pin it
    must drop before retrying unpinned. It is never silently served by
    a different model or connection."""

    def __init__(self, model: str):
        super().__init__(
            f"Chat model pin '{model}' cannot be honored: it is not a "
            f"catalogued model on an active connection, and it is not "
            f"the connected default model.")
        self.code = "model_pin_unresolvable"
        self.model = model


class ModelListEntryError(Exception):
    """Raised when one entry of a multi-model list cannot be used.

    The API maps it to HTTP 422 with code `model_list_entry_not_found`
    and the sentence below as both message and detail, so the UI can
    render it verbatim (the UI's per-code line map would otherwise
    discard the entry name). The sentence is plain words, names the
    FIRST failing entry only, and never carries a connection id, an
    HTTP status, or JSON. Validation happens before ANY model call
    (chat) or before the mission row becomes useful (creation) or
    before planning/drafting (run start) — a bad list never runs on
    a silent subset.
    """

    def __init__(self, entry: str, sentence: str):
        super().__init__(sentence)
        self.code = "model_list_entry_not_found"
        self.entry = entry


def resolve_model_list(store, tenant_id: str, models: list,
                       entries=None, connections=None) -> list[dict]:
    """Resolve an ordered multi-model list to failover targets.

    Shared by chat and missions (one function, not two copies):
    duplicates block; each entry resolves to the FIRST catalog entry
    with that model_id on an ACTIVE connection (the same first-match
    rule the single pin uses); then the eligible_targets exclusions
    apply as ERRORS, not silent drops — a blocked, allowlist-excluded,
    deprecated, paused-connection, or unknown entry fails the whole
    list naming that entry. `entries`/`connections` let a mission run
    resolve against its already-filtered runtime view (e.g. a pinned
    connection); chat passes neither and reads the store directly.
    Returns [{connection_id, model_id, weight: 1}, ...] in list order.
    """
    seen = set()
    for entry in models:
        if entry in seen:
            raise ModelListEntryError(
                entry,
                f'"{entry}" appears twice in the list. '
                "List each model once.")
        seen.add(entry)
    catalog = entries if entries is not None else store.list_catalog(tenant_id)
    if connections is None:
        connections = {c["id"]: c for c in store.list_connections(tenant_id)}
    active = {cid: c for cid, c in connections.items()
              if c.get("state") == "active"}
    targets = []
    for entry in models:
        matches = [e for e in catalog if e.get("model_id") == entry]
        if not matches:
            raise ModelListEntryError(
                entry,
                f'"{entry}" was not found on any of your active '
                "connections. Check the spelling, or clear the list to "
                "use the model choice above.")
        active_matches = [e for e in matches
                          if e.get("connection_id") in active]
        if not active_matches:
            raise ModelListEntryError(
                entry,
                f'"{entry}" is on a connection that is paused right '
                "now. Turn that connection back on, pick a different "
                "model, or clear the list.")
        chosen = active_matches[0]
        conn = active[chosen["connection_id"]]
        if chosen.get("deprecated"):
            raise ModelListEntryError(
                entry,
                f'"{entry}" is no longer offered by its provider. '
                "Pick a different model, or clear the list.")
        blocked = conn.get("blocked_models") or []
        allowed = conn.get("allowed_models") or []
        if entry in blocked or (allowed and entry not in allowed):
            raise ModelListEntryError(
                entry,
                f'"{entry}" is blocked on its connection. '
                "Pick a different model, or clear the list.")
        targets.append({"connection_id": chosen["connection_id"],
                        "model_id": entry, "weight": 1})
    return targets


def ephemeral_model_list_combo(targets: list[dict]) -> dict:
    """The in-memory priority combo a multi-model list runs as.

    combo_id None means "never persisted" (_combo_chain_gateway
    already treats None as unstored, so no throwaway stored combo
    pollutes the user's combo library). Name "Chosen models" keeps
    minutes/route decisions distinguishable from a named combo.
    """
    return {"combo_id": None, "name": "Chosen models",
            "strategy": "priority", "targets": list(targets),
            "enabled": True, "state": None}


@dataclass
class RunContext:
    """Per-run routing state (v21.4).

    A run's resolved adapters, BYOK strictness, model state and trace
    id live HERE — created by _prepare_runtime and threaded through the
    run — never on the shared Orchestrator instance. Before v21.4 they
    were instance attributes (self._runtime & friends): two concurrent
    runs interleaved at every awaited model call, the second run's
    _prepare_runtime overwrote the first's, and the first mission's
    remaining calls executed through the SECOND tenant's connection
    and API key."""
    runtime: dict[str, dict] = field(default_factory=dict)
    strict_byok: bool = False
    model_state: ModelState | None = None
    trace_id: str | None = None


class Orchestrator:
    def __init__(self, store, settings: Settings | None = None, vault=None):
        self.store = store
        self.settings = settings or load_settings()
        self.vault = vault
        research_provider = None
        if self.settings.research_mode == "live":
            from .research import LiveWebResearcher
            research_provider = LiveWebResearcher(
                max_fetches=self.settings.research_max_fetches)
        self.gateway = ToolGateway(
            store, research_provider=research_provider,
            research_mode=self.settings.research_mode,
            reach_channels={c.strip() for c in
                            self.settings.reach_channels.split(",")
                            if c.strip()})
        # v20: the only adapters are the real-model ones. The unrouted
        # instances below serve callers that bypass per-role routing
        # (the arena); mission runs always route via _prepare_runtime.
        self.research_adapter = LLMResearchAdapter(self.settings)
        self.critic_adapter = LLMCriticAdapter(self.settings)
        self.adapter_version = LLM_ADAPTER_VERSION
        # v21.4: NO per-run state lives on the instance — see RunContext.
        # The orchestrator is a process-wide singleton shared by every
        # tenant and every concurrent run; anything run-specific kept
        # here is a cross-tenant leak waiting for an interleave.

    # ------------------------------------------------------- model state
    def model_state(self, tenant_id: str | None = None) -> ModelState:
        """Whether a real model is connected for this tenant (their BYOK
        connection first, then the platform sources)."""
        return resolve_state(self.settings, self.store, tenant_id)

    def platform_model_state(self) -> ModelState:
        """Platform-level model state (env endpoint / OmniRoute /
        Ollama auto-detect) — what /health reports."""
        return platform_state(self.settings)

    # ------------------------------------------------------- conversation
    CHAT_SYSTEM_PROMPT = (
        "You are AgentQon — a capable, friendly assistant. Answer the "
        "user directly and conversationally. Be brief unless depth is "
        "asked for. You are having a conversation, not running a task: "
        "do not invent tools, missions, agents or steps, and do not "
        "describe internal machinery. If the user asks for a work "
        "product, simply answer as helpfully as you can in the reply.")

    # ------------------------------------------------- W6 chat helpers
    @staticmethod
    def _chat_turns(history: list[dict] | None) -> list[dict]:
        """Shared history shaping for chat (direct + combo + stream):
        last 10 turns, user/assistant text only, capped per turn."""
        turns: list[dict] = []
        for turn in (history or [])[-10:]:
            if not isinstance(turn, dict):
                continue
            role, content = turn.get("role"), turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                turns.append({"role": role, "content": content[:4000]})
        return turns

    @staticmethod
    def _chat_estimate(messages: list[dict]) -> dict:
        """Token estimate before the call (W6 gap 4)."""
        from .token_est import estimate_tokens
        return estimate_tokens(messages=messages)

    @staticmethod
    def _chat_context_warning(entry: dict | None, estimate: dict) -> str | None:
        """Context-limit warning before the call (W6 A2)."""
        from .model_registry import context_warning
        caps = (entry or {}).get("capabilities") or {}
        window = caps.get("context_window")
        if not isinstance(window, int):
            return None
        return context_warning(estimate.get("estimated_input_tokens"), window)

    def _chat_reconcile(self, tenant_id: str, model_id: str,
                        estimate: dict, usage: dict) -> dict:
        """Reconcile estimate vs provider usage (W6 gap 4) and record
        it. Best-effort: bookkeeping never eats a delivered reply."""
        from .modelrequest import normalize_usage
        from .token_est import reconcile
        reported = normalize_usage(usage)
        rec = reconcile(estimate.get("estimated_input_tokens"), None,
                        {"input_tokens": reported["input_tokens"],
                         "output_tokens": reported["output_tokens"]})
        try:
            self.store.record_token_reconciliation(
                tenant_id, "chat", model_id, rec)
        except Exception:
            pass
        return rec

    async def chat_reply(self, tenant_id: str, message: str,
                         history: list[dict] | None = None,
                         model: str | None = None,
                         models: list[str] | None = None) -> dict:
        """ONE routed model call for plain conversation (v21.4.3).

        Normal conversation is not a mission: no mission row, no queue
        job, no panel — just the caller's resolved model (tenant BYOK →
        platform, via the same resolve_state missions use) answering
        once. No model connected → ModelNotConnected, and the endpoint
        returns the honest 503 envelope.

        v22 pin: `model`, when given, is the model_id a /v1/models
        catalog entry exposes. Resolution (after the connected check):
        an entry with that model_id on one of the tenant's ACTIVE
        connections serves the reply through THAT entry's connection;
        failing that, a pin equal to the connected default model falls
        through to the normal path below unchanged; anything else
        raises ChatModelPinUnresolvable (HTTP 422) — a pin is never
        silently served by a different model. With no pin, conn/entry/
        model/source are exactly the pre-pin values.

        Multi-model list (`models`, redesign): an ORDERED FAILOVER
        chain, not simultaneous deliberation — the models are tried
        in the order given and the first one that answers does the
        work, via the existing combo failover machinery with an
        ephemeral (unstored) priority combo. A 1-element list
        normalises to the single-pin path. A list of 2+ is validated
        in full BEFORE any model call (and before the connected
        check, so a list that cannot be satisfied on a tenant with
        no connection yields the named unknown-entry error, not a
        generic not-connected one); any failing entry raises
        ModelListEntryError naming it."""
        if models is not None:
            if len(models) == 1:
                model = models[0]
                models = None
            elif len(models) >= 2:
                targets = resolve_model_list(self.store, tenant_id, models)
                combo = ephemeral_model_list_combo(targets)
                state = resolve_state(self.settings, self.store, tenant_id)
                if not state.connected:
                    raise ModelNotConnected(state.message)
                return await self._chat_reply_combo_record(
                    tenant_id, message, history, combo, state)
        state = resolve_state(self.settings, self.store, tenant_id)
        if not state.connected:
            raise ModelNotConnected(state.message)
        if model is not None and model.startswith("combo:"):
            return await self._chat_reply_combo(
                tenant_id, message, history, model, state)
        effective_model = state.model
        effective_source = state.source
        conn = entry = None
        if model is not None:
            active = {c["id"]: c for c in self.store.list_connections(tenant_id)
                      if c.get("state") == "active"}
            for candidate in self.store.list_catalog(tenant_id):
                pinned_conn = active.get(candidate.get("connection_id"))
                if pinned_conn is not None \
                        and candidate.get("model_id") == model:
                    entry, conn = candidate, pinned_conn
                    conn = {**conn, "tenant_id": tenant_id}
                    effective_model = model
                    effective_source = "byok"
                    break
            if conn is None and model != state.model:
                raise ChatModelPinUnresolvable(model)
        if conn is None:
            if state.source == "byok":
                from .modelstate import _pick_catalog_entry
                active = {c["id"]: c for c in self.store.list_connections(tenant_id)
                          if c.get("state") == "active"}
                entries = [(e, active[e.get("connection_id")])
                           for e in self.store.list_catalog(tenant_id)
                           if e.get("connection_id") in active]
                entry, conn = _pick_catalog_entry(self.settings, entries)
                conn = {**conn, "tenant_id": tenant_id}
            else:
                conn, entry = self._platform_connection(base_url=state.base_url,
                                                        model=state.model)
                conn = {**conn, "tenant_id": tenant_id}
        gateway = self._gateway_for(conn)
        turns: list[dict] = []
        for turn in (history or [])[-10:]:
            if not isinstance(turn, dict):
                continue
            role, content = turn.get("role"), turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                turns.append({"role": role, "content": content[:4000]})
        messages = ([{"role": "system", "content": self.CHAT_SYSTEM_PROMPT}]
                    + turns + [{"role": "user", "content": message}])
        # W6: estimate + context warning BEFORE the call, so a
        # request near the limit warns before it fails, not after.
        estimate = self._chat_estimate(messages)
        warning = self._chat_context_warning(entry, estimate)
        reply, usage = await gateway.chat_text(effective_model, messages)
        # W6 R20: downstream size enforcement — an oversized reply is
        # flagged and marked, never silently cut short.
        from .modelrequest import enforce_response_size
        sized = enforce_response_size(reply)
        reply = sized["text"]
        reconciliation = self._chat_reconcile(
            tenant_id, effective_model, estimate, usage)
        # Usage accounting through the same recorder the panel adapters
        # feed — under the pseudo-mission id "chat" (add_spend's UPDATE
        # matches no mission row and no-ops; the usage row itself keeps
        # chat spend visible in the tenant's totals). Best-effort: a
        # bookkeeping failure must never eat a delivered reply.
        try:
            price_in, price_out = entry.get("price_in"), entry.get("price_out")
            if entry.get("price_status") == "known" and price_in is not None \
                    and price_out is not None:
                usage = {**usage, "cost_usd": round(
                    usage["input_tokens"] / 1_000_000 * float(price_in)
                    + usage["output_tokens"] / 1_000_000 * float(price_out), 6)}
            self._record_inference_usage(
                tenant_id, "chat", usage,
                f"chat reply via {effective_model}", category="chat")
        except Exception:
            pass
        out = {"reply": reply, "model": effective_model,
               "source": effective_source,
               "token_estimate": estimate,
               "token_reconciliation": reconciliation,
               "response_truncated": sized["truncated"]}
        if warning:
            out["context_warning"] = warning
        return out

    async def _chat_reply_combo(self, tenant_id: str, message: str,
                                history: list[dict] | None,
                                combo_ref: str, state) -> dict:
        """Chat through a combo (item 7): the same failover chain
        missions use. A combo pin that cannot resolve is unresolv-
        able (422) — never silently served by another model."""
        ref = combo_ref[len("combo:"):]
        if ref == "auto":
            combo = {"combo_id": "auto", "name": "Auto combo",
                     "strategy": "auto", "targets": [],
                     "enabled": True, "state": None}
        else:
            combo = self.store.get_combo(tenant_id, ref)
            if not combo or not combo.get("enabled", True):
                raise ChatModelPinUnresolvable(combo_ref)
        return await self._chat_reply_combo_record(
            tenant_id, message, history, combo, state)

    async def _chat_reply_combo_record(self, tenant_id: str, message: str,
                                       history: list[dict] | None,
                                       combo: dict, state) -> dict:
        """Chat through an already-resolved combo record — stored,
        auto, or an ephemeral multi-model list (combo_id None, never
        persisted). The failover chain, switch notice, and reply
        stamp are identical in all three cases."""
        connections = {c["id"]: c for c in
                       self.store.list_connections(tenant_id)}
        entries = list(self.store.list_catalog(tenant_id))
        pconn, pentry = self._platform_connection(
            base_url=state.base_url, model=state.model)
        pconn = {**pconn, "tenant_id": tenant_id}
        connections["platform"] = pconn
        entries = entries + [pentry]
        # W6 A4: capture the first failover so the reply can say, in
        # plain words, that the model switched — never silently.
        switches: list[dict] = []
        gateway, entry, _conn, _n = self._combo_chain_gateway(
            tenant_id, combo, entries, connections,
            on_failover=lambda payload: switches.append(payload))
        turns: list[dict] = []
        for turn in (history or [])[-10:]:
            if not isinstance(turn, dict):
                continue
            role, content = turn.get("role"), turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                turns.append({"role": role, "content": content[:4000]})
        messages = ([{"role": "system", "content": self.CHAT_SYSTEM_PROMPT}]
                    + turns + [{"role": "user", "content": message}])
        estimate = self._chat_estimate(messages)
        warning = self._chat_context_warning(entry, estimate)
        reply, usage = await gateway.chat_text(entry["model_id"], messages)
        answered_by = usage.get("model") or entry["model_id"]
        from .modelrequest import enforce_response_size
        sized = enforce_response_size(reply)
        reply = sized["text"]
        reconciliation = self._chat_reconcile(
            tenant_id, answered_by, estimate, usage)
        try:
            price_in, price_out = entry.get("price_in"), entry.get("price_out")
            if entry.get("price_status") == "known" and price_in is not None \
                    and price_out is not None:
                usage = {**usage, "cost_usd": round(
                    usage["input_tokens"] / 1_000_000 * float(price_in)
                    + usage["output_tokens"] / 1_000_000 * float(price_out), 6)}
            self._record_inference_usage(
                tenant_id, "chat", usage,
                f"chat reply via combo {combo['name']} ({answered_by})",
                category="chat")
        except Exception:
            pass
        out = {"reply": reply, "model": answered_by,
               "source": "combo", "combo": combo["name"],
               "token_estimate": estimate,
               "token_reconciliation": reconciliation,
               "response_truncated": sized["truncated"]}
        if warning:
            out["context_warning"] = warning
        if switches:
            from .model_registry import model_switch_notice
            first = switches[0]
            out["model_switch_notice"] = model_switch_notice(
                str(first.get("from", "")).split(":", 1)[-1],
                str(first.get("to", "")).split(":", 1)[-1])
        return out

    async def chat_reply_stream(self, tenant_id: str, message: str,
                                history: list[dict] | None = None,
                                model: str | None = None,
                                models: list[str] | None = None):
        """Streaming chat (W6 gap 5): async generator of event dicts
        — {"type": "working"} first (a real indicator, not content),
        then {"type": "delta", "text"} as the reply arrives,
        {"type": "notice", "text"} for a model switch or a context
        warning, and finally {"type": "done", "model", "usage", ...}
        or {"type": "error", "message"} in plain words.

        Resolution mirrors chat_reply (pin → combo → default). When
        the selected model is stamped as streaming-incapable, the
        stream yields an unsupported error event instead of silently
        falling back to a whole reply (W6 A3). Early cancellation:
        closing the generator stops reading the provider stream.
        A multi-model list is validated before any model call; a
        failing entry yields a plain-words error event naming it."""
        if models is not None:
            if len(models) == 1:
                model = models[0]
                models = None
            elif len(models) >= 2:
                try:
                    targets = resolve_model_list(
                        self.store, tenant_id, models)
                except ModelListEntryError as exc:
                    yield {"type": "error", "message": str(exc)}
                    return
                combo = ephemeral_model_list_combo(targets)
                state = resolve_state(self.settings, self.store, tenant_id)
                if not state.connected:
                    yield {"type": "error", "message": state.message}
                    return
                yield {"type": "working"}
                async for event in self._chat_stream_combo_record(
                        tenant_id, message, history, combo, state):
                    yield event
                return
        state = resolve_state(self.settings, self.store, tenant_id)
        if not state.connected:
            yield {"type": "error", "message": state.message}
            return
        yield {"type": "working"}
        if model is not None and model.startswith("combo:"):
            async for event in self._chat_stream_combo(
                    tenant_id, message, history, model, state):
                yield event
            return
        effective_model = state.model
        conn = entry = None
        if model is not None:
            active = {c["id"]: c for c in self.store.list_connections(tenant_id)
                      if c.get("state") == "active"}
            for candidate in self.store.list_catalog(tenant_id):
                pinned_conn = active.get(candidate.get("connection_id"))
                if pinned_conn is not None \
                        and candidate.get("model_id") == model:
                    entry, conn = candidate, pinned_conn
                    conn = {**conn, "tenant_id": tenant_id}
                    effective_model = model
                    break
            if conn is None and model != state.model:
                yield {"type": "error",
                       "message": f"Chat model pin '{model}' cannot be honored."}
                return
        if conn is None:
            if state.source == "byok":
                from .modelstate import _pick_catalog_entry
                active = {c["id"]: c for c in self.store.list_connections(tenant_id)
                          if c.get("state") == "active"}
                entries = [(e, active[e.get("connection_id")])
                           for e in self.store.list_catalog(tenant_id)
                           if e.get("connection_id") in active]
                entry, conn = _pick_catalog_entry(self.settings, entries)
                conn = {**conn, "tenant_id": tenant_id}
            else:
                conn, entry = self._platform_connection(base_url=state.base_url,
                                                        model=state.model)
                conn = {**conn, "tenant_id": tenant_id}
        # A3: a stamped streaming failure is an honest error event,
        # never a silent whole-reply fallback.
        try:
            badges = self.store.latest_capability_badges(tenant_id)
            stamp = (badges.get((conn["id"], effective_model)) or {}).get("streaming")
        except Exception:
            stamp = None
        if stamp == "fail":
            from .model_registry import unsupported_warning
            yield {"type": "error",
                   "message": unsupported_warning("streaming", "fail", effective_model)}
            return
        gateway = self._gateway_for(conn)
        messages = ([{"role": "system", "content": self.CHAT_SYSTEM_PROMPT}]
                    + self._chat_turns(history)
                    + [{"role": "user", "content": message}])
        estimate = self._chat_estimate(messages)
        warning = self._chat_context_warning(entry, estimate)
        if warning:
            yield {"type": "notice", "text": warning}
        text_parts: list[str] = []
        usage: dict = {}
        try:
            stream = gateway.chat_stream(effective_model, messages)
            if hasattr(stream, "__aiter__"):
                async for event in stream:
                    if event.get("type") == "delta":
                        text_parts.append(event.get("text") or "")
                        yield event
                    elif event.get("type") == "tool_call":
                        yield event
                    elif event.get("type") == "done":
                        usage = event.get("usage") or {}
                        yield {"type": "done", "model": effective_model,
                               "source": state.source,
                               "reply": "".join(text_parts),
                               "usage": usage,
                               "finish_reason": event.get("finish_reason"),
                               "token_estimate": estimate,
                               "token_reconciliation": self._chat_reconcile(
                                   tenant_id, effective_model, estimate, usage)}
                        return
                    elif event.get("type") == "error":
                        yield {"type": "error",
                               "message": event.get("message") or
                                          "The model could not stream a reply."}
                        return
            else:
                yield {"type": "error",
                       "message": "This model cannot stream its reply."}
                return
        except GatewayError as exc:
            yield {"type": "error", "message": exc.category}
            return
        except Exception:
            yield {"type": "error",
                   "message": "The model's stream ended unexpectedly."}
            return

    async def _chat_stream_combo(self, tenant_id: str, message: str,
                                 history: list[dict] | None,
                                 combo_ref: str, state):
        """Combo streaming: the chain's failover (before the first
        chunk only) surfaces as a plain-words switch notice event."""
        ref = combo_ref[len("combo:"):]
        if ref == "auto":
            combo = {"combo_id": "auto", "name": "Auto combo",
                     "strategy": "auto", "targets": [],
                     "enabled": True, "state": None}
        else:
            combo = self.store.get_combo(tenant_id, ref)
            if not combo or not combo.get("enabled", True):
                yield {"type": "error",
                       "message": f"Chat model pin '{combo_ref}' cannot be honored."}
                return
        async for event in self._chat_stream_combo_record(
                tenant_id, message, history, combo, state):
            yield event

    async def _chat_stream_combo_record(self, tenant_id: str, message: str,
                                        history: list[dict] | None,
                                        combo: dict, state):
        """Stream through an already-resolved combo record — stored,
        auto, or an ephemeral multi-model list."""
        connections = {c["id"]: c for c in
                       self.store.list_connections(tenant_id)}
        entries = list(self.store.list_catalog(tenant_id))
        pconn, pentry = self._platform_connection(
            base_url=state.base_url, model=state.model)
        pconn = {**pconn, "tenant_id": tenant_id}
        connections["platform"] = pconn
        entries = entries + [pentry]
        switches: list[dict] = []
        gateway, entry, _conn, _n = self._combo_chain_gateway(
            tenant_id, combo, entries, connections,
            on_failover=lambda payload: switches.append(payload))
        messages = ([{"role": "system", "content": self.CHAT_SYSTEM_PROMPT}]
                    + self._chat_turns(history)
                    + [{"role": "user", "content": message}])
        estimate = self._chat_estimate(messages)
        text_parts: list[str] = []
        noticed = False
        try:
            async for event in gateway.chat_stream(entry["model_id"], messages):
                if event.get("type") == "delta":
                    if switches and not noticed:
                        from .model_registry import model_switch_notice
                        first = switches[0]
                        yield {"type": "notice", "text": model_switch_notice(
                            str(first.get("from", "")).split(":", 1)[-1],
                            str(first.get("to", "")).split(":", 1)[-1])}
                        noticed = True
                    text_parts.append(event.get("text") or "")
                    yield event
                elif event.get("type") == "done":
                    usage = event.get("usage") or {}
                    # Redesign fix: the done stamp must name the model
                    # that ANSWERED, not the chain's primary entry.
                    # Stream usage carries no model (normalize_usage
                    # drops it), so after a failover the old code
                    # stamped the failed primary. The answering
                    # target is the last switch's destination, else
                    # the primary entry.
                    if switches:
                        answered = str(switches[-1].get("to", "")).split(":", 1)[-1]
                    else:
                        answered = entry["model_id"]
                    yield {"type": "done", "model": usage.get("model") or answered,
                           "source": "combo", "combo": combo["name"],
                           "reply": "".join(text_parts), "usage": usage,
                           "token_estimate": estimate,
                           "token_reconciliation": self._chat_reconcile(
                               tenant_id, answered, estimate, usage)}
                    return
                elif event.get("type") == "error":
                    yield {"type": "error",
                           "message": event.get("message") or
                                      "No model in this combo could stream a reply."}
                    return
                else:
                    yield event
        except Exception:
            yield {"type": "error",
                   "message": "The model's stream ended unexpectedly."}
            return

    # ------------------------------------------------------------ guards
    def _check_cancelled(self, tenant_id: str, mission_id: str) -> None:
        fresh = self.store.get_mission(tenant_id, mission_id)
        if fresh and fresh["status"] == "cancelled":
            raise MissionCancelled(f"Mission {mission_id} cancelled")
        if fresh and fresh["status"] == "paused":
            raise MissionPaused(f"Mission {mission_id} paused")

    # v21 (AgentScope observability pattern): pipeline stage spans.
    _SPAN_BY_EVENT = {
        "MissionPlanned": "plan", "PanelSelected": "plan",
        "ModeSelected": "plan", "CandidateProposed": "research",
        "ModePhaseCompleted": "mode_phase",
        "ModeChannelPublished": "research",
        "CritiqueRoundCompleted": "critique",
        "EvaluationCompleted": "evaluation",
        "MissionCompleted": "complete", "MemoryExtracted": "evaluation",
        "MemoryContextInjected": "research",
    }

    def _event(self, tenant_id: str, mission_id: str, event_type: str, payload: dict,
               ctx: RunContext | None = None) -> None:
        # While a mission run is active, its stage events carry the
        # run's trace id (+ a per-event span id) so a whole run can be
        # followed across plan -> research -> critique -> evaluation.
        trace_id = ctx.trace_id if ctx is not None else None
        if trace_id:
            from .domain import new_id as _new_id
            payload = {**payload, "trace_id": trace_id,
                       "span_id": _new_id("spn"),
                       "span": self._SPAN_BY_EVENT.get(event_type, "misc")}
        self.store.put_event(tenant_id, event_envelope(
            tenant_id, "mission", mission_id, event_type, payload))

    def _transition(self, tenant_id: str, mission: dict, target: str) -> dict:
        # v21.4: assert against the FRESH stored status, not the
        # caller's possibly-stale dict — a transition computed from a
        # stale snapshot must never overwrite a newer state (a user
        # cancel landing mid-run is exactly that case).
        fresh = self.store.get_mission(tenant_id, mission["id"])
        current = fresh["status"] if fresh else mission["status"]
        assert_transition(current, target, MISSION_TRANSITIONS, "Mission")
        self.store.set_mission_status(tenant_id, mission["id"], target)
        return self.store.get_mission(tenant_id, mission["id"])

    # ------------------------------------------------- V3: routed runtime
    def _platform_connection(self, base_url: str | None = None,
                             model: str | None = None) -> tuple[dict, dict]:
        """The platform default connection (env endpoint, or an
        auto-detected OmniRoute/Ollama when the resolver found one),
        represented as a synthetic catalog entry + connection so the
        router treats it uniformly with tenant BYOK connections."""
        s = self.settings
        base_url = base_url or s.llm_base_url
        model = model or s.llm_model
        from .llm_adapter import _is_local_endpoint
        kind = "local_endpoint" if _is_local_endpoint(base_url) else "direct_provider"
        conn = {"id": "platform", "kind": kind, "state": "active",
                "base_url": base_url, "allowed_models": [], "blocked_models": [],
                "data_policy": {}, "routing_mode": "balanced"}
        entry = {"tenant_id": "", "connection_id": "platform", "model_id": model,
                 "provider": "platform", "display_name": model,
                 "capabilities": {"chat": "advertised", "structured_output": "unknown"},
                 "price_in": s.llm_price_in_per_m, "price_out": s.llm_price_out_per_m,
                 "price_source": "operator-config", "price_status": "known",
                 "health": "ok", "last_seen": now_iso(), "deprecated": False}
        return conn, entry

    def _gateway_for(self, conn: dict, on_rate_limited=None) -> GatewayClient:
        # connection_id keys the client's shared rate-limit pacing
        # (v21.4.4); on_rate_limited, when given, turns rate-limit
        # waits into mission events (see _prepare_runtime).
        if conn["id"] == "platform":
            return GatewayClient(base_url=conn.get("base_url") or self.settings.llm_base_url,
                                 api_key=self.settings.llm_api_key, timeout=180,
                                 connection_id="platform",
                                 on_rate_limited=on_rate_limited)
        from .connections import get_secret_for_call
        if self.vault is None:
            raise GatewayError("auth_failed", "vault unavailable for BYOK connection")
        secret = get_secret_for_call(self.store, self.vault, conn["tenant_id"], conn["id"])
        return GatewayClient(base_url=conn["base_url"], api_key=secret, timeout=180,
                             connection_id=conn["id"],
                             headers=conn.get("headers") or {},
                             on_rate_limited=on_rate_limited)

    # ------------------------------------------------------------ combos (2026-10-23, item 7)
    def _resolve_combo(self, tenant_id: str, pinned_model_id,
                       mission_id: Optional[str] = None, ctx=None):
        """Combo selection for a run, OmniRoute-style: an explicit
        `combo:` pin wins; otherwise a glob MAPPING whose pattern
        matches the pinned model may route it through a combo.
        Returns (combo_record, via) or (None, None). A combo pin
        naming a missing/disabled combo is an honest failure — a
        pin is never silently served by another model."""
        from .combos import match_mapping
        pinned = pinned_model_id
        if pinned and pinned.startswith("combo:"):
            ref = pinned[len("combo:"):]
            if ref == "auto":
                return ({"combo_id": "auto", "name": "Auto combo",
                         "strategy": "auto", "targets": [],
                         "enabled": True, "state": None}, "pin")
            combo = self.store.get_combo(tenant_id, ref)
            if combo and combo.get("enabled", True):
                return combo, "pin"
            if mission_id:
                self._event(tenant_id, mission_id, "ComboUnavailable",
                            {"combo": ref,
                             "reason": "missing or turned off"}, ctx=ctx)
            raise RuntimeError(
                "The pinned combo is not available (deleted or "
                "turned off). Refusing to substitute another model.")
        if pinned:
            m = match_mapping(
                self.store.list_combo_mappings(tenant_id), pinned)
            if m:
                combo = self.store.get_combo(tenant_id, m["combo_id"])
                if combo and combo.get("enabled", True):
                    return combo, "mapping"
        return None, None

    def _combo_chain_gateway(self, tenant_id: str, combo: dict,
                             entries: list, connections: dict, *,
                             mission_id: Optional[str] = None,
                             role: Optional[str] = None, ctx=None,
                             on_rate_limited=None, on_failover=None):
        """Resolve a combo to its ordered eligible chain and build
        the FailoverGateway over per-target gateways. Returns
        (gateway, primary_entry, primary_conn, chain_length).
        Combo runtime state (counters, last-good, sticky) is
        persisted for stored combos — that state is what the
        fill-first / lkgp / least-used strategies read."""
        from .combos import (FailoverGateway, empty_state,
                              resolve_chain)
        pairs = resolve_chain(combo, entries, connections)
        if not pairs:
            if mission_id:
                self._event(tenant_id, mission_id, "ComboUnavailable",
                            {"combo": combo.get("name"),
                             "reason": "no eligible targets"}, ctx=ctx)
            raise RuntimeError(
                f"Combo '{combo.get('name')}' has no eligible "
                "targets — every target's connection is inactive "
                "or its model is no longer in the catalog.")
        stored = combo.get("combo_id") not in (None, "auto")
        if stored:
            # persist the counter advances resolve_chain just made
            self.store.put_combo(tenant_id, combo)

        def _outcome(key, ok, _combo=combo, _stored=stored):
            if not _stored:
                return
            st = _combo.setdefault("state", empty_state())
            if ok:
                st["last_good"] = key
                st["calls"][key] = st["calls"].get(key, 0) + 1
                if _combo["strategy"] == "fill-first":
                    st["sticky"] = key
            else:
                st["failures"][key] = st["failures"].get(key, 0) + 1
            self.store.put_combo(tenant_id, _combo)

        def _failover(payload, _combo=combo, _stored=stored):
            if _stored and _combo["strategy"] == "fill-first":
                _combo.setdefault("state", empty_state())["sticky"] \
                    = payload["to"]
                self.store.put_combo(tenant_id, _combo)
            if mission_id:
                from .model_registry import model_switch_notice
                _from = str(payload.get("from", "")).split(":", 1)[-1]
                _to = str(payload.get("to", "")).split(":", 1)[-1]
                self._event(tenant_id, mission_id, "ComboFailover",
                            {**payload, "role": role,
                             "combo": _combo.get("name"),
                             "notice": model_switch_notice(_from, _to)},
                            ctx=ctx)
            # W6 A4: an external failover observer (chat) gets the
            # same payload, so a model switch is never silent there
            # either. A broken observer must never break the call.
            if on_failover is not None:
                try:
                    on_failover(payload)
                except Exception:
                    pass

        chain = []
        for e, c in pairs:
            gw = self._gateway_for({**c, "tenant_id": tenant_id},
                                   on_rate_limited=on_rate_limited)
            chain.append((gw, e["model_id"],
                          f"{e['connection_id']}:{e['model_id']}"))
        gateway = FailoverGateway(chain, on_failover=_failover,
                                  on_outcome=_outcome)
        entry, conn = pairs[0]
        return gateway, entry, conn, len(pairs)

    def _prepare_runtime(self, tenant_id: str, mission: dict) -> RunContext:
        """Resolve per-role adapters through the router into a fresh
        RunContext: {role: {adapter, version, decision, entry}}.

        v20 gate: generation requires a connected real model. A pinned
        BYOK connection is validated strictly (as before); otherwise the
        model-state resolver (tenant BYOK → env endpoint → OmniRoute →
        Ollama) must find a model or ModelNotConnected is raised before
        any planning or drafting happens."""
        ctx = RunContext()
        contract = mission["contract"]
        pinned_conn_id = contract.get("connection_id")
        mode = contract.get("routing_mode") or "balanced"

        connections = {c["id"]: c for c in self.store.list_connections(tenant_id)}
        entries = self.store.list_catalog(tenant_id)
        platform_conn, platform_entry = self._platform_connection()
        platform_conn = {**platform_conn, "tenant_id": tenant_id}
        connections["platform"] = platform_conn
        entries = entries + [platform_entry]

        if pinned_conn_id:
            conn = connections.get(pinned_conn_id)
            if conn is None or conn["state"] != "active":
                self._event(tenant_id, mission["id"], "ConnectionUnavailable",
                            {"connection_id": pinned_conn_id,
                             "reason": "missing, revoked or not active"})
                raise RuntimeError(
                    f"Mission connection {pinned_conn_id} is not available "
                    "(missing/revoked/inactive). Refusing to substitute another key.")
            ctx.strict_byok = True
            entries = [e for e in entries if e["connection_id"] == pinned_conn_id]
            if not entries:
                self._event(tenant_id, mission["id"], "ConnectionUnavailable",
                            {"connection_id": pinned_conn_id, "reason": "catalog empty — "
                             "validate/sync the connection first"})
                raise RuntimeError("Pinned connection has an empty model catalog; "
                                   "validate the connection to sync its models first.")
            mode = mode or conn.get("routing_mode") or "balanced"
            ctx.model_state = ModelState(connected=True, source="byok")
        else:
            # v20 gate (unpinned runs): tenant BYOK → env endpoint →
            # OmniRoute → Ollama, resolved by app/modelstate.py.
            state = resolve_state(self.settings, self.store, tenant_id)
            if not state.connected:
                self._event(tenant_id, mission["id"], "ModelNotConnected",
                            {"message": state.message})
                raise ModelNotConnected(state.message)
            ctx.model_state = state
            # Route against the RESOLVED platform endpoint (an
            # auto-detected sidecar/Ollama may differ from the raw env
            # defaults); with no platform source, BYOK entries only.
            pstate = platform_state(self.settings)
            entries = [e for e in entries if e["connection_id"] != "platform"]
            connections.pop("platform", None)
            if pstate.connected:
                platform_conn, platform_entry = self._platform_connection(
                    base_url=pstate.base_url, model=pstate.model)
                platform_conn = {**platform_conn, "tenant_id": tenant_id}
                connections["platform"] = platform_conn
                entries = entries + [platform_entry]

        # Multi-model list (redesign): an ordered failover list on
        # the contract. 2+ entries take the combo branch for BOTH
        # roles through an ephemeral priority combo ("Chosen
        # models") — each role tries the list in order and uses the
        # first model that answers for that role, so the two roles
        # MAY be served by different listed models. Validated here
        # strictly, before planning/drafting: if any entry is now
        # ineligible the run fails honestly naming that entry (the
        # run_mission handler turns this into a FAILED mission with
        # a ModelListUnavailable event) and runs on NO substitute.
        # A 1-entry list normalises to the single pin. When both
        # pinned_model_id and pinned_model_ids arrive, the list wins
        # and the single key is ignored (documented, not an error).
        list_ids = contract.get("pinned_model_ids") or []
        effective_pinned = contract.get("pinned_model_id")
        list_combo = None
        if len(list_ids) >= 2:
            # Resolve against the store view only (spec §2.4):
            # the synthetic platform entry is NOT a list target —
            # a list entry must be a catalogued model on an active
            # BYOK connection, never a silent platform substitute
            # for a model that was deleted. The pinned-connection
            # filter above still applies (entries were filtered).
            try:
                list_targets = resolve_model_list(
                    self.store, tenant_id, list_ids,
                    entries=[e for e in entries
                             if e.get("connection_id") != "platform"],
                    connections={k: v for k, v in connections.items()
                                 if k != "platform"})
            except ModelListEntryError as exc:
                self._event(tenant_id, mission["id"], "ModelListUnavailable",
                            {"model": exc.entry, "reason": str(exc)})
                raise
            list_combo = ephemeral_model_list_combo(list_targets)
        elif len(list_ids) == 1:
            effective_pinned = list_ids[0]

        # Combos (item 7): a combo pin — or a glob mapping matching
        # the pinned model — replaces single-model routing for
        # every role with the combo's failover chain.
        if list_combo is not None:
            combo_rec, combo_via = list_combo, "list"
        else:
            combo_rec, combo_via = self._resolve_combo(
                tenant_id, effective_pinned,
                mission["id"], ctx)

        for role, adapter_cls in (("researcher", LLMResearchAdapter),
                                  ("critic", LLMCriticAdapter)):
            if combo_rec is not None:
                from .combos import _est_cost as _combo_est_cost
                from .domain import new_id as _new_id
                gateway, entry, conn, n_targets = \
                    self._combo_chain_gateway(
                        tenant_id, combo_rec, entries, connections,
                        mission_id=mission["id"], role=role, ctx=ctx,
                        on_rate_limited=lambda payload, _tid=tenant_id,
                        _mid=mission["id"], _ctx=ctx:
                            self._event(_tid, _mid,
                                        "ProviderRateLimited",
                                        payload, ctx=_ctx))
                est = _combo_est_cost(entry)
                decision = {
                    "id": _new_id("rtd"), "tenant_id": tenant_id,
                    "mission_id": mission["id"], "role": role,
                    "connection_id": conn["id"],
                    "model_id": entry["model_id"], "mode": "combo",
                    "reason_codes": [
                        f"combo:{combo_rec['name']}",
                        f"via:{combo_via}",
                        f"strategy:{combo_rec['strategy']}",
                        f"targets:{n_targets}", "failover:on"],
                    "filtered_counts": {},
                    "snapshot_id": entry.get("last_seen"),
                    "estimated_cost": est,
                    "estimate_confidence": (
                        "high" if entry.get("price_status") == "known"
                        else "unknown"),
                    "created_at": now_iso()}
                self.store.put_route_decision(decision)
                adapter = adapter_cls(
                    self.settings, client=gateway,
                    model=decision["model_id"],
                    price_in=entry.get("price_in"),
                    price_out=entry.get("price_out"),
                    price_known=entry.get("price_status") == "known")
                ctx.runtime[role] = {"adapter": adapter,
                                     "version": LLM_ADAPTER_VERSION,
                                     "decision": decision,
                                     "entry": entry, "conn": conn}
                self._event(tenant_id, mission["id"],
                            "RouteDecisionCreated",
                            {"role": role, "model": decision["model_id"],
                             "connection_id": conn["id"], "mode": "combo",
                             "reasons": decision["reason_codes"],
                             "estimate_confidence":
                                 decision["estimate_confidence"]})
                continue
            req = RouteRequest(
                tenant_id=tenant_id, mode=mode, mission_id=mission["id"], role=role,
                pinned_model_id=effective_pinned,
                data_classification=contract.get("data_classification", "internal"),
                # v21.3: no user-facing budget — the router never
                # budget-filters a mission run (None = no budget gate).
                budget_remaining_usd=None,
                unknown_price_reserve_usd=self.settings.reserve_unknown_call_usd)
            try:
                decision = route(entries, connections, {}, req)
            except NoEligibleModel as exc:
                self._event(tenant_id, mission["id"], "RouteDecisionFailed",
                            {"role": role, "filtered": exc.filtered_counts})
                raise RuntimeError(f"No eligible model for role {role}: {exc}") from exc
            decision["created_at"] = now_iso()
            self.store.put_route_decision(decision)
            conn = connections[decision["connection_id"]]
            entry = next(e for e in entries
                         if e["connection_id"] == conn["id"] and e["model_id"] == decision["model_id"])
            gateway = self._gateway_for(
                {**conn, "tenant_id": tenant_id},
                on_rate_limited=lambda payload, _tid=tenant_id,
                _mid=mission["id"], _ctx=ctx:
                    self._event(_tid, _mid, "ProviderRateLimited",
                                payload, ctx=_ctx))
            adapter = adapter_cls(self.settings, client=gateway, model=decision["model_id"],
                                  price_in=entry.get("price_in"), price_out=entry.get("price_out"),
                                  price_known=entry.get("price_status") == "known")
            ctx.runtime[role] = {"adapter": adapter, "version": LLM_ADAPTER_VERSION,
                                 "decision": decision, "entry": entry, "conn": conn}
            self._event(tenant_id, mission["id"], "RouteDecisionCreated",
                        {"role": role, "model": decision["model_id"],
                         "connection_id": conn["id"], "mode": mode,
                         "reasons": decision["reason_codes"],
                         "estimate_confidence": decision["estimate_confidence"]})
        return ctx

    # ------------------------------------------- reserved, routed invocation
    async def _invoke_routed(self, tenant_id: str, mission: dict, role: str,
                             invoke_fn, manifest: dict,
                             ctx: RunContext | None = None):
        """One routed model call with a budget reservation. v20: a failed
        call fails the run — there is no substitute engine to fall back
        to, and pretending otherwise produced canned missions.

        The routing state comes from the run's OWN RunContext (v21.4).
        ctx=None marks an unrouted caller (the arena): an adapter bound
        to whatever model is actually connected for this tenant."""
        runtime = ctx.runtime.get(role) if ctx is not None else None
        if runtime is None:
            adapter = self._unrouted_adapter(tenant_id, role)
            return await invoke_fn(adapter), self.adapter_version
        adapter = runtime["adapter"]
        decision = runtime["decision"]
        # v21.3: reservations are accounting + runaway protection only.
        # The cap is the single internal ceiling — never a value from
        # the mission contract — so no user-set budget can block a call.
        cap = MISSION_BUDGET_CEILING_USD
        est = decision.get("estimated_cost")
        reserve_amt = (est * 1.5) if est is not None else self.settings.reserve_unknown_call_usd
        reservation = self.store.reserve_budget(tenant_id, mission["id"], reserve_amt, cap)
        if reservation is None:
            self._event(tenant_id, mission["id"], "BudgetExhausted",
                        {"role": role, "needed_reserve_usd": reserve_amt, "cap_usd": cap},
                        ctx=ctx)
            raise BudgetExceeded(f"budget reservation of ${reserve_amt:.2f} exceeds mission cap")
        try:
            response = await invoke_fn(adapter)
        except Exception as exc:
            self.store.settle_reservation(reservation, "released")
            if ctx is not None and ctx.strict_byok:
                if isinstance(exc, GatewayError):
                    self._event(tenant_id, mission["id"], "ConnectionCallFailed",
                                {"role": role, "category": exc.category,
                                 "connection_id": runtime["conn"]["id"]}, ctx=ctx)
                    raise RuntimeError(
                        f"BYOK connection call failed ({exc.category}); mission stopped rather "
                        "than silently switching keys or engines.") from exc
                # Audit B (2026-10-12): the provider answered, but the
                # ADAPTER rejected its output (bad shape, missing
                # deliverable, ...). The old code blamed the connection
                # with category "unknown" for these too, hiding the real
                # reason from the owner. Name the actual failure; the
                # no-silent-switch discipline is unchanged.
                self._event(tenant_id, mission["id"], "ModelCallFailed",
                            {"role": role, "reason": str(exc)[:300],
                             "connection_id": runtime["conn"]["id"]}, ctx=ctx)
                raise RuntimeError(
                    f"Model output failed for role {role} via BYOK connection "
                    f"{runtime['conn']['id']}: {exc}; mission stopped rather "
                    "than silently switching keys or engines.") from exc
            self._event(tenant_id, mission["id"], "ModelCallFailed",
                        {"agent": manifest.get("agent_id"), "role": role,
                         "reason": str(exc)[:300]}, ctx=ctx)
            raise RuntimeError(
                f"Model call failed for role {role} "
                f"({runtime['entry'].get('model_id')} via "
                f"{runtime['conn']['id']}): {exc}") from exc
        self.store.settle_reservation(reservation, "reconciled")
        return response, runtime["version"]

    def _unrouted_adapter(self, tenant_id: str, role: str):
        """Adapter for callers outside a mission run (the arena): bound
        to the tenant's BYOK connection when they have one, else to the
        resolved platform endpoint (env / OmniRoute / Ollama) — never to
        a raw default that resolution already found dead."""
        cls = LLMResearchAdapter if role == "researcher" else LLMCriticAdapter
        pstate = platform_state(self.settings)
        if not pstate.connected and tenant_id:
            try:
                active = {c["id"]: c for c in self.store.list_connections(tenant_id)
                          if c.get("state") == "active"}
                for entry in self.store.list_catalog(tenant_id):
                    conn = active.get(entry.get("connection_id"))
                    if conn is not None:
                        gateway = self._gateway_for({**conn, "tenant_id": tenant_id})
                        return cls(self.settings, client=gateway,
                                   model=entry.get("model_id"),
                                   price_known=entry.get("price_status") == "known")
            except Exception:
                pass
        if pstate.connected and (pstate.base_url != self.settings.llm_base_url
                                 or pstate.model != self.settings.llm_model):
            gateway = GatewayClient(base_url=pstate.base_url,
                                    api_key=self.settings.llm_api_key, timeout=180,
                                    connection_id="platform")
            return cls(self.settings, client=gateway, model=pstate.model)
        return self.research_adapter if role == "researcher" else self.critic_adapter

    def _with_persona(self, manifest: dict, tenant_id: str = "") -> dict:
        """Imported persona agents carry their brief into the LLM call.
        Both rosters (agency.* from v13, lib.* from v14) store briefs in
        the shared scope of the persona store — the v15 arena relies on
        this path treating them identically. Workspace-authored agents
        (custom.*, v21.2) keep their brief in the TENANT's scope (v21.4):
        the tenant is required to resolve it, because a same-named
        custom agent in another workspace has its own brief."""
        if str(manifest.get("agent_id", "")).startswith(
                ("agency.", "lib.", "pack.", "custom.")):
            persona = self.store.get_persona(manifest["agent_id"], tenant_id)
            if persona:
                return {**manifest, "persona_prompt": persona}
        return manifest

    async def _invoke_research(self, tenant_id: str, mission: dict, request, manifest,
                               ctx: RunContext | None = None):
        manifest = self._with_persona(manifest, tenant_id)
        return await self._invoke_routed(
            tenant_id, mission, "researcher",
            lambda a: a.invoke(request, manifest), manifest, ctx=ctx)

    async def _invoke_critic(self, tenant_id: str, mission: dict, request, manifest,
                             ctx: RunContext | None = None):
        return await self._invoke_routed(
            tenant_id, mission, "critic",
            lambda a: a.invoke(request, manifest), manifest, ctx=ctx)

    # ------------------------------------------------------------- the run
    async def run_mission(self, tenant_id: str, mission_id: str,
                          job_id: str | None = None) -> dict[str, Any]:
        mission = self.store.get_mission(tenant_id, mission_id)
        if mission is None:
            raise KeyError("mission not found")
        # Audit B4 + B3: runnability is validated BEFORE any mutation
        # (runtime prep, re-planning, task churn). Previously a
        # COMPLETED mission was fully re-planned — its tasks closed
        # and recreated, its plan artifact overwritten, MissionPlanned
        # emitted — and only then died on the state machine; and a
        # second execution reaching a RUNNING mission re-planned over
        # the live run, cancelling the first run's tasks underneath
        # it. Terminal missions refuse here, untouched. A RUNNING
        # mission proceeds only when no OTHER active mission_run job
        # owns it — the job-retry / lease-reclaim shape, where this
        # invocation IS the owning job's continuation (or a direct
        # call with no job in play); a foreign active job means a
        # live run owns the mission and this invocation must never
        # reach the planning code that would cancel its tasks.
        if mission["status"] in (MissionStatus.COMPLETED.value,
                                  MissionStatus.CANCELLED.value):
            raise StateConflict(
                f"Mission cannot transition from {mission['status']} to running")
        if mission["status"] == MissionStatus.RUNNING.value:
            owner = self.store.get_active_job(
                tenant_id, mission_id, kind="mission_run",
                exclude_job_id=job_id)
            if owner is not None:
                raise StateConflict(
                    "Mission is already running under another job "
                    f"({owner['id']})")
        started = time.time()
        contract = mission["contract"]
        try:
            ctx = self._prepare_runtime(tenant_id, mission)
        except ModelNotConnected as exc:
            # v20: fail fast and honestly — the mission ends FAILED with
            # the reason on record; it never "completes" without a model.
            self._event(tenant_id, mission_id, "MissionFailed",
                        {"reason": "model_not_connected", "message": str(exc)})
            try:
                # v22 (R2): the failure becomes a structured lesson.
                from .evolve import record_lesson
                record_lesson(self.store, tenant_id, subject_type="mission",
                              subject=mission_id, outcome="failed",
                              category="provider_error",
                              cause=f"model_not_connected: {str(exc)[:200]}",
                              evidence_id=mission_id, source_kind="mission",
                              source_id=mission_id)
            except Exception:
                pass  # lessons never break a mission
            if mission["status"] not in (MissionStatus.FAILED.value,
                                          MissionStatus.CANCELLED.value,
                                          MissionStatus.COMPLETED.value):
                self.store.set_mission_status(tenant_id, mission_id,
                                              MissionStatus.FAILED.value)
                # Audit R (bug #3): close any open tasks from a prior
                # attempt with the mission.
                self.store.close_open_tasks(tenant_id, mission_id, "failed")
            return self.mission_detail(tenant_id, mission_id)
        except ModelListEntryError as exc:
            # Redesign multi-model list, strict pre-run rule: a list
            # entry that is gone/paused/blocked/retired by run time
            # fails the mission honestly before planning/drafting,
            # naming the entry — it never runs on a subset and never
            # falls back to Auto. (The ModelListUnavailable event
            # itself is emitted by _prepare_runtime.)
            self._event(tenant_id, mission_id, "MissionFailed",
                        {"reason": "model_list_entry_not_found",
                         "message": str(exc)})
            if mission["status"] not in (MissionStatus.FAILED.value,
                                          MissionStatus.CANCELLED.value,
                                          MissionStatus.COMPLETED.value):
                self.store.set_mission_status(tenant_id, mission_id,
                                              MissionStatus.FAILED.value)
                self.store.close_open_tasks(tenant_id, mission_id, "failed")
            return self.mission_detail(tenant_id, mission_id)

        from .domain import new_id as _new_id
        ctx.trace_id = _new_id("trc")
        if mission["status"] == MissionStatus.FAILED.value:
            mission = self._transition(tenant_id, mission, MissionStatus.DRAFT.value)
            self._event(tenant_id, mission_id, "MissionResetForReplay", {}, ctx=ctx)
        if mission["status"] == MissionStatus.PAUSED.value:
            mission = self._transition(tenant_id, mission, MissionStatus.RUNNING.value)
            self._event(tenant_id, mission_id, "MissionResumed", {}, ctx=ctx)
        if mission["status"] == MissionStatus.DRAFT.value:
            mission = self._transition(tenant_id, mission, MissionStatus.PLANNED.value)
        tasks, reserve = build_plan(mission_id, contract)
        # Audit R (bug #3 sibling): a re-plan (resume after a mid-run
        # pause, replay of a failed mission) supersedes the previous
        # attempt's tasks — close any still open, or the detail view
        # keeps a phantom 'running' research task from the old attempt
        # forever, next to the new attempt's task set.
        self.store.close_open_tasks(tenant_id, mission_id, "cancelled")
        for t in tasks:
            self.store.create_task(tenant_id, t.model_dump())
        self.store.put_artifact(tenant_id, mission_id, "plan",
                                {"plan_version": "plan-v1", "tasks": [t.model_dump() for t in tasks],
                                 "evaluation_reserve_usd": reserve},
                                {"produced_by": "planner"})
        self._event(tenant_id, mission_id, "MissionPlanned",
                    {"tasks": len(tasks), "evaluation_reserve_usd": reserve}, ctx=ctx)
        if mission["status"] == MissionStatus.RUNNING.value:
            self._event(tenant_id, mission_id, "MissionResumed", {}, ctx=ctx)
        else:
            mission = self._transition(tenant_id, mission, MissionStatus.RUNNING.value)

        research_task, critique_task, synthesis_task = tasks
        agents = self.store.list_agents(tenant_id)

        # v13: the whole roster is the pool. A suitability-ranked PANEL
        # (not a fixed pair) drafts — ranked per task type from
        # capability/division fit blended with the measured performance
        # ledger (app/suitability.py).
        from .suitability import PANEL_MIN, rank_panel
        task_type = contract.get("task_type") or "strategy"
        # v21: a mission MODE replaces the panel with its own role
        # pack + phase pipeline (app/modes.py); the rest of the
        # harness (research corpus, critic, evaluator, ledger) is
        # unchanged.
        mode_def = get_mode(contract.get("mode"))
        if mode_def is not None:
            chosen = []
            self._event(tenant_id, mission_id, "ModeSelected", {
                "mode": mode_def["id"], "name": mode_def["name"],
                "source": mode_def["source"], "pack": mode_def["pack"],
                "phases": [p["key"] for p in
                           resolve_phases(mode_def, contract)]}, ctx=ctx)
        else:
            stats = self.store.list_agent_stats(tenant_id)
            panel = rank_panel(agents, task_type, stats)
            if len(panel) < PANEL_MIN:
                mission = self._transition(tenant_id, mission, MissionStatus.FAILED.value)
                self.store.close_open_tasks(tenant_id, mission_id, "failed")
                raise RuntimeError("Fewer than two eligible drafting agents in the registry")
            chosen = [entry["manifest"] for entry in panel]
            self._event(tenant_id, mission_id, "PanelSelected", {
                "task_type": task_type,
                "panel": [{"agent": e["agent_id"], "score": e["score"],
                           "fit": e["fit"], "runs": e["runs"]} for e in panel]}, ctx=ctx)
        self.store.update_task(tenant_id, research_task.task_id, "running")
        candidates: list[Candidate] = []
        skill_ids: list[str] = []  # v22.1: filled by the S3 retrieval below
        try:
            # ---- Phase 5 research stage: ONE gateway-authorized web.read
            # executes the mission's live research (or seed fallback)
            # before any agent runs; the corpus reaches every agent.
            corpus: list[dict] = []
            fresh0 = self.store.get_mission(tenant_id, mission_id)
            decision = self.gateway.authorize_and_run(
                tenant_id, fresh0, "web.read", {"query": contract["objective"]})
            research_blocked = (not decision.allowed
                                and decision.reason == "BUDGET_EXCEEDED")
            if decision.result:
                res = decision.result
                if res.get("mode") == "live":
                    self._event(tenant_id, mission_id, "ResearchCompleted",
                                {"mode": "live", "sources": res.get("sources", 0),
                                 "queries": res.get("queries", [])}, ctx=ctx)
                    corpus = [s for s in (self.store.get_source(tenant_id, sid)
                                          for sid in res.get("source_ids", [])) if s]
                elif res.get("status") == "fallback":
                    self._event(tenant_id, mission_id, "ResearchFallback",
                                {"mode": "seed", "reason": res.get("reason", "")}, ctx=ctx)
                elif "live web skipped" in str(res.get("note", "")):
                    self._event(tenant_id, mission_id, "ResearchSkipped",
                                {"mode": "seed", "note": res["note"]}, ctx=ctx)
            mem_context = memory_context(self.store, tenant_id, task_type)
            if mem_context:
                self._event(tenant_id, mission_id, "MemoryContextInjected",
                            {"facts": mem_context.count("[memory:")}, ctx=ctx)
            # v22.1 (S3): retrieve the top-matching skill-library
            # skills for the objective and inject them into the
            # drafting context next to memory — provenance-labelled
            # and budget-capped by app/skills.py. The ids are kept
            # for the terminal per-skill lessons. Strictly
            # best-effort: a library failure changes nothing.
            skill_ids: list[str] = []
            agent_context = mem_context
            try:
                from .skills import format_skills_section, retrieve_for_task
                found = retrieve_for_task(self.store, tenant_id,
                                          contract["objective"],
                                          self.settings)
                if found:
                    skill_ids = [s["id"] for s in found]
                    section = format_skills_section(found)
                    if section:
                        agent_context = (mem_context + "\n\n" + section
                                         if mem_context else section)
                        self._event(tenant_id, mission_id, "SkillInjected",
                                    {"skills": skill_ids,
                                     "source": "skill_library"}, ctx=ctx)
            except Exception:
                skill_ids, agent_context = [], mem_context
            if mode_def is not None and not research_blocked:
                mode_cand = await self._run_mode_pipeline(
                    tenant_id, mission, research_task, corpus,
                    context=agent_context, ctx=ctx)
                if mode_cand is not None:
                    candidates.append(mode_cand)
            for manifest in (chosen if not research_blocked else []):
                self._check_cancelled(tenant_id, mission_id)
                fresh = self.store.get_mission(tenant_id, mission_id)
                if fresh["spent"] >= fresh["budget_limit"]:
                    break
                request = AgentRequest(
                    mission_id=mission_id, task_id=research_task.task_id,
                    objective=contract["objective"], deliverables=contract["deliverables"],
                    output_schema={"version": research_task.output_schema_version},
                    sources=corpus,
                    task_type=contract.get("task_type") or "strategy",
                    context=agent_context)
                response, used_adapter_version = await self._invoke_research(
                    tenant_id, fresh, request, manifest, ctx=ctx)
                out = response.output
                cand = Candidate(
                    mission_id=mission_id, title=out["title"], strategy=out["strategy"],
                    summary=out["summary"], agent_version_id=out["agent_version_id"],
                    claims=out["claims"], assumptions=out["assumptions"], risks=out["risks"],
                    estimated_cost_usd=out["estimated_cost_usd"], time_to_learn=out["time_to_learn"],
                    deliverable=out.get("deliverable"))
                candidates.append(cand)
                self.store.put_candidate(tenant_id, cand.model_dump())
                art = self.store.put_artifact(
                    tenant_id, mission_id, "candidate",
                    cand.model_dump(), {"produced_by": cand.agent_version_id,
                                        "task_id": research_task.task_id})
                self.store.create_run(tenant_id, mission_id, research_task.task_id,
                                      cand.agent_version_id, used_adapter_version, response.usage)
                self._record_inference_usage(tenant_id, mission_id, response.usage,
                                             f"research run by {cand.agent_version_id}")
                self._event(tenant_id, mission_id, "CandidateProposed",
                            {"candidate_id": cand.candidate_id, "strategy": cand.strategy,
                             "agent": cand.agent_version_id, "artifact_id": art["artifact_id"]},
                            ctx=ctx)
        except BudgetExceeded:
            mission = self.store.get_mission(tenant_id, mission_id)
            if mission["status"] == "running":
                mission = self._transition(tenant_id, mission, MissionStatus.FAILED.value)
            # Audit R (bug #3): the research task is still 'running' and
            # critique/synthesis still 'queued' at this point — close
            # them with the mission instead of leaving them open.
            self.store.close_open_tasks(tenant_id, mission_id, "failed")
            self._event(tenant_id, mission_id, "MissionFailed",
                        {"reason": "budget_exhausted", "partial_candidates": len(candidates)},
                        ctx=ctx)
            # v22.1 (S3): injected skills share the failure outcome.
            for skill_id in skill_ids:
                try:
                    from .evolve import record_lesson
                    record_lesson(self.store, tenant_id,
                                  subject_type="skill", subject=skill_id,
                                  outcome="failed", category="other",
                                  cause="skill injected into mission "
                                        "that failed: budget exhausted",
                                  evidence_id=mission_id,
                                  source_kind="mission",
                                  source_id=mission_id)
                except Exception:
                    pass  # lessons never break a mission
            return self.mission_detail(tenant_id, mission_id)
        self.store.update_task(tenant_id, research_task.task_id, "completed",
                               assigned_agent=", ".join(c["agent_version_id"] for c in chosen))
        self._check_cancelled(tenant_id, mission_id)

        # ---- critique: one bounded round (MAX_ROUNDS is the ceiling)
        self.store.update_task(tenant_id, critique_task.task_id, "running")
        critics = rank_agents(agents, critique_task.required_capabilities)
        critiques: list[dict] = []
        if critics and candidates:
            manifest = critics[0]
            request = AgentRequest(
                mission_id=mission_id, task_id=critique_task.task_id,
                objective=contract["objective"], deliverables=contract["deliverables"],
                output_schema={"candidates": [c.model_dump() for c in candidates]},
                sources=corpus,
                task_type=contract.get("task_type") or "strategy")
            fresh = self.store.get_mission(tenant_id, mission_id)
            try:
                response, used_adapter_version = await self._invoke_critic(
                    tenant_id, fresh, request, manifest, ctx=ctx)
            except BudgetExceeded:
                response = None
                self._event(tenant_id, mission_id, "CritiqueSkippedBudget", {}, ctx=ctx)
            if response is not None:
                critiques = response.output["critiques"]
                for c in critiques:
                    self.store.put_critique(tenant_id, mission_id, c)
                self.store.create_run(tenant_id, mission_id, critique_task.task_id,
                                      f"{manifest['agent_id']}@{manifest['version']}",
                                      used_adapter_version, response.usage)
                self._record_inference_usage(tenant_id, mission_id, response.usage,
                                             "critique round 1 of max 2", category="evaluation")
                self._event(tenant_id, mission_id, "CritiqueRoundCompleted",
                            {"round": 1, "max_rounds": MAX_ROUNDS, "critiques": len(critiques)},
                            ctx=ctx)
        self.store.update_task(tenant_id, critique_task.task_id, "completed")
        self._check_cancelled(tenant_id, mission_id)

        # ---- synthesis + evaluation
        self.store.update_task(tenant_id, synthesis_task.task_id, "running")
        sources = {s["source_id"]: _src_obj(s) for s in self.store.list_sources(tenant_id)}
        evaluations = []
        elapsed = int((time.time() - started) * 1000)
        for cand in candidates:
            ev = evaluate_candidate(cand, critiques, sources, elapsed_ms=elapsed)
            evaluations.append(ev)
            self.store.put_evaluation(tenant_id, mission_id, ev.model_dump())
            self.store.put_candidate(tenant_id, cand.model_dump())
        self.store.add_usage(tenant_id, mission_id, "evaluation", 0.07 * max(1, len(candidates)),
                             "deterministic evaluation (schema + citation checks)")
        comparison = {
            "mission_id": mission_id,
            "candidates": [c.candidate_id for c in candidates],
            "evaluations": [e.evaluation_id for e in evaluations],
            "note": "Candidates differ in strategy, assumptions, cost and evidence quality — "
                    "not only in wording (Ch.10). Dimension scores are shown, not hidden in a total.",
        }
        self.store.put_artifact(tenant_id, mission_id, "comparison", comparison,
                                {"produced_by": "evaluator", "rubric_version": "research-v1"})
        self.store.update_task(tenant_id, synthesis_task.task_id, "completed")
        self._event(tenant_id, mission_id, "EvaluationCompleted",
                    {"candidates": len(candidates), "evaluations": len(evaluations)},
                    ctx=ctx)
        # v13: every panel agent's outcome lands in the performance
        # ledger — this is what makes the next panel smarter.
        try:
            from .suitability import empty_stat
            existing = {f"{x['agent_version_id']}|{x['task_type']}": x
                        for x in self.store.list_agent_stats(tenant_id)}
            ev_by_cand = {e.candidate_id: e for e in evaluations}
            for cand in candidates:
                ev = ev_by_cand.get(cand.candidate_id)
                if ev is None:
                    continue
                key = f"{cand.agent_version_id}|{task_type}"
                stat = existing.get(key) or empty_stat(cand.agent_version_id, task_type)
                stat["runs"] += 1
                stat["eval_sum"] += sum(d.score for d in ev.dimensions.values()) / max(1, len(ev.dimensions))
                stat["critiques_high"] += sum(
                    1 for c2 in critiques
                    if c2.get("target_candidate_id") == cand.candidate_id
                    and c2.get("severity") == "high")
                self.store.put_agent_stat(tenant_id, stat)
        except Exception:
            pass  # the ledger never breaks a mission

        # v21.4 (item: final-transition guard): re-check cancellation
        # against FRESH state immediately before the terminal
        # transition. The evaluation/ledger/memory tail above takes
        # real time; a cancel or pause landing during it must win —
        # _check_cancelled raises, the worker requeues/cancels the job,
        # and the mission keeps the user's state instead of being
        # flipped to completed from a stale in-memory snapshot.
        self._check_cancelled(tenant_id, mission_id)
        mission = self._transition(tenant_id, mission, MissionStatus.COMPLETED.value)
        try:
            written = extract_from_mission(self.store, tenant_id, mission_id)
            if written:
                self._event(tenant_id, mission_id, "MemoryExtracted",
                            {"facts": len(written)}, ctx=ctx)
        except Exception:
            pass  # memory never breaks a mission
        self._event(tenant_id, mission_id, "MissionCompleted",
                    {"spent_usd": mission["spent"], "budget_usd": mission["budget_limit"]},
                    ctx=ctx)
        try:
            # v22 (R2): a completed mission is an outcome lesson too —
            # the ledger learns what worked, not only what failed.
            from .evolve import record_lesson
            _rt = ctx.runtime.get("researcher") or {}
            _model = (_rt.get("decision") or {}).get("model_id")
            record_lesson(self.store, tenant_id,
                          subject_type="model" if _model else "mission",
                          subject=_model or mission_id,
                          outcome="completed", category="none",
                          cause="mission completed",
                          evidence_id=mission_id, source_kind="mission",
                          source_id=mission_id)
        except Exception:
            pass  # lessons never break a mission
        # v22.1 (S3): every injected skill earns the completion too.
        for skill_id in skill_ids:
            try:
                from .evolve import record_lesson
                record_lesson(self.store, tenant_id,
                              subject_type="skill", subject=skill_id,
                              outcome="completed", category="none",
                              cause="skill injected into mission "
                                    "that completed",
                              evidence_id=mission_id, source_kind="mission",
                              source_id=mission_id)
            except Exception:
                pass  # lessons never break a mission
        return self.mission_detail(tenant_id, mission_id)

    async def _run_mode_pipeline(self, tenant_id: str, mission: dict,
                                 research_task, corpus: list[dict],
                                 context: str = "",
                                 ctx: RunContext | None = None):
        """v21 mode execution: run the mode's phases in order, each
        staffed by its role-pack persona, each phase's artifact fed
        forward into the next phase's context (the SOP/debate/chain
        artifact flow of the source frameworks, re-implemented on the
        ordinary routed model calls). Assembles ONE candidate whose
        deliverable is the mode's shaped document; critique,
        evaluation and the ledger then treat it like any candidate."""
        contract = mission["contract"]
        mission_id = mission["id"]
        mode = get_mode(contract.get("mode"))
        ensure_pack(self.store, tenant_id, mode["pack"])
        phases = resolve_phases(mode, contract)
        by_id = {a["agent_id"]: a for a in self.store.list_agents(tenant_id)}
        fallback = next((a for a in by_id.values()
                         if a["agent_id"] == "agent.research.alpha"), None) \
            or next(iter(by_id.values()), None)
        sections: list[str] = []
        digest = ""
        # v21 (AgentScope broadcast pattern): the phases share ONE
        # mission channel — every role works from the same broadcast
        # of prior phase summaries, not isolated prompts.
        channel: list[dict] = []
        claims, assumptions, risks = [], [], []
        seen_claims: set[str] = set()
        last_out = None
        for idx, phase in enumerate(phases, start=1):
            self._check_cancelled(tenant_id, mission_id)
            fresh = self.store.get_mission(tenant_id, mission_id)
            if fresh["spent"] >= fresh["budget_limit"]:
                break
            agent_id = f"pack.{mode['pack']}.{phase['role']}"
            manifest = by_id.get(agent_id) or fallback
            if manifest is None:
                break
            role_title = phase["role"].replace("-", " ").title()
            objective = (
                f"{contract['objective']}\n\n"
                f"--- Mode phase {idx}/{len(phases)}: {phase['title']} "
                f"({mode['name']} mode). You are the {role_title}. "
                f"{phase['instruction']}")
            if channel:
                broadcast = "\n".join(
                    f"- [{e['phase']} · {e['role']} · {e['agent']}] {e['summary']}"
                    for e in channel)
                objective += ("\n\n--- Shared mission channel (broadcast — "
                              "every role in this mode sees this):\n" + broadcast)
            if digest:
                objective += ("\n\n--- Artifacts from earlier phases "
                              "(build on these; do not restart):\n" + digest)
            request = AgentRequest(
                mission_id=mission_id, task_id=research_task.task_id,
                objective=objective, deliverables=contract["deliverables"],
                output_schema={"version": "mode-phase-v1"},
                sources=corpus, task_type=phase["task_type"],
                context=context)
            response, used_adapter_version = await self._invoke_research(
                tenant_id, fresh, request, manifest, ctx=ctx)
            out = response.output
            last_out = out
            body = (out.get("deliverable") or out.get("summary") or "").strip()
            self.store.put_artifact(
                tenant_id, mission_id, "mode_phase",
                {"mode": mode["id"], "phase": phase["key"],
                 "title": phase["title"], "role": phase["role"],
                 "agent": out["agent_version_id"], "summary": out["summary"],
                 "deliverable": out.get("deliverable")},
                {"produced_by": out["agent_version_id"],
                 "task_id": research_task.task_id})
            self.store.create_run(tenant_id, mission_id, research_task.task_id,
                                  out["agent_version_id"], used_adapter_version,
                                  response.usage)
            self._record_inference_usage(
                tenant_id, mission_id, response.usage,
                f"mode phase {phase['key']} by {out['agent_version_id']}")
            self._event(tenant_id, mission_id, "ModePhaseCompleted", {
                "mode": mode["id"], "phase": phase["key"],
                "role": phase["role"], "agent": out["agent_version_id"]}, ctx=ctx)
            sections.append(f"## {idx}. {phase['title']} — {role_title}\n\n{body}")
            channel.append({"phase": phase["key"], "role": phase["role"],
                            "agent": out["agent_version_id"],
                            "summary": out["summary"]})
            digest = "\n\n".join(sections)[-6000:]
            for cl in out.get("claims", []):
                text = cl.text if hasattr(cl, "text") else str(cl)
                if text not in seen_claims:
                    seen_claims.add(text)
                    claims.append(cl)
            assumptions.extend(out.get("assumptions", []))
            risks.extend(out.get("risks", []))
        if channel:
            self.store.put_artifact(
                tenant_id, mission_id, "mode_channel",
                {"mode": mode["id"], "entries": channel},
                {"produced_by": "orchestrator", "task_id": research_task.task_id})
            self._event(tenant_id, mission_id, "ModeChannelPublished",
                        {"mode": mode["id"], "entries": len(channel)}, ctx=ctx)
        if last_out is None or not sections:
            return None
        deliverable = (f"# {mode['deliverable_title']}\n\n"
                       f"Objective: {contract['objective']}\n\n"
                       + "\n\n".join(sections))
        cand = Candidate(
            mission_id=mission_id,
            title=f"{mode['name']} — {contract['objective'][:64]}",
            strategy=mode["id"], summary=last_out["summary"],
            agent_version_id=last_out["agent_version_id"],
            claims=claims[:12], assumptions=assumptions[:8],
            risks=risks[:8],
            estimated_cost_usd=last_out["estimated_cost_usd"],
            time_to_learn=last_out["time_to_learn"],
            deliverable=deliverable)
        self.store.put_candidate(tenant_id, cand.model_dump())
        art = self.store.put_artifact(
            tenant_id, mission_id, "candidate", cand.model_dump(),
            {"produced_by": cand.agent_version_id, "mode": mode["id"],
             "task_id": research_task.task_id})
        self._event(tenant_id, mission_id, "CandidateProposed", {
            "candidate_id": cand.candidate_id, "strategy": cand.strategy,
            "agent": cand.agent_version_id, "artifact_id": art["artifact_id"],
            "mode": mode["id"], "phases": len(sections)}, ctx=ctx)
        return cand

    def _record_inference_usage(self, tenant_id: str, mission_id: str, usage: dict,
                                description: str, category: str = "inference") -> None:
        # W1 cost engine: inference/chat/agent calls go to the detailed
        # ledger ONLY (per mission/agent/provider/model, with cached
        # tokens and success status). They must NOT also go through
        # add_usage — one call, one bucket, no double-counting. Unknown
        # price: record the tokens, charge nothing, and SAY SO — never
        # present an unknown cost as $0 (§105.8/§114).
        cost = usage.get("cost_usd")
        status = usage.get("cost_status") or ("known" if cost is not None else "unknown")
        record = {
            "tenant_id": tenant_id, "mission_id": mission_id,
            "agent": usage.get("agent", ""), "provider": usage.get("provider", ""),
            "model": usage.get("model", ""), "phase": usage.get("phase", ""),
            "category": category,
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cached_tokens": usage.get("cached_tokens", 0) or 0,
            "latency_ms": usage.get("latency_ms"),
            "success": usage.get("success", True),
            "cost_usd": cost, "cost_status": status,
            "attempt": usage.get("attempt", 1),
            "retry_of": usage.get("retry_of"),
            "partial": usage.get("partial", False),
            "fallback": usage.get("fallback", False),
            "provider_request_id": usage.get("provider_request_id"),
        }
        if hasattr(self.store, "record_cost"):
            try:
                self.store.record_cost(record)
                return
            except Exception:
                pass  # fall through to the legacy path rather than losing the record
        amount = cost if cost is not None else 0.0
        suffix = "" if cost is not None else (
            f" — cost UNKNOWN (tokens in={usage.get('input_tokens')} "
            f"out={usage.get('output_tokens')}, model={usage.get('model')})")
        self.store.add_usage(tenant_id, mission_id, category, amount, description + suffix)

    # ------------------------------------------------------------- revision
    async def revise_candidate(self, tenant_id: str, mission_id: str, candidate_id: str,
                               instructions: str) -> dict[str, Any]:
        mission = self.store.get_mission(tenant_id, mission_id)
        if mission is None:
            raise KeyError("mission not found")
        originals = {c["candidate_id"]: c for c in self.store.list_candidates(tenant_id, mission_id)}
        original = originals.get(candidate_id)
        if original is None:
            raise KeyError("candidate not found")
        prior_revisions = [c for c in originals.values()
                           if c.get("revises") == candidate_id]
        chain = 0
        node = original
        while node.get("revises"):
            chain += 1
            node = originals[node["revises"]]
        if len(prior_revisions) >= MAX_REVISIONS or chain >= MAX_REVISIONS:
            raise ValueError(f"revision limit reached for this candidate (max {MAX_REVISIONS})")
        ctx = self._prepare_runtime(tenant_id, mission)
        self._event(tenant_id, mission_id, "RevisionRequested",
                    {"candidate_id": candidate_id, "instructions": instructions[:500]},
                    ctx=ctx)
        critiques = [c for c in self.store.list_critiques(tenant_id, mission_id)
                     if c.get("target_candidate_id") == candidate_id]
        new_data = await self._produce_revision(tenant_id, mission, original, critiques,
                                                instructions, ctx)
        cand = Candidate(**new_data)
        self.store.put_candidate(tenant_id, cand.model_dump())
        art = self.store.put_artifact(
            tenant_id, mission_id, "candidate", cand.model_dump(),
            {"produced_by": cand.agent_version_id, "revises": candidate_id})
        sources = {s["source_id"]: _src_obj(s) for s in self.store.list_sources(tenant_id)}
        ev = evaluate_candidate(cand, critiques, sources, elapsed_ms=0)
        self.store.put_evaluation(tenant_id, mission_id, ev.model_dump())
        self.store.put_candidate(tenant_id, cand.model_dump())
        self._event(tenant_id, mission_id, "CandidateRevised",
                    {"candidate_id": cand.candidate_id, "version": cand.version,
                     "revises": candidate_id, "artifact_id": art["artifact_id"]},
                    ctx=ctx)
        return self.mission_detail(tenant_id, mission_id)

    async def _produce_revision(self, tenant_id: str, mission: dict, original: dict,
                                critiques: list[dict], instructions: str,
                                ctx: RunContext | None = None) -> dict:
        """LLM revision (v20: the only kind — a revision is generated
        work, so it needs the connected model exactly like a first
        draft does). _prepare_runtime has already enforced the gate;
        a failed call propagates and the revision job fails honestly.
        A revision is a NEW candidate version — provenance preserved."""
        base = dict(original)
        request = AgentRequest(
            mission_id=mission["id"], task_id="revision",
            objective=mission["contract"]["objective"],
            deliverables=mission["contract"]["deliverables"],
            output_schema={"version": "revision"},
            task_type=mission["contract"].get("task_type") or "strategy")
        manifest = {"agent_id": "agent.research.alpha", "version": "1.3.0",
                    "agent_version_id": original["agent_version_id"],
                    "strategy": original["strategy"]}
        response, _ = await self._invoke_research(tenant_id, mission, request, manifest,
                                                  ctx=ctx)
        out = response.output
        base.update({"title": out["title"], "summary": out["summary"],
                     "claims": out["claims"], "assumptions": out["assumptions"],
                     "risks": out["risks"]})
        if out.get("deliverable"):
            base["deliverable"] = out["deliverable"]
        self.store.create_run(tenant_id, mission["id"], "revision",
                              original["agent_version_id"], LLM_ADAPTER_VERSION,
                              response.usage)
        self._record_inference_usage(tenant_id, mission["id"], response.usage,
                                     "candidate revision")
        return _revision_candidate(base, original)

    # ------------------------------------------------------------- detail
    def mission_detail(self, tenant_id: str, mission_id: str) -> dict[str, Any] | None:
        mission = self.store.get_mission(tenant_id, mission_id)
        if mission is None:
            return None
        events = self.store.list_events(tenant_id, mission_id)
        return {
            "mission": mission,
            "tasks": self.store.list_tasks(tenant_id, mission_id),
            "candidates": self.store.list_candidates(tenant_id, mission_id),
            "evaluations": self.store.list_evaluations(tenant_id, mission_id),
            "critiques": self.store.list_critiques(tenant_id, mission_id),
            "artifacts": self.store.list_artifacts(tenant_id, mission_id),
            "tool_invocations": self.store.list_invocations(tenant_id, mission_id),
            "approvals": self.store.list_approvals(tenant_id, mission_id),
            "usage": self.store.usage_summary(tenant_id, mission_id),
            "events": events,
            # W3: the final deliverable per kind, read from the same
            # event stream the record keeps — so the UI and the record
            # cannot disagree about which artifact is final.
            "final_artifacts": final_artifacts_from_events(events),
            "route_decisions": self.store.list_route_decisions(tenant_id, mission_id),
        }


def _revision_candidate(base: dict, original: dict) -> dict:
    from .domain import new_id
    base = dict(base)
    base.pop("candidate_id", None)
    base["version"] = int(original.get("version", 1)) + 1
    base["revises"] = original["candidate_id"]
    base["status"] = "proposed"
    validated = Candidate(candidate_id=new_id("cand"), **{k: v for k, v in base.items()
                                                         if k != "candidate_id"})
    return validated.model_dump()


def _src_obj(data: dict):
    from .domain import SourceRecord
    return SourceRecord(**data)
