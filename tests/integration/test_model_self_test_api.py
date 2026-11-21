"""Model self-test API (2026-10-22): POST /v1/models/self-test starts
a background run that probes every usable model with one real 1-token
completion; GET /v1/models/self-test/{run_id} reports per-model
verdicts as they land.

Covers the run lifecycle, healthy + platform legs, auth failure, a
missing model, an unreachable connection, the 40-model cap, the
already-running rule, and tenant isolation — against real local stub
providers (the platform leg is the suite's env stub LLM).
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.gateway_client import GatewayClient
from app.modeltest import ModelSelfTester
from tests.conftest import hdr
from tests.helpers_stub_gateway import start_stub

client = TestClient(main_mod.app)
KEY = "test-key-1234"


@pytest.fixture(scope="module", autouse=True)
def _allow_local():
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


@pytest.fixture(scope="module")
def stub():
    server, base = start_stub(expected_key=KEY)
    yield base
    server.shutdown()


def _create_validated(headers, base_url, key=KEY, **over):
    payload = {"kind": "direct_provider", "label": "Self-test target",
               "base_url": base_url, "api_key": key}
    payload.update(over)
    r = client.post("/v1/connections", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    conn = r.json()
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=headers)
    assert v.status_code == 200, v.text
    return conn, v.json()


def _poll(headers, run_id, deadline=60):
    end = time.time() + deadline
    while time.time() < end:
        r = client.get(f"/v1/models/self-test/{run_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["state"] == "done":
            return body
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {deadline}s")


def _results_for(body, connection_id):
    return {r["model"]: r for r in body["results"]
            if r["connection_id"] == connection_id}


def test_self_test_healthy_connection_and_platform(tenant, stub):
    h = hdr(tenant)
    conn, validated = _create_validated(h, stub)
    assert validated["state"] == "active"
    r = client.post("/v1/models/self-test", headers=h)
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["total"] == 4  # 2 BYOK models + 2 platform (env stub) models
    body = _poll(h, started["run_id"])
    assert body["done_count"] == 4 and body["capped"] is False
    mine = _results_for(body, conn["id"])
    assert set(mine) == {"stub-model-1", "stub-model-2"}
    for res in mine.values():
        assert res["ok"] is True and res["error_code"] is None
        assert isinstance(res["latency_ms"], int) and res["latency_ms"] >= 0
        assert res["connection_label"] == "Self-test target"
    platform = _results_for(body, "platform")
    assert set(platform) == {"stub-model-1", "stub-model-2"}
    assert all(res["ok"] for res in platform.values())


def test_self_test_unknown_run_and_cross_tenant_404(tenant, stub):
    ha, hb = hdr(tenant), hdr(tenant + "_b")
    conn, _ = _create_validated(ha, stub)
    started = client.post("/v1/models/self-test", headers=ha).json()
    _poll(ha, started["run_id"])
    r = client.get(f"/v1/models/self-test/{started['run_id']}", headers=hb)
    assert r.status_code == 404  # existence hidden across tenants
    r = client.get("/v1/models/self-test/mtest_nope", headers=ha)
    assert r.status_code == 404
    # Tenant B's own run never probes tenant A's connection.
    started_b = client.post("/v1/models/self-test", headers=hb).json()
    body_b = _poll(hb, started_b["run_id"])
    assert all(res["connection_id"] != conn["id"] for res in body_b["results"])


def test_self_test_auth_failed_connection(tenant, stub):
    h = hdr(tenant)
    conn, validated = _create_validated(
        h, stub, key="wrong-key", allowed_models=["stub-model-1"])
    assert validated["state"] == "auth_failed"
    started = client.post("/v1/models/self-test", headers=h).json()
    body = _poll(h, started["run_id"])
    mine = _results_for(body, conn["id"])
    assert mine["stub-model-1"]["ok"] is False
    assert mine["stub-model-1"]["error_code"] == "auth_failed"


class _MissingProvider:
    """Lists good-model + ghost-model; completions 404 for the ghost."""

    def __init__(self):
        outer = self

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
                if self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": "good-model"},
                                                     {"id": "ghost-model"}]})
                return self._send(404, {"error": "nope"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if payload.get("model") == "ghost-model":
                    return self._send(404, {"error": "no such model"})
                return self._send(200, {
                    "id": "x", "model": payload.get("model", ""),
                    "choices": [{"message": {"content": "pong"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


def test_self_test_model_not_found(tenant):
    h = hdr(tenant)
    prov = _MissingProvider()
    try:
        conn, validated = _create_validated(h, prov.base_url)
        assert validated["state"] == "active"
        started = client.post("/v1/models/self-test", headers=h).json()
        body = _poll(h, started["run_id"])
        mine = _results_for(body, conn["id"])
        assert mine["good-model"]["ok"] is True
        assert mine["ghost-model"]["ok"] is False
        assert mine["ghost-model"]["error_code"] == "model_not_found"
    finally:
        prov.server.shutdown()


def test_self_test_unreachable_connection(tenant):
    h = hdr(tenant)
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_url = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
    sock.close()
    conn, validated = _create_validated(
        h, dead_url, allowed_models=["stub-model-1"])
    assert validated["state"] == "degraded"  # list call could not reach it
    started = client.post("/v1/models/self-test", headers=h).json()
    body = _poll(h, started["run_id"])
    mine = _results_for(body, conn["id"])
    assert mine["stub-model-1"]["ok"] is False
    assert mine["stub-model-1"]["error_code"] == "unreachable"


def test_self_test_cap_40(tenant):
    h = hdr(tenant)
    server, base = start_stub(expected_key=KEY, models=[
        {"id": f"cap-model-{i:02d}", "owned_by": "stub"} for i in range(45)])
    try:
        conn, validated = _create_validated(h, base)
        assert validated["state"] == "active"
        started = client.post("/v1/models/self-test", headers=h).json()
        assert started["total"] == 42  # 40 (capped) + 2 platform models
        # Load-tolerant wait (audit F2): _poll already waits on the
        # observable state == "done" condition; the deadline is only
        # the failure bound, sized for 42 probes on a busy box rather
        # than the 60s default that flaked under load.
        body = _poll(h, started["run_id"], deadline=300)
        assert body["capped"] is True
        assert body["cap_note"] == \
            "Only the first 40 models per connection were tested"
        assert len(_results_for(body, conn["id"])) == 40
    finally:
        server.shutdown()


class _SlowProvider:
    """Lists fast; every completion BLOCKS on a gate the test holds
    shut — a run stays in progress until the test releases it, so a
    second POST provably meets it mid-flight. (The old design slept
    a fixed 3s per completion; under load the gap between the two
    POSTs could exceed the sleep window and the run finished first —
    audit F2. The gate removes the timing assumption entirely: the
    run CANNOT finish before the release, however busy the box is.
    A 90s safety bound keeps a wedged test failing, not hanging.)"""

    def __init__(self):
        self.release = threading.Event()
        self.started = 0
        outer = self

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
                if self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": "slow-1"},
                                                     {"id": "slow-2"}]})
                return self._send(404, {"error": "nope"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.started += 1
                outer.release.wait(timeout=90)
                return self._send(200, {
                    "id": "x", "model": payload.get("model", ""),
                    "choices": [{"message": {"content": "pong"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


def test_self_test_already_running_returns_same_run(tenant, monkeypatch):
    """A second POST while a run is in progress returns that run
    instead of starting a second one (deterministic: the provider's
    completions are gated shut until this test releases them, so
    the first run cannot finish between the two POSTs — on any box,
    under any load). The tester is swapped for one with the injected
    client factory — the unit-test seam — so probe clients use the
    connections' real base URLs with test-sized budgets."""
    h = hdr(tenant)
    prov = _SlowProvider()
    try:
        conn, validated = _create_validated(h, prov.base_url, key="any")
        assert validated["state"] == "active"

        def factory(conn_dict, tenant_id):
            return GatewayClient(conn_dict["base_url"], "any" "-key",
                                 timeout=15, max_retries=0, backoff_s=0.01,
                                 rate_limit_max_retries=0,
                                 connection_id=conn_dict["id"])

        # probe_timeout_s is test scaffolding: the gated probes must
        # not time out while the test holds the gate shut.
        tester = ModelSelfTester(main_mod.store, main_mod.settings,
                                 main_mod.orchestrator, client_factory=factory,
                                 probe_timeout_s=120)
        monkeypatch.setattr(main_mod, "model_tester", tester)

        first = client.post("/v1/models/self-test", headers=h).json()
        # Wait on the observable condition — a probe is in flight at
        # the gated provider — not on a fixed sleep.
        end = time.time() + 60
        while prov.started < 1 and time.time() < end:
            time.sleep(0.05)
        assert prov.started >= 1, "the first run never reached the provider"
        second = client.post("/v1/models/self-test", headers=h).json()
        assert second["run_id"] == first["run_id"]
        assert second["total"] == first["total"]
        prov.release.set()
        body = _poll(h, first["run_id"])
        mine = _results_for(body, conn["id"])
        assert set(mine) == {"slow-1", "slow-2"}
        assert all(res["ok"] for res in mine.values())
    finally:
        prov.release.set()
        prov.server.shutdown()
