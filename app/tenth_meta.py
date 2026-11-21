"""Tenth audit document, Group 1 — meta-verification (stdlib only).

Generalizes the evaluator's verdict honesty and deliverable checks into
standalone, testable machinery: validators are themselves measured against
controlled examples with known answers, and independent checks are produced
as oracles rather than assumed. All user-facing notes use plain words.
"""
from __future__ import annotations

import difflib
import json
import statistics
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 1: Verifier Reliability Lab
@dataclass
class KnownCase:
    """A controlled example whose correct answer is known in advance."""

    case_id: str
    is_defect: bool  # True when the example is deliberately wrong/broken
    expected_pass: bool  # what a correct validator should say
    note: str = ""


@dataclass
class LabResult:
    total: int
    false_passes: int
    false_failures: int
    missed_defects: int
    caught_defects: int
    false_pass_rate: float
    false_failure_rate: float
    details: list = field(default_factory=list)

    def plain_summary(self) -> str:
        if self.total == 0:
            return "No controlled examples were run, so reliability cannot be judged."
        return (
            f"Out of {self.total} controlled examples, the validator wrongly passed "
            f"{self.false_passes}, wrongly failed {self.false_failures}, and missed "
            f"{self.missed_defects} defects."
        )


class VerifierReliabilityLab:
    """Run a validator against known-answer cases and measure its mistakes."""

    def __init__(self):
        self._cases: list[KnownCase] = []

    def add_case(self, case: KnownCase) -> None:
        self._cases.append(case)

    def run(self, validator) -> LabResult:
        """validator(case) -> bool (True = passes the example)."""
        details, fp = [], 0
        ff = 0
        missed = 0
        caught = 0
        for case in self._cases:
            verdict = bool(validator(case))
            if case.is_defect and verdict:
                fp += 1
                missed += 1
                kind = "false_pass"
            elif case.is_defect and not verdict:
                caught += 1
                kind = "caught_defect"
            elif not case.is_defect and not verdict:
                ff += 1
                kind = "false_failure"
            else:
                kind = "correct"
            details.append({"case_id": case.case_id, "kind": kind, "verdict": verdict})
        total = len(self._cases) or 1
        return LabResult(
            total=len(self._cases),
            false_passes=fp,
            false_failures=ff,
            missed_defects=missed,
            caught_defects=caught,
            false_pass_rate=fp / total,
            false_failure_rate=ff / total,
            details=details,
        )


# ------------------------------------------------------- 2: Verification Oracle Generator
@dataclass
class OracleResult:
    kind: str  # "calculation" | "file" | "application"
    agrees: bool
    primary: object
    oracle: object
    independent: bool = True
    note: str = ""


class OracleGenerator:
    """Produce a check that does NOT share the primary implementation.

    A critic or evaluator re-reading the same work is not an oracle. An
    oracle re-does the work another way: a second implementation for a
    calculation, a real parser for a file, a real integration run for an app.
    """

    def calculation(self, primary_fn, oracle_fn, inputs):
        primary = primary_fn(inputs)
        oracle = oracle_fn(inputs)
        return OracleResult(
            kind="calculation",
            agrees=primary == oracle,
            primary=primary,
            oracle=oracle,
            note="Checked with a separate implementation."
            if primary == oracle
            else "The two implementations disagree, so the result is not trusted.",
        )

    def file_check(self, text: str, parser):
        """parser(text) must parse with real rules, not echo the writer."""
        try:
            parsed = parser(text)
            return OracleResult(kind="file", agrees=True, primary=text,
                                oracle=parsed,
                                note="The file parses cleanly with an independent parser.")
        except Exception as exc:  # noqa: BLE001 - oracle failure is data
            return OracleResult(kind="file", agrees=False, primary=text, oracle=None,
                                note=f"The file does not parse: {exc}")

    def application_check(self, run_fn):
        """run_fn() performs a real integration run and returns (passed, evidence)."""
        passed, evidence = run_fn()
        return OracleResult(
            kind="application",
            agrees=bool(passed),
            primary=None,
            oracle=evidence,
            note="Checked with a real integration run."
            if passed
            else "The real integration run failed.",
        )


