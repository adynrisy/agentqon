<!--
Source: anthropics/knowledge-work-plugins
Path: partner-built/zoom-plugin/skills/debug-zoom/SKILL.md
License: Apache-2.0
URL: https://github.com/anthropics/knowledge-work-plugins/tree/HEAD/partner-built/zoom-plugin/skills/debug-zoom/SKILL.md
-->

# /debug-zoom

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](../../CONNECTORS.md).

Debug Zoom auth, API, webhook, SDK, or MCP issues without wandering through the entire docs set.

## Usage

```text
/debug-zoom $ARGUMENTS
```

## Workflow

1. Identify the failing layer: auth, API request, webhook, SDK init, media/session behavior, or MCP transport.
2. Ask for the minimum missing evidence: exact error, platform, request/response, event payload, or code path.
3. Produce 2-4 plausible causes ranked by likelihood.
4. Route to the most relevant deep references in `skills/`.
5. Give a short verification plan so the user can confirm the fix.

## Output

- Most likely failure layer
- Ranked hypotheses
- Targeted fix steps
- Verification checklist
- Relevant skill links

## Related Skills

- [debug-zoom-integration](../debug-zoom-integration/SKILL.md)
- [setup-zoom-oauth](../setup-zoom-oauth/SKILL.md)
- [design-mcp-workflow](../design-mcp-workflow/SKILL.md)
