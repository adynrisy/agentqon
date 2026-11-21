"""Agency import endpoint: admin-gated, installs into the caller's
workspace only."""
from __future__ import annotations

from tests.conftest import hdr


def test_import_endpoint_admin_only_and_scoped(client, tenant):
    # dev-mode identity is owner, so this exercises the happy path;
    # role enforcement itself is covered by the Phase 4 auth tests.
    r = client.post("/v1/agents/import-agency", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 275 and body["lifecycle"] == "active"
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    agency = [a for a in agents if a["agent_id"].startswith("agency.")]
    assert len(agency) == body["total"]
    # v20.1: a fresh tenant no longer starts empty — first touch
    # auto-seeds its OWN copy of the roster. Scoping still holds: the
    # other tenant's rows are its own, independent of this tenant's
    # import and of this tenant's lifecycle decisions.
    other = client.get("/v1/agents", headers=hdr("org_noimport")).json()["agents"]
    other_agency = [a for a in other if a["agent_id"].startswith("agency.")]
    assert len(other_agency) == body["total"]
    victim = agency[0]
    client.post(f"/v1/agents/{victim['agent_version_id']}/lifecycle",
                json={"lifecycle": "quarantined"}, headers=hdr(tenant))
    other_copy = next(a for a in client.get(
        "/v1/agents", headers=hdr("org_noimport")).json()["agents"]
        if a["agent_id"] == victim["agent_id"])
    assert other_copy["lifecycle"] == "active"
