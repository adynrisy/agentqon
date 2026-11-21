"""Agent Arena tests (v15): blind duels between persona agents, votes
that move the performance ledger, leaderboard ordering rules."""
import asyncio
import tempfile

import pytest

from app.arena import (DuelError, cast_vote, create_duel, leaderboard,
                       pick_duelists, public_duel)
from app.domain import StateConflict
from app.orchestrator import Orchestrator
from tests.stub_llm import settings as stub_settings
from app.registry import seed_registry
from app.store import Store

T = "org_arena"


def _store(tmp):
    s = Store(f"{tmp}/a.db", f"{tmp}/artifacts")
    seed_registry(s, T)
    return s


def _persona_agent(s, agent_id, quality=4.0):
    s.put_persona(agent_id, f"You are {agent_id}, a specialist.")
    return s.upsert_agent(T, {
        "agent_id": agent_id, "version": "1.0.0", "owner": "test",
        "description": f"{agent_id} persona", "capabilities": ["persona.task"],
        "permissions_requested": ["artifact.read"], "strategy": "persona",
        "lifecycle": "active", "quality": quality, "reliability": 0.9,
        "latency_ms": 900, "cost_per_run_usd": 0.30})


def _duel(s, orch, **kw):
    return asyncio.run(create_duel(
        s, orch, T, "tester", kw.pop("task_type", "writing"),
        kw.pop("prompt", "Write a tagline for a coffee brand"), **kw))


def test_auto_pick_skips_agents_without_personas():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        _persona_agent(s, "agency.fake.alpha")
        _persona_agent(s, "lib.fake.beta")
        picked = pick_duelists(s, T, "strategy")
        # core seed agents rank on fit but carry no persona brief
        assert {m["agent_id"] for m in picked} == {"agency.fake.alpha",
                                                   "lib.fake.beta"}


def test_duel_blind_then_vote_moves_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        _persona_agent(s, "agency.fake.alpha")
        _persona_agent(s, "lib.fake.beta")
        orch = Orchestrator(s, settings=stub_settings())
        duel = _duel(s, orch)
        assert duel["status"] == "awaiting_vote"
        assert set(duel["outputs"]) == {"agency.fake.alpha", "lib.fake.beta"}
        assert all(o["output_text"] for o in duel["outputs"].values())
        view = public_duel(duel)
        # blind: no identity leaks anywhere in the public view
        assert "agent_id" not in view["side_a"] and "agent_id" not in view["side_b"]
        assert view["side_a"]["output"] and "winner_agent_id" not in view
        assert "evidence-checked" in view["note"]

        voted = cast_vote(s, T, duel["duel_id"], "a")
        assert voted["status"] == "voted" and voted["winner_agent_id"]
        view = public_duel(voted)
        assert view["side_a"]["agent_id"] == voted["side_a_agent_id"]
        assert view["winner_side"] == "a"
        stats = {(x["agent_version_id"], x["task_type"]): x
                 for x in s.list_agent_stats(T)}
        winner_key = (voted["outputs"][voted["winner_agent_id"]]["agent_version_id"],
                      "writing")
        loser_key = (voted["outputs"][voted["loser_agent_id"]]["agent_version_id"],
                     "writing")
        assert stats[winner_key]["arena_wins"] == 1
        assert stats[loser_key]["arena_losses"] == 1
        with pytest.raises(StateConflict):
            cast_vote(s, T, duel["duel_id"], "b")
        with pytest.raises(KeyError):
            cast_vote(s, T, "duel_missing", "a")
        with pytest.raises(KeyError):
            cast_vote(s, "org_other", duel["duel_id"], "a")


def test_duel_validation_rules():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        _persona_agent(s, "agency.fake.alpha")
        _persona_agent(s, "lib.fake.beta")
        orch = Orchestrator(s, settings=stub_settings())
        with pytest.raises(DuelError):  # self-duel
            _duel(s, orch, agent_a="agency.fake.alpha",
                  agent_b="agency.fake.alpha")
        with pytest.raises(DuelError):  # only one named
            _duel(s, orch, agent_a="agency.fake.alpha")
        with pytest.raises(KeyError):  # unknown agent
            _duel(s, orch, agent_a="agency.fake.alpha", agent_b="nope@x")
        # quarantined agents are ineligible
        s.set_agent_lifecycle(T, "lib.fake.beta@1.0.0", "quarantined")
        with pytest.raises(DuelError):
            _duel(s, orch, agent_a="agency.fake.alpha",
                  agent_b="lib.fake.beta")


def test_leaderboard_ranking_rule():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        _persona_agent(s, "agency.fake.alpha")
        _persona_agent(s, "lib.fake.beta")
        orch = Orchestrator(s, settings=stub_settings())
        board = leaderboard(s, T, "writing")
        assert board["entries"] == [] and board["min_duels_for_ranking"] == 3
        for _ in range(3):
            duel = _duel(s, orch)
            winner_side = "a" if duel["side_a_agent_id"] == "agency.fake.alpha" else "b"
            cast_vote(s, T, duel["duel_id"], winner_side)
        board = leaderboard(s, T, "writing")
        assert len(board["entries"]) == 2
        top = board["entries"][0]
        assert top["agent_id"] == "agency.fake.alpha"
        assert top["wins"] == 3 and top["win_rate"] == 1.0 and top["ranked"] is True
        assert board["entries"][1]["ranked"] is True
        assert board["entries"][1]["losses"] == 3
        # task-type filter is real
        assert leaderboard(s, T, "code")["entries"] == []
        # provisional (below the duel threshold) sorts below ranked
        _persona_agent(s, "agency.fake.gamma")
        duel = asyncio.run(create_duel(s, orch, T, "tester", "writing",
                                       "prompt", agent_a="agency.fake.gamma",
                                       agent_b="lib.fake.beta"))
        cast_vote(s, T, duel["duel_id"],
                  "a" if duel["side_a_agent_id"] == "agency.fake.gamma" else "b")
        board = leaderboard(s, T, "writing")
        gamma = next(e for e in board["entries"]
                     if e["agent_id"] == "agency.fake.gamma")
        assert gamma["ranked"] is False and gamma["wins"] == 1
        assert board["entries"][-1]["agent_id"] == "agency.fake.gamma"
