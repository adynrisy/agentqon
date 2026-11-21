"""Tenth audit document, Group 3 — distributed execution (stdlib only).

Generalizes gateway pacing, retry classification, worker heartbeats and the
orphan sweeper into coordinated, bounded machinery. Single-node today: the
monitors run over worker timelines and expirations regardless, so the same
code governs a multi-worker future. Mission SSE is the first consumer of
the backpressure controller; future chat streaming rides the same class.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


# ------------------------------------------------ 12: Retry-Storm Prevention
class RetryStormGuard:
    """Spot a flood of simultaneous retries and pace recovery so an outage
    does not get worse. Generalizes gateway pacing + retry classification."""

    def __init__(self, *, window_seconds: float = 60.0, threshold: int = 20,
                 now_fn=None):
        self._window = window_seconds
        self._threshold = threshold
        self._now = now_fn or time.time
        self._events: deque = deque()

    def record_retry(self) -> dict:
        now = self._now()
        self._events.append(now)
        while self._events and now - self._events[0] > self._window:
            self._events.popleft()
        count = len(self._events)
        if count >= self._threshold:
            # Coordinated backoff grows with the flood; concurrency is capped.
            delay = min(30.0, 1.0 + (count - self._threshold) * 0.5)
            return {"storm": True, "retries_in_window": count,
                    "backoff_seconds": delay, "concurrency_limit": 2,
                    "note": "Too many retries at once; recovery is being paced."}
        return {"storm": False, "retries_in_window": count,
                "backoff_seconds": 0.0, "concurrency_limit": None,
                "note": "Retry volume is normal."}


# ----------------------------------------------------- 13: Dependency Bulkheads
class Bulkhead:
    """One broken integration must not exhaust resources or block the rest."""

    def __init__(self, *, limit: int = 5, failure_threshold: int = 3):
        self._limit = limit
        self._failure_threshold = failure_threshold
        self._in_use: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()

    def acquire(self, dependency: str) -> dict:
        if dependency in self._open:
            return {"ok": False, "note": f"{dependency} is isolated after repeated failures."}
        used = self._in_use.get(dependency, 0)
        if used >= self._limit:
            return {"ok": False, "note": f"{dependency} is at its share of capacity."}
        self._in_use[dependency] = used + 1
        return {"ok": True, "note": "Capacity reserved."}

    def release(self, dependency: str, *, failed: bool = False) -> None:
        self._in_use[dependency] = max(0, self._in_use.get(dependency, 0) - 1)
        if failed:
            self._failures[dependency] = self._failures.get(dependency, 0) + 1
            if self._failures[dependency] >= self._failure_threshold:
                self._open.add(dependency)
        else:
            self._failures[dependency] = 0

    def reset(self, dependency: str) -> None:
        self._open.discard(dependency)
        self._failures[dependency] = 0

    def isolated(self, dependency: str) -> bool:
        return dependency in self._open


# --------------------------------------------------- 14: Graceful Load Shedding
CRITICAL_KINDS = ("execution", "cancellation", "recovery", "security")
NONESSENTIAL_KINDS = ("background", "optional_analysis", "report", "cleanup")


def shed_load(jobs: list[dict], *, load: float, threshold: float = 0.85) -> dict:
    """Under overload, defer the nonessential and keep the critical running."""
    if load < threshold:
        return {"shedding": False, "kept": [j["id"] for j in jobs], "deferred": [],
                "note": "Load is normal; nothing deferred."}
    kept, deferred = [], []
    for job in jobs:
        if job["kind"] in CRITICAL_KINDS:
            kept.append(job["id"])
        else:
            deferred.append(job["id"])
    return {"shedding": True, "kept": kept, "deferred": deferred,
            "note": "Overloaded: nonessential work deferred, critical work kept."}


# --------------------------------------- 15: Clock-Skew and Event-Ordering Monitor
class ClockSkewMonitor:
    """Detect inconsistent clocks and out-of-order events across workers."""

    def __init__(self, *, skew_tolerance_seconds: float = 5.0):
        self._tolerance = skew_tolerance_seconds
        self._last_seq: dict[str, int] = {}
        self._skewed: dict[str, float] = {}

    def observe_clock(self, worker: str, *, worker_time: float,
                      reference_time: float) -> dict:
        offset = worker_time - reference_time
        if abs(offset) > self._tolerance:
            self._skewed[worker] = offset
            return {"skewed": True, "offset_seconds": offset,
                    "note": f"{worker}'s clock is off, so its timelines and "
                            "expiry rules cannot be trusted until fixed."}
        self._skewed.pop(worker, None)
        return {"skewed": False, "offset_seconds": offset,
                "note": "Clock agrees with the reference."}

    def observe_event(self, worker: str, seq: int) -> dict:
        last = self._last_seq.get(worker)
        self._last_seq[worker] = max(seq, last if last is not None else seq)
        if last is not None and seq <= last:
            return {"ordered": False, "note": "Events arrived out of order."}
        return {"ordered": True, "note": "Events are in order."}

    @property
    def skewed_workers(self) -> dict:
        return dict(self._skewed)


# ------------------------------------------ 16: Streaming Backpressure Controller
class BackpressureController:
    """Regulate a stream when its consumer is slow: bounded buffer, sequence
    numbers, and an explicit slow-down signal — never silent drops or
    reordered output, and never unbounded memory growth."""

    def __init__(self, *, capacity: int = 100):
        self._capacity = capacity
        self._buffer: deque = deque()
        self._next_seq = 1
        self._delivered_seq = 0

    def push(self, item) -> dict:
        if len(self._buffer) >= self._capacity:
            return {"accepted": False, "signal": "slow_down",
                    "note": "The consumer is behind; the producer must slow down."}
        seq = self._next_seq
        self._next_seq += 1
        self._buffer.append((seq, item))
        return {"accepted": True, "seq": seq, "buffered": len(self._buffer),
                "signal": "ok", "note": "Event queued in order."}

    def drain(self, max_items: int | None = None) -> list:
        out = []
        while self._buffer and (max_items is None or len(out) < max_items):
            seq, item = self._buffer.popleft()
            assert seq == self._delivered_seq + 1, "stream order broken"
            self._delivered_seq = seq
            out.append(item)
        return out

    @property
    def buffered(self) -> int:
        return len(self._buffer)


# ------------------------------------------ 17: Distributed Work-Lease Recovery
@dataclass
class Lease:
    task_id: str
    worker: str
    fencing_token: int
    expires_at: float


class WorkLeaseManager:
    """Time-limited worker ownership of tasks. Generalizes heartbeats + the
    orphan sweeper. A fencing token stops two workers committing conflicting
    results for the same task: only the current token holder may commit."""

    def __init__(self, *, now_fn=None):
        self._now = now_fn or time.time
        self._leases: dict[str, Lease] = {}
        self._counter = 0
        self._committed: set[str] = set()

    def acquire(self, task_id: str, worker: str, *, ttl_seconds: float = 60.0) -> dict:
        existing = self._leases.get(task_id)
        if existing is not None and self._now() < existing.expires_at:
            return {"ok": False, "note": "Another worker still owns this task."}
        self._counter += 1
        lease = Lease(task_id, worker, self._counter, self._now() + ttl_seconds)
        self._leases[task_id] = lease
        return {"ok": True, "fencing_token": lease.fencing_token,
                "note": "Lease granted."}

    def heartbeat(self, task_id: str, worker: str, *, ttl_seconds: float = 60.0) -> dict:
        lease = self._leases.get(task_id)
        if lease is None or lease.worker != worker:
            return {"ok": False, "note": "No lease held by this worker."}
        lease.expires_at = self._now() + ttl_seconds
        return {"ok": True, "note": "Lease renewed."}

    def recover_abandoned(self) -> list[str]:
        now = self._now()
        abandoned = [t for t, l in self._leases.items() if now >= l.expires_at]
        for task_id in abandoned:
            del self._leases[task_id]
        return abandoned

    def commit(self, task_id: str, fencing_token: int) -> dict:
        lease = self._leases.get(task_id)
        if task_id in self._committed:
            return {"ok": False, "note": "This task already has a committed result."}
        if lease is None or lease.fencing_token != fencing_token:
            return {"ok": False,
                    "note": "Lease no longer current; this result must not be committed."}
        self._committed.add(task_id)
        del self._leases[task_id]
        return {"ok": True, "note": "Result committed by the current lease holder."}


# -------------------------------------- 18: Provider Failover Compatibility Gate
def failover_gate(candidate: dict, *, required: dict) -> dict:
    """BEFORE switching providers, check the replacement can actually do the
    job: tool calling, structured outputs, context size, task constraints.
    Failover itself exists; this pre-switch gate did not."""
    missing = []
    if required.get("tool_calling") and not candidate.get("tool_calling"):
        missing.append("tool calling")
    if required.get("structured_outputs") and not candidate.get("structured_outputs"):
        missing.append("structured outputs")
    if candidate.get("context_size", 0) < required.get("min_context_size", 0):
        missing.append("enough context size")
    for constraint in required.get("constraints", []):
        if constraint not in candidate.get("constraints", []):
            missing.append(f"task constraint: {constraint}")
    if missing:
        return {"ok": False, "missing": missing,
                "note": "The replacement provider cannot do everything required; "
                        "switching now would break the task."}
    return {"ok": True, "missing": [],
            "note": "The replacement provider covers everything required."}
