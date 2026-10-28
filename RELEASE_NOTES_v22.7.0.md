# AgentQon v22.7.0 — release notes (prepared 2026-10-28)

Status: STAGED ON NPM — built and verified, awaiting the owner's
approval to publish. Until it is approved, `agentqon@latest` remains
v22.6.0.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### Redesign merged into the 22.6.0 UI

- The redesign branch's non-overlapping features were ported into
  the shipped 22.6.0 interface: multi-model chat and mission
  picks, per-connection advanced settings folds, a verified
  connection delete, and the mobile shell fix. The 22.6.0 look
  stays as shipped — Muse-mirror activity panel and composer
  attachments unchanged; the redesign's own palette, panel and
  wording were superseded, not shipped.

### Four v22.6.0 bugs fixed

- Double-click Save draft and double-click Create project no
  longer create duplicates (1s post-success cooldown).
- Deep dry-run no longer returns a 500 on a graph with no start
  node: the walk is iterative, and unrunnable graphs get an
  honest 400 with a plain-words message.
- An orphaned running session after a restart can be re-driven,
  with its transcript preserved; a genuinely live drive is still
  refused, as it should be.
- Gate repairs: the live-drive test contract and null-safe
  detection reads on the merged page.

## Verification

- Final chunked gate on the repaired tree: unit 1631 passed + 1
  pre-existing failure (queue retry, fails on untouched 22.6.0
  too), integration 233 passed + 3 skipped, node 59/59,
  neighbours 168/168.
- Pack-gate PASS on the staged tarball; clean-room install smoke
  PASS (help output verified).
