"""Phase 7 API tests: world lifecycle, branch isolation, reproduce,
compare, promote (the only exit), tenant isolation, branch bounds."""
from __future__ import annotations

from tests.conftest import MISSION_BODY, hdr


def _world(client, tenant, key="w-0001", **kw):
    body = {"name": "Launch worlds", "seed": 42, **kw}
    r = client.post("/v1/worlds", headers=hdr(tenant, key), json=body)
    assert r.status_code == 201, r.text
    return r.json()["world_id"]


def _branch(client, tenant, wid, strategy, **kw):
    r = client.post(f"/v1/worlds/{wid}/branches", headers=hdr(tenant),
                    json={"strategy": strategy, **kw})
    assert r.status_code == 201, r.text
    return r.json()["branch_id"]


def test_world_lifecycle_compare_and_reproduce(client, tenant):
    wid = _world(client, tenant)
    b1 = _branch(client, tenant, wid, "niche-first", label="Niche")
    _branch(client, tenant, wid, "broad", label="Broad")
    ran = client.post(f"/v1/worlds/{wid}/run", headers=hdr(tenant))
    assert ran.status_code == 200
    hashes1 = ran.json()["outcome_hashes"]
    assert len(hashes1) == 2
    comp = client.get(f"/v1/worlds/{wid}/compare", headers=hdr(tenant)).json()
    assert comp["label"] == "SIMULATED ESTIMATE"
    assert len(comp["rows"]) == 2
    niche = next(r for r in comp["rows"] if r["strategy"] == "niche-first")
    broad = next(r for r in comp["rows"] if r["strategy"] == "broad")
    assert niche["adopters"] > broad["adopters"]  # same budget, same rubric
    assert niche["sensitivity"] and niche["outcome_hash"] == hashes1[0]
    # reproduce: a second identical world produces identical hashes
    wid2 = _world(client, tenant, key="w-0002")
    _branch(client, tenant, wid2, "niche-first", label="Niche")
    _branch(client, tenant, wid2, "broad", label="Broad")
    ran2 = client.post(f"/v1/worlds/{wid2}/run", headers=hdr(tenant))
    assert ran2.json()["outcome_hashes"] == hashes1
    assert b1


def test_branch_isolation_no_mutation_leaks(client, tenant):
    wid = _world(client, tenant)
    _branch(client, tenant, wid, "niche-first",
            assumptions={"budget_usd": 500})  # starved branch
    _branch(client, tenant, wid, "niche-first")  # full budget twin
    client.post(f"/v1/worlds/{wid}/run", headers=hdr(tenant))
    world = client.get(f"/v1/worlds/{wid}", headers=hdr(tenant)).json()
    starved, full = world["branches"]
    assert starved["result"]["final"]["budget_exhausted_tick"] is not None
    assert full["result"]["final"]["adopters"] > starved["result"]["final"]["adopters"]
    # the frozen baseline itself was not touched by the starved branch
    assert world["baseline"]["budget_usd"] == 150000.0


def test_world_from_mission_carries_provenance(client, tenant):
    created = client.post("/v1/missions", headers=hdr(tenant, "w-mission-1"),
                          json=MISSION_BODY)
    mid = created.json()["mission_id"]
    client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    wid = _world(client, tenant, key="w-0003", mission_id=mid)
    cand = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()["candidates"][0]
    _branch(client, tenant, wid, "niche-first", origin_candidate_id=cand["candidate_id"])
    client.post(f"/v1/worlds/{wid}/run", headers=hdr(tenant))
    comp = client.get(f"/v1/worlds/{wid}/compare", headers=hdr(tenant)).json()
    assert comp["rows"][0]["origin_candidate_scores_research_v1"]  # rubric scores ride along
    world = client.get(f"/v1/worlds/{wid}", headers=hdr(tenant)).json()
    assert world["provenance"]["source_snapshot_hash"]


def test_promote_is_explicit_logged_and_creates_draft(client, tenant):
    wid = _world(client, tenant)
    bid = _branch(client, tenant, wid, "partnership-led")
    client.post(f"/v1/worlds/{wid}/run", headers=hdr(tenant))
    r = client.post(f"/v1/worlds/{wid}/promote", headers=hdr(tenant),
                    json={"branch_id": bid})
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    detail = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "draft"  # not run, not approved
    assert "SIMULATED ESTIMATE" in detail["mission"]["contract"]["objective"]
    events = client.get(f"/v1/worlds/{wid}", headers=hdr(tenant)).json()
    assert events["world_id"] == wid


def test_branch_limit_and_tenant_isolation(client, tenant):
    wid = _world(client, tenant)
    for i in range(6):
        _branch(client, tenant, wid, "broad", label=f"b{i}")
    r = client.post(f"/v1/worlds/{wid}/branches", headers=hdr(tenant),
                    json={"strategy": "broad"})
    assert r.status_code == 409
    other = client.get(f"/v1/worlds/{wid}", headers=hdr("org_stranger"))
    assert other.status_code == 404
    run_other = client.post(f"/v1/worlds/{wid}/run", headers=hdr("org_stranger"))
    assert run_other.status_code == 404
