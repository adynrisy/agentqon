# AgentQon v21.1.2 — release notes

Release notes for the 21.1.x line (21.1.0–21.1.2), cumulative since
v21.0.0.

## Install

npm install -g agentqon@latest

Then:

agentqon setup --repair

agentqon

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
- Fixed `agentqon update` so it points at the correct install command:
  `npm install -g agentqon@latest`.

### Windows fixes (21.1.1 + 21.1.2)
- 21.1.1: `uvloop` (pulled in via uvicorn[standard]) has no Windows build
  at all — its installer refuses win32. It is now platform-marked
  `sys_platform != "win32"` in the locked requirements; uvicorn uses the
  built-in asyncio loop on Windows.
- 21.1.2: the installer's library-verification step now understands that
  platform marker — on Windows uvloop is both skipped at install time and
  not demanded by the checker. On Linux it remains installed and checked.
