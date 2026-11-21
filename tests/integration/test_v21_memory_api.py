"""v21 Track 3 — memory API: CRUD with role rules, tenant isolation,
and the mission round-trip (a completed mission extracts facts; the
next mission's run reports the injected context in its events)."""
from __future__ import annotations

import copy

from tests.conftest import MISSION_BODY, hdr


def test_memory_crud_and_supersede_via_api(client, tenant):
    r = client.post("/v1/memory", headers=hdr(tenant), json={
        "scope": "user", "scope_ref": "", "topic": "preference:tone",
        "fact": "Prefers concise answers."})
    assert r.status_code == 201, r.text
    first = r.json()
    r2 = client.post("/v1/memory", headers=hdr(tenant), json={
        "scope": "user", "scope_ref": "", "topic": "preference:tone",
        "fact": "Prefers detailed answers with examples."})
    assert r2.status_code == 201
    assert r2.json()["supersedes"] == first["memory_id"]
    listing = client.get("/v1/memory", headers=hdr(tenant)).json()["facts"]
    assert [f["fact"] for f in listing] == \
        ["Prefers detailed answers with examples."]
    full = client.get("/v1/memory?include_superseded=true",
                      headers=hdr(tenant)).json()["facts"]
    assert len(full) == 2
    d = client.delete(f"/v1/memory/{first['memory_id']}", headers=hdr(tenant))
    assert d.status_code == 200
    d2 = client.delete("/v1/memory/mem_missing", headers=hdr(tenant))
    assert d2.status_code == 404


def test_memory_is_tenant_isolated_via_api(client, tenant):
    client.post("/v1/memory", headers=hdr(tenant), json={
        "scope": "user", "scope_ref": "", "topic": "note",
        "fact": "Tenant A only."})
    other = client.get("/v1/memory",
                       headers=hdr("org_someone_else")).json()["facts"]
    assert other == []


def test_mission_completion_extracts_memory(client, tenant):
    body = copy.deepcopy(MISSION_BODY)
    r = client.post("/v1/missions", headers=hdr(tenant, "v21mem-run1"),
                    json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    detail = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "completed"
    kinds = [e["event_type"] for e in detail["events"]]
    assert "MemoryExtracted" in kinds
    facts = client.get("/v1/memory", headers=hdr(tenant)).json()["facts"]
    scopes = {f["scope"] for f in facts}
    assert scopes == {"user", "agent", "session"}
    session = client.get(f"/v1/memory?scope=session&scope_ref={mid}",
                         headers=hdr(tenant)).json()["facts"]
    assert len(session) == 1 and session[0]["topic"] == "summary"

    # Second mission: the stored memory is injected as context, and
    # its own outcome supersedes the first mission's user-scope fact.
    body2 = copy.deepcopy(MISSION_BODY)
    body2["title"] = "Compare launch strategies again"
    r = client.post("/v1/missions", headers=hdr(tenant, "v21mem-run2"),
                    json=body2)
    mid2 = r.json()["mission_id"]
    client.post(f"/v1/missions/{mid2}/run", headers=hdr(tenant), json={})
    detail2 = client.get(f"/v1/missions/{mid2}", headers=hdr(tenant)).json()
    kinds2 = [e["event_type"] for e in detail2["events"]]
    assert "MemoryContextInjected" in kinds2
    outcomes = client.get("/v1/memory?scope=user", headers=hdr(tenant)
                          ).json()["facts"]
    assert len(outcomes) == 1  # superseded, not accumulated
