
## V3 Master Build — 2026-10-10
- BYOK connections + envelope-encrypted vault (Fernet, versioned keys, crypto-shred), SSRF URL policy, cross-tenant 404s.
- Dynamic catalog synced from connections (OmniRoute live sync: 475 models) with price provenance + freshness; user-declared entries for allowlisted-but-unlisted models (OmniRoute v3.8.51 does not list ollama-local chat models — verified).
- Deterministic router, 6 modes, hard filters first, NO_ELIGIBLE_MODEL; decisions persisted + visible in UI.
- Race-safe budget reservations (reserve→reconcile/release) around every routed model call.
- Harness: pause/resume, bounded revision (new candidate versions), Markdown export, projects, admin overview + kill switch.
- OmniRoute v3.8.51 (MIT, tag c1e30b76) run as isolated sidecar; FULL mission verified OmniMind → OmniRoute → Ollama qwen2.5:1.5b (2 candidates, 7 critiques, all runs llm-adapter-1.0, inference cost honestly "unknown").
- Two dialect bugs found only by live PG runs, fixed + regression-tested: scalar MAX vs GREATEST in settle_reservation; concurrent first-boot CREATE TABLE race (pg_type unique violation) now tolerated; plus a non-reentrant-lock deadlock in PG column migrations.
- Tests: 66 passed + 3 skipped (SQLite), 69 passed (PostgreSQL). v3 zip fresh-unzip verified (66 passed in clean venv).
- Strict BYOK proven: revoked/pinned-broken connection fails the mission loudly with zero candidates — no platform-key substitution, ever.

## Phase 4 — Production Hardening (2026-10-10)
- Accounts: PBKDF2 password hashing, hashed+revocable session tokens, register/login/logout/me; tenant memberships with owner/admin/member/viewer roles enforced per action (viewers read-only, connections admin+, owner-grants owner-only, last-owner protection).
- OIDC: RS256 bearer verification against issuer JWKS (iss/aud/exp), JIT user provisioning; forged/expired/wrong-aud tokens rejected (locally-signed test keys).
- Rate limits: DB-backed fixed-window counters (race-safe conditional increment) on login/register (per email), mission runs+revisions and connection validations (per tenant); 429 + Retry-After.
- Vault: key providers (env | key file — the KMS mount seam), POST /v1/admin/vault/rewrap re-encrypts all secrets onto the active key version; shredded rows never resurrected.
- SSRF: request-time re-validation in GatewayClient (metadata/link-local always blocked; private/loopback resolutions refused for public endpoints; explicit private nets so benchmark egress answers pass).
- UI: account card (sign in/register/sign out, workspace switcher, members panel) shown in accounts mode.
- Tests: 77 passed + 3 skipped (SQLite). Live accounts-mode run verified end-to-end.
- ENVIRONMENT NOTE: the VM was replaced mid-session (~19:41 PKT); system-installed PostgreSQL was lost and the apt mirror is timing out, so Phase 4's PG suite re-run is pending reinstall (V3 PG suite passed 69 earlier today; Phase 4 PG delta is one standard ON CONFLICT upsert).

## Phase 5 — Live Web Research (2026-10-10)
- app/research.py: LiveWebResearcher (DuckDuckGo HTML search, ad filtering, bounded fetches, HTML text extraction, published-date discovery, provenance: url/publisher/content_hash/retrieval_method). SSRF: request-time policy on every fetch + blocked/private literal hosts never catalogued.
- Gateway executes web.read as the mission's research stage (one authorized call), classification-gated (confidential/restricted → seed unless tools.live_web=true), per-mission cache, ResearchCompleted/ResearchSkipped/ResearchFallback events.
- Corpus flows via AgentRequest.sources to mock agents (deterministic citation rotation), LLM agents (library = live corpus; invented source ids stripped with warnings), critic (seed+live source map), evaluator (unchanged, store-backed).
- SSE: GET /v1/missions/{id}/events/stream + UI EventSource live activity (dev mode; accounts mode falls back to refresh — EventSource cannot send Authorization headers).
- Egress fix with platform-wide effect: explicit proxy from env + SSL_CERT_FILE/system CA bundle (netpolicy.egress_proxy/tls_verify) — repairs the bracketed-no_proxy httpx crash and MITM-CA verification for ALL public gateway/research traffic; local endpoints stay proxy-free.
- Live proof: mission completed with ResearchCompleted (14 sources, 2 queries); candidates cited 4 real sites; tool spend $0.02 research + standard ledger.
- Tests: 88 passed + 3 skipped (SQLite). PG re-verification still pending VM PostgreSQL reinstall (Phase 4 note).

## Phase 6 — Golden-Set Evaluation Harness (2026-10-10)
- benchmarks/golden_set.json: 25 frozen missions across domains/classifications with authored expectations (research mode, citation coverage, dimension floor, budget).
- benchmarks/harness.py + run_golden.py: full-stack runner (real HTTP app in-process), per-case metrics + release gates with exit codes; router matrix (6 modes x 3 scenarios against an adversarial fixture catalog) with hard invariants; critic calibration with Cohen's kappa.
- Results: seed suite 25/25 (mean dimension 4.25, coverage 1.00, $1.07/case); live subset 6/6 (real web research + verified classification skips); router invariants all hold; critic kappa 1.0 after a calibration-driven fix to the mock critic's severity rules (pre-fix ~0.47 — corroboration now counts).
- benchmarks/golden_results.json + golden_results_live.json are the recorded runs.
- Tests: 93 passed + 3 skipped (SQLite).

## Phase 7 — Simulation Worlds (2026-10-10)
- app/simulation.py: deterministic seeded market engine (3 segments, competitor response, partner ramp, cash-floored budgets), frozen baselines with provenance (mission objective, source snapshot hash, candidate rubric scores), immutable branches, sensitivity analysis (+/-20% price & competitor), outcome hashes, SIMULATED ESTIMATE labelling + non-determinism disclosure. Bounds: 6 branches, 52 ticks, 1M market cap, zero external calls.
- API: worlds CRUD, branches, run, compare, promote (the only exit: explicit, logged, creates a DRAFT mission). Tenant isolation + role guards match missions.
- UI: worlds card with create (standalone or mission-seeded), branch/run controls, adopters sparklines, sensitivity + exhaustion flags, promote buttons.
- Live exit-gate run: Ch.23's three launch strategies compared under one budget (7,459 / 1,901 / 392 adopters); replica world reproduced identical outcome hashes; promote created a draft mission.
- Tests: 104 passed + 3 skipped (SQLite). Human-factors labelling test (Ch.21) not run — no real users available; recorded honestly in PROJECT_STATUS.

## Agency roster (2026-10-10)
- Bundled msitarzewski/agency-agents (MIT, commit 479193d) under app/data/agency-agents/ (296 files; 280 parse as agents — strategy playbooks without frontmatter are skipped, not agents).
- app/agency.py importer + POST /v1/agents/import-agency (admin): manifests in lifecycle testing, personas in a global store, capability curation documented in-module, re-import preserves operator lifecycle decisions.
- Persona execution: orchestrator enriches agency manifests with their brief; LLMResearchAdapter leads the system prompt with it under platform evidence rules. Live proof: mission completed with both candidates authored by agency.marketing personas (core researchers quarantined), real Ollama runs.
- Tests: 109 passed + 3 skipped (SQLite). Personas require LLM mode; in mock mode an activated persona behaves as a generic researcher (documented in UI + KNOWN_LIMITATIONS).
