"""OmniMind AI — Phase 6 golden-set evaluation harness.

Runs the frozen golden set through the real application (HTTP layer,
orchestrator, gateway, evaluator) and scores each run against the
case's authored expectations. Also hosts the router quality matrix.
Everything here uses the product's own machinery — the harness
measures, it never re-implements scoring.

Suites:
  golden   — 25 frozen missions (benchmarks/golden_set.json)
  router   — all 6 routing modes x budget/classification scenarios

(v20 removed the deterministic critic and with it the critic
calibration suite: there is no built-in critic left to calibrate.
The rubric-labelled fixtures remain in critic_calibration.json for
calibrating a live model's critic against, as future work.)
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def load_json(name: str):
    return json.loads((HERE / name).read_text())


# ------------------------------------------------------------------ metrics
def case_metrics(detail: dict, library_ids: set[str]) -> dict:
    """Quality metrics for one completed mission detail payload."""
    candidates = detail["candidates"]
    claims = [c for cand in candidates for c in cand["claims"]]
    cited = [c for c in claims if c["source_ids"]]
    invalid = sum(1 for c in claims for sid in c["source_ids"] if sid not in library_ids)
    dims = []
    for ev in detail["evaluations"]:
        dims.extend(d["score"] for d in ev["dimensions"].values())
    events = [e["event_type"] for e in detail["events"]]
    cited_ids = {sid for c in claims for sid in c["source_ids"]}
    return {
        "status": detail["mission"]["status"],
        "candidates": len(candidates),
        "strategies": sorted({c["strategy"] for c in candidates}),
        # Drafting voices: (strategy, author) pairs. Since v13/v20.1 a
        # mission is drafted by a suitability panel whose roster members
        # share the "persona" strategy label while writing from distinct
        # briefs — distinctness is a property of the voice, not the label.
        "voices": sorted({(c["strategy"], c["agent_version_id"])
                          for c in candidates}),
        "claims": len(claims),
        "citation_coverage": (len(cited) / len(claims)) if claims else 0.0,
        "invalid_citations": invalid,
        "mean_dimension": (sum(dims) / len(dims)) if dims else 0.0,
        "spend_usd": detail["mission"]["spent"],
        "research_event": next((e for e in events if e.startswith("Research")), None),
        "live_sources_cited": sum(1 for sid in cited_ids if sid.startswith("src_web_")),
        "critiques": len(detail["critiques"]),
    }


def check_case(case: dict, m: dict, live_mode: bool) -> list[str]:
    """Returns a list of gate failures (empty = pass)."""
    exp = case["expect"]
    fails = []
    if m["status"] != "completed":
        fails.append(f"status={m['status']}")
    if m["candidates"] < exp["min_candidates"]:
        fails.append(f"candidates={m['candidates']} < {exp['min_candidates']}")
    if exp["distinct_strategies"] and len(m["voices"]) < m["candidates"]:
        fails.append(f"drafting voices not distinct: {m['voices']}")
    if m["citation_coverage"] < exp["min_citation_coverage"]:
        fails.append(f"citation_coverage={m['citation_coverage']:.2f} < {exp['min_citation_coverage']}")
    if m["invalid_citations"] > exp["max_invalid_citations"]:
        fails.append(f"invalid_citations={m['invalid_citations']}")
    if m["mean_dimension"] < exp["min_mean_dimension"]:
        fails.append(f"mean_dimension={m['mean_dimension']:.2f} < {exp['min_mean_dimension']}")
    if m["spend_usd"] > exp["max_spend_usd"]:
        fails.append(f"spend={m['spend_usd']:.2f} > budget {exp['max_spend_usd']}")
    if live_mode:
        if exp["research_live"]:
            if m["research_event"] != "ResearchCompleted":
                fails.append(f"expected live research, got {m['research_event']}")
            if m["live_sources_cited"] < 1:
                fails.append("no live sources cited")
        else:
            if m["research_event"] != "ResearchSkipped":
                fails.append(f"expected ResearchSkipped, got {m['research_event']}")
            if m["live_sources_cited"]:
                fails.append("confidential/restricted case cited live sources")
    return fails


# ------------------------------------------------------------------ kappa
def cohens_kappa(pairs: list[tuple[str, str]], categories: list[str]) -> float:
    """Cohen's kappa for (observed, expected) label pairs."""
    n = len(pairs)
    if n == 0:
        return 0.0
    agree = sum(1 for o, e in pairs if o == e) / n
    expected = 0.0
    for cat in categories:
        p_obs = sum(1 for o, _ in pairs if o == cat) / n
        p_exp = sum(1 for _, e in pairs if e == cat) / n
        expected += p_obs * p_exp
    if expected >= 1.0:
        return 1.0
    return (agree - expected) / (1 - expected)


