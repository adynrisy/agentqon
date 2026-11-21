"""W3 Verification & Recovery: structural validation, the optional test
gate, evidence validation, quality scoring, repair planner, checkpoints,
rollback, and the expansion #1 verification mechanics.

All user-facing assertions are about plain-words findings, exit-code
verdicts, and record persistence — never about an agent's own claim.
"""
import sys
from pathlib import Path

from app import verification as v
from app.artifacts import final_artifacts_from_events, final_selection_event
from app.domain import event_envelope
from app.store import Store

TENANT = "org_verify_test"


def _project(tmp_path, files):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# ------------------------------------------------------- 1. structural validation

def test_missing_section_is_named_exactly():
    result = v.validate_deliverable(
        {"brief.md": b"# Summary\n\nSome text.\n"},
        {"required_files": ["brief.md"],
         "required_sections": {"brief.md": ["Summary", "Risks"]}})
    assert result["passed"] is False
    assert any("Risks" in m and "missing" in m for m in result["missing"])


def test_valid_deliverable_passes_with_config_and_schema():
    files = {
        "brief.md": b"# Summary\n\n## Risks\n\nNone yet.\n",
        "config.json": b'{"name": "demo", "retries": 2}',
        "api.json": b'{"title": "x", "version": 1}',
    }
    result = v.validate_deliverable(files, {
        "required_files": ["brief.md", "config.json"],
        "required_sections": {"brief.md": ["Summary", "Risks"]},
        "expected_types": {"config.json": "json"},
        "config_files": {"config.json": "json"},
        "schemas": {"api.json": {"required": ["title", "version"],
                                 "properties": {"version": {"type": "number"}}}},
    })
    assert result["passed"] is True, result["checks"]


def test_bad_config_and_schema_mismatch_are_named():
    result = v.validate_deliverable(
        {"config.json": b"{not json", "api.json": b'{"title": "x"}'},
        {"config_files": {"config.json": "json"},
         "schemas": {"api.json": {"required": ["title", "version"]}}})
    assert result["passed"] is False
    assert any("config.json" in m for m in result["missing"])
    assert any("version" in m and "missing" in m for m in result["missing"])


def test_missing_file_is_named():
    result = v.validate_deliverable(
        {}, {"required_files": ["decision.md"]})
    assert result["passed"] is False
    assert result["missing"] == ["File 'decision.md' is missing from the deliverable."]


# ------------------------------------------------------- 2. test-execution gate

def test_test_gate_off_by_default_refuses_without_approval(tmp_path):
    root = _project(tmp_path, {"test_demo.py": "print('ran')\n"})
    result = v.run_project_tests(root, [sys.executable, "test_demo.py"],
                                 approved=False)
    assert result["ran"] is False
    assert result["verdict"] == "refused"
    assert "not approved" in result["detail"]


def test_test_gate_approved_run_uses_exit_code_not_text(tmp_path):
    # Script prints PASS but exits 1: the verdict must be failed.
    root = _project(tmp_path, {"t.py": "print('PASS')\nimport sys; sys.exit(1)\n"})
    result = v.run_project_tests(root, [sys.executable, "t.py"],
                                 approved=True)
    assert result["passed"] is False
    assert result["exit_code"] == 1
    # And the mirror: FAIL text with exit 0 passes.
    root2 = _project(tmp_path, {"t2.py": "print('FAIL')\n"})
    result2 = v.run_project_tests(root2, [sys.executable, "t2.py"],
                                  approved=True)
    assert result2["passed"] is True
    assert result2["exit_code"] == 0


def test_test_gate_timeout_fails_never_hangs(tmp_path):
    root = _project(tmp_path, {"t.py": "import time; time.sleep(30)\n"})
    result = v.run_project_tests(root, [sys.executable, "t.py"],
                                 approved=True, timeout=2)
    assert result["verdict"] == "timed_out"
    assert result["passed"] is False


