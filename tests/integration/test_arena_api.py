"""Agent Arena endpoints (v15): blind creation, vote reveal + ledger,
leaderboard, tenant isolation."""
import uuid

from tests.conftest import hdr


def _import_agency(client, tenant):
    r = client.post("/v1/agents/import-agency", headers=hdr(tenant))
    assert r.status_code == 200, r.text


def test_duel_vote_leaderboard_flow(client, tenant):
    _import_agency(client, tenant)
    r = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "writing", "prompt": "Write a tagline for a coffee brand"})
    assert r.status_code == 201, r.text
    duel = r.json()
    assert duel["status"] == "awaiting_vote"
    assert duel["side_a"]["output"] and duel["side_b"]["output"]
    # blind: no identity fields before the vote
    assert "agent_id" not in duel["side_a"] and "agent_id" not in duel["side_b"]
    assert "winner_agent_id" not in duel
    duel_id = duel["duel_id"]
    got = client.get(f"/v1/arena/duels/{duel_id}", headers=hdr(tenant)).json()
    assert "agent_id" not in got["side_a"]

    v = client.post(f"/v1/arena/duels/{duel_id}/vote", headers=hdr(tenant),
                    json={"winner": "a"})
    assert v.status_code == 200, v.text
    voted = v.json()
    assert voted["status"] == "voted" and voted["winner_agent_id"]
    assert voted["side_a"]["agent_id"] and voted["winner_side"] == "a"

    # the vote moved the numbers GET /v1/agents/stats reports
    stats = client.get("/v1/agents/stats", headers=hdr(tenant)).json()["stats"]
    assert any(x.get("arena_wins", 0) == 1 for x in stats)
    assert any(x.get("arena_losses", 0) == 1 for x in stats)
    board = client.get("/v1/arena/leaderboard?task_type=writing",
                       headers=hdr(tenant)).json()
    assert board["min_duels_for_ranking"] == 3
    assert len(board["entries"]) == 2
    assert board["entries"][0]["wins"] == 1

    # one vote per duel
    again = client.post(f"/v1/arena/duels/{duel_id}/vote", headers=hdr(tenant),
                        json={"winner": "b"})
    assert again.status_code == 409


def test_duel_explicit_agents_and_validation(client, tenant):
    _import_agency(client, tenant)
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    agency = [a["agent_id"] for a in agents if a["agent_id"].startswith("agency.")]
    a, b = agency[0], agency[1]
    r = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "general", "prompt": "Say something wise",
        "agent_a": a, "agent_b": b})
    assert r.status_code == 201, r.text
    same = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "general", "prompt": "x", "agent_a": a, "agent_b": a})
    assert same.status_code == 400
    missing = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "general", "prompt": "x", "agent_a": a,
        "agent_b": "agency.does.not-exist"})
    assert missing.status_code == 404
    bad_type = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "nonsense", "prompt": "x"})
    assert bad_type.status_code == 400


def test_duel_tenant_isolation(client, tenant):
    _import_agency(client, tenant)
    duel = client.post("/v1/arena/duels", headers=hdr(tenant), json={
        "task_type": "qa", "prompt": "What is coffee?"}).json()
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    assert client.get(f"/v1/arena/duels/{duel['duel_id']}",
                      headers=hdr(other)).status_code == 404
    assert client.post(f"/v1/arena/duels/{duel['duel_id']}/vote",
                       headers=hdr(other), json={"winner": "a"}).status_code == 404
    assert client.get("/v1/arena/duels",
                      headers=hdr(other)).json()["duels"] == []
