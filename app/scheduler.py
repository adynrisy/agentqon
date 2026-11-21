"""AgentQon — general scheduler policy (gap-security).

The queue's rules in one place, as pure functions over job dicts plus
thin store-backed helpers — NOT one-off sweeper logic scattered across
the worker. The pre-existing orphan sweeper's two jobs are generalized
here: stale-lease reclamation (item 182) and stuck-job detection (item
188) are both just ``sweep_stale_jobs`` with different staleness rules.

Rules:
  - priorities normalize to 0 (low) / 1 (normal) / 2 (high);
  - picking is fair across workspaces: round-robin by tenant, oldest
    first within a tenant, so one busy tenant cannot starve the rest;
  - retries use exponential backoff WITH jitter (no thundering herd);
  - backpressure is signalled in plain words before the queue is
    refused more work;
  - repeatedly failing jobs go to the dead-letter queue (status
    'dead'), are recorded, and the rest of the queue keeps moving;
  - completion is acknowledged, fenced on the lease owner — a stale
    owner cannot ack a job it lost;
  - workers report heartbeats; a heartbeat gone stale means the
    worker is treated as dead and its jobs are reclaimed;
  - a worker whose version is too old is refused plainly, not crashed.
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone

PRIORITY_NAMES = {0: "low", 1: "normal", 2: "high"}
DEFAULT_LEASE_SECONDS = 120
DEFAULT_STALE_AFTER_SECONDS = 300.0


def normalize_priority(value) -> int:
    """Queue-priority normalization: ints clamp to 0..2, names map
    (high/urgent → 2, normal/default → 1, low → 0), anything else is
    normal. No caller can inject priority 99 or -1."""
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return max(0, min(2, value))
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("high", "urgent", "2"):
            return 2
        if lowered in ("low", "0"):
            return 0
        return 1
    return 1


def priority_name(value) -> str:
    return PRIORITY_NAMES[normalize_priority(value)]


def fair_pick(jobs: list[dict], limit: int = 1) -> list[dict]:
    """Fair scheduling across workspaces: group by tenant, sort each
    tenant's jobs by (priority desc, age asc), then deal round-robin
    starting from the tenant with the oldest job. One tenant's flood
    cannot starve another tenant's single job."""
    by_tenant: dict[str, list[dict]] = {}
    for job in jobs:
        by_tenant.setdefault(job.get("tenant_id", ""), []).append(job)
    for tenant_jobs in by_tenant.values():
        tenant_jobs.sort(key=lambda j: (-normalize_priority(j.get("priority", 1)),
                                        j.get("created_at", ""), j.get("id", "")))
    order = sorted(by_tenant, key=lambda t: (
        by_tenant[t][0].get("created_at", "") if by_tenant[t] else ""))
    picked: list[dict] = []
    queues = {t: list(by_tenant[t]) for t in order}
    while len(picked) < limit and any(queues.values()):
        for tenant in order:
            if len(picked) >= limit:
                break
            if queues[tenant]:
                picked.append(queues[tenant].pop(0))
    return picked


def queue_age_report(jobs: list[dict], now_iso: str | None = None,
                     warn_after_seconds: float = 300.0) -> dict:
    """Queue-age monitoring: how old is the oldest waiting job, and
    how many have waited past the warning line — in plain words."""
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        now = datetime.now(timezone.utc)
    ages = []
    for job in jobs:
        try:
            created = datetime.fromisoformat(job.get("created_at", ""))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        ages.append((now - created).total_seconds())
    if not ages:
        return {"waiting": 0, "oldest_seconds": 0, "over_warning": 0,
                "plain": "the queue is empty"}
    oldest = max(ages)
    over = sum(1 for age in ages if age > warn_after_seconds)
    if over:
        plain = (f"{over} job(s) have waited more than "
                 f"{int(warn_after_seconds)} seconds — the oldest has "
                 f"waited {int(oldest)} seconds")
    else:
        plain = f"{len(ages)} job(s) waiting, oldest {int(oldest)} seconds"
    return {"waiting": len(ages), "oldest_seconds": oldest,
            "over_warning": over, "plain": plain}


def retry_delay_with_jitter(attempt: int, base_seconds: float = 2.0,
                            max_seconds: float = 3600.0,
                            rng: random.Random | None = None) -> float:
    """Retry-delay jitter: exponential backoff (base * 2**attempt,
    capped) multiplied by a 0.5–1.0 jitter factor, so a fleet of
    failed jobs does not all retry in the same second."""
    rng = rng or random.Random()
    backoff = min(base_seconds * (2 ** max(0, attempt)), max_seconds)
    return round(backoff * (0.5 + 0.5 * rng.random()), 3)


def backpressure_status(depth: int, capacity: int) -> dict:
    """Backpressure signaling for the 500-missions case: the queue
    degrades gracefully (slow down, then pause new work) with
    plain-words status — it never silently collapses."""
    if depth < capacity * 0.7:
        return {"level": "ok", "accept": True,
                "plain": f"queue is healthy ({depth} waiting)"}
    if depth < capacity:
        return {"level": "degraded", "accept": True,
                "plain": (f"queue is busy ({depth} waiting) — new work "
                          "is still accepted but will wait longer")}
    return {"level": "full", "accept": False,
            "plain": (f"queue is full ({depth} waiting) — new work is "
                      "paused until room opens, nothing is lost")}


