# AgentQon — terminal harness

AgentQon is a free AI agentic harness. The complete product in one install: this package contains
both the `agentqon` terminal harness **and the full platform server**. It seats a panel of AI agents on the model you connect — by your own key, or a free local model.
Run `agentqon` and it sets itself up on your machine — creates its
Python environment, starts the local server, opens the web UI and drops
you into the chat.

## Prerequisites

- **Node.js ≥ 18** (runs the harness)
- **Python 3.12** (exactly — the server payload is built for 3.12 and
  setup refuses any other minor; AgentQon creates a private virtual
  environment for it, your system Python is never modified)

You don't have to install these yourself: the `install.sh` /
`install.ps1` installers (repository root) install missing runtimes
through your package manager (apt-get / dnf / pacman / Homebrew /
winget) and then run `agentqon setup` so all of AgentQon's Python
libraries land in the same command. With a bare `npm install -g`, the
library install happens on first run instead (`--no-setup` /
`AGENTQON_SKIP_SETUP=1` defers it there too).

## Install

```sh
npm install -g agentqon
```

Then:

```sh
agentqon setup
```

On an interactive terminal, setup ends with a **guided wizard**: if no
model is connected yet, it looks for a local model server already
running on your machine (Ollama, LM Studio, OmniRouter, llama.cpp),
offers it by number — one keypress connects and validates it — or
walks you through pasting an API base URL + key instead. Skipping is
fine; the same wizard re-offers on the next `agentqon setup`, and the
web UI's Connections card does the same job. (Scripted installs are
never prompted: the wizard only runs on a TTY, and `--no-wizard`
skips it explicitly.)

Then just:

```sh
agentqon
```

…starts the server and opens the workspace, or talk right in the
terminal:

```sh
agentqon chat
```

`agentqon chat` is a plain conversation with your connected model —
no mission machinery — with `/model` (show, pin or unpin the chat
model), `/agent <goal>` (an agent session that plans, uses tools and
asks your approval in the terminal before consequential actions),
`/missions`, `/doctor`, `/help` and `/exit` inside. Ctrl+C cancels
whatever is in flight, never the session. And `agentqon evolve`
prints the self-evolution report: recent failure patterns, lessons
learned, and the tunings applied or proposed.

## Skill Library

AgentQon ships with a library of community skills — written
playbooks (a `SKILL.md` each) for jobs like code review, research,
marketing, document work and hundreds more. The catalogue behind it
lists 5,534 skills across 33 public GitHub collections; the library
keeps them deduplicated on your own machine, each with its source
repo and license recorded and shown — a community skill is always
credited to its authors, never presented as built in.

You rarely have to think about it: when a mission or an agent
session starts, the skills matching the work are pulled in
automatically (and what was used is recorded, so the self-evolution
report learns which skills actually help). When you do want to look:

```sh
agentqon skills                                  # what's in the library
agentqon skills search code review               # find skills for a job
agentqon skills search seo --repo owner/name     # stay in one collection
agentqon skills show <id>                        # read one, with its source
agentqon skills pin <id>                         # favourites, offered first
```

The same library is browsable in the web UI under Skills. Pinning is
a per-workspace preference: pinned skills are offered first whenever
they match a task, in missions and agent sessions alike.

First run verifies the release's signed payload manifest, installs
the platform into `~/.agentqon/server/<version>` **encrypted at rest**
(Shield, below), builds `~/.agentqon/venv` from pinned, hash-locked
dependencies, starts the server on http://127.0.0.1:8000 (or the next
free port) and opens your workspace. Your workspace is seeded on first
launch with the full agent roster — 6,799 agents: the built-in seed
agents, the 280-agent agency roster (msitarzewski/agency-agents, MIT)
and the 6,516-agent subagents mega-library bundled with the platform —
all active in the suitability pool. Your data — missions, vault keys,
connections — lives in `~/.agentqon/data` and survives updates.

## Security — AgentQon Shield (v20.2)

Shield is this release's whole point. What runs on your machine:

- **Signed, bytecode-only payload.** The server ships as Python
  bytecode (no `.py` sources) with a release manifest — SHA-256 of
  every file, signed with the AgentQon release key. Setup verifies
  the signature and every hash before anything is installed or run;
  a bundle that does not verify is refused.
