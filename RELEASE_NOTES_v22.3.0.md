# AgentQon v22.3.0 — release notes (prepared 2026-10-24)

Status: LIVE ON NPM (status updated 2026-10-28 — approved by the
owner and published).

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### Stop mission
- A running mission can be **stopped** from its live chat card and
  from the mission detail page — one click, no confirmation,
  matching the society run's existing Stop control. The stop lands
  at the next step boundary, and work already done stays on record.

### Every feature its own page
- The sidebar is now **Chat · Missions · Agents · Simulations ·
  Skills · Advanced**. Missions (list and full detail), the agent
  registry, the skill library and the simulation worlds each live
  on their own page — clicking one no longer drops you into
  Advanced.
- **Connections live only in Advanced** — no sidebar entry, no
  separate page — next to the model catalog and self-test, usage,
  Telegram, WhatsApp and Admin, which stay where they were.

### Retry visibility
- When a running mission's latest event is a failed model call, the
  live card says **"Your model isn't answering — retrying"**
  instead of a bare "Queued" that reads as stuck. Any newer event
  takes the line back — the sentence is shown only while it is
  true.

### Plain-first simulation output
- World output now leads with the plain story: the world, the
  stance split in words, the rounds in plain language, and the
  SIMULATED ESTIMATE label as prominent as ever. The technical
  record — outcome hash, raw metrics, sensitivity, call counts —
  sits behind a collapsed **"Full record"** fold: kept as proof,
  never the first thing read.

### Custom headers + the AgentRouter preset
- Connections can carry a validated map of **custom headers**,
  sent with every request on that connection. Header values are
  treated like API keys: stored in the encrypted data home, never
  logged, and exposed in API responses only as names with masked
  values.
- The built-in **AgentRouter preset** fills the base address
  `https://agentrouter.org/v1` and the Claude Code identity headers
  AgentRouter requires (its door turns away clients that do not
  present them, which is why a plain connection there reported
  `auth_failed` even with a valid key).

### Provider presets — 115 in the picker, 17 honestly not offered
- Drawn from OmniRoute's provider registry: pick a provider name
  and its exact API address fills in. The address stays editable,
  and **Custom** remains for anything else.
- **115 presets** are offered. **17 registry entries are not** —
  web chat pages, command-line tools, OAuth-only doors and
  non-OpenAI shapes are not API endpoints — and the reason for
  each exclusion is recorded in the preset data rather than a
  preset being shipped that cannot work.

### Model combos
- A **combo** is a named chain of models, across connections,
  answered to one name. Pin `combo:<name>` or `combo:auto` in the
  model picker — combos work in **missions and in chat**.
- **Failover is real:** when a call fails for provider-class
  reasons (unreachable, timeout, auth, rate limit), the call moves
  to the next target; it never moves when the request itself is at
  fault. Every move is written to the mission's minutes as a
  `ComboFailover` event.
- **Glob mappings** route model-name patterns to combos,
  priority-ordered, each switchable on or off. The **auto-combo**
  builds its chain from every eligible model on your active
  connections, scored live.
- **12 routing strategies are fully real:** priority, fill-first,
  round-robin, weighted, random, strict-random, least-used,
  cost-optimized, headroom, p2c, lkgp, auto. **7 are deferred —
  and say so:** reset-window and reset-aware (they need
  per-provider quota-reset clocks AgentQon does not know),
  cache-optimized (needs prompt-cache telemetry), context-optimized
  and context-relay (need per-call context accounting), and fusion
  and pipeline (calling several models and merging or chaining
  outputs is the panel's job in AgentQon, not a route's). A
  deferred strategy is named with its reason in the UI and refused
  by validation — it cannot be saved as a label with no behaviour
  behind it.

## Honest limits
- Combo failover and the AgentRouter preset are verified against
  test gateways and fixtures; neither has yet been proven against
  the live providers. The first real AgentRouter connection made
  with the preset is its live test.
- A failover call is estimated at the primary target's rates; the
  answering model's name is recorded truthfully from the response.
