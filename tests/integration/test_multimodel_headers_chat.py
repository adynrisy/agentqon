"""Regression tests for the 2026-10-26 backend redesign scope.

1. Multi-model CHAT backend (multi-model.md §2.6): the chat bodies
   gain an optional ordered failover list named exactly `models`.
   Ordered failover, NOT simultaneous deliberation: the first listed
   model that answers does the work, and a switch is reported with
   the existing model_switch_notice. An unknown / duplicate entry
   fails in plain words naming the entry, before any model call.
   `model` + `models` together are rejected, never silently picked
   between. A 1-element list behaves exactly like the single pin.
2. Custom headers on CHAT calls (conn-advanced-settings §2.4): the
   restored regression file proves headers survive validate; this
   file proves a real chat provider call carries them too (the
   call path the user actually pays for).
3. Mission multi-model backend (multi-model.md §2.6): the contract
   gains `pinned_model_ids`. Creation fails fast on a bad list
   (no mission row is created); a list that goes stale before run
   start fails the mission honestly, naming the entry, with no run
   on a subset and no silent Auto fallback.

Each test uses a fresh tenant (conftest `tenant` fixture), so tests
are independent of each other and of test_connections_api.py.
"""
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import hdr
from tests.helpers_stub_gateway import start_stub

client = TestClient(app)
KEY = "test-key-1234"
HEADER_NAME = "X-Chat-Header"
HEADER_VALUE = "chat-hdr-4242"


@pytest.fixture(scope="module")
def stub():
    server, base = start_stub(expected_key=KEY)
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def _make_conn(h, base_url, key=KEY, **over):
    payload = {"kind": "direct_provider", "label": "MM conn",
               "base_url": base_url, "api_key": key}
    payload.update(over)
    r = client.post("/v1/connections", json=payload, headers=h)
    assert r.status_code == 201, r.text
    conn = r.json()
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=h)
    assert v.status_code == 200 and v.json()["state"] == "active", v.text
    return conn


def _mission_body(**contract_over):
    contract = {
        "objective": "Compare launch strategies for a new SMB invoicing product",
        "deliverables": ["Decision brief"],
        "acceptance": ["Two distinct options"],
        "data_classification": "internal",
    }
    contract.update(contract_over)
    return {"title": "MM mission", "contract": contract}


def _create_mission(h, **contract_over):
    r = client.post("/v1/missions", json=_mission_body(**contract_over),
                    headers=hdr(h["X-Tenant-Id"], key=f"mm-{uuid.uuid4().hex[:12]}"))
    return r


# ------------------------------------------------------------ chat: failover

