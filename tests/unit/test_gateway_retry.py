"""v21.4.2 workstream H: bounded retry with backoff in the model
gateway client. One transient 503 from a hosted provider used to kill
a whole mission; now retryable failures (429/500/502/503/504,
connection errors, timeouts) are retried up to 3 times with backoff,
client errors are never retried, and exhausted errors say so.

Uses a real local stub server (real sockets, real httpx) with a
scripted status sequence and a hit counter.
"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.config import Settings
from app.gateway_client import GatewayClient, GatewayError
from app.llm_adapter import _ChatClient

CHAT_OK = {
    "id": "chatcmpl-stub",
    "choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "stub-model-1",
}


class _ScriptedStub:
    """Serves `script` statuses for POST /chat/completions first,
    then 200 CHAT_OK forever. Counts every hit."""

    def __init__(self, script, retry_after=None):
        self.script = list(script)
        self.retry_after = retry_after
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, obj, headers=None):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                outer.hits += 1
                if outer.script:
                    code = outer.script.pop(0)
                    hdrs = {}
                    if outer.retry_after is not None:
                        hdrs["Retry-After"] = str(outer.retry_after)
                    return self._send(code, {"error": "scripted"}, hdrs)
                return self._send(200, CHAT_OK)

            def do_GET(self):
                outer.hits += 1
                return self._send(404, {"error": "nope"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _client(stub, **kw):
    kw.setdefault("backoff_s", 0.01)  # test-friendly delays
    return GatewayClient(base_url=stub.base_url, api_key="k", timeout=10, **kw)


def test_flaky_503_twice_then_success():
    stub = _ScriptedStub([503, 503])
    try:
        data, usage = asyncio.run(_client(stub).chat_json("stub-model-1", "sys", "user"))
        assert data == {"ok": True}
        assert usage["model"] == "stub-model-1"
        assert stub.hits == 3, "two failures + the successful third attempt"
    finally:
        stub.close()


def test_mission_path_invoke_survives_a_transient_503():
    """The layer every role's invoke() funnels through (_ChatClient →
    GatewayClient) returns normally after a transient failure."""
    stub = _ScriptedStub([503])
    try:
        settings = Settings(llm_base_url=stub.base_url, llm_api_key="k",
                            llm_model="stub-model-1")
        chat = _ChatClient(settings, client=_client(stub))
        result = asyncio.run(chat.chat_json("sys", "user"))
        assert result.data == {"ok": True}
        assert stub.hits == 2
    finally:
        stub.close()


def test_always_503_exhausts_with_attempt_count():
    stub = _ScriptedStub([503] * 10)
    try:
        with pytest.raises(GatewayError) as ei:
            asyncio.run(_client(stub).chat_json("stub-model-1", "sys", "user"))
        assert ei.value.status == 503
        assert "gateway returned HTTP 503" in str(ei.value)
        assert "(after 4 attempts)" in str(ei.value), "the error must say retries were attempted"
        assert stub.hits == 4, "1 initial attempt + 3 retries, no more"
    finally:
        stub.close()


def test_client_error_400_is_never_retried():
    stub = _ScriptedStub([400] * 10)
    try:
        client = _client(stub)
        with pytest.raises(GatewayError) as ei:
            asyncio.run(client._request("POST", "/chat/completions", json={"model": "m"}))
        assert ei.value.status == 400
        assert "after" not in str(ei.value), "a single attempt must not claim retries"
        assert stub.hits == 1, "400 is an answer, not weather — exactly one request"
    finally:
        stub.close()


def test_retry_after_header_is_honored():
    stub = _ScriptedStub([429], retry_after=0)
    try:
        # backoff_s=5 would sleep ~5s if Retry-After were ignored.
        # v21.4.4: a 429 also engages pacing (default base gap 5s);
        # pin it tiny here so this test isolates its own question —
        # Retry-After vs computed backoff. Pacing itself is pinned by
        # tests/unit/test_gateway_rate_limit.py.
        client = _client(stub, backoff_s=5.0, pace_base_s=0.01, pace_cap_s=0.01)
        t0 = time.monotonic()
        data, _ = asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        elapsed = time.monotonic() - t0
        assert data == {"ok": True}
        assert stub.hits == 2
        assert elapsed < 2.0, f"Retry-After: 0 must short-circuit the backoff (took {elapsed:.2f}s)"
    finally:
        stub.close()


def test_retry_delay_policy():
    c = GatewayClient(base_url="http://127.0.0.1:9/v1")
    # Pure backoff doubles: 1s, 2s, 4s (plus up to 25% jitter).
    assert 1.0 <= c._retry_delay(1, None) <= 1.25
    assert 2.0 <= c._retry_delay(2, None) <= 2.5
    assert 4.0 <= c._retry_delay(3, None) <= 5.0
    # Numeric Retry-After wins on 429/503 and is capped at 30s.
    ra = httpx.Response(503, headers={"Retry-After": "3600"})
    assert c._retry_delay(1, ra) == 30.0
    ra2 = httpx.Response(429, headers={"Retry-After": "2"})
    assert c._retry_delay(1, ra2) == 2.0
    # Retry-After on other statuses is ignored (spec: 429/503 only).
    ra3 = httpx.Response(500, headers={"Retry-After": "0"})
    assert 1.0 <= c._retry_delay(1, ra3) <= 1.25
    # Non-numeric (HTTP-date) Retry-After falls back to backoff.
    ra4 = httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
    assert 1.0 <= c._retry_delay(1, ra4) <= 1.25


def test_error_surfaces_never_echo_provider_body_or_credentials():
    """Audit R (X3): a provider error body can embed anything — C saw an
    upstream 402 whose text carried a provider URL containing a key.
    Our error surfaces (exception → events/jobs/API detail) must carry
    only the category + status, never the body, a URL, or a key."""
    secret_body = {"error": {"message": "payment required — see "
                                         "https://provider.example/v1?api_key=FAKEKEY999"}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps(secret_body).encode()
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/v1?api_key=FAKEKEY999"
        client = GatewayClient(base_url=base, api_key="FAKEKEY999",
                               timeout=10, backoff_s=0.01)
        with pytest.raises(GatewayError) as ei:
            asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        msg = str(ei.value)
        assert "FAKEKEY999" not in msg
        assert "provider.example" not in msg
        assert "payment required" not in msg
        assert "402" in msg
    finally:
        server.shutdown()
        server.server_close()
