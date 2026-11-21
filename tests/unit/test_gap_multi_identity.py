"""W5 gap-fix: agent identity (expansion B)."""
import pytest

from app.agent_identity import (AgentRecord, config_diff,
                                health_from_stats, memory_scope_filter,
                                reserve_for_agent, settle_for_agent)


class FakeStore:
    """Minimal stand-in exposing ONLY the existing budget hooks."""

    def __init__(self):
        self.reserved = []
        self.settled = []

    def reserve_budget(self, tenant_id, mission_id, amount, cap):
        if amount > cap:
            return None
        rid = f"rsv_{len(self.reserved)}"
        self.reserved.append((tenant_id, mission_id, amount, rid))
        return rid

    def settle_reservation(self, reservation_id, outcome):
        self.settled.append((reservation_id, outcome))


def test_version_config_diff_viewable():
    diff = config_diff({"model": "a", "temp": 0.5, "old": 1},
                       {"model": "b", "temp": 0.5, "new": 2})
    by_field = {c["field"]: c for c in diff["changes"]}
    assert by_field["model"]["change"] == "changed"
    assert by_field["old"]["change"] == "removed"
    assert by_field["new"]["change"] == "added"
    assert "temp" not in by_field
    assert "3 setting(s) changed" in diff["summary"]
    assert config_diff({"a": 1}, {"a": 1})["summary"] == "no settings changed"


def test_last_activity_timestamp():
    agent = AgentRecord("a1")
    assert agent.last_activity == ""
    agent.touch(at="2026-10-24T10:00:00Z")
    assert agent.last_activity == "2026-10-24T10:00:00Z"


def test_per_agent_budget_uses_existing_hooks_not_a_second_system():
    store = FakeStore()
    agent = AgentRecord("a1")
    rid = reserve_for_agent(store, "t1", "m1", agent, 5.0, 100.0)
    assert rid is not None and rid in agent.reservation_ids
    assert store.reserved == [("t1", "m1", 5.0, rid)]
    settle_for_agent(store, agent, rid, "reconciled")
    assert store.settled == [(rid, "reconciled")]
    assert agent.reservation_ids == []
    # Over the cap: no reservation, never treated as unlimited.
    assert reserve_for_agent(store, "t1", "m1", agent, 500.0, 100.0) is None


def test_memory_scope_filter_fail_closed():
    agent = AgentRecord("a1", memory_scopes=["user", "agent:a1"])
    memories = [
        {"scope": "user", "scope_ref": "", "topic": "shared"},
        {"scope": "agent", "scope_ref": "a1", "topic": "mine"},
        {"scope": "agent", "scope_ref": "a2", "topic": "theirs"},
        {"scope": "session", "scope_ref": "s9", "topic": "other session"},
    ]
    visible = memory_scope_filter(memories, agent)
    assert [m["topic"] for m in visible] == ["shared", "mine"]
    assert memory_scope_filter(memories, AgentRecord("a9")) == []


def test_disabled_agent_cannot_be_assigned_or_run():
    agent = AgentRecord("a1", disabled=True)
    ok, reason = agent.assignable()
    assert ok is False and "disabled" in reason
    assert AgentRecord("a1").assignable()[0] is True


def test_health_indicator_plain_words():
    healthy = health_from_stats([{"outcome": "success"}] * 5)
    assert "healthy" in healthy["label"] and "failed" not in healthy["label"].split("—")[0]
    unsteady = health_from_stats([{"outcome": "failed"}] * 3 +
                                 [{"outcome": "success"}])
    assert "unsteady" in unsteady["label"]
    none = health_from_stats([])
    assert none["label"] == "no recent activity recorded"


def test_ownership_shown_in_listing():
    entry = AgentRecord("a1", version="3", ownership="user-created",
                        disabled=False).listing_entry(
                            health={"label": "healthy — recent work finished without failures"})
    assert entry["ownership"] == "user-created"
    assert entry["version"] == "3"
    assert "healthy" in entry["health"]
    with pytest.raises(ValueError):
        AgentRecord("a1", ownership="third-party")
