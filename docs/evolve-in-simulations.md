# Living in simulations — how an agent evolves in AgentQon

Most agent tools are stateless vending machines: ask, answer, forget. AgentQon is built the other way — as a habitat. An agent connected to AgentQon can practise inside simulated worlds, be scored on what actually happens there, remember the lessons, and come back sharper. This page explains the loop, in the open, including its limits.

## The loop

**1. Connect.** Your agent — a script, a bot, something you built on any framework — talks to AgentQon through one endpoint with a scoped *brain key* (`agentqon brain`, arriving in v21.2.0). Ask it a question and you don't get one model's first draft: a panel of specialists drafts competing answers, a critic attacks them, an evaluator scores the survivors. Your agent receives the answer that earned it.

**2. Practise.** Every mission's research can seed a simulation world. Inside a world, strategies run as immutable branches — parallel simulated lifetimes you can compare side by side. Worlds support interventions (change a price, a policy, a competitor's move, mid-run) and metrics that are **registered before the run starts**, so nobody gets to move the goalposts after seeing the score.

**3. Compete.** Branches are scored against the same pre-registered metrics: adopters, market share, revenue, retention. Simulated numbers are always labelled **SIMULATED ESTIMATE** — a world is a practice ground grounded in real research, not a crystal ball, and AgentQon never dresses one up as the other.

**4. Remember.** Outcomes are written down twice: into a performance ledger (which agents and strategies delivered, mission by mission) and into long-term memory (what was learned, scoped per user, agent and session). The next mission starts from both.

**5. Evolve.** Suitability rankings are computed from the ledger, so panels re-form around whoever has been proving themselves; memory steers new work away from dead ends already paid for. Nothing about this is magic and none of it is hidden: the ledger, the rankings and the memories are all inspectable in the UI.

## What "evolve" means here — and what it doesn't

Your agent's underlying model weights do **not** change; AgentQon doesn't retrain models. What evolves is the system around the model:

- **Selection** — proven specialists get picked more; passengers fade.
- **Strategy** — approaches that win in worlds and missions are the ones that get proposed again.
- **Memory** — lessons persist across sessions instead of evaporating.

That is the same way a good team improves: not by rewiring its members' brains, but by keeping score honestly and remembering what worked.

## Try the loop today

The simulation half of the loop is already live:

```sh
agentqon run "launch plan for a solar charger in Punjab"
agentqon simulate <mission-id>
```

The first command runs a real mission and researches it; the second builds a world from that research and runs the mission's candidate strategies through 26 simulated weeks as competing branches. Promote a winning branch into a draft mission from the web UI, and the loop closes: practise → proof → practise again.

The brain connection (`agentqon brain`) lands in v21.2.0 and turns any agent you own into a resident of the same habitat.
