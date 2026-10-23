# Connecting models

AgentQon has **no built-in model and no mock mode**. Work runs on the
model you connect (BYOK or a free local model) — and if none is
connected, work refuses with a plain "no model connected" message
instead of inventing output.

There are exactly **two kinds of connection**. That is the whole
design, on purpose:

## Kind 1 — A local model server

A model running on your own machine. No API key, no account, and with
a fully local server, nothing leaves your computer at all.

AgentQon **auto-detects** the common local servers on its first run —
Ollama (port 11434), LM Studio (1234), llama.cpp (8080), and local
router apps (20128) — and says so on the connect screen. If yours is
already running when you first launch, you may find the model
connected before you've done anything.

To add one by hand: **Connections → Add**, choose *Local model
server*, give its address (for example `http://127.0.0.1:11434`).

Best for: free everyday work, privacy, offline use.

## Kind 2 — An API key (BYOK)

Any hosted provider or gateway that speaks the OpenAI-style API. Paste
your key once; it is stored in AgentQon's **envelope-encrypted vault**
on your machine — never in plain text, and sent nowhere except the
provider you named.

To add one: **Connections → Add**, choose *API key*, name the
provider's address, paste the key, and sync — the connection's models
appear in the catalog, ready to seat.

Best for: the strongest hosted models. Your provider bills you at
their rates; AgentQon charges nothing and adds nothing.

**First run ever:** the connect screen is the landing page — the full
connection form, on the main screen. Once you have connected once, you
land on the panel instead; a saved connection that is down shows as
"Not connected" on the panel, never as a fresh start.

## Which should I choose?

| | Local server | API key (BYOK) |
|---|---|---|
| Cost | Free | Your provider's rates |
| Privacy | Nothing leaves your machine | Prompts go to your provider |
| Strength | Bounded by your hardware | The best hosted models |
| Setup | Install a local server once | Paste a key |

Most people end up with one of each: local for everyday work, a key
for the missions that deserve a stronger model. Connections can be
switched per mission — or left on **Auto**, and pinned per chat when
it matters.

## Provider presets — the address fills itself in

For an API-key connection, the form carries **115 provider
presets**, drawn from OmniRoute's registry. Pick the provider's
name and its exact API address fills itself in — no hunting for the
address, no typo'd door. The filled address stays **editable**, and
**Custom** remains for any address at all.

Seventeen entries from that registry are deliberately **not**
offered, and the reason is recorded for each: web chat pages,
command-line tools and OAuth-only doors are not API endpoints, and
a preset that cannot work is worse than no preset.

## Custom headers — and the AgentRouter preset

Some gateways demand more than a key. A connection can carry its
own **custom headers**, sent with every request on that connection.
Headers are validated like a header block must be, stored in the
same encrypted vault as your key, never written to logs, and shown
in API responses only as names with masked values.

The built-in **AgentRouter** preset is the worked example:
AgentRouter turns away clients that do not present a Claude Code
identity, so a plain connection there reports `auth_failed` even
with a valid key. The preset fills the base address
(https://agentrouter.org/v1) **and** the identity headers it
demands — pick it, paste your key, and the door opens.

## Model combos — one name, a chain of models

A **combo** is a named chain of models — they can live on
different connections — that answers to a single name. Pin
`combo:<name>` (or `combo:auto`) anywhere a model is picked, in
missions and in chat, and calls go through the chain.

- **Failover:** if a call fails for provider-class reasons — the
  provider is unreachable, times out, refuses the key, or rate
  limits — the call moves to the next model in the chain. It never
  moves when the request itself is at fault, and a chain that runs
  out of models fails loudly rather than answering from somewhere
  unlisted.
- **Every move is recorded:** in a mission, each failover is
  written into the minutes as a `ComboFailover` event, and the
  route decision names the combo, the strategy and the targets.
- **Mappings:** a glob pattern over model names (like `claude-*`)
  can route to a combo automatically — priority-ordered, each
  mapping switchable on or off.
- **Auto-combo:** `combo:auto` builds its chain from every
  eligible model on your active connections, scored live — healthy
  first, then value.
- **Strategies:** twelve are fully real — priority, fill-first,
  round-robin, weighted, random, strict-random, least-used,
  cost-optimized, headroom, p2c, lkgp and auto. Seven are
  **deferred, and named as such in the product**: reset-window and
  reset-aware (they need per-provider quota-reset clocks, which
  AgentQon does not know), cache-optimized (needs prompt-cache
  telemetry), context-optimized and context-relay (need per-call
  context accounting), and fusion and pipeline (calling several
  models and merging or chaining their outputs is the *panel's*
  job in AgentQon, not a route's). A deferred strategy cannot be
  saved — the editor refuses it rather than filing a label with no
  behaviour behind it.

## For scripts and servers

The same two kinds can be declared by environment before starting
AgentQon — base URL, key, and model name — which is how containers
and hosted (accounts-mode) deployments are wired. The kinds do not
change; only who fills the form in.

## Checking the connection

- **Web UI:** the sidebar shows the seated model and its state —
  Connected, or Not connected, in plain words.
- **Terminal:** `agentqon doctor` or `agentqon status`.
- **Proof, not claims:** run the [model self-test](model-self-test.md).
  A configured-but-unreachable endpoint counts as **not connected** —
  AgentQon will never pretend a dead endpoint answered.

## If a model call fails

A connected model whose call fails makes the work fail **loudly** —
you see the failure and its reason in plain words, never a silently
substituted template answer. If the model was reached through a
[combo](#model-combos--one-name-a-chain-of-models), the call moves
down the chain first, and the failure surfaces only when every
target has failed. Check the address, the key, and the model name;
run the self-test; then re-run.
