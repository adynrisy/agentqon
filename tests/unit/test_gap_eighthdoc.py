"""W13 — eighth document: Reliability and Intelligence Layer (40 items).

One named test per item, per GAP_W13_CONTRACT.md. Pure library tests;
no network, no provider, no new dependencies.
"""
from app import reliability_layer as R


def test_01_dissonance_requires_reconciliation():
    out = R.detect_dissonance(
        [{"id": "f1", "statement": "Caching improves response speed", "verified": True}],
        {"statement": "Caching does not improve response speed"})
    assert out["conflict"] is True and out["action"] == "reconcile"
    assert out["conflicting_findings"] == ["f1"]
    # Unverified earlier findings never trigger reconciliation.
    out2 = R.detect_dissonance(
        [{"id": "f1", "statement": "Caching improves response speed", "verified": False}],
        {"statement": "Caching does not improve response speed"})
    assert out2["conflict"] is False


def test_02_drift_flags_detour_and_expansion():
    out = R.measure_drift("build a fast search page", [
        {"id": "s1", "description": "build a fast search page layout", "approved": True},
        {"id": "s2", "description": "redesign the company logo colours", "approved": False}])
    assert out["detours"] == ["s2"] and out["expansions"] == ["s2"]
    assert out["steps"][0]["detour"] is False


def test_03_boundary_names_smallest_observation():
    out = R.knowledge_boundary("What is the price of widget X in 2026?",
                               context_text="Widget X is a small tool.")
    assert out["answerable"] is False
    assert out["smallest_observation"] in out["missing_terms"]
    assert "price" in out["missing_terms"]


def test_04_counterfactual_ranks_survivable_plan():
    plans = [
        {"id": "p1", "steps": [{"id": "a", "needs_tools": ["web"]}, {"id": "b", "needs_tools": []}]},
        {"id": "p2", "steps": [{"id": "a", "needs_tools": ["web"]}, {"id": "b", "needs_tools": ["web"]}]},
    ]
    out = R.counterfactual_analyze(plans, failures=["web"])
    assert out["best_plan"] == "p1"
    assert out["ranking"][0]["survivability"] == 0.5


def test_05_stability_separates_wording_from_sensitivity():
    wording = R.reasoning_stability([
        {"variant_id": "v1", "wording": "fast", "conclusion": "yes", "material_change": False},
        {"variant_id": "v2", "wording": "quick", "conclusion": "no", "material_change": False}])
    assert wording["classification"] == "wording_instability"
    genuine = R.reasoning_stability([
        {"variant_id": "v1", "wording": "fast", "conclusion": "yes", "material_change": False},
        {"variant_id": "v2", "wording": "fast", "conclusion": "no", "material_change": True}])
    assert genuine["classification"] == "genuine_sensitivity"


def test_06_hidden_requirements_from_spec():
    out = R.discover_hidden_requirements("Store orders in a database and send an email receipt.")
    cats = {r["category"] for r in out["hidden_requirements"]}
    assert "prerequisite" in cats and "operational" in cats


def test_07_weighted_consensus_beats_vote_count():
    votes = [
        {"agent": "a1", "conclusion": "yes", "evidence_quality": 0.2,
         "source_independent": False, "tests_passed": False, "uncertainty": 0.8},
        {"agent": "a2", "conclusion": "yes", "evidence_quality": 0.2,
         "source_independent": False, "tests_passed": False, "uncertainty": 0.8},
        {"agent": "a3", "conclusion": "no", "evidence_quality": 0.95,
         "source_independent": True, "tests_passed": True, "uncertainty": 0.1},
    ]
    out = R.evidence_weighted_consensus(votes)
    assert out["by_vote_count"]["yes"] == 2  # majority by count...
    assert out["winner"] == "no"  # ...but weighting wins


def test_08_expiration_on_supporting_change():
    out = R.expire_conclusions(
        [{"id": "c1", "supports": ["lib-v2", "data-9"]}, {"id": "c2", "supports": ["lib-v1"]}],
        changes=["lib-v2"])
    assert out["stale"] == ["c1"]


