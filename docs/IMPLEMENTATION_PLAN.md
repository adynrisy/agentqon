# Implementation Plan (living)

- Phase 0 — Baseline study of existing agent frameworks. *Open.*
- Phase 1 — Vertical slice. ✅ Done 2026-10-10.
- Phase 2 — PostgreSQL, durable queue, real LLM adapters. ✅ Done 2026-10-10.
- Phase 3 (V3) — OmniRoute sidecar, BYOK vault, dynamic catalog,
  cost-aware router, budget reservations, harness completion
  (pause/resume/revise/export/projects), admin + kill switch. ✅ Done
  2026-10-10. See PROJECT_STATUS.md for per-area evidence.
- Phase 4 — Production identity (accounts + OIDC bearer verification),
  per-user roles, vault key providers + rotation re-wrap, rate limits,
  request-time SSRF re-validation. ✅ Done 2026-10-10 (PG re-verification
  pending VM PostgreSQL reinstall — see PROJECT_STATUS).
- Phase 5 — Live web research through the Tool Gateway (real sources,
  retrieval provenance), streaming progress UI. ✅ Done 2026-10-10.
- Phase 6 — Golden-set evaluation harness (see EVALUATION_PLAN.md),
  router quality matrix, critic calibration.
- Phase 7 — Public deployment: hosted OmniRoute sidecar option, billing
  reconciliation, status page, backups/DR drill.

Sequencing rule: a phase starts only when the previous phase's exit gate
(green suite on both backends + live-run evidence) is recorded in
PROJECT_STATUS.md.
