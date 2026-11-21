"""v21 Track 6 — simulation upgrade: composable environment modules
with provenance, first-class interventions + pre-registered metrics,
baseline-vs-intervention pairs on one seed, actor state, event-
triggered steps, JSONL traces, and byte-identical determinism."""
import copy
import json

from app.simulation import (ENVIRONMENT_MODULES, ExperimentCreate,
                            Intervention, WorldCreate, add_branch,
                            branch_trace, compose_baseline, create_world,
                            run_actor_sim, run_branch, run_experiment,
                            run_world, BranchCreate, DEFAULT_BASELINE)
from app.store import Store


def _world(tmp_path, name="w"):
    store = Store(str(tmp_path / "sim.db"))
    world = create_world(store, "org_s", "user1",
                          WorldCreate(name=name, seed=7))
    return store, world


def test_compose_baseline_modules_and_provenance():
    baseline, prov = compose_baseline()
    assert baseline == DEFAULT_BASELINE
    assert prov["market_size"]["origin"] == "module:market"
    assert prov["ticks"]["origin"] == "module:horizon"
    partial, pprov = compose_baseline(modules=["market"],
                                      overrides={"price_usd_month": 49})
    assert set(partial) == {"market_size", "price_usd_month",
                            "competitor_strength"}
    assert partial["price_usd_month"] == 49.0
    assert pprov["price_usd_month"]["origin"] == "override"
    clamped, _ = compose_baseline(overrides={"competitor_strength": 4.0})
    assert clamped["competitor_strength"] == 1.0  # spec maximum
    try:
        compose_baseline(modules=["nope"])
        raise AssertionError("unknown module must raise")
    except ValueError:
        pass
    assert set(ENVIRONMENT_MODULES) == {"market", "budget", "horizon"}


def test_world_carries_param_provenance(tmp_path):
    _store, world = _world(tmp_path)
    assert world["engine_version"] == "simulation-v2"
    assert world["param_provenance"]["budget_usd"]["origin"] == "module:budget"
    assert world["experiments"] == []
    assert world["label"] == "SIMULATED ESTIMATE"


def test_intervention_pair_on_one_seed(tmp_path):
    store, world = _world(tmp_path)
    body = ExperimentCreate(
        strategy="niche-first",
        intervention=Intervention(
            name="weaker competitor", description="rival exits",
            changes={"competitor_strength": 0.05}),
        metrics=["adopters", "total_revenue_usd"])
    exp = run_experiment(store, "org_s", world, body)
    assert exp["status"] == "completed"
    assert exp["metrics_registered"] == ["adopters", "total_revenue_usd"]
    assert exp["seed"] == world["seed"] == 7  # ONE seed for the pair
    m = exp["result"]["metrics"]
    assert set(m) == {"adopters", "total_revenue_usd"}  # exactly the registered set
    assert m["adopters"]["intervention"] > m["adopters"]["baseline"]
    assert m["adopters"]["delta"] > 0
    assert exp["result"]["baseline_outcome_hash"] != \
        exp["result"]["intervention_outcome_hash"]
    stored = store.get_world("org_s", world["world_id"])
    assert stored["experiments"][0]["experiment_id"] == exp["experiment_id"]


def test_experiment_determinism_byte_identical(tmp_path):
    _s1, w1 = _world(tmp_path, "a")
    _s2, w2 = _world(tmp_path, "b")
    body = ExperimentCreate(strategy="broad", metrics=["adopters"])
    e1 = run_experiment(_s1, "org_s", w1, copy.deepcopy(body))
    e2 = run_experiment(_s2, "org_s", w2, copy.deepcopy(body))
    assert e1["result"] == e2["result"]
    r1 = run_branch(DEFAULT_BASELINE, "niche-first", 99)
    r2 = run_branch(DEFAULT_BASELINE, "niche-first", 99)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_actor_engine_state_and_determinism():
    r1 = run_actor_sim(DEFAULT_BASELINE, "niche-first", 5, n_actors=120)
    r2 = run_actor_sim(DEFAULT_BASELINE, "niche-first", 5, n_actors=120)
    assert r1["outcome_hash"] == r2["outcome_hash"]
    assert r1["final"]["label"] == "SIMULATED ESTIMATE"
    assert r1["final"]["engine"] == "actors"
    sample = r1["actors_sample"]
    assert sample and "profile" in sample[0] and "state" in sample[0]
    assert set(sample[0]["profile"]) == {"segment", "price_sensitivity", "loyalty"}
    assert set(sample[0]["state"]) >= {"aware", "adopter", "churned"}
    assert 0 <= r1["final"]["adopters"] <= DEFAULT_BASELINE["market_size"]