def starvation_report(runs_by_tenant: dict[str, float],
                      now: float | None = None,
                      starve_after_seconds: float = 600.0) -> dict:
    """Resource-starvation detection: tenants whose last run is older
    than the threshold while others run are named plainly."""
    now = time.time() if now is None else now
    starved = sorted(t for t, last in runs_by_tenant.items()
                     if (now - last) > starve_after_seconds)
    return {"starved_tenants": starved,
            "plain": ("no tenant is starved" if not starved else
                      f"these workspaces have not run for a while: {', '.join(starved)}")}


def check_worker_version(worker_version: str, current_version: str,
                         min_supported: str) -> dict:
    """Worker-version compatibility: too old is refused plainly, a
    newer-than-server worker is warned (not crashed), current is fine."""
    def _parts(version: str) -> tuple:
        out = []
        for piece in str(version).strip().lstrip("v").split("."):
            try:
                out.append(int(piece))
            except ValueError:
                out.append(0)
        return tuple(out)
    worker, minimum, current = _parts(worker_version), _parts(min_supported), _parts(current_version)
    if worker < minimum:
        return {"compatible": False,
                "plain": (f"worker version {worker_version} is too old — "
                          f"please update to at least {min_supported}")}
    if worker > current:
        return {"compatible": True,
                "plain": (f"worker version {worker_version} is newer than "
                          f"this harness ({current_version}) — it will run, "
                          "but newer features may not work")}
    return {"compatible": True, "plain": f"worker version {worker_version} is supported"}


# ------------------------------------------------------------ heartbeats
class WorkerHeartbeats:
    """Worker heartbeats: every live worker checks in; a heartbeat
    older than ``stale_after_seconds`` means the worker is treated as
    dead (heartbeat timeout handling)."""

    def __init__(self, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS):
        self.stale_after_seconds = stale_after_seconds
        self._beats: dict[str, float] = {}
        self._lock = threading.Lock()

    def beat(self, owner: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._beats[owner] = now

    def is_stale(self, owner: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            last = self._beats.get(owner)
        return last is None or (now - last) > self.stale_after_seconds

    def stale_owners(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        with self._lock:
            return [o for o, last in self._beats.items()
                    if (now - last) > self.stale_after_seconds]


def renew_lease(store, job_id: str, owner: str,
                lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Job-lease renewal, fenced: True only while this owner still
    holds the lease. A lost lease renews nothing (the job may already
    belong to another worker)."""
    try:
        return bool(store.heartbeat_job(job_id, lease_seconds, owner=owner))
    except Exception:
        return False


def sweep_stale_jobs(store, heartbeats: WorkerHeartbeats | None = None,
                     now_iso: str | None = None) -> dict:
    """The GENERAL scheduler sweep (generalizing the old orphan
    sweeper): requeue running jobs whose lease expired (dead worker,
    item 182) AND running jobs whose owner heartbeat went stale even
    if the lease has not expired yet (stuck job, item 188). One
    function, both rules, with a report. Returns counts + plain words."""
    from .store import now_iso as _now_iso
    now_iso = now_iso or _now_iso()
    try:
        rows = store._all(
            "SELECT id, tenant_id, mission_id, lease_owner, lease_expires"
            " FROM jobs WHERE status='running'")
    except Exception:
        return {"reclaimed": 0, "plain": "the queue could not be checked right now"}
    stale_owners = set(heartbeats.stale_owners() if heartbeats else [])
    reclaimed = []
    for row in rows:
        lease_expired = bool(row.get("lease_expires")) and row["lease_expires"] < now_iso
        heartbeat_stale = bool(row.get("lease_owner")) and row["lease_owner"] in stale_owners
        if lease_expired or heartbeat_stale:
            try:
                store.requeue_job(row["id"], 0, owner=None)
                reclaimed.append(row["id"])
            except Exception:
                continue
    return {"reclaimed": len(reclaimed), "job_ids": reclaimed,
            "plain": (f"{len(reclaimed)} job(s) from workers that stopped "
                      "were put back in the queue — nothing was lost"
                      if reclaimed else "no stranded jobs were found")}


def dead_letter_report(store, tenant_id: str) -> dict:
    """Dead-letter queue: repeatedly failing jobs sit here, isolated
    and recorded, while the rest of the queue continues. Plain words."""
    try:
        dead = store.list_jobs(tenant_id, status="dead", limit=200)
    except Exception:
        return {"dead": [], "count": 0, "plain": "the failed-jobs list could not be read"}
    return {"dead": [{"id": j["id"], "mission_id": j.get("mission_id"),
                      "attempts": j.get("attempts"),
                      "reason": j.get("last_error") or "failed repeatedly"}
                     for j in dead],
            "count": len(dead),
            "plain": (f"{len(dead)} job(s) failed repeatedly and are held "
                      "here for review — the rest of the queue keeps running"
                      if dead else "no jobs are held for review")}


def acknowledge_completion(store, job_id: str, owner: str) -> dict:
    """Job-completion acknowledgment, fenced on the lease owner. A
    stale owner gets no ack (and cannot complete a job it lost)."""
    before = store._one("SELECT status, lease_owner FROM jobs WHERE id=?", (job_id,))
    if before is None:
        return {"acked": False, "plain": "that job no longer exists"}
    store.complete_job(job_id, status="completed", owner=owner)
    after = store._one("SELECT status FROM jobs WHERE id=?", (job_id,))
    acked = bool(after) and after["status"] == "completed"
    return {"acked": acked, "job_id": job_id,
            "plain": ("job completion confirmed" if acked else
                      "completion was not confirmed — the job may have "
                      "moved to another worker; it was not marked twice")}
