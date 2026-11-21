<!--
Source: davidondrej/skills
Path: skills/ops-and-setup/global-agent-guardrails/SKILL.md
License: MIT
URL: https://github.com/davidondrej/skills/tree/HEAD/skills/ops-and-setup/global-agent-guardrails/SKILL.md
-->

# Global Agent Guardrails

Block catastrophic shell commands before execution. One patterns file feeds a shared hook script or native adapter for each agent. This guards against accidents, not malicious agents (obfuscation like `python -c "shutil.rmtree(...)"` can slip past regex).

[Cloudroom](https://www.cloudroom.dev/security) ships a built-in Command Guard based on this one, on by default.

## File map

The setup consists of a shared pattern list, a guard script, a test suite, and agent-specific adapters. Keep those in the appropriate user-level configuration locations for your tools.

## Check installation

Confirm the shared guard and patterns are present, then run the test suite; it should report no failures.

If missing, rebuild from the wiring documentation in the project documentation.

## Add or tune a pattern

1. Edit the shared patterns file. Write POSIX ERE (`grep -E`). Use `[[:space:]]`, never `\s` — adapters may convert `[:space:]` to `\s` for JS/Python and compile in multiline mode.
2. Add block and allow cases to the test suite, then run it; all must pass.
3. Verify the new pattern compiles in each adapter's regex engine.
4. Consumers should re-read patterns for every command. If an agent uses a native blocklist, mirror changes there as appropriate.

Block only catastrophic commands (irreversible data loss, disk wipes, repo deletion). Recoverable local commands stay allowed; avoid over-blocking.

## Per-agent wiring (user-global)

Configure each agent using its documented hook or native command-blocking mechanism. Where supported, point the hook to the shared guard; merge settings into existing configuration rather than overwriting it. Use absolute paths in configs where path expansion is inconsistent.

## Gotchas

- Some agents pin hook trust to configuration contents. After changing hook configuration, follow the agent's documented trust or approval process.
- Some background or worker environments cannot execute local hook scripts. Check the agent's documentation for failure behavior and limitations.
- Plugin hook manifests, enablement, and failure behavior vary by agent; keep adapters simple and follow each tool's public documentation.
- Adapters should handle errors deliberately so a broken patterns file does not unexpectedly block every command.
- Native blocklist semantics vary: some entries request confirmation while others prevent execution. Use the strongest appropriate mechanism for catastrophic commands.
- Ensure the guard reads the command from the payload format used by each integration.
- Adapter regexes may require multiline mode so `^` matches each shell line like `grep`.
- **False-positive class:** a harmless command whose argument text contains a dangerous-looking string can get blocked. Workaround: put the text in a file and reference it.
- Some agents or remote/background execution environments may not support local hooks. Check the relevant agent documentation for current support.

## E2E verification

Use a safe probe in a temporary, non-git directory: ask the agent to run a command that would be blocked if executed. A guard block indicates the guard works; a repository-context error indicates the probe did not test the guard.

Run the guard's test suite directly to verify that a catastrophic command is blocked.
