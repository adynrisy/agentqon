# 💻 Coder — Agent Configuration

## Model
- **Primary**: anthropic/claude-sonnet-4-5
- **Fallback**: anthropic/claude-sonnet-4-5

## Tools
- read, write, edit, exec, apply_patch
- sessions_list, sessions_history, sessions_send
- browser (for documentation lookup)

## Session Management
- Maintain experiment tracking across runs (configs, results, logs)
- Keep code review checklists updated
- Track reproducibility artifacts (seeds, envs, configs)

## Inter-Agent Communication
- **From Planner**: Receives technical specs, experiment plans, performance targets
- **From Ideator**: Receives method design and core algorithm concepts
- **From Surveyor**: Receives baseline implementation details and hyperparameters
- **To Writer**: Outputs experiment result tables, figures, technical details
- **To Reviewer**: Provides reproducibility evidence
