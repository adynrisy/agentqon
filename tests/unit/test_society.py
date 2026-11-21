"""Society simulation (2026-10-22): sizer, casting, engine, derived
output — all proven against a SCRIPTED fake gateway whose reactions
are deterministic functions of (agent, round), so the bookkeeping
can be asserted exactly: events == responded x rounds, table calls ==
ceil math, leads individual, caps and convergence firing for real.

Nothing here touches a real model: the service's client_factory seam
(the validate_connection / modeltest precedent) injects the fake.
"""
import asyncio
import math
import os
import re
import tempfile
import time

import pytest

import app.society as society
from app.gateway_client import GatewayError
from app.modelstate import ModelState
from app.society import (SocietyNoModel, SocietyService, cast_society,
                         derive_graph, derive_world_output, est_calls_for,
                         leads_for, mission_result_text, size_society)
from app.store import Store

TENANT = "org_soc_a"
TENANT_B = "org_soc_b"
STANCES = ["support", "oppose", "neutral"]


# ------------------------------------------------------------- fake gateway
def _hash(text: str) -> int:
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) % 100000
    return h


class ScriptedGateway:
    """Deterministic reactions. `stable=True` ⇒ the room's split is
    identical every round (convergence must fire at round 2);
    otherwise stances rotate with the round so the split keeps
    moving and every round runs. `fail_if(system, user)` marks calls
    that raise, like a dead provider."""

    def __init__(self, *, stable=False, fail_if=None, target=False,
                 flip_agents=()):
        self.stable = stable
        self.fail_if = fail_if
        self.target = target
        self.flip_agents = set(flip_agents)
        self.calls = []          # (kind, round, [member_ids])

    def _reaction(self, agent_id, round_no, allowed_targets):
        if agent_id in self.flip_agents:
            # Guarantees the split moves every round by exactly one
            # responder — a room that can never read as converged.
            stance = "support" if round_no % 2 == 1 else "oppose"
            return {"stance": stance, "intensity": 0.9,
                    "action": "share",
                    "line": f"{agent_id} flips in round {round_no}"}
        if self.stable:
            stance = STANCES[_hash(agent_id) % 3]
            intensity = 0.4 + (_hash(agent_id) % 50) / 100.0
        else:
            stance = STANCES[(_hash(agent_id) + round_no) % 3]
            intensity = 0.4 + ((_hash(agent_id) + round_no) % 50) / 100.0
        out = {"stance": stance, "intensity": intensity,
               "action": "share" if stance == "support" else "ignore",
               "line": f"{agent_id} speaks in round {round_no}: {stance}"}
        if self.target and allowed_targets:
            out["target_agent_id"] = sorted(allowed_targets)[0]
        return out

    async def chat_json(self, model, system, user, temperature=0.4):
        round_no = int(re.search(r"ROUND: (\d+)", user).group(1))
        if self.fail_if is not None and self.fail_if(system, user):
            raise GatewayError("provider_unavailable", "scripted boom", 503)
        if "TABLE of simulated people" in system or "at the table" in system \
                or "reactions" in system:
            members = re.findall(r"^- (\S+) \(", user, flags=re.M)
            self.calls.append(("crowd", round_no, members))
            reactions = []
            for m in members:
                mates = [x for x in members if x != m]
                r = self._reaction(m, round_no, mates)
                r["agent_id"] = m
                reactions.append(r)
            return {"reactions": reactions}, {}
        m = re.search(r"you are agent (\S+?),", user)
        agent_id = m.group(1)
        self.calls.append(("lead", round_no, [agent_id]))
        allowed = re.findall(r"^- (\S+) \(", user, flags=re.M)
        return self._reaction(agent_id, round_no, allowed), {}


class _FakeOrchestrator:
    def __init__(self, connected=True):
        self.connected = connected

    def model_state(self, tenant_id=None):
        if self.connected:
            return ModelState(connected=True, source="env",
                              model="fake-model", base_url="http://x")
        return ModelState(connected=False, message="no model connected")


def _manifest(agent_id, *, description="", quality=4.0, division=None):
    return {"agent_id": agent_id, "version": "1.0.0", "owner": "test",
            "description": description or f"Persona of {agent_id}",
            "capabilities": ["persona.task"], "permissions_requested": [],
            "strategy": "persona", "lifecycle": "active",
            "quality": quality, "reliability": 0.9, "latency_ms": 500,
            "cost_per_run_usd": 0.1, "origin": "test",
            "persona_ref": agent_id}


def _seed_roster(store, tenant, count=40, divisions=("marketing",
                                                     "engineering",
                                                     "sales", "support")):
    for i in range(count):
        div = divisions[i % len(divisions)]
        store.upsert_agent(tenant, _manifest(
            f"agency.{div}.agent-{i:03d}",
            description=f"Persona number {i} in {div}"))


