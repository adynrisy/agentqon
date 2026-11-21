"""API end-to-end tests (Ch.18, 21): the full Phase 1 slice through HTTP."""
import uuid

from tests.conftest import MISSION_BODY, hdr


def create(client, tenant, body=None):
    return client.post("/v1/missions", json=body or MISSION_BODY,
                       headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))


def test_full_mission_flow(client, tenant):
    r = create(client, tenant)
    assert r.status_code == 201
    mid = r.json()["mission_id"]

    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200
    d = run.json()
    assert d["mission"]["status"] == "completed"
    assert d["mission"]["spent"] <= d["mission"]["budget_limit"]
    assert [t["status"] for t in d["tasks"]] == ["completed"] * 3
    # v20.1: a fresh workspace carries the full roster, so the drafting
    # panel is the two seed researchers PLUS the best-suited roster
    # agents (5 candidates, each from a different agent) — this test
    # was written when a tenant held only the 3 seed agents.
    assert len(d["candidates"]) == 5
    authors = {c["agent_version_id"] for c in d["candidates"]}
    assert len(authors) == 5
    strategies = [c["strategy"] for c in d["candidates"]]
    assert "niche-first" in strategies and "partnership-led" in strategies
    assert len(d["evaluations"]) == len(d["candidates"])
    assert len(d["critiques"]) >= 1
    assert d["usage"]["total_usd"] > 0
    assert d["events"][-1]["event_type"] == "MissionCompleted"
    # Evidence quality must actually differentiate the candidates:
    # the partnership candidate carries the contested opinion-backed claim.
    ev = {e["candidate_id"]: e for e in d["evaluations"]}
    by_strategy = {c["strategy"]: ev[c["candidate_id"]] for c in d["candidates"]
                   if c["strategy"] in ("niche-first", "partnership-led")}
    assert by_strategy["partnership-led"]["dimensions"]["evidence"]["score"] < \
        by_strategy["niche-first"]["dimensions"]["evidence"]["score"]

    cand = d["candidates"][0]["candidate_id"]
    sel = client.post(f"/v1/missions/{mid}/select", json={"candidate_id": cand}, headers=hdr(tenant))
    assert sel.status_code == 200 and sel.json()["selected_candidate_id"] == cand


def test_mission_without_any_budget_field_runs_end_to_end(client, tenant):
    # v21.3: budgets are not user-facing. A create body with no
    # constraints / budget anywhere must be accepted, run to
    # completion, and never surface a budget block — while spend
    # accounting keeps recording.
    from app.domain import MISSION_BUDGET_CEILING_USD
    body = {"title": "No-budget mission",
            "contract": {
                "objective": "Compare launch strategies for a new SMB invoicing product",
                "deliverables": ["Decision brief"],
                "acceptance": ["Material claims cite sources"],
                "tools": {"allow": ["web.read", "artifact.write"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal"}}
    r = client.post("/v1/missions", json=body,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200, run.text
    d = run.json()
    assert d["mission"]["status"] == "completed"
    # The stored limit is the internal runaway ceiling, not a user value.
    assert d["mission"]["budget_limit"] == MISSION_BUDGET_CEILING_USD
    assert d["usage"]["total_usd"] > 0  # accounting still recorded
    assert not [e for e in d["events"] if e["event_type"] == "BudgetExhausted"]


def test_idempotency_replay_and_conflict(client, tenant):
    key = f"key-{uuid.uuid4().hex[:10]}"
    r1 = client.post("/v1/missions", json=MISSION_BODY, headers=hdr(tenant, key))
    r2 = client.post("/v1/missions", json=MISSION_BODY, headers=hdr(tenant, key))
    assert r1.json()["mission_id"] == r2.json()["mission_id"]
    assert r2.json().get("idempotent_replay") is True
    changed = dict(MISSION_BODY, title="A different title")
    r3 = client.post("/v1/missions", json=changed, headers=hdr(tenant, key))
    assert r3.status_code == 409


def test_cross_tenant_isolation(client, tenant):
    mid = create(client, tenant).json()["mission_id"]
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    assert client.get(f"/v1/missions/{mid}", headers=hdr(other)).status_code == 404
    assert client.post(f"/v1/missions/{mid}/run", headers=hdr(other)).status_code == 404
    listed = client.get("/v1/missions", headers=hdr(other)).json()["missions"]
    assert all(m["id"] != mid for m in listed)


def test_unknown_tool_name_rejected(client, tenant):
    body = {"title": "X", "contract": {"objective": "Y", "deliverables": ["d"],
                                       "acceptance": ["a"], "tools": {"allow": ["teleport"]}}}
    r = client.post("/v1/missions", json=body, headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_quarantined_agent_gets_no_assignments(client, tenant):
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    alpha = next(a for a in agents if a["agent_id"] == "agent.research.alpha")
    avid = alpha["agent_version_id"]
    q = client.post(f"/v1/agents/{avid}/lifecycle", json={"lifecycle": "quarantined"}, headers=hdr(tenant))
    assert q.status_code == 200
    # v20.1: the full roster is auto-seeded, so other research agents
    # remain and the run still completes — the enforcement property
    # this test guards is that the QUARANTINED agent is never picked:
    # not in the panel, and author of no candidate. (In the seed-only
    # era the same quarantine left too few agents and the run failed
    # with 500; that premise no longer exists.)
    mid = create(client, tenant).json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200
    d = run.json()
    assert d["mission"]["status"] == "completed"
    assert all(c["agent_version_id"] != avid for c in d["candidates"])
    panel_events = [e for e in d["events"] if e["event_type"] == "PanelSelected"]
    assert panel_events and all(
        p["agent"] != "agent.research.alpha"
        for p in panel_events[0]["payload"]["panel"])
    # Restore through the legal lifecycle path: quarantined -> testing -> approved -> active.
    for step in ("testing", "approved", "active"):
        rr = client.post(f"/v1/agents/{avid}/lifecycle", json={"lifecycle": step}, headers=hdr(tenant))
        assert rr.status_code == 200, step
    mid2 = create(client, tenant).json()["mission_id"]
    assert client.post(f"/v1/missions/{mid2}/run", headers=hdr(tenant)).status_code == 200


def test_illegal_lifecycle_jump_rejected(client, tenant):
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    avid = agents[0]["agent_version_id"]
    r = client.post(f"/v1/agents/{avid}/lifecycle", json={"lifecycle": "retired"}, headers=hdr(tenant))
    assert r.status_code == 409  # active -> retired is not a legal transition


def test_cancel_draft_mission(client, tenant):
    mid = create(client, tenant).json()["mission_id"]
    r = client.post(f"/v1/missions/{mid}/cancel", json={}, headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # Terminal: cannot cancel again, cannot run.
    assert client.post(f"/v1/missions/{mid}/cancel", json={}, headers=hdr(tenant)).status_code == 409
