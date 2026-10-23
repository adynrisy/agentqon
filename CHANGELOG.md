# Changelog

The honest version story. Full notes for each release live on the
[releases page](https://github.com/adynrisy/agentqon/releases).

## v22.4.0 — staged, rolling out on approval

- **The world shows its work** — society runs now report live
  progress *inside* a round (round 1 of 5 · 4 of 6 calls answered ·
  still working) on the mission panel and the Simulations page,
  instead of showing nothing until a whole round finished.
- **Honest time estimates** — a run's pace is measured from your own
  finished runs first, then from the model self-test's timing; with
  no evidence at all, the panel says the time is unknown — it depends
  on your model's speed — instead of printing a confident guess.
- **Interruptions keep what they earned** — answers from a partial
  round stay on the record, and the interrupted note says how far
  the run got.

## v22.3.0 — staged, rolling out on approval

- **Stop mission** — a running mission can be stopped from its chat
  card or its detail page; work already done stays on record.
- **Every feature its own page** — the sidebar is Chat · Missions ·
  Agents · Simulations · Skills · Advanced; missions, agents, skills
  and simulations each live on their own page, and connections live
  only in Advanced (no sidebar entry).
- **Retry visibility** — when a model call fails mid-mission, the
  card says "Your model isn't answering — retrying" instead of
  sitting at "Starting…".
- **Plain-first simulation output** — the story leads; the outcome
  hash and the technical record sit behind a "Full record" fold,
  with the SIMULATED ESTIMATE label as prominent as ever.
- **Custom headers + the AgentRouter preset** — connections can
  carry their own headers (validated, stored encrypted, shown only
  masked), including the Claude Code identity headers AgentRouter
  demands; its preset fills address and headers in one pick.
- **Provider presets** — 115 providers drawn from OmniRoute's
  registry: pick the name, the exact address fills in, still
  editable; Custom remains for anything else. 17 registry entries
  are not offered — web chat pages, CLI tools and OAuth doors are
  not API endpoints — each with its reason recorded.
- **Model combos** — named chains of models across connections,
  with glob-pattern mappings and a live-scored auto-combo.
  Failover in missions and chat: a call moves to the next model on
  provider or quota failures — never when the request itself is
  wrong — and every move is recorded in the mission's minutes.
  Twelve routing strategies are fully real; seven are deferred,
  named in the product with their reasons, and refused by the
  editor rather than faked.

## v22.2.0 — staged, rolling out on approval

- **The Extended Library** — the 213 security-research skills cut
  from the npm package in v22.1.3 return as one optional download:
  a single tarball signed with Ed25519 and verified before anything
  installs, offered once at setup with the default answer No, and
  any time with `agentqon skills extended`. It lands only in
  `~/.agentqon/extended-library` and removes cleanly. Counts are
  reported both ways everywhere: **4,840 skills in the box · 5,053
  with the Extended Library**.

## v22.1.3 — live on npm

- **The Verdict Room** — a ground-up interface identity: carbon and
  bone, verdict-lime spent like highlighter ink, findings stamped and
  facts tagged. The chatbox lives on the main (panel) screen only;
  the first-run connect screen is a proper landing page that shows
  exactly once ever.
- **The skill library** — automatic retrieval into chats and
  missions, a Skills browser, and `agentqon skills`.
- **Model self-test** — one click stamps every model on every
  connection WORKING or NOT WORKING, with plain-word reasons.
- **Society simulation** — an optional simulated room of 100 agents
  reacting to a finished mission, drawn as a 2D network graph and
  labelled a simulated estimate, never a prediction.
- **AgentQon's own roster and library** — all 6,799 agents and the
  whole library carry AgentQon's own names, IDs included; existing
  installs keep their pins, history and settings.
- **The exclusion cut** — npm's automated review blocked 22.1.0,
  22.1.1 and 22.1.2 with no reason given. A file-by-file diff traced
  the block to one cluster of offensive-security documentation
  inside the library; v22.1.3 removes exactly that cluster — **213
  skills cut, 4,840 shipped**, agents untouched at 6,799 — and npm
  let it through. The cut skills are not gone: they return in
  v22.2.0 as the optional Extended Library.
- **A full-harness audit and repair pass** — five audit axes,
  everything found fixed and re-proven on a frozen tree before this
  release shipped.

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
