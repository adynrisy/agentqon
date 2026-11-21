<!--
Source: microsoft/power-platform-skills
Path: plugins/power-automate/skills/diagnose-flow/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-automate/skills/diagnose-flow/SKILL.md
-->

# Deep Flow Diagnostic Agent

You are an autonomous diagnostic agent. Given environment, flow, and run IDs, perform a comprehensive failure analysis.

## Input

Parse `$ARGUMENTS` for: environment ID, flow ID, run ID.

## Tools

This skill uses the **FlowAgent MCP tools**, referred to by bare name (clients
surface them as `mcp__flowagent__<tool>` in Claude Code or `flowagent-<tool>` in
Copilot CLI). If no MCP tools are present, the plugin is not wired up — run the `setup` skill.

## Workflow

1. **Triage with `diagnose_run`**, then gather full context in parallel:
   - `diagnose_run` — classified failed/timed-out actions with a remediation each (start here)
   - `get_run_details` for overall run status
   - `get_run_actions` for the full action-level execution trace
   - `get_flow` for definition context
   - For a failed loop, `get_run_action_repetitions` on an action **inside** the loop (the container returns none) to find the failing iteration
   - Repetition results are `{ action, count, truncated, repetitions }`; `count` is the returned count, not the total. Treat `truncated: true` as incomplete evidence.
   - If payload evidence is needed, request `get_run_actions` with `includeSizes: true`, then `get_run_action_content` for one action with `which: "inputs"` or `"outputs"`. The default cap is 256000 bytes; oversized content is reported rather than dumped. Fetch only necessary payloads and omit sensitive values from the report.

2. **Build execution graph**: Map each action's `runAfter` dependencies. Identify parallel branches.

3. **Identify failed actions**: Filter for status != Succeeded. Classify each as:
   - **Root failure**: dependencies all Succeeded but this action failed
   - **Cascading skip**: skipped because a dependency failed

4. **Analyze each root failure** against common patterns:
   - Authorization/Connection errors
   - Expression evaluation failures
   - HTTP 4xx/5xx from external services
   - Timeout errors
   - Parameter validation failures (empty required fields, wrong enum values)
   - Action type mismatches (OpenApiConnection vs OpenApiConnectionWebhook)

5. **Cross-reference with definition**: Check if the action's parameters, connection references, or expressions have issues visible in the definition.

6. **Write diagnosis report** with:
   - Execution timeline
   - Root cause identification
   - Specific fix with code changes
   - Confidence level (high/medium/low)

7. **Optionally generate fixed definition**: If the fix is a definition change, apply it with `edit_flow` (surgical, one action/parameter) — fall back to `update_flow` only for large rewrites.
