"""V3 harness: pause/resume, revision rounds, export, kill switch,
projects (Master Build §7/§16). Runs on the tests-only stub model —
deterministic."""
import uuid

from app.main import store
from tests.conftest import MISSION_BODY, hdr


def _mission(client, tenant) -> str:
    r = client.post("/v1/missions", json=MISSION_BODY,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 201, r.text
    return r.json()["mission_id"]


def test_pause_resume_cycle(client, tenant):
    mid = _mission(client, tenant)
    # Force a running state (inline runs complete too fast to pause live).
    store.set_mission_status(tenant, mid, "planned")
    store.set_mission_status(tenant, mid, "running")
    r = client.post(f"/v1/missions/{mid}/pause", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert client.post(f"/v1/missions/{mid}/pause", headers=hdr(tenant)).status_code == 409
    r = client.post(f"/v1/missions/{mid}/resume", headers=hdr(tenant))
    assert r.status_code == 200
    detail = r.json()
    assert detail["mission"]["status"] == "completed"
    types = [e["event_type"] for e in detail["events"]]
    assert "MissionPaused" in types


def test_resume_with_live_requeued_job_still_runs_inline(client, tenant):
    """Audit R regression: the state a REAL mid-run pause leaves behind
    is a paused mission + a still-queued (requeued) job. In inline mode
    this process is the only worker, so resume must execute that job —
    answering 'running' without executing stranded the mission in
    'running' forever (live repro on a real gateway run)."""
    mid = _mission(client, tenant)
    store.set_mission_status(tenant, mid, "planned")
    store.set_mission_status(tenant, mid, "running")
    r = client.post(f"/v1/missions/{mid}/pause", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["status"] == "paused"
    job = store.enqueue_job(tenant, mid, kind="mission_run")
    assert job["status"] == "queued"
    r = client.post(f"/v1/missions/{mid}/resume", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["mission"]["status"] == "completed"
    assert detail["candidates"], "the resumed run must produce candidates"
    assert store.get_job(tenant, job["id"])["status"] == "completed"


def test_resume_with_job_running_under_another_holder_returns_200(client, tenant):
    """Bug-1 (retest 2) regression: resume must not 500 when another
    legitimate holder already holds the mission_run job (an external
    worker, or the still-running original drive) — the mission IS
    resuming under that holder, so answer with its detail and start
    no duplicate drive."""
    mid = _mission(client, tenant)
    store.set_mission_status(tenant, mid, "planned")
    store.set_mission_status(tenant, mid, "running")
    r = client.post(f"/v1/missions/{mid}/pause", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["status"] == "paused"
    job = store.enqueue_job(tenant, mid, kind="mission_run")
    claimed = store.claim_job_by_id("external-worker", job["id"])
    assert claimed is not None and claimed["status"] == "running"
    r = client.post(f"/v1/missions/{mid}/resume", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["mission"]["status"] == "running"
    held = store.get_job(tenant, job["id"])
    assert held["status"] == "running"
    assert held["lease_owner"] == "external-worker"
    types = [e["event_type"] for e in detail["events"]]
    assert types.count("MissionResumed") == 1
    assert "MissionPlanned" not in types  # no duplicate drive started here


def test_resume_with_job_queued_but_not_yet_due_drives_inline(client, tenant):
    """Bug-1 second shape (retest 2): a resume landing inside the pause
    requeue delay must not 500 on the not-yet-due job — it waits out
    the delay, claims, and drives inline to completion."""
    mid = _mission(client, tenant)
    store.set_mission_status(tenant, mid, "planned")
    store.set_mission_status(tenant, mid, "running")
    r = client.post(f"/v1/missions/{mid}/pause", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["status"] == "paused"
    job = store.enqueue_job(tenant, mid, kind="mission_run")
    assert store.claim_job_by_id("external-worker", job["id"]) is not None
    store.requeue_job(job["id"], 6, refund_attempt=True, owner="external-worker")
    assert store.get_job(tenant, job["id"])["status"] == "queued"
    r = client.post(f"/v1/missions/{mid}/resume", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["mission"]["status"] == "completed"
    assert detail["candidates"], "the resumed run must produce candidates"
    assert store.get_job(tenant, job["id"])["status"] == "completed"


def test_replan_supersedes_previous_attempts_open_tasks(client, tenant):
    """Audit R regression (bug #3 sibling): re-running a mission whose
    previous attempt left open tasks must not leave a phantom 'running'
    task behind — the re-plan closes the old attempt's open tasks."""
    mid = _mission(client, tenant)
    store.create_task(tenant, {
        "task_id": "tsk_stale_research", "mission_id": mid,
        "kind": "research", "status": "running", "title": "Research",
        "description": "stale", "required_capabilities": [],
        "output_schema_version": "v1"})
    r = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["mission"]["status"] == "completed"
    stale = [t for t in detail["tasks"] if t["id"] == "tsk_stale_research"]
    assert stale and stale[0]["status"] == "cancelled"
    open_tasks = [t for t in detail["tasks"]
                  if t["status"] in ("queued", "running")]
    assert not open_tasks, f"no task may stay open on a completed mission: {open_tasks}"


def test_revision_round_creates_versioned_candidate(client, tenant):
    mid = _mission(client, tenant)
    detail = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant)).json()
    original = detail["candidates"][0]
    r = client.post(f"/v1/missions/{mid}/revise", headers=hdr(tenant),
                    json={"candidate_id": original["candidate_id"],
                          "instructions": "Tighten the evidence."})
    assert r.status_code == 200, r.text
    revised = [c for c in r.json()["candidates"] if c.get("revises") == original["candidate_id"]]
    assert len(revised) == 1
    assert revised[0]["version"] == 2
    assert revised[0]["candidate_id"] != original["candidate_id"]
    types = [e["event_type"] for e in r.json()["events"]]
    assert "RevisionRequested" in types and "CandidateRevised" in types
    # Bounded: a second revision of the original is allowed (2 total)...
    r2 = client.post(f"/v1/missions/{mid}/revise", headers=hdr(tenant),
                     json={"candidate_id": original["candidate_id"], "instructions": "again"})
    assert r2.status_code == 200
    # ...the third is refused.
    r3 = client.post(f"/v1/missions/{mid}/revise", headers=hdr(tenant),
                     json={"candidate_id": original["candidate_id"], "instructions": "third"})
    assert r3.status_code == 409


def test_export_markdown_and_json(client, tenant):
    mid = _mission(client, tenant)
    client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    r = client.get(f"/v1/missions/{mid}/export", headers=hdr(tenant))
    assert r.status_code == 200
    assert "Candidate" in r.text and "Usage (known costs)" in r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    r = client.get(f"/v1/missions/{mid}/export?format=json", headers=hdr(tenant))
    assert r.json()["mission"]["id"] == mid


def test_projects_and_mission_assignment(client, tenant):
    r = client.post("/v1/projects", json={"name": "Launch research"}, headers=hdr(tenant))
    assert r.status_code == 201
    project = r.json()
    body = {**MISSION_BODY, "project_id": project["id"]}
    r = client.post("/v1/missions", json=body,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 201
    detail = client.get(f"/v1/missions/{r.json()['mission_id']}",
                        headers=hdr(tenant)).json()
    assert detail["mission"]["project_id"] == project["id"]
    bad = {**MISSION_BODY, "project_id": "prj_nope"}
    r = client.post("/v1/missions", json=bad, headers=hdr(tenant, "key-bad-proj-1"))
    assert r.status_code == 400


def test_kill_switch_blocks_runs(client, tenant, monkeypatch):
    import dataclasses

    from app import main
    monkeypatch.setattr(main, "settings",
                        dataclasses.replace(main.settings, admin_key="test-admin-key"))
    admin = {**hdr(tenant), "X-Admin-Key": "test-admin-key"}
    try:
        assert client.post("/v1/admin/kill-switch", json={"on": True},
                           headers={**hdr(tenant), "X-Admin-Key": "nope"}).status_code == 403
        r = client.post("/v1/admin/kill-switch", json={"on": True}, headers=admin)
        assert r.json()["kill_switch"] == "on"
        mid = _mission(client, tenant)
        r = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "KILL_SWITCH_ACTIVE"
        overview = client.get("/v1/admin/overview", headers=admin).json()
        assert overview["kill_switch"] == "on" and "jobs_by_status" in overview
    finally:
        client.post("/v1/admin/kill-switch", json={"on": False}, headers=admin)
