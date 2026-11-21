"""Tenth audit document, Group 4 — communication and decision management
(stdlib only). Generalizes the strict-JSON protocol, mission states and
output-schema versions into versioned message contracts, plus the
decision/commitment memory a mission needs to stay coherent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


# ------------------------------------------- 19: Agent Message Contract Registry
class MessageContractRegistry:
    """Versioned formats for assignments, handoffs, progress, evidence and
    results. Incompatible messages are rejected or safely migrated — never
    silently misread."""

    def __init__(self):
        self._schemas: dict[tuple[str, int], dict] = {}
        self._migrations: dict[tuple[str, int, int], object] = {}

    def register(self, kind: str, version: int, *, required_fields,
                 optional_fields=()) -> None:
        self._schemas[(kind, version)] = {
            "required": list(required_fields), "optional": list(optional_fields)}

    def register_migration(self, kind: str, from_version: int, to_version: int,
                           fn) -> None:
        self._migrations[(kind, from_version, to_version)] = fn

    def validate(self, message: dict, *, target_version: int | None = None) -> dict:
        kind = message.get("kind")
        version = message.get("version")
        schema = self._schemas.get((kind, version))
        if schema is None:
            return {"ok": False, "note": "Unknown message format version."}
        missing = [f for f in schema["required"] if f not in message]
        if missing:
            return {"ok": False, "missing": missing,
                    "note": "The message is missing required fields."}
        if target_version is not None and target_version != version:
            migrate = self._migrations.get((kind, version, target_version))
            if migrate is None:
                return {"ok": False,
                        "note": "No safe migration exists to the target version."}
            migrated = migrate(dict(message))
            migrated["version"] = target_version
            return {"ok": True, "migrated": True, "message": migrated,
                    "note": "Message migrated safely to the newer format."}
        return {"ok": True, "migrated": False, "message": dict(message),
                "note": "Message matches its format."}


# ------------------------------- 20: Shared Vocabulary and Entity Resolution
class Vocabulary:
    """One canonical identifier per concept (file, requirement, component,
    term). Aliases resolve to it, so agents stop treating one concept as
    different objects. Generalizes agent/skill ID rules."""

    def __init__(self):
        self._canonical: dict[str, dict] = {}  # id -> {kind, aliases}
        self._alias_to_id: dict[str, str] = {}

    def register(self, canonical_id: str, *, kind: str, aliases=()) -> dict:
        conflicts = [a for a in aliases
                     if a in self._alias_to_id and self._alias_to_id[a] != canonical_id]
        if conflicts:
            return {"ok": False, "conflicts": conflicts,
                    "note": "Those names already belong to a different concept."}
        self._canonical[canonical_id] = {"kind": kind, "aliases": list(aliases)}
        for alias in aliases:
            self._alias_to_id[alias] = canonical_id
        self._alias_to_id[canonical_id] = canonical_id
        return {"ok": True, "note": "Concept registered."}

    def resolve(self, term: str) -> dict:
        target = self._alias_to_id.get(term)
        if target is None:
            return {"ok": False, "note": "No known concept matches that name."}
        return {"ok": True, "canonical_id": target,
                "kind": self._canonical[target]["kind"],
                "note": "Resolved to the shared concept."}


# --------------------------------------------- 21: Decision Dependency Tracker
class DecisionDependencyTracker:
    """Record which downstream actions, recommendations and artifacts rely
    on a decision, so reversing it names everything needing reassessment."""

    def __init__(self):
        self._decisions: dict[str, dict] = {}
        self._dependents: dict[str, list[dict]] = {}

    def record_decision(self, decision_id: str, *, summary: str) -> None:
        self._decisions[decision_id] = {"summary": summary, "reversed": False}

    def link(self, decision_id: str, *, kind: str, ref: str) -> None:
        self._dependents.setdefault(decision_id, []).append({"kind": kind, "ref": ref})

    def reverse(self, decision_id: str) -> dict:
        if decision_id not in self._decisions:
            return {"ok": False, "note": "Unknown decision."}
        self._decisions[decision_id]["reversed"] = True
        impacted = list(self._dependents.get(decision_id, []))
        return {"ok": True, "impacted": impacted,
                "note": f"Reversing this decision affects {len(impacted)} item(s) "
                        "that need reassessment."}


# --------------------------------------------------------- 22: Commitment Registry
@dataclass
class Commitment:
    commitment_id: str
    kind: str  # "deliverable" | "restriction" | "planned_check"
    description: str
    met: bool | None = None


class CommitmentRegistry:
    """Track explicit promises and compare them against the final result as
    a gap. Generalizes the evaluator's deliverables-vs-requirements check."""

    def __init__(self):
        self._items: dict[str, Commitment] = {}

    def promise(self, commitment_id: str, *, kind: str, description: str) -> None:
        self._items[commitment_id] = Commitment(commitment_id, kind, description)

    def compare(self, final_result: dict) -> dict:
        """final_result: {commitment_id: met_bool} plus 'delivered' notes."""
        gaps = []
        for item in self._items.values():
            met = final_result.get(item.commitment_id)
            item.met = bool(met) if met is not None else False
            if not item.met:
                gaps.append({"commitment_id": item.commitment_id, "kind": item.kind,
                             "description": item.description})
        return {"gaps": gaps,
                "note": "Every promise was kept."
                if not gaps
                else f"{len(gaps)} promise(s) were not kept in the final result."}


