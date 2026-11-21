"""§15 Disagreement resolution — full protocol.

Majority agreement NEVER selects the answer by itself: similar models,
prompts and evidence share blind spots. The protocol compares evidence and
assumptions, seeks blind review and independent verification, preserves
the minority report, and escalates to a human when it cannot resolve.
Builds on the panel's critic / evaluator / verdict seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    agent_id: str
    answer: str
    evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    status: str  # resolved | unresolved | escalated
    chosen: str | None
    summary: str
    minority_report: list[dict] = field(default_factory=list)
    method: str = ""


def detect(positions: list[Position]) -> dict:
    answers = {p.answer for p in positions}
    return {"disagreement": len(answers) > 1,
            "distinct_answers": sorted(answers),
            "positions": len(positions)}


def compare_evidence(positions: list[Position]) -> dict:
    all_evidence: dict[str, list[str]] = {}
    for pos in positions:
        for item in pos.evidence:
            all_evidence.setdefault(item, []).append(pos.agent_id)
    shared = [e for e, agents in all_evidence.items() if len(agents) > 1]
    unique = {p.agent_id: [e for e in p.evidence if e not in shared]
              for p in positions}
    return {"shared_evidence": shared, "unique_evidence": unique,
            "shared_blind_spot_warning": bool(shared) and len(positions) > 1}


def compare_assumptions(positions: list[Position]) -> dict:
    differing = []
    first = positions[0].assumptions if positions else []
    for pos in positions[1:]:
        if set(pos.assumptions) != set(first):
            differing.append(pos.agent_id)
    return {"assumptions_differ_for": differing,
            "assumptions": {p.agent_id: p.assumptions for p in positions}}


def resolve(positions: list[Position],
            independent_check: dict[str, bool] | None = None,
            tie_breaker: str | None = None) -> Resolution:
    """independent_check maps answer -> verified-true by an independent check.
    Without independent verification, an unresolved disagreement is escalated,
    never settled by vote count."""
    detection = detect(positions)
    if not detection["disagreement"]:
        return Resolution(status="resolved", chosen=positions[0].answer,
                          summary="All positions agree.", method="unanimous")
    independent_check = independent_check or {}
    verified = [a for a, ok in independent_check.items() if ok]
    minority = [{"agent_id": p.agent_id, "answer": p.answer,
                 "evidence": p.evidence, "assumptions": p.assumptions}
                for p in positions]
    if len(verified) == 1:
        chosen = verified[0]
        rest = [m for m in minority if m["answer"] != chosen]
        return Resolution(status="resolved", chosen=chosen,
                          summary="Resolved by independent verification, not "
                                  "by vote count.",
                          minority_report=rest, method="independent_verification")
    if tie_breaker:
        rest = [m for m in minority if m["answer"] != tie_breaker]
        return Resolution(status="resolved", chosen=tie_breaker,
                          summary="Resolved by the named tie-breaker route.",
                          minority_report=rest, method="tie_breaker")
    return Resolution(status="escalated", chosen=None,
                      summary="Disagreement could not be resolved on evidence "
                              "alone. Sent to a human with every position kept.",
                      minority_report=minority, method="human_escalation")


def label_unresolved(resolution: Resolution) -> str:
    if resolution.status == "resolved":
        return f"Resolved ({resolution.method}). Minority report kept."
    return ("Unresolved disagreement — no answer was picked. The minority "
            "report is preserved with the record.")
