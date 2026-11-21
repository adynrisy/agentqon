"""AgentQon — Simulation Worlds (Phase 7; textbook Ch.13).

A world is a FROZEN baseline (market assumptions + the mission context
that produced them). Branches are immutable strategy runs against deep
copies of that baseline — they share no mutable state, hold no
credentials, and can never touch the Tool Gateway or any real-world
action. The only exit from a world is `promote`, which creates a new
DRAFT mission through the normal path and logs the event.

The engine is deterministic (seeded), bounded (branches, ticks, market
size), and pure compute — no external calls.

v21 (Track 6) adds the AgentSociety/AgentVerse experiment DESIGN,
implemented natively on this same deterministic core: worlds compose
from typed environment modules with provenance-carrying parameters;
Interventions and pre-registered Metrics are first-class objects and
the standard experiment is a baseline-vs-intervention branch PAIR on
one seed; a rule-based actor engine gives simulated actors state
(static profile + rule-updated working state); steps can be triggered
by events as well as the clock; and every branch run emits an
append-only JSONL trace with a schema header. Every output is a
SIMULATED ESTIMATE: reproducibility metadata (seed, engine version,
baseline hash, outcome hash) travels with it. Non-determinism
disclosure: the numeric engine reproduces exactly for a given seed;
any future LLM-generated narrative layered on top would not, and is
therefore kept out of the numeric path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .domain import event_envelope, new_id

ENGINE_VERSION = "simulation-v2"
RUBRIC_VERSION = "simulation-v1"
MAX_BRANCHES = 6
MAX_TICKS = 52
MAX_MARKET = 1_000_000

STRATEGIES: dict[str, dict] = {
    # reach: weekly aware-pool growth; conversion: aware -> adopter;
    # cac_usd: acquisition cost per adopter; weekly_retention: survival;
    # focus: segment receptivity multipliers; competitor_drag: how hard
    # the competitor response bites this strategy.
    "niche-first": {"reach": 0.055, "conversion": 0.085, "cac_usd": 12.0,
                    "weekly_retention": 0.965, "competitor_drag": 0.25,
                    "focus": {"smb": 1.35, "midmarket": 0.8, "prosumer": 0.7}},
    "partnership-led": {"reach": 0.10, "conversion": 0.055, "cac_usd": 26.0,
                        "weekly_retention": 0.945, "competitor_drag": 0.5,
                        "focus": {"smb": 1.0, "midmarket": 1.15, "prosumer": 0.9},
                        "partner_ramp_weeks": 12},
    "broad": {"reach": 0.22, "conversion": 0.022, "cac_usd": 41.0,
              "weekly_retention": 0.90, "competitor_drag": 1.0,
              "focus": {"smb": 0.9, "midmarket": 1.0, "prosumer": 1.1}},
}

SEGMENTS = {"smb": 0.55, "midmarket": 0.30, "prosumer": 0.15}

DEFAULT_BASELINE = {
    "market_size": 20000,
    "price_usd_month": 29.0,
    "competitor_strength": 0.35,   # 0..1
    "budget_usd": 150000.0,
    "ticks": 26,                   # weeks
    "weekly_fixed_cost_usd": 400.0,
}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def baseline_hash(baseline: dict) -> str:
    return hashlib.sha256(_canonical(baseline).encode()).hexdigest()[:16]


def run_branch(baseline: dict, strategy_name: str, seed: int,
               overrides: Optional[dict] = None,
               events: Optional[list] = None) -> dict:
    """One deterministic branch run. Pure: inputs are never mutated.

    `events` (v21) are step triggers beside the clock: each is
    {"at_tick": n} or {"when": "cash_below"|"adopters_above",
    "threshold": x} plus a "changes" dict of parameter values applied
    when the trigger first holds (budget_usd ADDS to cash; the other
    parameters are set). Fired events land in result["events_fired"].
    With no events the numeric path is exactly the v1 engine's."""
    base = copy.deepcopy(baseline)
    if overrides:
        for k, v in overrides.items():
            if k in base and isinstance(v, (int, float)):
                base[k] = v
    params = copy.deepcopy(STRATEGIES[strategy_name])
    ticks = int(min(base["ticks"], MAX_TICKS))
    rng = random.Random(seed)
    market = float(min(base["market_size"], MAX_MARKET))
    price_m = float(base["price_usd_month"])
    competitor = float(base["competitor_strength"])
    cash = float(base["budget_usd"])
    aware = 0.0
    adopters = 0.0
    total_spend = 0.0
    total_revenue = 0.0
    total_new = 0.0
    exhausted_tick: Optional[int] = None
    series = []
    receptivity = sum(SEGMENTS[s] * params["focus"][s] for s in SEGMENTS)
    pending_events = [dict(e) for e in (events or [])]
    events_fired: list[dict] = []
    for tick in range(1, ticks + 1):
        # v21 event-triggered steps: evaluated on the state entering
        # the tick, before the tick's dynamics run.
        for ev in list(pending_events):
            when = ev.get("when")
            fired = False
            if ev.get("at_tick") == tick:
                fired = True
            elif when == "cash_below" and cash < float(ev.get("threshold", 0)):
                fired = True
            elif when == "adopters_above" and adopters > float(ev.get("threshold", 0)):
                fired = True
            if fired:
                for k, v in (ev.get("changes") or {}).items():
                    if not isinstance(v, (int, float)):
                        continue
                    if k == "budget_usd":
                        cash += float(v)
                    elif k == "price_usd_month":
                        price_m = float(v)
                    elif k == "competitor_strength":
                        competitor = float(v)
                    elif k == "market_size":
                        market = float(min(v, MAX_MARKET))
                    elif k in base:
                        base[k] = v
                events_fired.append({"tick": tick, "event": ev})
                pending_events.remove(ev)
        noise = 1.0 + rng.uniform(-0.06, 0.06)
        reach = params["reach"]
        if params.get("partner_ramp_weeks"):
            ramp = min(1.0, 0.6 + 0.4 * tick / params["partner_ramp_weeks"])
            reach = reach * ramp
        aware += (market - aware) * reach * noise
        potential = aware - adopters
        new = max(0.0, potential * params["conversion"] * receptivity * noise)
        acquisition_cost = new * params["cac_usd"]
        fixed = float(base["weekly_fixed_cost_usd"])
        if cash <= 0:
            if exhausted_tick is None:
                exhausted_tick = tick
            new = 0.0
            acquisition_cost = 0.0
            fixed = 0.0
        elif cash - acquisition_cost - fixed < 0:
            if exhausted_tick is None:
                exhausted_tick = tick
            affordable = max(0.0, (cash - fixed) / params["cac_usd"])
            new = min(new, affordable)
            acquisition_cost = new * params["cac_usd"]
        spend = min(acquisition_cost + fixed, max(cash, 0.0))
        cash -= spend
        total_spend += spend
        total_new += new
        retention = params["weekly_retention"] - 0.05 * competitor * params["competitor_drag"]
        adopters = adopters * retention + new
        revenue = adopters * price_m / 4.33
        total_revenue += revenue
        series.append({"tick": tick, "adopters": round(adopters),
                       "revenue_usd": round(revenue, 2),
                       "spend_usd": round(spend, 2), "cash_usd": round(cash, 2)})
    final = {
        "adopters": round(adopters),
        "market_share_pct": round(100.0 * adopters / market, 2) if market else 0.0,
        "total_revenue_usd": round(total_revenue, 2),
        "total_spend_usd": round(total_spend, 2),
        "blended_cac_usd": round(total_spend / total_new, 2) if total_new else None,
        "retention_90d_pct": round(100.0 * (params["weekly_retention"]
                                            - 0.05 * competitor * params["competitor_drag"]) ** 13, 1),
        "min_cash_usd": round(min(s["cash_usd"] for s in series), 2) if series else 0.0,
        "budget_exhausted_tick": exhausted_tick,
        "label": "SIMULATED ESTIMATE",
    }
    outcome_hash = hashlib.sha256(_canonical(
        {"final": final, "series": series,
         "events_fired": events_fired}).encode()).hexdigest()[:16]
    return {"strategy": strategy_name, "series": series, "final": final,
            "events_fired": events_fired, "outcome_hash": outcome_hash}


