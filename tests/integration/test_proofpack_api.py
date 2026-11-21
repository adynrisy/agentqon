"""Proof Pack endpoint (v15): issued for completed missions only,
stored as artifacts, tenant-scoped."""
import uuid

from tests.conftest import MISSION_BODY, hdr


def _completed_mission(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200 and run.json()["mission"]["status"] == "completed"
    return mid


def test_proof_pack_for_completed_mission(client, tenant):
    mid = _completed_mission(client, tenant)
    r = client.get(f"/v1/missions/{mid}/proof-pack", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack["kind"] == "proof_pack" and pack["mission_id"] == mid
    summary = pack["confidence_summary"]
    assert summary["claims_total"] > 0
    # The niche-first candidate's claims cite retrieved seed sources and
    # pass the citation checks: at least one verified claim must exist.
    assert summary["verified"] >= 1, summary
    labels = {c["label"] for c in pack["candidates"] for c in c["claims"]}
    assert labels <= {"verified", "estimated", "assumed"}
    # every cited source resolves to a retrieved record with provenance
    assert pack["sources"], "expected cited sources in the pack"
    assert all(s["retrieved"] for s in pack["sources"])
    assert all(s["title"] for s in pack["sources"])
    # stored through the artifact store, linked from the response
    art = client.get(f"/v1/artifacts/{pack['artifact']['artifact_id']}",
                     headers=hdr(tenant))
    assert art.status_code == 200
    assert art.json()["content"]["kind"] == "proof_pack"
    md_art = client.get(f"/v1/artifacts/{pack['markdown_artifact']['artifact_id']}",
                        headers=hdr(tenant))
    assert "Proof Pack" in md_art.json()["content"]
    assert "Proof Pack" in pack["markdown"]


def test_proof_pack_requires_completed_mission(client, tenant):
    r = client.post("/v1/missions", json=MISSION_BODY,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    mid = r.json()["mission_id"]  # draft, never run
    resp = client.get(f"/v1/missions/{mid}/proof-pack", headers=hdr(tenant))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STATE_CONFLICT"


def test_proof_pack_tenant_isolation(client, tenant):
    mid = _completed_mission(client, tenant)
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    assert client.get(f"/v1/missions/{mid}/proof-pack",
                      headers=hdr(other)).status_code == 404
    assert client.get("/v1/missions/mis_missing/proof-pack",
                      headers=hdr(tenant)).status_code == 404
