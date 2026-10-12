# AgentQon v21.1.2 — release notes (DRAFT, prepared 2026-10-12)

Status: DRAFT — publish only after agentqon@21.1.2 is confirmed live on npm
(latest = 21.1.2). Covers the cumulative 21.1.x line since the v21.0.0
GitHub release; 21.1.0 and 21.1.1 went live on npm without GitHub releases.

## Install

npm install -g agentqon@latest

Then:

agentqon setup --repair

agentqon

The npm package is now `agentqon` (renamed from `omnimind-ai` in the
21.1.x line, at the maintainer's order). The `omnimind` command remains
available as an alias. Existing data migrates automatically from
~/.omnimind to ~/.agentqon on first run; nothing is deleted.

## What changed in 21.1.x

### Terminal experience (21.1.0)
- Progress bars and live phase indicators for downloads and long phases
  (dependency install, payload encryption, shield verification) — setup
  no longer sits silent.
- Server start is gated: the browser opens only after the server and the
  web page itself are proven ready.
- The web UI has a boot watchdog: a visible loading state from the first
  second, and a recovery panel with a Retry button and the exact
  diagnostic command if boot fails — never a silent blank page.
- Full error-surface pass: every failure ends with what happened plus the
  exact command that fixes it, including the Python-missing case, which
  now ends with the per-platform install line.
- Fixed `agentqon update` pointing at a non-existent package name; it now
  reads `npm install -g agentqon@latest`.

### Windows fixes (21.1.1 + 21.1.2)
- 21.1.1: `uvloop` (pulled in via uvicorn[standard]) has no Windows build
  at all — its installer refuses win32. It is now platform-marked
  `sys_platform != "win32"` in the locked requirements; uvicorn uses the
  built-in asyncio loop on Windows.
- 21.1.2: the installer's library-verification step now understands that
  platform marker — on Windows uvloop is both skipped at install time and
  not demanded by the checker. On Linux it remains installed and checked.

## Known follow-up (not in this release)
- A pointer release for the old `omnimind-ai` npm package, so its update
  message directs existing users to `agentqon`. The old package remains
  live and working in the meantime.
