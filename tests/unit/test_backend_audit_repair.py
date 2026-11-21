"""Backend audit (2026-10-22, AUDIT_BACKEND.md) repair regressions.

Each test pins one finding's auditor repro:
  B1  concurrent approvals execute the parked action twice
      (fix owned by the security repair — verified here against the
      auditor's exact repro shape: a parked shell_run appending to a
      file, counted separately from the follow-up verify call)
  B2  two concurrent drives of one agent session corrupt its record
      (security repair added the `running` guard; its own test only
      covered the sequential case — the barrier-synchronized
      concurrent case is pinned here)
  B3  duplicate mission runs execute twice / cross-cancel tasks
  B4  running a COMPLETED mission mutates plan/tasks, then raises
  B5  self-test concurrent starts defeat one-run-per-tenant
  B6  self-test failed probe-thread start wedges the tenant forever
  B7  a connection wedged in state `validating`
  B9  event ordering has no monotonic tiebreaker
  B10 schedule firing has no cross-process claim
(B8 — the bounded sync-probe tradeoff — is dispositioned in the
repair report; the modeltest discovery offload it called for is
covered indirectly by the B5/B6 tests exercising start_run.)
"""
import asyncio
import tempfile
import threading

import pytest

from app.config import Settings
from app.domain import StateConflict, event_envelope
from app.store import Store

TENANT = "org_backend_audit"
CONTRACT = {"objective": "x", "deliverables": ["y"], "acceptance": ["z"],
            "constraints": {"max_cost_usd": 10.0}}


def _store(tmp):
    return Store(f"{tmp}/t.db", f"{tmp}/artifacts")


def _mission(store, title="m"):
    return store.create_mission(TENANT, "u", title, CONTRACT, 10.0)["id"]


def _tasks(store, mid, statuses=("running", "queued", "queued")):
    for i, kind in enumerate(("research", "critique", "synthesis")):
        store.create_task(TENANT, {
            "task_id": f"tsk_{kind}", "mission_id": mid, "kind": kind,
            "status": statuses[i], "title": kind, "description": kind,
            "required_capabilities": [], "output_schema_version": "v1"})


# ================================================================ B3 (store)
def test_b3_enqueue_dedupe_returns_the_active_job():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s)
        j1 = s.enqueue_job(TENANT, mid, kind="mission_run", dedupe_active=True)
        j2 = s.enqueue_job(TENANT, mid, kind="mission_run", dedupe_active=True)
        assert j2["id"] == j1["id"] and j2.get("existing") is True
        active = [j for j in s.list_jobs(TENANT)
                  if j["status"] in ("queued", "running")]
        assert len(active) == 1
        # a different kind is a different job — never merged
        j3 = s.enqueue_job(TENANT, mid, kind="mission_revision",
                           payload={"candidate_id": "c1"}, dedupe_active=True)
        assert j3["id"] != j1["id"]
        # once the active run finishes, a fresh run may be enqueued
        s.complete_job(j1["id"])
        j4 = s.enqueue_job(TENANT, mid, kind="mission_run", dedupe_active=True)
        assert j4["id"] != j1["id"] and not j4.get("existing")