def sensitivity(baseline: dict, strategy_name: str, seed: int) -> list[dict]:
    """Outcome deltas when single assumptions move +/-20%."""
    base_final = run_branch(baseline, strategy_name, seed)["final"]["adopters"]
    rows = []
    for assumption in ("price_usd_month", "competitor_strength"):
        row = {"assumption": assumption, "base_adopters": base_final}
        for label, factor in (("minus_20pct", 0.8), ("plus_20pct", 1.2)):
            varied = copy.deepcopy(baseline)
            varied[assumption] = varied[assumption] * factor
            if assumption == "competitor_strength":
                varied[assumption] = min(1.0, varied[assumption])
            row[label] = run_branch(varied, strategy_name, seed)["final"]["adopters"]
        rows.append(row)
    return rows


# ------------------------------------------------------- data grounding
_UNIT = {"million": 1_000_000, "billion": 1_000_000_000, "thousand": 1_000}
_MARKET_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand)\s+"
    r"(?:of\s+)?(?:active\s+|monthly\s+)?"
    r"(users|user|customers|subscribers|businesses|creators|people|channels|pages|members|students|freelancers|followers|viewers)",
    re.IGNORECASE)
_PRICE_RE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*(?:/|per\s+)(?:mo\b|month)", re.IGNORECASE)
_COMPETITOR_RE = re.compile(
    r"competitor|alternative|rival|incumbent|market leader|dominant player",
    re.IGNORECASE)


