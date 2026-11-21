"""Scheduled Missions endpoints (v15): CRUD, validation, tenant
isolation, and firing through the scheduler tick against the API's
own store."""
import uuid
from datetime import datetime, timedelta, timezone

from app.main import store as app_store
from app.schedules import tick_schedules
from tests.conftest import hdr

BODY = {
    "title": "Weekly market watch",
    "objective": "Research the SMB invoicing market and summarise changes",
    "cadence": {"kind": "interval_hours", "every_hours": 1},
}


def test_schedule_crud(client, tenant):
    r = client.post("/v1/schedules", json=BODY, headers=hdr(tenant))
    assert r.status_code == 201, r.text
    sched = r.json()
    assert sched["enabled"] is True and sched["run_count"] == 0
    assert sched["next_run_at"] and sched["last_mission_id"] is None
    sid = sched["schedule_id"]

    listed = client.get("/v1/schedules", headers=hdr(tenant)).json()["schedules"]
    assert [x["schedule_id"] for x in listed] == [sid]
    got = client.get(f"/v1/schedules/{sid}", headers=hdr(tenant))
    assert got.status_code == 200

    patched = client.patch(f"/v1/schedules/{sid}", headers=hdr(tenant),
                           json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    patched = client.patch(f"/v1/schedules/{sid}", headers=hdr(tenant),
                           json={"cadence": {"kind": "daily",
                                             "time_of_day": "07:45"},
                                 "enabled": True})
    assert patched.status_code == 200
    assert patched.json()["cadence"]["kind"] == "daily"
    assert patched.json()["next_run_at"].endswith("T07:45:00Z") or \
        "07:45" in patched.json()["next_run_at"]

    deleted = client.delete(f"/v1/schedules/{sid}", headers=hdr(tenant))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get(f"/v1/schedules/{sid}",
                      headers=hdr(tenant)).status_code == 404
    assert client.delete(f"/v1/schedules/{sid}",
                         headers=hdr(tenant)).status_code == 404


def test_schedule_validation_and_isolation(client, tenant):
    bad = client.post("/v1/schedules", headers=hdr(tenant), json={
        "title": "x", "objective": "y", "cadence": {"kind": "interval_hours"}})
    assert bad.status_code == 400  # every_hours required
    bogus_project = client.post("/v1/schedules", headers=hdr(tenant), json={
        **BODY, "project_id": "prj_missing"})
    assert bogus_project.status_code == 400
    sched = client.post("/v1/schedules", json=BODY,
                        headers=hdr(tenant)).json()
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    assert client.get(f"/v1/schedules/{sched['schedule_id']}",
                      headers=hdr(other)).status_code == 404
    assert client.get("/v1/schedules",
                      headers=hdr(other)).json()["schedules"] == []


def test_schedule_fires_via_tick(client, tenant):
    sched = client.post("/v1/schedules", json=BODY,
                        headers=hdr(tenant)).json()
    future = datetime.now(timezone.utc) + timedelta(days=2)
    out = tick_schedules(app_store, now=future)
    mine = [f for f in out["fired"]
            if f["schedule_id"] == sched["schedule_id"]]
    assert len(mine) == 1 and out["errors"] == []
    after = client.get(f"/v1/schedules/{sched['schedule_id']}",
                       headers=hdr(tenant)).json()
    assert after["run_count"] == 1
    assert after["last_mission_id"] == mine[0]["mission_id"]
    missions = client.get("/v1/missions", headers=hdr(tenant)).json()["missions"]
    created = next(m for m in missions if m["id"] == mine[0]["mission_id"])
    assert created["title"] == BODY["title"]
    assert created["contract"]["task_type"] == "research"  # classified
    # Retire EVERY job this tick fired (this test's schedule, plus any
    # still-live schedule from an earlier test in this file): the tick
    # proves creation + enqueue (the unit drain test proves execution),
    # and the session store is shared — the queue's claim is global
    # FIFO, so a live queued job here would be claimed by later tests'
    # inline mission runs.
    for f in out["fired"]:
        app_store.complete_job(f["job_id"], status="cancelled")
