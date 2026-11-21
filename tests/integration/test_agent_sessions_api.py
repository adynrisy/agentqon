"""v22 (R1/R2): agent sessions over the API — create, run, approval
park/resume (approve AND deny), events, cancel, and the evolve
report. The model is a scripted OpenAI-compatible stub reached
through a real BYOK connection, so the genuine gateway path (with
its 429 retry/pacing) carries every call.
"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from tests.conftest import hdr

client = TestClient(main_mod.app)
KEY = "agent-test-key"

WRITE = {"action": "tool", "tool": "fs_write",
         "args": {"path": "note.txt", "content": "written by the agent"},
         "thought": "create the note"}
DONE = {"action": "done", "summary": "note handled"}


def _start_agent_stub():
    counters: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth_ok(self):
            return self.headers.get("Authorization") == f"Bearer {KEY}"

        def do_GET(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            if self.path.endswith("/models"):
                return self._send(200, {"data": [
                    {"id": "stub-model-1", "owned_by": "stub"}]})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = payload.get("messages", [])
            user = messages[1]["content"] if len(messages) > 1 else ""
            m = re.search(r"GOAL: (.+)", user)
            goal = m.group(1).strip() if m else "?"
            step = counters.get(goal, 0)
            counters[goal] = step + 1
            script = [WRITE, DONE]
            content = script[min(step, len(script) - 1)]
            return self._send(200, {
                "id": "chatcmpl-agent-stub", "model": "stub-model-1",
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


@pytest.fixture(scope="module")
def stub():
    server, base = _start_agent_stub()
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def _connect(h, stub):
    r = client.post("/v1/connections", headers=h, json={
        "kind": "direct_provider", "label": "Agent stub",
        "base_url": stub, "api_key": KEY})
    assert r.status_code == 201, r.text
    conn = r.json()
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=h)
    assert v.status_code == 200 and v.json()["state"] == "active", v.text


def _create_session(h, tmp_path, goal):
    r = client.post("/v1/agent-sessions", headers=h, json={
        "goal": goal, "workspace_root": str(tmp_path / "ws"),
        "auto_approve_rw": False, "max_steps": 10})
    assert r.status_code == 201, r.text
    return r.json()["session"]


def test_session_parks_on_approval_and_approve_completes(stub, tenant,
                                                         tmp_path):
    h = hdr(tenant)
    _connect(h, stub)
    session = _create_session(h, tmp_path, "write the note file")
    sid = session["id"]
    assert session["status"] == "created"

    run = client.post(f"/v1/agent-sessions/{sid}/run", headers=h, json={})
    assert run.status_code == 200, run.text
    parked = run.json()["session"]
    assert parked["status"] == "awaiting_approval"
    assert parked["pending_action"]["tool"] == "fs_write"
    assert not (tmp_path / "ws" / "note.txt").exists()

    decided = client.post(f"/v1/agent-sessions/{sid}/approve", headers=h,
                          json={"decision": "approve"})
    assert decided.status_code == 200, decided.text
    done = decided.json()["session"]
    assert done["status"] == "completed"
    assert (tmp_path / "ws" / "note.txt").read_text() == \
        "written by the agent"

    events = client.get(f"/v1/agent-sessions/{sid}/events", headers=h)
    assert events.status_code == 200
    types = [e["event_type"] for e in events.json()["events"]]
    for expected in ("AgentSessionCreated", "ToolCall", "ApprovalNeeded",
                     "ApprovalResolved", "ToolResult", "AgentCompleted"):
        assert expected in types, types


def test_session_denial_skips_the_write_and_still_completes(stub, tenant,
                                                          tmp_path):
    h = hdr(tenant)
    _connect(h, stub)
    # A distinct goal: the stub's script counters key on the goal.
    session = _create_session(h, tmp_path, "write the note file (denied)")
    sid = session["id"]
    run = client.post(f"/v1/agent-sessions/{sid}/run", headers=h, json={})
    assert run.json()["session"]["status"] == "awaiting_approval"
    decided = client.post(f"/v1/agent-sessions/{sid}/approve", headers=h,
                          json={"decision": "deny"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["session"]["status"] == "completed"
    assert not (tmp_path / "ws" / "note.txt").exists()


def test_session_get_list_and_cancel(stub, tenant, tmp_path):
    h = hdr(tenant)
    session = _create_session(h, tmp_path, "a goal nobody runs")
    sid = session["id"]
    got = client.get(f"/v1/agent-sessions/{sid}", headers=h)
    assert got.status_code == 200
    assert got.json()["session"]["goal"] == "a goal nobody runs"
    listed = client.get("/v1/agent-sessions", headers=h)
    assert any(s["id"] == sid for s in listed.json()["sessions"])
    cancelled = client.post(f"/v1/agent-sessions/{sid}/cancel",
                            headers=h, json={})
    assert cancelled.json()["session"]["status"] == "cancelled"
    # Tenant isolation: another tenant cannot see the session.
    other = client.get(f"/v1/agent-sessions/{sid}",
                       headers=hdr("org_someone_else"))
    assert other.status_code == 404


def test_evolve_report_endpoint(stub, tenant):
    h = hdr(tenant)
    r = client.get("/v1/evolve/report", headers=h)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["tenant_id"] == tenant
    assert "failure_patterns" in report and "lessons" in report
    assert report["safety"]["self_modification"] == "never"
    assert "never modifies its own code" in report["safety"]["statement"]
