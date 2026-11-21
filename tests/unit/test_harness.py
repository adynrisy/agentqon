"""Phase 6: harness self-tests — kappa math, router invariants, and
a golden-suite smoke run (seed mode, 3 cases).

v20: the critic-calibration gate test was retired with the
deterministic critic it measured (removed from the product); it is
replaced below by a guard that the canned adapters stay gone."""
from __future__ import annotations

from benchmarks.harness import (cohens_kappa, load_json, router_invariants,
                                run_router_matrix)


def test_kappa_perfect_and_chance():
    cats = ["a", "b"]
    assert cohens_kappa([("a", "a"), ("b", "b")], cats) == 1.0
    # systematic disagreement scores below zero-ish territory
    k = cohens_kappa([("a", "b"), ("a", "b"), ("b", "a"), ("b", "a")], cats)
    assert k < 0


def test_canned_adapters_stay_removed():
    """v20 replacement for the retired calibration gate: the product
    must not grow its deterministic adapters back — critiques are a
    connected model's work now, not a built-in rule engine's."""
    import app.adapters as adapters
    assert not hasattr(adapters, "MockResearchAdapter")
    assert not hasattr(adapters, "CriticAdapter")
    assert not hasattr(adapters, "OpenAICompatibleAdapter")
    # The calibration fixtures survive for future live-model calibration.
    data = load_json("critic_calibration.json")
    assert len(data["cases"]) == 12


def test_router_matrix_invariants_hold():
    matrix = run_router_matrix()
    assert len(matrix["rows"]) == 18  # 6 modes x 3 scenarios
    assert router_invariants(matrix) == []


def test_golden_set_shape():
    data = load_json("golden_set.json")
    assert len(data["cases"]) == 25
    ids = [c["id"] for c in data["cases"]]
    assert len(set(ids)) == 25
    skipped = [c for c in data["cases"] if not c["expect"]["research_live"]]
    assert {c["contract"]["data_classification"] for c in skipped} <= {
        "confidential", "restricted"}
