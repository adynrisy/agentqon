# OmniMind AI — Project Status

Updated: 2026-10-10 (V3 Master Build).

> **Superseded snapshot (audit note, 2026-10-12).** This page is the
> status as of the 2026-10-10 V3 build, kept as history. The product
> has since shipped as **AgentQon v21.4.2**: v20 removed the mock
> engine entirely (the "mock fallback" rows below describe the
> pre-v20 design — generation is real-models-only now), and later
> releases added the Shield payload, the brain API, custom agents,
> the memory layer and the 6,799-agent roster. Do not read the test
> counts or feature rows here as the current state.

## What exists and is verified

| Area | Status | Evidence |
|---|---|---|
| Phase 1 vertical slice (missions, planner DAG, independent agents, critic, evaluation, selection, tenant isolation, idempotency, artifacts, cost ledger) | ✅ real | 24 tests; live HTTP mission |
| Phase 2 durability (PostgreSQL behind repo interface, durable queue + DLQ + replay, external worker) | ✅ real | Live DLQ→replay cycle on PG |
| Phase 2 real AI (OpenAI-compatible LLM adapters + mock fallback) | ✅ real | Live mission on local Ollama qwen2.5:1.5b; all runs `llm-adapter-1.0` |
| V3 credential vault (envelope encryption, key versioning, crypto-shred on revoke/delete) | ✅ real | Unit tests incl. tamper/wrong-key; API tests prove secrets never echo |
| V3 BYOK connections (lifecycle, validation, rotate, pause/revoke, cross-tenant 404) | ✅ real | Integration tests + live OmniRoute connection (475 models synced) |
| V3 dynamic catalog (sync, freshness, price provenance, user-declared entries) | ✅ real | Live sync from OmniRoute + Ollama; unit tests for staleness |
| V3 cost-aware router (6 modes, hard filters, deterministic, `NO_ELIGIBLE_MODEL`) | ✅ real | Unit tests per mode/filter; route decisions persisted + shown in UI |
| V3 budget reservations (reserve→reconcile/release, race-safe) | ✅ real | 10-thread race test (SQLite) + PG cycle test; live mission accounting consistent |
| V3 harness completion (pause/resume, bounded revision with new versions, export, projects) | ✅ real | Integration tests; export renders Markdown brief |
| OmniRoute v3.8.51 sidecar integration | ✅ real (transport) | Full mission OmniMind → OmniRoute → Ollama; see integration audit + compatibility matrix |
| Strict BYOK rule (pinned connection failure = mission failure, never platform-key substitution) | ✅ real | Test: revoked connection fails mission with zero candidates |
| Admin surface (overview + kill switch, admin-key gated) | ✅ real | Integration tests |
| Web UI (missions, connections, catalog, routing preview, pause/resume/revise/export, admin) | ✅ real (functional) | All controls call the live APIs above |

## Phase 4 additions (2026-10-10)

| Area | Status | Evidence |
|---|---|---|
| Accounts auth (PBKDF2 passwords, hashed revocable session tokens) | ✅ real | Integration tests + live run (register → mission → logout revocation) |
| Tenant memberships + roles (owner/admin/member/viewer) enforced per action | ✅ real | Tests: viewer 403 on create/connections, owner-only owner-grants, last-owner protection; live run confirms 401/403/200 paths |
| OIDC bearer verification (RS256 vs issuer JWKS, iss/aud/exp, JIT provisioning) | ✅ real | Unit tests with a locally generated RSA keypair: valid accepted; wrong aud/iss, expired, unknown kid, forged payload all rejected |
| Rate limits (login per email, runs per tenant, validations per tenant; 429 + Retry-After) | ✅ real | Store race-safe counters; test drives login limit to 429 |
| Vault key providers (env / key file) + rotation re-wrap endpoint | ✅ real | Test: v1→v2 rotation, rewrap, old key retired, shredded rows never resurrected |
| Request-time SSRF re-validation (rebinding defense in GatewayClient) | ✅ real | Tests: metadata/private resolutions blocked at call time; benchmark egress answers unaffected |

## Phase 5 additions (2026-10-10)

