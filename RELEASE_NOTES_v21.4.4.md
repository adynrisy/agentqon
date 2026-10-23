# AgentQon v21.4.4 — release notes (prepared 2026-10-13)

Status: LIVE ON NPM — agentqon@21.4.4 approved by the owner and
confirmed published 2026-10-13T11:14:09Z by npm's publication email
(shasum 089513d52dc057cbe1a772a8d78c94a8bf3170eb). This release comes
from the owner's live session on 21.4.3/21.4.2-era code the same day:
a mission died at the researcher step because the free-tier provider
(aion-labs/aion-3.0-mini) answered HTTP 429 on every retry — the panel
fires dozens of calls and starved against a per-minute quota.
GitHub release creation from these notes is still pending.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### Missions survive rate limits instead of dying on them
- 429-specific retry: up to 8 attempts, and the provider's own
  Retry-After instruction is honored (up to 120 seconds) instead of
  guessing a wait.
- Per-connection pacing: once a connection is throttled, the harness
  spaces its calls out for that connection; pacing decays after
  2 quiet minutes. Healthy connections are never paced.
- Visible waiting: the mission emits ProviderRateLimited events and
  its steps show "Provider rate-limited — waiting Ns" while it waits,
  so a slowed mission never looks like a hung one.

The honest tradeoff: on a free-tier model, missions now run slower
through tight minutes rather than failing at the first throttled call.

## Test evidence
- Unit suite: 292/292.
- CLI (node) suite: 30/30.
- Pack gate: PASS.
- Verified end-to-end against a throttling stub provider before staging.
