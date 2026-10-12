# Command reference

## Lifecycle

| Command | What it does |
|---|---|
| `agentqon` | Set up (first run), start the local server, open the UI, and enter the chat REPL |
| `agentqon setup` | Install or refresh the bundled platform in `~/.agentqon` |
| `agentqon start` | Start the local server in the background |
| `agentqon stop` | Stop the local server |
| `agentqon status` | Server state, port, model connection |
| `agentqon serve` | Run the local server in the foreground (for scripts/services) |
| `agentqon open` | Open the web UI in your browser |
| `agentqon logs` | Tail the server log |
| `agentqon doctor` | Styled health checklist: runtimes, libraries, roster, model |
| `agentqon update` | How to update to the latest release |

## Missions

| Command | What it does |
|---|---|
| `agentqon run <mission…>` | Run one mission, streaming the phases live: Planning → Panel → Researching → Drafting → Critic → Evaluating |
| `agentqon missions [--limit N]` | Recent missions and their outcomes |
| `agentqon proof <mission-id>` | Show evidence labels for a mission and save its Proof Pack as `proof-<id>.md` |
| `agentqon simulate <mission-id>` | Run a strategy mission's options through a deterministic simulation world (results labelled SIMULATED ESTIMATE) |

## Agents

| Command | What it does |
|---|---|
| `agentqon agents [--top N] [--type T]` | The roster, ranked by suitability (optionally for one task type) |
| `agentqon stats` | The performance ledger: per-agent records from evaluations and your picks, plus the arena leaderboard |
| `agentqon arena <task…>` | A blind duel between two agents — you pick the winner |

## Schedules

| Command | What it does |
|---|---|
| `agentqon schedules` | List recurring missions |
| `agentqon schedules add` | Create a scheduled mission |

## Hosted mode

By default the CLI talks to your local server. To work against a hosted
AgentQon deployment instead:

| Command | What it does |
|---|---|
| `agentqon login [--server URL]` | Point the CLI at a hosted AgentQon |
| `agentqon whoami` | Which server and identity you're using |
| `agentqon logout` | Forget the hosted login |
| `agentqon use-local` | Switch back to the local server |

Your CLI token is stored in `~/.agentqon/config.json` with owner-only
permissions and is never printed.

## Brain (for your own external agent)

| Command | What it does |
|---|---|
| `agentqon brain` | Brain endpoint URL, key status, and copy-paste snippets (curl / Python / Node) |
| `agentqon brain key [--label NAME]` | Create or rotate the scoped brain key (shown once, saved to your local config) |
| `agentqon brain revoke` | Revoke the brain key |

The brain key authorises `POST /v1/brain` only — see
[brain.md](brain.md) for the five-minute integration path and the
MCP bridge.

## In the chat REPL

Running plain `agentqon` drops you into the chat. Type any mission in
plain words, or use:

| Slash command | Effect |
|---|---|
| `/agents` | Suitability-ranked roster |
| `/missions` | Recent missions |
| `/proof <id>` | Proof pack for a mission |
| `/arena` | Start a blind duel |
| `/schedules` | Recurring missions |
| `/simulate <id>` | Simulate a mission's strategies |
| `/help` | Command list |
| `/exit` | Leave the chat (the server keeps running; `agentqon stop` stops it) |

## Output and accessibility

The harness's styling (gradient banner, panels, tables, score bars,
spinners) honours `NO_COLOR` and automatically falls back to plain text
when output is piped or the terminal doesn't support it.
