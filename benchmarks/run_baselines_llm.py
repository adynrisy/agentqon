"""OmniMind AI — Phase 0 baseline study, LLM arm.

The same three configurations as run_baselines.py (single / parallel /
full) but with the REAL LLM adapters against the configured endpoint
(local Ollama by default), so the study measures actual model behaviour
— varied text, real token costs, fallible citations — instead of the
the removed deterministic arm. Selection rule identical across arms.

Usage:
    OMNIMIND_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
    OMNIMIND_LLM_MODEL=qwen2.5:1.5b OMNIMIND_LLM_API_KEY=ollama \
    .venv/bin/python benchmarks/run_baselines_llm.py
Writes benchmarks/results_llm.json.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters import AgentRequest  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.domain import Candidate  # noqa: E402
from app.evaluator import evaluate_candidate  # noqa: E402
from app.llm_adapter import LLMCriticAdapter, LLMResearchAdapter  # noqa: E402
from app.registry import SEED_AGENTS, rank_agents  # noqa: E402
from app.sources import SEED_SOURCES  # noqa: E402

from benchmarks.run_baselines import BENCHMARK_OBJECTIVES, DELIVERABLES  # noqa: E402

SOURCES = {s.source_id: s for s in SEED_SOURCES}
RESEARCH_MANIFESTS = [a.model_dump() for a in SEED_AGENTS
                      if "research.web" in a.capabilities]
CRITIC_MANIFEST = next(a.model_dump() for a in SEED_AGENTS
                       if "critique.review" in a.capabilities)


async def _run_agent(adapter, manifest, objective):
    req = AgentRequest(mission_id="bench", task_id="bench-research",
                       objective=objective, deliverables=DELIVERABLES,
                       output_schema={"version": "research-v1"})
    t0 = time.perf_counter()
    resp = await adapter.invoke(req, manifest)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    out = resp.output
    cand = Candidate(mission_id="bench", title=out["title"],
                     strategy=out["strategy"], summary=out["summary"],
                     agent_version_id=out["agent_version_id"],
                     claims=out["claims"], assumptions=out["assumptions"],
                     risks=out["risks"],
                     estimated_cost_usd=out["estimated_cost_usd"],
                     time_to_learn=out["time_to_learn"])
    return cand, resp.usage["cost_usd"], elapsed_ms, resp.warnings


async def run_mode(settings, mode: str, objective: str) -> dict:
    ranked = rank_agents([dict(m, lifecycle="active") for m in RESEARCH_MANIFESTS],
                         ["research.web"])
    chosen = ranked[:1] if mode == "single" else ranked[:2]
    adapter = LLMResearchAdapter(settings)
    candidates, run_cost, elapsed, warnings = [], 0.0, 0.0, []
    errors = []
    for manifest in chosen:
        try:
            cand, cost, ms, warns = await _run_agent(adapter, manifest, objective)
            candidates.append(cand)
            run_cost += cost
            elapsed += ms
            warnings.extend(warns)
        except Exception as exc:  # a failed generation is a data point
            errors.append(f"{manifest['agent_id']}: {type(exc).__name__}")
    critiques: list[dict] = []
    if mode == "full" and candidates:
        critic = LLMCriticAdapter(settings)
        req = AgentRequest(mission_id="bench", task_id="bench-critique",
                           objective=objective, deliverables=DELIVERABLES,
                           output_schema={"candidates":
                                          [c.model_dump() for c in candidates]})
        t0 = time.perf_counter()
        try:
            resp = await critic.invoke(req, CRITIC_MANIFEST)
            elapsed += (time.perf_counter() - t0) * 1000
            run_cost += resp.usage["cost_usd"]
            critiques = resp.output["critiques"]
        except Exception as exc:
            errors.append(f"critic: {type(exc).__name__}")
    scored = []
    for cand in candidates:
        rec = evaluate_candidate(cand, critiques, SOURCES)
        run_cost += rec.cost_usd
        scored.append({"candidate_id": cand.candidate_id,
                       "strategy": cand.strategy, "agent": cand.agent_version_id,
                       "mean_score": round(statistics.mean(
                           d.score for d in rec.dimensions.values()), 3),
                       "confidence": rec.confidence, "dims": {
                           k: v.score for k, v in rec.dimensions.items()}})
    selected = max(scored, key=lambda s: s["mean_score"]) if scored else None
    return {"mode": mode, "objective": objective, "candidates": scored,
            "selected": selected, "cost_usd": round(run_cost, 4),
            "elapsed_ms": round(elapsed, 1),
            "high_critiques": sum(1 for c in critiques if c["severity"] == "high"),
            "warnings": warnings, "errors": errors}


async def main() -> None:
    settings = load_settings()
    results = []
    for objective in BENCHMARK_OBJECTIVES:
        for mode in ("single", "parallel", "full"):
            row = await run_mode(settings, mode, objective)
            results.append(row)
            sel = row["selected"]["strategy"] if row["selected"] else "FAILED"
            score = row["selected"]["mean_score"] if row["selected"] else 0
            print(f"{mode:<9} sel={sel:<16} score={score} cost=${row['cost_usd']} "
                  f"ms={row['elapsed_ms']} high_crit={row['high_critiques']} "
                  f"errors={row['errors']}", flush=True)
    out = Path(__file__).parent / "results_llm.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"Wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
