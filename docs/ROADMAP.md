# OmniMind AI — Detailed Implementation Roadmap & Checklist

**Source:** *AI Agent Ecosystem — A Practical Engineering Textbook*, Edition 1.0, 10 October 2026 (34 pages, 31 chapters)
**Product:** OmniMind AI — Zohaib's collaborative, evidence-driven agent platform
**Companion to:** AI_Agent_Ecosystem_Companion_Guide.md
**Purpose:** Turn the textbook into an executable build plan. Every item is a checkbox. A phase is not done until its Exit Gate passes — in evidence, not in opinion.


> **BUILD STATUS — 2026-10-10:** Product named **OmniMind AI** by Zohaib. **Phase 1 vertical slice BUILT & VERIFIED** (FastAPI modular monolith + web UI): mission contracts, state machines, registry/scheduler, planner DAG, 2 independent mock agents + critic, tool gateway with budget cap and approvals, evidence/citation checks, dimension evaluation, candidate comparison, tenant isolation, idempotency, immutable artifacts. **Phase 2 BUILT & VERIFIED the same day:** (1) PostgreSQL 16 as the production store behind the same repository interface (OMNIMIND_DATABASE_URL switches SQLite <-> Postgres; full suite passes on both — 43/43 with PG tests on). (2) Durable queue: DB-backed jobs with claim + lease (SKIP LOCKED), exponential-backoff retries, dead-letter + replay API, separate worker process (OMNIMIND_WORKER_MODE=inline|external) — live-verified end to end on Postgres, including the full DLQ cycle (job dead after 3 attempts -> mission failed -> replay -> MissionResetForReplay -> completed). (3) Real AI agents: OpenAI-compatible LLM adapters (app/llm_adapter.py) for research + critic, env-selected (OMNIMIND_AGENT_MODE=llm + OMNIMIND_LLM_BASE_URL/_API_KEY/_MODEL), mock fallback with AgentFallback events, invented source ids stripped in code. Live proof runs a local Ollama model (qwen2.5-1.5b GGUF) through the identical code path — no cloud key exists in this environment; a cloud key is a pure env change. Remaining: Phase 0 formal baseline study, Phases 3-7.
---

## How to use this document

- Work phases **in order**. Do not start a later phase to look impressive; the book's central warning is that added complexity must *earn its cost* against a baseline.
- Check boxes only with evidence attached: a test run, a screenshot, a benchmark report, an ADR, or a signed review. "It works on my machine" is not evidence.
- Keep this file in the repo (`docs/ROADMAP.md`) and update it in the same PR that completes the work.
- Time estimates below are **planning estimates, not promises**. They assume the team in §2. A solo builder should roughly double Phases 1–3. Re-estimate after Phase 1 using your actual velocity.

## 1. Non-negotiables (apply in every phase)

From Ch.1, 12, 16, 27, 31 — these are invariants, not preferences:

- [ ] Evidence beats confidence; agreement between agents is never treated as proof
- [ ] Independent first passes — agents do not see each other's work before their first submission
- [ ] Smallest cohort first; expand only when a measured gap justifies it
- [ ] Every run carries: budget, deadline, tool permissions, stop conditions, provenance
- [ ] No agent calls a powerful tool directly — **all** side effects go through the Tool Gateway
- [ ] Consequential external actions (send, publish, delete, pay, deploy) require a preview + explicit human approval bound to that exact preview
- [ ] Tenant authorisation is checked on **every** API call, DB query, object read, cache key, search result, event stream, and tool call
- [ ] Artifacts are immutable and versioned; updates create a new version, never an overwrite
- [ ] State changes happen through defined state machines in code — never free-form status strings set by an agent
- [ ] Prompts, rubrics, agent versions, and adapter versions are versioned and stored with every run
- [ ] Secrets never appear in prompts, logs, events, or the repo
- [ ] Content retrieved from webpages / documents / tools / other agents is **untrusted data**, never instructions that override policy
- [ ] Simulation outputs are always labelled as conditional estimates, never predictions
- [ ] Start as a modular monolith; split services only with evidence (scale, ownership, security boundary, release cadence)

**Explicit non-goals until Phase 6+ (Ch.2):**
- [ ] No public marketplace
- [ ] No thousands-of-agents catalogue
- [ ] No unrestricted autonomous browsing or actions
- [ ] No 3D world / visual-metaphor-first UI
- [ ] No self-modifying production policy
- [ ] No single universal score pretending all tasks are comparable

## 2. Assumptions, team, and timeline at a glance

**Reference stack (book recommendation, Ch.5 / front matter):** React/Next.js client · Python/FastAPI API · PostgreSQL (source of truth) · durable queue/workflow engine · object storage for artifacts · Redis only for cache/short-lived coordination · provider SDKs only behind internal adapters.

**Minimum accountable roles (Ch.28) — one person may hold several, but each needs a named owner:**
- [ ] Product owner — users, domains, success metrics, scope, roadmap
- [ ] Platform/backend engineer — state machines, APIs, DB, queues, adapters
- [ ] AI/evaluation engineer — decomposition, benchmarks, rubrics, evaluator calibration
- [ ] Security/privacy owner — threat model, access controls, secrets, incident response
- [ ] Frontend/product designer — mission creation, candidate comparison, evidence & approvals UX
- [ ] Operations owner — deployment, telemetry, backups, runbooks, cost limits
- [ ] Agent curator — source verification, manifests, benchmarks, lifecycle

