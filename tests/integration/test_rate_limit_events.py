"""v21.4.4: a mission whose provider answers 429 mid-run must not
die (the v21.4.3 owner failure: researcher call, HTTP 429, mission
gone) and must not look hung either — the wait shows up on the
mission's event stream as ProviderRateLimited.

A BYOK mission runs against a stub gateway whose FIRST chat
completion is a 429 with Retry-After; every later call succeeds.
The orchestrator's GatewayClient is patched with test-fast pacing
values (the production defaults — 5s base gap, 1s backoff base —
would make the test crawl without changing what it proves).
"""
import asyncio
import json
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import connections as conn_mod
from app import orchestrator as orch_mod
from app.catalog import sync_connection_catalog
from app.config import Settings
from app.gateway_client import GatewayClient
from app.orchestrator import Orchestrator
from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store
from app.vault import Vault
from tests.helpers_stub_gateway import CANDIDATE, MODELS

TENANT = "org_rate_limit"
KEY = "tenant-secret-429"


class _FastPaceClient(GatewayClient):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("backoff_s", 0.01)
        kwargs.setdefault("pace_base_s", 0.05)
        kwargs.setdefault("pace_cap_s", 0.05)
        super().__init__(*args, **kwargs)


def _start_flaky_stub():
    state = {"posts": 0}

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

        def do_GET(self):
            if self.path.endswith("/models"):
                return self._send(200, {"data": MODELS})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            state["posts"] += 1
            if state["posts"] == 1:
                # The provider's per-minute quota bites exactly once,
                # on the mission's first model call.
                return self._send(429, {"error": "rate limited"},
                                  {"Retry-After": "0.2"})
            messages = payload.get("messages", [])
            system = messages[0]["content"] if messages else ""
            user = messages[1]["content"] if len(messages) > 1 else ""
            if "critic" in system.lower():
                m = re.search(r"cand_[a-z0-9]+", user)
                content = {"critiques": [{
                    "candidate_id": m.group(0) if m else "cand_unknown",
                    "target_claim_id": None, "issue": "Stub critique.",
                    "severity": "medium", "requested_action": "Add sourcing."}]}
            else:
                content = CANDIDATE
            return self._send(200, {
                "id": "chatcmpl-stub", "model": payload.get("model", "stub-model-1"),
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1", state


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("OMNIMIND_ALLOW_LOCAL_GATEWAY", "1")
    monkeypatch.setattr(orch_mod, "GatewayClient", _FastPaceClient)
    server, base, state = _start_flaky_stub()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        vault = Vault.from_env(tmp)
        seed_registry(store, TENANT)
        for s in SEED_SOURCES:
            store.put_source(TENANT, s.model_dump())
        yield store, vault, base, state
    server.shutdown()


async def _setup_connection(store, vault, base):
    conn = conn_mod.create_connection(store, vault, TENANT, "alice", {
        "kind": "omniroute_gateway", "label": "Tenant gateway",
        "base_url": base, "api_key": KEY})
    conn = await conn_mod.validate_connection(store, vault, TENANT, "alice", conn["id"])
    assert conn["state"] == "active"
    full = store.get_connection(TENANT, conn["id"])
    secret = conn_mod.get_secret_for_call(store, vault, TENANT, conn["id"])
    await sync_connection_catalog(store, TENANT, full, secret)
    return conn


def test_mission_survives_429_and_reports_the_wait(env):
    store, vault, base, state = env
    conn = asyncio.run(_setup_connection(store, vault, base))
    contract = {"objective": "Compare launch strategies", "deliverables": ["Brief"],
                "acceptance": ["Cited claims"],
                "constraints": {"max_cost_usd": 10.0},
                "tools": {"allow": ["web.read"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal", "connection_id": conn["id"]}
    mission = store.create_mission(TENANT, "alice", "Rate-limited mission",
                                   contract, 10.0)
    orch = Orchestrator(store, settings=Settings(
        llm_base_url="http://127.0.0.1:9/v1", llm_model="platform-model",
        llm_api_key="platform-key"), vault=vault)
    detail = asyncio.run(orch.run_mission(TENANT, mission["id"]))

    assert detail["mission"]["status"] == "completed", \
        "one 429 with Retry-After must pause the run, not kill it"
    assert state["posts"] > 1, "the 429'd call was retried, not surrendered"

    waits = [e for e in detail["events"] if e["event_type"] == "ProviderRateLimited"]
    assert waits, "the rate-limit wait must be visible on the event stream"
    for e in waits:
        assert e["payload"]["wait_s"] > 0
        assert e["payload"]["attempt"] >= 1
        assert e["payload"]["connection_id"] == conn["id"]
    assert any(e["payload"]["reason"] == "retry_after" for e in waits)