def test_09_capability_negotiation_matches():
    out = R.negotiate_capabilities(
        [{"id": "ag1", "capabilities": ["research.web"], "context_limit_tokens": 8000,
          "output_formats": ["json"]},
         {"id": "ag2", "capabilities": [], "context_limit_tokens": 100,
          "output_formats": ["text"]}],
        {"required_capabilities": ["research.web"], "needed_context_tokens": 4000,
         "output_format": "json"})
    assert out["eligible"] == ["ag1"]


def test_10_semantic_agreement_flags_term():
    out = R.semantic_agreement([
        {"model": "m1", "terms": {"latency": "time for a reply to arrive"}},
        {"model": "m2", "terms": {"latency": "number of database rows stored"}}])
    assert out["disagreements"] == ["latency"]


def test_11_independent_evidence_required():
    out = R.independent_evidence([
        {"reviewer": "r1", "evidence_source": "src-b", "agrees_with_first": True},
        {"reviewer": "r2", "evidence_source": "src-a", "agrees_with_first": True}],
        first_agent_source="src-a")
    assert out["non_independent"] == ["r2"]


def test_12_incentive_conflict_flagged():
    out = R.detect_incentive_conflicts([
        {"id": "rule1", "rewards": ["output_length"], "checks_task_success": False},
        {"id": "rule2", "rewards": ["output_length"], "checks_task_success": True}])
    assert out["flagged"] == ["rule1"]


def test_13_transfer_validator_catches_gap():
    expected = {"goal": "build a search page", "constraints": "no paid services",
                "current_state": "layout finished", "next_action": "add search box"}
    got = {"goal": "build a search page", "constraints": "no paid services",
           "current_state": "layout finished", "next_action": "paint the office"}
    out = R.validate_transfer(expected, got)
    assert out["valid"] is False and out["missing"] == ["next_action"]


def test_14_capability_drift_on_provider_change():
    base = [{"test_id": "t1", "passed": True, "output_shape": "json"},
            {"test_id": "t2", "passed": True, "output_shape": "json"}]
    cur = [{"test_id": "t1", "passed": False, "output_shape": "json"},
           {"test_id": "t2", "passed": True, "output_shape": "json"}]
    out = R.monitor_capability_drift(base, cur, provider_change=True)
    assert out["rerun_required"] is True and out["regressions"] == ["t1"]


def test_15_redundancy_spots_duplicate_analysis():
    out = R.redundancy_optimizer([
        {"agent_id": "a1", "analysis": "trees cool cities by shade and water",
         "evidence_sources": ["s1"]},
        {"agent_id": "a2", "analysis": "trees cool cities by shade and water",
         "evidence_sources": ["s1"]},
        {"agent_id": "a3", "analysis": "solar panels cut grid demand sharply",
         "evidence_sources": ["s9"]}])
    assert out["duplicate_pairs"] == [["a1", "a2"]]
    assert out["independent_agents"] == ["a3"]


def test_16_escalation_routes_only_unresolved_part():
    out = R.escalation_router({
        "resolved_parts": ["research", "draft"],
        "unresolved_part": {"id": "legal-check", "specialist_kind": "legal",
                            "description": "check licence wording"}})
    assert out["escalate"] is True and out["target_part"] == "legal-check"
    assert out["restart"] is False
    assert out["context"]["resolved_parts"] == ["research", "draft"]


def test_17_architecture_checker_blocks_bad_direction():
    modules = [
        {"name": "ui", "layer": "ui", "imports": [{"module": "db", "layer": "data"}]},
        {"name": "svc", "layer": "service", "imports": [{"module": "db", "layer": "data"}]}]
    out = R.architecture_check(modules, {"ui": ["service"], "service": ["data"], "data": []})
    assert len(out["violations"]) == 1
    assert out["violations"][0]["module"] == "ui"
    assert out["clean"] is False


def test_18_behavioral_compatibility_diff():
    before = [{"case_id": "edge-empty", "output": "empty list", "error": None},
              {"case_id": "bad-input", "output": None, "error": "bad input"}]
    after = [{"case_id": "edge-empty", "output": "crash", "error": None},
             {"case_id": "bad-input", "output": None, "error": "bad input"}]
    out = R.behavioral_compatibility(before, after)
    assert out["compatible"] is False
    assert out["differences"][0]["case_id"] == "edge-empty"


