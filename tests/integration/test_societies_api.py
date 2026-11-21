"""Society simulation API (2026-10-22): POST /v1/societies sizes +
casts (never runs), POST /run starts the background world (with the
>100 confirmation fence), GET returns the derived summary / world
output / graph, POST /cancel stops it. Tenant isolation throughout.

The gateway is a scripted fake injected through the service's
client_factory seam (swapped onto app.main), so the HTTP surface is
exercised end-to-end with deterministic reactions — the engine's
bookkeeping is unit-proven in tests/unit/test_society.py.
"""
import re
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.society import SocietyService
from tests.conftest import MISSION_BODY, hdr

client = TestClient(main_mod.app)
STANCES = ["support", "oppose", "neutral"]


class _FakeGateway:
    """Stable split every round ⇒ the room converges at round 2."""

    def _stance(self, agent_id):
        return STANCES[sum(agent_id.encode()) % 3]

    async def chat_json(self, model, system, user, temperature=0.4):
        if "reactions" in system:
            members = re.findall(r"^- (\S+) \(", user, flags=re.M)
            return {"reactions": [
                {"agent_id": m, "stance": self._stance(m),
                 "intensity": 0.6, "action": "share",
                 "line": f"{m} has a settled view."} for m in members]}, {}
        m = re.search(r"you are agent (\S+?),", user)
        return {"stance": self._stance(m.group(1)), "intensity": 0.6,
                "action": "ignore",
                "line": f"{m.group(1)} has a settled view."}, {}


@pytest.fixture()
def fake_service(monkeypatch):
    svc = SocietyService(main_mod.store, main_mod.settings,
                         main_mod.orchestrator,
                         client_factory=lambda t: (_FakeGateway(),
                                                   "fake-model"))
    monkeypatch.setattr(main_mod, "society_service", svc)
    return svc


def _poll(tenant_id, society_id, deadline=60):
    end = time.time() + deadline
    while time.time() < end:
        r = client.get(f"/v1/societies/{society_id}", headers=hdr(tenant_id))
        assert r.status_code == 200, r.text
        body = r.json()
        if body["state"] in ("done", "capped", "failed", "cancelled"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"society {society_id} never settled")


def _completed_mission(tenant_id):
    h = hdr(tenant_id, key="soc-mission-done")
    r = client.post("/v1/missions", headers=h, json=MISSION_BODY)
    assert r.status_code in (200, 201), r.text
    mid = r.json()["mission_id"]
    store = main_mod.store
    store.put_candidate(tenant_id, {
        "candidate_id": "cand_api_1", "mission_id": mid,
        "agent_version_id": "agent.drafter.core@1",
        "deliverable": "Launch at $20 with a public announcement.",
        "summary": "Launch at $20.", "claims": []})
    store.put_evaluation(tenant_id, mid, {
        "evaluation_id": "ev_api_1", "candidate_id": "cand_api_1",
        "dimensions": {"quality": {"score": 0.9}}})
    store.set_mission_status(tenant_id, mid, "completed")
    return mid


NICHE_TASK = "Quick sanity check on a niche b2b pricing change"


def test_create_sizes_but_never_runs(tenant, fake_service):
    r = client.post("/v1/societies", headers=hdr(tenant),
                    json={"task_text": NICHE_TASK})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "sized"
    assert body["sizer"]["size"] == 25
    assert body["sizer"]["requires_confirmation"] is False
    assert body["summary"]["responded"] == 0
    assert body["world_output"] is None
    assert body["events_count"] == 0
    assert body["label"].startswith("SIMULATED ESTIMATE")


def test_create_validation_and_mission_states(tenant, fake_service):
    h = hdr(tenant)
    assert client.post("/v1/societies", headers=h,
                       json={}).status_code == 400
    mid = _completed_mission(tenant)
    both = client.post("/v1/societies", headers=h,
                       json={"mission_id": mid, "task_text": "x"})
    assert both.status_code == 400
    ok = client.post("/v1/societies", headers=h,
                     json={"mission_id": mid})
    assert ok.status_code == 201, ok.text
    assert client.post("/v1/societies", headers=h,
                       json={"mission_id": "mis_nope"}
                       ).status_code == 404
    draft = client.post("/v1/missions",
                        headers=hdr(tenant, key="soc-mission-draft"),
                        json=MISSION_BODY)
    draft_id = draft.json()["mission_id"]
    refused = client.post("/v1/societies", headers=h,
                          json={"mission_id": draft_id})
    assert refused.status_code == 409  # not completed yet


def test_run_completes_with_derived_output_and_graph(tenant, fake_service):
    h = hdr(tenant)
    created = client.post("/v1/societies", headers=h,
                          json={"task_text": NICHE_TASK}).json()
    sid = created["society_id"]
    r = client.post(f"/v1/societies/{sid}/run", headers=h, json={})
    assert r.status_code == 200, r.text
    done = _poll(tenant, sid)
    assert done["state"] == "done"
    assert done["summary"]["converged_round"] == 2
    assert done["summary"]["responded"] == 25
    assert done["events_count"] == 50  # 25 agents x 2 rounds
    world = done["world_output"]
    assert world["label"].startswith("SIMULATED ESTIMATE")
    assert world["model"] == "fake-model"
    assert abs(sum(world["stance_split_final"].values()) - 100.0) < 0.5
    graph = done["graph"]
    assert len(graph["nodes"]) == 26  # mission + 25 agents
    assert any(e["target"] == "mission" for e in graph["edges"])
    with_events = client.get(f"/v1/societies/{sid}?events=1",
                             headers=h).json()
    assert len(with_events["events"]) == 50
    # a finished society cannot be run again
    again = client.post(f"/v1/societies/{sid}/run", headers=h, json={})
    assert again.status_code == 409


def test_confirmation_fence_for_big_rooms(tenant, fake_service):
    h = hdr(tenant)
    created = client.post(
        "/v1/societies", headers=h,
        json={"task_text": "Will this go viral and spread to "
                           "consumers everywhere"}).json()
    assert created["sizer"]["size"] == 500
    assert created["sizer"]["requires_confirmation"] is True
    sid = created["society_id"]
    refused = client.post(f"/v1/societies/{sid}/run", headers=h,
                          json={"confirm": False})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    started = client.post(f"/v1/societies/{sid}/run", headers=h,
                          json={"confirm": True})
    assert started.status_code == 200, started.text
    done = _poll(tenant, sid)
    assert done["state"] == "done"
    assert done["summary"]["size"] == 500
    assert done["summary"]["responded"] == 500


def test_cancel_and_tenant_isolation(tenant, fake_service):
    h = hdr(tenant)
    other = f"{tenant}_other"
    created = client.post("/v1/societies", headers=h,
                          json={"task_text": NICHE_TASK}).json()
    sid = created["society_id"]
    assert client.get(f"/v1/societies/{sid}",
                      headers=hdr(other)).status_code == 404
    assert client.post(f"/v1/societies/{sid}/run",
                       headers=hdr(other),
                       json={}).status_code == 404
    assert client.post(f"/v1/societies/{sid}/cancel",
                       headers=hdr(other)).status_code == 404
    cancelled = client.post(f"/v1/societies/{sid}/cancel", headers=h)
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
