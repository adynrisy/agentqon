"""W10 — fifth-document gap-fix (gap-fifthdoc branch).

One named test cluster per section (§1–§21) plus HTTP smoke tests for the
/v1/deep/* surface. Engine tests are pure and deterministic; no live
browser, provider or network is touched (API-lab sends use a mock transport,
the browser surface records sessions without driving a real browser).
"""
import os

import pytest

from app.fifthdoc import FEATURE_MAP
from app.fifthdoc import (api_lab, browser_surface, disagreement,
                          env_inspector, extensions, hitl, inbox,
                          inspector, model_lab, output_quality,
                          personalization, policy_tests, queue_center,
                          release, replay, supply_chain, tiny, transform,
                          undo, watchers, workflows)


def _wf(nodes):
    wf = workflows.Workflow(workflow_id="wf")
    for node in nodes:
        wf.add_node(node)
    return wf


def _step(nid, kind, nxt=None, **kw):
    return workflows.Node(node_id=nid, kind=kind, next_nodes=nxt or [], **kw)


# ---------------------------------------------------------------- §1 undo

def test_s01_timeline_orders_and_selective_rollback():
    tl = undo.Timeline()
    tl.record(undo.TimelineEntry("e1", "file.edit", "a.txt", "old", "new"))
    tl.record(undo.TimelineEntry("e2", "file.edit", "b.txt", "B0", "B1"))
    files = {"a.txt": "new", "b.txt": "B1"}
    assert tl.rollback("e1", files)["ok"] is True
    assert files == {"a.txt": "old", "b.txt": "B1"}  # later unrelated work kept


def test_s01_external_action_never_promised_reversible():
    tl = undo.Timeline()
    entry = tl.record(undo.TimelineEntry("e9", "email.send", "boss", None, "sent"))
    assert entry.reversible is False
    assert tl.preview_rollback("e9", {})["irreversible"] is True
    assert "cannot be undone" in tl.recovery_manifest()["cannot_be_restored_note"]


def test_s01_newer_user_change_refused_then_previewed():
    tl = undo.Timeline()
    tl.record(undo.TimelineEntry("e1", "file.edit", "a.txt", "old", "new"))
    files = {"a.txt": "user-edited-later"}
    preview = tl.preview_rollback("e1", files)
    assert preview["newer_user_change_detected"] is True
    assert tl.rollback("e1", files)["ok"] is False
    assert tl.rollback("e1", files, force=True)["ok"] is True


def test_s01_deleted_restore_checkpoint_start_compare_storage_limit():
    tl = undo.Timeline(storage_limit_bytes=10)
    tl.record(undo.TimelineEntry("s1", "file.edit", "small.txt", "ab", "cd"))
    tl.record(undo.TimelineEntry("d1", "file.delete", "gone.txt", "content", None))
    assert tl.restore_deleted("gone.txt")["content"] == "content"
    assert tl.dropped_snapshots == ["s1"]  # oldest snapshot dropped past the limit
    tl2 = undo.Timeline()
    tl2.record(undo.TimelineEntry("e1", "file.create", "x", None, "v"))
    tl2.checkpoint("before-big-mission")
    assert "before-big-mission" in tl2.checkpoints
    assert tl2.compare_to_start({}, {"x": "v"})["added"] == ["x"]


def test_s01_patch_undo_and_restore_selected():
    tl = undo.Timeline()
    tl.record(undo.TimelineEntry("p1", "patch.apply", "code.py", "A", "B"))
    tl.record(undo.TimelineEntry("p2", "patch.apply", "code.py", "B", "C"))
    files = {"code.py": "C"}
    # Rolling back p1 alone while p2 is applied is refused: p2 is a newer
    # change that would be lost. Undo in order, newest first.
    assert tl.restore_selected(["p1"], files)["refused"] == ["p1"]
    result = tl.restore_selected(["p2", "p1"], files)
    assert result["applied"] == ["p2", "p1"] and files["code.py"] == "A"


# ---------------------------------------------------------------- §2 inbox

def test_s02_all_twelve_types_and_dismiss_never_cancels_mission():
    box = inbox.Inbox()
    for i, kind in enumerate(inbox.TYPES):
        box.add(inbox.InboxItem(f"i{i}", kind, f"title {kind}", mission_id="m1"))
    assert len(inbox.TYPES) == 12
    assert box.outstanding_count() == 12
    result = box.apply("i0", "Dismiss")
    assert result["mission_cancelled"] is False
    assert box.outstanding_count() == 11