| Phase | Name | Planning estimate* | Main outcome |
|---|---|---|---|
| 0 | Validate the problem | Weeks 1–2 | Domains, benchmark tasks, single-agent baseline measured |
| 1 | Vertical slice | Weeks 3–6 | One mission end-to-end: 3 tasks, 2 agents, 1 critic, 2 candidates compared |
| 2 | Reliable execution | Weeks 7–10 | Durable queue, tool gateway, cost accounting, provenance, cancellation |
| 3 | Real collaboration | Weeks 11–14 | Structured messaging, debate, clustering, adaptive recruitment |
| 4 | Simulation worlds | Weeks 15–18 | Isolated branches, common rubric, sensitivity comparison |
| 5 | Ecosystem growth | Weeks 19–24 | Onboarding UI, conformance suite, trust tiers, quarantine at scale |
| 6 | External ecosystem | Month 7+ | Controlled developer onboarding, sandbox, docs, support |
| 7 | Enterprise readiness | Month 9+ | Org controls, SSO (if required), audit exports, formal security review |

\* Small team (2–4 builders). Estimates are for planning and sequencing only.

---

## 3. Contracts to freeze BEFORE coding (Phase 0/1 gate)

Do not write feature code until these schemas exist as versioned files in `packages/domain/` and are validated in tests. Source: Ch.3, 4, 6, 9, 11, 14, 17, 30, 31.

