"""v13 suitability: the whole roster is the pool; OmniMind ranks a
panel per task type from fit + measured performance, and keeps the
ledger that makes the ranking earned."""
from __future__ import annotations

import copy

from app.suitability import capability_fit, rank_panel, suitability
from tests.conftest import MISSION_BODY, hdr


def _agent(agent_id, quality=3.8, caps=("persona.task", "research.web"),
           lifecycle="active", version="1.0"):
    return {"agent_id": agent_id, "version": version, "quality": quality,
            "capabilities": list(caps), "lifecycle": lifecycle}


def test_division_affinity():
    eng = _agent("agency.engineering.backend-architect")
    mkt = _agent("agency.marketing.content-creator")
    assert capability_fit(eng, "code") == 1.0
    assert capability_fit(eng, "code") > capability_fit(eng, "strategy")
    assert capability_fit(mkt, "writing") > capability_fit(mkt, "code")
    critic = _agent("agent.research.critic", caps=("critique.review",))
    assert capability_fit(critic, "writing") is None  # never drafts


def test_measured_performance_moves_the_ranking():
    rookie = _agent("agency.research.rookie", quality=4.0)
    veteran = _agent("agency.research.veteran", quality=3.6)
    agents = [rookie, veteran]
    # no history: declared quality wins
    panel = rank_panel(agents, "research", [])
    assert panel[0]["agent_id"].endswith("rookie")
    # a proven record (4.9 average, picked in 10 of 12 runs) outweighs it
    stats = [{"agent_version_id": "agency.research.veteran@1.0",
              "task_type": "research", "runs": 12, "eval_sum": 58.8,
              "selections": 10, "critiques_high": 0}]
    panel = rank_panel(agents, "research", stats)
    assert panel[0]["agent_id"].endswith("veteran")
    assert panel[0]["measured_eval"] == 4.9


def test_panel_respects_lifecycle_and_size():
    agents = [_agent(f"agency.research.a{i}", quality=3.0 + i * 0.1)
              for i in range(8)]
    agents[7]["lifecycle"] = "quarantined"
    panel = rank_panel(agents, "research", [])
    assert len(panel) == 5
    assert all(e["agent_id"] != "agency.research.a7" for e in panel)


def test_import_arrives_active_and_panel_drafts(client, tenant):
    r = client.post("/v1/agents/import-agency", headers=hdr(tenant), json={})
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "active"
    # a writing mission now drafts with a PANEL, not the fixed pair
    body = copy.deepcopy(MISSION_BODY)
    body["contract"]["objective"] = "Write a professional email to my landlord about a leaking tap"
    body["contract"]["task_type"] = "writing"
    m = client.post("/v1/missions", headers=hdr(tenant, "suit-0001"), json=body)
    mid = m.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    detail = run.json()
    authors = {c["agent_version_id"] for c in detail["candidates"]}
    assert len(detail["candidates"]) >= 3, authors
    assert len(authors) >= 3  # genuinely different agents drafted
    # the panel pick is on the record, with its reasoning
    panel_events = [e for e in detail["events"] if e["event_type"] == "PanelSelected"]
    assert panel_events and len(panel_events[0]["payload"]["panel"]) >= 3
    # and the ledger learned from the run
    stats = client.get("/v1/agents/stats", headers=hdr(tenant)).json()["stats"]
    writing_stats = [x for x in stats if x["task_type"] == "writing" and x["runs"] >= 1]
    assert len(writing_stats) >= 3
    assert all(x["mean_eval"] is not None for x in writing_stats)
    # suitability endpoint answers "who suits writing, and why"
    rank = client.get("/v1/agents/suitability?task_type=writing",
                      headers=hdr(tenant)).json()
    assert rank["task_type"] == "writing" and len(rank["ranking"]) >= 5
    top = rank["ranking"][0]
    assert {"score", "fit", "prior", "runs"} <= set(top)


def test_selection_feeds_the_ledger(client, tenant):
    body = copy.deepcopy(MISSION_BODY)  # strategy mission, core-only tenant
    m = client.post("/v1/missions", headers=hdr(tenant, "suit-0010"), json=body)
    mid = m.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    cand = run.json()["candidates"][0]
    client.post(f"/v1/missions/{mid}/select", headers=hdr(tenant),
                json={"candidate_id": cand["candidate_id"]})
    stats = client.get("/v1/agents/stats", headers=hdr(tenant)).json()["stats"]
    picked = [x for x in stats
              if x["agent_version_id"] == cand["agent_version_id"]
              and x["task_type"] == "strategy"]
    assert picked and picked[0]["selections"] == 1 and picked[0]["runs"] == 1