def extract_grounding(sources: list[dict]) -> dict:
    """Mine world parameters from the mission's OWN researched sources.

    The simulation should behave like the world the agents actually
    found — not like a generic spreadsheet. Every parameter records its
    provenance: `origin: "data"` with the source and the exact phrase it
    came from, or `origin: "assumption"` when the research contains no
    usable figure (the platform default stands, openly labelled).
    Deterministic: first match in source order wins."""
    grounding: dict[str, dict] = {}
    texts = [(s, f"{s.get('title', '')} {s.get('excerpt', '')}")
             for s in sources]

    market = None
    for s, t in texts:
        m = _MARKET_RE.search(t)
        if m:
            value = float(m.group(1).replace(",", "")) * _UNIT[m.group(2).lower()]
            market = {"value": int(min(value, MAX_MARKET)), "origin": "data",
                      "source_id": s["source_id"], "evidence": m.group(0)[:120]}
            break
    grounding["market_size"] = market or {
        "value": DEFAULT_BASELINE["market_size"], "origin": "assumption",
        "evidence": "no market-size figure in the researched sources"}

    price = None
    for s, t in texts:
        m = _PRICE_RE.search(t)
        if m:
            value = float(m.group(1))
            if 0 < value <= 1000:
                price = {"value": value, "origin": "data",
                         "source_id": s["source_id"],
                         "evidence": m.group(0)[:120]}
                break
    grounding["price_usd_month"] = price or {
        "value": DEFAULT_BASELINE["price_usd_month"], "origin": "assumption",
        "evidence": "no monthly price found in the researched sources"}

    if texts:
        hits = sum(1 for _, t in texts if _COMPETITOR_RE.search(t))
        frac = hits / len(texts)
        grounding["competitor_strength"] = {
            "value": round(min(0.75, max(0.15, 0.2 + 0.5 * frac)), 2),
            "origin": "data",
            "evidence": f"{hits} of {len(texts)} researched sources discuss "
                        f"competitors or alternatives"}
    else:
        grounding["competitor_strength"] = {
            "value": DEFAULT_BASELINE["competitor_strength"],
            "origin": "assumption",
            "evidence": "no researched sources to gauge competition from"}
    return grounding


# ---------------------------------------------------------------- service
class WorldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    mission_id: Optional[str] = None
    seed: int = 42
    baseline: dict = Field(default_factory=dict)  # overrides on defaults
    # v21: compose the world from environment modules (default: all)
    modules: Optional[list] = None


