"""W5 gap-fix: duplicate-work detection (W5-2), debate controls (W5-3),
per-task acceptance criteria (W5-4), result dedup (§5-92),
contradiction flagging (§5-96), partial-result acceptance (§5-97)."""
import pytest

from app.comms import flag_contradiction
from app.multiagent import (Debate, DebateClaim, TaskSpec,
                            VerifiedResultStore, accept_partial_result,
                            criteria_from_plan, evaluate_criteria)
from app.comms import ResultDedup


def _task(desc="research urban trees", kind="research"):
    return TaskSpec(task_id="t1", mission_id="m1", kind=kind,
                    description=desc)


def test_verified_result_reused_and_recorded():
    store = VerifiedResultStore()
    task = _task()
    assert store.record_verified(task, {"answer": 42}, verified_by="checker-v1")
    other = TaskSpec(task_id="t2", mission_id="m2", kind="research",
                     description="Research urban trees!")
    result, record = store.find_reuse(other)
    assert result == {"answer": 42}
    assert record["reused_from_task_id"] == "t1"
    assert "verified" in record["reason"]


def test_unverified_result_never_reused():
    store = VerifiedResultStore()
    # No verified_by: nothing is stored, so nothing can be reused.
    assert store.record_verified(_task(), {"answer": 1}, verified_by="") is False
    result, record = store.find_reuse(_task())
    assert result is None and record is None


def test_different_work_not_reused():
    store = VerifiedResultStore()
    store.record_verified(_task(), {"answer": 1}, verified_by="checker-v1")
    result, _ = store.find_reuse(_task("research ocean currents"))
    assert result is None


def test_debate_round_cap_and_evidence_gate():
    debate = Debate(max_rounds=2)
    claims = [DebateClaim("c1", "a1", "trees cool cities", evidence=["e1"]),
              DebateClaim("c2", "a2", "trees warm cities", evidence=[])]
    out = debate.run(claims)
    assert out["rounds_used"] <= 2
    assert "c1" in out["standing_claims"]
    assert "c2" in out["claims_without_evidence"]
    assert "c2" not in out["standing_claims"]


def test_debate_stops_when_further_discussion_unlikely_to_help():
    debate = Debate(max_rounds=5)
    # No evidence anywhere, so no evidence-backed consensus can fire
    # early; two quiet rounds (no new evidence) stop the debate.
    claims = [DebateClaim("c1", "a1", "claim one", evidence=[])]
    out = debate.run(claims, rounds_of_evidence=[{}, {}])
    assert out["rounds_used"] == 2
    assert "unlikely to improve" in out["stop_reason"]


def test_debate_escalates_unresolved_conflict_and_vote_is_not_truth():
    debate = Debate(max_rounds=2)
    claims = [DebateClaim("c1", "a1", "claim one", evidence=["e1"]),
              DebateClaim("c2", "a2", "claim two", evidence=["e2"])]
    out = debate.run(claims, contradictions=[("c1", "c2")],
                     votes={"c2": 99, "c1": 1})
    assert out["escalated"] is True
    assert out["remaining_uncertainty"]
    assert out["remaining_uncertainty"][0]["status"] == "remaining uncertainty"
    assert out["vote_tally"] == {"c2": 99, "c1": 1}
    # The vote tally is recorded but never decides standing.
    assert "never decide" in out["vote_note"]


def test_debate_resolves_when_one_side_has_deciding_evidence():
    debate = Debate(max_rounds=3)
    claims = [DebateClaim("c1", "a1", "claim one", evidence=["e1"]),
              DebateClaim("c2", "a2", "claim two", evidence=[])]
    out = debate.run(claims, contradictions=[("c1", "c2")])
    assert out["escalated"] is False
    assert out["remaining_uncertainty"] == []


def test_planner_acceptance_criteria_wiring():
    from app.domain import Budget, TaskContract
    from app.planner import acceptance_criteria
    task = TaskContract(task_id="t1", mission_id="m1",
                        description="research", kind="research",
                        acceptance_tests=["has_claims", "claims_have_sources"],
                        budget_limit=Budget(amount=1.0))
    criteria = acceptance_criteria([task])
    assert [c["id"] for c in criteria["t1"]] == ["has_claims",
                                                 "claims_have_sources"]


def test_criteria_come_from_plan_and_evaluate_per_criterion():
    criteria = criteria_from_plan(["has_claims", "claims_have_sources"])
    assert [c["id"] for c in criteria] == ["has_claims", "claims_have_sources"]
    assert all("claim" in c["description"] for c in criteria)
    output = {"claims": [{"text": "x", "source_ids": ["s1"]}]}
    out = evaluate_criteria(criteria, output)
    assert out["passed"] is True
    assert all(r["passed"] for r in out["criteria"])
    bad = evaluate_criteria(criteria, {"claims": [{"text": "x"}]})
    assert bad["passed"] is False
    by_id = {r["id"]: r for r in bad["criteria"]}
    assert by_id["has_claims"]["passed"] is True
    assert by_id["claims_have_sources"]["passed"] is False
    assert "not met" in by_id["claims_have_sources"]["detail"]


def test_criteria_ignore_producer_self_claim():
    criteria = criteria_from_plan(["has_claims"])
    # The agent claims success; the checker looks at the output only.
    out = evaluate_criteria(criteria, {"claims": [], "self_claim": "passed"})
    assert out["passed"] is False


def test_unknown_plan_test_fails_honestly_not_silently():
    criteria = criteria_from_plan(["no_such_test"])
    out = evaluate_criteria(criteria, {"anything": True})
    assert out["passed"] is False
    assert "no check is defined" in out["criteria"][0]["detail"]


def test_result_dedup_processes_same_result_once():
    dedup = ResultDedup()
    assert dedup.is_duplicate("t1", {"v": 1}) is False
    assert dedup.is_duplicate("t1", {"v": 1}) is True
    assert dedup.is_duplicate("t1", {"v": 2}) is False


def test_contradiction_metadata_explicit():
    meta = flag_contradiction("c1", "c2", "opposite conclusions")
    assert meta["status"] == "unresolved"
    assert meta["claims"] == ["c1", "c2"]


def test_partial_result_accepted_but_never_presented_complete():
    rec = accept_partial_result({"partial": True, "text": "half"})
    assert rec["accepted"] is True and rec["complete"] is False
    assert "incomplete" in rec["presentation"]
    rec = accept_partial_result({"partial": False, "text": "all"})
    assert rec["complete"] is True