def test_b3_concurrent_dedupe_enqueues_yield_one_job():
    """Barrier-synchronized: N threads enqueue with dedupe at the
    same instant; exactly one job row may exist afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s)
        barrier = threading.Barrier(6)
        results, errors = [], []

        def enqueue():
            try:
                barrier.wait(timeout=10)
                results.append(s.enqueue_job(
                    TENANT, mid, kind="mission_run", dedupe_active=True)["id"])
            except Exception as exc:  # noqa: BLE001 - collected for assertion
                errors.append(exc)

        threads = [threading.Thread(target=enqueue) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == []
        assert len(set(results)) == 1
        assert len([j for j in s.list_jobs(TENANT)
                    if j["status"] in ("queued", "running")]) == 1


def test_b3_boot_guard_repairs_preexisting_duplicates():
    """A database carrying the pre-fix bug's product (two active
    mission_run jobs for one mission) is repaired at Store boot:
    the running job is kept, the younger duplicate is failed with
    the reason on its row, and the invariant becomes structural."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s)
        # simulate pre-fix data: drop the guard's index, insert dupes
        s._exec("DROP INDEX IF EXISTS jobs_one_active_run_idx")
        j_old = s.enqueue_job(TENANT, mid, kind="mission_run")
        younger = "2999-01-01T00:00:00Z"  # unambiguously after j_old
        s._exec("INSERT INTO jobs (id, tenant_id, mission_id, kind, payload,"
                " status, attempts, max_attempts, available_at, created_at,"
                " updated_at) VALUES ('job_dupe', ?, ?, 'mission_run', '{}',"
                " 'queued', 0, 3, ?, ?, ?)", (TENANT, mid, younger, younger,
                                              younger))
        s2 = _store(tmp)  # second boot over the same database
        dupe = s2.get_job(TENANT, "job_dupe")
        assert dupe["status"] == "failed"
        assert "B3" in (dupe["last_error"] or "")
        kept = s2.get_job(TENANT, j_old["id"])
        assert kept["status"] == "queued"
        # the index now refuses a second active run at the SQL level
        with pytest.raises(Exception):
            s2._exec("INSERT INTO jobs (id, tenant_id, mission_id, kind,"
                     " payload, status, attempts, max_attempts, available_at,"
                     " created_at, updated_at) VALUES ('job_third', ?, ?,"
                     " 'mission_run', '{}', 'queued', 0, 3,"
                     " '2026-01-01T00:00:00Z', '2026-01-01T00:00:02Z',"
                     " '2026-01-01T00:00:02Z')", (TENANT, mid))


def test_b3_replay_refused_while_another_run_is_active():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s)
        active = s.enqueue_job(TENANT, mid, kind="mission_run")
        s._exec("INSERT INTO jobs (id, tenant_id, mission_id, kind, payload,"
                " status, attempts, max_attempts, available_at, created_at,"
                " updated_at) VALUES ('job_dead', ?, ?, 'mission_run', '{}',"
                " 'dead', 3, 3, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',"
                " '2026-01-01T00:00:00Z')", (TENANT, mid))
        # another run owns the mission: replay is a conflict, not a 500
        assert s.replay_job(TENANT, "job_dead") is None
        assert s.get_job(TENANT, "job_dead")["status"] == "dead"
        # with the active run finished, the same replay succeeds
        s.complete_job(active["id"])
        replayed = s.replay_job(TENANT, "job_dead")
        assert replayed is not None and replayed["status"] == "queued"


# ============================================================ B3/B4 (orchestrator)
class _Sentinel(Exception):
    """Raised by the stubbed _prepare_runtime: reaching it proves
    run_mission's validation let the call through."""


def _orchestrator(store, tmp):
    from app.orchestrator import Orchestrator
    orch = Orchestrator(store, Settings(data_dir=tmp))
    calls = []

    def _prep(tid, m):
        calls.append(1)
        raise _Sentinel()

    orch._prepare_runtime = _prep
    return orch, calls


def test_b4_completed_mission_refused_before_any_mutation():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s, "done")
        s.set_mission_status(TENANT, mid, "completed")
        orch, calls = _orchestrator(s, tmp)
        with pytest.raises(StateConflict):
            asyncio.run(orch.run_mission(TENANT, mid))
        # validation precedes even runtime preparation
        assert calls == []
        # and nothing was planned, closed, created, or emitted
        assert s.list_tasks(TENANT, mid) == []
        assert s.list_events(TENANT, mid) == []
        assert s.list_artifacts(TENANT, mid) == []
        assert s.get_mission(TENANT, mid)["status"] == "completed"


def test_b4_cancelled_mission_refused_before_any_mutation():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s, "cancelled")
        s.set_mission_status(TENANT, mid, "cancelled")
        orch, calls = _orchestrator(s, tmp)
        with pytest.raises(StateConflict):
            asyncio.run(orch.run_mission(TENANT, mid))
        assert calls == []
        assert s.list_tasks(TENANT, mid) == []
        assert s.list_events(TENANT, mid) == []