def test_s02_state_actions_priority_filters_bulk():
    box = inbox.Inbox()
    box.add(inbox.InboxItem("a", "approval", "t", state="awaiting_decision",
                            priority="urgent"))
    box.add(inbox.InboxItem("b", "failed_mission", "t", state="failed"))
    assert "Approve" in box.actions_for(box.items["a"])
    assert "Retry" in box.actions_for(box.items["b"])
    assert [i.item_id for i in box.list(priority="urgent")] == ["a"]
    assert box.bulk(["a", "b"], "Dismiss")["succeeded"] == 2
    assert box.apply("a", "Approve")["ok"] is False  # dismissed items stay put


# ---------------------------------------------------------------- §3 HITL

def test_s03_plan_gate_distinct_from_risky_step_approval():
    gates = hitl.Hitl()
    gates.open_gate(hitl.Gate("g-plan", "plan", "m1"))
    gates.open_gate(hitl.Gate("g-step", "step", "m1", step_id="s1"))
    result = gates.approve_plan("g-plan")
    assert result["blanket_approval_of_risky_actions"] is False
    assert gates.gates["g-step"].state == "waiting"  # step still needs approval
    assert gates.requires_review("file.delete") is True
    assert gates.requires_review("artifact.read") is False


def test_s03_editable_payload_rejection_feedback_timeout_delegation():
    gates = hitl.Hitl(timeout_policy="auto_reject")
    gates.open_gate(hitl.Gate("g1", "step", "m1", payload={"x": 1},
                              timeout_seconds=0))
    assert gates.approve("g1", edited_payload={"x": 2})["payload_edited"] is True
    gates.open_gate(hitl.Gate("g2", "step", "m1", timeout_seconds=0))
    assert gates.reject("g2", "too risky")["feedback"] == "too risky"
    gates.open_gate(hitl.Gate("g3", "step", "m1", timeout_seconds=0))
    timed = gates.check_timeouts(now=9999999999.0)
    assert any(t["gate_id"] == "g3" and t["state"] == "rejected" for t in timed)
    assert "reviewer is the owner" in gates.delegation_note()
    assert gates.resume_checkpoint("m1", "s1")["approved_gates"] >= 1


def test_s03_clarification_roundtrip():
    gates = hitl.Hitl()
    gates.ask(hitl.Clarification("c1", "m1", "Which format?"))
    assert gates.answer("c1", "Markdown")["state"] == "answered"
    assert gates.answer("nope", "x")["ok"] is False


# ---------------------------------------------------------------- §4 workflows

def _linear_wf():
    return _wf([
        _step("s", "start", ["a"]),
        _step("a", "action", ["f"], inputs={"in": 1}, outputs={"out": 2}),
        _step("f", "finish"),
    ])


def test_s04_valid_workflow_dry_run_trace_and_export_import():
    wf = _linear_wf()
    assert wf.validate()["ok"] is True
    run = wf.dry_run()
    assert [t["node_id"] for t in run["trace"]] == ["s", "a", "f"]
    assert run["trace"][1]["would_execute"] is True
    wf2 = workflows.Workflow.import_(wf.export())
    assert wf2.validate()["ok"] is True
    assert wf.save_version() == 1 and wf.version == 2


def test_s04_cycle_unreachable_and_loop_limits_rejected():
    cyclic = _wf([_step("s", "start", ["a"]), _step("a", "action", ["a"]),
                  _step("f", "finish")])
    result = cyclic.validate()
    assert result["ok"] is False
    assert any("Cycle" in e for e in result["errors"])
    assert any("unreachable" in w for w in result["warnings"])
    bad_loop = _wf([_step("s", "start", ["l"]), _step("l", "loop", ["f"]),
                    _step("f", "finish")])
    assert any("max_iterations" in e for e in bad_loop.validate()["errors"])


def test_s04_parallel_join_approval_retry_subworkflow_nodes():
    wf = _wf([
        _step("s", "start", ["p"]),
        _step("p", "parallel", ["a", "b"]),
        _step("a", "action", ["j"]),
        _step("b", "action", ["j"]),
        _step("j", "join", ["ap"]),
        _step("ap", "approval", ["r"]),
        _step("r", "retry", ["sw"]),
        _step("sw", "subworkflow", ["f"]),
        _step("f", "finish"),
    ])
    assert wf.validate()["ok"] is True


def test_s04_step_must_declare_all_fields():
    node = workflows.Node(node_id="x", kind="action")
    wf = _wf([_step("s", "start", ["x"]), node, _step("f", "finish")])
    assert wf.validate()["ok"] is True  # defaults declare every field
    node.on_failure = "explode"
    assert any("failure behaviour" in e for e in wf.validate()["errors"])


# ---------------------------------------------------------------- §5 transform

def test_s05_json_csv_roundtrip_and_malformed_rows_reported():
    rows = [{"a": 1, "b": "x"}, {"a": 2}]
    text, problems = transform.json_to_csv(rows)
    assert "a,b" in text.splitlines()[0]
    back, problems2 = transform.csv_to_json(text)
    assert back[0]["a"] == "1" and back[1]["b"] in ("", None)
    _, problems3 = transform.json_to_csv([{"a": 1}, "not-a-row"])
    assert problems3 and "not an object" in problems3[0]


