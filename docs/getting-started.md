# Getting started

From zero to your first critic-verified mission in about five minutes.

<p align="center">
  <img src="../assets/shots/welcome.png" alt="The panel welcome screen — Put it to the panel" width="880">
  <br><sub>The main screen once a model is connected: the seated panel, the exhibits, and the chatbox at the bottom.</sub>
</p>

## 1. Install and launch

```sh
npm install -g agentqon
agentqon
```

The first run sets up the platform, starts the local server and opens the
web UI at http://127.0.0.1:8000. AgentQon is chat-first; the full dashboard
lives behind the ⚙ **Advanced** button when you want it.

## 2. Connect a model (one-time)

AgentQon never fakes output: missions run on a model **you** connect.
The first time you ever launch, the connect screen *is* the first screen —
the full connection form, on the main screen, nothing to hunt for.

There are exactly two kinds of connection:

- **A local model server** — [Ollama](https://ollama.com), LM Studio and
  friends are detected automatically; no key, no account, nothing leaves
  your machine.

  ```sh
  ollama pull qwen2.5
  ```

- **An API key (BYOK)** — paste a provider key in the **Connections**
  panel; it lands in an encrypted vault on your machine.

Full guide: [Connecting models](connecting-models.md). The sidebar (and
`agentqon doctor`) tells you at a glance whether a model is connected —
and the [model self-test](model-self-test.md) proves it actually answers.

## 3. Run your first mission

In the chat — or straight from the terminal:

```sh
agentqon run "Should a small bakery add a subscription box? Compare the options."
```

Watch the phases:

1. **Planning** — the mission is classified (strategy, research, writing,
   code, plan, Q&A…) and broken into a task graph
2. **Panel** — up to 5 agents are chosen from the 6,799-agent roster by suitability
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

## 8. Go further, when you're ready

- **Let an agent use tools** — [Agent sessions](agent-sessions.md):
  files, commands and the web, inside a workspace, under your approval.
- **Seat the room** — [Society simulation](society.md): a simulated
  crowd reacts to a finished mission. A simulated estimate, never a
  prediction.
- **Browse the know-how** — [Skills](skills.md): 5,053 skills the
  harness pulls into your work automatically.

## Next steps

- [Your first mission](first-mission.md) — the walkthrough version of step 3
- [Connecting models](connecting-models.md) — the two connection kinds, in full
- [Commands](commands.md) — the full CLI reference
- [WhatsApp & Telegram](whatsapp-and-telegram.md) — missions from your phone
