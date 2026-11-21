# Evaluation Plan

## What is evaluated today (running code)
- **Deterministic tests** per candidate: citation resolution, citation
  coverage, claim-evidence entailment checks, schema/budget validity —
  these gate candidate quality before any model judgement.
- **Dimension rubric `research-v1`:** five dimensions scored independently
  by the critic agent; totals are never collapsed into one opaque number
  in the UI.
- **Regression suite:** 69 tests (both database backends) covering
  contracts, tenant isolation, queue/DLQ, vault, router, reservations,
  strict BYOK, harness controls, admin gating.

## Golden-set plan (next)
- A frozen set of 25 research missions with human-scored reference
  briefs; candidates scored blind by rubric + pairwise preference.
- Router quality: for each routing mode, measure cost vs rubric outcome
  on the golden set; publish the matrix in PROJECT_STATUS.
- Calibration: critic severity vs human severity agreement (target
  Cohen's κ ≥ 0.6 before any auto-selection is trusted).
- Safety evals: prompt-injection strings inside source excerpts must not
  alter tool policy or leak across tenants (red-team set).

## Release gate
No phase ships with a failing suite on either backend, an unrecorded
fallback, or a price/cost figure displayed without provenance.
