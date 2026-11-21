"""Jobs API tests (Phase 2): the durable queue is visible and tenant-scoped."""
import uuid

from tests.conftest import MISSION_BODY, hdr


def test_run_creates_a_completed_job_record(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY, headers=hdr(tenant, f"k-{uuid.uuid4().hex[:8]}"))
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200  # inline mode drains immediately
    jobs = client.get("/v1/jobs", headers=hdr(tenant)).json()["jobs"]
    mine = [j for j in jobs if j["mission_id"] == mid]
    assert len(mine) == 1 and mine[0]["status"] == "completed" and mine[0]["attempts"] == 1
    one = client.get(f"/v1/jobs/{mine[0]['id']}", headers=hdr(tenant))
    assert one.status_code == 200


def test_job_not_visible_to_other_tenants(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY, headers=hdr(tenant, f"k-{uuid.uuid4().hex[:8]}"))
    mid = r.json()["mission_id"]
    client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    job = client.get("/v1/jobs", headers=hdr(tenant)).json()["jobs"][0]
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    assert client.get(f"/v1/jobs/{job['id']}", headers=hdr(other)).status_code == 404


def test_completed_job_cannot_be_replayed(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY, headers=hdr(tenant, f"k-{uuid.uuid4().hex[:8]}"))
    mid = r.json()["mission_id"]
    client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    job = client.get("/v1/jobs", headers=hdr(tenant)).json()["jobs"][0]
    replay = client.post(f"/v1/jobs/{job['id']}/replay", headers=hdr(tenant))
    assert replay.status_code == 409


def test_health_reports_phase2_modes(client, tenant):
    h = client.get("/health").json()
    assert h["phase"] == "7-simulation-worlds"
    assert h["backend"] in ("sqlite", "postgres")
    assert h["worker_mode"] in ("inline", "external")
