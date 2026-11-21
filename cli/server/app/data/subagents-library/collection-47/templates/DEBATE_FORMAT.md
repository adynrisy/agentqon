# Multi-Agent Debate Format

How to structure a productive debate between AI agent personas.

## Setup

1. **Pick 2-5 agents** relevant to the decision
2. **Define the question** clearly (e.g., "Should we use Next.js or Astro?")
3. **Set rounds** (usually 2-3 is enough)

## Round Structure

### Round 1: Opening Positions
Each agent states their position using their Pitch Format.
No rebuttals yet — just lay out the case.

```
[Agent A]: Here's my analysis...
[Agent B]: Here's my analysis...
[Agent C]: Here's my analysis...
```

### Round 2: Rebuttals
Each agent responds to the others. Debate Rules kick in here.
Agents should reference specific claims from Round 1.

```
[Agent A]: I disagree with B on... because...
[Agent B]: A's point about... misses...
[Agent C]: Both of you are overlooking...
```

### Round 3: Final Positions
Each agent gives their final recommendation.
They must acknowledge at least one valid point from an opponent.

```
[Agent A]: Considering B's point about..., my final position is...
```

## Observer Round (Optional)
An Observer agent (see meta/observer.md) reviews all rounds and picks a winner.
The Observer should explain WHY, not just WHO.

## Tips

- **Odd number of agents** prevents deadlocks
- **Include a Devil's Advocate** for critical decisions
- **Include a Mediator** if agents have known conflict pairs
- **Time-box rounds** — long debates produce diminishing returns
- **Weakness awareness** — each agent's weakness section tells you when to discount their opinion

## Example: Tech Stack Decision

**Question:** "Next.js + Supabase or Astro + Firebase for our landing page?"

**Agents:**
- Speed Builder (coding/speed_builder.md) — wants to ship fast
- Architect (coding/architect.md) — wants clean foundation
- Growth Hacker (business/growth_hacker.md) — wants conversion optimization
- Observer (meta/observer.md) — picks the winner

**Result:** The debate surfaces trade-offs that a single perspective would miss.
