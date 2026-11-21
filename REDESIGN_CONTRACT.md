# AgentQon UI Redesign — Requirement Contract (PerfectTask)
Date: 2026-10-26. Worktree: ~/workspace/agentqon-ui-redesign-wt, branch ui-redesign-2026-10-26, base main 3a39ab5a.
Deliverable: redesigned apps/web/index.html in this worktree ONLY + report at
~/workspace/goals/omnimind-ai-agent-platform-build/files/AGENTQON_UI_REDESIGN_REPORT.md.
Nothing merges, stages, publishes, or deploys without Zohaib's review.

## Mandatory requirements
- M1 FIX-DBLCLICK: double-click on #wbDraftSave creates exactly 1 draft; double-click on
  #wbProjectCreate creates exactly 1 project. Evidence: automated double-click proof
  (Playwright or equivalent) against the redesigned UI, before/after counts.
- M2 NO-FEATURE-LOSS: every existing surface keeps working with same IDs and user-facing
  names: Chat, Missions, Agents, Skills, Simulations, Advanced (connections, model catalog +
  self-test, usage, Telegram/WhatsApp, admin), Traffic Console, Workbench, Memory, Deep,
  combos, palette, notifications. No route/API/backend change except M1 guards.
- M3 DESIGNER-GRADE REDESIGN: a genuine redesign (layout, hierarchy, type scale, spacing
  rhythm, feedback/empty/loading/error states, motion), not a palette swap. Competitor-informed
  principles only — no copied branding, logos, or trademarked look.
- M4 LOCKED WORDING: AgentQon naming, "Controlled Citations", plain-words errors (no raw
  JSON/"undefined"/"TypeError"/"Failed to fetch"/bare HTTP status visible), UI rulings
  2026-10-22 (chatbox only on main panel screen at bottom; landing page first-run only;
  MODEL ON RECORD in place of Exhibit tiles; mission creation only in chatbox Mission tab)
  and nav rule 2026-10-23 (sidebar Chat·Missions·Agents·Simulations·Skills·Advanced;
  CONNECTIONS only in Advanced) all preserved.
- M5 BUILD GREEN: the app boots and serves the redesigned index.html; no JS syntax errors;
  no new console errors on key screens (pre-existing sandbox font-tunnel/503-on-unconfigured
  channel artifacts excepted and named).
- M6 VISUAL EVIDENCE: headless-browser screenshots of key screens (chat, missions, agents,
  skills, simulations, advanced, traffic console) at desktop + mobile width, actually
  inspected for polish/alignment/overflow/hierarchy, defects fixed.
- M7 TESTS: existing UI test suite (tests/unit/test_ui_v*.py + any node UI checks) run
  against the redesigned file; every failure listed with cause, or PASS.
- M8 REPORT: AGENTQON_UI_REDESIGN_REPORT.md with principles + competitor references,
  screen-by-screen changes, errors fixed with evidence, build/test/screenshot evidence,
  and what still needs Zohaib's eyes (live look on his PC).

## Mandatory requirement added 2026-10-26 (Zohaib) — normal-user clarity, top-level
- M9 STRANGER-TEST CLARITY (equal to M3 polish): first-run must feel like opening a chat
  app, not setting up a server. Exactly one obvious thing to do first and one obvious
  place to type. All advanced material (connections, model catalog, self-test, usage,
  Telegram/WhatsApp, admin, traffic console) stays behind the single clearly-labelled
  Advanced area, out of sight until wanted. Plain everyday words everywhere — no
  unexplained technical terms facing the user. Every screen must pass a "stranger test":
  someone who has never seen AgentQon can tell in seconds what the screen is for and
  what to do next, or the screen gets simplified.
