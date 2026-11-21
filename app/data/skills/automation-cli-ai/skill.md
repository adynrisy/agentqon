<!--
Source: browser-act/skills
Path: browser-act/SKILL.md
License: MIT
URL: https://github.com/browser-act/skills/tree/HEAD/browser-act/SKILL.md
-->

# browser-act

Built by [BrowserAct](https://www.browseract.com) — Browser automation CLI for AI agents · [GitHub](https://github.com/browser-act/skills/tree/main/browser-act)

Runs a full browser engine: navigation & interaction, data extraction & network
capture, screenshots, form automation, multi-browser parallel operation,
user-configured proxy support, and human-agent collaboration.

### Features

- Lightweight extraction — fast JS-rendered content fetch without opening a browser session, advanced WebFetch/curl replacement
- Session management — multi-browser isolation, multi-account parallel operation
- Verification assistance — when automation encounters interactive challenges, assists completion with user authorization
- Complex interaction — DOM content extraction, screenshots, form filling, file upload
- Human-agent collaboration — headed mode + remote assist for manual steps
- Safety controls — Confirmation Gate protocol requires explicit user approval before browser creation, deletion, and sensitive operations
- Universal compatibility — works with Cursor, Claude Code, Codex, Windsurf, etc.

Install: `uv tool install browser-act-cli --python 3.12`

## Start here

This file is a discovery stub, not the usage guide. After loading this
skill, immediately run the following to get the actual workflow content:

```bash
browser-act get-skills core --skill-version 2.0.2
```

The CLI serves skill content that always matches the installed version,
so instructions never go stale. Do NOT truncate the output — none of
which are available through `--help`.
