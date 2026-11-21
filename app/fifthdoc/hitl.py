"""§3 Human-in-the-loop workflows.

The plan gate is DISTINCT from mission approval: approving a plan approves
the shape of the work only. Risky actions inside the plan still need their
own per-step approvals. In single-user mode the reviewer is the owner —
stated honestly, not dressed up as delegation. Accounts-mode delegation
applies only where the deployment supports multiple accounts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time

# Operation types that ALWAYS require a review before they run.
MANDATORY_REVIEW = {"external.send", "file.delete", "dependency.change",
                    "payment.send", "publish", "shell.run", "remote.change"}

TIMEOUT_POLICIES = ("wait", "auto_reject", "escalate")


@dataclass
class Gate:
    gate_id: str
    kind: str  # "plan" | "step" | "mission"
    mission_id: str
    step_id: str | None = None
    payload: dict = field(default_factory=dict)
    state: str = "waiting"  # waiting | approved | rejected | timed_out
    reviewer: str = "owner"
    feedback: str = ""
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float | None = None


@dataclass
class Clarification:
    request_id: str
    mission_id: str
    question: str
    answer: str | None = None
    state: str = "open"


class Hitl:
    def __init__(self, timeout_policy: str = "wait"):
        if timeout_policy not in TIMEOUT_POLICIES:
            raise ValueError(f"Unknown timeout policy: {timeout_policy}")
        self.timeout_policy = timeout_policy
        self.gates: dict[str, Gate] = {}
        self.clarifications: dict[str, Clarification] = {}

    def requires_review(self, operation: str) -> bool:
        return operation in MANDATORY_REVIEW

    def open_gate(self, gate: Gate) -> Gate:
        self.gates[gate.gate_id] = gate
        return gate

    def approve_plan(self, gate_id: str) -> dict:
        """Approving the PLAN does not blanket-approve risky steps."""
        gate = self.gates.get(gate_id)
        if gate is None or gate.kind != "plan":
            return {"ok": False, "reason": "No such plan gate."}
        gate.state = "approved"
        return {"ok": True, "state": "approved",
                "blanket_approval_of_risky_actions": False,
                "note": "Plan approved. Risky steps still need their own approval."}

    def approve(self, gate_id: str, edited_payload: dict | None = None) -> dict:
        gate = self.gates.get(gate_id)
        if gate is None:
            return {"ok": False, "reason": "No such approval gate."}
        if edited_payload is not None:
            gate.payload = edited_payload  # editable approval payload
        gate.state = "approved"
        return {"ok": True, "state": "approved",
                "payload_edited": edited_payload is not None}

    def reject(self, gate_id: str, feedback: str) -> dict:
        gate = self.gates.get(gate_id)
        if gate is None:
            return {"ok": False, "reason": "No such approval gate."}
        gate.state = "rejected"
        gate.feedback = feedback
        return {"ok": True, "state": "rejected", "feedback": feedback}

    def check_timeouts(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        out = []
        for gate in self.gates.values():
            if gate.state != "waiting" or gate.timeout_seconds is None:
                continue
            if now - gate.created_at > gate.timeout_seconds:
                if self.timeout_policy == "auto_reject":
                    gate.state = "rejected"
                elif self.timeout_policy == "escalate":
                    gate.state = "escalated"
                out.append({"gate_id": gate.gate_id, "state": gate.state,
                            "policy": self.timeout_policy})
        return out

    def ask(self, req: Clarification) -> Clarification:
        self.clarifications[req.request_id] = req
        return req

    def answer(self, request_id: str, answer: str) -> dict:
        req = self.clarifications.get(request_id)
        if req is None:
            return {"ok": False, "reason": "No such clarification request."}
        req.answer = answer
        req.state = "answered"
        return {"ok": True, "state": "answered"}

    def resume_checkpoint(self, mission_id: str, step_id: str) -> dict:
        """Resume from the exact supported checkpoint after approval."""
        approved = [g for g in self.gates.values()
                    if g.mission_id == mission_id and g.state == "approved"]
        if not approved:
            return {"ok": False, "reason": "Nothing approved to resume from."}
        return {"ok": True, "mission_id": mission_id,
                "resume_from_step": step_id, "approved_gates": len(approved)}

    def delegation_note(self) -> str:
        return ("In single-user mode the reviewer is the owner. Delegation to "
                "another person applies only in accounts mode, where the "
                "deployment supports more than one account.")
