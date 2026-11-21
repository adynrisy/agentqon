"""W4 (gap-memory) — memory management API: correct a fact
(PATCH supersedes, old wording kept), delete with propagation
(DELETE returns the invalidated list), and the conflicts surface
(GET /v1/memory/conflicts)."""
from tests.conftest import hdr


def test_memory_correction_and_propagated_delete_via_api(client, tenant):
    r = client.post("/v1/memory", headers=hdr(tenant), json={
        "scope": "user", "scope_ref": "", "topic": "preference:tone",
        "fact": "Prefers concise answers."})
    assert r.status_code == 201, r.text
    first = r.json()
    p = client.patch(f"/v1/memory/{first['memory_id']}",
                     headers=hdr(tenant),
                     json={"fact": "Prefers detailed answers."})
    assert p.status_code == 200, p.text
    assert p.json()["supersedes"] == first["memory_id"]
    listing = client.get("/v1/memory",
                         headers=hdr(tenant)).json()["facts"]
    assert [f["fact"] for f in listing] == ["Prefers detailed answers."]
    full = client.get("/v1/memory?include_superseded=true",
                      headers=hdr(tenant)).json()["facts"]
    assert len(full) == 2
    d = client.delete(f"/v1/memory/{p.json()['memory_id']}",
                      headers=hdr(tenant))
    assert d.status_code == 200
    assert d.json()["removed"] is True
    assert "invalidated" in d.json()
    assert client.get("/v1/memory",
                      headers=hdr(tenant)).json()["facts"] == []
    assert client.patch("/v1/memory/mem_missing", headers=hdr(tenant),
                        json={"fact": "Nope."}).status_code == 404
    conf = client.get("/v1/memory/conflicts", headers=hdr(tenant))
    assert conf.status_code == 200
    assert conf.json()["conflicts"] == []