class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str = Field(pattern="^(niche-first|partnership-led|broad)$")
    label: Optional[str] = None
    origin_candidate_id: Optional[str] = None
    assumptions: dict = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_world(store, tenant_id: str, user_id: str, body: WorldCreate) -> dict:
    # v21: the baseline is composed from typed environment modules;
    # every parameter carries its provenance forward.
    baseline, param_provenance = compose_baseline(body.modules)
    provenance: dict = {"created_by": user_id, "created_at": _now()}
    if body.mission_id:
        mission = store.get_mission(tenant_id, body.mission_id)
        if mission is None:
            raise KeyError("mission not found")
        sources = store.list_sources(tenant_id)
        provenance["mission_id"] = body.mission_id
        provenance["objective"] = mission["contract"]["objective"]
        provenance["source_snapshot_hash"] = hashlib.sha256(
            _canonical([s["source_id"] for s in sources]).encode()).hexdigest()[:16]
        provenance["candidate_evaluations"] = {
            ev["candidate_id"]: {k: v["score"] for k, v in ev["dimensions"].items()}
            for ev in store.list_evaluations(tenant_id, body.mission_id)}
        # Ground the world in the mission's researched data: parameters
        # come from the sources its candidates actually cited, each with
        # provenance. Explicit baseline overrides still win (applied next).
        cited_ids = {sid for c in store.list_candidates(tenant_id, body.mission_id)
                     for cl in c.get("claims", []) for sid in cl.get("source_ids", [])}
        cited = [s for s in sources if s["source_id"] in cited_ids]
        grounding = extract_grounding(cited)
        for param, g in grounding.items():
            if g["origin"] == "data":
                baseline[param] = g["value"]
                param_provenance[param] = {"value": g["value"],
                                           "origin": "data:grounding",
                                           "type": param_provenance.get(
                                               param, {}).get("type", "float")}
        provenance["grounding"] = grounding
    for k, v in (body.baseline or {}).items():
        if k in baseline and isinstance(v, (int, float)):
            baseline[k] = v
            if k in param_provenance:
                param_provenance[k] = {**param_provenance[k], "value": v,
                                       "origin": "override"}
    baseline["ticks"] = int(max(1, min(baseline["ticks"], MAX_TICKS)))
    param_provenance["ticks"] = {**param_provenance.get("ticks", {}),
                                 "value": baseline["ticks"]}
    world = {
        "world_id": new_id("wrld"), "tenant_id": tenant_id, "name": body.name,
        "status": "draft", "seed": body.seed, "engine_version": ENGINE_VERSION,
        "rubric_version": RUBRIC_VERSION, "baseline": baseline,
        "baseline_hash": baseline_hash(baseline), "provenance": provenance,
        "branches": [], "experiments": [],
        "param_provenance": param_provenance,
        "modules": body.modules or list(ENVIRONMENT_MODULES),
        "label": "SIMULATED ESTIMATE",
        "disclosure": ("Numeric engine is deterministic for a fixed seed. "
                       "Outputs are conditional estimates, not predictions."),
    }
    store.put_world(tenant_id, world)
    store.put_event(tenant_id, event_envelope(
        tenant_id, "world", world["world_id"], "WorldCreated",
        {"name": body.name, "mission_id": body.mission_id, "seed": body.seed}))
    return world


def add_branch(store, tenant_id: str, world: dict, body: BranchCreate) -> dict:
    if len(world["branches"]) >= MAX_BRANCHES:
        raise ValueError(f"branch limit reached ({MAX_BRANCHES})")
    branch = {
        "branch_id": new_id("brch"), "strategy": body.strategy,
        "label": body.label or body.strategy,
        "origin_candidate_id": body.origin_candidate_id,
        "assumptions": body.assumptions or {}, "status": "pending",
        "result": None, "sensitivity": None,
    }
    world["branches"].append(branch)
    store.put_world(tenant_id, world)
    store.put_event(tenant_id, event_envelope(
        tenant_id, "world", world["world_id"], "BranchAdded",
        {"branch_id": branch["branch_id"], "strategy": body.strategy}))
    return branch


