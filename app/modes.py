"""AgentQon — mission modes (v21, Track 2).

The audited frameworks join AgentQon as MODES, not sidecar runtimes:
a mode is a named way of working — a role pack, a phase pipeline and
a deliverable shape — layered on the ordinary mission contract. The
orchestrator still routes every call through the same model router,
budget reservations, critic and evaluator; a mode only decides WHO
works each phase and in WHAT ORDER, and how the phase artifacts are
assembled into the final deliverable.

Modes and their design sources (patterns re-implemented natively;
no upstream code is vendored — see THIRD_PARTY_NOTICES.md):

- software_company — MetaGPT's SOP chain (MIT): PRD → system design
  → task breakdown → implementation → review, staffed by its
  13-role software-company pack.
- trading_firm — TradingAgents (Apache-2.0): four analysts → bull vs
  bear debate → trader → aggressive/conservative/neutral risk trio →
  portfolio-manager decision.
- build_chain — ChatDev (Apache-2.0): a phased build chain whose
  phases are JSON-configurable per mission (contract.mode_config).
- society — CAMEL (Apache-2.0): workforce/role-pair tasking —
  framing by the instructing role, execution by the assistant role,
  challenge, final delivery.

Role packs live under app/data/framework-packs/<pack>/ as persona
files and are registered lazily per tenant on first mode use (ids
pack.<pack>.<role>), so the seeded roster count is unaffected.
"""
from __future__ import annotations

from pathlib import Path

from .library import parse_claude_md

PACK_VERSION = "1.0.0"

_TRADING_KEYWORDS = ("stock", "ticker", "share price", "shares of",
                     "portfolio", "invest in", "investing in", "trade ",
                     "trading", "buy or sell", "earnings report",
                     "equity", "crypto position", "position in")
_SOFTWARE_KEYWORDS = ("build an app", "build a software", "build software",
                      "develop an app", "build a web app", "build a mobile app",
                      "create a web app", "build a platform", "build an api",
                      "software product", "build a product", "develop software")


def suggest_mode(objective: str, task_type: str | None = None) -> str | None:
    """Conservative classifier default: only unambiguous objectives
    earn a mode; everything else keeps the standard pipeline (None)."""
    t = " " + objective.lower() + " "
    if any(k in t for k in _TRADING_KEYWORDS):
        return "trading_firm"
    if any(k in t for k in _SOFTWARE_KEYWORDS):
        return "software_company"
    return None


def _phase(key, title, role, task_type, instruction):
    return {"key": key, "title": title, "role": role,
            "task_type": task_type, "instruction": instruction}


MODES: dict[str, dict] = {
    "software_company": {
        "name": "Software Company",
        "source": "MetaGPT SOP artifact chain (foundationagents/MetaGPT, MIT)",
        "pack": "metagpt",
        "deliverable_title": "Software Company — product build record",
        "phases": [
            _phase("prd", "Product Requirements (PRD)", "product-manager", "plan",
                   "Write the PRD for the objective: users, problem, requirements "
                   "ranked must/should/could, acceptance criteria, explicit non-goals."),
            _phase("design", "System Design", "architect", "plan",
                   "Design the system that satisfies the PRD above: components, "
                   "data flow, interfaces, key technology choices and their trade-offs."),
            _phase("tasks", "Task Breakdown", "project-manager", "plan",
                   "Break the design into an ordered task list with dependencies "
                   "and a done-test per task."),
            _phase("code", "Implementation", "engineer", "code",
                   "Implement the product per the PRD, design and task list: "
                   "produce the working code (or the core of it) as the deliverable."),
            _phase("review", "QA Review", "qa-engineer", "qa",
                   "Review the implementation against the PRD's acceptance "
                   "criteria: test cases, edge cases, verdict with evidence."),
        ],
    },
    "trading_firm": {
        "name": "Trading Firm",
        "source": "TradingAgents debate pipeline (TauricResearch/TradingAgents, Apache-2.0)",
        "pack": "tradingagents",
        "deliverable_title": "Trading Firm — decision record",
        "phases": [
            _phase("fundamentals", "Fundamentals Analysis", "fundamentals-analyst", "research",
                   "Analyse the fundamentals behind the objective and report what the figures imply."),
            _phase("market", "Market Analysis", "market-analyst", "research",
                   "Analyse price action/technicals for the objective: trend, levels, momentum, volatility."),
            _phase("news", "News Analysis", "news-analyst", "research",
                   "Scan the news and macro picture for the objective; rank items by materiality."),
            _phase("sentiment", "Sentiment Analysis", "sentiment-analyst", "research",
                   "Gauge sentiment around the objective: direction, strength, and divergence from evidence."),
            _phase("bull", "Bull Case", "bull-researcher", "writing",
                   "Build the strongest evidence-based case FOR the position, using the analyst reports above; answer the bear's best point pre-emptively."),
            _phase("bear", "Bear Case", "bear-researcher", "writing",
                   "Build the strongest evidence-based case AGAINST the position, using the analyst reports and the bull case above."),
            _phase("trade", "Trade Proposal", "trader", "writing",
                   "Convert the debate into a concrete proposal: direction, sizing logic, invalidation level, review date."),
            _phase("risk_aggressive", "Risk Review — Aggressive", "aggressive-risk-analyst", "qa",
                   "Argue the high-conviction side of the risk review for the proposal above."),
            _phase("risk_conservative", "Risk Review — Conservative", "conservative-risk-analyst", "qa",
                   "Argue the capital-preservation side: worst plausible loss and the sizing that survives being wrong."),
            _phase("risk_neutral", "Risk Review — Neutral", "neutral-risk-analyst", "qa",
                   "Argue the balanced side: measured sizing and staged entry for the proposal above."),
            _phase("decision", "Final Decision", "portfolio-manager", "writing",
                   "Decide on the record: approve, resize or reject the proposal, with the deciding evidence and the condition that reopens the question."),
        ],
    },
    "build_chain": {
        "name": "Build Chain",
        "source": "ChatDev phased build chain (OpenBMB/ChatDev, Apache-2.0)",
        "pack": "chatdev",
        "deliverable_title": "Build Chain — phased build record",
        "configurable_phases": True,
        "phases": [
            _phase("demand", "Demand Analysis", "chief-product-officer", "plan",
                   "Analyse the demand: what exactly is being built, for whom, and what done means per feature."),
            _phase("approach", "Technical Approach", "chief-technology-officer", "plan",
                   "Choose the technical approach (language, architecture, libraries) with reasons and rejected alternatives."),
            _phase("coding", "Coding", "programmer", "code",
                   "Write the software per the demand analysis and approach above."),
            _phase("review", "Code Review", "reviewer", "qa",
                   "Review the code against the acceptance criteria: correctness, completeness, merge verdict."),
            _phase("testing", "Testing", "tester", "qa",
                   "Test the software against scenarios from the requirements; report failures with exact inputs, expected and actual results."),
            _phase("documentation", "Documentation", "chief-technology-officer", "writing",
                   "Write the user-facing documentation for what was built: purpose, setup, usage, known limits."),
        ],
    },
    "society": {
        "name": "Agent Society",
        "source": "CAMEL role-pair / workforce tasking (camel-ai/camel, Apache-2.0)",
        "pack": "camel",
        "deliverable_title": "Agent Society — role-pair task record",
        "phases": [
            _phase("framing", "Task Framing", "ai-user", "plan",
                   "As the instructing role: frame the objective as a sequence of concrete instructions with a success condition for the whole task."),
            _phase("execution", "Execution", "ai-assistant", "general",
                   "As the executing role: carry out the framed instructions and produce the working result."),
            _phase("challenge", "Challenge", "ai-user", "qa",
                   "As the instructing role: judge the execution against the success condition — what is missing, wrong or unverified."),
            _phase("delivery", "Final Delivery", "ai-assistant", "writing",
                   "As the executing role: deliver the final result, answering every challenge point above explicitly."),
        ],
    },
}

