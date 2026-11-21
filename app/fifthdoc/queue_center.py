"""§14 Queue / workload — visibility, fairness, backpressure, dead letters.

Positions and completion times are ESTIMATES unless reliably known, and
every estimate is labelled as such. Two lanes exist: interactive and
background. Priority aging slowly raises the effective priority of old
waiting jobs so low-priority work cannot starve forever.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

LANES = ("interactive", "background")


@dataclass
class QueueJob:
    job_id: str
    project: str = ""
    provider: str = ""
    model: str = ""
    priority: int = 5  # 1 highest .. 10 lowest
    lane: str = "background"
    deadline_at: float | None = None
    state: str = "waiting"  # waiting | running | done | failed | dead
    created_at: float = field(default_factory=time.time)
    failures: int = 0


class QueueCenter:
    def __init__(self, max_length: int = 1000,
                 concurrency: dict | None = None,
                 aging_per_minute: int = 1):
        self.max_length = max_length
        # concurrency keys: "project:<name>", "provider:<name>", "model:<name>"
        self.concurrency = concurrency or {}
        self.aging_per_minute = aging_per_minute
        self.jobs: dict[str, QueueJob] = {}
        self.paused_lanes: set[str] = set()
        self.dead_letters: list[dict] = []
        self.wait_samples: list[float] = []
        self._shutdown = False

    def effective_priority(self, job: QueueJob, now: float | None = None) -> int:
        now = time.time() if now is None else now
        aged = int((now - job.created_at) // 60) * self.aging_per_minute
        return max(1, job.priority - aged)

    def submit(self, job: QueueJob) -> dict:
        if self._shutdown:
            return {"ok": False, "reason": "Queue is shutting down."}
        waiting = [j for j in self.jobs.values() if j.state == "waiting"]
        if len(waiting) >= self.max_length:
            return {"ok": False, "reason": "Queue is full — admission refused "
                    "(backpressure). Try again later.",
                    "admission": "refused"}
        if job.lane not in LANES:
            return {"ok": False, "reason": f"Unknown lane: {job.lane}"}
        self.jobs[job.job_id] = job
        return {"ok": True, "job_id": job.job_id,
                "position_estimate": self.position(job.job_id)}

    def position(self, job_id: str) -> dict:
        """Estimated position — labelled, never presented as exact."""
        job = self.jobs.get(job_id)
        if job is None:
            return {"known": False}
        waiting = sorted((j for j in self.jobs.values() if j.state == "waiting"),
                         key=lambda j: (self.effective_priority(j), j.created_at))
        ahead = sum(1 for j in waiting
                    if (self.effective_priority(j), j.created_at)
                    < (self.effective_priority(job), job.created_at))
        return {"known": False, "estimate": True,
                "jobs_ahead_estimate": ahead,
                "label": "Estimated position — the queue changes as jobs finish."}

    def next_job(self, lane: str | None = None) -> QueueJob | None:
        """Fair + deadline-aware pick: earliest deadline first, then priority."""
        if lane in self.paused_lanes:
            return None
        waiting = [j for j in self.jobs.values() if j.state == "waiting"
                   and (lane is None or j.lane == lane)]
        if not waiting:
            return None

        def sort_key(job: QueueJob):
            deadline = job.deadline_at if job.deadline_at is not None else float("inf")
            return (deadline, self.effective_priority(job), job.created_at)

        waiting.sort(key=sort_key)
        for job in waiting:
            if self._within_limits(job):
                return job
        return None

    def _within_limits(self, job: QueueJob) -> bool:
        running = [j for j in self.jobs.values() if j.state == "running"]
        for key, value in (("project", job.project), ("provider", job.provider),
                           ("model", job.model)):
            limit = self.concurrency.get(f"{key}:{value}")
            if limit is not None and sum(
                    1 for r in running if getattr(r, key) == value) >= limit:
                return False
        return True

    def start(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "reason": "No such job."}
        job.state = "running"
        self.wait_samples.append(time.time() - job.created_at)
        return {"ok": True, "state": "running"}

    def finish(self, job_id: str, ok: bool = True, rate_limited: bool = False) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "reason": "No such job."}
        if rate_limited:
            job.state = "waiting"  # provider rate limit: back off, requeue
            return {"ok": True, "state": "waiting",
                    "note": "Provider rate limit — job waits and retries later."}
        if ok:
            job.state = "done"
            return {"ok": True, "state": "done"}
        job.failures += 1
        if job.failures >= 3:
            job.state = "dead"
            self.dead_letters.append({"job_id": job_id,
                                      "reason": "Failed three times.",
                                      "inspect_before_requeue": True})
            return {"ok": True, "state": "dead", "dead_letter": True}
        job.state = "waiting"
        return {"ok": True, "state": "waiting", "failures": job.failures}

    def requeue_after_inspection(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None or job.state != "dead":
            return {"ok": False, "reason": "Only dead-letter jobs can be requeued."}
        job.state, job.failures = "waiting", 0
        self.dead_letters = [d for d in self.dead_letters
                             if d["job_id"] != job_id]
        return {"ok": True, "state": "waiting"}

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "reason": "No such job."}
        job.state = "cancelled"
        return {"ok": True, "state": "cancelled"}

    def pause_lane(self, lane: str) -> None:
        self.paused_lanes.add(lane)

    def resume_lane(self, lane: str) -> None:
        self.paused_lanes.discard(lane)

    def graceful_shutdown(self) -> dict:
        self._shutdown = True
        running = [j.job_id for j in self.jobs.values() if j.state == "running"]
        return {"ok": True, "running_jobs_left": running,
                "note": "No new jobs are accepted. Running jobs finish; "
                        "waiting jobs stay queued."}

    def metrics(self) -> dict:
        by_state: dict[str, int] = {}
        for job in self.jobs.values():
            by_state[job.state] = by_state.get(job.state, 0) + 1
        avg_wait = (sum(self.wait_samples) / len(self.wait_samples)
                    if self.wait_samples else 0.0)
        return {"by_state": by_state, "dead_letters": len(self.dead_letters),
                "average_wait_seconds_estimate": round(avg_wait, 2)}
