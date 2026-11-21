"""AgentQon — automatic improvement, with honesty guardrails.

- Failures link to configuration versions (D1): a failure record names
  the config version it ran under, so "did the settings change cause
  this?" is answerable from the record.
- Skill regression detection (D2): a skill version whose outcomes get
  worse than the previous version, on enough runs, is flagged.
- Routing-policy comparison (D3): policies compared on the SAME tasks.
- Cost + quality together (D4): every summary carries both. A summary
  with only one is refused by ``require_cost_and_quality``.
- Drift detection (D5): recent outcomes vs a baseline, flagged past a
  threshold.
- Test-before-apply (D6): a proposed config change is evaluated on
  reference tasks first; the evaluation recommends, it never applies.
- Learn only from verified outcomes (D7): promoting a model for a
  category requires representative, verified evidence. One success is
  anecdote, not evidence — that rule is ENCODED in
  ``MIN_EVIDENCE_RUNS`` / ``MIN_EVIDENCE_TASKS`` and enforced by
  ``promote_model``, not left as advice.
"""
from __future__ import annotations

MIN_EVIDENCE_RUNS = 5
MIN_EVIDENCE_TASKS = 3
REGRESSION_MIN_RUNS = 5
DRIFT_THRESHOLD = 0.20


def link_failure(*, mission_id: str, error: str, config_version: int,
                 model: str, skill_versions: dict | None = None,
                 prompt_version: str = "") -> dict:
    """A failure record that names everything needed to reproduce the
    conditions: config version, model, prompt version, skill versions."""
    return {"mission_id": mission_id, "error": error,
            "config_version": config_version, "model": model,
            "prompt_version": prompt_version,
            "skill_versions": dict(skill_versions or {}),
            "note": ("This failure ran under configuration version "
                     f"{config_version}. Compare with failures under other "
                     "versions before blaming the model.")}


def _rate(outcomes: list[dict]) -> tuple[float, int]:
    n = len(outcomes)
    if n == 0:
        return 0.0, 0
    return sum(1 for o in outcomes if o.get("success")) / n, n


def detect_skill_regression(skill_id: str, *, before: list[dict],
                            after: list[dict]) -> dict:
    """Flag a skill update that worsened outcomes. Requires enough
    runs on both sides; with too few runs the verdict is 'not enough
    evidence', never a false flag and never a false all-clear."""
    before_rate, before_n = _rate(before)
    after_rate, after_n = _rate(after)
    if before_n < REGRESSION_MIN_RUNS or after_n < REGRESSION_MIN_RUNS:
        return {"skill_id": skill_id, "flagged": False,
                "verdict": "not enough evidence",
                "note": (f"Only {before_n} runs before and {after_n} after — "
                         "too few to judge a regression fairly."),
                "before_rate": before_rate, "after_rate": after_rate}
    flagged = after_rate < before_rate
    return {"skill_id": skill_id, "flagged": flagged,
            "verdict": "regression flagged" if flagged else "no regression",
            "before_rate": round(before_rate, 4),
            "after_rate": round(after_rate, 4),
            "note": ("A flagged skill should be rolled back or reviewed "
                     "before further use.") if flagged else ""}


def compare_routing_policies(results_by_policy: dict[str, list[dict]]) -> dict:
    """Compare routing policies on the same tasks. ``results_by_policy``
    maps policy name -> list of {task_id, success, cost_usd, quality}.
    Policies that did not run the same task set are labelled as not
    directly comparable — comparing different tasks would be dishonest."""
    task_sets = {tuple(sorted({r["task_id"] for r in rows}))
                 for rows in results_by_policy.values()}
    comparable = len(task_sets) <= 1
    rows = []
    for policy, outcomes in results_by_policy.items():
        rate, n = _rate(outcomes)
        cost = round(sum(float(o.get("cost_usd") or 0.0) for o in outcomes), 6)
        qualities = [float(o["quality"]) for o in outcomes if o.get("quality") is not None]
        mean_quality = round(sum(qualities) / len(qualities), 4) if qualities else None
        rows.append({"policy": policy, "runs": n,
                     "success_rate": round(rate, 4),
                     "total_cost_usd": cost, "mean_quality": mean_quality})
    return {"comparable": comparable,
            "comparability_note": ("Same tasks compared." if comparable
                                   else "Policies ran different tasks — "
                                   "not directly comparable."),
            "rows": rows}


