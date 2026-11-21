"""v21.4.4 rate-limit survival: a 429 is a per-minute quota, not a
flaky 503. The gateway client now (1) retries 429s up to 8 attempts
total, honoring Retry-After up to a 120s cap (other transient
statuses keep the v21.4.2 4-attempt policy), (2) paces subsequent
calls to a connection that has answered 429 — a gap that starts at
pace_base_s, doubles on repeated 429s up to pace_cap_s, and is
forgotten after a quiet period — and (3) reports every rate-limit
wait through on_rate_limited so a mission can show it.

Real local stub server (real sockets, real httpx) with a scripted
status sequence, mirroring test_gateway_retry.py. Test waits stay
tiny via injected backoff/pace parameters and fractional
Retry-After values — nothing here sleeps a real quota window.
"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app import gateway_client as gw_mod
from app.gateway_client import GatewayClient, GatewayError

CHAT_OK = {
    "id": "chatcmpl-stub",
    "choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "stub-model-1",
}


@pytest.fixture(autouse=True)
def _clean_pacing():
    gw_mod._reset_pacing()
    yield
    gw_mod._reset_pacing()


class _ScriptedStub:
    """Serves `script` statuses for POST /chat/completions first
    (each with the configured Retry-After), then 200 CHAT_OK."""

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


# ------------------------------------------------------------ (a)
def test_429_retry_after_twice_then_success_pacing_engaged_and_events():
    stub = _ScriptedStub([429, 429], retry_after=0.05)
    try:
        events = []
        client = _client(stub, connection_id="conn-rl-a",
                         pace_base_s=0.2, pace_cap_s=0.4,
                         on_rate_limited=events.append)
        t0 = time.monotonic()
        data, usage = asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        elapsed = time.monotonic() - t0
        assert data == {"ok": True}
        assert stub.hits == 3, "two 429s + the successful third attempt"
        assert elapsed < 5.0
        # Pacing engaged for this connection, gap doubled once (0.2 → 0.4).
        state = gw_mod._PACE_STATES["conn-rl-a"]
        assert state.gap_s == pytest.approx(0.4)
        assert state.last_429_at > 0
        # Visibility: both kinds of wait were reported, with attempt
        # numbers and positive waits.
        reasons = {e["reason"] for e in events}
        assert "retry_after" in reasons
        assert "pacing" in reasons
        assert all(e["wait_s"] > 0 and e["attempt"] >= 1 for e in events)
        assert all(e["status"] == 429 for e in events)
        assert all(e["connection_id"] == "conn-rl-a" for e in events)
    finally:
        stub.close()


# ------------------------------------------------------------ (b)
def test_429_exhaustion_eight_attempts_then_paced_next_call_only_for_that_connection():
    stub = _ScriptedStub([429] * 8, retry_after=0)
    try:
        client = _client(stub, connection_id="conn-rl-b",
                         pace_base_s=0.3, pace_cap_s=0.3)
        with pytest.raises(GatewayError) as ei:
            asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        assert ei.value.status == 429
        assert ei.value.category == "rate_limited"
        assert "(after 8 attempts)" in str(ei.value), \
            "429 gets the long budget: 1 initial + 7 retries"
        assert stub.hits == 8

        # The script is exhausted → the stub now answers 200, but the
        # NEXT call to the SAME connection must still honor the gap.
        t0 = time.monotonic()
        data, _ = asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        paced_elapsed = time.monotonic() - t0
        assert data == {"ok": True}
        assert stub.hits == 9
        assert paced_elapsed >= 0.2, \
            f"next call after a 429 storm must be paced (took {paced_elapsed:.2f}s)"

        # A DIFFERENT connection to the very same provider is not paced.
        other = _client(stub, connection_id="conn-rl-other",
                        pace_base_s=0.3, pace_cap_s=0.3)
        t0 = time.monotonic()
        data, _ = asyncio.run(other.chat_json("stub-model-1", "sys", "user"))
        other_elapsed = time.monotonic() - t0
        assert data == {"ok": True}
        assert stub.hits == 10
        assert other_elapsed < 0.2, \
            f"healthy connections are never paced (took {other_elapsed:.2f}s)"
    finally:
        stub.close()


# ------------------------------------------------------------ (c)
def test_503_keeps_the_four_attempt_policy_and_never_engages_pacing():
    stub = _ScriptedStub([503] * 10)
    try:
        events = []
        client = _client(stub, connection_id="conn-rl-c",
                         on_rate_limited=events.append)
        with pytest.raises(GatewayError) as ei:
            asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
        assert ei.value.status == 503
        assert "(after 4 attempts)" in str(ei.value), \
            "503 keeps the v21.4.2 budget, not the 429 budget"
        assert stub.hits == 4
        assert gw_mod._PACE_STATES["conn-rl-c"].gap_s == 0, \
            "pacing is a 429 behavior only"
        assert events == [], "no rate-limit waits happened, none reported"
    finally:
        stub.close()


# ------------------------------------------------------------ (d)
def test_healthy_connection_sees_zero_added_delay():
    stub = _ScriptedStub([])
    try:
        events = []
        client = _client(stub, connection_id="conn-rl-d",
                         on_rate_limited=events.append)
        t0 = time.monotonic()
        for _ in range(3):
            data, _ = asyncio.run(client.chat_json("stub-model-1", "sys", "user"))
            assert data == {"ok": True}
        elapsed = time.monotonic() - t0
        assert stub.hits == 3
        assert elapsed < 0.5, f"no 429 → no pacing, no retries (took {elapsed:.2f}s)"
        assert events == []
        assert gw_mod._PACE_STATES["conn-rl-d"].gap_s == 0
    finally:
        stub.close()


# ------------------------------------------------------- policy units
def test_rate_limit_policy_units():
    c = GatewayClient(base_url="http://127.0.0.1:9/v1")
    # Budgets: 429 → attempts 1..8; everything else → attempts 1..4.
    assert c._may_retry(7, 429) is True
    assert c._may_retry(8, 429) is False
    assert c._may_retry(3, 503) is True
    assert c._may_retry(4, 503) is False
    assert c._may_retry(3, None) is True
    assert c._may_retry(4, None) is False
    # Retry-After caps: 120s for a 429, the old 30s for a 503.
    ra429 = httpx.Response(429, headers={"Retry-After": "3600"})
    assert c._retry_delay(1, ra429) == 120.0
    ra429b = httpx.Response(429, headers={"Retry-After": "90"})
    assert c._retry_delay(1, ra429b) == 90.0
    ra503 = httpx.Response(503, headers={"Retry-After": "3600"})
    assert c._retry_delay(1, ra503) == 30.0


def test_pacing_gap_doubles_then_forgiven_after_quiet_period(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(gw_mod, "_now", lambda: clock["t"])
    c = GatewayClient(base_url="http://127.0.0.1:9/v1", connection_id="conn-rl-e",
                      pace_base_s=5.0, pace_cap_s=60.0, pace_reset_s=120.0)
    c._pace_note_rate_limited()
    assert gw_mod._PACE_STATES["conn-rl-e"].gap_s == 5.0
    c._pace_note_rate_limited()
    assert gw_mod._PACE_STATES["conn-rl-e"].gap_s == 10.0
    c._pace_note_rate_limited()
    assert gw_mod._PACE_STATES["conn-rl-e"].gap_s == 20.0
    # A 429 after a long quiet period restarts at the base gap
    # instead of doubling the stale one.
    clock["t"] += 121.0
    c._pace_note_rate_limited()
    assert gw_mod._PACE_STATES["conn-rl-e"].gap_s == 5.0
    # And once the connection has been quiet past the reset window,
    # the pre-attempt check forgets pacing entirely.
    clock["t"] += 121.0
    asyncio.run(c._pace_before_attempt(1))
    assert gw_mod._PACE_STATES["conn-rl-e"].gap_s == 0.0
