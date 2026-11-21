<!--
Source: microsoft/power-platform-skills
Path: plugins/power-automate/skills/manage-desktop-flows/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-automate/skills/manage-desktop-flows/SKILL.md
-->

# Desktop Flow Manager

Manage Power Automate Desktop (RPA) flows, machine groups, and run sessions.

## Tools

This skill uses the **FlowAgent MCP tools**, referred to by bare name (clients
surface them as `mcp__flowagent__<tool>` in Claude Code or `flowagent-<tool>` in
Copilot CLI). If no MCP tools are present, the plugin is not wired up — run the `setup` skill.

| Tool | Purpose |
|------|---------|
| `list_desktop_flows` | Browse RPA flows (filter by `name`) |
| `run_desktop_flow` | Trigger with optional `machineGroup`, `body`, `timeout` |
| `list_machine_groups` | Browse machine infrastructure |

## Operations

### List Desktop Flows
Call `list_desktop_flows`. Present in table: Name | Status | Created | Modified

### Run Desktop Flow
1. Call `list_desktop_flows` to find the flow (if name given, use `name` filter)
2. Optionally call `list_machine_groups` to target a specific group
3. Call `run_desktop_flow` with optional `machineGroup`, `body` (input data), `timeout`
4. Report: session status (Waiting/InProgress/Failed/Cancelled/Succeeded), outputs

### Machine Infrastructure
Call `list_machine_groups`. Present: Name | Type | Status | Machines
