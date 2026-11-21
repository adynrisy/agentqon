"""The AgentQon rename: shipped maps, importers, and the
stored-data upgrade migration.

Zohaib's order: no "reused" tag - every agent and skill carries
an original AgentQon name AND a clean AgentQon id, and a user's
stored data (pins, missions, sessions, society casts, evolve
lessons) must survive the upgrade with its references rewritten
to the new ids.
"""
import json

import pytest

from app import display_names
from app.id_migration import migrate_store_ids
from app.store import Store

# Real pre-rename ids sampled from the shipped map, incl. the
# society exhibit that triggered the rename.
OLD_UX = "lib.donchitos-claude-code-game-studios.claude-agents-ux-designer"
OLD_AGENCY = "agency.academic.academic-anthropologist"
OLD_SKILL = "ux-heuristics--c68510fca9"
TENANT = "t-rename"


@pytest.fixture()
def old_store(tmp_path):
    """A store in the PRE-rename state: records referencing old
    agent/skill ids across the stores the migration covers."""
    store = Store(str(tmp_path / "old.db"), str(tmp_path / "art"))
    amap, smap = display_names.agent_id_map(), display_names.skill_id_map()
    assert OLD_UX in amap and OLD_AGENCY in amap and OLD_SKILL in smap
    # agents + persona briefs
    for old in (OLD_UX, OLD_AGENCY):
        store.upsert_agent(TENANT, {
            "agent_id": old, "version": "1.0.0", "owner": "upstream",
            "description": "fixture", "capabilities": ["persona.task"],
            "permissions_requested": [], "strategy": "persona",
            "lifecycle": "active", "quality": 3.5, "reliability": 0.9,
            "latency_ms": 900, "cost_per_run_usd": 0.3,
            "origin": "subagents-library", "persona_ref": old})
        store.put_persona(old, "brief for " + old)
    # a skill pin on an old skill id
    store.set_skill_pin(TENANT, OLD_SKILL, True)
    # mission + task + run referencing the old agent id
    mission = store.create_mission(
        TENANT, "user-1", "fixture mission",
        {"objective": "test", "panel": [OLD_UX]}, 5.0)
    store._exec(
        "INSERT INTO tasks (id, mission_id, tenant_id, contract,"
        " status, assigned_agent, output_artifact_id, created_at,"
        " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("task-1", mission["id"], TENANT, "{}", "done", OLD_UX,
         None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    store._exec(
        "INSERT INTO runs (id, mission_id, task_id, tenant_id,"
        " agent_version_id, adapter_version, status, usage,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("run-1", mission["id"], "task-1", TENANT, OLD_UX + "@1.0.0",
         "1", "done", "{}", "2026-01-01T00:00:00Z"))
    # suitability stats under "oldid|task_type"
    store._exec(
        "INSERT INTO agent_stats (id, tenant_id, data) VALUES (?,?,?)",
        (OLD_UX + "|strategy", TENANT,
         json.dumps({"agent_version_id": OLD_UX, "runs": 3})))
    # an evolve lesson + an agent session transcript embedding ids
    store._exec(
        "INSERT INTO memories (id, tenant_id, data) VALUES (?,?,?)",
        ("mem-1", TENANT, json.dumps(
            {"subject": OLD_UX, "lesson": "works well",
             "evidence": "ran with " + OLD_AGENCY})))
    store.put_agent_session(TENANT, {
        "id": "sess-1", "agent_id": OLD_UX,
        "transcript": "used skill (id: " + OLD_SKILL + ") with "
                      + OLD_UX})
    # a society whose cast member name was derived from the old id
    derived = "Claude Agents Ux Designer"
    store.put_society(TENANT, {
        "society_id": "soc-1", "status": "complete",
        "cast": {"members": [
            {"agent_id": OLD_UX, "name": derived, "kind": "lead"},
            {"agent_id": OLD_AGENCY, "name": "Academic Anthropologist",
             "kind": "crowd"}]},
        "note": "cast led by " + OLD_UX})
    store.audit(TENANT, "user-1", "pin", "skill", OLD_SKILL, {})
    return store


