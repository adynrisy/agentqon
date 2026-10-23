# AgentQon v22.0.0 — release notes (prepared 2026-10-21)

Status: LIVE ON NPM — agentqon@22.0.0 confirmed published
2026-10-21T14:12:35Z by npm's publication email
(shasum 4a5894a67dc803a31ac30972faf0bb45cf37ede5).
This is the "proper harness" release, built and verified under the
PerfectTask discipline: a written requirement contract first,
verification picked before execution, and completion claimed only
where passing evidence exists.
GitHub release creation from these notes is still pending.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### A real agent loop
- Session-based agent work at /v1/agent-sessions, with a JSON tool
  protocol and a native tool-calling fallback for models without it.
- Tools are confined to the workspace: filesystem tools cannot leave
  it, the shell runs argv-only (no shell string injection), and
  actions that need it park for your approval and resume after it.
- Two completion guards: the agent cannot declare "done" over a
  failed last tool result, and builds are verified after they run.
  A session that ends without verification says so honestly
  (verified: false) instead of claiming success.

### Self-evolution — lessons, never self-modification
- Sessions record classified lessons; models that keep failing get
  deprioritized automatically.
- GET /v1/evolve/report shows what was learned and applied, with
  self_modification: "never" — the harness tunes its choices, it
  never rewrites itself.

### Chat-first interface
- A redesigned UI in the shape of a chat app: sidebar, the model
  picker in the top bar, and a first-run connect card that
  auto-detects local model servers.
- Agent mode runs in the chat with inline approve/deny.

### A CLI that sets itself up
- The setup wizard probes Ollama, LM Studio, OmniRouter and
  llama.cpp on your machine.
- `agentqon chat` opens an interactive REPL; `agentqon evolve`
  shows the evolution report from the terminal.

## Test evidence
- Unit suite: 334/334.
- Integration: 177 passed, 3 Postgres skips, across 37 files.
- UI suite: 60/60. CLI (node) suite: 41/41.
- Pack gate: PASS (9,451 files, identity agentqon, payload
  Python 3.12). Clean-room install verified.

## Honest limits
- The live end-to-end sandbox task was NOT achieved in pre-release
  attempts — first a small local model's capability, then CPU
  infrastructure 503s with the 7B coder model. The harness behaved
  correctly throughout: the completion guard ended a session
  verified: false live rather than claiming success, and
  self-evolution was proven live (the report showed an applied
  model deprioritization for the failing coder model).
- v22.1.0 (the Skill Library release) is next and already in build.