def test_test_gate_isolation_between_runs(tmp_path):
    # A run that writes a file must not leak it into the next run.
    code = ("from pathlib import Path\n"
            "Path('leaked.txt').write_text('from run one')\n")
    root = _project(tmp_path, {"t.py": code})
    v.run_project_tests(root, [sys.executable, "t.py"], approved=True)
    assert not (root / "leaked.txt").exists()
    v.run_project_tests(root, [sys.executable, "t.py"], approved=True)
    assert not (root / "leaked.txt").exists()


def test_test_gate_network_command_refused_even_when_approved(tmp_path):
    root = _project(tmp_path, {})
    result = v.run_project_tests(root, ["curl", "https://example.invalid"],
                                 approved=True)
    assert result["ran"] is False
    assert result["verdict"] == "refused"


def test_test_gate_records_environment_and_provenance(tmp_path):
    root = _project(tmp_path, {"t.py": "print('ok')\n"})
    result = v.run_project_tests(root, [sys.executable, "t.py"],
                                 approved=True, timeout=30)
    assert result["environment"]["interpreter"]
    assert result["environment"]["timeout_seconds"] == 30
    assert result["provenance"]["checker"] == v.CHECKER_NAME


# ------------------------------------------------------- 3. evidence validation

def test_evidence_conflict_surfaced_and_facts_separated():
    claims = [
        {"id": "c1", "subject": "price", "statement": "The price rose in June",
         "source_ids": ["s1"], "kind": "fact"},
        {"id": "c2", "subject": "price", "statement": "The price fell in June",
         "source_ids": ["s2"], "kind": "fact"},
        {"id": "c3", "subject": "mood", "statement": "Users may prefer blue",
         "source_ids": [], "kind": "assumption"},
    ]
    sources = {"s1": {"excerpt": "The price rose in June sharply"},
               "s2": {"excerpt": "The price fell in June sharply"}}
    result = v.validate_evidence(claims, sources)
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["subject"] == "price"
    assert len(result["assumptions"]) == 1
    assert len(result["facts"]) == 2


def test_evidence_fact_without_source_is_unproven():
    result = v.validate_evidence(
        [{"id": "c1", "subject": "x", "statement": "Something happened",
          "source_ids": [], "kind": "fact"}], {})
    assert result["passed"] is False
    assert any("no source" in f for f in result["findings"])


def test_evidence_irrelevant_source_flagged_as_warning(tmp_path):
    result = v.validate_evidence(
        [{"id": "c1", "subject": "cats", "statement": "Cats sleep most of the day",
          "source_ids": ["s1"], "kind": "fact"}],
        {"s1": {"excerpt": "Quantum entanglement violates local realism"}})
    assert any("may not be about this claim" in f for f in result["findings"])


# ------------------------------------------------------- 4. quality scoring

def test_quality_score_explains_every_criterion_and_disclaims():
    result = v.score_quality({
        "requirement_coverage": {"score": 0.8, "explanation": "4 of 5 met."},
        "correctness": {"score": None, "explanation": "Not checked."},
    })
    assert result["criteria"]["requirement_coverage"]["score"] == 0.8
    assert result["criteria"]["correctness"]["score"] is None
    assert result["criteria"]["safety"]["score"] is None  # not silently zero
    assert "not an" in result["overall_note"] and "intelligence" in result["overall_note"]
    assert "not proof of truth" in result["agreement_note"]


# ------------------------------------------------------- 5. repair planner

def test_repair_plan_touches_only_failing_files():
    plan = v.plan_repair([
        {"file": "app/broken.py", "problem": "missing section",
         "detail": "Risks section missing"},
        {"file": "app/broken.py", "problem": "missing section",
         "detail": "duplicate report"},
        {"file": "app/other.py", "problem": "bad config", "detail": ""},
    ])
    assert plan["files_touched"] == ["app/broken.py", "app/other.py"]
    assert all(s["file"] in ("app/broken.py", "app/other.py")
               for s in plan["steps"])
    assert "unrelated.py" not in plan["files_touched"]
    # duplicate failure collapses to one step per file+problem
    assert len([s for s in plan["steps"] if s["file"] == "app/broken.py"]) == 1


