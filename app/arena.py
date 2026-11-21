"""AgentQon — Agent Arena (v15).

Head-to-head persona duels that feed the performance ledger. Two
agents answer the SAME prompt, alone; the user sees the two outputs
blind (Side A / Side B — identities hidden) and picks a winner; only
then are the agents revealed and the result written into the tenant's
agent_stats ledger as arena_wins / arena_losses.

Why this exists: the v13 ledger learns from mission runs, which are
panel-shaped and slow to accumulate. Duels are cheap, frequent and
directly comparable, so a new roster (6,476 library personas landed
in v14 with declared-but-unmeasured priors) earns measured signal
fast — the cold-start fix.

Honesty rules (load-bearing, textbook labelling discipline):
  - Arena outputs are SINGLE-PERSONA drafts. They are not mission
    results: no panel, no research stage, no critic round, no
    citation checks, no evaluation were run on them, and the payload
    says so on every duel (`note`). A duel win is a preference signal,
    not an evidence verdict.
  - Blinding is real: until a vote lands, the public view of a duel
    contains neither agent id nor any field derived from it (titles
    included — in the deterministic harness a title can leak a
    strategy). Side assignment is randomised per duel.

Eligibility: ACTIVE agents with a persona brief in the persona store
(the arena runs personas, so core utility agents without briefs are
out). Quarantined / retired / degraded agents cannot be picked and
cannot be auto-selected. Auto-selection takes the top-2 by the same
suitability ranking that seats mission panels (app/suitability.py),
restricted to persona-bearing agents.

Leaderboard ordering rule (documented, deterministic): agents with at
least MIN_DUELS_RANKED duels form the ranked group; fewer duels =
provisional, always listed below the ranked group however good the
rate. Within a group: win rate desc, then wins desc, then agent id.
Mission record (runs, mean evaluation, selections) rides along from
the same ledger rows — the arena extends the ledger, it does not
fork it.
"""
from __future__ import annotations

import random
from typing import Optional

from .adapters import AgentRequest
from .domain import (StateConflict, MISSION_BUDGET_CEILING_USD, new_id,
                     now_iso)
from .suitability import empty_stat, rank_panel

ARENA_NOTE = ("Single-persona outputs produced for head-to-head comparison. "
              "NOT evidence-checked mission results: no panel, critic round, "
              "citation checks or evaluation were run on them.")
MIN_DUELS_RANKED = 3
_AUTO_PICK_SCAN = 50  # suitability entries scanned when auto-picking duelists


class DuelError(ValueError):
    """A duel cannot be created as requested (client error, 400)."""


def _version_id(manifest: dict) -> str:
    return f"{manifest['agent_id']}@{manifest['version']}"


def resolve_agent(store, tenant_id: str, ref: str) -> Optional[dict]:
    """Resolve an agent_id or agent_version_id within the tenant."""
    for a in store.list_agents(tenant_id):
        if a.get("agent_id") == ref or a.get("agent_version_id") == ref:
            return a
    return None


def _check_eligible(store, manifest: dict, tenant_id: str = "") -> None:
    if manifest.get("lifecycle") != "active":
        raise DuelError(f"agent {manifest['agent_id']} is "
                        f"{manifest.get('lifecycle')} — only ACTIVE agents duel")
    if not store.get_persona(manifest["agent_id"], tenant_id):
        raise DuelError(f"agent {manifest['agent_id']} has no persona brief — "
                        "the arena runs persona agents")


def pick_duelists(store, tenant_id: str, task_type: str) -> list[dict]:
    """Top-2 suitable ACTIVE agents for the task type that carry a
    persona brief — the mission panel ranking, persona-filtered."""
    agents = store.list_agents(tenant_id)
    stats = store.list_agent_stats(tenant_id)
    out = []
    for entry in rank_panel(agents, task_type, stats, k=_AUTO_PICK_SCAN):
        manifest = entry["manifest"]
        if store.get_persona(manifest["agent_id"], tenant_id):
            out.append(manifest)
        if len(out) == 2:
            break
    if len(out) < 2:
        raise DuelError("fewer than two eligible persona agents for "
                        f"task type '{task_type}' in this workspace")
    return out


