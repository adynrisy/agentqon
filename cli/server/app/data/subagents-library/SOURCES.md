# Subagents Mega-Library — Index

Built 2026-10-10 by bulk-extracting the individual subagent definition files (.md/.toml/.json) from the top public collections in the Master Catalog — the agents themselves, not just the collection list.

## Totals

- **Total files collected: 9198**
- **Unique by content (sha256): 8032**
- **Unique by agent name: 4595** (frontmatter/TOML/JSON `name` where present; OpenClaw companion files take their persona-directory name; OpenCode files take the filename, per that schema)
- Sources: 65 (64 cloned repos + msitarzewski/agency-agents copied from local)

## Per-source counts

| Source | Files |
|---|---:|
| will-assistant__openclaw-agents | 1085 |
| varun86__massive-agent-repo-1003 | 1000 |
| TravisLeeeeee__awesome-openclaw-personas | 431 |
| davepoon__claude-code-subagents-collection | 415 |
| gtmagents__gtm-agents | 412 |
| affaan-m__everything-claude-code | 404 |
| alirezarezvani__claude-skills | 320 |
| msitarzewski__agency-agents | 281 |
| Koroqe__claude-code-sdlc | 259 |
| H-mmer__pentest-agents | 236 |
| rjmurillo__ai-agents | 226 |
| rohitg00__awesome-claude-code-toolkit | 220 |
| mergisi__awesome-openclaw-agents | 208 |
| GammaLabTechnologies__harmonist | 206 |
| wshobson__agents | 205 |
| microsoft__hve-core | 179 |
| VoltAgent__awesome-codex-subagents | 175 |
| avelikiy__great_cto | 168 |
| VoltAgent__awesome-claude-code-subagents | 165 |
| m4br4__claude-code-subagents | 165 |
| rshah515__claude-code-subagents | 165 |
| HermeticOrmus__LibreUIUX-Claude-Code | 163 |
| risadams__claude-subagent | 160 |
| vibeeval__vibecosystem | 140 |
| 0xfurai__claude-code-subagents | 138 |
| ankitmundada__awesome-opencode-subagents | 128 |
| Donchitos__Claude-Code-Game-Studios | 100 |
| lodetomasi__agents-claude-code | 100 |
| borghei__Claude-Skills | 94 |
| Cletrics__finops-agents | 91 |
| JosephHampton__awesome-gemini-cli-subagents | 82 |
| NicholasSpisak__claude-code-subagents | 77 |
| aldow3n-a11y__prompt-library | 61 |
| TheJacksonCode__Agent-Architecture | 60 |
| 0xSteph__pentest-ai-agents | 53 |
| stretchcloud__claude-code-unified-agents | 53 |
| softspark__ai-toolkit | 49 |
| LerianStudio__ring | 43 |
| anthropics__claude-plugins-official | 43 |
| noumenon-ai__agent-personas | 43 |
| augmnt__agents | 40 |
| dsifry__metaswarm | 39 |
| oliver-kriska__claude-elixir-phoenix | 39 |
| proflead__codex-agents-library | 39 |
| lst97__claude-code-sub-agents | 37 |
| dl-ezo__claude-code-sub-agents | 36 |
| aiagentskit__claude-agents-library | 34 |
| vijaythecoder__awesome-claude-agents | 33 |
| contains-studio__agents | 32 |
| vanzan01__claude-code-sub-agent-collective | 32 |
| iamdemetris__lude-kit | 30 |
| Toskysun__sub-agents | 28 |
| nth5693__gemini-kit | 28 |
| Omar-Obando__qwen-orchestrator | 24 |
| shenhao-stu__openclaw-agents | 24 |
| AgriciDaniel__claude-seo | 23 |
| cft0808__edict | 18 |
| NYCU-Chung__my-claude-devteam | 12 |
| pamirtuna__gamestudio-subagents | 12 |
| feiskyer__claude-code-settings | 9 |
| davila7__claude_subagents | 7 |
| aws-samples__sample-claude-code-agent-team | 6 |
| momozi1996__awesome-ai-persona-skills | 5 |
| zubair-trabzada__ai-marketing-claude | 5 |
| agent-sh__agentsys | 3 |
| **TOTAL** | **9198** |

