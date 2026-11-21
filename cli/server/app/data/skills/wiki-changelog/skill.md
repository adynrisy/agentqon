<!--
Source: sickn33/agentic-awesome-skills
Path: plugins/agentic-awesome-skills-claude/skills/wiki-changelog/SKILL.md
License: MIT
URL: https://github.com/sickn33/agentic-awesome-skills/tree/HEAD/plugins/agentic-awesome-skills-claude/skills/wiki-changelog/SKILL.md
-->

# Wiki Changelog

Generate structured changelogs from git history.

## When to Use
- User asks "what changed recently", "generate a changelog", "summarize commits"
- User wants to understand recent development activity

## Procedure

1. Examine git log (commits, dates, authors, messages)
2. Group by time period: daily (last 7 days), weekly (older)
3. Classify each commit: Features (🆕), Fixes (🐛), Refactoring (🔄), Docs (📝), Config (🔧), Dependencies (📦), Breaking (⚠️)
4. Generate concise user-facing descriptions using project terminology

## Example

**User request:**

> Generate a changelog from the recent git history, grouping user-visible changes and linking each claim to its commit.

## Constraints

- Focus on user-facing changes
- Merge related commits into coherent descriptions
- Use project terminology from README
- Highlight breaking changes prominently with migration notes

### When to Use
This skill is applicable to execute the workflow or actions described in the overview.
