<!--
Source: davidondrej/skills
Path: skills/agent-orchestration/gpt-review/SKILL.md
License: MIT
URL: https://github.com/davidondrej/skills/tree/HEAD/skills/agent-orchestration/gpt-review/SKILL.md
-->

# GPT Review

Always use **GPT-6.1-Sol at Max reasoning** through Codex, orchestrated by [Cloudroom](https://www.cloudroom.dev). No other model or effort, ever.

## Run

Read `/room-cli` and follow its launch checks. Reuse the current environment, including uncommitted work, and use `--parent-self` when coordinating.

Always spawn with exactly these flags:

```bash
--provider codex --model gpt-6.1-sol --reasoning-level max
```

- Before spawning, confirm `room-cli provider models codex --json` lists `gpt-6.1-sol` with `max` in `supportedReasoningEfforts`.
- After spawning, confirm with `room-cli thread show` that the thread runs `gpt-6.1-sol` at `max`.
- If either check fails, stop and report the blocker. Never fall back to another model, effort, or harness.

## Review brief

Give neutral context: scope, paths, intended behavior, and diff/base revision. Ask for a thorough senior-developer review, including related code and tests, without steering toward suspected bugs, solutions, or verdicts. Review only; do not change files. Request a concise plain-English report of serious or critical issues, fixes, and production merge readiness. Distinguish verified findings from uncertainty and identify validation gaps.

## Result

Use `room-cli thread wait` and `room-cli thread output`. Verify this review actually completed; idle status, timeouts, and queued retries are not completion. Report blockers rather than presenting partial output as a finished review.

Return the completed reviewer's full final response verbatim.