def test_19_mutation_survivor_reveals_weak_test():
    src = "def check(a, b):\n    return a + b > 0\n"
    # Kill check that only notices the arithmetic swap: the comparison
    # flip survives, proving the suite is weak on that operator.
    out = R.run_mutation_testing(src, lambda m: "a - b" in m)
    assert out["total"] == 2
    assert "arithmetic operator swapped" in out["killed"]
    assert "comparison flipped" in out["surviving"]
    assert out["score"] == 0.5
    # A check that catches nothing leaves every mutant surviving.
    out2 = R.run_mutation_testing(src, lambda m: False)
    assert out2["score"] == 0.0 and len(out2["surviving"]) == 2


def test_20_minimal_fix_picks_smallest_passing():
    out = R.minimal_fix_finder([
        {"id": "big", "lines_changed": 90, "files_changed": 3,
         "satisfies_requirements": True, "tests_pass": True},
        {"id": "tiny", "lines_changed": 4, "files_changed": 1,
         "satisfies_requirements": True, "tests_pass": True},
        {"id": "tiny-broken", "lines_changed": 1, "files_changed": 1,
         "satisfies_requirements": True, "tests_pass": False}])
    assert out["chosen"] == "tiny"


def test_21_coupling_flags_frequent_pair():
    hist = [{"change_id": f"c{i}", "modules": ["auth", "billing"]} for i in range(3)]
    hist.append({"change_id": "c9", "modules": ["auth", "search"]})
    out = R.coupling_analyzer(hist, threshold=2)
    assert out["coupled_pairs"] == [{"modules": ["auth", "billing"], "joint_changes": 3}]
    risky = R.flag_risky_change(["auth", "billing"], out["coupled_pairs"])
    assert risky["risky"] is True


def test_22_dead_end_interface_without_backend():
    out = R.dead_end_detector([
        {"name": "Export", "has_backend": False, "has_storage": True,
         "has_events": True, "end_to_end": False},
        {"name": "Search", "has_backend": True, "has_storage": True,
         "has_events": True, "end_to_end": True}])
    assert out["dead_ends"] == ["Export"]


def test_23_reproducibility_ignores_timestamp_only():
    a = "version: 1\nbuilt_at: 2026-10-24T10:00:00\n"
    b = "version: 1\nbuilt_at: 2026-10-24T11:30:00\n"
    out = R.reproducibility_check(a, b)
    assert out["reproducible"] is True and out["identical"] is False
    assert R.reproducibility_check(a, "version: 2\n").get("reproducible") is False


def test_24_ownership_map_points_to_source():
    out = R.ownership_map([
        {"path": "gen/api.py", "generated": True, "generator_source": "spec/api.yaml"},
        {"path": "app/hand.py", "generated": False}])
    assert out["files"][0]["change_source"] == "spec/api.yaml"
    assert out["files"][1]["change_source"] == "app/hand.py"


def test_25_independence_counts_original_once():
    out = R.source_independence([
        {"id": "s1", "original_source": "wire-a"},
        {"id": "s2", "original_source": "wire-a"},
        {"id": "s3", "original_source": "wire-b"}])
    assert out["independent_count"] == 2
    assert out["duplicated_reporting"] == {"wire-a": ["s1", "s2"]}


def test_26_fact_change_lists_impacted_conclusions():
    cons = [{"id": "c1", "depends_on": ["f1", "f2"]}, {"id": "c2", "depends_on": ["f3"]}]
    assert R.fact_dependency_graph(["f1"], cons)["graph"] == {"c1": ["f1", "f2"], "c2": ["f3"]}
    assert R.impacted_by_fact_change(cons, "f1")["impacted_conclusions"] == ["c1"]


def test_27_timestamp_resolver_separates_times():
    out = R.timestamp_resolver([
        {"id": "r1", "event_time": "2026-01-05", "publication_time": "2026-01-01",
         "retrieval_time": "2026-10-24", "last_modified_time": "2026-01-02"}])
    assert out["conflicts"] == ["r1"]  # event after its own publication
    rec = out["records"][0]
    assert rec["event_time"] != rec["publication_time"] != rec["retrieval_time"]