def _store():
    return Store(os.path.join(tempfile.mkdtemp(), "soc.db"))


def _service(store, gateway=None, **kw):
    gateway = gateway if gateway is not None else ScriptedGateway()
    factory_calls = {"n": 0}

    def factory(tenant_id):
        factory_calls["n"] += 1
        return gateway, "fake-model"

    svc = SocietyService(store, None, _FakeOrchestrator(),
                         client_factory=factory, **kw)
    return svc, gateway, factory_calls


def _wait_done(store, tenant, society_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        record = store.get_society(tenant, society_id)
        if record["state"] in ("done", "capped", "failed", "cancelled"):
            return record
        time.sleep(0.05)
    raise AssertionError(f"society {society_id} never settled: "
                         f"{record['state']}")


# ------------------------------------------------------------------- sizer
def test_sizer_default_is_the_standard_room():
    s = size_society(objective="Compare launch strategies for a new "
                               "SMB invoicing product")
    assert s["size"] == 100 and s["tier"] == 100
    assert s["leads"] == 12 and s["rounds_cap"] == 5
    assert s["table_size"] == 10
    assert s["est_calls"] == est_calls_for(100, 12) == 105
    assert s["requires_confirmation"] is False
    assert "100" in s["reason"]


def test_sizer_cascade_tiers_need_confirmation():
    wide = size_society(objective="Will this go viral and spread to "
                                  "consumers everywhere")
    assert wide["size"] == 500 and wide["requires_confirmation"] is True
    assert wide["est_calls"] == est_calls_for(500, 12) == 305
    narrow = size_society(objective="Model the adoption cascade in "
                                    "our partner network")
    assert narrow["size"] == 250 and narrow["requires_confirmation"] is True
    assert narrow["est_calls"] == est_calls_for(250, 12) == 180


def test_sizer_reaction_and_focus_tiers():
    assert size_society(objective="How will users react to the new "
                                  "pricing announcement")["size"] == 50
    assert size_society(objective="How will consumers react to this "
                                  "announcement")["size"] == 100
    assert size_society(objective="Quick sanity check with enterprise "
                                  "b2b experts")["size"] == 25
    assert size_society(objective="Help negotiate and persuade the "
                                  "channel partners")["size"] == 50


def test_sizer_slow_model_downsizes_and_says_why():
    s = size_society(objective="Compare launch strategies",
                     latency_ms=60000)
    assert s["size"] == 50
    assert "slowly" in s["reason"]
    rl = size_society(objective="Compare launch strategies",
                      rate_limited=True)
    assert rl["size"] == 50 and "rate-limited" in rl["reason"]


def test_est_calls_math_uses_the_tables_model():
    for size in (25, 50, 100, 250, 500):
        leads = leads_for(size)
        crowd = size - leads
        expect = leads * 5 + math.ceil(crowd / 10) * 5
        assert est_calls_for(size, leads) == expect
    assert leads_for(100) == 12 and leads_for(25) == 3


# ----------------------------------------------------------------- casting
def test_casting_is_seed_reproducible_and_stratified():
    store = _store()
    _seed_roster(store, TENANT, count=140)
    agents = store.list_agents(TENANT)
    kw = dict(size=100, leads_n=12, mission_text="pricing launch",
              seed=7)
    a, b = cast_society(agents, **kw), cast_society(agents, **kw)
    assert a == b
    assert a["size"] == 100 and len(a["leads"]) == 12
    assert len(a["crowd"]) == 88
    cats = {m["category"] for m in a["crowd"]}
    assert len(cats) >= 3  # breadth across divisions, not one flood
    from collections import Counter
    arch = Counter(m["archetype"] for m in a["crowd"])
    # audience proportions: supportive the largest bloc, within a seat
    assert arch["supportive"] == max(arch.values())
    assert abs(arch["supportive"] - round(0.30 * 88)) <= 1
    # tables: ceil(88/10) of them, none a one-stance bloc
    assert len(a["tables"]) == math.ceil(88 / 10)
    by_id = {m["agent_id"]: m for m in a["crowd"]}
    for table in a["tables"]:
        assert len({by_id[aid]["archetype"] for aid in table}) >= 2
        assert all(by_id[aid]["table_id"] for aid in table)


def test_casting_leads_are_the_relevant_ones():
    store = _store()
    _seed_roster(store, TENANT, count=60)
    store.upsert_agent(TENANT, _manifest(
        "agency.marketing.pricing-guru",
        description="Marketing pricing launch strategist for pricing "
                    "launches", quality=4.9))
    agents = store.list_agents(TENANT)
    cast = cast_society(agents, size=25, leads_n=3,
                        mission_text="marketing pricing launch",
                        seed=1)
    assert cast["leads"][0]["agent_id"] == "agency.marketing.pricing-guru"


# ------------------------------------------------------------------ engine
def test_engine_bookkeeping_exact_with_fake():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store)
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a "
                                          "niche b2b pricing change")
    assert record["state"] == "sized" and record["sizer"]["size"] == 25
    assert record["cast"]["size"] == 25
    svc.start_run(TENANT, record["society_id"])
    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "done", done.get("error")
    # rotating fake ⇒ no convergence: all 5 rounds, everyone speaks
    assert len(done["events"]) == 25 * 5
    keys = {(e["agent_id"], e["round"]) for e in done["events"]}
    assert len(keys) == 25 * 5
    leads = svc.get_society(TENANT, record["society_id"])["sizer"]["leads"]
    tables = math.ceil((25 - leads) / 10)
    assert done["calls_made"] == (leads + tables) * 5
    assert done["calls_made"] == done["sizer"]["est_calls"]
    lead_calls = [c for c in gw.calls if c[0] == "lead"]
    crowd_calls = [c for c in gw.calls if c[0] == "crowd"]
    assert len(lead_calls) == leads * 5
    assert len(crowd_calls) == tables * 5
    assert all(len(c[2]) == 1 for c in lead_calls)  # leads are individual


