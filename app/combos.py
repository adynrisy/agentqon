"""Model combos (2026-10-23, fix-list item 7 — mirroring
OmniRoute's flagship "Combos" feature in AgentQon's routing).

A combo is a named chain of model targets (model + connection
pairs). When a run is pinned to a combo — directly in the model
picker (`combo:<id>`, or `combo:auto`), or through a glob mapping
(pattern -> combo) — calls try the targets in the order the
combo's STRATEGY produces, and a call that fails for an
availability reason (provider down, timed out, auth failed, rate
limited / quota, model unsupported at that endpoint) moves to
the next target automatically. The failover is recorded as an
event wherever the caller provides a callback.

STRATEGY HONESTY (Zohaib's rule — never a label with no behavior):
the strategies below are REAL, each with the behavior its code
actually performs, computed from data AgentQon genuinely has:
the catalog (health, prices), the combo's own recorded traffic
(state: calls, failures, last success), and the request itself.
OmniRoute names four more strategy families whose semantics need
data AgentQon does not track — per-provider quota RESET CLOCKS
(reset-window, reset-aware), prompt-cache telemetry
(cache-optimized, context-optimized, context-relay), and
multi-model fan-out/sequential composition (fusion, pipeline —
those are mission-level shapes in AgentQon, not routing). They
are listed in DEFERRED_STRATEGIES with their reasons and are NOT
offered in the picker.

State: a combo record carries its runtime state (round-robin
counter, sticky target for fill-first, last-known-good target,
per-target call/failure counts) — updated by the failover client
through the persist callback the orchestrator supplies.
"""
from __future__ import annotations

import fnmatch
import random
import uuid
from typing import Callable, Optional

from .gateway_client import GatewayError

# ------------------------------------------------------------ strategies
# name -> plain-words description of the REAL behavior.
REAL_STRATEGIES = {
    "priority": "Try the targets in the order you listed them.",
    "fill-first": "Use the first target until it fails, then move "
                  "to the next and stay there.",
    "round-robin": "Take turns: each call starts one target later "
                   "than the last.",
    "weighted": "Pick each call's starting target by the weights "
                "you set (bigger weight, more calls).",
    "random": "Pick each call's starting target at random.",
    "strict-random": "Random like above, but no target starts "
                     "twice until every target has started once.",
    "least-used": "Start with the target this combo has used the "
                  "fewest times.",
    "cost-optimized": "Cheapest estimated cost first (models with "
                      "unknown prices go last).",
    "headroom": "Healthiest first: catalog-healthy targets before "
                "degraded ones, fewest recent failures first.",
    "p2c": "Power of two choices: draw two targets at random and "
           "start with the healthier/cheaper of the pair.",
    "lkgp": "Last known good: start with the target that last "
            "succeeded for this combo.",
    "auto": "Built live from every connected model, scored like "
            "the router scores (quality per dollar, health first).",
}

DEFERRED_STRATEGIES = {
    "reset-window": "Needs each provider's quota reset clock — "
                    "AgentQon does not know when quotas reset.",
    "reset-aware": "Needs each provider's quota reset clock — "
                   "AgentQon does not know when quotas reset.",
    "cache-optimized": "Needs prompt-cache hit telemetry per "
                       "provider — AgentQon does not track it.",
    "context-optimized": "Needs per-call context/cache accounting "
                         "AgentQon does not track.",
    "context-relay": "Needs conversation-context handoff state "
                     "between providers — not a routing input here.",
    "fusion": "Calling several models and merging answers is a "
              "mission shape in AgentQon (the panel), not a route.",
    "pipeline": "Chaining models so each one's output feeds the "
                "next is a mission workflow, not a route.",
}

# A failure may move to the next target when the REQUEST is fine
# and the TARGET is the problem. "bad_request" (the request itself
# is malformed) and "blocked" (our own endpoint policy refused)
# never fail over — switching targets must not launder either.
FAILOVER_CATEGORIES = {"provider_unavailable", "timeout",
                       "auth_failed", "rate_limited", "unsupported",
                       "unknown"}


def new_combo_id() -> str:
    return "combo_" + uuid.uuid4().hex[:12]


def target_key(target: dict) -> str:
    return f"{target['connection_id']}:{target['model_id']}"


