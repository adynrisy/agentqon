# Changelog

The honest version story. Full notes for each release live on the
[releases page](https://github.com/adynrisy/agentqon/releases).

## Incoming — in staging now

The next release, described in full on the [README](README.md):

- **The Verdict Room** — a ground-up interface identity: carbon and
  bone, verdict-lime spent like highlighter ink, findings stamped and
  facts tagged. The chatbox lives on the main (panel) screen only;
  the first-run connect screen is a proper landing page that shows
  exactly once ever.
- **The skill library** — 5,053 skills with automatic retrieval into
  chats and missions, a Skills browser, and `agentqon skills`.
- **Model self-test** — one click stamps every model on every
  connection WORKING or NOT WORKING, with plain-word reasons.
- **Society simulation** — an optional simulated room of 100 agents
  reacting to a finished mission, drawn as a 2D network graph and
  labelled a simulated estimate, never a prediction.
- **AgentQon's own roster and library** — all 6,799 agents and 5,053
  skills carry AgentQon's own names, IDs included; existing installs
  keep their pins, history and settings.
- **A full-harness audit and repair pass** — five audit axes,
  everything found fixed and re-proven on a frozen tree before this
  release ships.

## v22.0.0 — live on npm

- **Agent sessions** — agents that use tools (files, commands, web)
  inside a workspace you choose, under approvals, with two completion
  guards that check an agent's claims against its own tool evidence.
- **Chat** — a dedicated conversation path: small talk and short
  questions answered in one call; anything asking for a deliverable
  routes to a mission.
- **Self-evolution, lessons only** — the harness records what worked
  and what failed, and reads its own report; it never modifies itself.
- **First-run connect** — local model servers auto-detected
  (Ollama, LM Studio, llama.cpp, local routers); returning users land
  on the panel, with a saved-but-down connection shown honestly as
  Not connected.
- **Shield** — signed, encrypted payload with boot-time integrity;
  per-install token, host allowlist, and a signed CLI↔server channel.

## The 21.x line

- **v21.4.4** — rate-limit survival: missions wait out provider 429s,
  visibly, instead of dying on them.
- **v21.4.3** — conversation endpoint; a composer that never locks
  while missions run.
- **v21.4.2** — truth, speed, and a full audit: honest model state
  everywhere, the roster cached on-device, provider errors retried.
- **v21.4.0–21.4.1** — the hardening release and its Windows
  library-check hotfix.
- **v21.3.0** — the simplification release: exactly two connection
  kinds, budgets removed from the user's face, forms that fold away.
- **v21.2.x** — AgentQon as a brain for your own agent (one scoped
  key, one POST, an MCP bridge), the custom agent registry, and the
  IPv6 loopback parity fix.
- **v21.0.0** — mission modes, the 6,799-agent roster, the memory
  layer, simulation worlds, a redesigned UI.

## The 20.x line

- **v20.2.x** — AgentQon Shield arrives: signed, encrypted payload,
  local API lockdown, hash-locked dependencies; a Windows filename
  hotfix follows within the day.
- **v20.1.0** — the full roster ships in the package; a fresh install
  seeds itself.

## v20.0.0

First public release: terminal harness plus local platform server,
missions run by rival panels, critic-verified, evidence-labelled.