def test_event_triggered_steps():
    base = dict(DEFAULT_BASELINE, budget_usd=3000.0)
    plain = run_branch(base, "broad", 3)
    assert plain["events_fired"] == []
    assert plain["final"]["budget_exhausted_tick"] is not None
    rescued = run_branch(base, "broad", 3, events=[
        {"when": "cash_below", "threshold": 500.0,
         "changes": {"budget_usd": 50000.0}}])
    assert len(rescued["events_fired"]) == 1
    assert rescued["events_fired"][0]["event"]["when"] == "cash_below"
    assert rescued["final"]["adopters"] > plain["final"]["adopters"]
    clocked = run_branch(DEFAULT_BASELINE, "niche-first", 3, events=[
        {"at_tick": 5, "changes": {"competitor_strength": 0.9}}])
    assert [e["tick"] for e in clocked["events_fired"]] == [5]
    untriggered = run_branch(DEFAULT_BASELINE, "niche-first", 3, events=[
        {"when": "adopters_above", "threshold": 10 ** 9,
         "changes": {"competitor_strength": 0.9}}])
    assert untriggered["events_fired"] == []
    assert untriggered["outcome_hash"] == \
        run_branch(DEFAULT_BASELINE, "niche-first", 3)["outcome_hash"]


def test_jsonl_trace_format(tmp_path):
    store, world = _world(tmp_path)
    add_branch(store, "org_s", world, BranchCreate(strategy="niche-first"))
    world = store.get_world("org_s", world["world_id"])
    run_world(store, "org_s", world)
    world = store.get_world("org_s", world["world_id"])
    trace = world["branches"][0]["trace"]
    lines = trace.strip().split("\n")
    header = json.loads(lines[0])
    assert header["schema"] == "omnimind.simtrace/1"
    assert header["label"] == "SIMULATED ESTIMATE"
    ticks = [json.loads(l) for l in lines[1:-1]]
    assert all(t["type"] == "tick" for t in ticks)
    assert len(ticks) == DEFAULT_BASELINE["ticks"]
    final = json.loads(lines[-1])
    assert final["type"] == "final" and "adopters" in final
    # trace building is itself deterministic
    assert branch_trace(world, world["branches"][0], world["seed"]) == trace


def test_experiment_endpoint(client, tenant):
    from tests.conftest import hdr
    r = client.post("/v1/worlds", headers=hdr(tenant),
                    json={"name": "exp-world", "seed": 11})
    assert r.status_code == 201, r.text
    wid = r.json()["world_id"]
    r = client.post(f"/v1/worlds/{wid}/experiments", headers=hdr(tenant),
                    json={"strategy": "niche-first",
                          "intervention": {"name": "price cut",
                                           "changes": {"price_usd_month": 15.0}},
                          "metrics": ["adopters", "total_revenue_usd"],
                          "engine": "actors", "actors": 100})
    assert r.status_code == 201, r.text
    exp = r.json()
    assert exp["status"] == "completed" and exp["engine"] == "actors"
    rev = exp["result"]["metrics"]["total_revenue_usd"]
    assert rev["intervention"] < rev["baseline"]  # price cut, same engine
    bad = client.post(f"/v1/worlds/{wid}/experiments", headers=hdr(tenant),
                      json={"strategy": "niche-first",
                            "metrics": ["not_a_metric"]})
    assert bad.status_code in (400, 422)