def _table_counts(store) -> dict:
    out = {}
    for table in ("agents", "personas", "skill_pins", "tasks", "runs",
                  "agent_stats", "memories", "agent_sessions",
                  "societies", "missions", "audit_log"):
        out[table] = store._one(
            f"SELECT COUNT(*) AS n FROM {table}")["n"]
    return out


def test_upgrade_migration_rewrites_every_store(old_store):
    amap, smap = display_names.agent_id_map(), display_names.skill_id_map()
    new_ux, new_ag, new_skill = amap[OLD_UX], amap[OLD_AGENCY], smap[OLD_SKILL]
    before = _table_counts(old_store)
    counts = migrate_store_ids(old_store, log=lambda *a: None)
    assert counts, "migration must report rewrites"
    # record counts unchanged - nothing deleted, nothing added
    assert _table_counts(old_store) == before
    # agents + personas resolve under the new ids
    ids = {a["agent_id"] for a in old_store.list_agents(TENANT)}
    assert ids == {new_ux, new_ag}
    assert old_store.get_persona(new_ux) == "brief for " + OLD_UX
    # the pin still works, under the new skill id
    assert old_store.pinned_skill_ids(TENANT) == {new_skill}
    # task + run references
    task = old_store._one("SELECT assigned_agent FROM tasks")
    assert task["assigned_agent"] == new_ux
    run = old_store._one("SELECT agent_version_id FROM runs")
    assert run["agent_version_id"] == new_ux + "@1.0.0"
    # stats composite key + payload
    stats = old_store.list_agent_stats(TENANT)
    assert stats and stats[0]["agent_version_id"] == new_ux
    # evolve lesson + session transcript
    mem = old_store.list_memories(TENANT)[0]
    blob = json.dumps(mem)
    assert new_ux in blob and new_ag in blob and OLD_UX not in blob
    sess = old_store.get_agent_session(TENANT, "sess-1")
    assert sess["agent_id"] == new_ux
    assert new_skill in sess["transcript"] and OLD_SKILL not in \
        sess["transcript"]
    # society history loads; the cast shows the NEW display name
    soc = old_store.get_society(TENANT, "soc-1")
    lead = soc["cast"]["members"][0]
    assert lead["agent_id"] == new_ux
    assert lead["name"] == display_names.agent_display(new_ux)
    assert OLD_UX not in json.dumps(soc)
    # mission contract + audit entity
    mission = old_store._one("SELECT contract FROM missions")
    assert new_ux in mission["contract"]
    aud = old_store._one("SELECT entity_id FROM audit_log")
    assert aud["entity_id"] == new_skill


def test_upgrade_migration_is_idempotent(old_store):
    migrate_store_ids(old_store, log=lambda *a: None)
    snapshot = {t: old_store._all(f"SELECT * FROM {t}")
                for t in ("agents", "personas", "skill_pins", "tasks",
                          "runs", "agent_stats", "memories",
                          "agent_sessions", "societies", "audit_log")}
    again = migrate_store_ids(old_store, log=lambda *a: None)
    assert again.get("already_applied") is True
    for t, rows in snapshot.items():
        assert old_store._all(f"SELECT * FROM {t}") == rows


def test_shipped_maps_are_complete_and_clean():
    amap, smap = display_names.agent_id_map(), display_names.skill_id_map()
    # v22.1.3: the 213-skill offensive-security cluster was cut from the
    # shipped library (5,053 -> 4,840); the skill map shrinks with it.
    # Cut list: CUT_SECURITY_CLUSTER_22.1.3.md in the goal's hidden_files.
    assert len(amap) == 6796 and len(smap) == 4840
    assert len(set(amap.values())) == 6796
    assert len(set(smap.values())) == 4840
    for new in list(amap.values()) + list(smap.values()):
        assert display_names.agent_display(new) or \
            display_names.skill_display(new)
    # the exhibit itself
    assert display_names.agent_display(amap[OLD_UX]) == "UX Designer"


def test_importers_resolve_new_ids(tmp_path):
    from app.agency import import_roster
    from app.library import import_library
    store = Store(str(tmp_path / "n.db"), str(tmp_path / "a"))
    r1 = import_roster(store, TENANT)
    r2 = import_library(store, TENANT)
    assert r1["total"] == 280 and r2["total"] == 6516
    ids = {a["agent_id"] for a in store.list_agents(TENANT)}
    assert ids == set(display_names.agent_id_map().values())
