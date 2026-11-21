"""AgentQon — ambiguous external actions (gap-seventhdoc, group D).

HIGHEST-STAKES group. Retries today are blind to actual outcome; this
module is the fix. The ledger here is a NEW durable record shape —
distinct from the existing agent performance ledger, which is never
cited as covering it. Stdlib only.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# Side-effect classes, weakest to strongest.
READ_ONLY_TOOLS = {"web.read", "artifact.read", "reach.read", "reach.rss",
                   "reach.github", "reach.youtube", "reach.reddit",
                   "reach.exa", "code.symbol_context", "code.impact",
                   "code.search"}
LOCALLY_MODIFYING_TOOLS = {"artifact.write", "code.execute"}
EXTERNALLY_MODIFYING_TOOLS = {"email.send", "message.send"}
IRREVERSIBLE_TOOLS = {"purchase.create", "file.delete", "deploy.production"}


def classify_side_effect(tool: str) -> dict:
    """Read-only / locally modifying / externally modifying /
    irreversible. Stronger confirmation + recovery planning as impact
    rises. Generalizes the existing read-only/mutating tool split."""
    if tool in READ_ONLY_TOOLS:
        return {"tool": tool, "class": "read_only",
                "confirmation": "none",
                "recovery_plan_required": False,
                "reason": "This only reads; nothing changes."}
    if tool in LOCALLY_MODIFYING_TOOLS:
        return {"tool": tool, "class": "locally_modifying",
                "confirmation": "normal",
                "recovery_plan_required": False,
                "reason": "This changes local work only."}
    if tool in EXTERNALLY_MODIFYING_TOOLS:
        return {"tool": tool, "class": "externally_modifying",
                "confirmation": "explicit",
                "recovery_plan_required": True,
                "reason": "This changes something outside this machine."}
    if tool in IRREVERSIBLE_TOOLS:
        return {"tool": tool, "class": "irreversible",
                "confirmation": "explicit_with_recovery_plan",
                "recovery_plan_required": True,
                "reason": "This cannot be undone — payment, deletion, or "
                "a live deployment."}
    return {"tool": tool, "class": "unknown",
            "confirmation": "explicit_with_recovery_plan",
            "recovery_plan_required": True,
            "reason": "Unknown tool: treated as high impact until classified."}


class ExternalActionLedger:
    """Durable record: intended action, approval, submission status,
    external identifiers, confirmed result, reconciliation status."""

    def __init__(self):
        self.actions: dict[str, dict] = {}

    def intend(self, action_id: str, tool: str, args: dict,
               approval_id: Optional[str] = None) -> dict:
        rec = {"action_id": action_id, "tool": tool, "args": dict(args),
               "approval_id": approval_id, "submission_status": "intended",
               "external_id": None, "confirmed_result": None,
               "reconciliation": "not_started"}
        self.actions[action_id] = rec
        return rec

    def submit(self, action_id: str, external_id: Optional[str] = None) -> dict:
        rec = self.actions[action_id]
        rec["submission_status"] = "submitted"
        rec["external_id"] = external_id
        return rec

    def mark_timeout(self, action_id: str) -> dict:
        rec = self.actions[action_id]
        rec["submission_status"] = "timed_out_after_submit"
        rec["reconciliation"] = "pending"
        return rec

    def confirm(self, action_id: str, result: dict) -> dict:
        rec = self.actions[action_id]
        rec["submission_status"] = "confirmed"
        rec["confirmed_result"] = dict(result)
        rec["reconciliation"] = "confirmed"
        return rec

    def get(self, action_id: str) -> Optional[dict]:
        return self.actions.get(action_id)


def reconcile_uncertain_outcome(ledger: ExternalActionLedger, action_id: str,
                                outcome_checker: Callable[[dict], Optional[dict]]) -> dict:
    """BINDING (D17): an API request that timed out after submission is
    CHECKED for its actual outcome BEFORE any retry. Blind retry is
    forbidden here by construction: this function never retries.
      checker returns a result dict  -> the operation DID happen:
        do not retry; mark confirmed.
      checker returns None           -> proven not to have happened:
        safe to retry.
      checker raises / returns 'unknown' -> outcome unknown: must NOT
        be auto-treated as failed; needs human review."""
    rec = ledger.get(action_id)
    if rec is None:
        return {"decision": "unknown_action",
                "reason": "No such action is on record."}
    if rec["submission_status"] != "timed_out_after_submit":
        return {"decision": "not_applicable",
                "reason": "This action did not time out after submission."}
    try:
        outcome = outcome_checker(rec)
    except Exception as exc:
        rec["reconciliation"] = "unknown"
        return {"decision": "manual_review",
                "reason": f"The outcome could not be checked ({exc}); "
                "the action must not be retried or counted as failed "
                "until someone checks it."}
    if outcome == "unknown":
        rec["reconciliation"] = "unknown"
        return {"decision": "manual_review",
                "reason": "The outcome is still unknown; do not retry and "
                "do not count this as failed."}
    if outcome is None:
        rec["reconciliation"] = "proven_not_happened"
        return {"decision": "safe_to_retry",
                "reason": "Checked: the operation did not happen, so a "
                "retry cannot duplicate it."}
    ledger.confirm(action_id, outcome)
    return {"decision": "do_not_retry",
            "reason": "Checked: the operation already happened, so "
            "retrying would duplicate it. It is now marked confirmed.",
            "confirmed_result": outcome}


def plan_compensation(steps: list[dict]) -> dict:
    """Safe compensations for partially succeeded multi-step operations.
    Steps run in reverse order. Genuinely reversible steps get their
    compensation; irreversible steps get NO fake undo — they are listed
    as needing manual handling."""
    compensations, manual = [], []
    for step in reversed(steps):
        if not step.get("completed"):
            continue
        if step.get("reversible") and step.get("compensation"):
            compensations.append({"step_id": step["step_id"],
                                  "compensation": step["compensation"]})
        else:
            manual.append({"step_id": step["step_id"],
                           "reason": "This step cannot be undone safely; "
                           "it needs manual handling, not a fake undo."})
    return {"compensations": compensations, "manual_steps": manual}


def recovery_confidence_gate(steps: list[dict]) -> dict:
    """Before resuming an interrupted mission, partition every step:
    completed / may-have-completed / safe-to-execute. Unknown outcomes
    are NEVER auto-treated as failures."""
    completed = [s["step_id"] for s in steps if s.get("status") == "completed"]
    may_have = [s["step_id"] for s in steps
                if s.get("status") in ("timed_out_after_submit", "unknown")]
    safe = [s["step_id"] for s in steps if s.get("status") == "not_started"]
    failed = [s["step_id"] for s in steps if s.get("status") == "failed"]
    return {"completed": completed, "may_have_completed": may_have,
            "safe_to_execute": safe, "failed": failed,
            "resume_plan": ("Resume only the safe steps; check each "
                            "may-have-completed step before touching it; "
                            "never re-run a may-have-completed step blindly."),
            "unknown_treated_as_failure": False}
