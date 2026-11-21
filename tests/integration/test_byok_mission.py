"""V3 BYOK mission E2E (orchestrator level): a mission pinned to a
tenant's own gateway connection runs through THAT connection (the stub
verifies the exact key), records route decisions, and — critically — a
revoked connection fails the mission instead of silently falling back
to a platform key (Master Build §108, §5)."""
import asyncio
import tempfile

import pytest

from app import connections as conn_mod
from app.catalog import sync_connection_catalog
from app.config import Settings
from app.orchestrator import Orchestrator
from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store
from app.vault import Vault
from tests.helpers_stub_gateway import start_stub

TENANT = "org_byok_e2e"
KEY = "tenant-secret-777"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("OMNIMIND_ALLOW_LOCAL_GATEWAY", "1")
    server, base = start_stub(expected_key=KEY)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        vault = Vault.from_env(tmp)
        seed_registry(store, TENANT)
        for s in SEED_SOURCES:
            store.put_source(TENANT, s.model_dump())
        yield store, vault, base
    server.shutdown()


async def _setup_connection(store, vault, base, key=KEY):
    conn = conn_mod.create_connection(store, vault, TENANT, "alice", {
        "kind": "omniroute_gateway", "label": "Tenant gateway",
        "base_url": base, "api_key": key})
    conn = await conn_mod.validate_connection(store, vault, TENANT, "alice", conn["id"])
    assert conn["state"] == "active"
    full = store.get_connection(TENANT, conn["id"])
    secret = conn_mod.get_secret_for_call(store, vault, TENANT, conn["id"])
    await sync_connection_catalog(store, TENANT, full, secret)
    return conn


def _mission(store, connection_id):
    contract = {"objective": "Compare launch strategies", "deliverables": ["Brief"],
                "acceptance": ["Cited claims"],
                "constraints": {"max_cost_usd": 10.0},
                "tools": {"allow": ["web.read"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal", "connection_id": connection_id}
    return store.create_mission(TENANT, "alice", "BYOK mission", contract, 10.0)


def _settings():
    return Settings(llm_base_url="http://127.0.0.1:9/v1",
                    llm_model="platform-model", llm_api_key="platform-key")


def test_mission_runs_through_tenant_connection(env):
    store, vault, base = env
    conn = asyncio.run(_setup_connection(store, vault, base))
    mission = _mission(store, conn["id"])
    orch = Orchestrator(store, settings=_settings(), vault=vault)
    detail = asyncio.run(orch.run_mission(TENANT, mission["id"]))
    assert detail["mission"]["status"] == "completed"
    assert len(detail["candidates"]) == 2
    decisions = detail["route_decisions"]
    assert decisions and all(d["connection_id"] == conn["id"] for d in decisions)
    assert all(d["model_id"] == "stub-model-1" for d in decisions)
    runs = store._all("SELECT adapter_version, usage FROM runs WHERE tenant_id=?", (TENANT,))
    assert runs and all(r["adapter_version"] == "llm-adapter-1.0" for r in runs)


def test_revoked_connection_fails_mission_no_silent_fallback(env):
    store, vault, base = env
    conn = asyncio.run(_setup_connection(store, vault, base))
    conn_mod.revoke_connection(store, TENANT, "alice", conn["id"])
    mission = _mission(store, conn["id"])
    orch = Orchestrator(store, settings=_settings(), vault=vault)
    with pytest.raises(RuntimeError, match="not available"):
        asyncio.run(orch.run_mission(TENANT, mission["id"]))
    events = [e["event_type"] for e in store.list_events(TENANT, mission["id"])]
    assert "ConnectionUnavailable" in events
    assert store.list_candidates(TENANT, mission["id"]) == []  # nothing faked
