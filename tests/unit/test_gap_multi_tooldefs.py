"""W5 gap-fix: tool definitions (expansion C) + tool executor (§3)."""
import asyncio
import os
from pathlib import Path

import pytest

from app import agent_tools
from app.tooldefs import (TOOL_DEFS, CancelHandle, filter_env,
                          is_retry_safe, normalize_arg_keys,
                          normalize_error, receipt, redact_args,
                          resource_limits_preexec, temp_workspace,
                          timed_run, truncate_text, validate_args,
                          validate_output, verdict_from_exit)


def test_every_session_tool_has_full_metadata():
    for name in agent_tools.TOOLS:
        d = TOOL_DEFS[name]
        assert d.tool_id and d.name and d.description
        assert d.args and d.timeout_s > 0 and d.version
        assert d.side_effect in ("none", "read", "write", "external")
        assert d.output_fields
    # Side-effect warnings exist exactly where they matter.
    assert TOOL_DEFS["fs_read"].side_effect_warning() == ""
    assert "changes files" in TOOL_DEFS["fs_write"].side_effect_warning()
    assert "outside this workspace" in TOOL_DEFS["shell_run"].side_effect_warning()


def test_unsafe_default_rejected_at_registry_build():
    from app.tooldefs import ToolDef, ArgSpec, _check_safe_defaults
    with pytest.raises(ValueError):
        _check_safe_defaults(ToolDef(
            tool_id="tool.x", name="fs_write", description="x",
            args={"content": ArgSpec("string", required=False, default="hi",
                                     has_default=True)}))


def test_argument_key_normalization_and_unknown_field_detection():
    clean = normalize_arg_keys("fs_read", {"File": "a.txt", " PATH ": "b.txt"})
    # Explicit canonical spelling wins over the alias, either order.
    assert clean["path"] == "b.txt"
    clean2 = normalize_arg_keys("fs_read", {"path": "b.txt", "file": "a.txt"})
    assert clean2["path"] == "b.txt"
    _, errors = validate_args("fs_read", {"path": "a.txt", "bogus": 1})
    assert any("unknown argument" in e for e in errors)
    _, errors = validate_args("fs_read", {})
    assert any("missing required" in e for e in errors)


def test_coercion_policy_documented_behaviours():
    # "false" as a string is NEVER coerced to bool.
    _, errors = validate_args("shell_run", {"argv": ["echo"],
                                            "timeout_s": "not-a-number"})
    assert errors
    clean, errors = validate_args("shell_run", {"argv": ["echo"],
                                                "timeout_s": "30"})
    assert errors == [] and clean["timeout_s"] == 30.0
    clean, errors = validate_args("shell_run", {"argv": ["echo"],
                                                "timeout_s": 30.5})
    assert errors == [] and clean["timeout_s"] == 30.5
    # Float 30.5 for an integer field is rejected, never cut down.
    from app.tooldefs import coerce_value
    assert coerce_value(30.5, "integer")[1] is not None
    assert coerce_value(3.0, "integer") == (3, None)
    assert coerce_value("false", "boolean")[1] is not None
    assert coerce_value("x", "list")[1] is not None


def test_enum_and_range_validation():
    # timeout_s range: 1..300 (declared on shell_run).
    _, errors = validate_args("shell_run", {"argv": ["echo"],
                                            "timeout_s": 9999})
    assert any("at most 300" in e for e in errors)
    _, errors = validate_args("fs_search", {"query": ""})
    assert any("must not be empty" in e for e in errors)


def test_truncation_marker_says_it_is_partial():
    shown, truncated, marker = truncate_text("x" * 100, 10)
    assert truncated is True
    assert "cut short" in marker and "100" in marker
    assert marker in shown
    shown, truncated, marker = truncate_text("short", 10)
    assert truncated is False and marker == ""


def test_exit_code_is_the_verdict_not_text():
    assert verdict_from_exit(0) == "success"
    assert verdict_from_exit(1) == "failure"
    assert verdict_from_exit(None) == "failure"


def test_idempotency_metadata():
    assert is_retry_safe("fs_read") is True
    assert is_retry_safe("fs_write") is False
    assert is_retry_safe("shell_run") is False


def test_output_schema_validation_and_empty_search_success():
    assert validate_output("fs_search", {"ok": True, "matches": [],
                                         "count": 0}) == []
    assert validate_output("fs_search", {"ok": "yes"}) != []


