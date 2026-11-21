"""OmniMind AI — Phase 0 baseline harness (Roadmap tasks 6, 7, 30).

HISTORY: this script originally compared three configurations
(single / parallel / full) using the Phase 1 deterministic adapters.
Its own scope note always said the study measured machinery overhead
only, and that the quality verdict would come from re-running it once
real provider adapters landed.

v20: the deterministic adapters were removed from the product — there
is no canned generation left to benchmark. The baseline study now runs
exclusively through the real model adapters; see
benchmarks/run_baselines_llm.py (same objectives, same selection rule,
a connected model required). This module remains as the shared home of
the study's objectives/deliverables and as the pointer to that runner.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BENCHMARK_OBJECTIVES = [
    "Choose a go-to-market strategy for a B2B invoicing tool for small retailers",
    "Choose a launch strategy for a consumer habit-tracking app",
    "Choose an expansion strategy for an online tutoring marketplace",
]
DELIVERABLES = ["strategy recommendation", "evidence-backed claims", "risks"]


def main() -> None:
    raise SystemExit(
        "The deterministic baseline arm was removed in v20 (the product "
        "no longer ships canned adapters). Run the study through the real "
        "model adapters instead:\n"
        "    .venv/bin/python benchmarks/run_baselines_llm.py\n"
        "(a connected model is required — see README 'Connect a model').")


if __name__ == "__main__":
    main()
