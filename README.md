<div align="center">

# 🧠 OmniMind AI

**Missions run by rival agents — critic-verified, evidence-labelled.**

OmniMind AI is a complete multi-agent platform that installs in one command.
Give it a mission; a panel of specialist agents competes to solve it, a critic
attacks the drafts, an evaluator scores what survives — and every claim in the
final answer carries its evidence.

[![npm version](https://img.shields.io/npm/v/omnimind-ai.svg)](https://www.npmjs.com/package/omnimind-ai)
[![npm downloads](https://img.shields.io/npm/dm/omnimind-ai.svg)](https://www.npmjs.com/package/omnimind-ai)
[![license](https://img.shields.io/npm/l/omnimind-ai.svg)](https://www.npmjs.com/package/omnimind-ai)
[![node](https://img.shields.io/badge/node-%E2%89%A518-brightgreen.svg)](https://nodejs.org)
[![python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)](https://www.python.org)

[**Install**](#-install) · [**Quick start**](#-quick-start) · [**Guides**](#-guides) · [**Commands**](#-commands) · [**FAQ**](docs/troubleshooting.md)

</div>

---

## What is OmniMind AI?

Most "multi-agent" tools are one model talking to itself. OmniMind is built
around **structured rivalry**:

1. **You state a mission** — a strategy question, research task, piece of
   writing, code job, plan, or question.
2. **A planner** breaks it into a task graph.
3. **A panel of up to 5 agents** — picked from a roster of 280+ specialists by
   measured suitability for *your* task type — drafts competing solutions.
4. **A critic** attacks every draft; weak work is revised or rejected.
5. **An evaluator** scores the survivors, and a **performance ledger** tracks
   every agent's real record across missions and arena duels.
6. **You get one answer with a Proof Pack**: the winning draft, the evidence
   behind every claim, what the critic found, and what it cost.

Everything runs **on your machine**. Your missions, keys and data live in
`~/.omnimind/data` — nothing is sent anywhere except the model *you* connect.

## The no-fake-output guarantee

OmniMind has **no mock mode and no canned answers**. If no model is
connected, a mission refuses to run and tells you exactly how to connect one —
it will never dress up template text as agent work. What you read was written
by a real model, or you read nothing.

Connect a model any of four ways (details in
[Connecting models](docs/connecting-models.md)):

- **Bring your own key** — add an OpenAI-compatible provider in the web UI
- **Environment endpoint** — `OMNIMIND_LLM_BASE_URL` / `OMNIMIND_LLM_API_KEY` / `OMNIMIND_LLM_MODEL`
- **OmniRoute sidecar** — auto-detected on port `20128`
- **Ollama** — auto-detected on port `11434`, fully local

## ✨ Features

- **Terminal harness + web UI in one install** — chat-first interface in the
  terminal and the browser, with the full dashboard one click away
- **280+ agent roster** — strategists, researchers, writers, engineers,
  critics and specialists, ranked per task by live performance data
- **Suitability panels** — up to 5 best-fit agents draft in parallel; no two
  missions get the same panel by default
- **Critic-verified output** — every candidate is attacked before it can win
- **Proof Packs** — exportable evidence trail for any mission
- **Agent Arena** — blind duels: two agents, one task, you pick the winner;
  results feed the leaderboard
- **Scheduled missions** — recurring work that fires on its own
- **Simulation worlds** — run a mission's strategies through a deterministic
  model world before committing to one
- **WhatsApp & Telegram front doors** — run missions from your phone
  (see [Channels](docs/whatsapp-and-telegram.md))
- **Budgets & BYOK vault** — per-tenant encrypted key storage and spend caps

## 📦 Install

**Requirements:** Node.js ≥ 18 and Python ≥ 3.11. (On first run, OmniMind
checks both and tells you exactly what to install if anything is missing.)

```sh
npm install -g omnimind-ai
```

Then:

```sh
omnimind
```

The first run installs the bundled platform into `~/.omnimind`, builds its
private Python environment, starts the local server on
http://127.0.0.1:8000 (or the next free port) and opens your workspace.

Platform-specific walkthroughs: [Installation guide](docs/installation.md).

## 🚀 Quick start

```sh
omnimind                                  # set up, start, open the UI, chat
omnimind run "Compare pricing strategies for a subscription coffee brand"
omnimind agents                           # who's on the roster, suitability-ranked
omnimind arena "Write a launch tweet for a smart lamp"   # blind duel — you judge
omnimind proof <mission-id>               # the evidence behind a result
omnimind doctor                           # health checklist
```

Full walkthrough: [Getting started](docs/getting-started.md).

## ⌨️ Commands

| Command | What it does |
|---|---|
| `omnimind` | Set up (first run), start the local server, open the UI, chat |
| `omnimind run <mission…>` | Run one mission and watch the phases live |
| `omnimind setup` | Install/refresh the bundled platform in `~/.omnimind` |
| `omnimind start` / `stop` / `status` | Manage the local server |
| `omnimind serve` | Run the local server in the foreground |
| `omnimind open` / `logs` | Open the web UI / tail the server log |
| `omnimind agents` | Suitability-ranked agents |
| `omnimind stats` | Performance ledger + arena leaderboard |
| `omnimind missions` | Recent missions |
| `omnimind proof <mission-id>` | Evidence labels + save the proof pack |
| `omnimind arena <task…>` | Blind agent duel — you pick the winner |
| `omnimind schedules` | Recurring missions |
| `omnimind simulate <mission-id>` | Run a mission's strategies in a model world |
| `omnimind login [--server URL]` | Point the CLI at a hosted OmniMind |
| `omnimind logout` / `whoami` / `use-local` | Identity and mode |
| `omnimind doctor` | Styled health checklist |
| `omnimind update` | How to update |

Reference: [Command guide](docs/commands.md).

## 📚 Guides

- [Installation](docs/installation.md) — every platform, updates, uninstall
- [Getting started](docs/getting-started.md) — first mission, end to end
- [Connecting models](docs/connecting-models.md) — BYOK, env, OmniRoute, Ollama
- [Commands](docs/commands.md) — the full CLI and chat reference
- [WhatsApp & Telegram](docs/whatsapp-and-telegram.md) — missions from your phone
- [Troubleshooting & FAQ](docs/troubleshooting.md) — ports, Python, "model not connected"

## 📱 Run missions from your phone

OmniMind can live where you already are. Connect **Telegram** with your own
bot, or link your **WhatsApp Business number with a Facebook login** — then a
message to your number *is* a mission, and the verified result comes back as a
message. Setup and honest limitations (including WhatsApp's 24-hour reply
window) are in the [channels guide](docs/whatsapp-and-telegram.md).

## About this repository

This repository is OmniMind AI's public home — product documentation,
guides and release notes. OmniMind itself is distributed as the
[`omnimind-ai` npm package](https://www.npmjs.com/package/omnimind-ai);
the one-line install above is all you need. Issues and feature requests are
welcome here.

## License

MIT © 2026 Zohaib Khan
