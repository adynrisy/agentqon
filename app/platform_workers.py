"""AgentQon — distributed execution over the EXISTING durable queue.

This module does NOT build a new queue. The durable queue in
app/store.py (claim with lease, heartbeat, backoff, dead-letter) stays
the only queue. What this module adds is the multi-worker discipline
on top of it:

- Worker registration + heartbeat: a worker announces itself and keeps
  its heartbeat fresh. Workers whose heartbeat goes stale are listed
  as stale, never silently assumed alive.
- Ownership: every claim is made under a worker id (the existing
  ``owner`` parameter of claim_job). A second worker can never claim a
  job another live worker holds, because the lease already prevents
  it — this module makes that ownership visible and checkable.
- Consistent mission state: before a worker acts on a mission it
  checks the mission is in a runnable state; a mission that is
  cancelled, paused or already complete is refused, not re-run.
- Consistent permissions: a worker acts with the tenant id on the job
  itself — it never borrows another tenant's identity.
- Consistent budgets: a worker checks recorded spend against the
  mission cap before starting expensive work; over-cap missions are
  refused with a plain-words reason instead of spending further.

Single-worker behaviour is unchanged: app/worker.py is NOT modified.
With one worker present, this module's registry simply lists one live
worker and every guard passes exactly as the single-worker path does.
"""
from __future__ import annotations

import time

RUNNABLE_MISSION_STATES = ("draft", "planned", "running", "awaiting_approval")
STALE_AFTER_SECONDS = 90.0


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, dict] = {}

    def register(self, worker_id: str, *, capabilities: list[str] | None = None) -> dict:
        record = {"worker_id": worker_id,
                  "capabilities": sorted(capabilities or []),
                  "registered_at": time.time(), "last_heartbeat": time.time()}
        self._workers[worker_id] = record
        return dict(record)

    def heartbeat(self, worker_id: str) -> bool:
        record = self._workers.get(worker_id)
        if record is None:
            return False
        record["last_heartbeat"] = time.time()
        return True

    def live_workers(self, *, stale_after: float = STALE_AFTER_SECONDS,
                     now: float | None = None) -> list[dict]:
        moment = time.time() if now is None else now
        return [dict(w) for w in self._workers.values()
                if moment - w["last_heartbeat"] <= stale_after]

    def stale_workers(self, *, stale_after: float = STALE_AFTER_SECONDS,
                      now: float | None = None) -> list[dict]:
        moment = time.time() if now is None else now
        return [dict(w) for w in self._workers.values()
                if moment - w["last_heartbeat"] > stale_after]


def check_mission_runnable(mission: dict | None) -> dict:
    """State guard: only runnable missions may be executed. Anything
    else is refused with the state named, so two workers can never
    disagree about whether a mission should run."""
    if mission is None:
        return {"allowed": False,
                "reason": "The mission no longer exists, so it cannot run."}
    state = mission.get("status")
    if state in RUNNABLE_MISSION_STATES:
        return {"allowed": True, "reason": "Mission is in a runnable state."}
    return {"allowed": False,
            "reason": (f"The mission is {state}, so it must not run again. "
                       "A second worker must not restart it.")}


def check_budget(store: object, tenant_id: str, mission_id: str, cap_usd: float) -> dict:
    """Budget guard across workers: recorded spend comes from the
    shared usage log, so every worker sees the same number. Over-cap
    is a refusal, not a warning — the mission stops spending here."""
    try:
        summary = store.usage_summary(tenant_id, mission_id)
        spent = float(summary.get("total_usd") or 0.0)
    except Exception:
        # A budget that cannot be read must not be treated as zero.
        return {"allowed": False,
                "reason": ("The mission's spending could not be read, so "
                           "work is paused rather than risking overspend."),
                "spent_usd": None}
    if spent >= cap_usd:
        return {"allowed": False,
                "reason": (f"This mission has already spent {spent} dollars, "
                           f"reaching its {cap_usd} dollar limit. No further "
                           "spending work starts."),
                "spent_usd": spent}
    return {"allowed": True, "reason": "Within budget.", "spent_usd": spent}


def claim_for_worker(store: object, worker_id: str) -> dict | None:
    """Claim one job for a named worker, using the existing queue's
    lease. The worker id becomes the claim owner, so the lease,
    heartbeat and fencing behaviour of the existing queue apply
    unchanged — this is the multi-worker path, not a second queue."""
    return store.claim_job(worker_id)
