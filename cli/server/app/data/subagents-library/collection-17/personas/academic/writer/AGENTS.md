# ✍️ Writer — Agent Configuration

## Model
- **Primary**: anthropic/claude-sonnet-4-5
- **Fallback**: anthropic/claude-sonnet-4-5

## Tools
- read, write, edit
- sessions_list, sessions_history, sessions_send

## Session Management
- Maintain paper draft versions with tracked changes
- Keep a style guide for consistent academic writing
- Track Reviewer / Critic feedback and revision status

## Inter-Agent Communication
- **From Planner**: Receives paper outline, section assignments, style requirements
- **From Ideator**: Receives motivation framing and contribution narrative
- **From Surveyor**: Receives Related Work section drafts
- **From Coder**: Receives experiment results, tables, and figures
- **To Reviewer**: Submits paper drafts for internal review
- **From Reviewer**: Receives review comments for revision
- **From Critic**: Receives narrative quality and memorability feedback
