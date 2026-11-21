<!--
Source: anthropics/knowledge-work-plugins
Path: partner-built/zoom-plugin/skills/debug-zoom-integration/SKILL.md
License: Apache-2.0
URL: https://github.com/anthropics/knowledge-work-plugins/tree/HEAD/partner-built/zoom-plugin/skills/debug-zoom-integration/SKILL.md
-->

# Debug Zoom Integration

Use this skill when the user already built something and it is failing.

## Triage Order

1. Auth and app configuration
2. Request construction or event verification
3. SDK initialization or platform mismatch
4. Media/session behavior
5. MCP transport and capability assumptions

## Evidence To Request

- Exact error text
- Platform and SDK/runtime
- Relevant request or payload sample
- What worked versus what failed
- Whether the issue is reproducible or intermittent

## Reference Routing

- [oauth](../oauth/SKILL.md)
- [rest-api](../rest-api/SKILL.md)
- [webhooks](../webhooks/SKILL.md)
- [meeting-sdk](../meeting-sdk/SKILL.md)
- [video-sdk](../video-sdk/SKILL.md)
- [rtms](../rtms/SKILL.md)
- [zoom-mcp](../zoom-mcp/SKILL.md)

## Output

- Most likely failing layer
- Ranked hypotheses
- Short fix plan
- Verification steps
