# AgentQon v21.4.3 — release notes (prepared 2026-10-13)

Status: READY TO PUBLISH — agentqon@21.4.3 approved by the owner and
confirmed live on npm 2026-10-13 (registry latest verified). This
release comes from the owner's first live sessions on 21.4.2: the chat
treated every message as a mission, and the composer would not take a
new request while one was running.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### The chat understands conversation now
- A dedicated conversation endpoint (POST /v1/chat) answers normal
  chat with a single call to your connected model — no mission, no
  panel, no research run. Greetings, thanks, small talk, questions
  about the product, and short questions that don't ask for a work
  product are conversation.
- The routing is deliberate about edge cases: "Can you write me a
  poem?" is a mission (a deliverable was requested); "How do I make
  tea?" is a chat. Anything with a production verb — write, research,
  compare, build, plan, audit… — or a detailed brief goes to a mission,
  exactly as before.
- Conversation carries its own short history (reset by New chat), and
  with no model connected it says so honestly, inline in the thread.

### The composer never locks
- The old busy-lock is gone entirely. Sending a mission returns
  immediately; the mission runs in its own progress box in the thread
  ("Mission running — ask me anything meanwhile, or start another
  task"), and completion is tracked by polling until the mission
  reaches its final state, rendering the same result — or the same
  persistent failure message — as before.
- You can chat, or start a second mission, while one is running. A
  send attempted while a conversation reply is still generating shows
  a visible note — never silence — and a hung call times out and
  releases the composer on its own.

## Test evidence
- Unit suite: 286 passed (incl. a 21-case classifier suite and
  behavioral tests proving a conversation creates zero missions and
  the composer unlocks at kickoff).
- Integration: 5/5 for the conversation endpoint (BYOK reply, platform
  reply, honest no-model 503, history handling, back-to-back missions).
- CLI (node) suite: 30/30. Pack gate: PASS (9,446 files, identity
  agentqon, payload Python 3.12).
