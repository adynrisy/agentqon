# OmniMind AI — Phase 0 Baseline Study

*The textbook's Phase 0 asks one question before any architecture is
trusted: does collaboration demonstrably beat a strong single agent,
and at what cost? This study is the platform's answer, measured with
the platform's own machinery (adapters, critic, evaluator — nothing
re-implemented for the study). First run 2026-10-10 (Phase 1 era,
mock arm); re-run in full on 2026-10-10 after Phases 2–7 landed,
including the real-LLM arm the original run could not perform.*

## Domain and users

- **Domain (one, deliberately):** evidence-backed research briefs —
  "choose/compare strategies" missions of the kind in the textbook's
  Ch.23 worked example. The second book domain (web-app builds) is a
  recorded non-goal for the MVP.
- **Named user:** the product sponsor (Zohaib Khan), who commissioned
  the platform from the textbook and directed every phase.
- **Not done:** interviews with external target users. No external
  users exist yet; willingness-to-pay is unmeasured. This study
  therefore validates *machinery value* (does the pipeline improve
  verified output per dollar), not market demand. Recorded plainly so
  nobody cites it as product-market evidence.
- **Primary metrics (defined before measurement):**
  1. *Independently verified task success* — the selected candidate's
     mean dimension score under rubric `research-v1`, where citation
     validity is checked deterministically against the source library
     (not by the producing agent).
  2. *Cost per successful task* — total recorded run cost (agent runs
     + critic + evaluation) per completed benchmark task.

## Improvement bar (set in writing, per the roadmap)

> Collaboration (parallel agents + critic + evaluation) is worth its
> complexity only if, versus the single-agent baseline, it **either**
> raises the selected candidate's verified score, **or** surfaces
> material defects the single agent ships silently — at **no more
> than 2.5×** the single-agent cost. If neither holds on the real-LLM
> arm, the affected domain defaults to single-agent (the roadmap's
> kill/simplify rule).

## Method

Three configurations, identical selection rule (highest mean dimension
score; ties → cheaper), the textbook's three benchmark objectives
(B2B invoicing GTM, consumer habit-app launch, tutoring marketplace
expansion), the frozen seed source library as the citable corpus:

| Config | Agents | Critic | Evaluation |
|---|---|---|---|
| single | top-ranked researcher only | none | deterministic only |
| parallel | both researchers | none | deterministic only |
| full | both researchers | one bounded round | deterministic + critiques |

Two arms: **mock** (deterministic adapters — measures machinery
overhead and critic behaviour; scores are expected to be flat across
objectives because mock claims are strategy-fixed) and **LLM** (real
adapters against a local qwen2.5:1.5b — measures actual model
behaviour: varied text, real token accounting, fallible citations).
Raw data: `benchmarks/results.json`, `benchmarks/results_llm.json`;
runners: `benchmarks/run_baselines.py`, `benchmarks/run_baselines_llm.py`.

## Results — mock arm (re-run 2026-10-10, current code)

| Config | Selected | Mean score | Avg cost | Avg latency | High-severity critiques surfaced |
|---|---|---|---|---|---|
| single | niche-first (3/3) | 4.750 | $0.39 | ~0.05 ms | 0 |
| parallel | niche-first (3/3) | 4.750 | $0.84 | ~0.06 ms | 0 |
| full | niche-first (3/3) | 4.750 | $1.05 | ~0.15 ms | **3 (1 per task)** |

Reading: with deterministic agents the candidates never change, so
scores cannot move — the mock arm measures *machinery*, and the
machinery's one measurable effect is the critic: only the full
configuration flags the partnership candidate's opinion-backed claim
as high severity. Cost of the full pipeline: **2.7× single** on mock
accounting (fixed per-run prices), slightly above the 2.5× bar — a
number to re-check on the LLM arm, where costs are token-real.

## Results — LLM arm (local qwen2.5:1.5b, 2026-10-10)

| Config | Selected (of 3 tasks) | Avg selected score | Avg cost | Avg latency | High-severity critiques surfaced |
|---|---|---|---|---|---|
| single | niche-first ×3 | 4.125 | $0.07 | 48.9 s | 0 |
| parallel | niche-first ×3 | 4.125 | $0.14 | 62.8 s | 0 |
| full | niche-first ×2, **partnership-led ×1** | 3.917 | $0.14 | 87.1 s | **3 (all on one task)** |