def test_env_filtering_drops_secrets_keeps_path():
    env = filter_env({"PATH": "/usr/bin", "MY_API_KEY": "secret",
                      "MY_TOKEN": "secret", "LANG": "C.UTF-8",
                      "AGENTQON_VAULT_KEY": "secret"})
    assert env["PATH"] == "/usr/bin" and env["LANG"] == "C.UTF-8"
    assert "MY_API_KEY" not in env and "MY_TOKEN" not in env
    assert "AGENTQON_VAULT_KEY" not in env


def test_receipt_redacts_secrets_and_records_outcome():
    r = receipt("fs_read", {"path": "a.txt", "api_key": "secret"},
                "success", duration_ms=12.5, workspace="/tmp/ws")
    assert r["arguments"]["api_key"] == "[hidden]"
    assert r["outcome"] == "success" and r["duration_ms"] == 12.5
    with pytest.raises(ValueError):
        receipt("fs_read", {}, "weird-outcome")


def test_resource_limits_preexec_available_on_posix():
    fn = resource_limits_preexec()
    assert fn is None or callable(fn)


def test_temp_workspace_cleaned_up():
    with temp_workspace() as tmp:
        (tmp / "x.txt").write_text("hi")
        assert (tmp / "x.txt").exists()
    assert not tmp.exists()


def test_cancel_handle_propagates():
    handle = CancelHandle()
    killed = []
    handle.register(lambda: killed.append(True))
    handle.cancel()
    assert handle.is_cancelled() and killed == [True]


def test_error_normalization_keeps_envelope_shape():
    err = normalize_error("TIMEOUT", "the tool took too long", "corr-1",
                          retryable=True)
    assert err["error"]["code"] == "TIMEOUT"
    assert err["error"]["retryable"] is True


def test_shell_timeout_is_timeout_not_failure(tmp_path):
    out = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": ["sleep", "5"], "timeout_s": 1},
        root=tmp_path))
    assert out["ok"] is False
    assert out["outcome"] == "timeout"
    assert out["timed_out"] is True


def test_shell_nonzero_exit_with_success_text_is_failure(tmp_path):
    out = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": ["sh", "-c", "echo success; exit 3"]},
        root=tmp_path))
    assert out["ok"] is False
    assert out["outcome"] == "failure"
    assert out["returncode"] == 3
    assert "success" in out["stdout"]  # text never overrides the code


def test_shell_stdout_stderr_separate_and_marked(tmp_path):
    out = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": ["sh", "-c", "echo out; echo err >&2"]},
        root=tmp_path))
    assert out["stdout"].strip() == "out"
    assert out["stderr"].strip() == "err"
    assert "duration_ms" in out


def test_shell_output_truncation_marked(tmp_path):
    out = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": ["sh", "-c", "yes x | head -c 20000"]},
        root=tmp_path))
    assert out["stdout_truncated"] is True
    assert "cut short" in out["note"]


def test_fs_read_truncation_note_and_empty_search_success(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("y" * 30000)
    out = asyncio.run(agent_tools.execute_tool(
        "fs_read", {"path": "big.txt"}, root=tmp_path))
    assert out["truncated"] is True and "cut short" in out["note"]
    out = asyncio.run(agent_tools.execute_tool(
        "fs_search", {"query": "zzz-no-match-zzz"}, root=tmp_path))
    assert out["ok"] is True and out["count"] == 0  # success, zero results


def test_shell_env_does_not_leak_secrets(tmp_path):
    # The executor passes a FILTERED environment (see filter_env):
    # a child process printing a planted secret-named variable must
    # see nothing, while PATH survives.
    script = ("import os; print('LEAKED' if os.environ.get('GAP_TEST_SECRET') "
              "else 'CLEAN'); print('PATH' in os.environ)")
    env_before = dict(os.environ)
    os.environ["GAP_TEST_SECRET"] = "should-not-leak"
    try:
        out = asyncio.run(agent_tools.execute_tool(
            "shell_run", {"argv": ["python3", "-c", script]}, root=tmp_path))
    finally:
        os.environ.clear()
        os.environ.update(env_before)
    assert "CLEAN" in out["stdout"]
    assert "True" in out["stdout"]