# ---------------------------------------------- 23: Communication Loss Detector
class CommunicationLossDetector:
    """Tell stalled work from slow work, and name the recovery step."""

    def __init__(self, *, ack_timeout: float = 300.0, progress_timeout: float = 900.0,
                 now_fn=None):
        self._ack_timeout = ack_timeout
        self._progress_timeout = progress_timeout
        self._now = now_fn or time.time
        self._assignments: dict[str, dict] = {}

    def assign(self, assignment_id: str, *, assigned_at: float | None = None) -> None:
        now = assigned_at if assigned_at is not None else self._now()
        self._assignments[assignment_id] = {"assigned_at": now, "acked_at": None,
                                            "last_progress_at": now}

    def acknowledge(self, assignment_id: str) -> None:
        if assignment_id in self._assignments:
            self._assignments[assignment_id]["acked_at"] = self._now()

    def progress(self, assignment_id: str) -> None:
        if assignment_id in self._assignments:
            self._assignments[assignment_id]["last_progress_at"] = self._now()

    def check(self, assignment_id: str) -> dict:
        rec = self._assignments.get(assignment_id)
        if rec is None:
            return {"state": "unknown", "note": "Unknown assignment."}
        now = self._now()
        if rec["acked_at"] is None and now - rec["assigned_at"] > self._ack_timeout:
            return {"state": "lost",
                    "recovery": "reassign",
                    "note": "No acknowledgment arrived; the assignment looks lost."}
        if now - rec["last_progress_at"] > self._progress_timeout:
            return {"state": "stalled",
                    "recovery": "nudge_then_reassign",
                    "note": "Work started but progress stopped; it looks stalled."}
        if rec["acked_at"] is None:
            return {"state": "awaiting_ack", "recovery": "wait",
                    "note": "Still waiting for acknowledgment."}
        return {"state": "healthy", "recovery": "none",
                "note": "Work is progressing."}


# ------------------------------------------- 24: Agent Handoff Compression Test
def handoff_compression_test(original_state: dict, condensed_handoff: dict) -> dict:
    """A condensed handoff must preserve every critical constraint and
    decision. Compare against the original mission state, not vibes."""
    missing_constraints = [c for c in original_state.get("critical_constraints", [])
                           if c not in condensed_handoff.get("constraints", [])]
    missing_decisions = [d for d in original_state.get("critical_decisions", [])
                         if d not in condensed_handoff.get("decisions", [])]
    missing = [{"kind": "constraint", "item": c} for c in missing_constraints]
    missing += [{"kind": "decision", "item": d} for d in missing_decisions]
    return {"preserved": not missing, "missing": missing,
            "note": "The condensed handoff keeps every critical item."
            if not missing
            else f"The condensed handoff drops {len(missing)} critical item(s)."}
