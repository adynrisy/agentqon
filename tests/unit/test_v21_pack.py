"""v21 Track 1 — the 500-AI-Agents-Projects pack is a bundled library
source: a fresh workspace seeds to 6,799 agents (3 built-in + 280
agency + 6,516 library), 131 of them from the pack."""
from collections import Counter

from app.agency import import_roster
from app.library import import_library
from app.registry import seed_registry
from app.store import Store

# The AgentQon rename (2026-10-23) replaced harvested ids with
# clean AgentQon ids, so the pack is identified by its recorded
# provenance (the manifest owner field) - ids no longer encode
# where an agent came from; that is the point of the rename.
PACK_OWNER = "ashishpatel26/500-ai-agents-projects"


def _pack(agents):
    return [a for a in agents
            if str(a.get("owner", "")).lower().startswith(PACK_OWNER)]


def _seeded_store(tmp_path):
    store = Store(str(tmp_path / "v21pack.db"))
    tenant = "org_v21pack"
    seed_registry(store, tenant)
    with store.bulk():
        import_roster(store, tenant)
    with store.bulk():
        import_library(store, tenant)
    return store, tenant


def test_fresh_seed_total_is_6799_with_pack(tmp_path):
    store, tenant = _seeded_store(tmp_path)
    agents = store.list_agents(tenant)
    origins = Counter(
        "agency" if a["agent_id"].startswith("agency.")
        else "library" if a["agent_id"].startswith("lib.")
        else "builtin"
        for a in agents)
    assert origins == {"builtin": 3, "agency": 280, "library": 6516}
    assert len(agents) == 6799
    pack = _pack(agents)
    assert len(pack) == 131
    assert all(a["lifecycle"] == "active" for a in pack)


def test_pack_personas_carry_provenance(tmp_path):
    store, tenant = _seeded_store(tmp_path)
    pack = _pack(store.list_agents(tenant))
    body = store.get_persona(pack[0]["agent_id"])
    assert body and "Provenance" in body
    assert "500-AI-Agents-Projects" in body or "500 AI Agents" in body
    assert pack[0]["owner"].startswith("ashishpatel26/")