def run_world(store, tenant_id: str, world: dict) -> dict:
    """Runs every pending branch against a DEEP COPY of the frozen
    baseline; the stored baseline is never mutated by a run."""
    frozen = copy.deepcopy(world["baseline"])
    for i, branch in enumerate(world["branches"]):
        if branch["status"] == "completed":
            continue
        result = run_branch(frozen, branch["strategy"],
                            seed=world["seed"] + i,
                            overrides=branch.get("assumptions") or None)
        branch["result"] = result
        branch["sensitivity"] = sensitivity(frozen, branch["strategy"],
                                            seed=world["seed"] + i)
        branch["trace"] = branch_trace(world, branch, world["seed"] + i)
        branch["status"] = "completed"
    world["status"] = "completed"
    assert baseline_hash(frozen) == world["baseline_hash"]
    store.put_world(tenant_id, world)
    store.put_event(tenant_id, event_envelope(
        tenant_id, "world", world["world_id"], "SimulationCompleted",
        {"branches": len(world["branches"]),
         "outcome_hashes": [b["result"]["outcome_hash"]
                            for b in world["branches"] if b["result"]]}))
    return world


def compare(world: dict) -> dict:
    rows = []
    for b in world["branches"]:
        if not b["result"]:
            continue
        f = b["result"]["final"]
        origin_scores = (world["provenance"].get("candidate_evaluations", {})
                         .get(b.get("origin_candidate_id") or "", {}))
        rows.append({
            "branch_id": b["branch_id"], "label": b["label"],
            "strategy": b["strategy"], "adopters": f["adopters"],
            "market_share_pct": f["market_share_pct"],
            "total_revenue_usd": f["total_revenue_usd"],
            "total_spend_usd": f["total_spend_usd"],
            "blended_cac_usd": f["blended_cac_usd"],
            "retention_90d_pct": f["retention_90d_pct"],
            "min_cash_usd": f["min_cash_usd"],
            "budget_exhausted_tick": f["budget_exhausted_tick"],
            "outcome_hash": b["result"]["outcome_hash"],
            "origin_candidate_scores_research_v1": origin_scores,
            "sensitivity": b["sensitivity"], "label": "SIMULATED ESTIMATE",
        })
    return {"world_id": world["world_id"], "baseline_hash": world["baseline_hash"],
            "seed": world["seed"], "engine_version": world["engine_version"],
            "rubric_version": world["rubric_version"], "rows": rows,
            "label": "SIMULATED ESTIMATE", "disclosure": world["disclosure"]}


# ------------------------------------------- v21: environment modules
class ParamSpec(BaseModel):
    """One typed world parameter. Worlds are composed from modules of
    these; every value in a composed baseline carries provenance
    (which module/override/grounding produced it)."""
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str = Field(pattern="^(float|int)$")
    default: float
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""


def _param(name, type_, default, lo, hi, desc):
    return ParamSpec(name=name, type=type_, default=default,
                     minimum=lo, maximum=hi, description=desc)


ENVIRONMENT_MODULES: dict[str, dict] = {
    "market": {"description": "The addressable market and its price/competition shape.",
               "params": [_param("market_size", "int", 20000, 1, MAX_MARKET,
                                 "Total addressable users/customers."),
                          _param("price_usd_month", "float", 29.0, 0, 100000,
                                 "Monthly price per adopter, USD."),
                          _param("competitor_strength", "float", 0.35, 0, 1,
                                 "Competitor pressure, 0..1.")]},
    "budget": {"description": "The money the strategy may spend.",
               "params": [_param("budget_usd", "float", 150000.0, 0, None,
                                 "Total budget, USD."),
                          _param("weekly_fixed_cost_usd", "float", 400.0, 0, None,
                                 "Fixed weekly operating cost, USD.")]},
    "horizon": {"description": "How long the simulation runs.",
                "params": [_param("ticks", "int", 26, 1, MAX_TICKS,
                                  "Weeks simulated.")]},
}

