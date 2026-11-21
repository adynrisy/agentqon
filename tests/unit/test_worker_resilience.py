"""Worker-loop resilience (audit B, 2026-10-12).

Reproduced live: with AGENTQON_WORKER_MODE=external on SQLite, the API
process bulk-writing the model catalog held the write lock past
sqlite's busy timeout, claim_job raised OperationalError("database is
locked"), and the unguarded worker main loop died — every queued job
stranded until a human restarted the worker. The loop must treat
transient store errors as weather and keep polling.
"""
import sqlite3
import tempfile

from app import worker
from app.store import Store


class _FlakyStore:
    """Wraps a real store; claim_job raises a transient lock error
    for the first `failures` calls, then delegates."""

    def __init__(self, real, failures=1):
        self._real = real
        self._failures = failures

    def claim_job(self, owner):
        if self._failures > 0:
            self._failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return self._real.claim_job(owner)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_poll_once_survives_transient_store_lock():
    with tempfile.TemporaryDirectory() as tmp:
        real = Store(f"{tmp}/w.db", f"{tmp}/artifacts")
        job = real.enqueue_job("org_t", "mis_1")
        store = _FlakyStore(real, failures=1)
        orch = worker.build_orchestrator(real)

        # First pass: the lock error is swallowed (logged), loop lives.
        assert worker._poll_once(store, orch, "w-test") is False
        # The job is still queued — not claimed, not failed, not lost.
        assert real.get_job("org_t", job["id"])["status"] == "queued"

        # Second pass claims it; the mission does not exist, so the job
        # resolves as mission_missing rather than crashing the loop.
        assert worker._poll_once(store, orch, "w-test") is True
        assert real.get_job("org_t", job["id"])["status"] == "failed"


def test_drain_survives_transient_store_lock():
    with tempfile.TemporaryDirectory() as tmp:
        real = Store(f"{tmp}/w.db", f"{tmp}/artifacts")
        real.enqueue_job("org_t", "mis_1")
        store = _FlakyStore(real, failures=1)
        orch = worker.build_orchestrator(real)
        results = worker.drain(store, orch, "w-test")
        assert results and results[0]["result"] == "store_error"
        assert "database is locked" in results[0]["error"]