def test_s05_delimiter_encoding_yaml_xml_markdown_html():
    assert transform.detect_delimiter("a;b\n1;2\n") == ";"
    assert transform.detect_encoding(b"\xef\xbb\xbfhi") == "utf-8-sig"
    assert transform.detect_encoding(b"\xff\xfeh\x00") == "utf-16"
    out, problems = transform.yaml_to_json("name: demo\ntags:\n- a\n- b\n")
    assert out["name"] == "demo" and out["tags"] == ["a", "b"]
    assert transform.yaml_validate("broken line") != []
    tree, _ = transform.xml_to_json("<root><child>hi</child></root>")
    assert tree["children"][0]["text"] == "hi"
    assert transform.markdown_tables("| A | B |\n|---|---|\n| 1 | 2 |\n") == [[["A", "B"], ["1", "2"]]]
    assert transform.html_to_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_s05_normalize_dates_schema_rename_dedup_sort_stream_preview():
    assert transform.normalize_text("  café  ") == "café"
    converted = transform.convert_datetime("2026-10-24T12:00:00", "UTC", "Asia/Karachi")
    assert converted.startswith("2026-10-24T17:00:00")
    rows = [{"a": 1}, {"a": 1}, {"b": 2}]
    deduped, removed = transform.dedup_rows(rows)
    assert removed == 1
    assert transform.validate_schema(rows, ["a"])["missing"] != []
    assert transform.rename_fields([{"a": 1}], {"a": "alpha"}) == [{"alpha": 1}]
    assert [c for c in transform.stream_rows(range(5), chunk_size=2)] == [[0, 1], [2, 3], [4]]
    assert transform.infer_types([{"a": "3"}])["a"] == "integer-string"
    assert transform.preview(rows, limit=1)["total_rows"] == 3


# ---------------------------------------------------------------- §6 browser

def test_s06_browser_session_permission_redaction_and_barrier():
    session = browser_surface.BrowserSession("s1", permission_granted=False)
    assert session.navigate("https://example.com")["ok"] is False
    session.permission_granted = True
    session.open_tab("t1")
    assert session.navigate("https://example.com")["ok"] is True
    assert session.submit_form({"password": "hunter2"}, confirmed=True)["ok"] is True
    assert session.actions[-1]["password"] == "[redacted]"
    assert session.submit_form({"q": "x"}, confirmed=False)["ok"] is False
    barrier = browser_surface.detect_access_barrier("Please solve this captcha", 200)
    assert barrier["blocked"] is True and "never bypasses" in barrier["action"]
    assert session.auth_state(9999999999.0, now=1000.0)["signed_in"] is True
    assert session.cleanup()["closed"] is True


def test_s06_browser_tabs_snapshots_locators_downloads_popup():
    session = browser_surface.BrowserSession("s1", permission_granted=True)
    session.open_tab("t1", "https://a.example")
    session.open_tab("t2", "https://b.example")
    assert session.select_tab("t1")["active_tab"] == "t1"
    assert session.select_tab("nope")["ok"] is False
    assert session.page_loaded("t1", "Title", "hello")["access_barrier"]["blocked"] is False
    snap = session.dom_snapshot([{"tag": "input", "name": "password", "value": "x"}])
    assert snap["elements"][0]["value"] == "[redacted]"
    assert session.inspect_locator("#btn", True, 1)["found"] is True
    assert session.note_download("file.pdf", "https://x/file.pdf")["downloads"] == 1
    assert session.note_popup("https://pop.example")["ok"] is True
    assert session.page_changed("t1", "https://a.example", "https://b.example")["changed"] is True


# ---------------------------------------------------------------- §7 api lab

def test_s07_mutating_request_needs_permission_and_mock_send():
    req = api_lab.ApiRequest(method="DELETE", url="https://api.example/x")
    assert api_lab.send(req, permission_granted=False)["ok"] is False
    result = api_lab.send(req, permission_granted=True,
                          transport=lambda r: {"status": 204, "body": "", "mock": True})
    assert result["status"] == 204
    bad = api_lab.ApiRequest(method="GET", url="ftp://x")
    assert api_lab.send(bad)["problems"]