| Area | Status | Evidence |
|---|---|---|
| Live web research (keyless DDG search + bounded fetch/extract) through the Tool Gateway | ✅ real | Live mission: ResearchCompleted with **14 real sources** (2 queries); candidates cited NerdWallet, beancount.io, bizdevstrategy.com, stratridge.com with real URLs |
| Provenance (URL, publisher, published date when discoverable, content hash, retrieval method) | ✅ real | Stored per source; shown in UI with a LIVE WEB badge |
| Classification gate (confidential/restricted stay on local corpus unless tools.live_web=true) | ✅ real | Tests: provider never invoked for confidential; override forces live |
| Fallback honesty (provider failure → seed corpus + ResearchFallback event) | ✅ real | Integration test + one accidental live proof during development (proxy env bug, since fixed) |
| Corpus reaches agents (mock rotates citations onto live ids; LLM library = live corpus, invented ids stripped) | ✅ real | Integration + LLM adapter unit test |
| Streaming progress (SSE mission events + UI live activity) | ✅ real | Stream test: MissionCreated…MissionCompleted delivered; UI EventSource wiring (dev mode; accounts-mode browsers can't set stream headers — polling fallback documented) |
| Egress robustness (explicit proxy + system CA bundle; fixes bracketed-no_proxy crash for ALL public gateway calls) | ✅ real | Live DDG + gateway paths re-verified; regression suite green |

## Phase 6 additions (2026-10-10)

| Area | Status | Evidence |
|---|---|---|
| Golden set: 25 frozen missions with authored expectations (benchmarks/golden_set.json) | ✅ real | Seed suite **25/25 pass**; aggregates: mean dimension 4.25, citation coverage 1.00, mean spend $1.07/case |
| Live golden subset (real web) | ✅ real | 6/6 pass: 3 live-researched cases cite live sources; confidential/restricted cases verified skipped onto the seed corpus |
| Router quality matrix (6 modes x 3 scenarios, real router) | ✅ real | All invariants hold: confidential always local, pinned refuses under tight budget/confidential, flagship excluded when budget-tight; matrix in golden_results.json |
| Critic calibration (12 rubric-labelled fixtures, Cohen's kappa) | ✅ real, with a found-and-fixed defect | Original severity rules disagreed with rubric labels on 4/12 cases (kappa ~0.47, computed from the pre-fix rules); corroboration-aware severity now scores **kappa = 1.0**, agreement 12/12 |
| Release gates in the runner (exit code) | ✅ real | `python -m benchmarks.run_golden` — seed suite + invariants + kappa gate |

## Phase 7 additions (2026-10-10) — Simulation Worlds (textbook Ch.13)

| Area | Status | Evidence |
|---|---|---|
| World model (frozen baseline, parent/provenance, seed, versions, baseline hash) | ✅ real | Worlds seeded from missions carry objective, source snapshot hash and candidate research-v1 scores |
| Branch isolation (immutable baseline copies, no shared mutable state, no credentials/gateway in worlds) | ✅ real | Isolation test: a starved branch cannot affect its full-budget twin or the baseline |
| Bounds (<=6 branches, <=52 ticks, market cap, identical budget+rubric per branch) | ✅ real | Engine + API tests |
| Comparison + sensitivity (±20% price / competitor strength) side by side | ✅ real | Live run of Ch.23's three strategies below |
| "SIMULATED ESTIMATE" labelling + reproducibility metadata everywhere (UI, API, exports) | ✅ real | Outcome hashes on every branch; disclosure text stored on the world |
| Only exit = explicit logged promote -> DRAFT mission | ✅ real | Live-verified; BranchPromoted event; mission starts as draft under normal approvals |
| Exit-gate reproduction | ✅ real | Two identical worlds produced identical outcome hashes |

Live comparison (same $150k budget, seed 42, 26 weeks, market 20,000):
niche-first 7,459 adopters (CAC $12.82, 90-day retention 59.3%) ·
partnership-led 1,901 (CAC $27.08, 42.5%) · broad 392 (CAC $42.47, 19.7%,
budget exhausted week 13). The human-factors labelling test with real
users (Ch.21) has NOT been run — labelling is implemented and stated,
comprehension is unverified.

## Phase 0 baseline study (completed 2026-10-10)

docs/BASELINE_STUDY.md — single vs parallel vs full pipeline, two arms
(deterministic mock + real LLM on local qwen2.5:1.5b), improvement bar
stated before results. Verdict: the critic is the value (surfaced 3
high-severity defects and flipped one selection at 2.0x single-agent
cost — bar met via the defect-surfacing clause); parallel-without-critic
recorded as a dominated configuration. Outstanding honestly: external
user interviews / willingness-to-pay unmeasured.

## Agency roster installation (2026-10-10, post-roadmap)

| Area | Status | Evidence |
|---|---|---|
| msitarzewski/agency-agents roster (MIT) bundled + importer | ✅ real | 280 agents installed per workspace via POST /v1/agents/import-agency (16 strategy playbook docs correctly skipped — no frontmatter, not agents) |
| Onboarding discipline | ✅ real | Agents land in lifecycle **testing**; scheduler never picks them until an operator approves + activates; re-import preserves lifecycle |
| Persona execution | ✅ real | Persona briefs (global persona store) lead the LLM system prompt under the platform's evidence rules; live mission produced both candidates from agency.marketing personas with the core researchers quarantined |
| Capability curation | ✅ recorded | Advisory/analytical divisions also get research.web; builder/reviewer divisions get persona.task only — mapping documented in app/agency.py |
| UI | ✅ real | Import button + agent filter in the registry card |

## Test counts (2026-10-10)

- SQLite default: **109 passed, 3 skipped** (PG-only tests skip cleanly).
- PostgreSQL: V3 suite passed **69** earlier today; Phase 4's PG
  re-verification is **pending an environment repair** — the VM was
  replaced mid-session and its PostgreSQL install (a system package,
  not workspace data) was lost; the apt mirror is currently timing
  out. Phase 4's PG-specific SQL is limited to one standard
  `ON CONFLICT DO NOTHING` upsert in the rate counter (SQLite branch
  fully tested; PG branch syntax-reviewed). Re-run
  `OMNIMIND_TEST_DATABASE_URL=... pytest` after reinstalling
  PostgreSQL 16 (see runbooks) to close this.

## Deliberately mocked / still open

- Seed source library (citation corpus) is demonstration data — live web
  research is a later phase.
- Agent-generated spend through gateways records **unknown** inference cost
  (token counts real; gateway prices absent) — ledger stays honest.
- Production authN/Z (OIDC), billing, multi-node deployment, streaming UI:
  later phases (see IMPLEMENTATION_PLAN.md).
- OmniRoute `auto/*` combos, streaming, tool-calling via gateway:
  unverified (compatibility matrix).

- v11: simulation worlds are **grounded in the mission's researched data** (market size / price / competitor strength mined from cited sources, each with provenance; assumptions labelled). Chat gains a one-tap **Simulate these strategies** flow that narrates grounded outcomes; candidate selection is now exclusive (re-selection clears the previous pick).

- v12: **general-purpose missions**. Every mission carries a `task_type` (strategy | research | writing | code | plan | qa | general), resolved at creation by a deterministic classifier (`app/tasktypes.py`; explicit contract value wins). 'Strategy' covers all decision/comparison missions — the golden set routes 25/25 there, pipeline unchanged. Other types produce a real **deliverable** (Markdown draft/answer/code/plan/report) from two agents (direct vs thorough), critic-reviewed and rubric-scored as before. Chat shows the type chip, renders deliverables, mixes example tasks, and offers simulation only for strategy missions.

- v13: **the whole roster works, tracked by suitability**. Agency import now lands agents ACTIVE (operator's standing order; re-import still preserves per-agent lifecycle decisions). Drafting is done by a suitability-ranked PANEL of up to 5 agents per mission (`app/suitability.py`): score = capability/division fit + declared quality, blended toward measured performance (evaluation means, high critiques, user selections) as runs accumulate in the `agent_stats` ledger. PanelSelected events carry the reasoning; GET /v1/agents/stats and /v1/agents/suitability expose the ledger and rankings; the registry shows each agent's record and a 'Who suits which task?' board. The evaluator's distinct-approach test is now task-type aware.

- v17: **the WhatsApp front door (Meta Cloud API)**. The Telegram door's twin on Meta's mechanics (`app/whatsapp.py` + `app/whatsapp_client.py` — the client is the ONLY module that calls the Graph API, base + version from OMNIMIND_WHATSAPP_API_BASE / OMNIMIND_WHATSAPP_API_VERSION so tests/proofs stub it). Linking mirrors v16 (one-time code minted at POST /v1/channels/whatsapp/link-code, ~10 min, single-use, minting replaces unused codes; GET /status + DELETE /link serve the ⚙ Advanced → WhatsApp panel) but redemption is a `LINK <code>` message — no /start deep link exists on WhatsApp; the panel shows a `wa.me/<number>?text=LINK%20<code>` tap-to-link URL when OMNIMIND_WHATSAPP_BUSINESS_NUMBER is set. Webhook: GET handshake returns hub.challenge only for hub.mode=subscribe + the matching OMNIMIND_WHATSAPP_VERIFY_TOKEN; POST deliveries are deduped by wamid and, whenever OMNIMIND_WHATSAPP_APP_SECRET is set, MUST carry a valid X-Hub-Signature-256 (HMAC-SHA256 of the RAW body) — without an app secret the webhook is unauthenticated, a local-dev trade-off documented loudly in code, config and README, never silently. Linked numbers: plain text (or `mission …`) creates a mission through the normal contract path (the chat contract is shared with v16; classifier-resolved task type, default classification, live_web NEVER silently enabled; viewer-role links refused), `status`, `arena <topic>` (blind duel: outputs as text, each side's vote a single reply button — interactive bodies cap at 1024 chars so outputs ride as text and the button message is only the call to action), `schedules`. Votes go through arena.cast_vote; button payloads naming another tenant's duel/mission fail politely; spent buttons cannot be removed on WhatsApp, so a re-tap earns "already decided". Results are pushed by a window-aware outbox tick in the worker drain + main loop: **Meta's 24-hour customer-service window is the load-bearing rule** — the link record tracks last_inbound_at; in-window completions push the full summary + [🧾 Proof Pack] [⚔️ New Duel] reply buttons (proof pack = label summary, then media upload, then document-by-media-id); out-of-window completions send ONE utility template alert (OMNIMIND_WHATSAPP_TEMPLATE_NAME, {{1}} = mission title) if configured, else are HELD — either way the held full result is delivered the moment the user's next inbound reopens the window (deliver_pending_for_link in the inbound path + tick retries), so nothing is ever dropped for being out of window. All output plain text, chunked under the 4096 cap (shared chunker). Channel disabled (token or phone number id unset) = clean 503 WHATSAPP_NOT_CONFIGURED on all five routes, boot and everything else untouched. Store: whatsapp_links / whatsapp_codes / whatsapp_updates / whatsapp_outbox / whatsapp_duels JSON-blob tables. Live proof (offline, stub Graph API, agency + library registry of 6,759 agents): handshake 200, bad signature 403, LINK binds, mission completes and pushes with buttons, proof pack uploads 9,212-byte media + document, arena duel voted (ledger 1 win / 1 loss, winner revealed), out-of-window mission alerted by template then delivered in full on reply — 18 Graph API calls captured. Suite: **216 passed, 3 skipped** (190 + 26 new, zero regressions; Telegram untouched).

- v16: **the Telegram front door**. Run the platform from chat (`app/telegram.py` + `app/telegram_client.py` — the client is the ONLY module that calls the Bot API, and its base comes from OMNIMIND_TELEGRAM_API_BASE so tests/proofs stub it). A Telegram chat links to exactly one account (tenant + user + role snapshot) via a one-time code minted at POST /v1/channels/telegram/link-code (~10 min, single-use, minting replaces unused codes; GET /status + DELETE /link serve the ⚙ Advanced → Telegram panel; t.me/<bot>?start=<code> deep link when OMNIMIND_TELEGRAM_BOT_USERNAME is set). Webhook POST /v1/channels/telegram/webhook is secret-verified (X-Telegram-Bot-Api-Secret-Token vs OMNIMIND_TELEGRAM_WEBHOOK_SECRET; a token without a secret accepts nothing), deduped by update_id, and always answers valid updates fast — missions are queued, never run in the request. Linked chats: plain text (or /mission) creates a mission through the normal contract path (classifier-resolved task type, default classification, live_web NEVER silently enabled; viewer-role links are refused, matching web guards), /status reports the latest, /arena runs a blind duel delivered as Side A / Side B messages with [🏆 This one wins] buttons, /schedules lists the user's schedules. Votes go through arena.cast_vote (never reimplemented); callback payloads naming another tenant's duel/mission fail politely. Results are PUSHED by an outbox tick in the worker drain + main loop (mirroring the schedules tick): the summary carries [🧾 Proof Pack] [⚔️ New Duel] buttons; the proof-pack button replies with the computed label summary and sends the pack Markdown via sendDocument from the artifact store. All bot output is plain text (persona output is untrusted — no parse_mode anywhere) and chunked at paragraph boundaries under Telegram's 4096 cap. Channel disabled (no OMNIMIND_TELEGRAM_BOT_TOKEN) = clean 503 TELEGRAM_NOT_CONFIGURED on all four routes, boot and everything else untouched. Store: telegram_links / telegram_codes / telegram_updates / telegram_outbox / telegram_duels JSON-blob tables. Suite: **190 passed, 3 skipped** (171 + 19 new, zero regressions).

- v15: **the differentiation trio — Proof Pack, Agent Arena, Scheduled Missions**. (1) **Proof Pack** (`app/proofpack.py`): GET /v1/missions/{id}/proof-pack assembles the completed mission's evidence record — brief, selected candidate, per-candidate evaluation dimensions, critic findings with severity, every cited source with provenance — with per-claim confidence labels COMPUTED from the recorded evidence using the evaluator's own citation checks (verified = citation passes + no high-severity critique open + uncontested; assumed = no source attached; estimated = everything between, incl. SIMULATED-ESTIMATE-derived briefs). Rendered to Markdown and stored through the artifact store (kinds proof_pack / proof_pack_md); chat shows a 🧾 Proof Pack action beside 🌍 Simulate on completed missions. (2) **Agent Arena** (`app/arena.py`): POST /v1/arena/duels runs two persona agents on one prompt through the same adapter path missions use (`Orchestrator._invoke_research`; `_with_persona` now honours BOTH rosters' briefs), presented blind (Side A/B, identities and titles hidden until the vote, side assignment randomised); POST .../vote reveals and writes arena_wins/arena_losses into the same agent_stats ledger (GET /v1/agents/stats moves), GET /v1/arena/leaderboard ranks (≥3 duels = ranked group, then win rate) merged with the mission record. Payloads state plainly that arena outputs are single-persona drafts, NOT evidence-checked mission results. Quarantined/retired/persona-less agents are ineligible; an agent cannot duel itself; duels and votes are tenant-scoped. (3) **Scheduled Missions** (`app/schedules.py`): CRUD at /v1/schedules with cadences interval_hours / daily / weekly (UTC); the durable-queue worker ticks the scheduler from its drain + main loop — NO external cron. A firing creates an ordinary mission through the normal contract path (task type resolved at creation exactly as the endpoint does; live_web and data_classification preserved, so the classification gate applies unchanged) and enqueues its mission_run job. Catch-up rule: at most one firing per tick however many occurrences were missed; next_run_at advances to the first occurrence after now. Tests drive an injectable clock — nothing sleeps. Suite: **171 passed, 3 skipped** (148 + 23 new, zero regressions).

- v14: **the whole internet's subagents, integrated**. A second roster beside agency-agents: the subagents mega-library (9,198 agent definition files harvested from 65 public collections) imports via POST /v1/agents/import-library (`app/library.py`, mirroring the agency importer). One tolerant parser per source format — Claude Code .md frontmatter, Codex .toml, OpenCode .md (no name; filename rules), OpenClaw persona directories (SOUL.md + companions = ONE persona), frontmatter-less persona markdown, Kiro .json; SKILL.md files are skills, never imported. Byte-identical briefs dedupe to one agent (first source keeps provenance); on the real library: **6,476 agents** imported ACTIVE (1,571 duplicates dropped, 1,101 companion files folded, 50 files skipped), 1,678 of them research.web-capable by the recorded keyword curation rule. Declared priors are honestly lower than the agency roster (quality 3.5 / reliability 0.85, unmeasured — the v13 ledger earns real scores from here). The 72 MB library is deliberately NOT vendored into app/data; resolution is OMNIMIND_SUBAGENT_LIBRARY → bundled app/data/subagents-library → ~/workspace/subagents-library/files. Live proof (fresh SQLite boot): import 14.0s, suitability over 6,479 agents 0.15s, and a research mission completed with 3 of 5 panel seats — and 3 candidate authorships — held by library personas.
