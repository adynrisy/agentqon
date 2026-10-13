# AgentQon v21.4.0 — release notes (FINAL, prepared 2026-10-12)

Status: READY TO PUBLISH — agentqon@21.4.0 confirmed live on npm
2026-10-12T14:25:30Z (npm publication email; shasum
0156fcf138e1e63102100d0be56f6956c2829b9c). This is the hardening release
that closes the full-system audit of 2026-10-12 (five surfaces audited;
suite 343 passed at audit time, with the one failure proven to be a
sandbox queue-timing artifact).

## Install / update

Stop AgentQon first, then:

npm install -g agentqon@latest

agentqon setup --repair

agentqon

## What changed

### Backend — isolation and reliability
- Per-run isolation: concurrent runs can no longer cross model routing or
  share per-run state (the audit's top critical).
- Custom-agent instructions are tenant-scoped — no more leaking across
  accounts.
- Queue claiming is atomic, with real heartbeats and owner fencing; the
  inline runner claims the correct job.
- Vault configuration parsing fixed and now fails loudly instead of
  silently; the kill switch works in the default mode; approval state
  cannot be flipped after the fact; API errors no longer leak tracebacks.

### CLI — the terminal hang is dead
- Fixed the Progress timer leak that hung the terminal after "ready"
  (16/16 new regression tests pass).
- Repair is staged-and-swapped, so it cannot brick an install, and it
  refuses to run while a server is live.
- Python detection now tries every interpreter on the machine.
- Server identity + version check on launch: a stale server left over
  from an older install is restarted, never silently reused. This was
  the root cause of the lingering ::1 loopback error in 21.2.x — the fix
  shipped in 21.2.1, but an old running server kept serving the bug.

### Web UI
- Export downloads work again, including in Shield mode.
- Custom-agent registry capped at 100 rows with debounced filtering.
- An expired session now shows a clear "session expired — relaunch with
  agentqon" banner instead of failing silently.
- Run statuses are honest, and counts corrected to the verified 6,799
  agent roster.

### Release pipeline
- The pack-gate now scans the compiled payload, asserts the package
  identity and the Python 3.12 build, and uses wider secret patterns.
- The package rename is a verified script (11 replacements, gate PASS
  with identity verified for this staging).

## Test evidence
- Backend unit: 240 passed (the one sandbox queue-timing failure is
  environmental and proven as such).
- Integration: 149 passed.
- CLI (node): 16/16.
- UI: 25/25.

## Note on dependencies
Three pins an early audit draft called "dead" (opentelemetry-api, PyYAML,
python-dotenv) are in fact required by FastAPI / uvicorn[standard] under
the hash-locked requirements — proven in a clean venv. They stay.