ALL_PARAMS: dict[str, ParamSpec] = {
    p.name: p for m in ENVIRONMENT_MODULES.values() for p in m["params"]}


def compose_baseline(modules: Optional[list] = None,
                     overrides: Optional[dict] = None) -> tuple[dict, dict]:
    """Compose a baseline from environment modules (default: all).
    Returns (baseline, param_provenance) where provenance records, per
    parameter, {value, origin} — origin is "module:<name>", or
    "override" for caller-supplied values. Unknown modules/params are
    rejected loudly, never silently dropped."""
    chosen = modules or list(ENVIRONMENT_MODULES)
    baseline: dict = {}
    provenance: dict = {}
    for name in chosen:
        module = ENVIRONMENT_MODULES.get(name)
        if module is None:
            raise ValueError(f"unknown environment module: {name}")
        for spec in module["params"]:
            baseline[spec.name] = spec.default
            provenance[spec.name] = {"value": spec.default,
                                     "origin": f"module:{name}",
                                     "type": spec.type}
    for k, v in (overrides or {}).items():
        spec = ALL_PARAMS.get(k)
        if spec is None or not isinstance(v, (int, float)):
            continue
        value = float(v)
        if spec.minimum is not None:
            value = max(spec.minimum, value)
        if spec.maximum is not None:
            value = min(spec.maximum, value)
        if spec.type == "int":
            value = int(value)
        baseline[k] = value
        provenance[k] = {"value": value, "origin": "override",
                         "type": spec.type}
    return baseline, provenance


# ---------------------------------- v21: interventions + metrics + pairs
METRIC_NAMES = ("adopters", "market_share_pct", "total_revenue_usd",
                "total_spend_usd", "blended_cac_usd", "retention_90d_pct",
                "min_cash_usd")