The decisive observation is the tutoring-marketplace task. In single
mode the niche-first candidate scored **4.125 (evidence 5.0,
confidence medium)** and would have shipped as-is. In full mode the
critic's three high-severity critiques hit that same strategy's
candidate: its evidence dimension fell to **2.75**, robustness to 2.5,
confidence to **low** — and selection flipped to the partnership-led
candidate (evidence 4.0). The full pipeline's *average selected score
is lower* (3.917) precisely because it refused an inflated score. Any
reading of this study that treats "higher selected score" as
automatically better would punish the critic for working; verified
success means the score of what survives scrutiny.

On the other two tasks the model produced well-cited candidates in
every configuration and the critic found nothing high-severity — with
a model this small, candidates are structurally similar across
objectives (identical 4.125s), which caps how much any configuration
can differentiate. That is a model limitation, not a pipeline result.

Costs invert the mock arm's lesson: with token-real accounting the
critic is nearly free next to a second researcher — parallel and full
cost the same $0.14 (2.0× single, inside the 2.5× bar), while mock
fixed pricing had put full at 2.7×. Latency is model-bound (a 1.5B
local model taking ~50–90 s per task), not architecture-bound.

## Analysis

1. **The critic is the value, not the crowd.** On this evidence the
   second researcher added cost without changing any outcome the
   single agent reached; the critic changed an outcome (selection +
   confidence) on 1 of 3 tasks by catching evidence inflation the
   deterministic checks alone had scored 5.0.
2. **Parallel-without-critic is dominated.** It costs what full costs
   (LLM arm) and provides none of the defect-surfacing. The platform's
   honest configuration choice is *single* (cheap, unscrutinised) vs
   *full* (scrutinised). The orchestrator keeps both agents because
   strategy diversity is the input the critic selects over — but a
   deployment that disables critique should also disable the second
   researcher and run single-agent.
3. **Deterministic checks are necessary and insufficient.** Citation
   existence/coverage passed everywhere (Phase 6: coverage 1.00 across
   25 golden missions); the defect the critic caught was *quality of
   support*, which only the critique round scored down.

## Limitations (read before quoting any number above)

1. **Three objectives, one small local model.** The LLM arm's model
   (qwen2.5:1.5b) is far weaker than production cloud models; absolute
   scores say little about a GPT-class deployment. The *comparison
   between configurations* is the study's content, not the scores.
2. **Mock determinism.** Mock-arm flatness is a property of the mocks,
   documented in the runner header since Phase 1 — it is not evidence
   about real agents.
3. **Rubric-scored success is a proxy.** Dimension scores are the
   platform's own rubric (partially deterministic checks + structured
   judgement), not blind human grading. Phase 6's golden set (25
   missions, 25/25, mean dimension 4.25, citation coverage 1.00) is
   the broader platform-level corroboration.
4. **No external users interviewed** (see Domain and users).

## Verdict (against the bar set above)

- **Score-raising clause: NOT met.** Configurations tie on verified
  score wherever the critic finds nothing (2 of 3 LLM tasks; all mock
  tasks).
- **Defect-surfacing clause: MET.** On the LLM arm the full pipeline
  surfaced 3 high-severity defects and changed the shipped decision
  on 1 of 3 tasks, at **2.0×** single-agent cost — inside the 2.5×
  ceiling. The mock arm corroborates the mechanism (the critic flags
  the opinion-backed claim in every full run; single/parallel ship it
  silently).
- **Decision:** KEEP the full pipeline as the default for the
  research-brief domain. Record parallel-without-critic as a dominated
  configuration (simplify guidance documented in Analysis §2). Re-run
  this study unchanged when a materially stronger model is connected
  — the harness is one command — because the score-raising clause may
  resolve differently at higher model capability.
- **Phase 0 exit gate:** domains chosen (1), metrics defined,
  benchmark set + rubric versioned in-repo, single AND parallel
  baselines measured and reported (both arms). Outstanding and
  recorded, not hidden: external user interviews and
  willingness-to-pay remain unmeasured — this study validates
  machinery value only.
