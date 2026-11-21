"""v21.4.2 workstream B: GET /v1/agents/version — the cheap roster
freshness probe behind the UI's on-device roster cache.

The token must be stable while the registry is unchanged and must
change on every registry mutation (custom-agent create/edit/delete),
so a warm cache is never silently stale.
"""
from tests.conftest import hdr

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


def _version(client, tenant):
    r = client.get("/v1/agents/version", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    return r.json()


def test_version_is_stable_and_roster_sized(client, tenant):
    a = _version(client, tenant)
    b = _version(client, tenant)
    assert a == b, "no mutation between probes → identical token"
    assert a["total"] > 6000, "the seeded roster (6,799 agents)"
    assert a["custom"] == 0
    assert a["version"]


def test_version_changes_on_custom_agent_create_edit_delete(client, tenant):
    v0 = _version(client, tenant)

    r = client.post("/v1/agents/custom", headers=hdr(tenant), json=BODY)
    assert r.status_code in (200, 201), r.text
    v1 = _version(client, tenant)
    assert v1["version"] != v0["version"], "create must move the token"
    assert v1["custom"] == v0["custom"] + 1
    assert v1["total"] == v0["total"] + 1

    edited = dict(BODY, title="Funding researcher II")
    r = client.put(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant), json=edited)
    assert r.status_code == 200, r.text
    v2 = _version(client, tenant)
    assert v2["version"] != v1["version"], "edit must move the token"
    assert v2["custom"] == v1["custom"]

    r = client.delete(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    v3 = _version(client, tenant)
    assert v3["version"] != v2["version"], "delete must move the token"
    assert v3["custom"] == v0["custom"]
    assert v3["total"] == v0["total"]


def test_version_is_tenant_scoped(client, tenant):
    other = tenant + "_other"
    before = _version(client, other)
    r = client.post("/v1/agents/custom", headers=hdr(tenant), json=BODY)
    assert r.status_code in (200, 201), r.text
    after = _version(client, other)
    assert after["custom"] == before["custom"], \
        "another tenant's registry must not move this tenant's token"
    client.delete(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant))
