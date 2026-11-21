"""Composer input has no character cap (owner order 2026-10-30: like
Claude, however much the user wants to input, they can).

The three caps that used to reject long composer input at the API
boundary — chat message 8,000, mission objective 12,000, agent goal
4,000 — are removed. These tests pin the removal at the model layer
and at the composer element itself.
"""
import re
from pathlib import Path

from app.domain import MissionContract
from app.main import AgentSessionBody, ChatBody

LONG = 100_000


def test_chat_message_accepts_very_long_text():
    body = ChatBody(message="x" * LONG)
    assert len(body.message) == LONG


def test_mission_objective_accepts_very_long_text():
    contract = MissionContract(
        objective="y" * LONG,
        deliverables=["Decision brief"],
        acceptance=["Material claims cite sources"],
    )
    assert len(contract.objective) == LONG


def test_agent_goal_accepts_very_long_text():
    body = AgentSessionBody(goal="z" * LONG)
    assert len(body.goal) == LONG


def test_composer_textarea_has_no_maxlength():
    html = Path("apps/web/index.html").read_text()
    m = re.search(r"<textarea[^>]*id=\"chatInput\"[^>]*>", html)
    assert m is not None, "chatInput textarea not found"
    assert "maxlength" not in m.group(0).lower()
