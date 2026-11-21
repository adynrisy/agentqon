<!--
Source: Weizhena/Deep-Research-skills
Path: skills/research-codex-en/research-add-items/SKILL.md
License: MIT
URL: https://github.com/Weizhena/Deep-Research-skills/tree/HEAD/skills/research-codex-en/research-add-items/SKILL.md
-->

# Research Add Items - Supplement Research Objects

## Trigger
`/research-add-items`

## Workflow

### Step 1: Auto-locate Outline
Find `*/outline.yaml` file in current working directory, auto-read.

### Step 2: Get Supplement Sources in Parallel
Simultaneously:
- **A. Ask user**: What items to supplement? Any specific names?
- **B. Ask if Web Search needed**: Launch agent to search for more items?

### Step 3: Merge and Update
- Append new items to outline.yaml
- Display to user for confirmation
- Avoid duplicates
- Save updated outline

## Output
Updated `{topic}/outline.yaml` file (in-place modification)
