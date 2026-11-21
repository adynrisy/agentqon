"""Integration tests for chat composer attachments (v22.6).

The + button's full path: POST /v1/chat with base64 attachments →
server stores the bytes under the tenant data dir, folds readable
text into the message the model receives, and answers with attachment
metadata (never contents). A capturing stub gateway proves what the
model actually received: the text-file content inlined, the image
named on an honest line — never pretended to be seen.
"""
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from tests.conftest import hdr

client = TestClient(main_mod.app)
KEY = "test-key-1234"
MODELS = [{"id": "stub-model-1", "owned_by": "stub"}]


@pytest.fixture(scope="module")
def stub():
    """OpenAI-compatible stub that CAPTURES every chat payload."""
    captured = []

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

        def do_GET(self):
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._send(401, {"error": "bad key"})
            if self.path.endswith("/models"):
                return self._send(200, {"data": MODELS})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._send(401, {"error": "bad key"})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            captured.append(payload)
            user = payload.get("messages", [])[-1].get("content", "")
            return self._send(200, {
                "id": "chatcmpl-capture", "model": "stub-model-1",
                "choices": [{"message": {"content": "stub reply"}}],
                "usage": {}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", captured
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def _connect(h, base):
    r = client.post("/v1/connections", headers=h, json={
        "kind": "direct_provider", "label": "Local BYOK stub",
        "base_url": base, "api_key": KEY})
    assert r.status_code == 201, r.text
    conn = r.json()
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=h)
    assert v.status_code == 200 and v.json()["state"] == "active", v.text


def _b64(raw):
    return base64.b64encode(raw).decode()


def _stored_files(tenant_id, name):
    root = Path(os.environ["OMNIMIND_DATA"]) / "attachments" / tenant_id
    return sorted(root.glob(f"*/{name}"))


def test_chat_with_attachments_stores_and_informs_model(stub, tenant):
    base, captured = stub
    h = hdr(tenant)
    _connect(h, base)
    r = client.post("/v1/chat", headers=h, json={
        "message": "What do these say?",
        "attachments": [
            {"name": "notes.txt", "media_type": "text/plain",
             "size": 11, "content_b64": _b64(b"hello world")},
            {"name": "photo.png", "media_type": "image/png",
             "size": 9, "content_b64": _b64(b"\x89PNG fake")},
        ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] and body["source"] == "byok"
    metas = body["attachments"]
    assert [m["name"] for m in metas] == ["notes.txt", "photo.png"]
    assert [m["kind"] for m in metas] == ["text", "image"]
    assert all("content_b64" not in m and "_text" not in m for m in metas)

    stored_txt = _stored_files(tenant, "notes.txt")
    assert len(stored_txt) == 1
    assert stored_txt[0].read_bytes() == b"hello world"
    assert _stored_files(tenant, "photo.png")[0].read_bytes() == b"\x89PNG fake"
    meta = json.loads((stored_txt[0].parent / "meta.json").read_text())
    assert [f["name"] for f in meta["files"]] == ["notes.txt", "photo.png"]

    assert captured, "the stub must have received a model call"
    user_text = captured[-1]["messages"][-1]["content"]
    assert "hello world" in user_text, "text-file content reaches the model"
    assert "--- notes.txt ---" in user_text
    assert "photo.png (image, 9 bytes)" in user_text
    assert "the model reads text only" in user_text


def test_chat_attachments_stream_path(stub, tenant):
    base, captured = stub
    h = hdr(tenant)
    _connect(h, base)
    r = client.post("/v1/chat/stream", headers=h, json={
        "message": "Read this",
        "attachments": [{"name": "a.txt", "media_type": "text/plain",
                         "size": 3, "content_b64": _b64(b"abc")}]})
    assert r.status_code == 200, r.text
    assert "data:" in r.text
    assert _stored_files(tenant, "a.txt")
    assert "abc" in captured[-1]["messages"][-1]["content"]


def test_chat_attachments_rejected_cleanly(stub, tenant):
    base, _ = stub
    h = hdr(tenant)
    _connect(h, base)
    r = client.post("/v1/chat", headers=h, json={
        "message": "hi",
        "attachments": [{"name": "run.exe", "media_type": "application/x-msdownload",
                         "size": 1, "content_b64": _b64(b"x")}]})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_attachments"
    r = client.post("/v1/chat", headers=h, json={"message": ""})
    assert r.status_code == 400, r.text