def test_convergence_stops_the_run_early():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, ScriptedGateway(stable=True))
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a "
                                          "niche b2b pricing change")
    svc.start_run(TENANT, record["society_id"])
    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "done"
    assert done["converged_round"] == 2
    assert len(done["events"]) == 25 * 2
    assert done["calls_made"] == done["sizer"]["est_calls"] // 5 * 2


def test_failed_table_is_counted_never_fatal():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    probe = _service(store)[0].create_society(
        TENANT, "tester", task_text="Quick sanity check on a niche "
                                    "b2b pricing change")
    victim = probe["cast"]["crowd"][0]["agent_id"]
    victim_table = {m["agent_id"] for m in probe["cast"]["crowd"]
                    if m["table_id"] == probe["cast"]["crowd"][0]["table_id"]}
    flipper = probe["cast"]["leads"][0]["agent_id"]

    def fail_if(system, user):
        return "reactions" in system and victim in user

    svc, gw, _ = _service(store, ScriptedGateway(
        stable=True, fail_if=fail_if, flip_agents={flipper}))
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a "
                                          "niche b2b pricing change")
    svc.start_run(TENANT, record["society_id"])
    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "done", done.get("error")
    spoken = {e["agent_id"] for e in done["events"]}
    assert not (victim_table & spoken)          # the table never spoke
    assert len(spoken) == 25 - len(victim_table)
    summary = society.summarize(done)
    assert summary["non_responded"] == len(victim_table)
    assert done["missed_responses"] == len(victim_table) * 5


def test_call_cap_ends_the_run_as_capped():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, call_cap_override=4)
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a "
                                          "niche b2b pricing change")
    svc.start_run(TENANT, record["society_id"])
    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "capped" and done["capped"] is True
    assert done["calls_made"] == 4
    assert done["capped_reason"]


def test_cancel_a_sized_society():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, _, _ = _service(store)
    record = svc.create_society(TENANT, "tester", task_text="anything")
    out = svc.cancel(TENANT, record["society_id"])
    assert out["state"] == "cancelled"
    with pytest.raises(society.SocietyStateError):
        svc.start_run(TENANT, record["society_id"])


# ------------------------------------------------------- derived reporting
def test_graph_and_world_output_are_derived_from_events():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, ScriptedGateway(target=True))
    record = svc.create_society(TENANT, "tester",
                                task_text="Quick sanity check on a "
                                          "niche b2b pricing change")
    svc.start_run(TENANT, record["society_id"])
    done = _wait_done(store, TENANT, record["society_id"])
    world = derive_world_output(done)
    assert world["label"] == society.LABEL
    assert world["model"] == "fake-model"
    lines = {e["line"] for e in done["events"]}
    for stance_lines in world["top_lines"].values():
        for item in stance_lines:
            assert item["line"] in lines  # verbatim, never freehand
    split = world["stance_split_final"]
    assert abs(sum(split.values()) - 100.0) < 0.5
    assert world["responded_count"] + world["non_responded_count"] == 25

    graph = derive_graph(done)
    node_ids = {n["id"] for n in graph["nodes"]}
    assert "mission" in node_ids and len(graph["nodes"]) == 26
    by_id = {n["id"]: n for n in graph["nodes"]}
    last = {}
    for e in done["events"]:
        last[e["agent_id"]] = e["stance"]
    for aid, stance in last.items():
        assert by_id[aid]["stance_final"] == stance
        assert by_id[aid]["activity"] == 5
    assert any(e["target"] == "mission" and e["round"] == 1
               for e in graph["edges"])
    assert any(e["target"] != "mission" and e["round"] >= 2
               for e in graph["edges"])  # targeted answers exist
    assert all(e["kind"] in society.ACTIONS for e in graph["edges"])


