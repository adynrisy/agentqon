# AgentQon v21.0.0 — release notes

## Install

```sh
npm install -g agentqon@latest
agentqon
```

## What's new

- **Mission modes** — Software Company, Trading Firm, Build Chain and
  Society modes, plus framework role packs, run missions the way
  specialist firms are organised instead of one generic panel.
- **Roster: 6,799 agents** (verified from the packaged install) — the
  131-agent use-case pack joins the 280 agency specialists, the 6,385
  library agents and the built-ins.
- **Memory layer** — long-term agent/user memory (Mem0-pattern, native
  SQLite) so panels remember what actually worked.
- **Reach pack** — external-reach channels mapped into the tool gateway,
  so agents can act beyond the local machine under the same budget and
  allowlist rules.
- **Native code graph** — a lightweight SQLite code graph (symbols,
  imports, calls) exposed through gateway tools for per-task codebase
  knowledge.
- **Simulation upgrade** — AgentSociety-pattern worlds: composable
  environment modules, first-class interventions, and pre-registered
  metrics with baseline-vs-intervention branch pairs. Simulated numbers
  stay labelled SIMULATED ESTIMATE.
- **AgentScope patterns** — broadcast panel messaging and richer
  observability traces.
- **Redesigned web UI** — professional app shell (top bar, nav rail,
  status), no chatbot-hero look; chat-first layout with the full
  dashboard under Advanced. All API contracts and the Shield token flow
  unchanged.
- **Windows fix, made permanent** — filenames are sanitized during the
  build itself and the pack gate refuses any Windows-hostile path, so
  the v20.2.1 filename refusal cannot recur.

## Verification (before staging)

- Full test suite: 320 passed; the single failure is the known sandbox
  timing artifact, proven to fail identically on untouched v20 code.
- Pack gate: PASS. Clean-room install from the tarball: both bins run,
  setup exits 0, payload 0 plaintext / 9,429 .enc, /health reports
  product "AgentQon".
