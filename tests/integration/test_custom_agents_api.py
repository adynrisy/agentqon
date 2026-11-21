"""v21.2 custom agents: workspace-authored agents are first-class
registry citizens — CRUD via /v1/agents/custom, tenant-scoped, ranked
by the suitability layer for their declared task types, eligible for
mission panels, and written into the performance ledger when they run.
"""
from __future__ import annotations

import copy

from app.main import store as app_store
from app.suitability import capability_fit, rank_panel, suitability
from tests.conftest import MISSION_BODY, hdr

BODY = {
    "name": "Grant Sleuth",
    "title": "Funding researcher",
    "description": "Finds grants and deadlines",
    "instructions": ("You hunt down grant opportunities, verify every "
                     "deadline from a primary source, and rank options "
                     "by fit for the applicant."),
    "task_types": ["qa", "research"],
}
AGENT_ID = "custom.grant-sleuth"
AVID = "custom.grant-sleuth@1.0.0"


def _create(client, tenant, body=None):
    return client.post("/v1/agents/custom", headers=hdr(tenant),
                       json=body or BODY)


# ------------------------------------------------------------ CRUD

def test_custom_agent_crud_roundtrip(client, tenant):
    r = _create(client, tenant)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["agent_id"] == AGENT_ID
    assert created["agent_version_id"] == AVID
    assert created["origin"] == "custom"
    assert created["lifecycle"] == "active"  # standing order: active on arrival
    assert created["name"] == "Grant Sleuth"
    assert created["title"] == "Funding researcher"
    assert created["task_types"] == ["qa", "research"]
    # roster-import priors: declared, unmeasured, below the curated core
    assert created["quality"] == 3.8 and created["reliability"] == 0.90
    # the brief landed in the tenant's persona scope, composed for the LLM
    persona = app_store.get_persona(AGENT_ID, tenant)
    assert persona is not None and "Role: Funding researcher" in persona
    assert "verify every" in persona

    # listed both as a custom agent and in the whole-registry view
    mine = client.get("/v1/agents/custom", headers=hdr(tenant)).json()
    assert mine["total"] == 1 and mine["agents"][0]["agent_id"] == AGENT_ID
    everyone = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    assert any(a["agent_id"] == AGENT_ID for a in everyone)

    # update refreshes fields + brief
    r = client.put(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant),
                   json={**BODY, "title": "Senior funding researcher",
                         "instructions": "You verify deadlines twice."})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Senior funding researcher"
    assert "verify deadlines twice" in app_store.get_persona(AGENT_ID, tenant)

    # delete removes the manifest, the ledger rows and the brief
    r = client.delete(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["deleted"] == AGENT_ID
    assert client.get(f"/v1/agents/custom/{AGENT_ID}",
                      headers=hdr(tenant)).status_code == 404
    everyone = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    assert not any(a["agent_id"] == AGENT_ID for a in everyone)
    assert app_store.get_persona(AGENT_ID, tenant) is None


def test_custom_agent_edit_preserves_lifecycle(client, tenant):
    _create(client, tenant)
    r = client.post(f"/v1/agents/{AVID}/lifecycle", headers=hdr(tenant),
                    json={"lifecycle": "quarantined"})
    assert r.status_code == 200, r.text
    r = client.put(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant),
                   json={**BODY, "title": "Edited while benched"})
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "quarantined"  # the bench survives an edit


def test_custom_agent_validation_is_human(client, tenant):
    def expect_400(body, needle):
        r = client.post("/v1/agents/custom", headers=hdr(tenant), json=body)
        assert r.status_code == 400, r.text
        msg = r.json()["error"]["message"]
        assert needle in msg.lower(), msg

    expect_400({**BODY, "name": "  "}, "name")
    expect_400({**BODY, "name": "!!!"}, "letter or digit")
    expect_400({**BODY, "instructions": " "}, "instructions")
    expect_400({**BODY, "instructions": "x" * 20_001}, "limit is 20000")
    expect_400({**BODY, "task_types": ["telepathy"]}, "unknown task type")
    r = _create(client, tenant)
    assert r.status_code == 201, r.text
    r = _create(client, tenant)
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["error"]["message"]


def test_custom_agents_are_tenant_scoped(client, tenant):
    _create(client, tenant)
    other = f"{tenant}_other"
    assert client.get("/v1/agents/custom",
                      headers=hdr(other)).json()["total"] == 0
    assert client.get(f"/v1/agents/custom/{AGENT_ID}",
                      headers=hdr(other)).status_code == 404
    assert client.put(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(other),
                      json=BODY).status_code == 404
    assert client.delete(f"/v1/agents/custom/{AGENT_ID}",
                         headers=hdr(other)).status_code == 404
    # bundled roster agents are not editable through the custom surface
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    bundled = next(a for a in agents if a["agent_id"].startswith("agency."))
    assert client.delete(f"/v1/agents/custom/{bundled['agent_id']}",
                         headers=hdr(tenant)).status_code == 404


# ------------------------------------------------- suitability + panel

def test_custom_agent_fit_uses_declared_task_types():
    manifest = {"agent_id": AGENT_ID, "version": "1.0.0", "quality": 3.8,
                "capabilities": ["persona.task", "research.web"],
                "lifecycle": "active", "task_types": ["qa", "research"]}
    assert capability_fit(manifest, "qa") == 1.0       # declared turf
    assert capability_fit(manifest, "research") == 1.0
    assert capability_fit(manifest, "general") == 0.6  # generalist floor
    assert capability_fit(manifest, "code") == 0.45    # eligible, not favoured
    panel = rank_panel([manifest], "qa", [])
    assert panel and panel[0]["agent_id"] == AGENT_ID
    assert suitability(manifest, "qa", None)["score"] > 0.89


def test_custom_agent_ranked_by_suitability_endpoint(client, tenant):
    _create(client, tenant)
    rank = client.get("/v1/agents/suitability?task_type=qa",
                      headers=hdr(tenant)).json()
    hit = [e for e in rank["ranking"] if e["agent_id"] == AGENT_ID]
    assert hit and hit[0]["fit"] == 1.0, rank["ranking"][:3]


def test_custom_agent_runs_on_panel_and_accrues_ledger(client, tenant):
    _create(client, tenant)
    # the orchestrator injects the brief at the LLM seam, like a roster
    # persona — the instructions are what the agent actually works from
    from app.main import orchestrator
    manifest = app_store.get_agent(tenant, AGENT_ID)
    enriched = orchestrator._with_persona(manifest, tenant)
    assert "verify every" in enriched.get("persona_prompt", "")

    body = copy.deepcopy(MISSION_BODY)
    body["contract"]["objective"] = "What grants exist for a small bakery?"
    body["contract"]["task_type"] = "qa"
    m = client.post("/v1/missions", headers=hdr(tenant, "custom-qa-1"),
                    json=body)
    assert m.status_code in (200, 201), m.text
    mid = m.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    detail = run.json()
    authors = {c["agent_version_id"] for c in detail["candidates"]}
    assert AVID in authors, authors
    panel_events = [e for e in detail["events"]
                    if e["event_type"] == "PanelSelected"]
    assert panel_events and any(
        p["agent"] == AGENT_ID for p in panel_events[0]["payload"]["panel"])
    # the performance ledger learned from the run
    stats = client.get("/v1/agents/stats", headers=hdr(tenant)).json()["stats"]
    mine = [x for x in stats if x["agent_version_id"] == AVID
            and x["task_type"] == "qa"]
    assert mine and mine[0]["runs"] >= 1 and mine[0]["mean_eval"] is not None