def test_s07_schema_compare_collections_redaction_codegen_spec():
    assert api_lab.validate_json_schema('{"a": 1}', ["a", "b"])["problems"] == ["Missing field: b"]
    a = api_lab.mock_response(200, '{"x": 1}', {})
    b = api_lab.mock_response(500, '{"x": 2}', {})
    assert api_lab.compare_runs(a, b)["diffs"] != []
    coll = api_lab.Collection(name="c")
    example = api_lab.save_example(
        coll, api_lab.ApiRequest("POST", "https://api.example", body='{"token": "abc"}'),
        api_lab.mock_response(200, '{"ok": true}'))
    assert example["body"] != '{"token": "abc"}'
    assert "urllib" in api_lab.client_codegen(api_lab.ApiRequest("GET", "https://x"))
    assert "curl" in api_lab.client_codegen(api_lab.ApiRequest("GET", "https://x"), "curl")
    assert len(api_lab.spec_examples({"base_url": "https://x", "paths": {"/v1": {"get": {}}}})) == 1
    assert api_lab.import_collection({"name": "i", "requests": [{"name": "r", "url": "https://x"}]}).name == "i"
    assert api_lab.validate_webhook('{"event": "ping"}', ["event"])["ok"] is True
    coll.environment.credential_set = "production"
    assert api_lab.sanitized_report(coll)["credential_set"] == "production"


# ---------------------------------------------------------------- §8 inspector

def test_s08_unified_inspector_links_and_filters_and_redacts():
    insp = inspector.Inspector()
    insp.record(inspector.InspectorEvent("e1", "tool", "web.read", "t1", "agent-a",
                                         artifact_id="art1", detail="api_key: hunter2"))
    insp.record(inspector.InspectorEvent("e2", "error", "tool.run", "t1", "agent-a",
                                         severity="error", detail="boom"))
    assert insp.events[0].detail == "api_key=[redacted]"
    assert len(insp.query(agent_id="agent-a")) == 2
    assert len(insp.query(stage="tool")) == 1
    assert insp.timeline_for_artifact("art1")[0].event_id == "e1"
    assert len(insp.error_history()) == 1
    assert insp.summary()["events"] == 2


# ---------------------------------------------------------------- §9 model lab

def _lab():
    cases = [model_lab.LabCase("c1", "coding", "write f", "ok"),
             model_lab.LabCase("c2", "tool_call", "call t", "ok")]
    return model_lab.ModelLab(cases)


def test_s09_fixed_set_context_benchmark_repeatability_profile():
    lab = _lab()
    assert lab.test_set_version.startswith("lab-set-")
    lab.record(model_lab.LabRun("r1", "m", "v1", "c1", "coding", True, latency_ms=100))
    lab.record(model_lab.LabRun("r2", "m", "v1", "c1", "coding", True, latency_ms=200))
    lab.record(model_lab.LabRun("r3", "m", "v1", "c2", "tool_call", False, error="timeout"))
    assert lab.benchmark("m")["pass_rate"] == round(2 / 3, 4)
    assert lab.benchmark("m")["error_rate"] == round(1 / 3, 4)
    assert lab.repeatability("m", "c1")["consistent"] is True
    assert lab.capability_profile("m")["coding"] == 1.0
    assert lab.context_limit_check(100, 150)["fits"] is False
    assert lab.context_limit_check(100, 50)["headroom_tokens"] == 50


def test_s09_compare_is_per_capability_and_regression():
    lab = _lab()
    lab.record(model_lab.LabRun("r1", "m1", "v1", "c1", "coding", True))
    lab.record(model_lab.LabRun("r2", "m2", "v1", "c1", "coding", False))
    lab.record(model_lab.LabRun("r3", "m2", "v1", "c2", "tool_call", True))
    result = lab.compare(["m1", "m2"])
    assert result["best_per_capability"]["coding"] == "m1"
    assert result["best_per_capability"]["tool_call"] == "m2"
    assert "not automatically best" in result["note"]
    lab.record(model_lab.LabRun("r4", "m1", "v2", "c1", "coding", False))
    assert lab.regression("m1", "v1", "v2")["regressed"] is True


# ---------------------------------------------------------------- §10 policy

def test_s10_policy_suite_report_and_controlled_cases():
    suite = policy_tests.PolicySuite("p1", [
        policy_tests.PolicyCase("c1", "secret_leakage", "s", "no leak"),
        policy_tests.PolicyCase("c2", "permission_respect", "s", "ask first"),
    ])
    suite.record(policy_tests.PolicyResult("c1", "secret_leakage", True, "clean"))
    suite.record(policy_tests.PolicyResult("c2", "permission_respect", False, "skipped ask"))
    report = suite.report()
    assert report["compliant"] is False
    assert report["by_kind"]["secret_leakage"]["passed"] == 1
    assert "does not prove" in report["honesty_note"]
    assert policy_tests.PolicySuite.check_secret_leakage("key abc123", ["abc123"]) is True
    assert policy_tests.PolicySuite.check_invented_completion(True, False) is True
    assert policy_tests.PolicySuite.check_invented_completion(True, True) is False
    assert policy_tests.PolicySuite.check_repeated_calls(["a", "a"])["flagged"] is True
    other = policy_tests.PolicySuite("p2", [])
    assert suite.compare_versions(other)["other_version"] == "p2"


