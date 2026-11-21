"""Backend audit (2026-10-22) repair — API-level regressions.

B4: re-running a COMPLETED mission through POST /v1/missions/{id}/run
used to enqueue a job whose execution re-planned the mission (phantom
tasks, overwritten plan) and then died on the state machine — inline
callers got a 500 and the mission detail was left mutated. The endpoint
now refuses with 409 STATE_CONFLICT before anything is enqueued, and
the completed mission is byte-identical afterwards.
"""
import uuid

from tests.conftest import MISSION_BODY, hdr


def test_run_completed_mission_is_409_and_mutates_nothing(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 201
    mid = r.json()["mission_id"]

    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200
    detail = run.json()
    assert detail["mission"]["status"] == "completed"
    tasks_before = [(t["id"], t["status"]) for t in detail["tasks"]]
    events_before = [e["event_type"] for e in detail["events"]]

    again = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_CONFLICT"

    after = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    assert after["mission"]["status"] == "completed"
    assert [(t["id"], t["status"]) for t in after["tasks"]] == tasks_before
    assert [e["event_type"] for e in after["events"]] == events_before
