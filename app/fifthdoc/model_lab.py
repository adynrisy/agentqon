"""§9 Model lab — fixed test set, recorded conditions, honest comparison.

A model good on one benchmark is NEVER auto-considered best for every
task: comparison reports are per capability, and capability profiles are
per task category. Every run records model version, prompt, settings,
hardware and measurement conditions so a later run is comparable.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field

CAPABILITIES = ("structured_output", "tool_call", "coding",
                "instruction_following", "long_context_retrieval",
                "multilingual", "error_recovery")


@dataclass
class LabCase:
    case_id: str
    capability: str
    prompt: str
    expected: str
    settings: dict = field(default_factory=dict)


@dataclass
class LabRun:
    run_id: str
    model: str
    model_version: str
    case_id: str
    capability: str
    passed: bool
    latency_ms: float = 0.0
    tokens: int = 0
    memory_mb: float | None = None
    error: str = ""
    hardware: str = ""
    conditions: str = ""


class ModelLab:
    """Fixed test set: cases are set once and versioned by hash."""

    def __init__(self, cases: list[LabCase]):
        for case in cases:
            if case.capability not in CAPABILITIES:
                raise ValueError(f"Unknown capability: {case.capability}")
        self.cases = {c.case_id: c for c in cases}
        digest = hashlib.sha256(json.dumps(
            sorted((c.case_id, c.capability, c.prompt) for c in cases),
            sort_keys=True).encode()).hexdigest()[:12]
        self.test_set_version = f"lab-set-{digest}"
        self.runs: list[LabRun] = []

    def record(self, run: LabRun) -> LabRun:
        self.runs.append(run)
        return run

    def context_limit_check(self, model_max_tokens: int, prompt_tokens: int) -> dict:
        fits = prompt_tokens <= model_max_tokens
        return {"fits": fits, "prompt_tokens": prompt_tokens,
                "model_max_tokens": model_max_tokens,
                "headroom_tokens": model_max_tokens - prompt_tokens,
                "warning": ("" if fits else "Prompt is larger than the model's "
                            "context limit — it will be cut off.")}

    def benchmark(self, model: str) -> dict:
        runs = [r for r in self.runs if r.model == model]
        if not runs:
            return {"model": model, "runs": 0}
        latencies = [r.latency_ms for r in runs if r.latency_ms]
        return {"model": model, "runs": len(runs),
                "pass_rate": round(sum(r.passed for r in runs) / len(runs), 4),
                "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
                "throughput_runs_per_min": round(
                    60000 / statistics.mean(latencies), 2) if latencies else 0,
                "errors": sum(1 for r in runs if r.error),
                "error_rate": round(sum(1 for r in runs if r.error) / len(runs), 4),
                "max_memory_mb": max((r.memory_mb or 0) for r in runs)}

    def repeatability(self, model: str, case_id: str) -> dict:
        runs = [r for r in self.runs if r.model == model and r.case_id == case_id]
        if len(runs) < 2:
            return {"comparable": False, "reason": "Need at least two runs."}
        outcomes = {r.passed for r in runs}
        return {"comparable": True, "runs": len(runs),
                "consistent": len(outcomes) == 1,
                "pass_count": sum(r.passed for r in runs)}

    def capability_profile(self, model: str) -> dict[str, float]:
        profile = {}
        for capability in CAPABILITIES:
            runs = [r for r in self.runs if r.model == model
                    and r.capability == capability]
            if runs:
                profile[capability] = round(sum(r.passed for r in runs) / len(runs), 4)
        return profile

    def compare(self, models: list[str]) -> dict:
        """Per-capability comparison — never a single 'best model' verdict."""
        profiles = {m: self.capability_profile(m) for m in models}
        best_per_capability = {}
        for capability in CAPABILITIES:
            scored = [(m, p[capability]) for m, p in profiles.items()
                      if capability in p]
            if scored:
                best_per_capability[capability] = max(scored, key=lambda x: x[1])[0]
        return {"profiles": profiles, "best_per_capability": best_per_capability,
                "note": "Best is per capability only. A model good on one "
                        "benchmark is not automatically best for every task."}

    def regression(self, model: str, before_version: str, after_version: str) -> dict:
        before = [r for r in self.runs if r.model == model
                  and r.model_version == before_version]
        after = [r for r in self.runs if r.model == model
                 and r.model_version == after_version]
        if not before or not after:
            return {"comparable": False,
                    "reason": "Missing runs for one of the versions."}
        b = sum(r.passed for r in before) / len(before)
        a = sum(r.passed for r in after) / len(after)
        return {"comparable": True, "before_pass_rate": round(b, 4),
                "after_pass_rate": round(a, 4), "delta": round(a - b, 4),
                "regressed": a < b}
