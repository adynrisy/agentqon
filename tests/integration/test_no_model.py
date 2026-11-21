"""v20 no-model behaviour at the API surface: with no real model
connected, generation endpoints refuse with HTTP 503
`model_not_connected` (naming the fixes), and a mission that somehow
starts anyway ends FAILED with that reason — never completed."""
import asyncio

import app.main as main
from app.config import Settings
from app.orchestrator import Orchestrator
from tests.conftest import MISSION_BODY, hdr

DEAD = Settings(llm_base_url="http://127.0.0.1:9/v1", llm_api_key="ollama",
                llm_base_url_explicit=True)


def _patch_dead_orchestrator(monkeypatch):
    dead_orch = Orchestrator(main.store, settings=DEAD, vault=main.vault)
    monkeypatch.setattr(main, "orchestrator", dead_orch)
    return dead_orch


def test_run_without_model_returns_503_model_not_connected(client, tenant, monkeypatch):
    _patch_dead_orchestrator(monkeypatch)
    created = client.post("/v1/missions", headers=hdr(tenant, "nomodel-1"),
                          json=MISSION_BODY)
    assert created.status_code == 201  # a draft is data, not generation
    mid = created.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 503
    err = run.json()["error"]
    assert err["code"] == "model_not_connected"
    assert "Connections" in err["message"]  # the fixes are named
    # Nothing was queued or faked: the mission is still a draft.
    detail = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "draft"
    assert detail["candidates"] == []


def test_started_mission_without_model_ends_failed(client, tenant, monkeypatch):
    dead_orch = _patch_dead_orchestrator(monkeypatch)
    created = client.post("/v1/missions", headers=hdr(tenant, "nomodel-2"),
                          json=MISSION_BODY)
    mid = created.json()["mission_id"]
    detail = asyncio.run(dead_orch.run_mission(tenant, mid))
    assert detail["mission"]["status"] == "failed"
    assert detail["candidates"] == []
    failed = [e for e in detail["events"] if e["event_type"] == "MissionFailed"]
    assert failed and failed[0]["payload"]["reason"] == "model_not_connected"


def test_arena_duel_without_model_returns_503(client, tenant, monkeypatch):
    _patch_dead_orchestrator(monkeypatch)
    r = client.post("/v1/arena/duels", headers=hdr(tenant),
                    json={"task_type": "strategy", "prompt": "Pitch the launch"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_not_connected"


def test_health_reports_model_state(client):
    h = client.get("/health").json()
    assert "agent_mode" not in h
    # The suite's server is wired to the stub model via env config.
    assert h["model"]["connected"] is True
    assert h["model"]["source"] == "env"
    assert h["model"]["name"] == "stub-model-1"