def test_b3_foreign_run_cannot_touch_a_live_run():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s, "live")
        s.set_mission_status(TENANT, mid, "running")
        _tasks(s, mid)
        owner = s.enqueue_job(TENANT, mid, kind="mission_run")
        orch, calls = _orchestrator(s, tmp)
        with pytest.raises(StateConflict):
            asyncio.run(orch.run_mission(TENANT, mid, job_id="job_foreign"))
        assert calls == []  # refused during validation, before prep
        statuses = {t["id"]: t["status"] for t in s.list_tasks(TENANT, mid)}
        assert statuses == {"tsk_research": "running",
                            "tsk_critique": "queued",
                            "tsk_synthesis": "queued"}
        assert s.list_events(TENANT, mid) == []
        assert owner["status"] == "queued"  # the owner's job untouched


def test_b3_owning_job_continuation_passes_validation():
    """Job retry / lease reclaim: the SAME job re-entering a RUNNING
    mission is the legitimate continuation and must not be refused."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = _mission(s, "retry")
        s.set_mission_status(TENANT, mid, "running")
        owner = s.enqueue_job(TENANT, mid, kind="mission_run")
        orch, calls = _orchestrator(s, tmp)
        with pytest.raises(_Sentinel):  # reached runtime preparation
            asyncio.run(orch.run_mission(TENANT, mid, job_id=owner["id"]))
        assert calls == [1]


# ================================================================ B5 / B6
def _tester(tmp, store=None):
    from app.modeltest import ModelSelfTester
    store = store or _store(tmp)
    return ModelSelfTester(store, Settings(data_dir=tmp), object(),
                           client_factory=lambda c, t: None)


def test_b5_concurrent_starts_yield_one_run():
    with tempfile.TemporaryDirectory() as tmp:
        tester = _tester(tmp)

        async def slow_discover(tenant_id, run):
            await asyncio.sleep(0.2)  # the auditor's slowed discovery
            return []

        tester._discover = slow_discover

        async def both():
            return await asyncio.gather(tester.start_run(TENANT),
                                        tester.start_run(TENANT))

        r1, r2 = asyncio.run(both())
        assert r1.run_id == r2.run_id
        assert tester._by_tenant[TENANT] == r1.run_id
        assert len(tester._runs) == 1


async def _no_discover(tenant_id, run):
    return []


def test_b6_thread_start_failure_does_not_wedge_the_tenant(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tester = _tester(tmp)
        tester._discover = _no_discover  # isolate: failure must come
        # from the probe thread's start, nothing else

        def boom(self):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", boom)
        with pytest.raises(RuntimeError):
            asyncio.run(tester.start_run(TENANT))
        monkeypatch.undo()
        retired = list(tester._runs.values())
        assert len(retired) == 1 and retired[0].state == "done"
        assert TENANT not in tester._by_tenant
        # the next start is a genuinely fresh run, not the zombie
        r2 = asyncio.run(tester.start_run(TENANT))
        assert r2.run_id != retired[0].run_id and r2.fresh is True


def test_b6_dead_thread_run_is_retired_on_next_start(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tester = _tester(tmp)
        tester._discover = _no_discover
        # a thread object that exists but never runs (silent death)
        monkeypatch.setattr(threading.Thread, "start", lambda self: None)
        r1 = asyncio.run(tester.start_run(TENANT))
        assert r1.state == "running" and r1.thread is not None
        monkeypatch.undo()
        r2 = asyncio.run(tester.start_run(TENANT))
        assert r2.run_id != r1.run_id
        assert r1.state == "done"  # the corpse was retired, not returned


# ================================================================ B7
class _FakeVault:
    def encrypt(self, plaintext):
        return ("v1", "ct:" + plaintext)

    def decrypt(self, version, ciphertext):
        return ciphertext[3:]


def test_b7_vault_failure_lands_in_terminal_state():
    import app.connections as conn_mod

    class ExplodingVault(_FakeVault):
        def decrypt(self, version, ciphertext):
            raise RuntimeError("vault unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        vault = ExplodingVault()
        conn = conn_mod.create_connection(
            s, vault, TENANT, "alice",
            {"kind": "direct_provider",
             "base_url": "https://api.example.com/v1",
             "api_key": "sk-test", "label": "repro"})
        with pytest.raises(RuntimeError):
            asyncio.run(conn_mod.validate_connection(
                s, vault, TENANT, "alice", conn["id"]))
        after = s.get_connection(TENANT, conn["id"])
        assert after["state"] == "degraded"  # never "validating"
        assert after["last_error_category"] == "validation_error"


def test_b7_missing_secret_maps_to_auth_failed():
    import app.connections as conn_mod

    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        vault = _FakeVault()
        conn = conn_mod.create_connection(
            s, vault, TENANT, "alice",
            {"kind": "direct_provider",
             "base_url": "https://api.example.com/v1",
             "api_key": "sk-test", "label": "key later shredded"})
        s.shred_secret(TENANT, conn["id"])  # the stored secret is gone
        view = asyncio.run(conn_mod.validate_connection(
            s, vault, TENANT, "alice", conn["id"]))
        assert view["state"] == "auth_failed"
        assert s.get_connection(TENANT, conn["id"])["state"] == "auth_failed"


# ================================================================ B9
def test_b9_same_tick_events_keep_insertion_order_and_cursor():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid = "mis_order"
        tick = "2026-01-01T00:00:00Z"
        ids = []
        for i in range(5):
            env = event_envelope(TENANT, "mission", mid, f"E{i}", {"i": i})
            env["occurred_at"] = tick  # every event in the same tick
            ids.append(env["event_id"])
            s.put_event(TENANT, env)
        later = event_envelope(TENANT, "mission", mid, "E5", {"i": 5})
        later["occurred_at"] = "2026-01-01T00:00:01Z"
        s.put_event(TENANT, later)
        got = s.list_events(TENANT, mid)
        assert [e["event_id"] for e in got] == ids + [later["event_id"]]
        # the `after` cursor slices exactly at the anchor
        rest = s.list_events(TENANT, mid, after=ids[1])
        assert [e["event_id"] for e in rest] == ids[2:] + [later["event_id"]]


# ================================================================ B10
def test_b10_schedule_claim_admits_exactly_one_winner():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        sched = {"schedule_id": "sch_claim", "tenant_id": TENANT,
                 "next_run_at": "2026-01-01T09:00:00Z", "enabled": True}
        s.put_schedule(TENANT, sched)
        advanced = dict(sched, next_run_at="2026-01-01T10:00:00Z")
        assert s.claim_schedule_advance(
            TENANT, "sch_claim", "2026-01-01T09:00:00Z", advanced) is True
        # the loser read the same due value before the winner landed
        assert s.claim_schedule_advance(
            TENANT, "sch_claim", "2026-01-01T09:00:00Z", advanced) is False
        # a later, legitimate advance from the NEW value still works
        assert s.claim_schedule_advance(
            TENANT, "sch_claim", "2026-01-01T10:00:00Z",
            dict(sched, next_run_at="2026-01-01T11:00:00Z")) is True


def test_b10_two_ticks_with_the_same_stale_read_fire_once():
    from app.schedules import (ScheduleCreate, build_schedule,
                                fire_schedule)
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        body = ScheduleCreate(
            title="Hourly", objective="Compare launch strategies",
            cadence={"kind": "interval_hours", "every_hours": 1})
        sched = build_schedule(TENANT, "tester", body, now=t0)
        s.put_schedule(TENANT, sched)
        stale = s.get_schedule(TENANT, sched["schedule_id"])  # both ticks read this
        due = t0 + timedelta(hours=2)
        first = fire_schedule(s, dict(stale), due)
        second = fire_schedule(s, dict(stale), due)
        assert first is not None and second is None
        missions = s.list_missions(TENANT)
        assert len(missions) == 1
        after = s.get_schedule(TENANT, sched["schedule_id"])
        assert after["run_count"] == 1
        assert after["last_mission_id"] == first["mission_id"]


# ================================================================ B1 (verify)
_ENTRY = {"price_status": "unknown", "price_in": None, "price_out": None}


def test_b1_concurrent_approvals_execute_the_parked_action_once(
        tmp_path, monkeypatch):
    """The auditor's repro, precisely: a parked shell_run appending
    one line to a file; two concurrent approvals; the append must
    execute exactly once (counted apart from the follow-up verify
    read), the second approver is refused, the file gains one line."""
    import app.agent_loop as agent_loop
    import app.agent_tools as agent_tools

    class ScriptedGateway:
        def __init__(self, script):
            self.script = list(script)

        async def chat_json(self, model, system, user, temperature=0.4):
            item = self.script.pop(0)
            return item, {"input_tokens": 10, "output_tokens": 5,
                          "model": model}

    store = Store(str(tmp_path / "s.db"))
    gateway = ScriptedGateway([
        {"action": "tool", "tool": "shell_run",
         "args": {"argv": ["sh", "-c", "echo line >> counter.txt"]},
         "thought": "append one line"},
        {"action": "tool", "tool": "shell_run",
         "args": {"argv": ["cat", "counter.txt"]}, "thought": "verify"},
        {"action": "done", "summary": "done"},
    ])

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model", _ENTRY, {"id": "c", "kind": "direct"}

    runner = agent_loop.AgentRunner(store, Settings(data_dir=tmp_path),
                                    factory)
    root = tmp_path / "ws"
    session = runner.create_session(TENANT, "u1", "append a line",
                                    workspace_root=str(root))
    parked = asyncio.run(runner.run(TENANT, session["id"]))
    assert parked["status"] == "awaiting_approval"

    executions = []
    original = agent_tools.execute_tool

    async def slow_execute(tool, *a, **kw):
        if tool == "shell_run" and "echo line" in str(a):
            executions.append(1)          # the parked action only
            await asyncio.sleep(0.3)      # the auditor's window
        return await original(tool, *a, **kw)

    monkeypatch.setattr(agent_tools, "execute_tool", slow_execute)

    async def both():
        return await asyncio.gather(
            runner.resolve_approval(TENANT, session["id"], True),
            runner.resolve_approval(TENANT, session["id"], True),
            return_exceptions=True)

    results = asyncio.run(both())
    assert len(executions) == 1
    assert len([r for r in results if isinstance(r, ValueError)]) == 1
    dones = [r for r in results if isinstance(r, dict)]
    assert len(dones) == 1 and dones[0]["status"] == "completed"
    assert (root / "counter.txt").read_text().splitlines() == ["line"]


# ================================================================ B2 (verify)
def test_b2_concurrent_drives_cannot_corrupt_the_record(tmp_path):
    """Barrier-synchronized: drive A is parked INSIDE its first
    model call while drive B attempts to start. B must not drive.
    Single-drive accounting: exactly 3 model calls, 2 action turns,
    steps_used 3, no transcript loss."""
    import app.agent_loop as agent_loop

    class BarrierGateway:
        def __init__(self):
            self.script = [
                {"action": "tool", "tool": "shell_run",
                 "args": {"argv": ["ls"]}, "thought": "one"},
                {"action": "tool", "tool": "shell_run",
                 "args": {"argv": ["pwd"]}, "thought": "two"},
                {"action": "done", "summary": "done"},
            ]
            self.calls = 0
            self.first_inside = threading.Event()
            self.release = threading.Event()

        async def chat_json(self, model, system, user, temperature=0.4):
            self.calls += 1
            if self.calls == 1:
                self.first_inside.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
            item = self.script.pop(0)
            return item, {"input_tokens": 10, "output_tokens": 5,
                          "model": model}

    store = Store(str(tmp_path / "s.db"))
    gateway = BarrierGateway()

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model", _ENTRY, {"id": "c", "kind": "direct"}

    runner = agent_loop.AgentRunner(store, Settings(data_dir=tmp_path),
                                    factory)
    session = runner.create_session(TENANT, "u1", "look around",
                                    workspace_root=str(tmp_path / "ws"),
                                    auto_approve_rw=True)

    async def scenario():
        a = asyncio.ensure_future(runner.run(TENANT, session["id"]))
        while not gateway.first_inside.is_set():
            await asyncio.sleep(0.01)
        b = asyncio.ensure_future(runner.run(TENANT, session["id"]))
        await asyncio.sleep(0.3)  # B's full chance to enter a 2nd drive
        assert gateway.calls == 1  # B never reached the model
        gateway.release.set()
        ra = await a
        rb = await b
        return ra, rb

    ra, rb = asyncio.run(scenario())
    assert rb["status"] == "running"  # B was told a drive owns it
    final = store.get_agent_session(TENANT, session["id"])
    assert gateway.calls == 3
    assert final["status"] == "completed"
    assert final["steps_used"] == 3
    assert len([t for t in final["transcript"]
                if t["role"] == "action"]) == 2