def _output_text(out: dict) -> str:
    """The side's readable answer: the deliverable when the persona
    produced one, else summary + claims (the candidate's own words)."""
    if out.get("deliverable"):
        return str(out["deliverable"])
    parts = [str(out.get("summary", ""))]
    for c in out.get("claims", []):
        text = c.get("text") if isinstance(c, dict) else getattr(c, "text", "")
        if text:
            parts.append(f"- {text}")
    return "\n".join(p for p in parts if p)


async def create_duel(store, orchestrator, tenant_id: str, created_by: str,
                      task_type: str, prompt: str,
                      agent_a: Optional[str] = None,
                      agent_b: Optional[str] = None) -> dict:
    """Create and RUN a duel. Both personas execute through the same
    adapter path a mission uses (Orchestrator._invoke_research — the
    persona brief leads the model call). Execution is synchronous: a
    duel is two single calls, not a queued mission.

    v20: a duel is generated work, so the model gate applies exactly
    as for missions — no connected model, no duel (ModelNotConnected
    propagates; the API maps it to 503 `model_not_connected`)."""
    from .orchestrator import ModelNotConnected
    state = orchestrator.model_state(tenant_id)
    if not state.connected:
        raise ModelNotConnected(state.message)
    if agent_a or agent_b:
        if not (agent_a and agent_b):
            raise DuelError("name both duelists or neither (auto-pick)")
        ma, mb = resolve_agent(store, tenant_id, agent_a), \
            resolve_agent(store, tenant_id, agent_b)
        if ma is None:
            raise KeyError(agent_a)
        if mb is None:
            raise KeyError(agent_b)
        if ma["agent_id"] == mb["agent_id"]:
            raise DuelError("an agent cannot duel itself")
        _check_eligible(store, ma, tenant_id)
        _check_eligible(store, mb, tenant_id)
        duelists = [ma, mb]
    else:
        duelists = pick_duelists(store, tenant_id, task_type)

    duel_id = new_id("duel")
    outputs: dict[str, dict] = {}
    # A duel is not a mission: it runs WITHOUT a RunContext, so the
    # orchestrator takes the unrouted path and binds an adapter to the
    # tenant's connected model (Orchestrator._unrouted_adapter). Since
    # v21.4 per-run state lives in RunContext, a duel can no longer
    # disturb — or be disturbed by — a mission run in flight; the old
    # save/clear/restore of orchestrator._runtime is gone by design.
    for manifest in duelists:
        request = AgentRequest(
            mission_id=duel_id, task_id="arena-duel",
            objective=prompt,
            deliverables=["Arena response"],
            output_schema={"version": "arena-v1"},
            task_type=task_type)
        stub_mission = {"id": duel_id, "contract": {
            "constraints": {"max_cost_usd": MISSION_BUDGET_CEILING_USD},
            "data_classification": "internal"}}
        response, adapter_version = await orchestrator._invoke_research(
            tenant_id, stub_mission, request, manifest)
        out = response.output
        outputs[manifest["agent_id"]] = {
            "agent_version_id": _version_id(manifest),
            "title": out.get("title", ""), "strategy": out.get("strategy", ""),
            "output_text": _output_text(out),
            "adapter_version": adapter_version,
            "usage": response.usage}

    first, second = duelists[0]["agent_id"], duelists[1]["agent_id"]
    # Randomised side assignment (seeded by the duel id, so the record
    # is reproducible but the requester cannot predict it).
    if random.Random(duel_id).random() < 0.5:
        side_a, side_b = first, second
    else:
        side_a, side_b = second, first
    duel = {
        "duel_id": duel_id, "tenant_id": tenant_id, "created_by": created_by,
        "task_type": task_type, "prompt": prompt, "status": "awaiting_vote",
        "agent_a": first, "agent_b": second,
        "side_a_agent_id": side_a, "side_b_agent_id": side_b,
        "outputs": outputs, "winner_agent_id": None, "loser_agent_id": None,
        "note": ARENA_NOTE, "created_at": now_iso(), "voted_at": None,
    }
    store.put_duel(tenant_id, duel)
    return duel


