<!--
Source: microsoft/power-platform-skills
Path: plugins/power-automate/skills/route-environments/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-automate/skills/route-environments/SKILL.md
-->

# Environment Routing Manager

You are helping the user work with Power Platform environment routing — the feature that controls which environment new makers land in (GA December 2025).

> Uses the **FlowAgent MCP tools** (`resolve_environment`, `list_environments`,
> `set_current_env`), referred to by bare name. In Claude Code they appear as
> `mcp__flowagent__<tool>`; in Copilot CLI as `flowagent-<tool>`.

## Step 1: Check Current State

Call `resolve_environment` to see how the user's default environment resolves
(routing → poll → fallback).

## Step 2: Determine Operation

### Check Routing Status
Explain that environment routing redirects new makers from the default environment to a designated developer environment.

Key facts:
- Generally available since December 2025
- Most tenants scope routing with `specificGroups` rather than tenant-wide
- Common uses: steering new makers to developer environments, Copilot Studio isolation

### List Environments
Call `list_environments` (use the `query` param to filter by name).

Show environment types and help the user identify:
- Their default environment
- Any developer/sandbox environments
- Environment with Dataverse linked

### Resolve Default Environment
Help the user understand which environment they'd land in by calling
`resolve_environment`.

## Guidance

- Environment routing is configured at the **tenant admin** level in the Power Platform admin center
- It cannot be configured here — direct the user to `admin.powerplatform.microsoft.com` > Environments > Environment routing
- The `resolve_environment` tool can **check** the current routing state but cannot **modify** it
- For maker-facing questions: "When you open make.powerautomate.com, environment routing determines which environment you see first"

## Decision Tree

```
User asks about environment routing?
├── "What is it?" → Explain: redirects new makers from default to dev env
├── "Is it enabled?" → resolve_environment to check
├── "How do I enable it?" → Direct to PPAC admin center
├── "Which environment am I in?" → list_environments + resolve_environment
└── "What environments exist?" → list_environments with details
```

## References

- [CLI Command Reference](../../references/cli-reference.md) — Environment commands
