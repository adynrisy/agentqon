# Command reference

## Lifecycle

| Command | What it does |
|---|---|
| `omnimind` | Set up (first run), start the local server, open the UI, and enter the chat REPL |
| `omnimind setup` | Install or refresh the bundled platform in `~/.omnimind` |
| `omnimind start` | Start the local server in the background |
| `omnimind stop` | Stop the local server |
| `omnimind status` | Server state, port, model connection |
| `omnimind serve` | Run the local server in the foreground (for scripts/services) |
| `omnimind open` | Open the web UI in your browser |
| `omnimind logs` | Tail the server log |
| `omnimind doctor` | Styled health checklist: runtimes, libraries, roster, model |
| `omnimind update` | How to update to the latest release |

## Missions

| Command | What it does |
|---|---|
| `omnimind run <mission…>` | Run one mission, streaming the phases live: Planning → Panel → Researching → Drafting → Critic → Evaluating |
| `omnimind missions [--limit N]` | Recent missions and their outcomes |
| `omnimind proof <mission-id>` | Show evidence labels for a mission and save its Proof Pack as `proof-<id>.md` |
| `omnimind simulate <mission-id>` | Run a strategy mission's options through a deterministic simulation world (results labelled SIMULATED ESTIMATE) |

## Agents

| Command | What it does |
|---|---|
| `omnimind agents [--top N] [--type T]` | The roster, ranked by suitability (optionally for one task type) |
| `omnimind stats` | The performance ledger: per-agent records from evaluations and your picks, plus the arena leaderboard |
| `omnimind arena <task…>` | A blind duel between two agents — you pick the winner |

## Schedules

| Command | What it does |
|---|---|
| `omnimind schedules` | List recurring missions |
| `omnimind schedules add` | Create a scheduled mission |

## Hosted mode

By default the CLI talks to your local server. To work against a hosted
OmniMind deployment instead:

| Command | What it does |
|---|---|
| `omnimind login [--server URL]` | Point the CLI at a hosted OmniMind |
| `omnimind whoami` | Which server and identity you're using |
| `omnimind logout` | Forget the hosted login |
| `omnimind use-local` | Switch back to the local server |

Your CLI token is stored in `~/.omnimind/config.json` with owner-only
permissions and is never printed.

## In the chat REPL

Running plain `omnimind` drops you into the chat. Type any mission in
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
| `/exit` | Leave the chat (the server keeps running; `omnimind stop` stops it) |

## Output and accessibility

The harness's styling (gradient banner, panels, tables, score bars,
spinners) honours `NO_COLOR` and automatically falls back to plain text
when output is piped or the terminal doesn't support it.
