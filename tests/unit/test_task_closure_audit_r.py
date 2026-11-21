"""Audit R regression tests — bug #3: a mission that reaches a terminal
state must take its open tasks with it.

Live repro (Agent A, mission mis_d17bca2b49af): the run failed in the
research stage; the mission row was 'failed' but its tasks stayed
research='running', critique='queued', synthesis='queued' forever.
"""
import asyncio
import tempfile

import pytest

from app.store import Store
from app.worker import run_job_inline_async


def _mission_with_tasks(store, tenant="org_t"):
    mid = store.create_mission(tenant, "user", "closure test",
                               {"objective": "x", "deliverables": ["y"],
                                "acceptance": ["z"]}, 1000.0)
    mission_id = mid["id"] if isinstance(mid, dict) else mid
    for i, kind in enumerate(("research", "critique", "synthesis")):
        store.create_task(tenant, {
            "task_id": f"tsk_{kind}", "mission_id": mission_id,
            "kind": kind, "status": "queued",
            "title": kind, "description": kind,
            "required_capabilities": [], "output_schema_version": "v1"})
    store.update_task(tenant, "tsk_research", "running")
    store.set_mission_status(tenant, mission_id, "running")
    return mission_id


def _statuses(store, tenant, mission_id):
    return {t["id"]: t["status"] for t in store.list_tasks(tenant, mission_id)}


def test_close_open_tasks_only_touches_open_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        mid = _mission_with_tasks(s)
        s.update_task("org_t", "tsk_critique", "completed")
        n = s.close_open_tasks("org_t", mid, "failed")
        assert n == 2
        st = _statuses(s, "org_t", mid)
        assert st["tsk_research"] == "failed"
        assert st["tsk_synthesis"] == "failed"
        assert st["tsk_critique"] == "completed"  # finished work untouched


class _BoomOrchestrator:
    """An orchestrator whose run dies mid-stage, like an adapter error."""

    async def run_mission(self, tenant_id, mission_id, job_id=None):
        # job_id: accepted (and ignored) since the worker passes the
        # owning job's id — backend-audit B3 repair, 2026-10-22.
        raise RuntimeError("LLM general output contained no deliverable")


def test_inline_failure_closes_tasks_with_the_mission():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        mid = _mission_with_tasks(s)
        job = s.enqueue_job("org_t", mid)
        with pytest.raises(RuntimeError):
            asyncio.run(run_job_inline_async(s, _BoomOrchestrator(), job["id"]))
        assert s.get_mission("org_t", mid)["status"] == "failed"
        st = _statuses(s, "org_t", mid)
        assert st == {"tsk_research": "failed", "tsk_critique": "failed",
                      "tsk_synthesis": "failed"}


def test_mark_mission_failed_closes_tasks():
    from app.worker import _mark_mission_failed
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        mid = _mission_with_tasks(s)
        _mark_mission_failed(s, "org_t", mid)
        assert s.get_mission("org_t", mid)["status"] == "failed"
        assert all(v == "failed" for v in _statuses(s, "org_t", mid).values())
