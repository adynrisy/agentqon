"""AgentQon — agent suitability tracking (v13).

Zohaib's direction: not a fixed pair of agents — the whole roster is
active, and AgentQon tracks which agents are suitable for which task.

Suitability is a transparent score per (agent, task_type):

    prior    = 0.55 * capability/division fit + 0.45 * declared quality
    measured = 0.8 * (mean evaluation / 5) + 0.2 * selection rate
    score    = prior blended toward measured as runs accumulate
               (weight = runs / (runs + 3))

Every mission records its panel's outcomes (evaluation means, high
critiques, user selections) into a per-tenant performance ledger
(`agent_stats`), so the ranking is earned, auditable, and improves with
use. Nothing here is a black box: the API returns the components.
"""
from __future__ import annotations

PANEL_MAX = 5
PANEL_MIN = 2

# Division affinities for the agency roster (agent_id agency.<div>.<slug>).
# 1.0 = this division exists for this kind of task; lower = can help.
DIVISION_AFFINITY: dict[str, dict[str, float]] = {
    "strategy":          {"strategy": 1.0, "research": 0.7, "plan": 0.7, "general": 0.6},
    "research":          {"research": 1.0, "qa": 0.9, "strategy": 0.7, "general": 0.6},
    "academic":          {"research": 0.95, "qa": 0.95, "writing": 0.7, "general": 0.6},
    "marketing":         {"strategy": 0.9, "writing": 0.85, "research": 0.6, "general": 0.6},
    "paid-media":        {"strategy": 0.85, "writing": 0.7, "research": 0.55, "general": 0.5},
    "sales":             {"strategy": 0.85, "writing": 0.75, "general": 0.55},
    "product":           {"plan": 0.9, "strategy": 0.85, "research": 0.65, "general": 0.6},
    "project-management": {"plan": 1.0, "general": 0.65, "strategy": 0.6},
    "engineering":       {"code": 1.0, "general": 0.6, "research": 0.5},
    "game-development":  {"code": 0.95, "general": 0.55, "writing": 0.5},
    "security":          {"code": 0.9, "research": 0.6, "general": 0.5},
    "spatial-computing": {"code": 0.85, "general": 0.5},
    "design":            {"writing": 0.75, "code": 0.55, "general": 0.65, "strategy": 0.5},
    "finance":           {"research": 0.8, "strategy": 0.75, "qa": 0.65, "general": 0.55},
    "support":           {"qa": 0.8, "writing": 0.7, "general": 0.6},
    "healthcare":        {"research": 0.65, "qa": 0.6, "general": 0.5},
    "gis":               {"research": 0.65, "code": 0.55, "general": 0.5},
    "specialized":       {"general": 0.6, "research": 0.55, "qa": 0.55},
}

CORE_AFFINITY = {"strategy": 0.9, "research": 0.85, "writing": 0.8,
                 "code": 0.8, "plan": 0.8, "qa": 0.85, "general": 0.85}
DEFAULT_FIT = 0.45


def capability_fit(agent: dict, task_type: str) -> float | None:
    """None = not a drafting agent at all (e.g. the critic)."""
    caps = set(agent.get("capabilities", []))
    if "research.web" not in caps and "persona.task" not in caps:
        return None
    agent_id = agent.get("agent_id", "")
    if agent_id.startswith("agency."):
        division = agent_id.split(".")[1] if agent_id.count(".") >= 2 else ""
        return DIVISION_AFFINITY.get(division, {}).get(task_type, DEFAULT_FIT)
    if agent_id.startswith("custom."):
        # Workspace-authored agents (v21.2): the author declares the
        # task types the agent is FOR — full fit there, a generalist
        # floor elsewhere, so a custom agent competes on its declared
        # turf and stays eligible (never first) off it.
        declared = agent.get("task_types") or []
        if task_type in declared:
            return 1.0
        if task_type == "general":
            return 0.6
        return DEFAULT_FIT
    if agent_id.startswith("agent.research."):
        return CORE_AFFINITY.get(task_type, 0.8)
    return 0.7 if "research.web" in caps else DEFAULT_FIT


def suitability(agent: dict, task_type: str, stat: dict | None) -> dict | None:
    fit = capability_fit(agent, task_type)
    if fit is None:
        return None
    prior = 0.55 * fit + 0.45 * (float(agent.get("quality", 0)) / 5.0)
    runs = int((stat or {}).get("runs", 0))
    mean_eval = ((stat or {}).get("eval_sum", 0.0) / runs) if runs else None
    selections = int((stat or {}).get("selections", 0))
    if runs:
        measured = 0.8 * (mean_eval / 5.0) + 0.2 * (selections / runs)
        w = runs / (runs + 3)
        score = (1 - w) * prior + w * measured
    else:
        measured = None
        score = prior
    return {
        "agent_id": agent.get("agent_id"),
        "agent_version_id": f"{agent.get('agent_id')}@{agent.get('version')}",
        "task_type": task_type,
        "score": round(score, 4),
        "fit": fit,
        "prior": round(prior, 4),
        "measured_eval": (round(mean_eval, 2) if mean_eval is not None else None),
        "runs": runs,
        "selections": selections,
        "critiques_high": int((stat or {}).get("critiques_high", 0)),
    }


def rank_panel(agents: list[dict], task_type: str,
               stats: list[dict], k: int = PANEL_MAX) -> list[dict]:
    """The best-suited ACTIVE drafting agents for this task type,
    best first. Each entry carries its suitability breakdown."""
    by_key = {f"{s['agent_version_id']}|{s['task_type']}": s for s in stats}
    scored = []
    for a in agents:
        if a.get("lifecycle") != "active":
            continue
        avid = f"{a.get('agent_id')}@{a.get('version')}"
        s = suitability(a, task_type, by_key.get(f"{avid}|{task_type}"))
        if s is None:
            continue
        s["manifest"] = a
        scored.append(s)
    scored.sort(key=lambda x: (-x["score"], -float(x["manifest"].get("quality", 0)),
                               x["agent_id"]))
    return scored[:k]


def empty_stat(agent_version_id: str, task_type: str) -> dict:
    # v15: arena_wins / arena_losses join the ledger (Agent Arena duels).
    # Records written before v15 lack the keys — readers use .get(..., 0).
    return {"agent_version_id": agent_version_id, "task_type": task_type,
            "runs": 0, "eval_sum": 0.0, "selections": 0, "critiques_high": 0,
            "arena_wins": 0, "arena_losses": 0}