def test_28_heatmap_grades_each_requirement():
    out = R.knowledge_gap_heatmap(
        [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}, {"id": "q4"}],
        [{"requirement_id": "q1", "strength": "strong"},
         {"requirement_id": "q2", "strength": "weak"},
         {"requirement_id": "q4", "strength": "strong", "conflicting": True}])
    grades = {h["requirement_id"]: h["grade"] for h in out["heatmap"]}
    assert grades == {"q1": "strong", "q2": "weak", "q3": "none", "q4": "conflicting"}


def test_29_entitlement_denies_before_retrieval():
    doc = {"id": "d1", "required_permission": "docs.read", "tenant_id": "t1"}
    assert R.entitlement_check(["docs.read"], "t1", doc)["allowed"] is True
    assert R.entitlement_check(["docs.read"], "t2", doc)["allowed"] is False
    assert R.entitlement_check([], "t1", doc)["allowed"] is False


def test_30_granularity_rejects_topic_only_citation():
    claims = [{"id": "cl1", "text": "trees cool cities by shade and evaporation",
               "citation_passage_id": "p1"}]
    passages = [{"id": "p1", "text": "Cities are warm in summer and trees are green plants."}]
    out = R.citation_granularity(claims, passages)
    assert out["unsupported"] == ["cl1"]
    good = R.citation_granularity(
        [{"id": "cl1", "text": "trees cool cities by shade", "citation_passage_id": "p1"}],
        [{"id": "p1", "text": "Trees cool cities by shade on hot days."}])
    assert good["unsupported"] == []


def test_31_freshness_budget_per_kind():
    out = R.freshness_budget([
        {"id": "api-doc", "kind": "api_documentation", "age_days": 120},
        {"id": "history", "kind": "historical_background", "age_days": 120}])
    assert out["stale"] == ["api-doc"]


def test_32_dispute_preserves_both_claims():
    out = R.dispute_workflow([{
        "id": "d1",
        "claim_a": {"value": 10, "unit": "km"},
        "claim_b": {"value": 12, "unit": "km"}}])
    d = out["disputes"][0]
    assert d["status"] == "open_for_investigation"
    assert d["resolved_to"] is None
    assert len(d["preserved_claims"]) == 2
    assert d["exact_disagreement"] == ["value"]


def test_33_workload_shape_classifies():
    assert R.workload_shape({"deadline_seconds": 10})["shape"] == "latency_sensitive"
    assert R.workload_shape({"memory_mb": 8192})["shape"] == "memory_bound"
    assert R.workload_shape({"tool_calls": 80})["shape"] == "tool_bound"
    assert R.workload_shape({"verification_steps": 6, "total_steps": 8})["shape"] == "verification_heavy"


def test_34_context_utility_flags_expensive_low_value():
    out = R.context_utility_profiler([
        {"category": "full-history", "tokens": 5000, "outcome_gain": 0.01},
        {"category": "full-history", "tokens": 5000, "outcome_gain": 0.02},
        {"category": "summary", "tokens": 300, "outcome_gain": 0.4}])
    assert out["expensive_low_value"] == ["full-history"]


def test_35_breakeven_rejects_costly_split():
    out = R.breakeven_estimator(single_time=100, agent_count=4, per_agent_time=40,
                                coordination_overhead=30, duplicated_context_cost=20,
                                merge_cost=20)
    assert out["worth_splitting"] is False and out["split_total"] == 110
    ok = R.breakeven_estimator(single_time=100, agent_count=4, per_agent_time=20,
                               coordination_overhead=5, duplicated_context_cost=5,
                               merge_cost=5)
    assert ok["worth_splitting"] is True


def test_36_memory_forecast_blocks_overload():
    out = R.memory_pressure_forecast(8000, [4000, 3000],
                                     [{"memory_mb": 2000}])
    assert out["would_exceed"] is True and out["needed_mb"] == 9000
    assert R.memory_pressure_forecast(16000, [4000], []).get("would_exceed") is False


