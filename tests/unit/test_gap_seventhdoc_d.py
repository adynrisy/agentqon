"""Gap-seventhdoc group D: ambiguous external actions (highest stakes)."""
from app.external_actions import (
    ExternalActionLedger, classify_side_effect, plan_compensation,
    reconcile_uncertain_outcome, recovery_confidence_gate)


def test_d17_reconciler_never_retries_blind():
    ledger = ExternalActionLedger()
    ledger.intend("a1", "purchase.create", {"item": "book"}, approval_id="ap1")
    ledger.submit("a1", external_id="ext-9")
    ledger.mark_timeout("a1")
    # Checker proves it happened -> do not retry, confirmed.
    out = reconcile_uncertain_outcome(
        ledger, "a1", lambda rec: {"order_id": "ext-9", "status": "paid"})
    assert out["decision"] == "do_not_retry"
    assert ledger.get("a1")["submission_status"] == "confirmed"

    ledger.intend("a2", "email.send", {"to": "x"})
    ledger.submit("a2")
    ledger.mark_timeout("a2")
    out = reconcile_uncertain_outcome(ledger, "a2", lambda rec: None)
    assert out["decision"] == "safe_to_retry"

    ledger.intend("a3", "deploy.production", {})
    ledger.submit("a3")
    ledger.mark_timeout("a3")
    out = reconcile_uncertain_outcome(
        ledger, "a3", lambda rec: (_ for _ in ()).throw(RuntimeError("down")))
    assert out["decision"] == "manual_review"
    assert ledger.get("a3")["reconciliation"] == "unknown"


def test_d18_ledger_records_full_lifecycle():
    ledger = ExternalActionLedger()
    ledger.intend("a1", "email.send", {"to": "x"}, approval_id="ap1")
    ledger.submit("a1", external_id="msg-1")
    rec = ledger.get("a1")
    assert rec["approval_id"] == "ap1" and rec["external_id"] == "msg-1"
    assert rec["submission_status"] == "submitted"
    assert rec["reconciliation"] == "not_started"


def test_d19_compensation_distinguishes_reversible():
    out = plan_compensation([
        {"step_id": "s1", "completed": True, "reversible": True,
         "compensation": "cancel reservation"},
        {"step_id": "s2", "completed": True, "reversible": False},
        {"step_id": "s3", "completed": False, "reversible": True,
         "compensation": "noop"}])
    assert [c["step_id"] for c in out["compensations"]] == ["s1"]
    assert [m["step_id"] for m in out["manual_steps"]] == ["s2"]


def test_d20_side_effect_classes_rise_with_impact():
    assert classify_side_effect("web.read")["class"] == "read_only"
    assert classify_side_effect("artifact.write")["class"] == "locally_modifying"
    assert classify_side_effect("email.send")["class"] == "externally_modifying"
    assert classify_side_effect("email.send")["recovery_plan_required"] is True
    irr = classify_side_effect("purchase.create")
    assert irr["class"] == "irreversible"
    assert irr["confirmation"] == "explicit_with_recovery_plan"
    assert classify_side_effect("mystery.tool")["class"] == "unknown"


def test_d21_recovery_gate_never_treats_unknown_as_failure():
    out = recovery_confidence_gate([
        {"step_id": "s1", "status": "completed"},
        {"step_id": "s2", "status": "timed_out_after_submit"},
        {"step_id": "s3", "status": "unknown"},
        {"step_id": "s4", "status": "not_started"},
        {"step_id": "s5", "status": "failed"}])
    assert out["may_have_completed"] == ["s2", "s3"]
    assert out["safe_to_execute"] == ["s4"]
    assert out["failed"] == ["s5"]
    assert out["unknown_treated_as_failure"] is False
    assert "s2" not in out["failed"] and "s3" not in out["failed"]
