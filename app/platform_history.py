"""AgentQon — model evaluation history view.

Per-model history over time — success rate, latency, cost — built
from self-test and run records. The history is only as good as the
records fed to it: ``record`` is the single entry point, used both by
self-test results and by mission/run results, so the two sources can
never drift into different shapes.

Plain-words rule: every summary carries a sentence a non-technical
reader can understand ("Model X worked 4 out of 5 times..."), and
uncertainty is stated ("only 2 runs recorded — too few to judge")
instead of hidden behind a percentage.
"""
from __future__ import annotations

import time

SOURCES = ("self-test", "run")


class ModelHistory:
    """In-memory history store. The caller owns persistence (the
    harness record); this class owns aggregation and wording, so the
    same wording appears everywhere the history is shown."""

    def __init__(self) -> None:
        self._runs: list[dict] = []

    def record(self, *, model: str, success: bool, latency_seconds: float,
               cost_usd: float, source: str, config_version: int | None = None,
               task_category: str = "") -> dict:
        if source not in SOURCES:
            raise ValueError(
                f"Unknown history source {source!r} — use 'self-test' or 'run'.")
        entry = {"model": model, "success": bool(success),
                 "latency_seconds": round(float(latency_seconds), 3),
                 "cost_usd": round(float(cost_usd), 6), "source": source,
                 "config_version": config_version,
                 "task_category": task_category, "at": time.time()}
        self._runs.append(entry)
        return dict(entry)

    def history(self, model: str) -> list[dict]:
        return [dict(r) for r in self._runs if r["model"] == model]

    def models(self) -> list[str]:
        seen: list[str] = []
        for r in self._runs:
            if r["model"] not in seen:
                seen.append(r["model"])
        return seen

    def summary(self, model: str) -> dict:
        runs = self.history(model)
        n = len(runs)
        if n == 0:
            return {"model": model, "runs": 0, "success_rate": None,
                    "mean_latency_seconds": None, "total_cost_usd": 0.0,
                    "plain_words": (f"No runs recorded for {model} yet — "
                                    "nothing to judge it by."),
                    "enough_to_judge": False}
        passed = sum(1 for r in runs if r["success"])
        mean_latency = round(sum(r["latency_seconds"] for r in runs) / n, 3)
        total_cost = round(sum(r["cost_usd"] for r in runs), 6)
        enough = n >= 5
        words = (f"{model} worked {passed} out of {n} times, "
                 f"averaging {mean_latency} seconds per run, "
                 f"costing {total_cost} dollars in total.")
        if not enough:
            words += (" Only a few runs are recorded — too few to judge "
                      "this model fairly.")
        return {"model": model, "runs": n, "passed": passed,
                "success_rate": round(passed / n, 4),
                "mean_latency_seconds": mean_latency,
                "total_cost_usd": total_cost,
                "by_source": {
                    source: sum(1 for r in runs if r["source"] == source)
                    for source in SOURCES},
                "by_category": _by_category(runs),
                "plain_words": words,
                "enough_to_judge": enough,
                "history": runs}

    def all_summaries(self) -> list[dict]:
        return [self.summary(model) for model in self.models()]


def _by_category(runs: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in runs:
        cat = r["task_category"] or "uncategorised"
        slot = out.setdefault(cat, {"runs": 0, "passed": 0})
        slot["runs"] += 1
        slot["passed"] += 1 if r["success"] else 0
    for slot in out.values():
        slot["success_rate"] = round(slot["passed"] / slot["runs"], 4)
    return out
