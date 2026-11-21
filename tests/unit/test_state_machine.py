"""State machine invariants (Ch.17, 30, 31)."""
import pytest

from app.domain import (AGENT_TRANSITIONS, MISSION_TRANSITIONS, StateConflict,
                        assert_transition)


def test_legal_mission_path():
    for cur, nxt in [("draft", "planned"), ("planned", "running"),
                     ("running", "awaiting_approval"), ("awaiting_approval", "running"),
                     ("running", "completed"), ("running", "cancelled")]:
        assert_transition(cur, nxt, MISSION_TRANSITIONS, "Mission")


def test_agent_cannot_complete_mission_from_awaiting_approval():
    # The book's core invariant: approval is a record, not an agent's claim.
    with pytest.raises(StateConflict):
        assert_transition("awaiting_approval", "completed", MISSION_TRANSITIONS, "Mission")


def test_terminal_states_are_terminal():
    for terminal in ("completed", "cancelled"):
        with pytest.raises(StateConflict):
            assert_transition(terminal, "running", MISSION_TRANSITIONS, "Mission")


def test_failed_mission_can_only_reset_to_draft_for_replay():
    # Controlled DLQ replay path (Ch.19): failed -> draft is the one legal
    # exit from failed; failed -> running stays illegal.
    assert_transition("failed", "draft", MISSION_TRANSITIONS, "Mission")
    with pytest.raises(StateConflict):
        assert_transition("failed", "running", MISSION_TRANSITIONS, "Mission")


def test_agent_lifecycle_and_quarantine():
    assert_transition("active", "quarantined", AGENT_TRANSITIONS, "Agent")
    assert_transition("quarantined", "testing", AGENT_TRANSITIONS, "Agent")
    with pytest.raises(StateConflict):
        assert_transition("quarantined", "active", AGENT_TRANSITIONS, "Agent")  # must re-test first
    with pytest.raises(StateConflict):
        assert_transition("draft", "active", AGENT_TRANSITIONS, "Agent")  # must pass testing/approved
