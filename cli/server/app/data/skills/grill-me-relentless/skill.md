<!--
Source: sickn33/agentic-awesome-skills
Path: plugins/agentic-awesome-skills-claude/skills/grill-me/SKILL.md
License: MIT
URL: https://github.com/sickn33/agentic-awesome-skills/tree/HEAD/plugins/agentic-awesome-skills-claude/skills/grill-me/SKILL.md
-->

## When to Use

Use when this workflow matches the user request: A relentless interview to sharpen a plan or design.


_Source: [mattpocock/skills](https://github.com/mattpocock/skills) (MIT)._

Use the locally available `grilling` skill to interview the user about the plan. If the host does not expose slash commands, follow its question-by-question procedure directly. Establish the intended outcome, challenge assumptions and dependencies, and stop when the user-defined decision criteria are resolved or the user ends the interview. Do not claim `/grilling` executed merely because it was printed.

## Example

**User request:**

> Use @grill-me for this task: A relentless interview to sharpen a plan or design.

## Limitations

- Requires the upstream tool, account, API key, or local setup when the workflow names one.
- Does not authorize destructive, production, paid, or external-message actions without explicit user approval.
- Validate generated artifacts or recommendations against the user's real sources before treating them as final.
