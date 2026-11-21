"""Scheduled Missions tests (v15): cadence math, the tick firing once
per due schedule with an injected clock (no sleeping), catch-up
without storms, and worker-drain integration."""
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.orchestrator import Orchestrator
from tests.stub_llm import settings as stub_settings
from app.registry import seed_registry
from app.schedules import (Cadence, ScheduleCreate, build_schedule,
                           compute_next_run, tick_schedules)
from app.sources import SEED_SOURCES
from app.store import Store
from app.worker import drain

T = "org_sched"
T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)  # a Thursday


def _store(tmp):
    return Store(f"{tmp}/s.db", f"{tmp}/artifacts")


def _mk(store, tenant=T, now=T0, enabled=True, **kw):
    body = ScheduleCreate(
        title=kw.pop("title", "Morning brief"),
        objective=kw.pop("objective", "Compare launch strategies for an SMB tool"),
        cadence=kw.pop("cadence", {"kind": "interval_hours", "every_hours": 1}),
        enabled=enabled, **kw)
    sched = build_schedule(tenant, "tester", body, now=now)
    store.put_schedule(tenant, sched)
    return sched


def test_cadence_validation():
    with pytest.raises(ValidationError):
        Cadence(kind="interval_hours")  # missing every_hours
    with pytest.raises(ValidationError):
        Cadence(kind="daily")  # missing time_of_day
    with pytest.raises(ValidationError):
        Cadence(kind="weekly", time_of_day="09:00")  # missing weekday
    with pytest.raises(ValidationError):
        Cadence(kind="daily", time_of_day="25:00")
    assert Cadence(kind="weekly", time_of_day="09:00", weekday=0).weekday == 0


def test_compute_next_run():
    every6 = {"kind": "interval_hours", "every_hours": 6}
    assert compute_next_run(every6, T0) == T0 + timedelta(hours=6)
    daily = {"kind": "daily", "time_of_day": "09:30"}
    assert compute_next_run(daily, T0) == datetime(2026, 1, 1, 9, 30,
                                                   tzinfo=timezone.utc)
    assert compute_next_run(daily, datetime(2026, 1, 1, 10, 0,
                                            tzinfo=timezone.utc)) == \
        datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc)
    weekly_mon = {"kind": "weekly", "weekday": 0, "time_of_day": "09:00"}
    # Thu Jan 1 -> Mon Jan 5
    assert compute_next_run(weekly_mon, T0) == datetime(2026, 1, 5, 9, 0,
                                                        tzinfo=timezone.utc)
    # Mon Jan 5 after 09:00 -> Mon Jan 12
    assert compute_next_run(weekly_mon, datetime(2026, 1, 5, 10, 0,
                                                 tzinfo=timezone.utc)) == \
        datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)


def test_tick_fires_once_and_advances():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        sched = _mk(s, live_web=True, data_classification="confidential")
        out = tick_schedules(s, now=T0 + timedelta(minutes=30))
        assert out["fired"] == [] and out["errors"] == []
        out = tick_schedules(s, now=T0 + timedelta(hours=2))
        assert len(out["fired"]) == 1 and out["errors"] == []
        fired = out["fired"][0]
        assert fired["schedule_id"] == sched["schedule_id"]
        after = s.get_schedule(T, sched["schedule_id"])
        assert after["run_count"] == 1
        assert after["last_mission_id"] == fired["mission_id"]
        assert after["next_run_at"] > (T0 + timedelta(hours=2)).isoformat()
        # the mission is a normal mission: contract resolved like the
        # endpoint would (task type classified, flags preserved)
        mission = s.get_mission(T, fired["mission_id"])
        assert mission["status"] == "draft"
        assert mission["contract"]["task_type"] == "strategy"
        assert mission["contract"]["tools"]["live_web"] is True
        assert mission["contract"]["data_classification"] == "confidential"
        jobs = [j for j in s.list_jobs(T) if j["mission_id"] == fired["mission_id"]]
        assert len(jobs) == 1 and jobs[0]["kind"] == "mission_run"
        # same tick instant again: nothing more fires
        assert tick_schedules(s, now=T0 + timedelta(hours=2))["fired"] == []
        # long downtime: exactly ONE catch-up firing, never a storm
        out = tick_schedules(s, now=T0 + timedelta(hours=30))
        assert len(out["fired"]) == 1
        assert s.get_schedule(T, sched["schedule_id"])["run_count"] == 2


def test_disabled_schedule_never_fires():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        _mk(s, enabled=False)
        out = tick_schedules(s, now=T0 + timedelta(days=7))
        assert out["fired"] == []


def test_worker_drain_fires_and_runs_scheduled_mission():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        seed_registry(s, T)
        for src in SEED_SOURCES:
            s.put_source(T, src.model_dump())
        sched = _mk(s)
        # make it due by rewinding next_run_at into the past
        sched["next_run_at"] = "2025-12-31T00:00:00Z"
        s.put_schedule(T, sched)
        results = drain(s, Orchestrator(s, settings=stub_settings()), "worker-test")
        assert any(r["result"] == "completed" for r in results), results
        after = s.get_schedule(T, sched["schedule_id"])
        assert after["run_count"] == 1 and after["last_mission_id"]
        mission = s.get_mission(T, after["last_mission_id"])
        assert mission["status"] == "completed"
        events = s.list_events(T, mission["id"])
        created = next(e for e in events if e["event_type"] == "MissionCreated")
        assert created["payload"]["origin"] == "schedule"