def test_37_starvation_flags_repeated_delays():
    out = R.starvation_detector([
        {"task_id": "t1", "cause": "provider_limit"},
        {"task_id": "t1", "cause": "provider_limit"},
        {"task_id": "t1", "cause": "low_priority"},
        {"task_id": "t2", "cause": "scheduling"}])
    assert out["starving"] == ["t1"]


def test_38_adaptive_sampling_keeps_mandatory():
    checks = [{"id": "safety", "mandatory": True, "risk_level": "high"},
              {"id": "correctness", "mandatory": True, "risk_level": "high"},
              {"id": "style", "mandatory": False, "risk_level": "low"}]
    low = R.adaptive_verification("low", checks)
    assert set(low["checks_run"]) == {"safety", "correctness"}
    assert low["mandatory_preserved"] is True
    high = R.adaptive_verification("high", checks)
    assert set(high["checks_run"]) == {"safety", "correctness", "style"}


def test_39_cost_reconciliation_reports_variance():
    out = R.cost_reconciliation(
        [{"id": "m1", "estimated_cost": 1.0}, {"id": "m2", "estimated_cost": 2.0}],
        [{"id": "m1", "reported_cost": 1.25, "invoice_available": True}])
    rec = {r["id"]: r for r in out["reconciliation"]}
    assert rec["m1"]["variance"] == 0.25
    assert rec["m2"]["unmatched"] is True
    assert out["unmatched"] == ["m2"]


def test_40_efficiency_gate_blocks_unjustified_rise():
    base = {"time_seconds": 100, "memory_mb": 500, "tool_calls": 10, "cost_usd": 1.0}
    cur = {"time_seconds": 150, "memory_mb": 500, "tool_calls": 10, "cost_usd": 1.0}
    assert R.efficiency_gate(base, cur)["blocked"] is True
    assert R.efficiency_gate(base, cur, justified_quality_gain=True,
                             justification="fixes a data-loss bug")["blocked"] is False
    assert R.efficiency_gate(base, base)["verdict"] == "pass"


def test_17b_codegraph_integration_real_import_edges(tmp_path):
    """Boundary test: real CodeGraph import edges feed the checker."""
    import sqlite3
    from app.codegraph import build_graph
    (tmp_path / "ui_mod.py").write_text("import data_mod\n")
    (tmp_path / "data_mod.py").write_text("VALUE = 1\n")
    db = tmp_path / "graph.db"
    build_graph(tmp_path, db)
    conn = sqlite3.connect(db)
    edges = conn.execute(
        "SELECT src, dst FROM edges WHERE kind='IMPORTS'").fetchall()
    conn.close()
    assert any("ui_mod" in s and "data_mod" in d for s, d in edges)
    modules = [{"name": "ui_mod", "layer": "ui",
                "imports": [{"module": "data_mod", "layer": "data"}]}]
    out = R.architecture_check(modules, {"ui": ["service"], "service": ["data"], "data": []})
    assert len(out["violations"]) == 1


def test_39b_ledger_seam_real_store(tmp_path):
    """Boundary test: real store usage ledger totals feed reconciliation."""
    from app.store import Store
    store = Store(str(tmp_path / "s.db"), str(tmp_path / "art"))
    store.add_usage("t1", "m1", "model", 1.5, "call")
    store.add_usage("t1", "m1", "model", 0.5, "call")
    summary = store.usage_summary("t1", "m1")
    assert summary["total_usd"] == 2.0
    out = R.cost_reconciliation(
        [{"id": "m1", "estimated_cost": summary["total_usd"]}],
        [{"id": "m1", "reported_cost": 2.0, "invoice_available": True}])
    assert out["reconciliation"][0]["variance"] == 0.0
    assert out["unmatched"] == []


def test_layer_contracts_cover_five_layers():
    assert set(R.LAYER_CONTRACTS) == {"L1", "L2", "L3", "L4", "L5"}
    for layer in R.LAYER_CONTRACTS.values():
        assert layer["inputs"] and layer["outputs"] and layer["failures"]
