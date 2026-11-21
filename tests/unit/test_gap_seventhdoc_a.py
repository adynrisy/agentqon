"""Gap-seventhdoc group A: correctness / orchestration integrity."""
from app.integrity import (
    NaiveOrchestratorModel, SafeOrchestratorModel, audit_plan_vs_execution,
    build_proof_map, check_invariants, find_orchestration_bugs,
    generate_orchestration_sequences, replay_race)


def test_a1_invariant_monitor_flags_all_three_core_rules():
    alerts = check_invariants(
        {"status": "completed"},
        [{"action_id": "a1"}],
        [{"action_id": "a1"}, {"action_id": "a2"}],
        [{"artifact_id": "art1", "published": True, "checks": {}}])
    by_rule = {a["rule"]: a for a in alerts}
    assert by_rule["INV-1"]["ok"] is True
    assert by_rule["INV-2"]["ok"] is False  # a2 ran unapproved
    assert by_rule["INV-3"]["ok"] is False  # published without checks
    clean = check_invariants(
        {"status": "completed"}, [{"action_id": "a1"}], [{"action_id": "a1"}],
        [{"artifact_id": "art1", "published": True,
          "checks": {"schema_valid": True, "citations_checked": True}}])
    assert all(a["ok"] for a in clean)


def test_a1_structural_rules_generalized_not_duplicated():
    alerts = check_invariants(
        {"status": "completed"}, [], [],
        [], plan_tasks=[{"task_id": "t1", "dependencies": ["t2"]},
                        {"task_id": "t2", "dependencies": ["t1"]}],
        active_runs=[{"status": "active"}, {"status": "active"}],
        spent=900.0, reserved=200.0, ceiling=1000.0)
    by_rule = {a["rule"]: a for a in alerts}
    assert by_rule["INV-PLAN"]["ok"] is False
    assert by_rule["INV-ONE-RUN"]["ok"] is False
    assert by_rule["INV-RESERVE"]["ok"] is False


def test_a2_plan_vs_execution_flags_skipped_and_changed():
    plan = [{"step_id": "s1", "tool": "web.read", "deliverable": "Brief"},
            {"step_id": "s2", "tool": "email.send", "agent": "agent-x"},
            {"step_id": "s3", "tool": "web.read", "agent": "agent-y"}]
    out = audit_plan_vs_execution(
        plan, [{"tool": "web.read"}],
        [], {"s3": "agent-z"}, ["Brief"])
    assert [s["step_id"] for s in out["skipped"]] == ["s2"]
    assert [c["step_id"] for c in out["changed"]] == ["s3"]
    assert out["matched"] == ["s1"]


def test_a3_property_generator_finds_planted_bug_not_in_safe_model():
    seqs = generate_orchestration_sequences(seed=7, count=50, length=12)
    assert generate_orchestration_sequences(seed=7, count=50, length=12) == seqs
    found = find_orchestration_bugs(seqs, NaiveOrchestratorModel)
    assert found and "completed twice" in found[0]["bugs"][0]
    assert find_orchestration_bugs(seqs, SafeOrchestratorModel) == []


def test_a4_race_lab_detects_all_three_conflict_kinds():
    out = replay_race([
        {"actor": "a", "kind": "edit_file", "target": "f.py", "value": "x"},
        {"actor": "b", "kind": "edit_file", "target": "f.py", "value": "y"},
        {"actor": "a", "kind": "complete_step", "target": "s1"},
        {"actor": "b", "kind": "complete_step", "target": "s1"},
        {"actor": "a", "kind": "update_state", "target": "m", "version": 3},
        {"actor": "a", "kind": "update_state", "target": "m", "version": 2},
    ])
    assert len(out["conflicts"]) == 2
    assert len(out["rejected"]) == 1
    assert "out of order" in out["rejected"][0]["reason"]


def test_a5_proof_map_requires_real_evidence():
    reqs = [{"requirement_id": "r1", "text": "Brief cites sources"},
            {"requirement_id": "r2", "text": "Brief is polished"}]
    out = build_proof_map(reqs, {
        "r1": {"test_results": ["citation check passed"]},
        "r2": {"response_text": "A very polished brief."},
    })
    rows = {r["requirement_id"]: r for r in out["rows"]}
    assert rows["r1"]["proven"] is True
    assert rows["r2"]["proven"] is False
    assert "alone is not proof" in rows["r2"]["reason"]
    out2 = build_proof_map(reqs, {
        "r1": {"test_results": ["ok"], "unresolved_exceptions": ["boom"]},
        "r2": {"output_files": ["brief.md"]}})
    assert out2["rows"][0]["proven"] is False
    assert out2["verdict"] == "not_proven"
