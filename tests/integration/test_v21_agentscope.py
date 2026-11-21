"""v21 Track 7 — AgentScope patterns: (a) mode phases share ONE
broadcast mission channel (a channel artifact + a ModeChannelPublished
event, phase summaries visible to every later role), and (b) mission
events carry observability spans — one trace id across plan →
research → critique → evaluation, a span id per stage event."""
from __future__ import annotations

import copy

from tests.conftest import MISSION_BODY, hdr


def _run(client, tenant, key, contract_extra=None, objective=None):
    body = copy.deepcopy(MISSION_BODY)
    if objective:
        body["contract"]["objective"] = objective
        body["title"] = objective[:60]
    if contract_extra:
        body["contract"].update(contract_extra)
    r = client.post("/v1/missions", headers=hdr(tenant, key), json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    return client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()


def test_standard_mission_events_share_one_trace(client, tenant):
    d = _run(client, tenant, "v21scope-std1")
    traced = [e for e in d["events"] if "trace_id" in e["payload"]]
    assert traced, "expected traced stage events"
    trace_ids = {e["payload"]["trace_id"] for e in traced}
    assert len(trace_ids) == 1  # ONE trace across the whole run
    spans = {e["payload"]["span"] for e in traced}
    assert {"plan", "research", "critique", "evaluation"} <= spans
    assert all(e["payload"].get("span_id") for e in traced)
    by_type = {e["event_type"]: e["payload"]["span"] for e in traced}
    assert by_type["MissionPlanned"] == "plan"
    assert by_type["CandidateProposed"] == "research"
    assert by_type["CritiqueRoundCompleted"] == "critique"
    assert by_type["EvaluationCompleted"] == "evaluation"


def test_mode_mission_broadcast_channel_and_spans(client, tenant):
    d = _run(client, tenant, "v21scope-mode1",
             {"mode": "society"},
             "Organise the team's shared drive into a sane folder structure")
    assert d["mission"]["status"] == "completed"
    phase_events = [e for e in d["events"]
                    if e["event_type"] == "ModePhaseCompleted"]
    assert len(phase_events) == 4
    assert all(e["payload"]["span"] == "mode_phase" for e in phase_events)
    assert len({e["payload"]["trace_id"] for e in phase_events}) == 1
    published = [e for e in d["events"]
                 if e["event_type"] == "ModeChannelPublished"]
    assert published and published[0]["payload"]["entries"] == 4
    channel_arts = [a for a in d["artifacts"] if a["kind"] == "mode_channel"]
    assert len(channel_arts) == 1
    art = client.get(f"/v1/artifacts/{channel_arts[0]['id']}",
                     headers=hdr(tenant)).json()
    entries = art["content"]["entries"]
    assert [e["phase"] for e in entries] == \
        ["framing", "execution", "challenge", "delivery"]
    assert all(e["agent"].startswith("pack.camel.") for e in entries)
    assert all(e["summary"] for e in entries)  # every entry is a real summary
