"""W17 thirteenth document: unit tests for app/thirteenth.py.

Every test uses injected fakes or local data only — no network, no live
backend, no real Windows APIs. Platform-gated behaviour (item 27) is
asserted on this Linux machine as 'unsupported with reasons'.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import thirteenth as T


# --- Group 1 ------------------------------------------------------------
def test_01_context_menu_preview_required_before_processing():
    menu = T.build_context_menu(["/tmp/report.md"])
    assert menu["status"] == "supported"
    assert [i["action"] for i in menu["items"]] == list(T.CONTEXT_MENU_ACTIONS)
    preview = T.preview_selection(["/tmp/report.md"], "explain_file")
    assert preview["preview"]["approved"] is False
    refused = T.process_selection(preview, approved=False)
    assert refused["status"] == "refused"
    approved = T.approve_preview(preview)
    done = T.process_selection(approved, approved=True)
    assert done["status"] == "allowed"
    assert T.preview_selection([], "analyze")["status"] == "refused"
    assert T.preview_selection(["x"], "bogus")["status"] == "refused"


def test_02_file_association_four_choices():
    for action in T.ASSOCIATION_ACTIONS:
        out = T.dispatch_association("/tmp/a.md", action)
        assert out["status"] == "allowed" and out["action"] == action
    assert T.dispatch_association("/tmp/a.md", "delete")["status"] == "refused"


def test_03_quick_capture_explicit_only():
    ok = T.create_capture(text="hello", file="/tmp/a.md")
    assert ok["carried"] == {"text": "hello", "file": "/tmp/a.md"}
    bad = T.create_capture(text="secret", source="clipboard_background")
    assert bad["status"] == "refused"
    assert "clipboard" in bad["message"]


def test_04_tray_menu_and_pause():
    menu = T.tray_menu("running", [{"id": "m1"}], ["job-1"])
    assert menu["engine_status"] == "running"
    assert menu["recent_missions"] == [{"id": "m1"}]
    assert T.pause_background_work("job-1", ["job-1"])["status"] == "paused"
    assert T.pause_background_work("job-9", ["job-1"])["status"] == "refused"


def test_05_portable_mode_data_dir_and_credential_rule(monkeypatch):
    monkeypatch.setenv("AGENTQON_DATA", "/tmp/portable-data")
    report = T.portable_mode_report(vault_present=True)
    assert report["data_dir"] == "/tmp/portable-data"
    assert "never plain text" in report["credential_rule"] \
        or "vault" in report["credential_rule"]
    refused = T.portable_mode_report(plaintext_credential_found=True)
    assert refused["status"] == "refused"
    assert "plain text" in refused["message"]
    monkeypatch.delenv("AGENTQON_DATA", raising=False)
    monkeypatch.delenv("OMNIMIND_DATA", raising=False)
    assert T.resolve_portable_data_dir("/x").as_posix() == "/x"


# --- Group 2 ------------------------------------------------------------
def test_06_mcp_gateway_permissions_default_deny_and_write_law():
    servers = {}
    assert T.register_mcp_server(servers, "srv", "stdio",
                                 {"read_file": {"files": []},
                                  "write_file": {"files": ["/work/"]}})["status"] == "supported"
    srv = servers["srv"]
    assert srv.tool_allowed("unknown_tool")["status"] == "refused"
    assert srv.tool_allowed("read_file")["status"] == "allowed"
    assert srv.tool_allowed("write_file", "/work/a.txt")["status"] == "allowed"
    assert srv.tool_allowed("write_file", "/other/a.txt")["status"] == "refused"
    assert srv.tool_allowed("write_file", "app/main.py")["status"] == "refused"
    assert T.register_mcp_server(servers, "srv", "stdio", {})["status"] == "refused"
    assert T.register_mcp_server(servers, "bad", "carrier-pigeon", {})["status"] == "refused"
    plan = srv.reconnect_plan()
    assert plan["status"] == "supported" and plan["delay_seconds"] == 1.0
    srv.reconnect_attempts = 3
    assert srv.reconnect_plan()["status"] == "unsupported"

    def fake_transport(req):
        assert req["method"] == "initialize"
        return {"jsonrpc": "2.0", "id": 1,
                "result": {"serverInfo": {"name": "fake", "version": "1"}}}
    assert T.mcp_client_handshake(fake_transport)["status"] == "supported"
    assert T.mcp_client_handshake(lambda r: {})["status"] == "unsupported"


def _fake_lsp_transport_factory():
    def transport(req):
        method = req.get("method", "")
        if method == "initialize":
            return {"result": {"serverInfo": {"name": "fake-ls", "version": "1"}}}
        if method == "textDocument/definition":
            return {"result": [{"uri": "file:///b.py", "range": {}}]}
        if method == "textDocument/references":
            return {"result": [{"uri": "file:///a.py"}, {"uri": "file:///c.py"}]}
        if method == "textDocument/hover":
            return {"result": {"contents": "a function"}}
        return {"result": {}}
    return transport


def test_07_lsp_bridge_consumes_server_semantics():
    client = T.LSPClient(_fake_lsp_transport_factory())
    init = client.initialize("file:///proj")
    assert init["status"] == "supported"
    assert client.definition("file:///a.py", 3, 5)["definition"]
    refs = client.references("file:///a.py", 3, 5)["references"]
    assert len(refs) == 2
    assert client.document_info("file:///a.py", 3, 5)["info"]["contents"] == "a function"


def _fake_dap_transport_factory():
    def transport(req):
        cmd = req.get("command", "")
        bodies = {"setBreakpoints": {"breakpoints": [{"verified": True}]},
                  "stackTrace": {"stackFrames": [{"name": "main"}]},
                  "variables": {"variables": [{"name": "x", "value": "1"}]},
                  "threads": {"threads": [{"id": 1, "name": "main"}]}}
        return {"body": bodies.get(cmd, {})}
    return transport


def test_08_dap_requires_explicit_authorization():
    sess = T.DAPSession(_fake_dap_transport_factory())
    assert sess.set_breakpoints("/tmp/a.py", [10])["status"] == "refused"
    assert sess.stack_trace()["status"] == "refused"
    assert sess.authorize()["status"] == "allowed"
    assert sess.set_breakpoints("/tmp/a.py", [10])["status"] == "supported"
    assert sess.stack_trace()["body"]["stackFrames"][0]["name"] == "main"
    assert sess.variables(1)["body"]["variables"][0]["name"] == "x"
    assert sess.execution_state()["status"] == "supported"


def test_10_local_api_mode_loopback_and_limits():
    mode = T.describe_local_api_mode()
    assert mode["bind"] == "127.0.0.1"
    assert "127.0.0.1" in mode["host_allowlist"]
    ok = T.check_local_api_request("127.0.0.1", ["chat"], ["/work/a.md"],
                                   ["chat"], ["/work/"])
    assert ok["status"] == "allowed"
    assert T.check_local_api_request("203.0.113.9", [], [], [], [])["status"] == "refused"
    assert T.check_local_api_request("127.0.0.1", ["delete_all"], [], [], [])["status"] == "refused"
    assert T.check_local_api_request("127.0.0.1", [], ["/etc/passwd"], [], ["/work/"])["status"] == "refused"


def test_11_shell_completions():
    for shell in ("bash", "powershell", "zsh"):
        out = T.shell_completions(shell, ["agentqon"])
        assert "mission" in out["candidates"]
        script = T.completion_script(shell)
        assert script["status"] == "supported" and script["script"]
    fmt = T.shell_completions("bash", ["agentqon", "mission", "--format"])
    assert fmt["candidates"] == ["json", "text"]
    mids = T.shell_completions("bash", ["agentqon", "mission"], ["m-1"])
    assert "m-1" in mids["candidates"]
    assert T.shell_completions("fish", ["agentqon"])["status"] == "refused"


# --- Group 3 ------------------------------------------------------------
def _cap(**kw):
    return T.detect_backend_capabilities({"name": "test-backend", **kw})


def test_12_conversion_drives_backend_and_records_unrepresentable():
    cap = _cap()
    assert T.convert_model(cap, "safetensors", "gguf", {})["status"] == "unsupported"
    cap2 = _cap(supports_conversion=True,
                conversion_formats=("safetensors", "gguf"))
    out = T.convert_model(cap2, "safetensors", "gguf",
                          {"architecture": "x", "layers": 4, "hidden_size": 8})
    assert out["status"] == "supported"
    assert out["preserved_config"]["architecture"] == "x"
    assert out["unrepresentable"] == ["hidden_size"]


def test_13_quantization_gate_blocks_bad_variant():
    base = {"a": 1.0, "b": 1.0}
    good = T.quantization_gate(base, {"a": 0.98, "b": 0.99}, 1000, 600, 10, 5)
    assert good["recommended"] is True
    bad = T.quantization_gate(base, {"a": 0.5, "b": 0.5}, 1000, 600, 10, 5)
    assert bad["recommended"] is False
    mismatch = T.quantization_gate(base, {"a": 1.0}, 1000, 600, 10, 5)
    assert mismatch["status"] == "refused"


def test_14_adapter_lifecycle_compatibility_and_unsupported():
    base = {"name": "m", "architecture": "arch-a"}
    assert T.check_adapter_compatibility(base, {"name": "ad", "base_architecture": "arch-a"})["status"] == "supported"
    assert T.check_adapter_compatibility(base, {"name": "ad", "base_architecture": "arch-b"})["status"] == "unsupported"
    assert T.adapter_lifecycle(_cap(), "load", "ad")["status"] == "unsupported"
    cap = _cap(supports_adapters=True)
    assert T.adapter_lifecycle(cap, "load", "ad", "2")["status"] == "supported"
    assert T.adapter_lifecycle(cap, "explode", "ad")["status"] == "refused"


def test_15_tokenizer_and_template_conformance():
    def render(messages):
        parts = []
        for m in messages:
            parts.append(f"<{m['role']}>{m['content']}")
        return "".join(parts) + "<stop>"
    cases = [{"name": "c1",
              "messages": [{"role": "user", "content": "hi"}],
              "expected_tokens": ["<user>", "<stop>"]}]
    assert T.test_chat_template(render, cases)["status"] == "supported"
    assert T.test_chat_template(render, [{"name": "c2",
                                         "messages": [{"role": "user", "content": "hi"}],
                                         "expected_tokens": ["<missing>"]}])["status"] == "unsupported"
    assert T.test_tokenizer(lambda s: list(s), lambda t: "".join(t), ["abc"])["status"] == "supported"
    assert T.test_tokenizer(lambda s: [], lambda t: "", ["abc"])["status"] == "unsupported"


def test_16_grammar_constrained_only_where_supported():
    cap = _cap()
    assert T.grammar_constrained_generate(cap, {"type": "object"}, lambda g: "{}")["status"] == "unsupported"
    cap2 = _cap(supports_grammar=True)
    out = T.grammar_constrained_generate(cap2, {"type": "object"}, lambda g: '{"a": 1}')
    assert out["status"] == "supported" and out["output"] == '{"a": 1}'


def test_17_speculative_measurement_gate():
    assert T.speculative_decision(0.8, 1.5)["enabled"] is True
    assert T.speculative_decision(0.2, 1.5)["enabled"] is False
    assert T.speculative_decision(0.9, 0.9)["enabled"] is False


def test_18_batching_is_report_not_engine():
    assert T.batching_support_report(_cap())["status"] == "unsupported"
    out = T.batching_support_report(_cap(supports_batching=True))
    assert out["status"] == "supported"
    assert out["routing_note"] == "batching-aware routing"


def test_19_sharded_validator_requires_everything():
    manifest = {"architecture": "a", "index_present": True, "config_present": True,
                "tokenizer_present": True,
                "shards": [{"name": "s1", "present": True, "architecture": "a", "dtype": "f16"},
                           {"name": "s2", "present": True, "architecture": "a", "dtype": "f16"}]}
    assert T.validate_sharded_model(manifest)["status"] == "supported"
    broken = dict(manifest, shards=[{"name": "s1", "present": False}])
    assert T.validate_sharded_model(broken)["status"] == "unsupported"
    mixed = dict(manifest, shards=[{"name": "s1", "present": True, "dtype": "f16"},
                                   {"name": "s2", "present": True, "dtype": "f32"}])
    assert T.validate_sharded_model(mixed)["status"] == "unsupported"


def test_20_npu_detection_reports_honestly(tmp_path):
    out = T.detect_npu(sysfs_root=str(tmp_path), dev_root=str(tmp_path))
    assert out["status"] == "unsupported" and out["devices"] == []
    assert T.npu_benchmark_report(False)["status"] == "unsupported"


# --- Group 4 ------------------------------------------------------------
def test_21_reranker_measures_quality_and_latency():
    passages = [{"id": "p1", "text": "cats"}, {"id": "p2", "text": "dogs"}]
    out = T.rerank("dogs", passages, lambda q, t: 1.0 if t == "dogs" else 0.0)
    assert out["order"] == ["p2", "p1"]
    measured = T.measure_rerank(["p1", "p2", "p3", "p4"],
                                ["p4", "p1", "p2", "p3"],
                                {"p4": True}, 10.0, 25.0)
    assert measured["quality_baseline"] == 0.0
    assert measured["quality_reranked"] == pytest.approx(1 / 3)
    assert measured["latency_added_ms"] == 15.0
    assert measured["recommended"] is True


def test_22_multimodal_validator_rejects_bad_combination():
    bad = T.validate_multimodal_components({"text_backbone": "t", "vision_encoder": "v"})
    assert bad["status"] == "unsupported" and bad["pipeline_supported"] is False
    ok = T.validate_multimodal_components({"text_backbone": "t", "vision_encoder": "v",
                                           "projector": "p"})
    assert ok["status"] == "supported" and ok["pipeline_supported"] is False


def test_23_push_to_talk_state_machine():
    sess = T.VoiceSession()
    assert sess.status()["recording"] is False
    assert sess.partial("hi")["status"] == "refused"
    sess.start_recording()
    assert sess.partial("hello")["partial"] == "hello"
    assert sess.interrupt()["speaker"] == "user"
    assert sess.hand_over("assistant")["speaker"] == "assistant"
    assert sess.cancel()["cancelled"] is True


def test_24_speech_engine_discovery_reports_none_here():
    out = T.discover_speech_engines(which=lambda name: None)
    assert out["status"] == "unsupported" and out["engines"] == {}
    found = T.discover_speech_engines(which=lambda name: "/usr/bin/espeak" if name == "espeak" else None)
    assert found["status"] == "supported" and "espeak" in found["engines"]


def test_25_alignment_preserves_uncertainty_and_correction():
    segs = [{"start": 0.0, "end": 1.5, "speaker": "A", "text": "hi", "uncertainty": "high"}]
    aligned = T.align_transcript(segs)
    assert aligned["segments"][0]["uncertainty"] == "high"
    corrected = T.correct_diarization(aligned["segments"], {0: "B"})
    assert corrected["segments"][0]["speaker"] == "B"
    assert corrected["segments"][0]["previous_speaker"] == "A"


def test_26_screen_context_explicit_and_masked_only():
    assert T.select_screen_context(None)["status"] == "refused"
    assert T.select_screen_context({})["status"] == "refused"
    ctx = T.select_screen_context({"window": "Notes"})
    assert T.screen_context_for_model(ctx)["status"] == "refused"
    masked = T.apply_masking(ctx, [{"region": "top-right"}])
    assert T.screen_context_for_model(masked)["status"] == "allowed"


# --- Group 5 ------------------------------------------------------------
def test_27_accessibility_tree_platform_gate():
    out = T.accessibility_tree_status()
    assert out["status"] == "unsupported"
    assert "screenshot" in out["message"].lower() or "not available" in out["message"]
    injected = T.inspect_accessibility_tree([{"role": "button", "name": "Save"}])
    assert injected["status"] == "supported" and injected["count"] == 1
    assert T.inspect_accessibility_tree(None)["status"] == "unsupported"


def test_28_target_revalidation_pauses_on_change():
    expected = T.AutomationTarget("Notes", "Save")
    assert T.revalidate_target(expected, T.AutomationTarget("Notes", "Save"))["status"] == "allowed"
    paused = T.revalidate_target(expected, T.AutomationTarget("Browser", "Save"))
    assert paused["status"] == "paused"
    assert T.revalidate_target(expected, None)["status"] == "paused"


def test_29_focus_tracker_detects_dialogs():
    tracker = T.FocusTracker()
    assert tracker.focus_changed("Notes")["status"] == "supported"
    assert tracker.focus_changed("Save?", kind="dialog")["status"] == "paused"
    assert tracker.focus_changed("Popup", kind="unexpected")["status"] == "paused"
    assert tracker.report()["current_owner"] == "Popup"


# --- Group 6 ------------------------------------------------------------
def test_30_redaction_verifier_catches_covered_but_extractable(tmp_path):
    covered = T.verify_redaction("name: Alice secret", {}, ["Alice"])
    assert covered["status"] == "unsupported"
    meta = T.verify_redaction("clean text", {"author": "Alice"}, ["Alice"])
    assert meta["status"] == "unsupported"
    clean = T.verify_redaction("clean text", {"author": "Bob"}, ["Alice"])
    assert clean["status"] == "supported"
    f = tmp_path / "doc.txt"
    f.write_text("clean text")
    assert T.verify_redaction_in_file(str(f), ["Alice"])["status"] == "supported"


def test_31_visual_baseline_compare_highlights_changes():
    mgr = T.VisualBaselineManager()
    mgr.approve("home", [[1, 2], [3, 4]])
    assert mgr.compare("home", [[1, 2], [3, 4]])["status"] == "supported"
    diff = mgr.compare("home", [[1, 9], [3, 4]])
    assert diff["status"] == "unsupported"
    assert diff["changed_regions"] == [{"row": 0, "col": 1, "was": 2, "now": 9}]
    assert mgr.compare("missing", [[1]])["status"] == "refused"


def test_33_debug_evidence_filters_secrets():
    state = {"frames": [{"name": "main", "source": "a.py", "line": 10}],
             "variables": {"token": "sk-abcdefgh12345678", "count": "3"},
             "execution": {"thread": 1}}
    out = T.capture_debug_evidence(state, "m-1")
    assert out["status"] == "supported"
    assert out["artifact"]["filtered"] is True
    assert "sk-abcdefgh" not in str(out["artifact"])
    assert out["artifact"]["frames"][0]["line"] == 10


def test_34_removable_volume_and_disconnect(tmp_path):
    mounts = "/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /media/usb vfat rw 0 0\n"
    sysfs = tmp_path / "sys"
    (sysfs / "block" / "sdb").mkdir(parents=True)
    (sysfs / "block" / "sdb" / "removable").write_text("1\n")
    vol = T.volume_kind_for_path("/media/usb/proj", mounts_text=mounts,
                                 sysfs_root=str(sysfs))
    assert vol["kind"] == "removable"
    warn = T.warn_before_long_operation(vol, 30)
    assert warn["status"] == "warning"
    assert T.warn_before_long_operation({"kind": "fixed"}, 30)["status"] == "supported"
    assert T.classify_disconnect("device removed")["kind"] == "possible_data_loss"
    assert T.classify_disconnect("timeout, retry")["kind"] == "recoverable"


def test_35_contention_inspector_uses_real_telemetry():
    out = T.inspect_processes()
    assert out["status"] == "supported"
    assert isinstance(out["top_cpu"], list)
    assert "unavailable" in out["gpu_memory"] or "available" in out["gpu_memory"]
    assert all("cpu_seconds" in p for p in out["top_cpu"])


def test_36_conformance_suite_gates_routing():
    probe = {"ping": lambda: True, "request_compatibility": lambda: True,
             "output_parsing": lambda: True, "cancellation": lambda: True,
             "error_handling": lambda: True, "feature_matrix": lambda: True}
    suite = T.run_conformance_suite(probe)
    assert suite["core_passed"] is True
    assert T.route_to_backend(suite)["routing"] == "full"
    bad = T.run_conformance_suite({**probe, "ping": lambda: False})
    assert bad["core_passed"] is False
    assert T.route_to_backend(bad)["status"] == "refused"
    restricted = T.run_conformance_suite({**probe, "cancellation": lambda: False})
    assert T.route_to_backend(restricted)["routing"] == "restricted"
    missing = T.run_conformance_suite({})
    assert missing["core_passed"] is False