def empty_state() -> dict:
    return {"rr": 0, "sticky": None, "last_good": None,
            "calls": {}, "failures": {}, "bag": []}


# ------------------------------------------------------------ validation
def validate_combo(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("a combo needs a name")
    strategy = str(payload.get("strategy") or "priority")
    if strategy not in REAL_STRATEGIES:
        raise ValueError(f"unknown combo strategy: {strategy}")
    raw_targets = payload.get("targets") or []
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("a combo needs at least one model target")
    targets = []
    for t in raw_targets:
        if not isinstance(t, dict) or not t.get("connection_id") \
                or not t.get("model_id"):
            raise ValueError("each target needs a connection and a model")
        weight = t.get("weight", 1)
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            raise ValueError("target weights must be numbers") from None
        if weight <= 0:
            raise ValueError("target weights must be above zero")
        targets.append({"connection_id": str(t["connection_id"]),
                        "model_id": str(t["model_id"]),
                        "weight": weight})
    return {"name": name, "strategy": strategy, "targets": targets,
            "enabled": bool(payload.get("enabled", True))}


def validate_mapping(payload: dict) -> dict:
    pattern = str(payload.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("a mapping needs a model pattern (glob, e.g. gpt-*)")
    if not payload.get("combo_id"):
        raise ValueError("a mapping needs a combo to point at")
    try:
        priority = int(payload.get("priority", 100))
    except (TypeError, ValueError):
        raise ValueError("mapping priority must be a whole number") from None
    return {"pattern": pattern, "combo_id": str(payload["combo_id"]),
            "priority": priority,
            "enabled": bool(payload.get("enabled", True))}


def match_mapping(mappings: list[dict], model_id: str) -> Optional[dict]:
    """Highest-priority (lowest number) enabled mapping whose glob
    matches the requested model. None when nothing matches."""
    hits = [m for m in mappings if m.get("enabled", True)
            and fnmatch.fnmatchcase(model_id, m["pattern"])]
    if not hits:
        return None
    return sorted(hits, key=lambda m: (m["priority"], m["pattern"]))[0]


# ------------------------------------------------------------ eligibility
def eligible_targets(targets: list[dict], entries: list[dict],
                     connections: dict[str, dict]) -> list[tuple[dict, dict]]:
    """Resolve target specs to (entry, conn) pairs, applying the
    same hard rules the router applies: the connection must be
    active, the model must be in its catalog, not deprecated, not
    blocked, and inside the connection's allowlist when it has one.
    Ineligible targets drop out silently here — the ORDER function
    only ever sees runnable targets."""
    by_key = {}
    for e in entries:
        by_key[(e["connection_id"], e["model_id"])] = e
    out = []
    for t in targets:
        conn = connections.get(t["connection_id"])
        if not conn or conn.get("state") != "active":
            continue
        entry = by_key.get((t["connection_id"], t["model_id"]))
        if entry is None or entry.get("deprecated"):
            continue
        if t["model_id"] in (conn.get("blocked_models") or []):
            continue
        allowed = conn.get("allowed_models") or []
        if allowed and t["model_id"] not in allowed:
            continue
        out.append((entry, conn))
    return out


def _est_cost(entry: dict) -> Optional[float]:
    if entry.get("price_status") != "known" or entry.get("price_in") is None:
        return None
    return (1500 * (entry["price_in"] or 0.0)
            + 700 * (entry.get("price_out") or 0.0)) / 1_000_000


def auto_targets(entries: list[dict],
                 connections: dict[str, dict]) -> list[tuple[dict, dict]]:
    """The AUTO combo: every eligible model on every active
    connection, ordered like the router's balanced mode — healthy
    first, then quality-per-dollar with the neutral quality prior
    (0.5, the router's own NEUTRAL_QUALITY when no outcomes are
    observed)."""
    specs = [{"connection_id": e["connection_id"],
              "model_id": e["model_id"], "weight": 1} for e in entries]
    pool = eligible_targets(specs, entries, connections)

    def score(pair):
        entry, _conn = pair
        est = _est_cost(entry)
        value = 0.5 / (1.0 + (est if est is not None else 0.25))
        healthy = 0 if entry.get("health") == "ok" else 1
        return (healthy, -value, entry["model_id"])
    return sorted(pool, key=score)


# ------------------------------------------------------------ ordering
def order_targets(strategy: str, pool: list[tuple[dict, dict]],
                  specs: list[dict], state: dict,
                  rng: random.Random) -> list[tuple[dict, dict]]:
    """Order an eligible pool by strategy. `specs` is the combo's
    target list (listed order + weights); `state` is the combo's
    runtime state (mutated for counters/bags by the caller)."""
    if len(pool) <= 1:
        return list(pool)
    listed = {target_key(t): i for i, t in enumerate(specs)}

    def key_of(pair):
        e, _c = pair
        return f"{e['connection_id']}:{e['model_id']}"

    def by_listed(pairs):
        return sorted(pairs, key=lambda p: listed.get(key_of(p), 999))

    if strategy == "priority":
        return by_listed(pool)
    if strategy == "fill-first":
        sticky = state.get("sticky")
        if sticky and any(key_of(p) == sticky for p in pool):
            rest = by_listed([p for p in pool if key_of(p) != sticky])
            head = [p for p in pool if key_of(p) == sticky]
            return head + rest
        return by_listed(pool)
    if strategy == "lkgp":
        good = state.get("last_good")
        if good and any(key_of(p) == good for p in pool):
            rest = by_listed([p for p in pool if key_of(p) != good])
            return [p for p in pool if key_of(p) == good] + rest
        return by_listed(pool)
    if strategy == "round-robin":
        ordered = by_listed(pool)
        shift = int(state.get("rr", 0)) % len(ordered)
        state["rr"] = int(state.get("rr", 0)) + 1
        return ordered[shift:] + ordered[:shift]
    if strategy == "least-used":
        calls = state.get("calls", {})
        return sorted(pool, key=lambda p: (calls.get(key_of(p), 0),
                                           listed.get(key_of(p), 999)))
    if strategy == "cost-optimized":
        return sorted(pool, key=lambda p: (
            _est_cost(p[0]) if _est_cost(p[0]) is not None else float("inf"),
            listed.get(key_of(p), 999)))
    if strategy == "headroom":
        failures = state.get("failures", {})
        return sorted(pool, key=lambda p: (
            0 if p[0].get("health") == "ok" else 1,
            failures.get(key_of(p), 0),
            listed.get(key_of(p), 999)))
    if strategy == "random":
        out = list(pool)
        rng.shuffle(out)
        return out
    if strategy == "weighted":
        remaining = list(pool)
        out = []
        weights = {target_key(t): t.get("weight", 1) for t in specs}
        while remaining:
            total = sum(weights.get(key_of(p), 1) for p in remaining)
            pick = rng.uniform(0, total)
            acc = 0.0
            for p in list(remaining):
                acc += weights.get(key_of(p), 1)
                if pick <= acc:
                    out.append(p)
                    remaining.remove(p)
                    break
            else:  # float dust: take the last
                out.append(remaining.pop())
        return out
    if strategy == "strict-random":
        bag = [k for k in state.get("bag", [])
               if any(key_of(p) == k for p in pool)]
        if not bag:
            bag = [key_of(p) for p in pool]
            rng.shuffle(bag)
        head_key = bag[0]
        state["bag"] = bag[1:]
        head = [p for p in pool if key_of(p) == head_key]
        rest = by_listed([p for p in pool if key_of(p) != head_key])
        return head + rest
    if strategy == "p2c":
        pair = rng.sample(pool, 2)
        failures = state.get("failures", {})

        def pair_score(p):
            est = _est_cost(p[0])
            return (0 if p[0].get("health") == "ok" else 1,
                    failures.get(key_of(p), 0),
                    est if est is not None else float("inf"))
        pair.sort(key=pair_score)
        rest = by_listed([p for p in pool if p not in pair])
        return pair + rest
    # "auto" pools arrive pre-ordered by auto_targets; anything
    # unrecognised falls back to the listed order rather than
    # failing the run.
    return by_listed(pool)


def resolve_chain(combo: dict, entries: list[dict],
                  connections: dict[str, dict],
                  rng: Optional[random.Random] = None
                  ) -> list[tuple[dict, dict]]:
    """The ordered, eligible (entry, conn) chain for one call.
    Mutates combo['state'] for the counter strategies; the caller
    persists it (store.put_combo) when the combo is a stored one."""
    rng = rng or random.Random()
    strategy = combo["strategy"]
    if strategy == "auto":
        return auto_targets(entries, connections)
    state = combo.setdefault("state", empty_state())
    pool = eligible_targets(combo["targets"], entries, connections)
    return order_targets(strategy, pool, combo["targets"], state, rng)


# ------------------------------------------------------------ failover client
class FailoverGateway:
    """A GatewayClient-shaped chain: tries each target's gateway
    with that target's model, in order, failing over on
    availability-class errors. Satisfies the _ChatClient seam
    (chat_json) and the society factory's chat_json shape.

    Callbacks (all optional):
    - on_failover({from, to, category}) — a target failed and the
      chain moved on; the orchestrator turns it into an event.
    - on_outcome(target_key, ok) — success/failure bookkeeping so
      the caller can persist combo state (last_good, sticky,
      calls, failures).
    """

    def __init__(self, chain: list, *, on_failover: Optional[Callable] = None,
                 on_outcome: Optional[Callable] = None):
        # chain: [(gateway, model_id, target_key)]
        self.chain = chain
        self.on_failover = on_failover
        self.on_outcome = on_outcome
        # _ChatClient reads .base_url for its trust_env decision.
        self.base_url = chain[0][0].base_url if chain else ""

    async def _call(self, method: str, *args, **kwargs):
        if not self.chain:
            raise GatewayError("provider_unavailable",
                               "combo has no eligible targets")
        last_exc: Optional[GatewayError] = None
        for i, (gateway, target_model, key) in enumerate(self.chain):
            try:
                result = await getattr(gateway, method)(
                    target_model, *args, **kwargs)
            except GatewayError as exc:
                if self.on_outcome:
                    self.on_outcome(key, False)
                if exc.category not in FAILOVER_CATEGORIES \
                        or i == len(self.chain) - 1:
                    raise
                last_exc = exc
                if self.on_failover:
                    self.on_failover({
                        "from": key, "to": self.chain[i + 1][2],
                        "category": exc.category,
                        "model": target_model})
                continue
            if self.on_outcome:
                self.on_outcome(key, True)
            return result
        raise last_exc or GatewayError("provider_unavailable",
                                       "combo chain exhausted")

    async def chat_json(self, model: str, system: str, user: str, **kwargs):
        return await self._call("chat_json", system, user, **kwargs)

    async def chat_text(self, model: str, messages: list, **kwargs):
        return await self._call("chat_text", messages, **kwargs)

    async def chat_stream(self, model: str, messages: list, **kwargs):
        """Streaming through the chain (W6 gap 5): targets are tried
        in order, but failover happens ONLY before the first chunk
        is yielded — once part of a reply has been delivered, moving
        to another model would mix two models' replies. A target
        whose stream errors before any chunk moves the chain on with
        on_failover, exactly as chat_text does."""
        if not self.chain:
            yield {"type": "error",
                   "message": "This combo has no models to stream from."}
            return
        for i, (gateway, target_model, key) in enumerate(self.chain):
            stream = getattr(gateway, "chat_stream", None)
            if stream is None:
                if self.on_outcome:
                    self.on_outcome(key, False)
                if i == len(self.chain) - 1:
                    yield {"type": "error",
                           "message": "This model cannot stream its reply."}
                    return
                if self.on_failover:
                    self.on_failover({"from": key, "to": self.chain[i + 1][2],
                                      "category": "unsupported",
                                      "model": target_model})
                continue
            saw_any = False
            failed_before_chunk = False
            try:
                async for event in stream(target_model, messages, **kwargs):
                    if event.get("type") == "error" and not saw_any:
                        failed_before_chunk = True
                        break
                    if event.get("type") in ("delta", "tool_call"):
                        saw_any = True
                    yield event
                    if event.get("type") == "done":
                        if self.on_outcome:
                            self.on_outcome(key, True)
                        return
            except Exception:
                if not saw_any:
                    failed_before_chunk = True
                else:
                    raise
            if failed_before_chunk:
                if self.on_outcome:
                    self.on_outcome(key, False)
                if i == len(self.chain) - 1:
                    yield {"type": "error",
                           "message": "No model in this combo could stream a reply."}
                    return
                if self.on_failover:
                    self.on_failover({"from": key, "to": self.chain[i + 1][2],
                                      "category": "provider_unavailable",
                                      "model": target_model})
                continue
            return
