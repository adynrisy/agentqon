"""§12/§13 wiring proofs (2026-10-24): the four items that were
PARTIAL after extraction — 206 participant-ID stability, 211
duplicate-exposure detection, 213 participant-state versioning, 218
state-transition logging — each wired into the live path, not just
audited from outside. All proofs use fakes; no real model is called.
"""
import os
import re
import tempfile
import time

import app.platform_society as psoc
import app.society as society_mod
from app.modelstate import ModelState
from app.society import (SocietyService, _clean_reaction)
from app.store import Store

TENANT = "org_soc_wire"


def _hash(text: str) -> int:
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) % 100000
    return h


class ScriptedGateway:
    """Deterministic reactions. `reason=True` ⇒ every reaction
    carries a reason, so the why wiring can be proven end to end."""

    def __init__(self, *, reason=False):
        self.reason = reason

    def _reaction(self, agent_id):
        stance = ["support", "oppose", "neutral"][_hash(agent_id) % 3]
        out = {"stance": stance, "intensity": 0.6, "action": "share",
               "line": f"{agent_id} speaks: {stance}"}
        if self.reason:
            out["reason"] = f"{agent_id} has a reason"
        return out

    async def chat_json(self, model, system, user, temperature=0.4):
        if "TABLE of simulated people" in system:
            members = re.findall(r"^- (\S+) \(", user, flags=re.M)
            reactions = []
            for m in members:
                r = self._reaction(m)
                r["agent_id"] = m
                reactions.append(r)
            return {"reactions": reactions}, {}
        m = re.search(r"you are agent (\S+?),", user)
        return self._reaction(m.group(1)), {}


class _FakeOrchestrator:
    def model_state(self, tenant_id=None):
        return ModelState(connected=True, source="env",
                          model="fake-model", base_url="http://x")


def _manifest(agent_id):
    return {"agent_id": agent_id, "version": "1.0.0", "owner": "test",
            "description": f"Persona of {agent_id}",
            "capabilities": ["persona.task"], "permissions_requested": [],
            "strategy": "persona", "lifecycle": "active",
            "quality": 4.0, "reliability": 0.9, "latency_ms": 500,
            "cost_per_run_usd": 0.1, "origin": "test",
            "persona_ref": agent_id}


def _run_society(*, reason=False):
    store = Store(os.path.join(tempfile.mkdtemp(), "soc.db"))
    for i in range(40):
        div = ("marketing", "engineering", "sales", "support")[i % 4]
        store.upsert_agent(TENANT, _manifest(f"agency.{div}.agent-{i:03d}"))
    gateway = ScriptedGateway(reason=reason)

    def factory(tenant_id):
        return gateway, "fake-model"

    svc = SocietyService(store, None, _FakeOrchestrator(),
                         client_factory=factory)
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a niche "
                                          "b2b pricing change")
    cast_ids = [m["agent_id"]
                for m in record["cast"]["leads"] + record["cast"]["crowd"]]
    svc.start_run(TENANT, record["society_id"])
    end = time.time() + 30
    while time.time() < end:
        done = store.get_society(TENANT, record["society_id"])
        if done["state"] in ("done", "capped", "failed", "cancelled"):
            return done, cast_ids
        time.sleep(0.05)
    raise AssertionError(f"society never settled: {done['state']}")


# ------------------------------------------------------------- 206: identity
def test_participant_id_stability_check():
    assert psoc.participant_id_stability(["a1", "a2"], [
        {"agent_id": "a1", "round": 1},
        {"agent_id": "a2", "round": 2}]) == []
    dup = psoc.participant_id_stability(["a1", "a1"], [])
    assert dup and "more than once" in dup[0]
    outsider = psoc.participant_id_stability(["a1"], [
        {"agent_id": "zz", "round": 1}])
    assert outsider and "not in the cast" in outsider[0]
    nameless = psoc.participant_id_stability(["a1"], [{"round": 1}])
    assert nameless and "no agent id" in nameless[0]
    # Wired into harden_record: an outsider event fails the record.
    good = {"cast": [{"agent_id": "a1", "stance": "support",
                      "confidence": 0.5}],
            "events": [{"agent_id": "a1", "round": 1, "stance": "support"}]}
    assert psoc.harden_record(good)["all_valid"] is True
    bad = {"cast": [{"agent_id": "a1", "stance": "support",
                     "confidence": 0.5}],
           "events": [{"agent_id": "zz", "round": 1, "stance": "support"}]}
    result = psoc.harden_record(bad)
    assert result["participant_id_errors"] and result["all_valid"] is False


