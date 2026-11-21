"""W7 §12 society invariants + the separation rule (proven by test)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import platform_society as psoc


def test_initial_state_validation_rejects_bad_cast():
    errors = psoc.validate_initial_state([
        {"agent_id": "a1", "stance": "support", "confidence": 0.8},
        {"agent_id": "a1", "stance": "oppose", "confidence": 0.5},
        {"agent_id": "a2", "stance": "love it", "confidence": 0.5},
        {"agent_id": "a3", "stance": "neutral", "confidence": 1.5},
    ])
    assert any("twice" in e for e in errors)
    assert any("not valid" in e for e in errors)
    assert any("outside 0 to 1" in e for e in errors)
    assert psoc.validate_initial_state(
        [{"agent_id": "a1", "stance": "neutral", "confidence": 0.5}]) == []


def test_stance_and_confidence_validation():
    assert psoc.validate_stance("support") is None
    assert psoc.validate_stance("maybe") is not None
    assert psoc.validate_confidence(0.0) is None
    assert psoc.validate_confidence(1.0) is None
    assert psoc.validate_confidence(1.01) is not None
    assert psoc.validate_confidence("high") is not None


def test_interaction_eligibility_blocks_double_reaction():
    participant = {"agent_id": "a1", "stance": "support", "confidence": 0.5}
    assert psoc.interaction_eligible(participant=participant, round_no=1,
                                     already_reacted_this_round=set())["eligible"] is True
    blocked = psoc.interaction_eligible(
        participant=participant, round_no=1, already_reacted_this_round={"a1"})
    assert blocked["eligible"] is False and "already reacted" in blocked["reason"]
    assert psoc.interaction_eligible(participant=participant, round_no=0,
                                     already_reacted_this_round=set())["eligible"] is False


def test_round_order_and_completeness():
    events = [{"agent_id": "a1", "round": 1, "stance": "support"},
              {"agent_id": "a1", "round": 1, "stance": "support"},
              {"agent_id": "a2", "round": 2, "stance": "oppose"}]
    assert psoc.round_order_consistency(events) == []
    backwards = [{"agent_id": "a1", "round": 2, "stance": "support"},
                 {"agent_id": "a1", "round": 1, "stance": "support"}]
    assert psoc.round_order_consistency(backwards)
    completeness = psoc.reaction_completeness(events, ["a1", "a2"], rounds=2)
    assert completeness[1]["non_responded"] == ["a2"]
    assert completeness[2]["reacted"] == ["a2"]


def test_invalid_state_recovery_lists_repairs():
    record = {"cast": [{"agent_id": "a1", "confidence": 2.0}],
              "events": [{"agent_id": "a1", "round": 1, "stance": "bogus"},
                         {"agent_id": "a1", "round": 1, "stance": "support"}]}
    out = psoc.invalid_state_recovery(record)
    assert out["record"]["cast"][0]["confidence"] == 1.0
    assert len(out["record"]["events"]) == 1
    assert len(out["repairs"]) == 2  # clamp + drop, both listed


def test_population_total_checking():
    assert psoc.population_total_check(size=3, stance_counts={"support": 2, "oppose": 1},
                                       responded=3) == []
    errors = psoc.population_total_check(size=3, stance_counts={"support": 5},
                                         responded=9)
    assert errors
    assert psoc.population_total_check(size=2, stance_counts={"support": -1},
                                       responded=1)


def test_separation_rule_four_histories_never_conflated():
    """THE SEPARATION RULE: received, reactions, confidence changes and
    stance changes are four separate histories. Receiving information
    must not change stance or confidence; reacting must not count as
    receiving; activity alone must not count as persuasion."""
    participant = psoc.ParticipantState(agent_id="a1", stance="neutral",
                                        confidence=0.5)
    participant.receive("item-1", content_summary="the mission result")
    participant.receive("item-1", content_summary="the mission result")
    assert participant.stance == "neutral"
    assert participant.confidence == 0.5
    assert len(participant.stance_history) == 1
    assert len(participant.confidence_history) == 1
    # Duplicate information is recognised as one exposure, not two.
    assert len(psoc.detect_duplicate_information(participant.received)) == 1
    participant.react(stance="support", text_summary="agreeing line",
                      reason="the result convinced me")
    assert len(participant.reactions) == 1
    assert len(participant.received) == 2  # reacting did not add a receipt
    participant.set_confidence(0.8, why="felt surer after discussion")
    assert participant.confidence == 0.8
    assert participant.stance == "neutral"  # confidence != stance
    participant.set_stance("support", why="changed mind after the result")
    assert participant.stance == "support"
    assert len(participant.stance_history) == 2
    assert len(participant.confidence_history) == 2
    persuasion = participant.persuasion_events()
    assert len(persuasion) == 1
    assert persuasion[0]["from"] == "neutral" and persuasion[0]["to"] == "support"
    assert persuasion[0]["followed_received_item"] is True


def test_replay_metadata_labels_llm_non_reproducibility():
    honest = psoc.replay_metadata(seed=42, config={"size": 10},
                                  mode="deterministic", llm_used=True)
    assert honest["reproducible_exactly"] is False
    assert "cannot reproduce it exactly" in honest["reproducibility_note"]
    exact = psoc.replay_metadata(seed=42, config={}, mode="deterministic",
                                 llm_used=False)
    assert exact["reproducible_exactly"] is True
    for mode in ("individual", "batched", "deterministic", "hybrid"):
        assert psoc.replay_metadata(seed=1, config={}, mode=mode,
                                    llm_used=False)["mode"] == mode
    try:
        psoc.replay_metadata(seed=1, config={}, mode="chaotic", llm_used=False)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown simulation mode must be refused")


def test_output_interpretation_three_labelled_sections():
    out = psoc.interpret_output(
        assumptions=["cast chosen by relevance"],
        generated_reactions=[{"agent_id": "a1", "text": "I agree"}],
        calculated_statistics={"support_share": 0.6})
    assert "Assumptions" in out["assumptions"]["label"]
    assert "Generated reactions" in out["generated_reactions"]["label"]
    assert "Calculated statistics" in out["calculated_statistics"]["label"]
    assert "simulated, not predictions" in out["warning"]


def test_harden_record_applies_all_named_checks():
    record = {"cast": [{"agent_id": "a1", "stance": "support", "confidence": 0.5}],
              "events": [{"agent_id": "a1", "round": 1, "stance": "support"}]}
    result = psoc.harden_record(record)
    assert result["all_valid"] is True
    bad = {"cast": [{"agent_id": "a1", "stance": "bogus", "confidence": 0.5}],
           "events": []}
    assert psoc.harden_record(bad)["all_valid"] is False
