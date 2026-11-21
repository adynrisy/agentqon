<!--
Source: microsoft/power-platform-skills
Path: plugins/power-automate/skills/telemetry/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-automate/skills/telemetry/SKILL.md
-->

# Power Automate Telemetry Control

Enable, disable, or check the status of pseudonymous usage telemetry for the
`power-automate` plugin.

## What is collected

Telemetry is **pseudonymous**. It records operational fields — skill name,
plugin version, OS/Node versions, agent name/version, Dataverse organization
and Entra tenant IDs, and the signed-in user's Entra object ID when PAC exposes
it. It never includes file paths, prompts, tool inputs, flow names, URLs,
credentials, usernames, hostnames, or error messages. Plugin hook events have a
local diagnostic log, one file per session, under
`~/.power-platform-skills/telemetry/power-automate/sessions/<sessionId>/events.jsonl`
(nothing is transmitted when telemetry is OFF). Session logs older than 14 days
are pruned automatically. This hook log is separate from FlowAgent CLI/MCP
telemetry, whose local mirror is `~/.flowagent/telemetry.jsonl` by default
(`FLOWAGENT_TELEMETRY_LOG` overrides the path) and is not automatically pruned.
The status command below reports the plugin hook's setting and session logs;
it also prints the core log's path and whether the file exists, not whether a
particular CLI/MCP call is eligible for remote collection.

## Workflow

1. Determine the action from the user's request / `$ARGUMENTS`:
   - `on` — opt in
   - `off` — opt out (nothing transmitted; local log still kept)
   - `status` (default) — report current state

2. Run the bundled config CLI and show its output verbatim:

   ```bash
   node "${PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/scripts/lib/telemetry/lib/telemetry-config.js" --action <on|off|status> --plugin power-automate
   ```

   For `status`, also show the FlowAgent CLI/MCP local log location:

   ```bash
   node -e 'const fs = require("node:fs"); const os = require("node:os"); const path = require("node:path"); const log = process.env.FLOWAGENT_TELEMETRY_LOG || path.join(os.homedir(), ".flowagent", "telemetry.jsonl"); console.log(`FlowAgent CLI/MCP local log: ${log}${fs.existsSync(log) ? "" : " (not created yet)"}`);'
   ```

3. Confirm the result to the user in one sentence.
