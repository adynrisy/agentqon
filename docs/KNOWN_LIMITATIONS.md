# Known Limitations

Honest edges of the current build. Nothing here is hidden from users in
the product: where a limitation affects a screen or a number, the UI/API
labels it.

1. **Gateway inference cost is often "unknown".** When a gateway publishes
   no price, AgentQon records tokens exactly and inference cost as
   unknown, reserves budget conservatively ($0.50 default per call), and
   never displays unknown as $0. Tool/evaluation costs are exact.
2. **OmniRoute does not list local chat models** (v3.8.51, verified).
   Allowlisted models are catalogued as *user-declared* until a run
   exercises them; capability flags stay `unknown`.
3. **SSRF checks run at validation time AND at request time** (Phase 4:
   the gateway client re-resolves and refuses blocked/private answers
   for public endpoints). A narrow TOCTOU window remains between the
   check and the connect (true IP pinning is future work), and when a
   proxy performs resolution the check defers to validation-time
   policy. Metadata/link-local targets are always blocked.
4. **Auth modes.** `OMNIMIND_AUTH_MODE=accounts` (Phase 4) provides real
   users, sessions, OIDC bearer verification and role enforcement.
   `dev` header mode remains for local development only and must never
   be exposed publicly. The OIDC authorization-code login flow depends
   on the deployment's IdP; the API verifies IdP-issued tokens.
5. **Single-node queue.** The durable queue is database-backed and safe
   for multiple workers on one database; cross-region operation is out
   of scope.
6. **Small local models are weak at strict JSON.** The 1.5B dev model
   occasionally needs the adapter's lenient parsing; production models
   behave better. Failures on tenant connections fail the mission loudly
   (by design) instead of silently substituting anything else.
7. **Catalog freshness is pull-based** (sync on validate / explicit
   refresh), TTL 15 minutes for freshness labelling; no push webhooks.
8. **Seed corpus remains the fallback.** Live web research (Phase 5) is
   the default for public/internal missions; the seed library is used
   for confidential/restricted missions and whenever live retrieval
   fails (always with a visible event). Search is provided by
   DuckDuckGo's keyless HTML endpoint — a replaceable provider seam,
   not a contracted SLA. Grounded claim-writing is the connected
   model's work through the LLM adapters; v20 removed the canned
   template agents entirely.

9. **Imported persona agents are prompt-level specialists.** Their
   registry quality/reliability are declared priors (3.8/0.90), not
   measurements; their briefs shape the connected model's behaviour
   when they run. Benchmarking personas on the golden set before
   trusting their priors is the operator's onboarding step (Ch.6
   pipeline).

## Grounding depth (v11)
- Simulation grounding mines the **titles and excerpts** of the sources a
  mission's candidates cited. Figures that appear only deep inside a
  fetched page (beyond the stored excerpt) are not found; the parameter
  then falls back to the platform default and is labelled
  `origin: "assumption"` in the world provenance and in the chat.
- Market size is capped by the engine bound (1,000,000) even when the
  research reports a larger market; the grounding record shows the
  capped value the simulation actually used.

## General-task quality (v12)
- Non-strategy deliverables are written by the connected model under
  the evidence rules — the quality ceiling is the model's, and there is
  no template fallback (v20): without a model, nothing is generated at
  all rather than something canned.
- The task classifier is heuristic. A misread objective can be corrected
  by setting the task type explicitly (contract field / Advanced picker).