def public_duel(duel: dict) -> dict:
    """The tenant-facing view. While awaiting a vote it is BLIND: sides
    carry output text only — no agent ids, titles or strategies. After
    the vote, identities and the winner are revealed."""
    outputs = duel.get("outputs", {})
    view = {
        "duel_id": duel["duel_id"], "task_type": duel["task_type"],
        "prompt": duel["prompt"], "status": duel["status"],
        "created_at": duel["created_at"], "note": duel["note"],
        "side_a": {"output": outputs.get(duel["side_a_agent_id"], {}).get("output_text", "")},
        "side_b": {"output": outputs.get(duel["side_b_agent_id"], {}).get("output_text", "")},
    }
    if duel["status"] == "voted":
        for side, key in (("side_a", "side_a_agent_id"), ("side_b", "side_b_agent_id")):
            agent_id = duel[key]
            view[side]["agent_id"] = agent_id
            view[side]["agent_version_id"] = outputs.get(agent_id, {}).get("agent_version_id")
            view[side]["title"] = outputs.get(agent_id, {}).get("title", "")
        view["winner_agent_id"] = duel["winner_agent_id"]
        view["loser_agent_id"] = duel["loser_agent_id"]
        view["winner_side"] = "a" if duel["winner_agent_id"] == duel["side_a_agent_id"] else "b"
        view["voted_at"] = duel["voted_at"]
    return view


def cast_vote(store, tenant_id: str, duel_id: str, winner_side: str) -> dict:
    """Record a vote: reveal, and move the ledger. The winner's
    arena_wins and the loser's arena_losses (per duel task type) are
    written into agent_stats — the same rows GET /v1/agents/stats
    reports and the suitability ranking blends."""
    duel = store.get_duel(tenant_id, duel_id)
    if duel is None:
        raise KeyError(duel_id)
    if duel["status"] != "awaiting_vote":
        raise StateConflict(f"duel {duel_id} is already {duel['status']}")
    winner = duel["side_a_agent_id"] if winner_side == "a" else duel["side_b_agent_id"]
    loser = duel["side_b_agent_id"] if winner_side == "a" else duel["side_a_agent_id"]
    duel["status"] = "voted"
    duel["winner_agent_id"] = winner
    duel["loser_agent_id"] = loser
    duel["voted_at"] = now_iso()
    store.put_duel(tenant_id, duel)

    task_type = duel["task_type"]
    stats = {f"{s['agent_version_id']}|{s['task_type']}": s
             for s in store.list_agent_stats(tenant_id)}
    outputs = duel.get("outputs", {})
    for agent_id, field in ((winner, "arena_wins"), (loser, "arena_losses")):
        avid = outputs.get(agent_id, {}).get("agent_version_id") or agent_id
        key = f"{avid}|{task_type}"
        stat = stats.get(key) or empty_stat(avid, task_type)
        stat[field] = int(stat.get(field, 0)) + 1
        store.put_agent_stat(tenant_id, stat)
    return duel


def leaderboard(store, tenant_id: str, task_type: Optional[str] = None) -> dict:
    """Arena standings merged with the mission record from the same
    ledger rows. Ordering rule: see module docstring."""
    agg: dict[str, dict] = {}
    for s in store.list_agent_stats(tenant_id):
        if task_type and s.get("task_type") != task_type:
            continue
        avid = s["agent_version_id"]
        e = agg.setdefault(avid, {"agent_version_id": avid,
                                  "agent_id": avid.rsplit("@", 1)[0],
                                  "arena_wins": 0, "arena_losses": 0,
                                  "runs": 0, "eval_sum": 0.0, "selections": 0})
        e["arena_wins"] += int(s.get("arena_wins", 0) or 0)
        e["arena_losses"] += int(s.get("arena_losses", 0) or 0)
        e["runs"] += int(s.get("runs", 0) or 0)
        e["eval_sum"] += float(s.get("eval_sum", 0.0) or 0.0)
        e["selections"] += int(s.get("selections", 0) or 0)
    entries = []
    for e in agg.values():
        duels = e["arena_wins"] + e["arena_losses"]
        if duels == 0:
            continue
        entries.append({
            "agent_version_id": e["agent_version_id"], "agent_id": e["agent_id"],
            "wins": e["arena_wins"], "losses": e["arena_losses"], "duels": duels,
            "win_rate": round(e["arena_wins"] / duels, 4),
            "ranked": duels >= MIN_DUELS_RANKED,
            "mission_runs": e["runs"],
            "mean_eval": (round(e["eval_sum"] / e["runs"], 2) if e["runs"] else None),
            "selections": e["selections"]})
    entries.sort(key=lambda x: (not x["ranked"], -x["win_rate"], -x["wins"],
                                x["agent_id"]))
    return {"task_type": task_type, "min_duels_for_ranking": MIN_DUELS_RANKED,
            "entries": entries}