# ------------------------------------------------------------ mission flow
def _completed_mission(store, tenant):
    mission = store.create_mission(
        tenant, "tester", "Launch check",
        {"objective": "Decide the launch announcement for consumers",
         "deliverables": ["Brief"], "acceptance": [],
         "data_classification": "internal"}, 10)
    store.put_candidate(tenant, {
        "candidate_id": "cand_1", "mission_id": mission["id"],
        "agent_version_id": "agent.drafter.core@1",
        "deliverable": "We will launch at $20 with a public announcement.",
        "summary": "Launch at $20.", "claims": []})
    store.put_evaluation(tenant, mission["id"], {
        "evaluation_id": "ev_1", "candidate_id": "cand_1",
        "dimensions": {"quality": {"score": 0.9}}})
    store.set_mission_status(tenant, mission["id"], "completed")
    return mission


def test_mission_flow_and_result_text():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    mission = _completed_mission(store, TENANT)
    assert "launch at $20" in mission_result_text(
        store, TENANT, store.get_mission(TENANT, mission["id"]))
    svc, gw, factory_calls = _service(store)
    record = svc.create_society(TENANT, "tester",
                                mission_id=mission["id"])
    assert record["state"] == "sized"          # creation never runs
    assert record["result_text"].startswith("We will launch")
    assert factory_calls["n"] == 0            # no model call spent
    assert gw.calls == []


def test_unfinished_mission_is_refused():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    mission = store.create_mission(
        TENANT, "tester", "Draft", {"objective": "x"}, 10)
    svc, _, _ = _service(store)
    with pytest.raises(society.SocietyStateError):
        svc.create_society(TENANT, "tester", mission_id=mission["id"])


def test_run_without_confirmation_is_refused_then_runs():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, ScriptedGateway(stable=True))
    record = svc.create_society(
        TENANT, "tester",
        task_text="Will this go viral and spread to consumers "
                  "everywhere")
    assert record["sizer"]["requires_confirmation"] is True
    with pytest.raises(society.ConfirmationRequired):
        svc.start_run(TENANT, record["society_id"])
    assert gw.calls == []  # the fence held: nothing was spent
    svc.start_run(TENANT, record["society_id"], confirm=True)
    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "done"
    assert done["cast"]["size"] == 40  # cast is bounded by the roster


def test_run_with_no_model_connected_is_refused():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc = SocietyService(store, None, _FakeOrchestrator(connected=False),
                         client_factory=None)
    # create sizes against no model; the RUN refuses, never fakes
    record = svc.create_society(TENANT, "tester", task_text="anything")
    assert record["model"] is None
    with pytest.raises(SocietyNoModel):
        svc.start_run(TENANT, record["society_id"])


def test_tenant_isolation():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    _seed_roster(store, TENANT_B, count=40)
    svc, _, _ = _service(store)
    record = svc.create_society(TENANT, "tester", task_text="anything")
    with pytest.raises(society.SocietyNotFound):
        svc.get_society(TENANT_B, record["society_id"])
    with pytest.raises(society.SocietyNotFound):
        svc.start_run(TENANT_B, record["society_id"])
    mission = _completed_mission(store, TENANT)
    with pytest.raises(society.SocietyNotFound):
        svc.create_society(TENANT_B, "tester", mission_id=mission["id"])


# ------------------------------------------------- stale-run sweeper
def test_stale_running_record_is_swept_to_interrupted():
    """A record stuck at "running" with no live engine in this
    process (the server stopped mid-run) must not read as running
    forever: the read path marks it interrupted, in plain words,
    and persists the new state."""
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, _, _ = _service(store)
    record = svc.create_society(TENANT, "tester", task_text="anything")
    record["state"] = "running"  # the crashed run's last write
    store.put_society(TENANT, record)
    swept = svc.get_society(TENANT, record["society_id"])
    assert swept["state"] == "interrupted"
    assert "interrupted" in swept["error"]
    assert store.get_society(TENANT, record["society_id"])["state"] \
        == "interrupted"
    # Stable: a second read changes nothing further.
    assert svc.get_society(TENANT, record["society_id"])["state"] \
        == "interrupted"


def test_live_run_is_never_swept():
    """The guard's other branch: while a run handle is registered
    (a genuinely live engine), a "running" record reads untouched."""
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, _, _ = _service(store)
    record = svc.create_society(TENANT, "tester", task_text="anything")
    record["state"] = "running"
    store.put_society(TENANT, record)
    svc._live[record["society_id"]] = society._LiveRun()
    try:
        out = svc.get_society(TENANT, record["society_id"])
        assert out["state"] == "running"
        assert not out.get("error")
    finally:
        svc._live.pop(record["society_id"], None)