# ------------------------------------------------------------------ router
def run_router_matrix() -> dict:
    """Every routing mode x scenario, scored with the real router.

    The fixture catalog is adversarial on purpose: a free local model
    with middling observed quality, a cheap cloud model, a fast cloud
    model, and an expensive flagship. Confidential scenarios may only
    ever land on the local endpoint; tight budgets must exclude the
    flagship; the pinned mode must honour or refuse the pin.
    """
    from app.router import NoEligibleModel, RouteRequest, route

    conns = {
        "c1": {"id": "c1", "state": "active", "kind": "direct_provider",
               "allowed_models": [], "blocked_models": [], "data_policy": {},
               "tenant_id": "bench"},
        "c2": {"id": "c2", "state": "active", "kind": "local_endpoint",
               "allowed_models": [], "blocked_models": [], "data_policy": {},
               "tenant_id": "bench"},
    }

    def entry(cid, mid, pin, pout):
        return {"connection_id": cid, "model_id": mid, "provider": "bench",
                "display_name": mid, "capabilities": {"chat": "advertised"},
                "price_in": pin, "price_out": pout, "price_status": "known",
                "health": "ok", "last_seen": "2026-10-10T00:00:00+00:00",
                "deprecated": False}

    entries = [entry("c1", "cloud/cheap", 0.2, 0.6),
               entry("c1", "cloud/flagship", 5.0, 15.0),
               entry("c1", "cloud/fast", 1.0, 3.0),
               entry("c2", "local/private", 0.0, 0.0)]
    observed = {"cloud/cheap": {"quality": 0.62, "latency_rank": 0.5},
                "cloud/flagship": {"quality": 0.93, "latency_rank": 0.5},
                "cloud/fast": {"quality": 0.78, "latency_rank": 0.95},
                "local/private": {"quality": 0.55, "latency_rank": 0.4}}
    scenarios = [
        {"name": "open-budget", "data_classification": "internal",
         "budget_remaining_usd": 50.0},
        {"name": "tight-budget", "data_classification": "internal",
         "budget_remaining_usd": 0.004},
        {"name": "confidential", "data_classification": "confidential",
         "budget_remaining_usd": 50.0},
    ]
    modes = ["lowest_cost_acceptable", "balanced", "quality_within_budget",
             "fastest_eligible", "pinned_model", "task_aware_auto"]
    rows = []
    for sc in scenarios:
        for mode in modes:
            req = RouteRequest(
                tenant_id="bench", mode=mode, role="critic",
                pinned_model_id="cloud/flagship" if mode == "pinned_model" else None,
                data_classification=sc["data_classification"],
                budget_remaining_usd=sc["budget_remaining_usd"])
            try:
                d = route(entries, conns, observed, req)
                rows.append({"scenario": sc["name"], "mode": mode,
                             "outcome": "routed", "model": d["model_id"],
                             "est_cost_usd": round(d["estimated_cost"] or 0, 5),
                             "quality": observed[d["model_id"]]["quality"]})
            except NoEligibleModel:
                rows.append({"scenario": sc["name"], "mode": mode,
                             "outcome": "no_eligible_model", "model": None,
                             "est_cost_usd": None, "quality": None})
    return {"rows": rows}


def router_invariants(matrix: dict) -> list[str]:
    """Hard expectations any correct router must satisfy on the matrix."""
    fails = []
    for row in matrix["rows"]:
        if row["scenario"] == "confidential":
            if row["mode"] == "pinned_model":
                if row["outcome"] != "no_eligible_model":
                    fails.append("confidential+pinned(cloud) must refuse, "
                                 f"got {row['model']}")
            elif row["model"] != "local/private":
                fails.append(f"confidential/{row['mode']} routed to {row['model']}")
        if row["scenario"] == "tight-budget" and row["model"] == "cloud/flagship":
            fails.append(f"tight-budget/{row['mode']} picked the flagship")
        if row["scenario"] == "open-budget" and row["mode"] == "pinned_model" \
                and row["model"] != "cloud/flagship":
            fails.append(f"pinned mode ignored the pin: {row['model']}")
        if row["scenario"] == "open-budget" \
                and row["mode"] == "lowest_cost_acceptable" \
                and row["model"] != "local/private":
            fails.append(f"lowest_cost picked {row['model']}, expected the $0 local model")
    return fails