- M9 tension, resolved explicitly (do NOT silently break locked rulings): the locked
  2026-10-22 rulings stay (nav order, chatbox only on panel, MODEL ON RECORD, plain-words
  errors, per-model WORKING/NOT WORKING stamps + "X of Y models working" summary). Where a
  locked technical stamp must stay, it gets a plain-words explanation beside it; purely
  decorative docket-style numbering (e.g. "01 ·", "02 ·" on Advanced fold headers) may be
  dropped visually since it carries no identity or test meaning — verify no test asserts
  it before dropping. No feature dropped, renamed confusingly, or hidden behind extra
  clicks without reason (M2 still binds); "hidden behind Advanced" means folded/out of
  the first-run path, NOT removed.
- V5 STRANGER WALKTHROUGH (verification, added): click through the key flows as a
  first-time user (land → connect → chat → mission → advanced) and record every moment
  of confusion found and fixed, screen by screen, in the report.

## Mandatory requirement added 2026-10-26 (Zohaib) — side activity bar
- M10 ACTIVITY BAR (live, Muse-style, AgentQon's own): a side activity surface showing
  LIVE what the harness is doing — the current task in plain words — and, when the user
  clicks a task, WHAT FILES it is working on (files read, written, or edited, with paths
  and what happened to each). Proper activity surface, not a static log: updates as the
  run progresses. Past tasks collapse into tidy history; file lists readable, not raw
  dumps. Inside the M9 non-confusing rule: current activity prominent and plain-worded.
- M10 evidence note (lead's own audit, 2026-10-26, before the specialist lane reports):
  the agent-session stream DOES carry per-file detail already — app/agent_loop.py
  ToolCall event payload = {step, call_id, tool, args, needs_approval}, where fs-tool
  args carry `path`; ToolResult = {call_id, tool, ok, summary}. So the bar can show real
  files from the existing stream with NO invented paths — but the bar must show PATH ONLY,
  never fs_write content from args. Still to verify (specialist lane): how the UI
  consumes the stream (SSE vs polling), whether mission runs surface the same detail,
  and what ToolResult `summary` contains per tool. If any tool call's file detail does
  NOT reach the browser, that is a recorded backend gap and the smallest supporting
  change in this worktree closes it — never invented data.
- V6 ACTIVITY PROOF (verification, added): drive a real agent session (or the closest
  honest local equivalent), watch the bar update live through Created → SkillInjected →
  Step → ToolCall → ToolResult → Completed, click a task, and verify every listed file
  path matches a real ToolCall arg path; record any gap found.
- M10 CLARIFICATION (Zohaib, 2026-10-26): the activity bar is integrated IN THE APP
  itself as a working everyday surface — wired to the live event stream in index.html,
  not a mockup, not a separate page. V6 is the proof: it must update during a real run.

## Mandatory requirement added 2026-10-26 (Zohaib) — connection delete bug
- M11 CONN DELETE: connections must ACTUALLY be gone after the user deletes one — from
  EVERY surface (connections list, model picker, combos) after a refresh AND after a
  restart, not just hidden for a moment. First-look hypothesis (NOT the diagnosis):
  backend DELETE /v1/connections/{id} soft-deletes (deleted_at + secret shredded),
  list queries filter deleted_at IS NULL, UI connDelete calls DELETE with confirm:true
  then reloads — so on paper it should work; the real failure is in the refresh path,
  a stale cache/catalog, a silently failing call, or a surface that keeps showing the
  deleted connection. Reproduce properly before fixing: create → delete through the UI
  → check every surface → refresh → restart → check again.
- V7 DELETE REGRESSION (verification, added): delete → list no longer contains it →
  picker no longer offers it, asserted by a regression check that runs in the suite
  (or, if the suite cannot cover the UI path, by an automated browser proof + an API
  regression test — record which). Fix the actual cause; never mask it by hiding rows
  in the UI only.
## Mandatory requirement added 2026-10-26 (Zohaib) — optional multi-model field
- M12 MULTI-MODEL (optional): wherever models are chosen (chat model choice AND mission
  creation), offer an optional field to type or pick several specific models as a
  comma-separated list (example "model-a, model-b"). Genuinely optional — empty keeps
  today's default (Auto / single picker) completely unchanged. Each named model validated
  against the models actually available on the user's connections; an unknown name gets
  a plain-words error naming exactly which entry was not found, never a raw error. The
  chosen models must ACTUALLY be used for that run, not merely displayed. Non-confusing:
  plain-worded label + short example, placed as a secondary/advanced option so a normal
  user who never touches it sees no extra complexity.
- M12 code-level lead (lead's own read, 2026-10-26 — specialist lane to ground fully):
  chat choice today = hidden select #chatModel + #chatModelSearch + #chatModelList;
  chat send passes chatModelPin() as chatBody.model, and the server honors plain model_ids
  AND combo: pins (index.html ~1745). Mission creation today pins at most ONE model
  (contract.routing_mode='pinned_model', pinned_model_id, ~3865). So the open design
  question is what "used" means per surface (chat: one call takes one model — fan-out?
  panel? ; mission: existing combo/panel multi-model mechanisms to reuse vs a new list
  field the backend must honor). The specialist lane must answer from the backend code
  before implementation; never ship a field that only displays.
- V8 MULTI-MODEL PROOF (verification, added): empty field = today's behaviour unchanged;
  valid list = run demonstrably uses those models (evidence from the run's route/record);
  unknown entry = plain-words error naming the entry, nothing sent.

## Mandatory requirement added 2026-10-26 (Zohaib) — Advanced settings on every add flow
- M13 CONN ADVANCED SETTINGS: EVERY connection add flow (preset providers AND custom
  address alike) must present an "Advanced settings" (or "Custom settings") section —
  collapsed by default so normal users are not burdened, but always present and
  discoverable. It exposes the per-connection settings the backend already supports
  where applicable (custom base URL/address, custom headers, any other per-connection
  options). Settings must actually save with that connection and show on its card
  afterwards (masked secrets, as today). No connection type may be addable only with
  basic fields and no path to its advanced settings.
- M13 code-level lead (lead's own read, 2026-10-26 — specialist lane to verify):
  Advanced form (connAddBtn) already sends kind/label/base_url/api_key/provider_key/
  headers/allowed_models/blocked_models, with an "Advanced headers" <details> (collapsed)
  — but the section is named headers-only and allowed/blocked sit outside it.
  First-run API-key flow (ccConnectBtn) sends headers via its own "Advanced headers"
  <details> but hardcodes allowed_models/blocked_models to [] — no way to set them.
  Autodetect flow creates local_endpoint connections with NO settings step at all, and
  there is NO edit-connection UI anywhere (only Rotate key) — so an autodetected
  connection can never gain headers/allowed/blocked via the UI afterwards. Card render
  shows headers_masked keys + base_url + masked key, but NOT allowed/blocked lists.
  Preset selection (applyPreset) fills the address, forces kind direct_provider on the
  Advanced form, and opens the headers fold when the preset carries headers — that
  discoverability is the pattern to extend, not a gap.
- V9 ADVANCED SETTINGS PROOF (verification, added): for EACH add flow, add with advanced
  settings set → reload → card shows them (secrets masked) → restart → still shown.
  A flow with no settings section at creation fails V9 even if the connection itself saves.

## Mandatory requirement added 2026-10-26 (Zohaib) — connection delete bug (continued)
- M11 code-level lead (lead's own read, 2026-10-26 — NOT yet reproduced, lane to prove):
  store.list_connections DOES filter deleted_at IS NULL (app/store.py ~1861), so the
  connections list itself should hide a deleted connection — but store.list_catalog
  (~1943) has NO deleted-connection filter: catalog rows survive the delete, so
  GET /v1/models (which feeds the model picker AND the catalog view) keeps offering
  the deleted connection's models. Prime suspect for "not actually deleted" as the user
  experiences it. Combos referencing the connection_id are a second surface to check.
  The fix, if reproduction confirms it, belongs at the catalog/delete boundary (purge
  or filter catalog rows for deleted connections), not in UI hiding.

## Optional
- O1 Light theme polish if the app already supports a theme switch (verify first; do not
  invent a second theme system if only dark exists — perfect the one it has).
- O2 Broader double-submit guards on other mutating buttons (combo save, connection add,
  agent add) where provably safe and behaviour-preserving.

## Fences
- Never touch ~/workspace/omnimind-ai (main), ~/workspace/agentqon-fix-retest2-wt, or any
  running retest process. No npm publish, no git push, no deploy. No KrawFreeShell/ToolMere.
- Never weaken a test to force a pass; never re-change UI wording to satisfy a stale test
  without reporting it as a break (M7).

## Verification (chosen before execution)
- V1: Playwright double-click proof script, counts asserted.
- V2: UI pytest files run with the worktree file as the served UI (or file-assertion run
  if the suite reads the tree path — record which).
- V3: Boot worktree server on a scratch port + scratch data dir, screenshot key screens,
  inspect each image.
- V4: Node-free JS syntax check via python/js parse or headless browser load with zero
  pageerrors (browser load is the gate; node --check is useless for ESM, and this file is
  inline script — browser load covers it).

## Workflow restart 2026-10-26 (Zohaib's standing order: separate-copies-then-merge)
- Implementation RESTARTED under copies-then-merge; the earlier direct-edit join was
  NOT forced. Merge contract (frozen): ~/workspace/agentqon-ui-redesign-proposals/MERGE_CONTRACT.md.
- Conversion was clean except one item, handled honestly: the first backend lane had
  already edited the shared worktree (app/connections.py, app/main.py, app/store.py +
  one new test) before being stopped. Its diff was saved verbatim to
  ~/workspace/agentqon-ui-redesign-patches/backend-partial.patch (+ test file copy),
  the merge target was reverted to clean base 3a39ab5a, and the new backend lane
  starts by applying + verifying that patch — nothing lost, nothing half-applied.
  index.html itself was NEVER edited in the merge target, so the UI join restarts clean.
- Lanes (own worktrees off 3a39ab5a, non-overlapping regions, patch handovers):
  CSS (style block only) · ACTIVITY (aq-act-* + topbar toggle) · FEATURES
  (connection forms + multi-model fields + card template + copy) · BACKEND
  (app/ + tests/ only: conn-delete cleanup, headers save fix, multi-model chat backend).
- Merge pass (lead, sequential): apply backend → css → activity → features; cross-check
  every new ID/class against callers; FULL re-verification on the merged tree (boot,
  UI suite, double-click proof, stranger walkthrough, desktop + mobile screenshots)
  before any completion claim. A clean merge alone proves nothing.

## Status (pre-restart)
- M1 PLANNED, M2 PLANNED, M3 IN_PROGRESS (design agents fanned out), M4 PLANNED,
  M5 PLANNED, M6 PLANNED, M7 PLANNED, M8 PLANNED.
- 2026-10-26 ~18:05 PKT: competitor-research lane COMPLETE
  (~/workspace/agentqon-ui-redesign-proposals/competitor-research.md).
  Top principles banked for consolidation: content-width discipline + composer as hero;
  one primary action per screen; every state designed (empty/loading/error/success);
  palette as primary navigation, context-ranked, shortcut-teaching; progressive disclosure;
  status as words first, colour never alone; compact uniform list density + airy reading
  surfaces; optimistic feedback with honest rollback; type-led hierarchy (narrow weight
  range, frozen 4-5 size scale, mono only for metadata); hairlines + tonal steps, one
  accent rarely used. Greyscale-first tie-break rule adopted: if hierarchy collapses
  without colour, the treatment is rejected. No branding copied (exclusion list §8).
- Still pending: design-system, layout/UX, components-a11y, error-audit, verify-harness lanes.
