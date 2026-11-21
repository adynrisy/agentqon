"""v21.4.2 workstream A: GET /v1/model-state is TENANT-aware.

/health reports platform sources only (env endpoint / OmniRoute /
Ollama probes) — a user whose only model is an active BYOK connection
was told "No model connected" forever while missions ran fine. The new
endpoint resolves exactly what mission runs resolve (resolve_state):
the tenant's active connection + catalog first.
"""
import dataclasses
import os

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.modelstate
from tests.conftest import hdr
from tests.helpers_stub_gateway import start_stub

client = TestClient(main_mod.app)
KEY = "test-key-1234"


@pytest.fixture(scope="module")
def stub():
    server, base = start_stub(expected_key=KEY)
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def test_byok_connection_counts_as_connected(stub, tenant):
    h = hdr(tenant)
    r = client.post("/v1/connections", headers=h, json={
        "kind": "direct_provider", "label": "Local BYOK stub",
        "base_url": stub, "api_key": KEY})
    assert r.status_code == 201, r.text
    conn = r.json()
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=h)
    assert v.status_code == 200, v.text
    assert v.json()["state"] == "active" and v.json()["models_count"] == 2

    r = client.get("/v1/model-state", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["source"] == "byok"
    assert body["model"] in ("stub-model-1", "stub-model-2")
    assert body["base_url"] == stub


def test_fresh_tenant_with_no_platform_endpoint_is_not_connected(tenant, monkeypatch):
    """With no BYOK and no platform endpoint, the state is honestly
    not-connected. (The session conftest points the env endpoint at a
    stub LLM, so this test re-resolves with the env endpoint NOT
    explicitly configured — and, audit R: the probes are stubbed to
    find nothing. The original version relied on the sandbox having
    no local model servers, which is false wherever Ollama/OmniRoute
    legitimately run — the resolver auto-detecting them is the
    designed behaviour, so the test must isolate itself from the
    ambient environment instead.)"""
    monkeypatch.setattr("app.modelstate.probe_models",
                        lambda url, key: None)
    real = main_mod.orchestrator

    class _Stub:
        def model_state(self, tenant_id=None):
            settings = dataclasses.replace(real.settings, llm_base_url_explicit=False)
            return app.modelstate.resolve_state(settings, main_mod.store, tenant_id)

    monkeypatch.setattr(main_mod, "orchestrator", _Stub())
    r = client.get("/v1/model-state", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is False
    assert body["model"] is None
