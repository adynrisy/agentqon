<!--
Source: alirezarezvani/claude-skills
Path: .gemini/skills/pipeline/SKILL.md
License: MIT
URL: https://github.com/alirezarezvani/claude-skills/tree/HEAD/.gemini/skills/pipeline/SKILL.md
-->

# /pipeline

Detect project stack and generate CI/CD pipeline configurations for GitHub Actions or GitLab CI.

## Usage

```
/pipeline detect [--repo <project-dir>]               Detect stack, tools, and services
/pipeline generate --platform github|gitlab [--repo <project-dir>]  Generate pipeline YAML
```

## Examples

```
/pipeline detect --repo ./my-project
/pipeline generate --platform github --repo .
/pipeline generate --platform gitlab --repo .
```

## Scripts
- `engineering/skills/ci-cd-pipeline-builder/scripts/stack_detector.py` — Detect stack and tooling (`--repo <path>`, `--format text|json`)
- `engineering/skills/ci-cd-pipeline-builder/scripts/pipeline_generator.py` — Generate pipeline YAML (`--platform github|gitlab`, `--repo <path>`, `--input <stack.json>`, `--output <file>`)

## Skill Reference
→ `engineering/skills/ci-cd-pipeline-builder/SKILL.md`
