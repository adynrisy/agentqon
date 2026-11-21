"""v22 (R1): the agent loop — session-based, tool-using, iterating.

Driven through a scripted stub model over the strict-JSON action
protocol (chat_json), the same path production models without
native function-calling take. Covers: a multi-step task completing,
the step cap stopping a looping model, an approval denial NOT
killing the session, workspace escapes refused (../ and symlink),
shell argv-only semantics, lessons on failure with the right
category, and the budget ceiling stopping a run.
"""
import asyncio
import sys
from pathlib import Path

import pytest

from app import agent_loop, agent_tools, evolve
from app.config import Settings
from app.gateway_client import GatewayError
from app.store import Store

TENANT = "org_agent_test"

UNKNOWN_ENTRY = {"price_status": "unknown", "price_in": None,
                 "price_out": None}


class StubGateway:
    """Scripted chat_json: pops (reply, usage) pairs; a reply may be
    a dict, an Exception to raise, or a callable(prompt) -> dict."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def chat_json(self, model, system, user, temperature=0.4):
        self.prompts.append(user)
        self.systems.append(system)
        if not self.script:
            raise AssertionError("stub model script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(user)
        return item, {"input_tokens": 100, "output_tokens": 50,
                      "model": model}


def _runner(tmp_path, script, entry=None):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path)
    gateway = StubGateway(script)

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model-1", entry or UNKNOWN_ENTRY, \
            {"id": "conn_test", "kind": "direct_provider"}

    return AgentRunner_for_tests(store, settings, factory), store, gateway


def AgentRunner_for_tests(store, settings, factory):
    return agent_loop.AgentRunner(store, settings, factory)


def _tool(tool, args, thought="step"):
    return {"action": "tool", "tool": tool, "args": args,
            "thought": thought}


def _done(summary="finished"):
    return {"action": "done", "summary": summary}


def _run(runner, tenant, session_id):
    return asyncio.run(runner.run(tenant, session_id))


def _events(store, session_id):
    return store.list_events(TENANT, session_id)


def _types(store, session_id):
    return [e["event_type"] for e in _events(store, session_id)]


# ------------------------------------------------------------ happy path
def test_loop_completes_a_scripted_multistep_task(tmp_path):
    ws = tmp_path / "ws"
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": "hello.py",
                           "content": "print('hello from agent')\n"}),
        _tool("shell_run", {"argv": [sys.executable, "hello.py"]}),
        _done("created hello.py and ran it"),
    ])
    session = runner.create_session(TENANT, "tester", "make hello.py",
                                    workspace_root=str(ws),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["summary"] == "created hello.py and ran it"
    assert (ws / "hello.py").read_text() == "print('hello from agent')\n"
    types = _types(store, session["id"])
    assert types.count("ToolCall") == 2 and types.count("ToolResult") == 2
    assert types[-1] == "AgentCompleted"
    # The shell step's stdout must have fed back as an observation.
    results = [e for e in _events(store, session["id"])
               if e["event_type"] == "ToolResult"]
    assert all(r["payload"]["ok"] for r in results)
    # A completed session writes a completion lesson (R2).
    lessons = evolve.list_lessons(store, TENANT)
    assert any(l["outcome"] == "completed"
               and l["source_id"] == session["id"] for l in lessons)


def test_step_cap_stops_a_looping_model(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_list", {"path": "."}) for _ in range(10)])
    session = runner.create_session(TENANT, "tester", "never finish",
                                    workspace_root=str(tmp_path / "ws"),
                                    max_steps=3)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "failed"
    assert done["steps_used"] == 3
    assert done["failure"]["category"] == "planning"
    assert "step cap" in done["failure"]["cause"]
    lessons = evolve.list_lessons(store, TENANT)
    assert any(l["outcome"] == "failed" and l["category"] == "planning"
               for l in lessons)


def test_approval_parks_and_denial_continues_the_session(tmp_path):
    ws = tmp_path / "ws"
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": "secret.txt", "content": "nope"}),
        _done("worked around the denial"),
    ])
    session = runner.create_session(TENANT, "tester", "write a file",
                                    workspace_root=str(ws),
                                    auto_approve_rw=False)
    parked = _run(runner, TENANT, session["id"])
    assert parked["status"] == "awaiting_approval"
    assert parked["pending_action"]["tool"] == "fs_write"
    assert "ApprovalNeeded" in _types(store, session["id"])

    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=False))
    assert done["status"] == "completed"
    assert not (ws / "secret.txt").exists(), \
        "a denied write must never execute"
    # The denial is in the transcript the model saw.
    observations = [t for t in done["transcript"]
                    if t["role"] == "observation"]
    assert any("DENIED" in t["text"] for t in observations)
    # ... and in the lesson ledger as an approval_denied lesson.
    lessons = evolve.list_lessons(store, TENANT)
    assert any(l["category"] == "approval_denied"
               and l["subject"] == "fs_write" for l in lessons)


def test_approval_grant_executes_the_parked_call(tmp_path):
    ws = tmp_path / "ws"
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": "ok.txt", "content": "yes"}),
        # The agent reads its write back before claiming completion:
        # the verify-after-build guard rejects a claim made after a
        # change nothing ever checked (see the guard tests below).
        _tool("fs_read", {"path": "ok.txt"}),
        _done("wrote it"),
    ])
    session = runner.create_session(TENANT, "tester", "write ok.txt",
                                    workspace_root=str(ws))
    parked = _run(runner, TENANT, session["id"])
    assert parked["status"] == "awaiting_approval"
    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=True))
    assert done["status"] == "completed"
    assert (ws / "ok.txt").read_text() == "yes"


# ------------------------------------------------------------- confinement
def test_workspace_escape_via_dotdot_is_refused(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret")
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_read", {"path": "../outside.txt"}),
        # The refusal is the last tool result (ok:false), so the
        # completion guard pushes the claim back (see the completion
        # guard tests below); the model restates its justification
        # and insists — the session ends claimed-but-UNVERIFIED.
        _done("could not read it, finished anyway"),
        _done("could not read it: the file is outside the workspace"),
        _done("could not read it: the file is outside the workspace"),
    ])
    ws = tmp_path / "ws"
    session = runner.create_session(TENANT, "tester", "read a file",
                                    workspace_root=str(ws))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"  # refusal is an observation
    assert done["completion"]["verified"] is False, \
        "a goal whose only tool result was a refusal is never 'verified'"
    results = [e for e in _events(store, session["id"])
               if e["event_type"] == "ToolResult"]
    assert results and results[0]["payload"]["ok"] is False
    assert "escapes" in results[0]["payload"]["summary"]
    transcript = " ".join(t.get("text", "") for t in done["transcript"])
    assert "top secret" not in transcript


def test_workspace_escape_via_symlink_is_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    secret_dir = tmp_path / "secret-dir"
    secret_dir.mkdir()
    (secret_dir / "loot.txt").write_text("loot")
    (ws / "link").symlink_to(secret_dir, target_is_directory=True)
    result = asyncio.run(agent_tools.execute_tool(
        "fs_read", {"path": "link/loot.txt"}, root=ws))
    assert result["ok"] is False
    assert result.get("refused") == "workspace_escape"
    # A write through the symlink is refused too.
    result = asyncio.run(agent_tools.execute_tool(
        "fs_write", {"path": "link/new.txt", "content": "x"}, root=ws))
    assert result["ok"] is False
    assert not (secret_dir / "new.txt").exists()


# ------------------------------------------------------------------ shell
def test_shell_metacharacters_in_a_string_are_inert(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # Passed as ONE string: it is a single executable name, never a
    # shell line — the ';' never separates commands.
    result = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"command": "echo pwned; echo second"}, root=ws))
    assert result["ok"] is False
    assert "executable not found" in result["error"]
    # And as an argv element it is literal data to echo.
    result = asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": ["echo", "a; rm -rf /"]}, root=ws))
    assert result["ok"] is True
    assert result["stdout"].strip() == "a; rm -rf /"


def test_file_tools_roundtrip_and_search(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    run = lambda tool, args: asyncio.run(  # noqa: E731
        agent_tools.execute_tool(tool, args, root=ws))
    assert run("fs_write", {"path": "a/b.txt",
                            "content": "alpha beta\nbeta needle\ngamma"})["ok"]
    read = run("fs_read", {"path": "a/b.txt"})
    assert read["ok"] and "needle" in read["content"]
    edited = run("fs_edit", {"path": "a/b.txt", "old": "beta needle",
                             "new": "beta thread"})
    assert edited["ok"]
    assert run("fs_edit", {"path": "a/b.txt", "old": "beta",
                           "new": "x"})["ok"] is False  # 2 occurrences
    found = run("fs_search", {"query": "thread"})
    assert found["ok"] and found["count"] == 1
    assert found["matches"][0]["path"] == "a/b.txt"
    listing = run("fs_list", {"path": "a"})
    assert listing["ok"] and listing["entries"][0]["name"] == "b.txt"


def test_shell_classification():
    from app.agent_tools import classify_shell
    assert classify_shell(["ls", "-la"]) == "read_only"
    assert classify_shell(["git", "status"]) == "read_only"
    assert classify_shell(["git", "push"]) == "network"
    assert classify_shell(["curl", "https://x.invalid"]) == "network"
    assert classify_shell(["python3", "build.py"]) == "mutating"
    assert classify_shell(["some-unknown-tool"]) == "mutating"


# ---------------------------------------------------------------- failures
def test_provider_failure_writes_a_classified_lesson(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        GatewayError("rate_limited", "gateway returned HTTP 429 "
                                     "(after 8 attempts)", status=429)])
    session = runner.create_session(TENANT, "tester", "do work",
                                    workspace_root=str(tmp_path / "ws"))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "failed"
    assert done["failure"]["category"] == "provider_rate_limit"
    lessons = evolve.list_lessons(store, TENANT)
    assert lessons and lessons[0]["category"] == "provider_rate_limit"
    assert lessons[0]["subject"] == "stub-model-1"
    assert lessons[0]["evidence_id"] == session["id"]


def test_budget_ceiling_stops_the_run_before_spending(tmp_path):
    pricey = {"price_status": "known", "price_in": 1_000_000.0,
              "price_out": 1_000_000.0}
    runner, store, gateway = _runner(tmp_path, [_done("never reached")],
                                     entry=pricey)
    session = runner.create_session(TENANT, "tester", "cheap goal",
                                    workspace_root=str(tmp_path / "ws"),
                                    budget_cap_usd=1.0)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "failed"
    assert "budget ceiling" in done["failure"]["cause"]
    assert gateway.prompts == [], "no model call may happen past the cap"


def test_unusable_replies_get_a_correction_then_recover(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        {"unexpected": "shape"},          # → format correction
        {"tool": "fs_list", "args": {"path": "."}},  # alias shape
        {"answer": "all done here"},       # content-key completion
    ])
    session = runner.create_session(TENANT, "tester", "look around",
                                    workspace_root=str(tmp_path / "ws"))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["summary"] == "all done here"
    observations = [t for t in done["transcript"]
                    if t["role"] == "observation"]
    assert any("not a usable action" in t["text"] for t in observations)


def test_persistent_garbage_fails_as_output_format(tmp_path):
    runner, store, _ = _runner(tmp_path, [{"x": 1}] * 6)
    session = runner.create_session(TENANT, "tester", "anything",
                                    workspace_root=str(tmp_path / "ws"))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "failed"
    assert done["failure"]["category"] == "output_format"


def test_cancel_marks_the_session_and_writes_a_lesson(tmp_path):
    runner, store, _ = _runner(tmp_path, [_done("x")])
    session = runner.create_session(TENANT, "tester", "goal",
                                    workspace_root=str(tmp_path / "ws"))
    cancelled = runner.cancel_session(TENANT, session["id"])
    assert cancelled["status"] == "cancelled"
    lessons = evolve.list_lessons(store, TENANT)
    assert any(l["outcome"] == "cancelled" for l in lessons)
    # A cancelled session does not run.
    after = _run(runner, TENANT, session["id"])
    assert after["status"] == "cancelled"


# ------------------------------------------------------- completion guard
def _fail_tool():
    return _tool("shell_run",
                 {"argv": [sys.executable, "-c", "raise SystemExit(1)"]})


def _ok_tool():
    return _tool("shell_run",
                 {"argv": [sys.executable, "-c", "print('fixed')"]})


def _terminal_event(store, session_id):
    events = [e for e in _events(store, session_id)
              if e["event_type"] == "AgentCompleted"]
    assert events, "an AgentCompleted event must exist"
    return events[-1]


def _session_lessons(store, session_id):
    return [l for l in evolve.list_lessons(store, TENANT)
            if l["source_id"] == session_id]


def test_completion_claim_after_failure_is_pushed_back_then_verified(
        tmp_path):
    runner, store, gateway = _runner(tmp_path, [
        _fail_tool(),                          # last tool result: failure
        _done("everything works"),            # claim → REJECTED
        _ok_tool(),                            # genuine fix, verified ok
        _done("fixed the failure and verified it"),
    ])
    session = runner.create_session(TENANT, "tester", "run the check",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["summary"] == "fixed the failure and verified it"
    assert done["completion_rejections"] == 1
    assert done["completion"]["claimed_complete"] is True
    assert done["completion"]["verified"] is True
    # The pushback quoted the failed tool back to the model.
    rejected = [t for t in done["transcript"]
                if t.get("completion_rejected")]
    assert len(rejected) == 1
    assert "COMPLETION REJECTED" in rejected[0]["text"]
    assert "shell_run" in rejected[0]["text"]
    assert "COMPLETION REJECTED" in gateway.prompts[2], \
        "the rejection must reach the model in its next prompt"
    # Terminal event carries the verification flags.
    payload = _terminal_event(store, session["id"])["payload"]
    assert payload["claimed_complete"] is True
    assert payload["verified"] is True
    # The lesson reflects the verified outcome — a clean completion.
    lessons = _session_lessons(store, session["id"])
    assert any(l["outcome"] == "completed" for l in lessons)
    assert not any(l["outcome"] == "failed" for l in lessons)


def test_failure_then_success_then_first_claim_is_accepted(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        _fail_tool(), _ok_tool(), _done("failed, fixed, verified"),
    ])
    session = runner.create_session(TENANT, "tester", "run the check",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["completion_rejections"] == 0
    assert done["completion"]["verified"] is True


def test_insistent_claims_over_failure_end_unverified(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        _fail_tool(),
        _done("the tests passed, trust me"),   # rejection 1
        _done("they really passed"),           # rejection 2
        _done("definitely passed"),            # 3rd claim → ends, unverified
    ])
    session = runner.create_session(TENANT, "tester", "run the check",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"       # the CLAIM ended the session
    assert done["summary"] == "definitely passed"
    assert done["completion"]["claimed_complete"] is True
    assert done["completion"]["verified"] is False
    assert done["completion"]["unresolved_failure"]["tool"] == "shell_run"
    assert done["completion_rejections"] == 2
    rejected = [t for t in done["transcript"]
                if t.get("completion_rejected")]
    assert len(rejected) == 2
    payload = _terminal_event(store, session["id"])["payload"]
    assert payload["claimed_complete"] is True
    assert payload["verified"] is False
    assert payload["unresolved_failure"]["tool"] == "shell_run"
    # The lesson must NOT repeat the false success claim: it records
    # the unresolved failure instead.
    lessons = _session_lessons(store, session["id"])
    assert not any(l["outcome"] == "completed" for l in lessons), \
        "an unverified completion must not write a success lesson"
    failed = [l for l in lessons if l["outcome"] == "failed"]
    assert failed and failed[0]["category"] == "tool_error"
    assert "claimed completion" in failed[0]["cause"]
    assert "shell_run" in failed[0]["cause"]


# ------------------------------------------------- verify-after-build
def test_write_then_claim_is_pushed_back_until_the_work_is_checked(
        tmp_path):
    ws = tmp_path / "ws"
    test_src = (
        "import subprocess, sys, unittest\n"
        "class TestHello(unittest.TestCase):\n"
        "    def test_output(self):\n"
        "        out = subprocess.run([sys.executable, 'hello.py'],\n"
        "                             capture_output=True, text=True)\n"
        "        self.assertEqual(out.stdout.strip(), 'hello agentqon')\n"
        "unittest.main()\n")
    runner, store, gateway = _runner(tmp_path, [
        _tool("fs_write", {"path": "hello.py",
                           "content": "print('hello agentqon')\n"}),
        _done("created hello.py"),            # claim → REJECTED (unchecked)
        _tool("fs_write", {"path": "test_hello.py", "content": test_src}),
        _tool("shell_run", {"argv": [sys.executable, "test_hello.py"]}),
        _done("created both files and ran the test — it passed"),
    ])
    session = runner.create_session(TENANT, "tester", "make hello + test",
                                    workspace_root=str(ws),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["summary"] == "created both files and ran the test — it passed"
    assert done["completion_rejections"] == 1
    assert done["completion"]["claimed_complete"] is True
    assert done["completion"]["verified"] is True
    # The pushback named the missing verification, and it reached the
    # model in its next prompt.
    rejected = [t for t in done["transcript"]
                if t.get("completion_rejected")]
    assert len(rejected) == 1
    assert "COMPLETION REJECTED" in rejected[0]["text"]
    assert "never verified" in rejected[0]["text"]
    assert "COMPLETION REJECTED" in gateway.prompts[2]
    payload = _terminal_event(store, session["id"])["payload"]
    assert payload["verified"] is True
    lessons = _session_lessons(store, session["id"])
    assert any(l["outcome"] == "completed" for l in lessons)
    assert not any(l["outcome"] == "failed" for l in lessons)


def test_unchecked_changes_end_unverified_when_the_model_insists(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": "draft.txt", "content": "draft"}),
        _done("wrote the draft"),         # rejection 1
        _done("it is definitely fine"),   # rejection 2
        _done("trust me, done"),          # 3rd claim → ends, UNVERIFIED
    ])
    session = runner.create_session(TENANT, "tester", "write a draft",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"       # the CLAIM ended the session
    assert done["summary"] == "trust me, done"
    assert done["completion"]["claimed_complete"] is True
    assert done["completion"]["verified"] is False
    assert done["completion"]["unresolved_failure"]["tool"] == "verification"
    assert "no verification step ran" in \
        done["completion"]["unresolved_failure"]["error"]
    assert done["completion_rejections"] == 2
    rejected = [t for t in done["transcript"]
                if t.get("completion_rejected")]
    assert len(rejected) == 2
    payload = _terminal_event(store, session["id"])["payload"]
    assert payload["claimed_complete"] is True
    assert payload["verified"] is False
    assert payload["unresolved_failure"]["tool"] == "verification"
    # The lesson records the planning failure — never the claim.
    lessons = _session_lessons(store, session["id"])
    assert not any(l["outcome"] == "completed" for l in lessons), \
        "an unverified completion must not write a success lesson"
    failed = [l for l in lessons if l["outcome"] == "failed"]
    assert failed and failed[0]["category"] == "planning"
    assert "never verified" in failed[0]["cause"]


def test_read_only_session_completes_on_the_first_claim(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "note.txt").write_text("already here")
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_read", {"path": "note.txt"}),
        _done("read the note; nothing to change"),
    ])
    session = runner.create_session(TENANT, "tester", "read the note",
                                    workspace_root=str(ws))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["completion_rejections"] == 0
    assert done["completion"]["verified"] is True


def test_write_then_read_back_then_claim_is_accepted_immediately(tmp_path):
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": "a.txt", "content": "alpha"}),
        _tool("fs_read", {"path": "a.txt"}),
        _done("wrote a.txt and read it back"),
    ])
    session = runner.create_session(TENANT, "tester", "write and check",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["completion_rejections"] == 0
    assert done["completion"]["verified"] is True
    rejected = [t for t in done["transcript"]
                if t.get("completion_rejected")]
    assert rejected == []


def test_no_model_connected_is_honest_and_recoverable(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path)

    def factory(tenant_id, on_rate_limited=None):
        raise agent_loop.ModelNotConnected("No model is connected")

    runner = agent_loop.AgentRunner(store, settings, factory)
    session = runner.create_session(TENANT, "tester", "goal",
                                    workspace_root=str(tmp_path / "ws"))
    with pytest.raises(agent_loop.ModelNotConnected):
        _run(runner, TENANT, session["id"])
    fresh = runner.get_session(TENANT, session["id"])
    assert fresh["status"] == "created"  # not stuck in "running"


# ------------------------------------------------- environment facts
def test_environment_facts_derive_the_python_interpreter(monkeypatch):
    """The interpreter fact is DERIVED from shutil.which, never
    hardcoded — each host shape states its own truth plainly."""
    def fake_which(mapping):
        return lambda name: mapping.get(name)

    monkeypatch.setattr(agent_loop.shutil, "which",
                        fake_which({"python3": "/usr/bin/python3"}))
    facts = agent_loop.environment_facts("/ws/root")
    assert "python is available as: python3 (/usr/bin/python3)" in facts
    assert "no 'python' command" in facts
    assert "/ws/root" in facts
    assert "non-interactive argv execution" in facts
    assert "NO shell" in facts

    monkeypatch.setattr(agent_loop.shutil, "which",
                        fake_which({"python": "/usr/local/bin/python"}))
    facts = agent_loop.environment_facts("/ws/root")
    assert "python is available as: python (/usr/local/bin/python)" in facts
    assert "no 'python3' command" in facts

    monkeypatch.setattr(agent_loop.shutil, "which", fake_which(
        {"python3": "/usr/bin/python3", "python": "/usr/bin/python"}))
    facts = agent_loop.environment_facts("/ws/root")
    assert "python3 (/usr/bin/python3)" in facts
    assert "python (/usr/bin/python)" in facts
    assert "no 'python" not in facts

    monkeypatch.setattr(agent_loop.shutil, "which", fake_which({}))
    facts = agent_loop.environment_facts("/ws/root")
    assert "no python interpreter" in facts


def test_session_system_prompt_carries_the_environment_facts(tmp_path):
    """A live session's system context states the host truth: this
    machine's derived interpreter fact (the exact gap the first live
    rounds exposed — the model guessed `python` on a python3-only
    host), the OS, and the workspace the session runs in."""
    import shutil as _shutil

    runner, store, gateway = _runner(tmp_path, [_done("nothing to do")])
    ws = tmp_path / "ws"
    session = runner.create_session(TENANT, "tester", "look around",
                                    workspace_root=str(ws))
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert gateway.systems, "the stub gateway saw no system prompt"
    system = gateway.systems[0]
    assert system.startswith(agent_loop.AGENT_SYSTEM_PROMPT)
    assert "Environment (host facts" in system
    assert str(ws) in system
    py3, py = _shutil.which("python3"), _shutil.which("python")
    if py3 and not py:
        assert f"python is available as: python3 ({py3})" in system
        assert "no 'python' command" in system
    elif py3 and py:
        assert f"python3 ({py3})" in system and f"python ({py})" in system
    elif py:
        assert f"python is available as: python ({py})" in system
    else:
        assert "no python interpreter" in system
    assert "non-interactive argv execution" in system


# ------------------------------------------------------------ B4: restart orphans
def _orphan(store, session_id, *, token=None, fresh=True):
    record = store.get_agent_session(TENANT, session_id)
    record["status"] = "running"
    record["updated_at"] = agent_loop.now_iso() if fresh else record["updated_at"]
    if token is None:
        record.pop("driver_token", None)
    else:
        record["driver_token"] = token
    store.put_agent_session(TENANT, record)


def test_restart_orphan_running_session_is_redrivable(tmp_path):
    """B4: a session left 'running' by a restart minutes ago — fresh
    save, no driver token, no live driver — re-drives from its
    recorded transcript instead of returning the stuck record."""
    runner, store, gateway = _runner(tmp_path, [_done("recovered")])
    session = runner.create_session(TENANT, "tester", "orphan",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    record = store.get_agent_session(TENANT, session["id"])
    record["transcript"] = [{"role": "user",
                             "text": "PRIOR-WORK-MARKER make hello.py"}]
    record["steps_used"] = 2
    store.put_agent_session(TENANT, record)
    _orphan(store, session["id"])
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    assert done["steps_used"] >= 2  # steps are not reset
    assert "PRIOR-WORK-MARKER" in gateway.prompts[0]  # resumes, not restarts
    assert "AgentSessionRecovered" in _types(store, session["id"])
    reason = [e for e in _events(store, session["id"])
              if e["event_type"] == "AgentSessionRecovered"][0]["payload"]["reason"]
    assert "run it again" in reason and "did not finish" in reason


def test_live_drive_in_this_process_is_not_double_driven(tmp_path):
    """B4 guard (Audit F5 preserved): a 'running' session owned by
    THIS process with a fresh save is a live drive — a second run()
    still returns it untouched and never calls the model."""
    runner, store, gateway = _runner(tmp_path, [_done()])
    session = runner.create_session(TENANT, "tester", "live",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    _orphan(store, session["id"], token=agent_loop._PROCESS_TOKEN)
    out = _run(runner, TENANT, session["id"])
    assert out["status"] == "running"
    assert gateway.prompts == []


def test_crashed_drive_resets_to_redrivable_with_progress_kept(tmp_path):
    """B4: a drive that dies abnormally mid-run (here: the store
    blows up on the second committed post-step save) leaves the
    session re-drivable with its committed progress intact — and
    the re-drive then completes."""
    runner, store, gateway = _runner(tmp_path, [
        _tool("fs_list", {"path": "."}),
        _tool("fs_list", {"path": "."}),
        _done("after crash"),
    ])
    session = runner.create_session(TENANT, "tester", "crash",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)
    orig_put = store.put_agent_session
    state = {"saves": 0, "fired": False}

    def flaky_put(tenant_id, data):
        if (data.get("steps_used", 0) >= 1 and any(
                t.get("role") == "observation"
                for t in data.get("transcript", []))):
            state["saves"] += 1
            if state["saves"] == 2 and not state["fired"]:
                state["fired"] = True
                raise RuntimeError("simulated store blowup mid-drive")
        return orig_put(tenant_id, data)

    store.put_agent_session = flaky_put
    try:
        with pytest.raises(RuntimeError):
            _run(runner, TENANT, session["id"])
    finally:
        store.put_agent_session = orig_put
    assert state["fired"]
    mid = runner.get_session(TENANT, session["id"])
    assert mid["status"] == "created"  # not stuck in "running"
    assert mid["steps_used"] >= 1
    assert any(t.get("role") == "observation"
               for t in mid["transcript"])  # committed progress kept
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
