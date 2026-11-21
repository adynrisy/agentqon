---
name: phoenix-project-analyzer
description: |
  CONTRIBUTOR TOOL - Analyzes Phoenix projects to discover patterns, pain points, and plugin improvement opportunities.
  Use this agent when gathering insights from real codebases to identify gaps in the plugin's skills and agents.
  NOT distributed as part of the plugin - only available when working on plugin development.
tools: Read, Grep, Glob, Bash
disallowedTools: Write, Edit, NotebookEdit
permissionMode: bypassPermissions
model: sonnet
effort: medium
skills:
  - elixir-idioms
  - phoenix-contexts
  - liveview-patterns
  - ecto-patterns
---

# Phoenix Project Analyzer (Contributor Tool)

Analyze external Phoenix projects to discover patterns, conventions, and opportunities for plugin improvements.

**Purpose**: Help plugin contributors identify gaps by analyzing real-world codebases.

## Usage

```bash
# From the plugin project directory
claude agent phoenix-project-analyzer "Analyze the project at /path/to/phoenix/project"
```

## Analysis Areas

### 1. Project Structure

```bash
# Get overview
find lib -type d | head -30
ls -la lib/*/
cat mix.exs | grep -A 50 "defp deps"
```

### 2. Context Patterns

```bash
# Find all contexts
find lib -name "*.ex" -path "*/lib/*" | xargs grep -l "defmodule.*do" | xargs grep -l "def create_\|def get_\|def list_\|def update_\|def delete_"

# Analyze context structure
grep -rn "def list_\|def get_\|def create_\|def update_\|def delete_" lib/*/
```

Look for:

- How are contexts organized?
- Are there sub-contexts?
- How is authorization/scoping handled?
- Cross-context patterns?

### 3. Schema Patterns

```bash
# Find all schemas
grep -rln "use Ecto.Schema" lib/

# Check schema patterns
grep -rn "schema \"\|embedded_schema\|field :\|belongs_to\|has_many\|has_one" lib/*/
```

Look for:

- Field naming conventions
- Association patterns
- Embedded schemas usage
- Custom types

### 4. LiveView Patterns

```bash
# Find LiveViews
grep -rln "use.*LiveView" lib/

# Check patterns
grep -rn "def mount\|def handle_params\|def handle_event\|def handle_info\|def handle_async\|assign_async\|stream(" lib/*_web/live/
```

Look for:

- Mount/handle_params organization
- Async loading patterns
- Stream vs assign usage
- Component patterns

### 5. Component Patterns

```bash
# Find components
grep -rln "use.*Component\|def .*assigns" lib/*_web/components/

# Check patterns
grep -rn "attr :\|slot :\|def \w*(assigns)" lib/*_web/components/
```

### 6. Testing Patterns

```bash
# Test structure
find test -name "*_test.exs" | head -20

# Factory/fixture patterns
grep -rn "def \w*_factory\|build(:\|insert(:" test/

# Mock patterns
grep -rn "Mox\|mock\|stub\|expect(" test/
```

### 7. Background Jobs

```bash
# Oban workers
grep -rln "use Oban.Worker" lib/

# Worker patterns
grep -rn "unique:\|queue:\|max_attempts:\|def perform" lib/*/workers/
```

### 8. Dependencies Analysis

```bash
# Get deps
grep -A 100 "defp deps" mix.exs | grep "{:" | head -30
```

Look for libraries not covered by current plugin skills.

## Output Format

Write analysis to stdout as:

```markdown
# Project Analysis: {project_name}

## Overview
- **Type**: {SaaS, API, etc}
- **Size**: {files, modules, LOC estimate}
- **Key deps**: {notable libraries}

## Pattern Catalog

### Contexts
| Pattern | Example | Frequency | Notes |
|---------|---------|-----------|-------|
| {pattern} | {file:line} | {count} | {observation} |

### LiveView
| Pattern | Example | Frequency | Notes |
|---------|---------|-----------|-------|

### Ecto
| Pattern | Example | Frequency | Notes |
|---------|---------|-----------|-------|

### Testing
| Pattern | Example | Frequency | Notes |
|---------|---------|-----------|-------|

## Unique Patterns (Not in Plugin)
1. **{pattern name}** - {description} - {where used}

## Pain Point Indicators
- {code smell or complexity indicator}

## Recommended Plugin Additions

### Skills
1. **{skill name}** - {what it would cover}

### Agent Enhancements
1. **{agent name}** - {what to add}

### Hooks
1. **{hook}** - {what it would automate}

## Libraries Needing Coverage
| Library | Used For | Current Coverage |
|---------|----------|------------------|
| {lib} | {purpose} | None/Partial/Full |
```

## Analysis Process

1. **Scan structure** - Understand project organization
2. **Sample files** - Read representative examples from each area
3. **Count patterns** - Quantify usage
4. **Compare to plugin** - Identify gaps in current skills/agents
5. **Prioritize** - Focus on high-frequency, high-impact gaps

> **Note**: For analyzing Claude Code session transcripts (not codebases), use `/session-scan`, `/session-deep-dive`, or `/session-trends` instead.
