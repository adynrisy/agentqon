"""W17 thirteenth document: integration tests (boundary crossings).

- Item 9: a real PTY terminal session (prompts, input, resize, recovery).
- Item 32: git bisection on a real fixture repo, with dirty-tree protection.
- Item 6 (server direction): the expanded MCP server over real stdio.
- Item 36: conformance suite gating a routing decision end to end.
No network, no live backend.
"""
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import thirteenth as T

REPL = ("import sys\n"
        "print('ready')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if line == 'quit':\n"
        "        break\n"
        "    print('got:' + line)\n"
        "    sys.stdout.flush()\n")


def _wait_for(session, needle, timeout=5.0):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        buf += session.read_available(timeout=0.5)
        if needle in buf:
            return buf
    return buf


def test_09_interactive_terminal_session_prompts_input_resize_recovery():
    session = T.TerminalSession([sys.executable, "-u", "-c", REPL])
    try:
        buf = _wait_for(session, "ready")
        assert "ready" in buf
        assert session.alive() is True
        session.send("hello\n")
        buf = _wait_for(session, "got:hello")
        assert "got:hello" in buf
        resized = session.resize(120, 40)
        assert resized["status"] == "supported"
        assert resized["cols"] == 120
        session.send("quit\n")
        time.sleep(0.5)
        assert session.alive() is False
        recovered = session.recover()
        assert recovered["recovered"] is True
        assert session.alive() is True
        buf = _wait_for(session, "ready")
        assert "ready" in buf
    finally:
        session.close()
    assert session.alive() is False


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True,
                   capture_output=True, text=True)


def test_32_git_bisection_finds_bad_commit_and_restores_dirty_tree(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    check = tmp_path / "repo" / "check.py"
    check.write_text("import sys; sys.exit(0)\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "good start")
    good = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    check.write_text("import sys; sys.exit(1)\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "breaks the check")
    bad_subject_commit = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    # Dirty uncommitted work that must survive the bisection.
    dirty = tmp_path / "repo" / "notes.txt"
    dirty.write_text("do not lose me\n")
    out = T.bisect_regression(repo, [sys.executable, "check.py"], good)
    assert out["status"] == "supported"
    assert out["first_bad"] == bad_subject_commit
    assert out["subject"] == "breaks the check"
    assert dirty.read_text() == "do not lose me\n"
    head = subprocess.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == "main"


def test_06_mcp_server_stdio_lists_two_tools_and_answers_limits():
    server_py = os.path.join(os.path.dirname(__file__), "..", "..",
                             "cli", "mcp", "agentqon_brain_mcp.py")
    proc = subprocess.Popen([sys.executable, server_py],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1,
                                     "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        tools = json.loads(line)["result"]["tools"]
        names = [t["name"] for t in tools]
        assert names == ["ask_agentqon", "agentqon_limits"]
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "agentqon_limits", "arguments": {}}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        text = json.loads(line)["result"]["content"][0]["text"]
        assert "cannot" in text
        assert json.loads(line)["result"]["isError"] is False
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def test_36_conformance_routing_end_to_end():
    probe = {"ping": lambda: True, "request_compatibility": lambda: True,
             "output_parsing": lambda: True, "cancellation": lambda: False,
             "error_handling": lambda: True, "feature_matrix": lambda: True}
    suite = T.run_conformance_suite(probe)
    route = T.route_to_backend(suite)
    assert route["routing"] == "restricted"
    assert suite["results"]["ping"]["passed"] is True
    assert suite["results"]["cancellation"]["passed"] is False