# ---------------------------------------------------------------- §11 environment

def test_s11_env_inspect_tmp_project(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc", "lint": "eslint"}}')
    (tmp_path / "package-lock.json").write_text("{}")
    facts = env_inspector.inspect(str(tmp_path), required_env_vars=["DEFINITELY_MISSING_VAR_X"])
    assert facts["languages"] == ["node"]
    assert facts["build_command"] == "tsc"
    assert facts["lockfiles"] == ["package-lock.json"]
    assert "DEFINITELY_MISSING_VAR_X" in facts["missing_env_vars"]
    assert facts["fingerprint"] and len(facts["fingerprint"]) == 16
    assert env_inspector.warn_before_dependency_change("x", "1.0.0", "2.0.0")["warning"] != ""
    assert env_inspector.find_project_root(str(tmp_path)) == str(tmp_path)


def test_s11_env_git_status_is_readonly_shape(tmp_path):
    status = env_inspector.git_status_readonly(str(tmp_path))
    assert status["is_repo"] is False  # tmp dir is not a repo; call never writes


# ---------------------------------------------------------------- §12 output quality

def test_s12_per_format_checks_and_manifest():
    md = output_quality.check_markdown("# A\n### skipped\n")
    assert any("jumps" in p for p in md["problems"])
    assert md["outline"] == ["# A", "### skipped"]
    assert output_quality.check_html('<img src="x.png">')["problems"]
    assert output_quality.check_json_text("{bad")["ok"] is False
    assert output_quality.check_pdf_header(b"%PDF-1.7\n/Type /Page\n")["page_count_estimate"] == 1
    assert output_quality.check_svg("<svg width='1' height='1'></svg>")["ok"] is True
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x02\x00\x00\x00\x03"
    dims = output_quality.check_image_dimensions(png)
    assert (dims["width"], dims["height"]) == (2, 3)
    assert output_quality.check_filename("CON.txt")["problems"]
    assert output_quality.check_spreadsheet({"S": [{"v": "#REF!"}]})["problems"]
    assert output_quality.check_slides([{"title": ""}])["slide_count"] == 1
    manifest = output_quality.deliverable_manifest(["a", "b"], {"a": True}, {"a": True})
    assert manifest["missing"] == ["b"] and manifest["complete"] is False
    assert output_quality.licence_reminder(False) != ""


# ---------------------------------------------------------------- §13 watchers

def test_s13_watcher_detects_changes_debounces_and_loop_limits(tmp_path):
    watcher = watchers.Watcher()
    watcher.add_rule(watchers.WatchRule("r1", str(tmp_path), debounce_seconds=0))
    (tmp_path / "new.txt").write_text("hi")
    fired = watcher.poll()
    assert fired and fired[0]["events"][0]["kind"] == "created"
    (tmp_path / "new.txt").write_text("hi2")
    fired = watcher.poll()
    assert fired[0]["events"][0]["kind"] == "modified"
    (tmp_path / "new.txt").unlink()
    assert watcher.poll()[0]["events"][0]["kind"] == "deleted"
    watcher.mark_own_output("r1", "run-1")
    assert watcher.rules["r1"].own_run_ids == {"run-1"}
    assert watcher.pause_bulk("r1")["paused"] is True
    assert watcher.resume("r1")["paused"] is False
    # After resume the snapshot is the (empty) directory; a recorded snapshot
    # that still lists the deleted file is therefore stale.
    assert watcher.stale_working_copy("r1", {"new.txt": "1:2"}) is True


def test_s13_watcher_reconcile_recovers_missed_changes(tmp_path):
    # W16 item-19 dependency: a full rescan must surface changes even if
    # the incremental poll path missed them (overflow/coalescing).
    watcher = watchers.Watcher()
    watcher.add_rule(watchers.WatchRule("r1", str(tmp_path), debounce_seconds=0))
    (tmp_path / "a.txt").write_text("1")
    watcher.poll()
    # Simulate a missed notification: rewind the known snapshot.
    watcher._last_snapshot["r1"] = {}
    (tmp_path / "b.txt").write_text("2")
    result = watcher.reconcile("r1")
    assert result["ok"] is True
    assert result["created"] == ["a.txt", "b.txt"]
    assert watcher.reconcile("nope")["ok"] is False


def test_s13_watcher_loop_limit_blocks_storm(tmp_path):
    watcher = watchers.Watcher()
    rule = watchers.WatchRule("r1", str(tmp_path), debounce_seconds=0,
                              max_triggers_per_minute=1)
    watcher.add_rule(rule)
    (tmp_path / "a.txt").write_text("1")
    assert watcher.poll()
    (tmp_path / "b.txt").write_text("2")
    assert watcher.poll() == []  # second trigger inside the minute is suppressed
    assert any(h.get("blocked") for h in watcher.history)


# ---------------------------------------------------------------- §14 queue

def test_s14_queue_position_is_labelled_estimate():
    queue = queue_center.QueueCenter()
    queue.submit(queue_center.QueueJob("j1"))
    queue.submit(queue_center.QueueJob("j2"))
    position = queue.position("j2")
    assert position["estimate"] is True and position["known"] is False
    assert "Estimated" in position["label"]


def test_s14_queue_deadline_fairness_limits_dead_letter_requeue():
    queue = queue_center.QueueCenter(max_length=2,
                                     concurrency={"provider:p": 1})
    queue.submit(queue_center.QueueJob("j1", provider="p", priority=9))
    queue.submit(queue_center.QueueJob("j2", provider="p", priority=1))
    assert queue.submit(queue_center.QueueJob("j3"))["admission"] == "refused"
    picked = queue.next_job()
    assert picked.job_id == "j2"  # higher priority wins
    queue.start("j2")
    assert queue.next_job() is None  # provider concurrency limit reached
    assert queue.finish("j2", ok=False)["failures"] == 1
    queue.finish("j2", ok=False)
    result = queue.finish("j2", ok=False)
    assert result["dead_letter"] is True
    assert queue.requeue_after_inspection("j2")["state"] == "waiting"
    queue.pause_lane("background")
    assert queue.next_job("background") is None
    queue.resume_lane("background")
    assert queue.finish("j1", rate_limited=True)["state"] == "waiting"
    queue.cancel("j1")
    assert queue.graceful_shutdown()["ok"] is True
    assert queue.metrics()["dead_letters"] == 0


# ---------------------------------------------------------------- §15 disagreement

def test_s15_majority_never_decides_alone():
    positions = [disagreement.Position("a", "yes", ["e1"], ["x"]),
                 disagreement.Position("b", "yes", ["e1"], ["x"]),
                 disagreement.Position("c", "no", ["e2"], ["y"])]
    result = disagreement.resolve(positions)
    assert result.status == "escalated"  # 2-vs-1 majority is NOT enough
    assert result.chosen is None
    assert len(result.minority_report) == 3  # nothing auto-discarded
    assert "Unresolved" in disagreement.label_unresolved(result)


def test_s15_independent_verification_resolves_and_keeps_minority():
    positions = [disagreement.Position("a", "yes", ["e1"], ["x"]),
                 disagreement.Position("b", "no", ["e2"], ["y"])]
    result = disagreement.resolve(positions, independent_check={"yes": True, "no": False})
    assert result.status == "resolved" and result.chosen == "yes"
    assert result.minority_report[0]["answer"] == "no"
    assert disagreement.compare_evidence(positions)["shared_evidence"] == []
    assert disagreement.compare_assumptions(positions)["assumptions_differ_for"] == ["b"]


# ---------------------------------------------------------------- §16 replay

def _trace():
    return replay.Trace(trace_id="t1", app_version="22.5.0",
                        config={"model": "m1"}, seeds={"seed": 7},
                        steps=[replay.TraceStep("s1", "tool.run", outputs={"v": 1}),
                               replay.TraceStep("s2", "email.send", outputs={"v": 2})],
                        performed_external_actions=True)


def test_s16_replay_defaults_to_safe_mode_after_external_actions():
    result = replay.replay_plan(_trace())
    assert result["safe_mode"] is True
    assert "will not resend" in result["warning"]
    assert result["config_hash"]


def test_s16_rerun_step_substitution_divergence_bundle():
    trace = _trace()
    rerun = replay.rerun_step(trace, "s1", model="m2")
    assert rerun["substitutions"]["model"]["to"] == "m2"
    assert rerun["safe_mode"] is False
    assert replay.rerun_step(trace, "s2")["safe_mode"] is True
    other = _trace()
    other.steps[1].outputs = {"v": 99}
    compared = replay.compare_traces(trace, other)
    assert compared["first_divergence"]["step_index"] == 1
    bundle = replay.export_bundle(trace)
    assert bundle["non_reproducible"] != [] or bundle["steps"][0]["step_id"] == "s1"
    assert replay.Trace(trace_id="x", config={"a": 1}).config_hash()


# ---------------------------------------------------------------- §17 supply chain

def test_s17_inventory_sbom_compatibility_secrets_diff():
    deps = [supply_chain.Dependency("a", "1.0.0", "MIT", "pypi", "sha256:x", "sha256:x"),
            supply_chain.Dependency("b", None, None, "pypi"),
            supply_chain.Dependency("c", "2.0.0", "GPL", "pypi")]
    inv = supply_chain.inventory(deps)
    assert inv["missing_licence"] == ["b"] and inv["unpinned"] == ["b"]
    assert inv["integrity_mismatches"] == []
    sbom = supply_chain.generate_sbom(deps, "proj")
    assert sbom["bomFormat"] == "CycloneDX" and len(sbom["components"]) == 3
    assert supply_chain.check_compatibility(deps)["conflicts"] == ["c"]
    assert supply_chain.detect_licence("MIT License\n...") == "MIT"
    findings = supply_chain.scan_committed_secrets(
        {"f.py": "key = 'AKIA1234567890ABCDEF'"})
    assert findings and findings[0]["kind"] == "aws_key"
    assert supply_chain.diff_inventory(deps, deps[:1])["removed"] == ["b", "c"]
    assert supply_chain.suspicious_change(deps[0], supply_chain.Dependency(
        "a", "2.0.0", "MIT", "other"))["suspicious"] is True


def test_s17_vulnerability_scan_is_blocked_not_faked():
    result = supply_chain.vulnerability_scan()
    assert result["status"] == "blocked" and result["scanner"] is None
    assert "No vulnerability scanner is pinned" in result["reason"]
    report = supply_chain.compliance_report([], "proj")
    assert "never proves security" in report["honesty_note"]


# ---------------------------------------------------------------- §18 release

def _release():
    return release.Release(release_id="r1", version="1.2.0",
                           changes=["fix x"],
                           checks={c: True for c in release.CHECKLIST_TEMPLATE},
                           artifacts=[{"name": "app.zip", "verified": True}])


def test_s18_release_checklist_approval_deploy_gate():
    rel = _release()
    assert release.checklist_status(rel)["complete"] is True
    assert release.changelog(rel).startswith("# Release 1.2.0")
    assert release.verify_artifacts(rel)["ok"] is True
    assert release.dry_run_deploy(rel)["ok"] is True
    assert release.deploy(rel, permission_granted=False)["ok"] is False
    assert release.approve(rel)["approved"] is True
    assert release.deploy(rel, permission_granted=True)["deployed"] is True
    assert release.post_deploy_health(rel, {"ping": True})["healthy"] is True
    other = _release()
    other.version, other.changes = "1.3.0", ["fix x", "add y"]
    assert release.compare_releases(rel, other)["changes_added"] == ["add y"]
    assert release.incident_summary(rel, "none")["release"] == "1.2.0"


def test_s18_release_incomplete_checklist_blocks_approval():
    rel = release.Release(release_id="r2", version="0.1.0", checks={})
    assert release.approve(rel)["missing"] != []
    assert release.deploy(rel, permission_granted=True)["ok"] is False


# ---------------------------------------------------------------- §19 personalization

def test_s19_precedence_and_provenance_and_explain():
    prefs = personalization.Preferences()
    prefs.set("workspace", "response_detail", "brief")
    prefs.set("project", "response_detail", "detailed")
    prefs.set("user", "response_detail", "normal")
    assert prefs.effective("response_detail")["response_detail"] == {
        "value": "normal", "from": "user"}
    assert "system default" in prefs.explain("output_format")
    assert "user override" in prefs.explain("response_detail")
    assert prefs.set("system", "output_format", "x")["ok"] is False
    exported = prefs.export()
    prefs.reset()
    assert prefs.import_(exported)["applied"]
    assert prefs.effective("response_detail")["response_detail"]["value"] == "normal"


# ---------------------------------------------------------------- §20 extensions

def test_s20_manifest_validation_compat_signing_lifecycle():
    good = {"name": "ext", "version": "1.2.3", "entrypoint": "index.js",
            "permissions": ["artifact.read"], "api_version": "1"}
    assert extensions.validate_manifest(good)["ok"] is True
    bad = dict(good, permissions=["shell.run"])
    assert extensions.validate_manifest(bad)["problems"]
    assert extensions.check_api_compatibility("1", "2")["compatible"] is False
    files = {"manifest.json": '{"name": "ext"}'}
    sig = extensions.sign_package(files)
    assert extensions.verify_package(files, sig) is True
    assert extensions.verify_package(files, "sha256:nope") is False
    assert extensions.validate_package(files)["ok"] is False  # manifest lacks fields
    registry = extensions.ExtensionRegistry()
    ext = extensions.Extension(name="ext", version="1.2.3", entrypoint="index.js",
                               permissions=["artifact.read"])
    installed = registry.install(ext)
    assert installed["permissions_granted"] == ["artifact.read"]
    assert registry.health_check("ext")["health"] == "ok"
    assert registry.mock_tool_provider("t", {"ok": True})["mock"] is True
    assert "Permissions requested" in extensions.generate_docs(ext)
    assert registry.remove("ext")["removed"] == "ext"


# ---------------------------------------------------------------- §21 tiny

def test_s21_tiny_utilities():
    assert tiny.sanitize_filename('a<b>:"c?.txt') == "a_b___c_.txt"
    assert tiny.sanitize_filename("CON.txt") == "_CON.txt"
    assert tiny.convert_path("a/b/c", "windows") == "a\\b\\c"
    assert tiny.convert_path("/proj/src/x", "project_relative", "/proj") == "src/x"
    assert tiny.copy_as_shell_command(["echo", "hello world"]) == "echo 'hello world'"
    assert tiny.bulk_rename(["a.md", "b.md"], "doc-{n}-{name}") == ["doc-1-a", "doc-2-b"]
    assert tiny.collision_preview({"x"}, ["x"])["would_overwrite"] is True
    assert tiny.duplicate_mission_check("Hello  World", ["hello world"])["duplicate"] is True
    assert tiny.new_correlation_id().startswith("op-")
    history = tiny.DurationHistory()
    history.record("op", 2.0)
    history.record("op", 4.0)
    assert history.average("op") == 3.0
    assert tiny.missing_result_warning(["a", "b"], ["a"]) == ["b"]
    assert tiny.navigate_long_output("# H\n```\ncode\n```\nError: x\n")["code_blocks"] == 1
    assert tiny.recent_destinations_push(["a", "b"], "c") == ["c", "a", "b"]


def test_s21_confirmation_memory_never_overrides_policy():
    memory = tiny.ConfirmationMemory()
    assert memory.remember("file.delete") is False  # policy-gated: cannot remember
    assert memory.should_ask("file.delete") is True
    assert memory.remember("export") is True
    assert memory.should_ask("export") is False
    lock = tiny.StaleLock("l1", "agent", heartbeat_at=1000.0)
    assert lock.is_stale(now=2000.0) is True
    assert lock.is_stale(now=1010.0) is False
    artifacts = [{"name": "old", "created_at": 0.0}, {"name": "new", "created_at": 9999999999.0}]
    cleaned = tiny.cleanup_temp_artifacts(artifacts, policy_days=7, now=9999999999.0)
    assert cleaned["removed"] == ["old"] and cleaned["kept"] == ["new"]


# ---------------------------------------------------------------- subsystem map

def test_map_covers_all_sections_with_one_subsystem_and_one_system():
    from app.fifthdoc import SUBSYSTEMS, SYSTEMS
    assert len(FEATURE_MAP) == 21
    for key, (subsystem, system, _seam) in FEATURE_MAP.items():
        assert subsystem in SUBSYSTEMS, key
        assert system in SYSTEMS, key


# ---------------------------------------------------------------- HTTP smoke tests

def test_http_deep_map_and_inbox_roundtrip(client, tenant):
    response = client.get("/v1/deep/map", headers={"X-Tenant-Id": tenant, "X-User-Id": "t"})
    assert response.status_code == 200
    assert len(response.json()["features"]) == 21
    created = client.post("/v1/deep/inbox",
                          json={"item_id": "in1", "kind": "approval", "title": "Approve?"},
                          headers={"X-Tenant-Id": tenant, "X-User-Id": "t"})
    assert created.status_code == 201
    listed = client.get("/v1/deep/inbox", headers={"X-Tenant-Id": tenant, "X-User-Id": "t"})
    assert listed.json()["outstanding"] == 1
    acted = client.post("/v1/deep/inbox/in1/action", json={"action": "Dismiss"},
                        headers={"X-Tenant-Id": tenant, "X-User-Id": "t"})
    assert acted.json()["mission_cancelled"] is False
    assert client.get("/v1/deep/inbox", headers={"X-Tenant-Id": tenant, "X-User-Id": "t"}).json()["outstanding"] == 0


def test_http_undo_workflow_and_vuln_scan(client, tenant):
    headers = {"X-Tenant-Id": tenant, "X-User-Id": "t"}
    client.post("/v1/deep/undo/record",
                json={"entry_id": "e1", "kind": "file.edit", "target": "a.txt",
                      "before": "old", "after": "new"}, headers=headers)
    preview = client.post("/v1/deep/undo/preview", json={"entry_id": "e1"}, headers=headers)
    assert preview.json()["ok"] is True
    wf = {"workflow_id": "w", "nodes": [
        {"node_id": "s", "kind": "start", "next_nodes": ["f"]},
        {"node_id": "f", "kind": "finish", "next_nodes": []}]}
    assert client.post("/v1/deep/workflows/validate", json=wf, headers=headers).json()["ok"] is True
    scan = client.get("/v1/deep/supply-chain/vulnerability-scan", headers=headers)
    assert scan.json()["status"] == "blocked"
    deep = client.get("/v1/deep/map", headers=headers)
    assert deep.status_code == 200