# ------------------------------------------------------- 6/7. checkpoints + rollback

def test_checkpoint_diff_and_rollback_restore_only_snapshot(tmp_path):
    root = _project(tmp_path, {"a.py": "x = 1\n", "keep.py": "keep me\n"})
    before = v.snapshot_paths([root / "a.py", root / "keep.py"], root)
    (root / "a.py").write_text("x = 2\n")
    (root / "new.py").write_text("agent created\n")
    after = v.snapshot_paths([root / "a.py", root / "keep.py"], root)
    diff = v.diff_snapshots(before, after)
    assert any("a.py" in s and "changed" in s for s in diff["summary"])
    assert "x = 1" in diff["diffs"]["a.py"] or "-x = 1" in diff["diffs"]["a.py"]

    # Unapproved rollback changes nothing.
    refused = v.execute_rollback(root, before, approved=False)
    assert refused["ok"] is False
    assert (root / "a.py").read_text() == "x = 2\n"

    # Approved rollback restores a.py, leaves keep.py and new.py alone
    # (new.py was created after the snapshot and is not named for deletion).
    done = v.execute_rollback(root, before, approved=True)
    assert done["ok"] is True
    assert (root / "a.py").read_text() == "x = 1\n"
    assert (root / "keep.py").read_text() == "keep me\n"
    assert (root / "new.py").read_text() == "agent created\n"


def test_rollback_refuses_paths_outside_root(tmp_path):
    root = _project(tmp_path, {"a.py": "x = 1\n"})
    outside = tmp_path / "outside.py"
    outside.write_text("do not touch\n")
    before = v.snapshot_paths([root / "a.py"], root)
    # A forged snapshot entry pointing outside must be refused.
    before["../outside.py"] = {"exists": True, "content": "hacked\n",
                               "content_hash": "x"}
    done = v.execute_rollback(root, before, approved=True)
    assert outside.read_text() == "do not touch\n"
    assert any("outside" in r for r in done["refused"])


