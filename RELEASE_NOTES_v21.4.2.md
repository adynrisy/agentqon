# AgentQon v21.4.2 — release notes (drafted 2026-10-13)

Status: READY TO PUBLISH — agentqon@21.4.2 confirmed live on npm
2026-10-12T23:32:09Z (npm publication email; shasum
91bc13959a3ef311b1b241d8e17736dcdde1b8cd). This release folds the nine
fixes from Zohaib's live Windows session of 2026-10-12 together with
every finding from the full harness audit ordered the same day (every
function exercised end-to-end with real model calls routed through
OmniRouter, plus failure injection and the every-machine platform
matrix). Every audit bug below was reproduced live before it was fixed.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### From the live session (the nine)
- Honest model state: a new tenant-aware model-state endpoint feeds
  the header chip and `agentqon doctor`, so both stop reporting "no
  model connected" for BYOK / workspace connections that are Active.
- The agent roster is saved on the user's device (browser local
  database): it downloads once, browsing/filtering is instant and
  offline-fast, and it only re-downloads on a version check when the
  roster actually changed.
- UI performance overhaul: chat actions no longer trigger full
  refreshes, server responses are gzip-compressed, and pollers pause
  when the tab is not visible.
- Chat failures now stay visible in the thread with the real reason,
  instead of a banner that auto-dismisses after ~6 seconds.
- The chatbox Privacy dropdown is replaced by a model picker ("Auto"
  by default, or pin any synced model per mission).
- Terminal runs reuse the already-running verified server instead of
  re-decrypting the payload over its locked Windows runtime folder
  (the EPERM failure).
- Provider transient errors (429/500/502/503/504 and timeouts) retry
  automatically with backoff — a single provider 503 no longer kills
  a mission at the researcher step.
- Local installs show the real error detail again; only hosted
  multi-user mode keeps the sanitized INTERNAL_ERROR surface.
- The CLI's "of $1000.00 budget" wording is removed from run output.

### From the full audit
- Pausing then resuming a mission no longer strands it in "running"
  forever; re-planning no longer leaves ghost tasks; finished
  missions no longer leave their tasks dangling open.
- A model answering in its own persona-schema format no longer kills
  code missions with "no deliverable" (was failing 3/3).
- The CLI's silent $10-per-run cap is removed — it contradicted the
  simplification law (user-facing budgets removed entirely).
- The CLI can no longer spawn a duplicate server over a live-but-slow
  one and orphan the first beyond `agentqon stop`'s reach.
- Failed terminal runs now exit non-zero instead of reporting success
  to scripts.
- Dashboard lists no longer freeze at whatever they showed at boot
  (refresh sequencing fixed); the roster count is no longer
  hardcoded.
- Installers now require exactly Python 3.12 (the payload's version)
  instead of accepting 3.11.
- Redaction proof: provider error bodies and API keys never reach
  error surfaces.

## Test evidence (final gates on the staged tarball)
- Backend unit: 278/278.
- Integration: 67/67.
- CLI (node): 30/30.
- UI: 48/48.
- Pack-gate: PASS on the exact staged tarball — 9,446 files, package
  identity and Python 3.12 verified.

## Honest limit
The every-machine checks ran as an automated matrix; a real Windows PC
remains the final proof surface for install behaviour.