## Top 30 most-duplicated agent names (across sources)

| Agent name | Files | Sources |
|---|---:|---:|
| code-reviewer | 59 | 25 |
| security-auditor | 35 | 19 |
| debugger | 23 | 18 |
| devops-engineer | 17 | 16 |
| ai-engineer | 17 | 16 |
| ml-engineer | 18 | 15 |
| incident-responder | 17 | 15 |
| performance-engineer | 19 | 14 |
| mobile-developer | 16 | 14 |
| project-manager | 16 | 14 |
| data-engineer | 15 | 14 |
| data-scientist | 15 | 14 |
| frontend-developer | 17 | 13 |
| cloud-architect | 17 | 13 |
| business-analyst | 14 | 13 |
| prompt-engineer | 14 | 13 |
| mlops-engineer | 14 | 13 |
| game-developer | 14 | 13 |
| product-manager | 13 | 13 |
| architect | 39 | 12 |
| seo-specialist | 14 | 12 |
| flutter-expert | 12 | 12 |
| test-automator | 15 | 11 |
| api-documenter | 13 | 11 |
| blockchain-developer | 12 | 11 |
| api-designer | 11 | 11 |
| ui-designer | 11 | 11 |
| planner | 39 | 10 |
| database-optimizer | 14 | 10 |
| performance-optimizer | 14 | 10 |

## Failed clones

- None — all 64 repos cloned successfully.

## Extraction rules (as applied, v2)

1. File under an `agents/`-type path segment (.claude/agents/, .codex/agents/, agents/, subagents/…), ext .md/.toml/.json, excluding README*/INDEX*/CONTRIBUTING*/CHANGELOG*/LICENSE*. A dot-prefixed `.agents/` segment alone is not enough (some repos use it as a project scratchpad — e.g. rjmurillo's 486 archive docs): there the file must also carry agent evidence (name frontmatter / TOML name / companion filename).
2. OpenClaw repos (mergisi, TravisLeeeeee, shenhao-stu, will-assistant, cft0808/edict): SOUL.md / IDENTITY.md / AGENTS.md persona files.
3. Any .md with YAML frontmatter containing `name:` (with or without `description:`), or `description:` + `mode:` (OpenCode schema); any .toml with `name =` + `developer_instructions`/`description =` (Codex schema). Files >200KB skipped for content rules.
- A file named SKILL.md is a skill, not a subagent — excluded everywhere (the naive frontmatter rule swept in 5,213 of them). Frontmatter rules also don't fire under a `skills/` directory.
- Fallback: a repo yielding 0 files contributes all its non-doc .md files (noumenon-ai/agent-personas = frontmatter-less persona markdown).
- Note: OpenClaw packs bundle companion files (SOUL/IDENTITY/AGENTS/USER) per persona, so their file counts run ~3-4× the persona count. Several collections are near-copies of each other (e.g. the VoltAgent category tree is re-shipped by ankitmundada and inside the 1,003 mega-pack), which is what the dedupe numbers quantify.

## Layout

- `files/<owner>__<repo>/<original relative path>` — extracted definitions, original names/paths preserved.
- `_scripts/` — repos.txt, clone_all.sh, extract.py, analyze.py (reproducibility).
- `_clones/` was deleted after extraction to save space.

## v21 addition (2026-10-11) — 500 AI Agents Projects pack

- `ashishpatel26__500-ai-agents-projects/` — 131 agent persona files
  converted for AgentQon from ashishpatel26/500-AI-Agents-Projects
  (MIT, © 2025 ashishpatel26). The upstream repo is a use-case list,
  not 500 agent definitions: 133 real entries were found (112 table
  rows + 21 runnable agents), 2 true duplicates dropped, leaving 131
  unique agents. Each file's frontmatter and Provenance section carry
  the origin; bodies were constructed for AgentQon from each entry's
  own description.
