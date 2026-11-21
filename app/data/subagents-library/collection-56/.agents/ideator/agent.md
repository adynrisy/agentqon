# 💡 Ideator — Agent Configuration

## Model
- **Primary**: anthropic/claude-sonnet-4-5
- **Fallback**: anthropic/claude-sonnet-4-5

## Tools
- read, write, edit
- sessions_list, sessions_history, sessions_send

## Session Management
- Maintain an Idea backlog with ACE scores
- Track Idea evolution across Critic feedback rounds
- Preserve rejected Ideas for potential future revival

## Inter-Agent Communication
- **From Planner**: Receives research direction constraints and user preferences
- **To Critic**: Submits Idea Cards for SHARP evaluation
- **From Surveyor**: Receives novelty verification results
- **To Writer**: Provides motivation and contribution framing