def test_live_run_keeps_participant_identity_across_rounds():
    done, cast_ids = _run_society()
    assert done["state"] == "done", done.get("error")
    # The cast is created once and never rewritten by the run.
    after = [m["agent_id"] for m in done["cast"]["leads"]
             + done["cast"]["crowd"]]
    assert after == cast_ids
    # Every round speaks with exactly the same participant ids.
    by_round: dict[int, set] = {}
    for event in done["events"]:
        by_round.setdefault(event["round"], set()).add(event["agent_id"])
    assert len(by_round) >= 2  # at least two rounds ran
    assert all(ids == set(cast_ids) for ids in by_round.values())
    assert psoc.participant_id_stability(cast_ids, done["events"]) == []


# -------------------------------------------------- 211: duplicate exposure
def test_duplicate_exposure_tracked_at_intake():
    participant = psoc.ParticipantState(agent_id="a1", stance="neutral",
                                        confidence=0.5)
    participant.receive("item-1", content_summary="the mission result")
    participant.receive("item-1", content_summary="the mission result")
    participant.receive("item-2", content_summary="a second item")
    # The raw log stays intact (audit), but counting uses exposures.
    assert len(participant.received) == 3
    assert [r["item_id"] for r in participant.exposures] == ["item-1",
                                                             "item-2"]
    assert len(participant.duplicate_exposures) == 1
    assert participant.duplicate_exposures[0]["item_id"] == "item-1"
    # The standalone audit helper still agrees with the intake record.
    assert len(psoc.detect_duplicate_information(participant.received)) == 1


# -------------------------------------------------------- 213: versioning
def test_state_version_links_reaction_to_state():
    participant = psoc.ParticipantState(agent_id="a1", stance="neutral",
                                        confidence=0.5)
    assert participant.state_version == 0
    participant.react(stance="neutral", text_summary="first word",
                      reason="nothing changed yet")
    assert participant.reactions[0]["state_version"] == 0
    participant.set_stance("support", why="changed mind after the result")
    assert participant.state_version == 1
    assert participant.stance_history[-1]["version"] == 1
    participant.set_confidence(0.8, why="felt surer after discussion")
    assert participant.state_version == 2
    assert participant.confidence_history[-1]["version"] == 2
    participant.react(stance="support", text_summary="agreeing line",
                      reason="the result convinced me")
    assert participant.reactions[-1]["state_version"] == 2
    # Histories stay separate; persuasion still reads from them.
    assert len(participant.persuasion_events()) == 1


# ------------------------------------------------------- 218: why in events
def test_clean_reaction_carries_why_only_when_given():
    base = {"stance": "support", "intensity": 0.7, "action": "share",
            "line": "I agree with this"}
    assert "why" not in _clean_reaction(dict(base), allowed_targets=set())
    with_reason = _clean_reaction(
        dict(base, reason="the numbers convinced me"),
        allowed_targets=set())
    assert with_reason["why"] == "the numbers convinced me"
    long_reason = _clean_reaction(
        dict(base, reason="x" * 500), allowed_targets=set())
    assert len(long_reason["why"]) == 200
    assert "why" not in _clean_reaction(
        dict(base, reason="   "), allowed_targets=set())


def test_system_prompts_ask_for_reason():
    assert "reason" in society_mod._SYSTEM_LEAD
    assert "reason" in society_mod._SYSTEM_TABLE


def test_live_events_carry_why_from_model():
    done, _ = _run_society(reason=True)
    assert done["state"] == "done", done.get("error")
    assert done["events"]
    for event in done["events"]:
        assert event["why"] == f"{event['agent_id']} has a reason"
    # Backwards compatibility: reactions without a reason keep the
    # old event shape exactly — no empty why key is invented.
    plain, _ = _run_society(reason=False)
    assert plain["events"]
    assert all("why" not in e for e in plain["events"])