def require_cost_and_quality(summary: dict) -> dict:
    """D4 enforcement: a cost/quality summary must carry BOTH. A
    summary with only cost or only quality is refused with a plain
    reason, so no report can ever show one without the other."""
    has_cost = summary.get("total_cost_usd") is not None
    has_quality = summary.get("mean_quality") is not None
    if has_cost and has_quality:
        return {"ok": True, "summary": summary}
    missing = "quality" if has_cost else "cost"
    return {"ok": False,
            "reason": (f"This summary has no {missing} figure. Cost and "
                       "quality must always be shown together — one alone "
                       "would mislead.")}


def detect_drift(*, baseline: list[dict], recent: list[dict]) -> dict:
    """Flag behaviour drifting from baseline. Drift is a change in
    success rate past DRIFT_THRESHOLD, in either direction (a sudden
    improvement is also drift — it may mean the task changed, not the
    model). Too few recent runs = 'not enough evidence'."""
    base_rate, base_n = _rate(baseline)
    recent_rate, recent_n = _rate(recent)
    if recent_n < REGRESSION_MIN_RUNS:
        return {"flagged": False, "verdict": "not enough evidence",
                "baseline_rate": round(base_rate, 4),
                "recent_rate": round(recent_rate, 4)}
    delta = recent_rate - base_rate
    flagged = abs(delta) >= DRIFT_THRESHOLD
    return {"flagged": flagged,
            "verdict": "drift flagged" if flagged else "stable",
            "baseline_rate": round(base_rate, 4),
            "recent_rate": round(recent_rate, 4),
            "delta": round(delta, 4),
            "note": ("Outcomes moved noticeably from baseline — check "
                     "whether the model, the settings or the tasks changed.")
            if flagged else ""}


def evaluate_change(*, proposed_config: dict, reference_tasks: list,
                    scorer: object) -> dict:
    """D6: evaluate a proposed config change on reference tasks BEFORE
    applying it. ``scorer(task, config) -> {success, cost_usd, quality}``
    is supplied by the caller (a real run or a stub). This function
    recommends; it never applies the change itself."""
    outcomes = [scorer(task, proposed_config) for task in reference_tasks]
    rate, n = _rate(outcomes)
    cost = round(sum(float(o.get("cost_usd") or 0.0) for o in outcomes), 6)
    qualities = [float(o["quality"]) for o in outcomes if o.get("quality") is not None]
    mean_quality = round(sum(qualities) / len(qualities), 4) if qualities else None
    return {"proposed_config": dict(proposed_config), "runs": n,
            "success_rate": round(rate, 4), "total_cost_usd": cost,
            "mean_quality": mean_quality,
            "recommendation": ("Evaluate against the current configuration "
                               "on the same tasks before applying — a single "
                               "evaluation never justifies a change alone."),
            "applied": False}


def promote_model(*, model: str, category: str,
                  verified_outcomes: list[dict]) -> dict:
    """D7, encoded: promote a model for a category ONLY on verified,
    representative evidence — at least MIN_EVIDENCE_RUNS verified
    outcomes across at least MIN_EVIDENCE_TASKS distinct tasks, with a
    success rate above half. Anything less is refused as anecdote."""
    verified = [o for o in verified_outcomes if o.get("verified")]
    tasks = {o.get("task_id") for o in verified}
    rate, n = _rate(verified)
    if n < MIN_EVIDENCE_RUNS or len(tasks) < MIN_EVIDENCE_TASKS:
        return {"promoted": False, "model": model, "category": category,
                "reason": (f"Only {n} verified runs across {len(tasks)} tasks — "
                           "one success is anecdote, not evidence. A model "
                           "is promoted only on representative, verified "
                           "outcomes."),
                "required": {"runs": MIN_EVIDENCE_RUNS,
                             "tasks": MIN_EVIDENCE_TASKS}}
    if rate <= 0.5:
        return {"promoted": False, "model": model, "category": category,
                "reason": (f"Verified success rate is {rate:.0%} — too low "
                           "to promote."),
                "success_rate": round(rate, 4)}
    return {"promoted": True, "model": model, "category": category,
            "success_rate": round(rate, 4), "runs": n,
            "tasks": len(tasks)}