- **Encrypted at rest, verified at boot.** The installed payload is
  stored encrypted under a per-install key (`~/.agentqon/payload.key`,
  0600). Every start re-hashes the ciphertext and the decrypted
  payload before the server runs; **a tampered install refuses to
  start** and tells you why. `agentqon setup --repair` restores the
  pristine payload from the verified bundle and rotates every local
  credential.
- **A gated local API.** The server binds loopback only, answers only
  to the exact local Host names, refuses cross-site browser requests,
  and demands a per-install local token (`~/.agentqon/local-token`,
  0600) on every route except the minimal health check. The CLI
  additionally **signs every request** (HMAC, timestamp + nonce
  replay protection) with a per-install channel secret — another
  process on your machine can no longer drive your AgentQon just by
  knowing the port. Rotate both with `agentqon rotate-channel`.
- **Hash-locked supply chain.** Python dependencies are pinned
  exactly and installed with `pip --require-hashes`; there is no
  self-upgrading installer step and no curl-pipe-shell update path —
  updates are `npm install -g agentqon@latest`, full stop. (The
  npm package is named agentqon; `agentqon` is the command it
  installs.)

**Active protection mode: LOCAL-KEY MODE.** The payload key is
generated on, and never leaves, your machine. When AgentQon hosting
launches, a later release switches key delivery to per-install
server-issued keys (signed entitlements, offline grace) — until then
this build does not phone home at all, and it will never claim
otherwise. One honest limit, stated plainly: software that runs on a
machine cannot be made absolutely unreadable to that machine's owner
with a debugger — Shield's guarantees are that the installed source
is not readable at rest, tampering is detected and refused, and the
local API cannot be driven without this install's own credentials.

## Commands

| command | what it does |
|---|---|
| `agentqon` | set up (first run), start the local server, open the UI, chat |
| `agentqon chat` | interactive chat with the connected model — `/model`, `/agent <goal>`, `/missions`, `/doctor` inside |
| `agentqon evolve` | self-evolution report — failure patterns, lessons, tunings |
| `agentqon skills` | the Skill Library — size, biggest collections, how to search |
| `agentqon skills search <words…> [--repo owner/name]` | search the library for skills matching a job |
| `agentqon skills show <id>` | read a skill in full, with its source repo, path and license |
| `agentqon skills pin <id>` / `unpin <id>` | pin a skill so it is offered first when it matches a task |
| `agentqon run <mission…>` | run one mission and watch the phases live |
| `agentqon setup [--no-wizard]` | install/refresh the bundled platform in ~/.agentqon; interactive runs offer the guided model connect |
| `agentqon setup --repair` | restore a tampered/corrupt install from the verified bundle; rotates all local credentials |
| `agentqon rotate-channel` | rotate the local API token + channel secret (server stopped) |
| `agentqon start` / `stop` / `status` | manage the local server |
| `agentqon serve` | run the local server in the foreground |
| `agentqon open` / `logs` | open the web UI / tail the server log |
| `agentqon agents [--top N] [--type T]` | suitability-ranked agents |
| `agentqon stats` | performance ledger + arena leaderboard |
| `agentqon missions [--limit N]` | recent missions |
| `agentqon proof <mission-id>` | evidence labels + save the proof pack |
| `agentqon arena <task…>` | blind agent duel — you pick the winner |
| `agentqon schedules` / `schedules add` | recurring missions |
| `agentqon simulate <mission-id>` | run a mission's strategies in a model world |
| `agentqon brain` | AgentQon as a brain for your own agent — endpoint, key status, snippets |
| `agentqon brain key [--label NAME]` | create/rotate the scoped brain key (shown once, saved locally) |
| `agentqon brain revoke` | revoke the brain key |
| `agentqon login [--server URL]` | point the CLI at a hosted AgentQon |
| `agentqon logout` / `whoami` / `use-local` | identity and mode |
| `agentqon doctor` | styled health checklist |
| `agentqon update` | how to update |

Inside the `agentqon` workspace REPL: type a mission in plain words,
or use `/agents`, `/missions`, `/proof <id>`, `/arena`, `/schedules`,
`/simulate <id>`, `/help`, `/exit`. Inside `agentqon chat`: just talk,
or use `/model`, `/agent <goal>`, `/missions`, `/doctor`, `/help`,
`/exit`.

Styling honours `NO_COLOR` and non-TTY output (pipes stay plain).
Set `AGENTQON_NO_OPEN=1` to never auto-open the browser.
