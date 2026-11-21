"""AgentQon — benchmark datasets, cost benchmarking, experiment tracking.

A small shipped set of representative tasks, kept in the repo, against
which models, prompts, skills and routing strategies can be compared.
Cost, latency and success are recorded per run, and every experiment
records the model, prompt version, skill versions, configuration and
results used, so a comparison can be reproduced later.

Design notes (honesty first):
- This module never calls a model itself. The caller supplies a
  ``run_fn(task) -> (output_text, cost_usd, latency_seconds)`` callable
  (a real model call, or a stub in tests). Benchmarking therefore works
  without a live model and never invents a model result.
- Scoring is deterministic and local: keyword coverage of the task's
  expected points, plus a length sanity check. It measures "did the
  answer cover the brief", not quality of prose — that limit is stated
  wherever a score is shown.
- A task set is "representative" only when it covers more than one kind
  of work. The shipped set covers eight kinds; a single-task comparison
  is labelled as such and must not be used to promote a model (see
  app/platform_improve.py, which enforces that rule).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    kind: str
    prompt: str
    expected_points: tuple[str, ...]
    max_words: int = 400


# The shipped representative set. Prompts are plain briefs, not model
# outputs; expected points are the facts a correct answer must cover.
BENCHMARK_TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask("bench-summary", "summary",
                  "Summarise in plain words why urban trees cool cities.",
                  ("shade", "evaporation", "temperature")),
    BenchmarkTask("bench-brief", "brief",
                  "Write a one-page brief: how urban trees cool cities.",
                  ("trees", "cities", "cool")),
    BenchmarkTask("bench-list", "list",
                  "List three ways to save water at home, with one line each.",
                  ("water", "save")),
    BenchmarkTask("bench-plan", "plan",
                  "Plan a small morning routine in five steps.",
                  ("morning", "steps")),
    BenchmarkTask("bench-compare", "compare",
                  "Compare walking and cycling for a short trip to work.",
                  ("walking", "cycling")),
    BenchmarkTask("bench-rewrite", "rewrite",
                  "Rewrite this in plain words: 'The utilisation of "
                  "vehicular transport contributes to atmospheric pollution.'",
                  ("cars", "pollution")),
    BenchmarkTask("bench-extract", "extract",
                  "From this note, extract the date and the place: "
                  "'Meet in Lahore on Friday at the old library.'",
                  ("Lahore", "Friday")),
    BenchmarkTask("bench-explain", "explain",
                  "Explain in plain words what a budget limit does.",
                  ("budget", "limit")),
)

TASK_KINDS: tuple[str, ...] = tuple(sorted({t.kind for t in BENCHMARK_TASKS}))


def score_output(task: BenchmarkTask, output_text: str) -> dict:
    """Deterministic local score for one output. Returns coverage
    (0..1), word count, and whether the output passes the task bar
    (covers at least half the expected points within the word limit).
    The score is a coverage check, not a quality judgement."""
    text = (output_text or "").lower()
    words = text.split()
    if task.expected_points:
        covered = sum(1 for p in task.expected_points if p.lower() in text)
        coverage = covered / len(task.expected_points)
    else:
        coverage = 1.0
    within_limit = len(words) <= task.max_words
    passed = coverage >= 0.5 and within_limit and len(words) > 0
    return {"coverage": round(coverage, 4), "words": len(words),
            "within_word_limit": within_limit, "passed": passed,
            "note": "Coverage check only — it does not judge quality of writing."}


@dataclass
class Experiment:
    experiment_id: str
    model: str
    prompt_version: str
    skill_versions: dict
    configuration: dict
    results: list = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def summary(self) -> dict:
        runs = self.results
        n = len(runs)
        passed = sum(1 for r in runs if r["score"]["passed"]) if n else 0
        total_cost = round(sum(r["cost_usd"] for r in runs), 6)
        latencies = [r["latency_seconds"] for r in runs]
        mean_latency = round(sum(latencies) / len(latencies), 3) if latencies else 0.0
        kinds = sorted({r["task_kind"] for r in runs})
        return {
            "experiment_id": self.experiment_id,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "skill_versions": dict(self.skill_versions),
            "configuration": dict(self.configuration),
            "runs": n,
            "task_kinds_covered": kinds,
            "representative": len(kinds) >= 3 and n >= 3,
            "success_rate": round(passed / n, 4) if n else 0.0,
            "passed": passed,
            "total_cost_usd": total_cost,
            "mean_latency_seconds": mean_latency,
            "results": list(runs),
            # Everything needed to reproduce this comparison, on the record.
            "reproduction": {
                "model": self.model,
                "prompt_version": self.prompt_version,
                "skill_versions": dict(self.skill_versions),
                "configuration": dict(self.configuration),
                "task_ids": [r["task_id"] for r in runs],
            },
        }


def run_experiment(experiment_id: str, *, model: str, prompt_version: str,
                   skill_versions: dict | None = None,
                   configuration: dict | None = None, run_fn: object = None,
                   tasks: tuple[BenchmarkTask, ...] = BENCHMARK_TASKS) -> dict:
    """Run every task through ``run_fn`` and record cost, latency and
    success per run. ``run_fn`` must return (output_text, cost_usd,
    latency_seconds). Raises ValueError when no runner is supplied —
    a benchmark with no runner is refused, never faked."""
    if run_fn is None:
        raise ValueError(
            "No runner supplied: a benchmark needs a real model call or a "
            "test runner — it never invents results.")
    exp = Experiment(experiment_id=experiment_id, model=model,
                     prompt_version=prompt_version,
                     skill_versions=dict(skill_versions or {}),
                     configuration=dict(configuration or {}),
                     started_at=time.time())
    for task in tasks:
        output_text, cost_usd, latency_s = run_fn(task)
        exp.results.append({
            "task_id": task.task_id, "task_kind": task.kind,
            "cost_usd": round(float(cost_usd), 6),
            "latency_seconds": round(float(latency_s), 3),
            "score": score_output(task, output_text),
        })
    exp.finished_at = time.time()
    return exp.summary()


def compare_experiments(summaries: list[dict]) -> dict:
    """Compare experiment summaries on the same footing: success rate,
    cost and latency side by side. Cost and quality are always shown
    together — a cheaper experiment that fails more is flagged, not
    crowned. Experiments covering different task kinds are labelled
    as not directly comparable."""
    rows = []
    for s in summaries:
        rows.append({
            "experiment_id": s["experiment_id"], "model": s["model"],
            "prompt_version": s["prompt_version"],
            "runs": s["runs"], "task_kinds_covered": s["task_kinds_covered"],
            "success_rate": s["success_rate"],
            "total_cost_usd": s["total_cost_usd"],
            "mean_latency_seconds": s["mean_latency_seconds"],
            "representative": s["representative"],
        })
    kinds_sets = {tuple(s["task_kinds_covered"]) for s in summaries}
    comparable = len(kinds_sets) <= 1
    best = None
    if rows and comparable:
        # Highest success rate wins; cost breaks ties (cheaper wins).
        best = min(rows, key=lambda r: (-r["success_rate"], r["total_cost_usd"]))
    return {"comparable": comparable,
            "comparability_note": ("Same task kinds compared." if comparable
                                   else "Different task kinds — these results "
                                   "are not directly comparable."),
            "rows": rows,
            "best_by_success_then_cost": best["experiment_id"] if best else None}
