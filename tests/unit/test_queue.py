"""Durable queue tests (Ch.19): claim/lease, backoff retries, DLQ, replay."""
import tempfile

from app.store import Store


def make_store(tmp):
    return Store(f"{tmp}/q.db", f"{tmp}/artifacts")


def test_enqueue_claim_complete(tmp_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org_t", "mis_1")
        assert job["status"] == "queued"
        claimed = s.claim_job("worker-1")
        assert claimed["id"] == job["id"] and claimed["attempts"] == 1
        # A second worker cannot claim the same leased job.
        assert s.claim_job("worker-2") is None
        s.complete_job(job["id"])
        assert s.get_job("org_t", job["id"])["status"] == "completed"


def test_failed_job_retries_with_backoff_then_dies():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org_t", "mis_1", max_attempts=2)
        jid = job["id"]
        s.claim_job("w")
        out = s.fail_job(jid, "boom-1", base_backoff_seconds=0.01)
        assert out["status"] == "queued" and out["retry_in_seconds"] > 0
        # Not claimable until the backoff has elapsed.
        assert s.claim_job("w") is None
        # Force it due, claim again, fail again -> attempts exhausted -> dead letter.
        s._exec("UPDATE jobs SET available_at='2000-01-01T00:00:00Z' WHERE id=?", (jid,))
        s.claim_job("w")
        out = s.fail_job(jid, "boom-2")
        assert out["status"] == "dead"
        dead = s.list_jobs("org_t", status="dead")
        assert len(dead) == 1 and "boom-2" in dead[0]["last_error"]


def test_replay_resurrects_dead_job():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org_t", "mis_1", max_attempts=1)
        s.claim_job("w")
        s.fail_job(job["id"], "fatal")
        assert s.get_job("org_t", job["id"])["status"] == "dead"
        replayed = s.replay_job("org_t", job["id"])
        assert replayed["status"] == "queued" and replayed["attempts"] == 0
        # And it can be claimed and completed after replay.
        claimed = s.claim_job("w2")
        assert claimed["id"] == job["id"]
        s.complete_job(job["id"])
        # A completed job cannot be replayed.
        assert s.replay_job("org_t", job["id"]) is None


def test_expired_lease_is_reclaimable():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org_t", "mis_1")
        s.claim_job("crashed-worker", lease_seconds=120)
        # Simulate the worker dying: its lease expires in the past.
        s._exec("UPDATE jobs SET lease_expires='2000-01-01T00:00:00Z' WHERE id=?", (job["id"],))
        reclaimed = s.claim_job("worker-2")
        assert reclaimed is not None and reclaimed["id"] == job["id"]
        assert reclaimed["lease_owner"] == "worker-2"


def test_jobs_are_tenant_scoped():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org_a", "mis_1")
        assert s.get_job("org_b", job["id"]) is None
        assert s.list_jobs("org_b") == []