class Intervention(BaseModel):
    """A named, described set of parameter changes — the 'treatment'
    half of an experiment pair."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    changes: dict = Field(default_factory=dict)


class MetricsSpec(BaseModel):
    """The metrics an experiment commits to BEFORE it runs
    (pre-registration): the result reports exactly these."""
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=10)

    @classmethod
    def validated(cls, metrics: list) -> "MetricsSpec":
        bad = [m for m in metrics if m not in METRIC_NAMES]
        if bad:
            raise ValueError(f"unknown metrics: {bad} (choose from {METRIC_NAMES})")
        return cls(metrics=list(dict.fromkeys(metrics)))


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str = Field(pattern="^(niche-first|partnership-led|broad)$")
    intervention: Intervention | None = None
    metrics: list[str] = Field(default_factory=lambda: ["adopters",
                                                        "total_revenue_usd",
                                                        "blended_cac_usd"])
    engine: str = Field(default="aggregate", pattern="^(aggregate|actors)$")
    actors: int = Field(default=400, ge=10, le=5000)
    events: list = Field(default_factory=list)


def _metric_deltas(base_final: dict, int_final: dict, metrics: list) -> dict:
    out = {}
    for m in metrics:
        b, i = base_final.get(m), int_final.get(m)
        delta = (round(i - b, 2) if isinstance(b, (int, float))
                 and isinstance(i, (int, float)) else None)
        out[m] = {"baseline": b, "intervention": i, "delta": delta}
    return out


def run_experiment(store, tenant_id: str, world: dict,
                   body: ExperimentCreate) -> dict:
    """The standard v21 experiment record: metrics are registered on
    the world FIRST (status "registered"), then a baseline branch and
    an intervention branch run as a PAIR on the world's one seed, and
    the record completes with per-metric deltas. Deterministic: the
    same world + experiment always produces the same record."""
    metrics = MetricsSpec.validated(body.metrics)
    frozen = copy.deepcopy(world["baseline"])
    experiment = {
        "experiment_id": new_id("exp"), "status": "registered",
        "registered_at": _now(), "completed_at": None,
        "strategy": body.strategy, "engine": body.engine,
        "seed": world["seed"],
        "intervention": body.intervention.model_dump() if body.intervention else None,
        "metrics_registered": metrics.metrics,
        "baseline_hash": world["baseline_hash"],
        "label": "SIMULATED ESTIMATE",
        "result": None,
    }
    world.setdefault("experiments", []).append(experiment)
    store.put_world(tenant_id, world)
    store.put_event(tenant_id, event_envelope(
        tenant_id, "world", world["world_id"], "ExperimentRegistered",
        {"experiment_id": experiment["experiment_id"],
         "metrics": metrics.metrics, "engine": body.engine}))

    runner = run_actor_sim if body.engine == "actors" else run_branch
    kwargs = {"events": body.events or None} if body.engine == "aggregate" else {}
    if body.engine == "actors":
        kwargs = {"n_actors": body.actors}
    base_run = runner(frozen, body.strategy, seed=world["seed"], **kwargs)
    changes = (body.intervention.changes if body.intervention else {}) or {}
    int_run = runner(frozen, body.strategy, seed=world["seed"],
                     overrides=changes, **kwargs)
    experiment["result"] = {
        "metrics": _metric_deltas(base_run["final"], int_run["final"],
                                  metrics.metrics),
        "baseline_outcome_hash": base_run["outcome_hash"],
        "intervention_outcome_hash": int_run["outcome_hash"],
        "baseline_final": base_run["final"],
        "intervention_final": int_run["final"],
        "events_fired": int_run.get("events_fired", []),
    }
    experiment["status"] = "completed"
    experiment["completed_at"] = _now()
    store.put_world(tenant_id, world)
    store.put_event(tenant_id, event_envelope(
        tenant_id, "world", world["world_id"], "ExperimentCompleted",
        {"experiment_id": experiment["experiment_id"],
         "baseline_outcome_hash": base_run["outcome_hash"],
         "intervention_outcome_hash": int_run["outcome_hash"]}))
    return experiment


# -------------------------------------------- v21: simulated actors
def run_actor_sim(baseline: dict, strategy_name: str, seed: int,
                  n_actors: int = 400,
                  overrides: Optional[dict] = None) -> dict:
    """Rule-based actor engine (AgentVerse/AgentSociety design,
    native): each simulated actor has a STATIC PROFILE (segment,
    price sensitivity, loyalty) and a WORKING STATE (unaware → aware
    → adopter → churned) updated by fixed rules each tick. Fully
    deterministic for a seed: one rng stream, fixed actor order, no
    wall-clock or LLM input anywhere in the numeric path."""
    base = copy.deepcopy(baseline)
    if overrides:
        for k, v in overrides.items():
            if k in base and isinstance(v, (int, float)):
                base[k] = v
    params = copy.deepcopy(STRATEGIES[strategy_name])
    ticks = int(min(base["ticks"], MAX_TICKS))
    rng = random.Random(seed)
    market = float(min(base["market_size"], MAX_MARKET))
    price_m = float(base["price_usd_month"])
    competitor = float(base["competitor_strength"])
    cash = float(base["budget_usd"])
    fixed = float(base["weekly_fixed_cost_usd"])
    segments = list(SEGMENTS)
    weights = [SEGMENTS[s] for s in segments]
    actors = []
    for _i in range(n_actors):
        segment = rng.choices(segments, weights)[0]
        actors.append({
            "profile": {"segment": segment,
                        "price_sensitivity": round(rng.uniform(0.7, 1.3), 4),
                        "loyalty": round(rng.uniform(0.85, 1.0), 4)},
            "state": {"aware": False, "adopter": False, "churned": False,
                      "spend_usd": 0.0, "revenue_usd": 0.0},
        })
    scale = market / n_actors if n_actors else 0.0
    retention = params["weekly_retention"] - 0.05 * competitor * params["competitor_drag"]
    series = []
    total_spend = 0.0
    total_revenue = 0.0
    total_new = 0
    exhausted_tick: Optional[int] = None
    for tick in range(1, ticks + 1):
        noise = 1.0 + rng.uniform(-0.06, 0.06)
        reach = params["reach"]
        if params.get("partner_ramp_weeks"):
            reach = reach * min(1.0, 0.6 + 0.4 * tick / params["partner_ramp_weeks"])
        new_adopters = 0
        for actor in actors:
            st, prof = actor["state"], actor["profile"]
            focus = params["focus"][prof["segment"]]
            if st["churned"]:
                continue
            if not st["aware"]:
                if rng.random() < reach * focus * noise:
                    st["aware"] = True
                continue
            if not st["adopter"]:
                price_factor = max(0.4, min(1.6, (29.0 / price_m)
                                             / prof["price_sensitivity"]))
                if rng.random() < params["conversion"] * focus * price_factor * noise:
                    st["adopter"] = True
                    new_adopters += 1
                continue
            if rng.random() > retention * prof["loyalty"] / 0.925:
                st["adopter"] = False
                st["churned"] = True
        acquisition = new_adopters * scale * params["cac_usd"]
        tick_fixed = fixed if cash > 0 else 0.0
        if cash <= 0:
            if exhausted_tick is None:
                exhausted_tick = tick
            acquisition = 0.0
            tick_fixed = 0.0
        elif cash - acquisition - tick_fixed < 0:
            if exhausted_tick is None:
                exhausted_tick = tick
            affordable = max(0.0, (cash - tick_fixed) / params["cac_usd"])
            acquisition = min(acquisition, affordable)
        spend = min(acquisition + tick_fixed, max(cash, 0.0))
        cash -= spend
        total_spend += spend
        total_new += new_adopters
        adopters = sum(1 for a in actors if a["state"]["adopter"])
        revenue = adopters * scale * price_m / 4.33
        total_revenue += revenue
        series.append({"tick": tick, "adopters": round(adopters * scale),
                       "revenue_usd": round(revenue, 2),
                       "spend_usd": round(spend, 2),
                       "cash_usd": round(cash, 2)})
    adopters_n = sum(1 for a in actors if a["state"]["adopter"])
    final = {
        "adopters": round(adopters_n * scale),
        "market_share_pct": round(100.0 * adopters_n / n_actors, 2) if n_actors else 0.0,
        "total_revenue_usd": round(total_revenue, 2),
        "total_spend_usd": round(total_spend, 2),
        "blended_cac_usd": round(total_spend / (total_new * scale), 2) if total_new else None,
        "retention_90d_pct": round(100.0 * retention ** 13, 1),
        "min_cash_usd": round(min(s["cash_usd"] for s in series), 2) if series else 0.0,
        "budget_exhausted_tick": exhausted_tick,
        "engine": "actors", "actors": n_actors,
        "label": "SIMULATED ESTIMATE",
    }
    outcome_hash = hashlib.sha256(_canonical(
        {"final": final, "series": series}).encode()).hexdigest()[:16]
    return {"strategy": strategy_name, "series": series, "final": final,
            "events_fired": [], "outcome_hash": outcome_hash,
            "actors_sample": actors[:5]}


# ------------------------------------------------- v21: JSONL traces
def branch_trace(world: dict, branch: dict, branch_seed: int) -> str:
    """Append-only JSONL run trace with a schema header: one header
    line, one line per tick, one per fired event, one final line.
    Byte-stable for a given run — the determinism evidence."""
    result = branch.get("result") or {}
    lines = [json.dumps({
        "schema": "omnimind.simtrace/1", "type": "header",
        "world_id": world.get("world_id"), "branch_id": branch.get("branch_id"),
        "strategy": branch.get("strategy"), "seed": branch_seed,
        "engine_version": ENGINE_VERSION, "label": "SIMULATED ESTIMATE"},
        sort_keys=True)]
    for row in result.get("series", []):
        lines.append(json.dumps({"type": "tick", **row}, sort_keys=True))
    for ev in result.get("events_fired", []):
        lines.append(json.dumps({"type": "event", **ev}, sort_keys=True))
    lines.append(json.dumps({"type": "final", **(result.get("final") or {})},
                            sort_keys=True))
    return "\n".join(lines) + "\n"
