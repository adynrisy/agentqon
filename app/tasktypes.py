"""AgentQon — task classification (v12: general-purpose missions).

The platform began as a strategy-comparison harness. Zohaib's direction:
whatever task the user types, the agents take it on. Every mission gets
a `task_type`, resolved once at creation:

- an explicit `task_type` in the contract always wins;
- otherwise a deterministic heuristic classifier reads the objective.

`strategy` keeps the original pipeline exactly (two launch strategies,
simulation worlds). Every other type runs the same harness discipline —
two agents draft, a critic reviews, the rubric scores — but the agents
produce a DELIVERABLE (the draft, the answer, the code, the plan)
instead of launch candidates. Deterministic on purpose: the same
objective always routes the same way, in tests and in production.
"""
from __future__ import annotations

TASK_TYPES = ("strategy", "research", "writing", "code", "plan", "qa",
              "general")

_LABELS = {
    "strategy": "🎯 Strategy decision",
    "research": "🔍 Research report",
    "writing": "✍️ Writing",
    "code": "💻 Code",
    "plan": "🗓️ Plan",
    "qa": "❓ Question",
    "general": "🧩 General task",
}


def task_label(task_type: str | None) -> str:
    return _LABELS.get(task_type or "strategy", _LABELS["general"])


def classify_task(objective: str, requested: str | None = None) -> str:
    if requested in TASK_TYPES:
        return requested
    t = " " + objective.lower().strip() + " "

    # Strategy first: the original pipeline, and the golden set's home.
    # "Strategy" here means any DECISION mission — comparing options to
    # choose between them (pricing models, hiring plans, vendors,
    # policies, roadmaps), not only launch strategy.
    if ("go-to-market" in t or "gtm" in t or "launch strateg" in t
            or ("strateg" in t and any(w in t for w in
                ("compare", "launch", "grow", "pricing", "market entry",
                 "positioning")))):
        return "strategy"

    if any(w in t for w in (
            "write code", "write a function", "write a script", "function ",
            "script that", "program that", "debug", "fix the bug",
            "implement", "python", "javascript", "typescript", "sql query",
            "regex", "algorithm", "api endpoint", "code that", "code to",
            "write a program", "sorting", "parse ")):
        return "code"

    # A comparison/choice with no creation verb is a decision mission.
    if any(w in t for w in ("compare ", "comparison of", " vs ",
                             " versus ", "choose between", "decide between",
                             "which is better")):
        return "strategy"

    if (any(w in t for w in ("write a", "write an", "write the", "draft ",
                             "compose", "rewrite", "poem", "short story",
                             "essay", "cover letter", "write me"))
            or any(w in t for w in (" email", " letter", " caption",
                                    " bio ", "speech"))):
        return "writing"

    if any(w in t for w in (
            "plan ", " plan", "schedule", "itinerary", "routine",
            "study plan", "meal plan", "workout", "timetable",
            "prepare for", "organize a", "organise a")):
        return "plan"

    if any(w in t for w in (
            "research", "investigate", "analyze", "analyse", "analysis of",
            "report on", "survey of", "find out about", "deep dive")):
        return "research"

    stripped = objective.strip()
    if stripped.endswith("?") or stripped.lower().startswith((
            "what", "why", "how", "when", "where", "who", "which",
            "explain", "describe", "tell me", "define")):
        return "qa"

    return "general"
