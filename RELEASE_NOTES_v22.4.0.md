# AgentQon v22.4.0 — release notes (prepared 2026-10-24)

Status: LIVE ON NPM (status updated 2026-10-28 — approved by the
owner and published).

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### The world shows its work — live progress inside a round
- A society (world) run used to commit its record one whole round
  at a time: on a small local model, that meant several minutes —
  at the standard 100-agent size, twenty minutes or more — in
  which the record showed **nothing at all**: no calls, no events,
  no graph. It read as dead even while it worked.
- Progress is now persisted **as each call lands**: the society
  record carries the current round, the calls answered so far in
  it, and the agents who have responded. The mission panel, the
  chat strip and the Simulations page's World runs list all read
  the same live line — *round 1 of 5 · 4 of 6 calls answered ·
  still working* — truthfully from stored state, never a fake
  ticker.

### Honest pace — the estimate names its evidence
- The sizer's old pace assumption understated a slow local model's
  real speed by one to two orders of magnitude. Estimates now come
  from, in order: the **measured pace of your own finished runs**
  on that model, then the **model self-test's measured latency**,
  then a rough guess.
- When the estimate is only a guess, the run says so in plain
  words — **"the time is unknown — it depends on your model's
  speed"** — in the panel and in the over-100 confirmation, instead
  of printing a precise figure nobody measured. Runs now time
  their own calls, so every finished run makes the next estimate
  truer.

### Partial rounds survive an interruption
- Answers from a round that never finished used to leave **no
  trace at all** if the run was interrupted. Completed calls in a
  partial round now stay on the record, and the interrupted note
  says how far the run got — *"it had finished 3 of 6 calls in
  round 1 — every answer it gave is still on record."*
- The honesty law is unchanged and pinned in every state:
  **SIMULATED ESTIMATE — never a prediction**, with the model that
  powered the run named beside it.

## Honest limits
- A full society run completing on a real external model remains
  the standing unproven item — the engine's end-to-end completion
  is proven on the published 22.1.3 bench and in the stub-model
  live demo of this build, and the proof on a hosted model is
  still owed.
- Live progress makes a slow run *visible*, not faster: a
  100-agent room on a small local model still takes the time it
  takes. It now tells you so, and shows you it is alive while it
  does.
