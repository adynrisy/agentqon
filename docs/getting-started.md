# Getting started

From zero to your first critic-verified mission in about five minutes.

## 1. Install and launch

```sh
npm install -g agentqon
agentqon
```

The first run sets up the platform, starts the local server and opens the
web UI at http://127.0.0.1:8000. You land in a chat — that's deliberate.
AgentQon is chat-first; the full dashboard lives behind the ⚙ **Advanced**
button when you want it.

## 2. Connect a model (one-time)

AgentQon never fakes output: missions run on a model **you** connect.

The easiest local option is [Ollama](https://ollama.com): install it, pull a
model, and AgentQon detects it automatically — no configuration at all.

```sh
ollama pull qwen2.5
```

Or add a provider key in the UI (**Connections** panel), or point AgentQon
at an OpenAI-compatible endpoint. All four routes are covered in
[Connecting models](connecting-models.md). The status chip in the UI (and
`agentqon doctor`) tells you at a glance whether a model is connected.

## 3. Run your first mission

In the chat — or straight from the terminal:

```sh
agentqon run "Should a small bakery add a subscription box? Compare the options."
```

Watch the phases:

1. **Planning** — the mission is classified (strategy, research, writing,
   code, plan, Q&A…) and broken into a task graph
2. **Panel** — up to 5 agents are chosen from the 6,668-agent roster by suitability
   for this task type, weighted by their measured track record
3. **Researching / Drafting** — the panel drafts competing solutions
4. **Critic** — every draft is attacked; weak ones are revised or dropped
5. **Evaluating** — the survivors are scored; the best becomes your answer

The result leads with the outcome. Under it: the panel that ran, the
evaluator's scores, and the run cost as a footnote.

## 4. Read the Proof Pack

Every mission can produce its receipts:

```sh
agentqon proof <mission-id>
```

The Proof Pack shows the winning draft, the evidence label on every claim,
what the critic flagged, and the full scoring trail. It's the difference
between "an AI said so" and "here's why this answer survived".

## 5. Judge a duel in the Arena

```sh
agentqon arena "Write a product description for a solar backpack"
```

Two agents draft blind. You see both answers without names and pick the
winner — your pick updates the agents' performance ledger and the
leaderboard (`agentqon stats`). It's the fastest way to feel how different
the roster's specialists really are.

## 6. Make it recurring

```sh
agentqon schedules add
```

Scheduled missions fire on their own — a weekly competitor scan, a daily
research digest — and land in the same mission history, fully provenanced.

## 7. Test strategies in a simulation world

```sh
agentqon simulate <mission-id>
```

Simulation takes a finished strategy mission and runs its options through a
deterministic model world, so you can compare outcomes before committing.
Simulated numbers are always labelled **SIMULATED ESTIMATE** — AgentQon
never lets a projection masquerade as research.

## Next steps

- [Connecting models](connecting-models.md) — BYOK, endpoints, OmniRoute, Ollama
- [Commands](commands.md) — the full CLI reference
- [WhatsApp & Telegram](whatsapp-and-telegram.md) — missions from your phone