MODE_IDS = tuple(MODES)


def get_mode(mode_id: str | None) -> dict | None:
    if not mode_id:
        return None
    base = MODES.get(mode_id)
    if base is None:
        return None
    return {"id": mode_id, **base}


def resolve_phases(mode: dict, contract: dict) -> list[dict]:
    """The phase list for one mission: build_chain missions may carry
    a JSON phase config in contract.mode_config ({'phases': [...]});
    each configured phase must name a role from the mode's pack and a
    valid task type. Invalid entries fall back to the default chain —
    a typo must never silently shrink the pipeline."""
    if not mode.get("configurable_phases"):
        return list(mode["phases"])
    cfg = (contract.get("mode_config") or {}).get("phases")
    if not isinstance(cfg, list) or not cfg:
        return list(mode["phases"])
    from .tasktypes import TASK_TYPES
    valid_roles = pack_roles(mode["pack"])
    phases = []
    for i, raw in enumerate(cfg):
        if not isinstance(raw, dict):
            return list(mode["phases"])
        role = str(raw.get("role", ""))
        task_type = str(raw.get("task_type", "general"))
        if role not in valid_roles or task_type not in TASK_TYPES:
            return list(mode["phases"])
        phases.append(_phase(str(raw.get("key") or f"phase_{i + 1}"),
                             str(raw.get("title") or role),
                             role, task_type,
                             str(raw.get("instruction")
                                 or f"Complete the {role} phase.")))
    return phases or list(mode["phases"])


# ------------------------------------------------------------- role packs
def packs_dir() -> Path:
    return Path(__file__).parent / "data" / "framework-packs"


def pack_roles(pack: str) -> dict[str, Path]:
    d = packs_dir() / pack
    if not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.glob("*.md"))}


def ensure_pack(store, tenant_id: str, pack: str) -> int:
    """Register a framework role pack for one tenant (idempotent
    upserts; personas in the global persona store). Returns how many
    role agents the pack holds for this tenant afterwards."""
    roles = pack_roles(pack)
    existing = {a["agent_id"]: a for a in store.list_agents(tenant_id)}
    count = 0
    for slug, path in roles.items():
        agent_id = f"pack.{pack}.{slug}"
        parsed = parse_claude_md(path)
        if parsed is None:
            continue
        store.put_persona(agent_id, parsed["body"].strip())
        manifest = {
            "agent_id": agent_id, "version": PACK_VERSION,
            "owner": f"framework-pack/{pack}",
            "description": parsed["description"][:400],
            "capabilities": ["persona.task"],
            "permissions_requested": ["artifact.read"],
            "strategy": "persona", "lifecycle": "active",
            "quality": 3.6, "reliability": 0.86, "latency_ms": 950,
            "cost_per_run_usd": 0.30,
            "origin": f"framework-pack:{pack}", "persona_ref": agent_id,
        }
        if agent_id in existing:
            manifest["lifecycle"] = existing[agent_id].get("lifecycle", "active")
        store.upsert_agent(tenant_id, manifest)
        count += 1
    return count


def list_modes() -> list[dict]:
    return [{"id": mid, "name": m["name"], "source": m["source"],
             "pack": m["pack"],
             "deliverable_title": m["deliverable_title"],
             "configurable_phases": bool(m.get("configurable_phases")),
             "phases": [{"key": p["key"], "title": p["title"],
                         "role": p["role"], "task_type": p["task_type"]}
                        for p in m["phases"]]}
            for mid, m in MODES.items()]