# ---------------------------------------------------------- 3: Validator Drift Monitor
class ValidatorDriftMonitor:
    """Flag reliability getting worse after a change to rules, data, or model."""

    def __init__(self, *, window: int = 10, tolerance: float = 0.10):
        self._window = window
        self._tolerance = tolerance
        self._runs: list[dict] = []  # {version, false_pass_rate, false_failure_rate}

    def record(self, version: str, lab_result: LabResult) -> None:
        self._runs.append({
            "version": version,
            "false_pass_rate": lab_result.false_pass_rate,
            "false_failure_rate": lab_result.false_failure_rate,
        })

    def check(self) -> dict:
        if len(self._runs) < 2:
            return {"drift": False, "note": "Not enough runs yet to judge drift."}
        baseline = self._runs[0]
        recent = self._runs[-self._window:]
        avg_fp = statistics.fmean(r["false_pass_rate"] for r in recent)
        avg_ff = statistics.fmean(r["false_failure_rate"] for r in recent)
        drifted = (
            avg_fp - baseline["false_pass_rate"] > self._tolerance
            or avg_ff - baseline["false_failure_rate"] > self._tolerance
        )
        return {
            "drift": drifted,
            "baseline_version": baseline["version"],
            "recent_version": recent[-1]["version"],
            "recent_false_pass_rate": avg_fp,
            "recent_false_failure_rate": avg_ff,
            "note": "Validator reliability has got worse since the baseline run."
            if drifted
            else "Validator reliability is holding steady.",
        }


# ------------------------------------------- 4: Independent Verification Diversity Score
@dataclass
class CheckDescription:
    check_id: str
    model: str
    prompt: str
    evidence: str
    reasoning_path: str


def diversity_score(checks: list[CheckDescription]) -> dict:
    """How independent are these checks, across four axes?

    Score 0 = identical on every axis; 1 = different on every axis.
    Role separation alone (different names, same model/prompt) scores low.
    """
    axes = ("model", "prompt", "evidence", "reasoning_path")
    if len(checks) < 2:
        return {"score": 0.0, "pairs": [],
                "note": "One check alone cannot be independent of itself."}
    pairs = []
    for i in range(len(checks)):
        for j in range(i + 1, len(checks)):
            differing = sum(
                1 for ax in axes if getattr(checks[i], ax) != getattr(checks[j], ax)
            )
            pairs.append({
                "pair": (checks[i].check_id, checks[j].check_id),
                "score": differing / len(axes),
                "differing_axes": [ax for ax in axes
                                   if getattr(checks[i], ax) != getattr(checks[j], ax)],
            })
    overall = statistics.fmean(p["score"] for p in pairs)
    return {
        "score": overall,
        "pairs": pairs,
        "note": "Checks are genuinely independent."
        if overall >= 0.75
        else "Checks share too much to count as independent verification.",
    }


# ------------------------------------------------------- 5: False-Confidence Detector
def detect_false_confidence(result: dict) -> dict:
    """Flag a result claimed as highly reliable on weak footing.

    Generalizes verdict honesty: a high reliability claim needs tests run,
    real evidence, no contradictions, and a calibrated (previously measured)
    validator behind it.
    """
    flags = []
    claimed = str(result.get("claimed_reliability", "")).lower()
    high = claimed in ("high", "very high", "certain", "guaranteed")
    if high and not result.get("tests_run"):
        flags.append("Claimed as highly reliable but no tests were run.")
    if high and not result.get("evidence"):
        flags.append("Claimed as highly reliable but no evidence was recorded.")
    if result.get("contradictions"):
        flags.append("Findings contradict each other, so the result cannot be highly reliable.")
    if high and not result.get("validator_calibrated"):
        flags.append("The validator behind this result has never been measured for accuracy.")
    if result.get("evidence_strength", "strong") in ("weak", "none"):
        flags.append("The evidence behind this result is weak.")
    return {
        "false_confidence": bool(flags),
        "flags": flags,
        "note": "This result looks more confident than its evidence supports."
        if flags
        else "The claimed reliability matches the evidence.",
    }


def semantic_diff_text(old: str, new: str) -> list[str]:
    """Shared helper: unified diff lines (used by the strategic module too)."""
    return list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
