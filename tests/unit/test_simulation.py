"""Phase 7 engine tests: determinism, bounds, isolation of inputs."""
from __future__ import annotations

import copy

from app.simulation import (DEFAULT_BASELINE, MAX_TICKS, run_branch,
                            sensitivity)


def test_deterministic_same_seed_same_hash():
    a = run_branch(DEFAULT_BASELINE, "niche-first", 42)
    b = run_branch(DEFAULT_BASELINE, "niche-first", 42)
    assert a["outcome_hash"] == b["outcome_hash"]
    assert a["series"] == b["series"]


def test_different_seed_different_outcome():
    a = run_branch(DEFAULT_BASELINE, "niche-first", 42)
    b = run_branch(DEFAULT_BASELINE, "niche-first", 7)
    assert a["outcome_hash"] != b["outcome_hash"]


def test_inputs_never_mutated():
    baseline = copy.deepcopy(DEFAULT_BASELINE)
    snapshot = copy.deepcopy(baseline)
    run_branch(baseline, "broad", 1, overrides={"budget_usd": 1000})
    sensitivity(baseline, "broad", 1)
    assert baseline == snapshot


def test_bounds_enforced():
    baseline = {**DEFAULT_BASELINE, "ticks": 500, "market_size": 10**9}
    r = run_branch(baseline, "broad", 3)
    assert len(r["series"]) <= MAX_TICKS


def test_budget_never_negative_and_exhaustion_flagged():
    baseline = {**DEFAULT_BASELINE, "budget_usd": 500}
    r = run_branch(baseline, "broad", 3)
    assert all(s["cash_usd"] >= 0 for s in r["series"])
    assert r["final"]["budget_exhausted_tick"] is not None


def test_every_output_labelled_estimate():
    r = run_branch(DEFAULT_BASELINE, "partnership-led", 5)
    assert r["final"]["label"] == "SIMULATED ESTIMATE"