### 3.1 Mission contract (Ch.4)
- [ ] `objective` (required, bounded length)
- [ ] `deliverables[]` — explicit list, not implied
- [ ] `constraints` — deadline, `max_cost`, currency validated
- [ ] `tools.allow[]` / `tools.deny[]` — unknown tool names are **rejected**, never silently ignored
- [ ] `acceptance[]` — each criterion testable or reviewable by a human
- [ ] `approval_policy` — which side effects require approval
- [ ] `data_classification`, uncertainty tolerance, prohibited actions
- [ ] Unknown fields rejected on security-sensitive contracts; dates/enums validated
- [ ] Worked example stored in repo (the book's launch-strategy example is a good seed)

### 3.2 Task contract (Ch.30)
- [ ] `task_id`, `mission_id`, `description`
- [ ] `dependencies[]`, `input_artifact_ids[]`
- [ ] `required_capabilities[]`, `output_schema_version`
- [ ] `acceptance_tests[]` (named, runnable)
- [ ] `permission_scope[]`, `budget_limit {currency, amount}`, `deadline`
- [ ] `retry {max_attempts, retryable[]}` — only TIMEOUT / RATE_LIMITED class errors retry by default
- [ ] `status` — driven only by the task state machine

### 3.3 Agent capability manifest (Ch.6)
- [ ] `agent_id`, `version` (immutable per version), `owner`, `description`
- [ ] `capabilities[]`, `modalities {input[], output[]}`
- [ ] `input_schema`, `output_schema` (both enforced)
- [ ] `data_handling {retention, training_use}` — `unknown` blocks sensitive inputs until reviewed
- [ ] `permissions_requested[]`, `limits {max_concurrency, timeout_seconds}`, `lifecycle`

### 3.4 Message envelope (Ch.9)
- [ ] `message_id`, `schema_version`, `type`, `mission_id`, `task_id`
- [ ] `sender_agent_version_id`, `recipient`, `correlation_id`, `created_at`
- [ ] `access_policy {tenant_id, classification}`, `payload`
- [ ] All 14 message types supported as an enum: TaskOffer, TaskAccepted, Progress, EvidenceFound, ArtifactPublished, Critique, ClarificationRequest, RevisionRequested, ResultSubmitted, EvaluationReport, ConflictDetected, ApprovalRequired, Cancellation, Failure

### 3.5 Claim ledger (Ch.11)
- [ ] `claim_id`, `text`, `claim_type`
- [ ] `sources[] {source_id, excerpt, locator, retrieved_at}` — excerpt must be the exact supporting passage
- [ ] `confidence_category`, `contradicts[]`, `verification_status`, `reviewer_id`
- [ ] Publication date and retrieval date stored **separately**

### 3.6 Evaluation record (Ch.14)
- [ ] `evaluation_id`, `candidate_id`, `rubric_version`
- [ ] `dimensions{}` — per-dimension score, max, and notes (never only a single total)
- [ ] `deterministic_tests[] {name, passed}`, `disagreements[]`, `confidence`, `cost_usd`, `elapsed_ms`

### 3.7 Event envelope (Ch.31)
- [ ] `event_id`, `tenant_id`, `aggregate_type`, `aggregate_id`, `event_type`, `schema_version`, `occurred_at`, `correlation_id`, minimal payload
- [ ] Append-only; no secrets or full sensitive prompts in payloads; consumers are idempotent

### 3.8 API error envelope (Ch.18, 31)
- [ ] `{error: {code, message, correlation_id, retryable}}` used everywhere
- [ ] Codes mapped: 400 VALIDATION_ERROR · 401 UNAUTHENTICATED · 403 FORBIDDEN · 404 NOT_FOUND (also for inaccessible resources, to hide existence) · 409 STATE_CONFLICT · 413 PAYLOAD_TOO_LARGE · 429 RATE_LIMITED · 500 INTERNAL_ERROR
- [ ] No stack traces or provider secrets ever returned

### 3.9 Core state machines (Ch.17, 30, 31) — define legal transitions in code, enforce transactionally
- [ ] Mission: `draft → planned → running → awaiting_approval → completed / failed / cancelled`
- [ ] Agent lifecycle: `draft → testing → approved → active → degraded → quarantined → deprecated → retired`
- [ ] Invariant test: a mission can **never** move from `awaiting_approval` to `completed` on an agent's say-so — only a valid approval record bound to the exact action preview + a verified tool result + met acceptance criteria
- [ ] Task claiming uses a lease; finalisation uses compare-and-swap / row lock so a stale worker cannot overwrite a newer result
- [ ] Cancellation is a state transition **plus** a propagation signal to active runs and tools

---

## PHASE 0 — Validate the Problem (Weeks 1–2)

**Goal (Ch.22):** Prove there is a problem worth this architecture, and define "better" before building anything.

### Product & research
- [ ] Pick **1–2** task domains only (e.g. research briefs, web-app builds — the book's two example missions, Ch.23–24)
- [ ] Interview target users for those domains; record pains, current workarounds, willingness to pay
- [ ] Write down explicit non-goals for the MVP (copy §1 list into the product doc)
- [ ] Define the primary metric: **independently verified task success** and **cost per successful task**

### Baseline & benchmarks (this is the phase most teams skip — don't)
- [ ] Assemble 15–30 representative tasks per domain, with rubrics, stored versioned in the repo
- [ ] Run a **strong single agent** on every benchmark task; record success, cost, latency, human correction effort
- [ ] Run a **simple parallel-agent baseline** (same agents, no debate/evaluation machinery); record the same measures
- [ ] Publish the baseline report — every later phase is judged against these numbers
- [ ] Set the improvement bar in writing (e.g. "collaboration must beat single-agent success by X at ≤ Y× cost") — if you cannot state it, you are not ready for Phase 1

### Foundations
- [ ] Choose the modular-monolith stack (§2) and record it as ADR-001
- [ ] Define tenant boundary, data classification levels, retention defaults, approval policy defaults
- [ ] Create the repo using the book's layout (apps/web, services/api, packages/domain, packages/adapters, workers/, migrations/, tests/, infra/, docs/)
- [ ] Threat model v1 drafted (Ch.16 threat list as the starting checklist)

### ✅ Exit Gate — Phase 0
- [ ] 1–2 domains chosen with named target users
- [ ] Benchmark set + rubrics versioned in repo
- [ ] Single-agent AND simple-parallel baselines measured and reported
- [ ] Success bar written down and agreed by the product owner
- [ ] Contracts in §3 drafted (not necessarily final)

**Kill / pivot signal:** If no domain shows a plausible gap where verified collaboration could beat a single agent, stop. Do not build the platform hoping a use case appears.

---

## PHASE 1 — Vertical Slice (Weeks 3–6)

**Goal (Ch.22, front matter):** One mission, end to end, with mock tools. Prove the flow and the baseline comparison before any marketplace or simulation work.
**Slice definition:** 1 mission → 3 tasks → 2 independent agents → 1 targeted critique → schema + citation validation → 2 candidates → user-facing comparison.

### Sprint 1A — Skeleton & mission state
- [ ] Repo tooling: lint, type-check, unit test runner, CI on every PR
- [ ] PostgreSQL + migrations tooling working locally and in CI
- [ ] Auth v1: user login, tenant resolution — authentication kept separate from authorisation
- [ ] Mission CRUD API: `POST /v1/missions`, `GET /v1/missions/{id}` with idempotency key on create (same key + same request = same mission, no duplicate)
- [ ] Mission contract validation (§3.1) enforced server-side; invalid contracts return 400 with the error envelope
- [ ] Mission state machine (§3.9) implemented + unit tests for every legal/illegal transition
- [ ] Mission composer UI (minimal): objective, deliverables, constraints, budget, acceptance criteria
- [ ] Correlation ID generated at API entry and propagated to logs/events

### Sprint 1B — Registry, planner, scheduler (minimal)
- [ ] Agent registry tables + admin seed for **2 curated agents, manually onboarded** (no onboarding UI yet)
- [ ] Capability manifests (§3.3) stored; agent versions immutable
- [ ] Planner v1: decomposes the slice mission into exactly the 3-task DAG; cycle detection before execution; plan version persisted before scheduling
- [ ] Scheduler v1 (Ch.30 pseudocode as the spec): hard eligibility filters first (lifecycle = active, schema support, policy, availability) → then rank by measured quality / cost / latency / diversity
- [ ] Budget reserve step in scheduler: no reservation = no run (`BudgetExceeded`)
- [ ] Universal Agent Interface (Ch.7) implemented as a typed Protocol: `invoke(request) → response`, `health()`
- [ ] 2 adapters behind the interface (mock adapter + 1 real provider adapter); adapter version stored with every run
- [ ] Input AND output schema validation on every adapter call

### Sprint 1C — Execution, artifacts, critique
- [ ] Run execution in-process / simple queue (durable queue arrives in Phase 2 — do not gold-plate here)
- [ ] Artifact service v1: immutable artifacts, content hash, version graph, tenant-scoped access on every read
- [ ] Independent first passes enforced: Agent B cannot read Agent A's artifact until B has submitted its own
- [ ] Critic step: 1 critic produces structured critiques — target claim, why it may be wrong, evidence/test, severity, suggested correction, confidence (Ch.10 format is mandatory, free-text rants are rejected)
- [ ] Bounded loop: MAX_ROUNDS, max tokens, max tool calls, max elapsed time, max cost all set and enforced (Ch.8)
- [ ] Mock tools only behind a stub gateway: allow/deny by mission contract, every invocation logged

### Sprint 1D — Evaluation & candidate comparison
- [ ] Evaluator v1: schema checks + deterministic tests + citation checks (URL resolves, excerpt matches, source supports the adjacent claim — Ch.11 checklist)
- [ ] Evaluation records (§3.6) stored per candidate, dimension-level, rubric versioned as `research-v1` (or domain equivalent)
- [ ] Candidate assembly: 2 genuinely distinct candidates (different strategy/assumptions, not just different wording)
- [ ] Candidate comparison UI: side-by-side with evidence, assumptions, risks, cost, latency, uncertainty, and *why* each scored as it did
- [ ] User can select / combine / request revision of a candidate
- [ ] Cost + latency accounting per mission, per task, per run (even if crude in v1)
- [ ] Event stream (SSE) showing mission progress: task graph, agent activity timeline

### Phase 1 testing (Ch.21 subset)
- [ ] Unit: state transitions, schema validation, ranking, budget accounting, permission checks
- [ ] Contract: both adapters pass the same request/response/error contract tests
- [ ] Integration: DB + artifact store + stub gateway with provider mocks
- [ ] E2E: mission creation → candidate selection → export, automated
- [ ] Economics test (from Ch.21, use it verbatim as a spec): a tool call that would exceed the budget cap is blocked with reason `BUDGET_EXCEEDED`
- [ ] Isolation smoke test: Tenant B cannot read Tenant A's mission or artifact by ID (404, not 403-with-details)

### ✅ Exit Gate — Phase 1
- [ ] The full slice runs end-to-end without manual intervention, on mock tools
- [ ] Benchmark set re-run through the slice; results compared against **both** Phase 0 baselines
- [ ] Comparison report written: success, cost per successful task, latency, correction effort
- [ ] Known limitations listed in the repo (no hiding them)
- [ ] Demo recorded / walkthrough documented

**Kill / simplify signal:** If the slice does not beat the simple parallel baseline enough to justify its cost and latency, simplify the workflow before Phase 2 (Ch.2). Do not add more agents to fix a workflow problem.

---

## PHASE 2 — Reliable Execution (Weeks 7–10)

**Goal (Ch.22):** Make execution durable, safe, auditable, and cancellable. This phase is unglamorous and is the difference between a demo and a product.

### Durable execution
- [ ] Durable queue / workflow engine adopted (ADR required: options, decision, revisit trigger)
- [ ] Idempotent task handlers + deduplication keys on every retried operation
- [ ] Explicit timeouts on every provider/tool call; bounded retries with exponential backoff + jitter
- [ ] Dead-letter queue with controlled, audited replay
- [ ] Checkpoints before expensive or irreversible steps
- [ ] Cancellation propagation verified end-to-end: API → mission → tasks → active runs → tool calls
- [ ] Worker crash / queue redelivery resilience tests pass (Ch.21)

### Tool Gateway (real, Ch.12)
- [ ] Gateway is the **only** path to external actions; direct provider/tool calls from agents are blocked by architecture, not by prompt
- [ ] Invocation lifecycle enforced in order: authenticate run → tenant policy check → schema validation → resource scope → data classification → quota/rate limit → risk classification → approval (if required) → execute with short-lived scoped credentials → verify result independently → audit record
- [ ] Risk tiers implemented: Low (read-only) / Moderate (internal edits, sandboxed code) / High (send, publish, delete, pay, production change — approval + preview mandatory)
- [ ] Approval binds to the exact action preview + version; any material change invalidates the approval
- [ ] Tool results are never trusted on the agent's word — gateway result is the record
- [ ] Inputs recorded redacted; provider IDs, duration, cost stored per invocation

### Sandboxing (Ch.12)
- [ ] Ephemeral execution environments with CPU, memory, wall-clock, process, filesystem, and network limits
- [ ] No access to control-plane credentials, host sockets, internal metadata services, or other tenants' files
- [ ] Dependencies pinned; artifacts scanned before use
- [ ] Documented note in threat model: a container alone is not a complete boundary for hostile code

### Cost & scheduling (Ch.15)
- [ ] Budget ledger per mission: allocated / reserved / spent / remaining, by category (inference, tools, runtime, retrieval, storage, evaluation, retries, orchestration)
- [ ] Budget reserved for final evaluation so early agents cannot starve it
- [ ] Hard caps enforced *during* execution, not reconciled after
- [ ] Provider concurrency + rate limits respected; tenant quotas + fair-use queues
- [ ] Backpressure when downstream tools/storage are unhealthy

### Provenance & artifacts
- [ ] Full lineage: every artifact records inputs, agent version, adapter version, model/provider version, prompts/rubric versions
- [ ] Object storage with tenant-scoped paths + server-side authorisation (secret URLs are never the only control)
- [ ] Append-only audit events stored separately from mutable state

### ✅ Exit Gate — Phase 2
- [ ] Kill a worker mid-mission: the mission recovers or fails cleanly, with no duplicate side effects
- [ ] Budget cap, cancellation, and approval-bypass tests all pass
- [ ] Every external action in a test mission is traceable gateway → approval → result → audit event
- [ ] Cost report per mission reconciles with provider usage records

---

## PHASE 3 — Real Collaboration (Weeks 11–14)

**Goal (Ch.22):** Collaboration that measurably helps — structured, bounded, and evidence-driven.

- [ ] Structured messaging live (§3.4 envelope + all message types); agents exchange artifact references, not raw transcripts
- [ ] Tenant/classification check on **every** message and artifact read; no default broadcast of private user data to all agents
- [ ] Collaboration loop implemented as specified (Ch.9): independent pass → publish claims/evidence → targeted critique → accept / rebut-with-evidence / revise / mark unresolved → evaluator tests disputes → stop
- [ ] Stopping rules enforced (Ch.10): acceptance met · only low-severity issues remain · marginal gain < cost · budget/deadline hit · human decision needed
- [ ] Critique quality bar: a critique without a target, evidence/test, and severity is rejected by schema
- [ ] Candidate clustering: semantic similarity **plus** key-decision comparison; merge/split decisions carry a human-auditable reason (Ch.10)
- [ ] Materially different candidates preserved even when they score lower on one dimension; minority views with evidence stay visible in the UI
- [ ] Adaptive cohort expansion (Ch.15): extra agents recruited only on evidence gaps, uncertain evaluations, material conflicts, or justified mission value — logged with the reason
- [ ] Replanning (Ch.8): only on defined triggers (failed dependency, new evidence, failed evaluation, explicit user change); old plan versions preserved with the reason for the change
- [ ] A/B harness: collaboration ON vs OFF on the benchmark set, same tasks, same budgets

### ✅ Exit Gate — Phase 3
- [ ] Benchmark comparison published: collaboration vs Phase 0 baselines AND vs Phase 1 slice
- [ ] Cost per successful task and correction effort both reported — not just success rate
- [ ] Debate rounds demonstrably bounded (no runaway loops in load/chaos tests)
- [ ] If collaboration does not earn its cost on a domain, that domain is documented as single-agent-by-default

---

## PHASE 4 — Simulation Worlds (Weeks 15–18)

**Goal (Ch.13, 22):** Compare conditional strategies in isolated branches. Only start this once the Phase 1–3 evaluation pipeline is credible.

- [ ] World model stored: world ID, parent world, baseline snapshot, assumptions, task graph, agent/model versions, actions, artifacts, seeds (where supported), evaluation results, creation metadata
- [ ] Baseline state frozen before branching; child worlds are immutable copies of that baseline
- [ ] Hard isolation: no mutable state, credentials, or artifacts leak between branches (isolation tests required)
- [ ] Each branch gets a comparable budget and the **same** evaluation rubric
- [ ] Branch count, depth, iterations, external calls, and cost all bounded with hard limits
- [ ] Comparison view: outcomes, sensitivity to assumptions, failure cases — side by side
- [ ] Every simulation output labelled "simulated estimate" in UI and exports, with reproducibility metadata attached
- [ ] No path from a simulation world to a real-world action except via the explicit approval flow (leaving simulation mode is a visible, logged action)
- [ ] Non-determinism disclosed: model outputs and external services may vary even with fixed seeds

### ✅ Exit Gate — Phase 4
- [ ] Two example branches (use Ch.23's three launch strategies as the test case) run, compare, and reproduce within documented variance
- [ ] Cross-branch leakage tests pass
- [ ] A user test confirms readers understand outputs are estimates, not predictions (human-factors test, Ch.21)

---

## PHASE 5 — Ecosystem Growth (Weeks 19–24)

**Goal (Ch.6, 22):** Scale curation without lowering the trust bar.

### Agent onboarding pipeline (Ch.6 — all 10 steps, in order, per agent)
- [ ] 1. Discover candidate; verify source, owner, identity, endpoint
- [ ] 2. Read terms, licence, data-processing commitments, limitations — record version + date reviewed
- [ ] 3. Inspect package/dependencies and network behaviour (if self-hosted)
- [ ] 4. Create capability manifest (§3.3); define allowed input/output schemas
- [ ] 5. Implement adapter behind the stable platform interface
- [ ] 6. Run conformance tests: schema, timeout, error handling, prompt-injection, tenant isolation
- [ ] 7. Benchmark on representative tasks with blind labels and reproducible datasets
- [ ] 8. Set trust tier, permissions, quotas, fallback behaviour
- [ ] 9. Roll out: internal test missions → small % of eligible traffic
- [ ] 10. Monitor quality drift, latency, cost, incidents, provider changes; re-evaluate on version change and on a scheduled cadence

### Platform work
- [ ] Onboarding UI + curator console (mission history, usage, incidents — Ch.18 surfaces)
- [ ] Conformance suite packaged so steps 6–7 above are one command, not a manual ritual
- [ ] Lifecycle automation: quarantine immediately blocks new assignments while preserving inspection of existing runs; all lifecycle transitions audited
- [ ] Ranking v2: hard eligibility filters first, then visible dimension scores (quality, reliability, diversity contribution, latency, cost) — dimensions shown separately, never collapsed into one opaque score (Ch.6, 14)
- [ ] Evaluator hardening: blind candidate labels, deterministic checks preferred, different model families where practical, rubric calibration against human judgements, drift monitoring (Ch.14)
- [ ] A2A / MCP integration **only** where a concrete interoperability need exists, after verifying current protocol specs — and never as a replacement for registry, identity, permissions, scheduling, evidence, or evaluation (Ch.7)

### ✅ Exit Gate — Phase 5
- [ ] A new agent can be onboarded end-to-end using only the documented pipeline + conformance suite
- [ ] Quarantine drill passes: a "bad" agent is quarantined in a test and receives zero new assignments
- [ ] Ranking dimensions are visible and explainable to a user asking "why this agent?"

---

## PHASE 6 — External Ecosystem (Month 7+)

**Goal (Ch.22):** Let outsiders in — slowly, with control. Marketplace comes **last** in this phase, not first.

- [ ] Developer onboarding: identity/ownership verification, terms acceptance, security review process published
- [ ] Developer sandbox environment, isolated from production tenants and data
- [ ] Public documentation: interface contracts, manifest spec, conformance suite, lifecycle policy
- [ ] Support channel + incident process for external developers (SLAs only if you can actually meet them)
- [ ] Abuse controls live before launch: rate limits, quotas, abuse detection, kill switches per agent/developer (Ch.16)
- [ ] Legal pack complete (Ch.26): licence, acceptable use, data processing, retention/training use, subprocessors, geographic processing, output rights, attribution — per provider/agent, versioned
- [ ] Marketplace (only now, and only if Ch.25 conditions are met): quality, trust, ownership verification, licensing, version compatibility, incident response, and developer support are all mature
- [ ] If ranking is ever commercially influenced: sponsorship disclosed, paid placement kept separate from measured quality ranking (Ch.25)

### ✅ Exit Gate — Phase 6
- [ ] External developer onboarded in a pilot without engineering hand-holding beyond documented support
- [ ] Security review + legal review signed off in writing
- [ ] Incident drill with an external agent completed (detect → contain → notify → remediate → post-incident review, Ch.16)

---

## PHASE 7 — Enterprise Readiness (Month 9+)

**Goal (Ch.22):** Controls and assurances larger organisations require. Build only against real customer requirements.

- [ ] Organisation controls: roles, membership management, project-level policies
- [ ] SSO / SAML if (and only if) required by actual customers
- [ ] Audit exports (events, approvals, tool invocations, evaluations) in a documented format
- [ ] Retention options per tenant: configurable retention, deletion, export — deletion workflow covers primary records, object versions, search/vector indexes, caches, previews, and provider deletion requests, with exceptions recorded (Ch.31)
- [ ] Deployment options assessed (shared cloud vs private) against real demand
- [ ] Support commitments defined and staffed before being sold
- [ ] Formal security review / penetration test by qualified external reviewers; findings remediated or explicitly accepted in writing
- [ ] Data-handling disclosures published and accurate: which providers may receive user data, retention, deletion behaviour including backup expiry

### ✅ Exit Gate — Phase 7
- [ ] Restore-from-backup and rollback both **tested** (not assumed) against documented RPO/RTO
- [ ] Deletion request completed end-to-end in a test tenant and verified across every store
- [ ] External security review closed with no critical findings open

---

## 4. Cross-cutting checklists (every phase, every release)

### 4.1 Testing matrix (Ch.21)
- [ ] Unit — state transitions, schema validation, ranking, budget accounting, permission checks
- [ ] Contract — every adapter conforms to request/response/error contracts
- [ ] Integration — DB, queue, object store, tool gateway, provider mocks
- [ ] End-to-end — mission creation → candidate selection → export
- [ ] Agent behaviour — representative tasks, output validity, refusal behaviour, tool discipline
- [ ] Regression — fixed task set with versioned expected properties, run on every release
- [ ] Adversarial — prompt injection, poisoned documents, malicious agents, malformed files
- [ ] Isolation — cross-tenant API, object, cache, search, and event access (any failure here blocks release)
- [ ] Resilience — provider timeout, worker crash, queue redelivery, cancellation, retry storms
- [ ] Economics — hard budget cap, concurrency limit, usage accounting accuracy
- [ ] Human factors — candidate distinctions understandable, uncertainty visible, approval screens genuinely comprehensible
- [ ] Live-provider smoke tests kept in a separate controlled suite with explicit spend limits

### 4.2 Security & privacy (Ch.16)
- [ ] Least privilege everywhere; short-lived scoped credentials; secrets in a secret manager, rotated, never in prompts/logs/repo
- [ ] Encryption in transit and at rest; field-level protection where the data classification requires it
- [ ] Input/output validation, file type/size limits, malware scanning where appropriate
- [ ] Redaction of secrets and personal data in all telemetry
- [ ] Signed/versioned agent artifacts where feasible; dependency scanning in CI; provenance recorded
- [ ] Rate limits, quotas, abuse detection, and a tested kill switch
- [ ] Incident response runbook written **and practised**: detect → contain (disable agent/tool/credential) → preserve audit evidence → assess tenant/data impact → notify per policy/law → remediate → validate recovery → post-incident review
- [ ] Qualified legal/security review obtained for applicable laws, provider terms, and high-impact deployments

### 4.3 Observability & operations (Ch.19)
- [ ] Correlation ID propagated: API → mission → task → run → provider call → tool invocation → artifact → evaluation
- [ ] Structured logs, metrics, traces, and durable audit events kept as separate systems with separate retention
- [ ] Metrics live: mission/task success, p50/p95/p99 latency, provider error/timeout rates, retry counts, queue depth/age, tool failures, evaluator disagreement, budget utilisation, cancellation rate, cost per successful task, quarantine events, approval wait time
- [ ] Alerts on: sustained provider failure, growing queue age, repeated budget overruns, cross-tenant access attempts, abnormal tool behaviour, elevated cancellations, missing telemetry, storage/backup failure
- [ ] Runbooks written and tested: provider outage (Ch.31 version is a good template), credential compromise, bad deployment, runaway mission
- [ ] Backups scheduled **and restores tested on a cadence** — an untested backup is a hope, not a control

### 4.4 Data (Ch.17)
- [ ] Core entities migrated: organisations, users, memberships, projects, missions, mission_versions, tasks, task_dependencies, agents, agent_versions, capabilities, runs, messages, artifacts, artifact_versions, sources, claims, candidates, evaluations, approvals, tool_invocations, usage_records, worlds, events, incidents
- [ ] Tenant-aware constraints + row-level security where appropriate; status transitions validated in DB and app layers
- [ ] Large binaries in object storage, never in mission rows
- [ ] Every search result re-authorised before content is returned
- [ ] Full-text search first; vector retrieval added only with a measured use case
- [ ] Deletion covers derived indexes, caches, previews, and retention queues

### 4.5 API & UI (Ch.18)
- [ ] Versioned JSON API (`/v1/...`); pagination, rate limits, idempotency keys on create/action endpoints, correlation IDs on all responses
- [ ] Endpoint surface: `POST /v1/missions` · `GET /v1/missions/{id}` · `POST /v1/missions/{id}/approve` · `POST /v1/missions/{id}/cancel` · `GET /v1/missions/{id}/events?after={cursor}` · `GET /v1/agents?capability=...` · `GET /v1/artifacts/{id}` · `POST /v1/missions/{id}/revisions` · `GET /v1/worlds/{id}/comparison`
- [ ] SSE for one-way live updates; WebSockets only where bidirectional interaction is genuinely required
- [ ] UI surfaces: mission composer · task graph · agent activity timeline · evidence/artifact viewer · debate/critique timeline · candidate comparison · approval inbox · mission history · usage dashboard · curator console · incident console
- [ ] UI always displays: uncertainty, source provenance, cost, latency, and why a candidate was selected

### 4.6 Cost & business metrics (Ch.15, 25)
- [ ] Tracked per mission: model input/output usage, external API calls, tool invocations, runtime, retrieval, storage, evaluator calls, retries, orchestration overhead
- [ ] Cost estimated **before** a mission starts and shown to the user; hard cap enforced during the run
- [ ] Business dashboard shows: cost per successful task, gross margin by task class, repeat use, user correction effort, success rate, latency, retention, support burden
- [ ] Never report "number of agents" or "number of messages" as success metrics
- [ ] Pricing (when it comes) is based on outcomes/tasks, not raw agent count; quotas aligned with plan economics

### 4.7 Release gate — applies to EVERY production release (Ch.20, 21, 29, 31)
A release is **blocked** if any of these are true:
- [ ] Any critical tenant-isolation test failing
- [ ] Approval can be bypassed, or is not bound to the exact action preview
- [ ] Budget caps unenforced, or usage accounting unverified
- [ ] Artifact lineage broken
- [ ] Secrets found in logs, events, or artifacts
- [ ] Cancellation non-functional anywhere in the chain
- [ ] A material regression is unexplained
- [ ] Restore/rollback not tested for this release's migrations
Release sequence:
- [ ] Lint → type-check → unit tests → dependency + secret scans
- [ ] Build immutable images; record SBOM where practical
- [ ] Integration + security tests in an isolated environment
- [ ] Backward-compatible migrations only
- [ ] Staging deploy → smoke + load tests
- [ ] Canary a small production slice with strict budget + kill switch armed
- [ ] Compare success / errors / latency / cost against the previous baseline before full rollout

---

## 5. First 30 build tasks — in dependency order (Phase 0–1 backlog)

Use this as the literal starting backlog. Do not reorder across dependencies.

1. [ ] Repo scaffold using the book's layout + CI (lint, type-check, unit tests)
2. [ ] ADR-001: modular monolith + stack decision recorded
3. [ ] Domain schemas v1: mission contract, task contract (§3.1–3.2) with validators + tests
4. [ ] Domain schemas v1: capability manifest, message envelope, claim ledger, evaluation record, event envelope, error envelope (§3.3–3.8)
5. [ ] Benchmark task set v1 (15–30 tasks/domain) + rubrics, versioned
6. [ ] Single-agent baseline runner + baseline report
7. [ ] Simple parallel baseline runner + baseline report
8. [ ] PostgreSQL schema + migrations: tenants, users, missions, tasks (core slice only)
9. [ ] Mission state machine + transition tests
10. [ ] Auth v1 + tenant authorisation middleware (with cross-tenant 404 test)
11. [ ] `POST /v1/missions` with idempotency + contract validation + error envelope
12. [ ] Mission composer UI (minimal, functional over beautiful)
13. [ ] Registry tables + seed: 2 manually curated agents with manifests
14. [ ] Universal Agent Interface (Protocol) + mock adapter + contract tests
15. [ ] First real provider adapter + contract tests (same suite as #14)
16. [ ] Planner v1: fixed 3-task DAG decomposition, cycle check, plan versioning
17. [ ] Scheduler v1: eligibility filters → ranking → budget reserve
18. [ ] Artifact service v1: immutable, hash-addressed, tenant-checked reads
19. [ ] Independent-first-pass enforcement between the 2 agents
20. [ ] Critic agent + structured critique schema enforcement
21. [ ] Bounded collaboration loop (MAX_ROUNDS + all budget/time/token limits)
22. [ ] Stub tool gateway: allow/deny by contract, full invocation logging
23. [ ] Evaluator v1: schema checks + deterministic tests
24. [ ] Citation checker: resolve, excerpt-match, claim-support checks
25. [ ] Candidate assembly: 2 distinct candidates with evidence/assumptions/risks attached
26. [ ] Candidate comparison UI + select/combine/revise actions
27. [ ] SSE mission event stream + activity timeline UI
28. [ ] Per-mission cost/latency accounting report
29. [ ] E2E test: mission → candidates → selection → export
30. [ ] Phase 1 benchmark re-run + comparison report vs both baselines + known-limitations doc

---

## 6. Initial risk register (seed — maintain from Phase 0, Ch.27–28)

| # | Risk / anti-pattern | Early warning sign | Mitigation in this roadmap |
|---|---|---|---|
| R1 | Collaboration doesn't beat a single agent | Phase 1 benchmark ≈ baseline | Phase 0 baselines + Phase 1/3 kill-or-simplify gates |
| R2 | Cost runaway / unbounded debate | Budget utilisation alerts, long missions | Hard caps, bounded loops, budget ledger (Phase 2) |
| R3 | Consensus mistaken for truth | Candidates agree but citations are weak | Evidence engine, citation checks, minority-view preservation |
| R4 | Tool autonomy by prompt | Agent attempts an action with no gateway record | Gateway-only architecture, approval binding (Phase 2) |
| R5 | Prompt injection via retrieved content | Tool/web content tries to change agent behaviour | Untrusted-data rule, gateway policy, adversarial tests |
| R6 | Cross-tenant leakage | Any isolation test failure | Authorisation on every surface; release-blocking gate |
| R7 | Citation theatre | Sources listed that don't support claims | Excerpt-match + claim-support checks (Phase 1) |
| R8 | Premature scaling (microservices, vectors, marketplace) | Phase-skipping proposals | Phase order + exit gates in this document |
| R9 | Evaluator gaming / drift | Scores rise while human judgements don't | Blind labels, calibration, held-out tasks, drift monitoring |
| R10 | Sensitive data in logs | Secret/PII found in telemetry scan | Redaction, retention policy, release gate check |
| R11 | Provider change / outage | Error-rate alerts on one adapter | Adapter isolation, health-based routing, outage runbook |
| R12 | Simulation read as prediction | Users quote estimates as facts | Mandatory labelling, human-factors test (Phase 4) |

For every incident, record (Ch.27): which invariant failed · why tests missed it · what telemetry would have detected it · the structural fix (not just a prompt tweak).

## 7. ADR backlog — decisions to record early (Ch.28, 31)

- [ ] ADR-001 Modular monolith vs services (start: monolith)
- [ ] ADR-002 Workflow/queue engine choice
- [ ] ADR-003 Artifact immutability + content-hash model
- [ ] ADR-004 Event delivery semantics (append-only, idempotent consumers)
- [ ] ADR-005 Candidate scoring rubric design (dimensions, no single opaque score)
- [ ] ADR-006 Data retention defaults + deletion workflow
- [ ] ADR-007 Provider adapter strategy + first provider selection
- [ ] ADR-008 Search strategy (full-text first; criteria for adding vectors)
Each ADR: title, status, context, options, decision, positive/negative consequences, security/privacy impact, owner, date, revisit trigger.

## 8. Definition of Done — any task, any phase

- [ ] Code merged with tests (unit + relevant layers from §4.1) passing in CI
- [ ] Schemas/contracts updated and versioned if behaviour changed
- [ ] No new secrets, no sensitive data in logs — verified, not assumed
- [ ] Tenant authorisation considered and tested if the change touches data access
- [ ] Cost/budget impact considered if the change touches execution
- [ ] Docs/ADR updated in the same PR where the decision changed
- [ ] This roadmap's checkbox ticked **with a link to the evidence**

---

*Prepared by Milo for OmniMind AI, from AI Agent Ecosystem, Ed. 1.0 (10 Oct 2026). Chapter references are to that textbook. Time estimates are planning estimates for a small team and must be re-based on actual Phase 1 velocity. Nothing in this roadmap overrides the textbook's own caution: provider specs, terms, and pricing must be re-validated at implementation time.*
