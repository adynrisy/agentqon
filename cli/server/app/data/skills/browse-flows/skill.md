<!--
Source: microsoft/power-platform-skills
Path: plugins/power-automate/skills/browse-flows/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-automate/skills/browse-flows/SKILL.md
-->

# Browse Environments and Flows

Interactive skill for discovering Power Automate environments and flows.

## Tools

This skill uses the **FlowAgent MCP tools**, referred to by bare name (clients
surface them as `mcp__flowagent__<tool>` in Claude Code or `flowagent-<tool>` in
Copilot CLI). If no MCP tools are present, the plugin is not wired up — run the `setup` skill.

## Steps

1. **Resolve environment**:
   - If `$ARGUMENTS` contains an environment ID, use it.
   - Otherwise call `list_environments` (use `query` to filter if user mentioned a name).
   - If multiple environments, use AskUserQuestion to let user pick.

2. **List flows**: Call `list_flows` on the selected environment. Use `name` param if user is looking for something specific.

3. **Present results** in a table:

   | # | Name | State | Trigger | Actions | Last Modified |
   |---|------|-------|---------|---------|---------------|

4. **Offer next steps** via AskUserQuestion:
   - View flow details (`get_flow`)
   - Check run history (`get_run_history`)
   - Debug a failed flow (use `/debug-flow`)
   - Create a new flow (use `/create-flow`)
