<!--
Source: davidondrej/skills
Path: skills/agent-orchestration/reset-cursor-acp/SKILL.md
License: MIT
URL: https://github.com/davidondrej/skills/tree/HEAD/skills/agent-orchestration/reset-cursor-acp/SKILL.md
-->

# Reset Cursor ACP in your chat app

For terminal sessions use `cursor-cli`; for general app control use `<app-cli>`.

## How resets work

- The app's ACP integration starts one `cursor-agent acp` subprocess per thread, on demand.
- There is no global Cursor ACP server. Do not create one or keep it alive with a watchdog.
- Rules, skills, and `.cursor/mcp.json` reload when a new subprocess starts.
- Use the app's thread-stop command with the thread ID to release the runtime and stop its subprocess while preserving history. The next message starts a fresh agent.

## Quick reset

Resolve `<reset-script>` relative to this `SKILL.md`. Run:

```bash
<reset-script> <thread-id>      # reset one Cursor thread
<reset-script> --self           # reset the current thread
<reset-script>                  # no thread: orphan cleanup + health check only
<reset-script> --dry-run        # show what would happen
<reset-script> <id> --kill-all  # also kill live agents of OTHER Cursor threads
```

Find Cursor thread IDs using the app's status or thread-list command.

The script stops the selected thread, removes orphaned `cursor-agent acp` processes, reports CLI version/login/update status, and asks for the next message. Other threads stay running unless `--kill-all`.

If sandboxing blocks `ps` with "operation not permitted", rerun the script outside the sandbox.

## Verify

Send one short message to verify a fresh agent responds. If it hangs again, check the causes below before another reset.

## Known causes

- **Old Cursor CLI.** If the health check shows an update, use the app's update command, then reset.
- **Expired login.** Symptom "Failed to initialize session services". Fix: `cursor-agent login`, then reset.
- **Pending permission.** Cursor waits for `session/request_permission`. Check and resolve the thread's pending approval before resetting.
- **Resume failure.** For `session/load` → "Session not found", use a fresh session after stopping the thread rather than retrying the load.
- **Team-level MCP servers** from the Cursor dashboard do not work in ACP mode. Only project or user `.cursor/mcp.json`.
- **Rate limits.** Enable retries using the app's plugin controls.

## Do not

- No launchd KeepAlive, cron, or watchdog for `cursor-agent acp`.
- Never blindly run `pkill -f cursor-agent`; it kills all Cursor threads and the interactive TUI. Use the reset script, which targets orphans by default.
- Do not restart the app for one stuck thread; stop only the affected thread.
- Do not use the app's thread-compaction feature; Cursor does not support it.
