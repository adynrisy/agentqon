"""Cost-aware routing engine (Master Build V3 §105.4, §112, §114).

Pure and deterministic for fixed inputs: same catalog + same request →
same decision, so route behavior is unit-testable and auditable. Hard
constraints are applied as filters first (and counted, so a denial is
explainable); only survivors are scored per routing mode.

Invariants:
  - a blocked/disallowed/unowned/inactive-connection model can never win,
    including via fallback paths (callers route every attempt through here);
  - an unknown price is never treated as zero: with a finite budget it
    must fit inside the configured unknown-price reserve, else filtered;
  - confidential/restricted missions may only use connections whose data
    policy allows it (local endpoints qualify by default);
  - when nothing survives, raise NoEligibleModel with the filter counts.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from .llm_adapter import _is_local_endpoint

ROUTING_MODES = ("lowest_cost_acceptable", "balanced", "quality_within_budget",
                 "fastest_eligible", "pinned_model", "task_aware_auto")

# Quality prior per model when no observed outcomes exist: deliberately
# neutral; observed success (from the usage ledger) dominates when present.
NEUTRAL_QUALITY = 0.5


class NoEligibleModel(Exception):
    def __init__(self, filtered_counts: dict, reasons: list[str]):
        super().__init__("no eligible model: " + ", ".join(
            f"{k}={v}" for k, v in filtered_counts.items() if v))
        self.code = "NO_ELIGIBLE_MODEL"
        self.filtered_counts = filtered_counts
        self.reasons = reasons


@dataclass
class RouteRequest:
    tenant_id: str
    mode: str = "balanced"
    mission_id: Optional[str] = None
    role: Optional[str] = None
    pinned_model_id: Optional[str] = None
    data_classification: str = "internal"
    budget_remaining_usd: Optional[float] = None
    expected_input_tokens: int = 1500
    expected_output_tokens: int = 700
    unknown_price_reserve_usd: float = 0.50
    # W6 M3: the task class this route serves (extraction, debugging,
    # classification, ...). When `profiles` are supplied to route(),
    # scoring blends the task-specific profile — never a universal
    # ranking. When absent, routing is exactly as before.
    task_class: Optional[str] = None


@dataclass
class Candidate:
    entry: dict          # catalog row
    connection: dict     # connection row
    est_cost: Optional[float]
    quality: float
    latency_rank: float  # 0..1, higher = faster prior (neutral until observed)


def _est_cost(entry: dict, req: RouteRequest) -> Optional[float]:
    if entry.get("price_status") != "known" or entry.get("price_in") is None:
        return None
    pin = entry["price_in"] or 0.0
    pout = entry.get("price_out") or 0.0
    return (req.expected_input_tokens * pin + req.expected_output_tokens * pout) / 1_000_000


def _is_local_conn(conn: dict) -> bool:
    """Locality is a property of the connection's URL, not its kind
    (v21.3): a local_endpoint connection qualifies, and so does any
    other kind whose stored base_url points at a local address."""
    if conn.get("kind") == "local_endpoint":
        return True
    return _is_local_endpoint(conn.get("base_url") or "")


def _confidential_ok(conn: dict) -> bool:
    if _is_local_conn(conn):
        return True
    policy = conn.get("data_policy") or {}
    return policy.get("allow_confidential") is True


def route(entries: list[dict], connections: dict[str, dict],
          observed: dict[str, dict], req: RouteRequest,
          profiles: Optional[dict] = None) -> dict:
    """entries: catalog rows for the tenant. connections: id -> conn row.
    observed: model_id -> {success_rate, avg_eval, calls} from the ledger.
    profiles (optional): model_id -> {task_class: score} from
    model_registry.profile_scores_for_models — when supplied AND
    req.task_class names a class, each candidate's quality is blended
    50/50 with its task-specific profile score (a profiled model is
    judged for THIS task, not in general). Without profiles, or
    without a task class, scoring is exactly as before.
    Returns a RouteDecision dict (see spec §112)."""
    if req.mode not in ROUTING_MODES:
        raise ValueError(f"unknown routing mode: {req.mode}")
    filtered = {"total": 0, "connection_inactive": 0, "deprecated": 0, "blocked": 0,
                "not_allowlisted": 0, "classification": 0, "pinned_mismatch": 0,
                "budget": 0, "budget_unknown_price": 0}
    survivors: list[Candidate] = []
    for entry in entries:
        filtered["total"] += 1
        conn = connections.get(entry["connection_id"])
        if not conn or conn["state"] != "active":
            filtered["connection_inactive"] += 1
            continue
        if entry.get("deprecated"):
            filtered["deprecated"] += 1
            continue
        mid = entry["model_id"]
        if mid in (conn.get("blocked_models") or []):
            filtered["blocked"] += 1
            continue
        allowed = conn.get("allowed_models") or []
        if allowed and mid not in allowed:
            filtered["not_allowlisted"] += 1
            continue
        if req.data_classification in ("confidential", "restricted") and not _confidential_ok(conn):
            filtered["classification"] += 1
            continue
        if req.mode == "pinned_model" and req.pinned_model_id and mid != req.pinned_model_id:
            filtered["pinned_mismatch"] += 1
            continue
        est = _est_cost(entry, req)
        if req.budget_remaining_usd is not None:
            if est is not None and est > req.budget_remaining_usd:
                filtered["budget"] += 1
                continue
            if est is None and req.unknown_price_reserve_usd > req.budget_remaining_usd:
                filtered["budget_unknown_price"] += 1
                continue
        obs = observed.get(mid, {})
        quality = obs.get("quality", NEUTRAL_QUALITY)
        profile_score = None
        if profiles is not None and req.task_class:
            prof = (profiles.get(mid) or {}).get(req.task_class)
            if prof is not None:
                profile_score = float(prof)
                quality = 0.5 * quality + 0.5 * profile_score
        survivors.append(Candidate(entry=entry, connection=conn, est_cost=est,
                                   quality=quality, latency_rank=obs.get("latency_rank", 0.5)))
    if not survivors:
        raise NoEligibleModel(filtered, ["all candidates were removed by hard filters"])

    mode = req.mode
    effective = mode
    if mode == "task_aware_auto":
        # Role-aware mapping (§115): research favours cost, critique favours quality.
        effective = "lowest_cost_acceptable" if req.role in ("researcher", "planner") \
            else "quality_within_budget" if req.role in ("critic", "verifier") else "balanced"

    def sort_key(c: Candidate):
        est = c.est_cost if c.est_cost is not None else float("inf")
        if effective == "lowest_cost_acceptable":
            return (est, -c.quality, c.entry["model_id"])
        if effective == "quality_within_budget":
            return (-c.quality, est, c.entry["model_id"])
        if effective == "fastest_eligible":
            return (-c.latency_rank, est, c.entry["model_id"])
        if effective == "pinned_model":
            return (c.entry["model_id"],)
        # balanced: quality per dollar with neutral handling of unknown price
        value = c.quality / (1.0 + (c.est_cost if c.est_cost is not None else 0.25))
        return (-value, est, c.entry["model_id"])

    survivors.sort(key=sort_key)
    best = survivors[0]
    reasons = [f"mode:{effective}", f"eligible:{len(survivors)}"]
    if profiles is not None and req.task_class:
        reasons.append(f"task_profile:{req.task_class}")
    if best.est_cost is None:
        reasons.append("price:unknown (reserved conservatively)")
        confidence = "unknown"
    else:
        reasons.append(f"estimated_cost:${best.est_cost:.4f}")
        confidence = "high" if best.entry.get("price_status") == "known" else "low"
    if _is_local_conn(best.connection):
        reasons.append("local endpoint: no provider egress")
    return {
        "id": "rtd_" + uuid.uuid4().hex[:12],
        "tenant_id": req.tenant_id, "mission_id": req.mission_id, "role": req.role,
        "connection_id": best.connection["id"], "model_id": best.entry["model_id"],
        "mode": mode, "reason_codes": reasons, "filtered_counts": filtered,
        "snapshot_id": best.entry.get("last_seen"),
        "estimated_cost": best.est_cost, "estimate_confidence": confidence,
    }
