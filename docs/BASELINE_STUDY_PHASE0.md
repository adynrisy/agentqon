# OmniMind AI — Phase 0 Baseline Study (mock-scoped)

**Date:** 2026-10-10 · **Harness:** `benchmarks/run_baselines.py` · **Raw data:** `benchmarks/results.json`
**Roadmap items covered:** Phase 0 baseline checklists; First-30 tasks #6 (single-agent baseline), #7 (simple parallel baseline), #30 (Phase 1 benchmark re-run + comparison vs both baselines).

## Method

Three configurations, same benchmark objectives, same selection rule, scored only by the product's own evaluator (`app/evaluator.py`, rubric `research-v1`) — no re-implemented scoring:

- **single** — top-ranked research agent only (niche-first), evaluated with no critiques.
- **parallel** — both research agents, evaluated with no critiques. This is the textbook's "simple parallel baseline": no debate/evaluation machinery beyond deterministic schema + citation checks.
- **full** — both agents + one critic round, evaluated with critiques (the Phase 1 slice's logic, minus planner/gateway/store overhead).

Selection rule (fixed up front): highest mean of the four visible dimension scores.
Benchmark objectives (3): B2B invoicing tool GTM; consumer habit-app launch; online tutoring marketplace expansion. Each mode ran all 3.

## Results (verified by running the harness, 2026-10-10)

| Mode | Selected | Winner mean score | Avg run cost | Avg time | High-severity critiques |
|---|---|---|---|---|---|
| single | niche-first | 4.750 | $0.39 | 0.06 ms | 0 |
| parallel | niche-first | 4.750 | $0.84 | 0.32 ms | 0 |
| full | niche-first | 4.750 | $1.05 | 0.51 ms | 3 (1 per run) |

Candidate-level detail (identical across all 3 objectives):

- Niche-first candidate: 4.750 in every mode (evidence 5.0, completeness 5.0, robustness 4.0, cost-efficiency 5.0). The critic never changes it.
- Partnership-led candidate: **4.375 in parallel mode → 3.750 in full mode** (evidence 5.0 → 4.0, robustness 4.0 → 2.5), because the critic flags its load-bearing claim — "partnerships alone can replace a sales team entirely" — as resting on a single opinion source, and the evaluator prices that in.

## Honest interpretation

1. **No quality win on the winner — yet.** All three modes select the same candidate with the same score. On this data, the full machinery does not beat the single-agent baseline at picking the best answer, which is exactly the outcome the textbook's kill/simplify gate warns about. Nobody should claim otherwise from these numbers.
2. **What the machinery measurably buys is downside protection.** In parallel mode the weak candidate looks deceptively close (4.375 vs 4.750 — a 0.375 gap). In full mode the gap widens to a full point and the disagreement is written down in plain language. The critic + evaluator earn their cost by making a bad candidate visibly bad, not by making the good one better.
3. **The overhead is real and now quantified:** full costs $1.05/run vs $0.39 single (2.7×) and $0.84 parallel (+25%). Latency differences are meaningless at mock scale (sub-millisecond).
4. **The decisive limitation:** the Phase 1 mock agents' claims are strategy-fixed and do not vary with the objective text (`app/adapters.py`), so scores are identical across all 3 objectives. This study proves the comparison machinery and prices its overhead — it **cannot** settle the textbook's real question (does collaboration beat a single strong agent on real work?). That verdict requires real provider adapters (Phase 2/3), re-running this same harness unchanged.

## Recommendation carried into Phase 2

- Keep the critic/evaluator (its risk-detection demonstrably works), but do **not** add more agents or collaboration rounds until the harness, re-run against a real provider, shows the full mode beating single-agent on winner quality — per the book's own gate.
- First Phase 2 benchmark task: wire one real provider behind the existing adapter template, re-run `benchmarks/run_baselines.py`, and publish the delta against this report before any Phase 3 collaboration work.

## Verification

- Harness run 2026-10-10: table above is its printed output; per-candidate dimensions are in `benchmarks/results.json`.
- Existing test suite still green after adding the harness: **24 passed** (`pytest tests/ -q`).
