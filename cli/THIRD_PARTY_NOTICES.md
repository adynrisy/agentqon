# Third-Party Notices

## OmniRoute (optional sidecar dependency)
OmniRoute v3.8.51 — https://github.com/diegosouzapw/OmniRoute
Copyright (c) 2026 diegosouzapw
Licensed under the MIT License. AgentQon integrates with OmniRoute as a
separately running service over its public API; no OmniRoute source code
is vendored into this repository. The full MIT license text ships with
OmniRoute itself (LICENSE file in its distribution).

## Python runtime dependencies
FastAPI, Starlette, Uvicorn, Pydantic, httpx, psycopg, cryptography —
each under its own open-source license (MIT/BSD/Apache-2.0 family);
exact versions are pinned in requirements.txt and license texts ship
with their wheels.

## Local development model
Qwen2.5-1.5B-Instruct (GGUF) via Ollama — used only as a local dev/test
model under its upstream license terms; not redistributed here.

## agency-agents (msitarzewski) — MIT
The agent persona roster bundled under app/data/agency-agents/ (280
agent briefs across 19 divisions, plus divisions.json) is from
https://github.com/msitarzewski/agency-agents (commit 479193d),
MIT-licensed. Imported agents keep their provenance in the registry
(owner/origin fields) and execute only as operator-activated personas.

## Subagents mega-library (65 public collections) — per-source licenses
The second roster (auto-seeded on first run;
POST /v1/agents/import-library re-imports it) is parsed at import
time from the subagents library: 9,198 agent definition files
harvested 2026-10-10 from 65 public collections — VoltAgent, wshobson,
0xfurai, davepoon, ECC, harmonist, the OpenClaw persona packs, and
others; the library's own census ships inside the bundle as
app/data/subagents-library/SOURCES.md. Since v20.1 the library IS
vendored into this distribution under app/data/subagents-library/ so a
fresh install carries the full roster (previously it was read from
the operator's own copy; the OMNIMIND_SUBAGENT_LIBRARY env var still
overrides the bundled copy). Most collections are MIT-licensed; some
are Apache-2.0 and a few carry no stated license — each imported agent
records its source in the registry (`owner` = "<owner>/<repo> (via
subagents-library)"), and redistribution of any individual persona
remains governed by its upstream collection's license.

## 500 AI Agents Projects (ashishpatel26) — MIT
131 persona briefs under app/data/subagents-library/
ashishpatel26__500-ai-agents-projects/ were converted for AgentQon
from the use-case list at https://github.com/ashishpatel26/
500-AI-Agents-Projects (MIT License, Copyright (c) 2025
ashishpatel26). The briefs are original AgentQon constructions built
from each entry's published one-line description; each file credits
its source entry and original project link in its Provenance
section. No upstream code is vendored.

## v21 mission-mode design sources (patterns only — no code vendored)
The mission modes and role packs in app/data/framework-packs/ are
original AgentQon implementations of workflow PATTERNS published by:
- MetaGPT (foundationagents/MetaGPT, MIT) — Software Company SOP
  artifact chain (PRD → design → tasks → code → review) and its
  13-role roster, re-briefed in AgentQon's own words.
- TradingAgents (TauricResearch/TradingAgents, Apache-2.0) —
  Trading Firm mode: analyst panel → bull/bear debate → trader →
  risk trio → portfolio-manager decision; 12-role roster.
- ChatDev (OpenBMB/ChatDev, Apache-2.0) — Build Chain mode: phased
  build chain with JSON-configurable phases; 9-role roster.
- CAMEL (camel-ai/camel, Apache-2.0) — Society mode: workforce /
  role-pair tasking; 7-role roster.
Role names and pipeline shapes are facts about those projects'
designs; every persona brief and all mode code were written for
AgentQon. Nothing from these repositories is copied or bundled.
Mem0 (mem0ai/mem0, Apache-2.0) informed the scoped-fact design of
app/memory.py, implemented natively on SQLite. agent-reach
(Panniantong/agent-reach, MIT) informed the read-only reach tools in
the gateway. GitNexus (abhigyanpatwari/GitNexus) is PolyForm
Noncommercial and is NOT used: app/codegraph.py is an independent
native implementation of the same idea.