class _FailoverHandler(BaseHTTPRequestHandler):
    """One stub, two models: 'fail-model' always 503s on chat,
    'good-model' answers. Both are listed by /v1/models, so a list
    [fail-model, good-model] must fail over in order."""

    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [
                {"id": "fail-model", "owned_by": "stub"},
                {"id": "good-model", "owned_by": "stub"}]})
        return self._send(404, {"error": "nope"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("model") == "fail-model":
            return self._send(503, {"error": {"message": "provider down",
                                              "type": "server_error"}})
        if payload.get("stream"):
            # Real SSE: the gateway's stream reader only accepts
            # data: lines with delta.content — a plain JSON body
            # would (correctly) yield "no reply" and exhaust the
            # chain, which would test the stub, not the failover.
            body = (
                'data: {"choices": [{"delta": {"content": "answer from good model"}}]}\n\n'
                'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], '
                '"usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n\n'
                'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._send(200, {
            "id": "chatcmpl-mm", "model": payload.get("model", "good-model"),
            "choices": [{"message": {"content": "answer from good model"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


@pytest.fixture()
def failover_stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FailoverHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def test_chat_models_failover_in_order_with_switch_notice(failover_stub, tenant):
    h = hdr(tenant)
    _make_conn(h, failover_stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": ["fail-model", "good-model"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "good-model", body
    assert body["source"] == "combo"
    assert body["combo"] == "Chosen models"
    assert body.get("model_switch_notice"), \
        "failover must never be silent — the switch notice is the proof"
    assert body["reply"]


def test_chat_models_stream_uses_list(failover_stub, tenant):
    h = hdr(tenant)
    _make_conn(h, failover_stub)
    r = client.post("/v1/chat/stream", headers=h, json={
        "message": "hello", "models": ["fail-model", "good-model"]})
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines()
              if line.startswith("data: ")]
    dones = [e for e in events if e.get("type") == "done"]
    assert dones and dones[0]["model"] == "good-model", events
    assert not any(e.get("type") == "error" for e in events), events


def test_chat_models_unknown_entry_names_it(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": ["stub-model-1", "model-z-nope"]})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model_list_entry_not_found"
    assert "model-z-nope" in err["message"]
    assert "{" not in err["message"], "plain words, never a raw dump"


def test_chat_models_stream_unknown_entry_names_it(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat/stream", headers=h, json={
        "message": "hello", "models": ["model-z-nope", "stub-model-1"]})
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines()
              if line.startswith("data: ")]
    errors = [e for e in events if e.get("type") == "error"]
    assert errors and "model-z-nope" in errors[0]["message"], events
    assert not any(e.get("type") == "done" for e in events)


def test_chat_models_duplicate_names_it(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": ["stub-model-1", "stub-model-1"]})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model_list_entry_not_found"
    assert "stub-model-1" in err["message"]


def test_chat_model_and_models_together_rejected(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "model": "stub-model-1",
        "models": ["stub-model-1", "stub-model-2"]})
    assert r.status_code == 422, r.text
    assert "one model or a list" in r.json()["error"]["message"]


def test_chat_models_empty_list_rejected(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": []})
    assert r.status_code == 422, r.text
    assert "no model names" in r.json()["error"]["message"]


def test_chat_models_single_entry_is_the_single_pin(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": ["stub-model-2"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "stub-model-2"
    assert body["source"] == "byok"


def test_chat_models_over_ten_rejected(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello",
        "models": [f"m-{i}" for i in range(11)]})
    assert r.status_code == 400, r.text
    assert "10" in r.json()["error"]["message"]


def test_chat_models_no_stored_combo_created(failover_stub, tenant):
    """A list send must not pollute the user's combo library."""
    h = hdr(tenant)
    _make_conn(h, failover_stub)
    before = client.get("/v1/combos", headers=h).json()["combos"]
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "models": ["fail-model", "good-model"]})
    assert r.status_code == 200, r.text
    after = client.get("/v1/combos", headers=h).json()["combos"]
    assert after == before


# ------------------------------------------------------------ headers on chat calls

class _RecordingChatHandler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _RecordingChatHandler.seen.append(dict(self.headers))
        if self.path.endswith("/models"):
            return self._send(200, {"data": [
                {"id": "rec-chat-model", "owned_by": "rec"}]})
        return self._send(404, {"error": "nope"})

    def do_POST(self):
        _RecordingChatHandler.seen.append(dict(self.headers))
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        return self._send(200, {
            "id": "chatcmpl-rec", "model": payload.get("model", "rec-chat-model"),
            "choices": [{"message": {"content": "recorded reply"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


@pytest.fixture()
def recording_chat_stub():
    _RecordingChatHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingChatHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def test_custom_headers_sent_on_chat_call(recording_chat_stub, tenant):
    """§2.4, chat half: headers that survive reload must actually be
    sent on a provider chat call, not only on validate's model list."""
    h = hdr(tenant)
    conn = _make_conn(h, recording_chat_stub,
                      headers={HEADER_NAME: HEADER_VALUE})
    assert conn["headers_masked"] == {HEADER_NAME: "••••4242"}
    _RecordingChatHandler.seen = []
    r = client.post("/v1/chat", headers=h, json={
        "message": "hello", "model": "rec-chat-model"})
    assert r.status_code == 200, r.text
    assert any(hdrs.get(HEADER_NAME) == HEADER_VALUE
               for hdrs in _RecordingChatHandler.seen), \
        "the chat provider call must carry the custom header"
    assert HEADER_VALUE not in r.text


# ------------------------------------------------------------ missions

def test_mission_create_with_bad_list_fails_fast_no_row(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    before = client.get("/v1/missions", headers=h).json()["missions"]
    r = _create_mission(h, pinned_model_ids=["stub-model-1", "ghost-model"])
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model_list_entry_not_found"
    assert "ghost-model" in err["message"]
    after = client.get("/v1/missions", headers=h).json()["missions"]
    assert len(after) == len(before), "a bad list must not create a mission row"


def test_mission_create_with_valid_list_stores_it(stub, tenant):
    h = hdr(tenant)
    _make_conn(h, stub)
    r = _create_mission(
        h, pinned_model_ids=["stub-model-1", "stub-model-2"])
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    detail = client.get(f"/v1/missions/{mid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json()["mission"]["contract"]["pinned_model_ids"] == [
        "stub-model-1", "stub-model-2"]


def test_mission_list_wins_over_single_pin_at_create(stub, tenant):
    """Spec §2.6: both keys arrive → the list wins, single ignored."""
    h = hdr(tenant)
    _make_conn(h, stub)
    r = _create_mission(
        h, pinned_model_id="ghost-single",
        pinned_model_ids=["stub-model-1", "stub-model-2"])
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    contract = client.get(f"/v1/missions/{mid}", headers=h).json()["mission"]["contract"]
    assert contract["pinned_model_ids"] == ["stub-model-1", "stub-model-2"]


def test_mission_stale_list_fails_run_honestly(stub, tenant):
    """Spec §3 test 11: a model removed before run start fails the
    mission naming the entry — no run on a subset, no Auto fallback."""
    h = hdr(tenant)
    conn = _make_conn(h, stub)
    r = _create_mission(
        h, pinned_model_ids=["stub-model-1", "stub-model-2"])
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    # Remove the models: delete the connection (catalog purged).
    d = client.request("DELETE", f"/v1/connections/{conn['id']}",
                       json={"confirm": True}, headers=h)
    assert d.status_code == 200, d.text
    run = client.post(f"/v1/missions/{mid}/run", headers=h)
    assert run.status_code == 200, run.text
    detail = run.json()
    assert detail["mission"]["status"] == "failed", detail
    events = detail["events"]
    assert any(e["event_type"] == "ModelListUnavailable" for e in events), events
    bad = [e for e in events if e["event_type"] == "ModelListUnavailable"][0]
    assert "stub-model-1" in json.dumps(bad), bad
    assert detail["candidates"] == [], "nothing may run on a subset"
