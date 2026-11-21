"""Data-grounded simulation worlds: parameters are mined from the
mission's own researched sources, with provenance — never silently
invented. Falls back to openly-labelled assumptions."""
from __future__ import annotations

from app.simulation import DEFAULT_BASELINE, extract_grounding
from tests.conftest import MISSION_BODY, hdr

SRC = [
    {"source_id": "s1", "title": "Freelancer tools market",
     "excerpt": "The platform serves 850 thousand users worldwide and the "
                "leading competitor charges $49/month for its pro tier."},
    {"source_id": "s2", "title": "Pricing survey",
     "excerpt": "Rival tools and alternatives cluster around $19 per month."},
]


def test_extract_grounding_from_data():
    g = extract_grounding(SRC)
    assert g["market_size"]["origin"] == "data"
    assert g["market_size"]["value"] == 850_000
    assert g["market_size"]["source_id"] == "s1"
    assert "850 thousand users" in g["market_size"]["evidence"]
    assert g["price_usd_month"]["origin"] == "data"
    assert g["price_usd_month"]["value"] == 49.0  # first match wins
    assert g["competitor_strength"]["origin"] == "data"
    assert 0.15 <= g["competitor_strength"]["value"] <= 0.75


def test_extract_grounding_assumptions_when_no_data():
    g = extract_grounding([])
    for k in ("market_size", "price_usd_month", "competitor_strength"):
        assert g[k]["origin"] == "assumption"
        assert g[k]["value"] == DEFAULT_BASELINE[k]
    g2 = extract_grounding([{"source_id": "x", "title": "News",
                             "excerpt": "A quiet week in the industry."}])
    assert g2["market_size"]["origin"] == "assumption"
    assert g2["price_usd_month"]["origin"] == "assumption"


def test_world_from_mission_carries_grounding(client, tenant):
    r = client.post("/v1/missions", headers=hdr(tenant, "grnd-0001"),
                    json=MISSION_BODY)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    w = client.post("/v1/worlds", headers=hdr(tenant, "grnd-0002"),
                    json={"name": "Grounded", "mission_id": mid})
    assert w.status_code == 201, w.text
    world = client.get(f"/v1/worlds/{w.json()['world_id']}",
                       headers=hdr(tenant)).json()
    g = world["provenance"]["grounding"]
    assert set(g) == {"market_size", "price_usd_month", "competitor_strength"}
    for param, entry in g.items():
        assert entry["origin"] in ("data", "assumption")
        if entry["origin"] == "data":
            assert world["baseline"][param] == entry["value"]
    # explicit operator override still wins over grounding
    w2 = client.post("/v1/worlds", headers=hdr(tenant, "grnd-0003"),
                     json={"name": "Override", "mission_id": mid,
                           "baseline": {"market_size": 12345}})
    got2 = client.get(f"/v1/worlds/{w2.json()['world_id']}",
                      headers=hdr(tenant)).json()
    assert got2["baseline"]["market_size"] == 12345


def test_selection_is_exclusive(client, tenant):
    """Regression: re-selecting must clear the previous candidate's
    'selected' status — exactly one selected candidate per mission."""
    r = client.post("/v1/missions", headers=hdr(tenant, "grnd-0010"),
                    json=MISSION_BODY)
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    cands = run.json()["candidates"]
    assert len(cands) >= 2
    a, b = cands[0]["candidate_id"], cands[1]["candidate_id"]
    client.post(f"/v1/missions/{mid}/select", headers=hdr(tenant),
                json={"candidate_id": a})
    client.post(f"/v1/missions/{mid}/select", headers=hdr(tenant),
                json={"candidate_id": b})
    detail = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    selected = [c for c in detail["candidates"] if c["status"] == "selected"]
    assert len(selected) == 1 and selected[0]["candidate_id"] == b
    assert detail["mission"]["selected_candidate_id"] == b
