"""W5 gap-fix: mission management (expansion E)."""
import pytest

from app.mission_mgmt import (CheckpointStore, apply_retention,
                              clone_mission, delete_with_confirmation,
                              deletion_scope, dependency_view,
                              failure_summary, pause_mission,
                              rename_mission, retention_statement,
                              resume_mission)
from app.store import Store


def _store(tmp_path):
    return Store(db_path=str(tmp_path / "test.db"))


def _mission(store, title="Trees brief"):
    contract = {"objective": "brief on trees",
                "deliverables": ["brief"],
                "constraints": {"max_cost_usd": 10},
                "tools": {"allow": ["web.read"], "deny": []},
                "acceptance": ["claims cite sources"],
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal"}
    return store.create_mission("t1", "tester", title, contract, 1000.0)


def test_rename_reflected_in_detail_view(tmp_path):
    store = _store(tmp_path)
    mission = _mission(store)
    renamed = rename_mission(store, "t1", mission["id"], "City trees brief")
    assert renamed["title"] == "City trees brief"
    assert store.list_missions("t1")[0]["title"] == "City trees brief"
    with pytest.raises(ValueError):
        rename_mission(store, "t1", mission["id"], "   ")


def test_pause_resume_reconciles_first_and_skips_irreversible(tmp_path):
    mission = {"id": "m1", "title": "Trees brief", "status": "running"}
    paused = pause_mission(mission)
    assert paused["status"] == "paused"
    seen = {}

    def reconcile(m):
        seen["ran"] = True
        return {"external": "email already sent outside",
                "changed": ["inbox count grew"]}

    out = resume_mission(paused, reconcile,
                         irreversible_done=["send-email"],
                         planned_steps=["send-email", "write-brief"])
    assert seen["ran"] is True
    assert out["mission"]["status"] == "running"
    assert out["resume_plan"] == ["write-brief"]
    assert out["skipped_irreversible_steps"] == ["send-email"]
    assert out["reconciliation"]["external"] == "email already sent outside"


def test_resume_refuses_non_paused():
    with pytest.raises(ValueError):
        resume_mission({"id": "m1", "status": "running"}, lambda m: {})


def test_checkpoints_create_list_restore():
    store = CheckpointStore()
    cp = store.create("m1", "before synthesis", {"step": "research done"})
    listed = store.list_for("m1")
    assert len(listed) == 1 and listed[0]["label"] == "before synthesis"
    restored = store.restore(cp.checkpoint_id)
    assert restored["snapshot"] == {"step": "research done"}
    # Restoring does not delete the checkpoint.
    assert store.restore(cp.checkpoint_id)["snapshot"] == {"step": "research done"}
    with pytest.raises(KeyError):
        store.restore("ckpt-nope")


def test_clone_copies_shape_not_results():
    mission = {"id": "m1", "title": "Trees brief", "status": "completed",
               "contract": {"objective": "brief"}}
    clone = clone_mission(mission)
    assert clone["id"] != "m1"
    assert clone["title"] == "Copy of Trees brief"
    assert clone["status"] == "draft"
    assert clone["contract"] == {"objective": "brief"}
    assert clone["tasks"] == [] and clone["results"] == []
    assert clone["cloned_from"] == "m1"


def test_dependency_visualization_plain_words():
    tasks = [{"task_id": "a", "dependencies": []},
             {"task_id": "b", "dependencies": ["a"]},
             {"task_id": "c", "dependencies": ["b"]}]
    view = dependency_view(tasks)
    assert any("waits for task a" in line for line in view["lines"])
    assert any("can start right away" in line for line in view["lines"])
    assert view["levels"] == {"a": 0, "b": 1, "c": 2}
    assert ("b", "a") in view["edges"]


def test_failure_summary_plain_words():
    summary = failure_summary(
        {"title": "Trees brief"},
        [{"task_id": "t-research", "reason": "the web search timed out"}])
    assert "Trees brief" in summary
    assert "web search timed out" in summary
    assert "HTTP" not in summary and "500" not in summary


def test_retention_policy_stated_and_applied():
    statement = retention_statement()
    assert "12 months" in statement and "90 days" in statement
    records = [{"id": "old", "finished_at": "2020-01-01T00:00:00Z"},
               {"id": "new", "finished_at": "2026-10-01T00:00:00Z"}]
    out = apply_retention(records, "mission_records")
    assert [r["id"] for r in out["due_for_removal"]] == ["old"]
    assert [r["id"] for r in out["kept"]] == ["new"]
    # Unknown kind keeps everything: never delete on an unknown rule.
    assert len(apply_retention(records, "mystery")["kept"]) == 2


def test_deletion_scope_stated_before_confirmation(tmp_path):
    store = _store(tmp_path)
    mission = _mission(store)
    scope = deletion_scope(mission)
    assert "will also delete" in scope["scope_text"]
    assert "tasks" in scope["scope_text"]
    assert "other missions are not touched" in scope["scope_text"]
    # Wrong token deletes nothing.
    out = delete_with_confirmation(store, "t1", mission, "wrong-token")
    assert out["deleted"] is False
    assert store.get_mission("t1", mission["id"]) is not None
    # Right token deletes the mission and its retained data.
    out = delete_with_confirmation(store, "t1", mission,
                                   scope["confirmation_token"])
    assert out["deleted"] is True
    assert out["counts"]["missions"] == 1
    assert store.get_mission("t1", mission["id"]) is None
