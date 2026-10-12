# AgentQon v21.0.0 — release notes (DRAFT, publish after npm approval)

Status: v21.0.0 is STAGED on npm under the package name **omnimind-ai**,
awaiting Zohaib's passkey approval in Staged Publishing. Do not publish
this GitHub release until that approval lands and `latest` shows 21.0.0.

## Headline

OmniMind is now **AgentQon**. The command is `agentqon` — the npm package
name stays `omnimind-ai`, and `omnimind` remains a working alias, so
existing installs update in place:

```sh
npm install -g omnimind-ai@latest
agentqon
```

On first run, an existing `~/.omnimind` home migrates itself to
`~/.agentqon` (config, keys and data copy over; the old directory is
never deleted).

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
  setup exits 0, legacy-home migration drill passed, payload 0
  plaintext / 9,429 .enc, /health reports product "AgentQon".

## Still owed after this release goes live

- PUBLISHING.md refresh in the main project.