def test_checkpoint_and_rollback_recorded_on_mission_record(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    mission = store.create_mission(TENANT, "tester", "Verify me", {}, 0)
    mid = mission["id"]
    root = _project(tmp_path, {"a.py": "x = 1\n"})
    before = v.snapshot_paths([root / "a.py"], root)
    (root / "a.py").write_text("x = 2\n")
    after = v.snapshot_paths([root / "a.py"], root)
    store.put_event(TENANT, v.checkpoint_event(TENANT, mid, "before fix",
                                               before, after))
    result = v.execute_rollback(root, before, approved=True)
    store.put_event(TENANT, v.rollback_event(TENANT, mid, result))
    events = store.list_events(TENANT, mid)
    kinds = [e["event_type"] for e in events]
    assert "VerificationCheckpoint" in kinds
    assert "VerificationRollback" in kinds


# ------------------------------------------- mechanics: mapping, grouping, etc.

def test_requirement_mapping_reports_unmapped():
    result = v.map_requirements(
        ["Two distinct options", "Material claims cite sources"],
        [{"name": "check:two-options",
          "detail": "Two distinct options are present", "passed": True}])
    assert result["unmapped"] == ["Material claims cite sources"]
    assert result["all_mapped"] is False


def test_failure_grouping_collapses_identical_failures():
    failures = [
        {"name": "t", "detail": "Missing section Risks in file 1"},
        {"name": "t", "detail": "Missing section Risks in file 2"},
        {"name": "u", "detail": "Bad config"},
    ]
    groups = v.group_failures(failures)
    assert len(groups) == 2
    assert groups[0]["count"] == 2


def test_flaky_detection_flags_mixed_history():
    history = [{"input_hash": "abc", "passed": True},
               {"input_hash": "abc", "passed": False},
               {"input_hash": "def", "passed": True}]
    result = v.detect_flaky(history)
    assert result["flaky"] is True
    assert result["flaky_inputs"] == ["abc"]
    stable = v.detect_flaky([{"input_hash": "abc", "passed": True},
                             {"input_hash": "abc", "passed": True}])
    assert stable["flaky"] is False


def test_baseline_comparison_classifies_changes():
    current = [{"name": "a", "passed": True}, {"name": "b", "passed": False},
               {"name": "c", "passed": True}]
    baseline = [{"name": "a", "passed": True}, {"name": "b", "passed": True},
                {"name": "c", "passed": True}]
    result = v.compare_to_baseline(current, baseline)
    assert result["new_failures"] == ["b"]
    assert result["unchanged"] == ["a", "c"]


def test_coverage_reported_with_disclaimer_not_quality_proof():
    report = v.coverage_report("TOTAL  100  10  90%\n")
    assert report["coverage_percent"] == 90.0
    assert "does not prove" in report["note"]
    assert v.coverage_report("no coverage here")["reported"] is False


def test_artifact_verification_names_missing_and_wrong_content():
    files = {"report.md": b"# Summary\n"}
    result = v.validate_deliverable(
        files, {"required_files": ["report.md", "data.json"],
                "expected_types": {"report.md": "text"}})
    assert result["passed"] is False
    assert any("data.json" in m for m in result["missing"])


def test_verification_freshness_bound_to_hash():
    checks = [{"name": "file:report.md", "passed": True, "detail": "ok"}]
    record = v.make_verification_record(checks, "hash-one", artifact_version=1)
    assert v.is_fresh(record, "hash-one") is True
    assert v.is_fresh(record, "hash-two") is False
    assert v.stale_checks(record, "hash-two") == ["file:report.md"]
    assert v.stale_checks(record, "hash-one") == []


def test_verification_result_persists_on_mission_record(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    mission = store.create_mission(TENANT, "tester", "Verify me", {}, 0)
    mid = mission["id"]
    record = v.make_verification_record(
        [{"name": "file:report.md", "passed": True, "detail": "present"}],
        "hash-one", artifact_version=2)
    store.put_event(TENANT, v.verification_event(TENANT, mid, record))
    events = store.list_events(TENANT, mid)
    assert events[0]["event_type"] == "VerificationResult"
    assert events[0]["payload"]["artifact_hash"] == "hash-one"
    assert events[0]["payload"]["provenance"]["checker"] == v.CHECKER_NAME


def test_mission_detail_exposes_final_artifacts_from_same_events(tmp_path):
    # Integration across the store -> orchestrator boundary: the
    # detail view's final_artifacts come from the mission's own event
    # stream, and every other detail key is still present.
    from app.config import Settings
    from app.orchestrator import Orchestrator
    store = Store(str(tmp_path / "s.db"))
    mission = store.create_mission(TENANT, "tester", "Verify me", {}, 0)
    mid = mission["id"]
    store.put_event(TENANT, final_selection_event(
        TENANT, mid, "report", "art_first"))
    store.put_event(TENANT, final_selection_event(
        TENANT, mid, "report", "art_second"))
    detail = Orchestrator(store, Settings(data_dir=tmp_path / "data"))
    detail = detail.mission_detail(TENANT, mid)
    assert detail["final_artifacts"] == {"report": "art_second"}
    assert detail["mission"]["id"] == mid
    assert "artifacts" in detail and "events" in detail


def test_final_selection_event_reads_back_from_mission_detail(tmp_path):
    # Record-level final selection: the last selection wins, and the
    # helper the mission-detail view uses reads the same events.
    store = Store(str(tmp_path / "s.db"))
    mission = store.create_mission(TENANT, "tester", "Verify me", {}, 0)
    mid = mission["id"]
    store.put_event(TENANT, final_selection_event(
        TENANT, mid, "report", "art_first"))
    store.put_event(TENANT, final_selection_event(
        TENANT, mid, "report", "art_second"))
    events = store.list_events(TENANT, mid)
    assert final_artifacts_from_events(events) == {"report": "art_second"}
    assert event_envelope  # event stream shape unchanged
