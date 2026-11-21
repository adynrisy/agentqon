"""AgentQon — durable queue worker (Ch.19; V3: pause + kill switch).

Workers claim jobs from the database queue with a lease and execute
them. Job kinds: `mission_run` and `mission_revision`. Failures retry
with exponential backoff; after max_attempts the job is dead-lettered
and can be replayed from the API.

v15: the same loop also ticks the mission scheduler (app/schedules.py)
— due schedules create their mission + job here, so no external cron
exists anywhere in the system.

v16: the loop also ticks the Telegram outbox (app/telegram.py) —
results of telegram-origin missions are pushed back to their chats
from here, never from the webhook request path.

v17: the same for the WhatsApp outbox (app/whatsapp.py) — window
rules apply there: in-window results push in full, out-of-window
results are alerted by template or held, never dropped.

Semantics worth knowing:
  - a mission cancelled while queued never starts;
  - a mission paused mid-run requeues its job (attempt refunded) and
    waits until a human resumes it — pause is not a failure;
  - the admin kill switch holds ALL execution: claimed jobs are
    requeued untouched until the flag is cleared;
  - with AGENTQON_WORKER_MODE=inline (dev default) the API drains jobs
    itself; external mode runs this file as a separate process.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import traceback

from .config import load_settings
from .orchestrator import MissionCancelled, MissionPaused, Orchestrator
from .store import default_store


def build_orchestrator(store) -> Orchestrator:
    """The worker builds its own vault so BYOK connections can be used by
    background jobs exactly as by inline runs (server-side only)."""
    settings = load_settings()
    vault = None
    try:
        from .vault import Vault
        vault = Vault.from_env(settings.data_dir)
    except Exception as exc:
        # v21.4: never silent — a dead vault means every BYOK job fails
        # downstream with no hint why. The reason belongs in the log.
        print(f"[agentqon-worker] WARNING: credential vault failed to load "
              f"({type(exc).__name__}: {exc}) — BYOK connections are "
              f"unavailable until the vault configuration is fixed",
              flush=True)
        vault = None
    return Orchestrator(store, settings=settings, vault=vault)


HEARTBEAT_INTERVAL_SECONDS = 30.0


async def _run_with_heartbeat(store, job_id: str, owner: str, coro,
                              interval: float = HEARTBEAT_INTERVAL_SECONDS):
    """Execute a claimed job's coroutine while renewing its lease.

    A mission run can take far longer than the 120s claim lease; before
    v21.4 nothing ever called heartbeat_job, so every long job's lease
    expired mid-run and another worker could reclaim and re-execute
    the same mission concurrently. The heartbeat keeps the lease (and
    with it, the fencing token) valid for the run's whole life."""
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except asyncio.TimeoutError:
                store.heartbeat_job(job_id, owner=owner)
        return task.result()
    finally:
        if not task.done():
            task.cancel()


def _dispatch(store, orchestrator: Orchestrator, job: dict):
    """Execute one claimed job's payload. Returns a coroutine result."""
    tenant_id, mission_id = job["tenant_id"], job["mission_id"]
    raw_payload = job.get("payload") or {}
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    if job["kind"] == "mission_revision":
        return orchestrator.revise_candidate(
            tenant_id, mission_id, payload["candidate_id"],
            payload.get("instructions", ""))
    # Audit B3: the executing job names itself, so run_mission can
    # tell its own continuation (retry / lease reclaim) from a
    # foreign run that must be refused.
    return orchestrator.run_mission(tenant_id, mission_id,
                                    job_id=job["id"])


def process_one(store, orchestrator: Orchestrator, owner: str) -> dict | None:
    job = store.claim_job(owner)
    if job is None:
        return None
    tenant_id, mission_id = job["tenant_id"], job["mission_id"]
    if store.get_flag("kill_switch") == "on":
        store.requeue_job(job["id"], 30, refund_attempt=True, owner=owner)
        return {"job_id": job["id"], "result": "kill_switch_hold"}
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None:
        store.complete_job(job["id"], status="failed", owner=owner)
        return {"job_id": job["id"], "result": "mission_missing"}
    if mission["status"] == "cancelled":
        store.complete_job(job["id"], status="cancelled", owner=owner)
        _close_tasks_quietly(store, tenant_id, mission_id, "cancelled")
        _mission_lesson_quietly(store, tenant_id, mission_id, "cancelled",
                                cause="cancelled by the user")
        return {"job_id": job["id"], "result": "cancelled_before_start"}
    if mission["status"] == "paused":
        store.requeue_job(job["id"], 5, refund_attempt=True, owner=owner)
        return {"job_id": job["id"], "result": "paused_waiting"}
    try:
        asyncio.run(_run_with_heartbeat(store, job["id"], owner,
                                        _dispatch(store, orchestrator, job)))
        store.complete_job(job["id"], status="completed", owner=owner)
        return {"job_id": job["id"], "result": "completed"}
    except MissionPaused:
        store.requeue_job(job["id"], 3, refund_attempt=True, owner=owner)
        return {"job_id": job["id"], "result": "paused_mid_run"}
    except MissionCancelled:
        store.complete_job(job["id"], status="cancelled", owner=owner)
        _close_tasks_quietly(store, tenant_id, mission_id, "cancelled")
        _mission_lesson_quietly(store, tenant_id, mission_id, "cancelled",
                                cause="cancelled by the user")
        return {"job_id": job["id"], "result": "cancelled_mid_run"}
    except Exception as exc:
        # Full traceback to the worker log (server-side); the job row
        # stores only the one-line summary (fail_job trims it) because
        # last_error is API-visible to every tenant member.
        traceback.print_exc()
        summary = f"{type(exc).__name__}: {exc}"
        outcome = store.fail_job(job["id"], summary, owner=owner)
        if outcome["status"] == "dead":
            _mark_mission_failed(store, tenant_id, mission_id, error=summary)
        return {"job_id": job["id"], "result": outcome["status"],
                "error": summary[:300]}


def _mission_lesson_quietly(store, tenant_id: str, mission_id: str,
                            outcome: str, cause: str = "") -> None:
    """v22 (R2): a mission that ends writes a structured lesson to
    the memory layer — failures classified from the error, the
    subject the routed model when a route decision exists. Strictly
    best-effort: lesson writing must never break job handling."""
    try:
        from .evolve import classify_failure_text, record_lesson
        subject_type, subject = "mission", mission_id
        try:
            decisions = store.list_route_decisions(tenant_id, mission_id)
            if decisions and decisions[-1].get("model_id"):
                subject_type, subject = "model", decisions[-1]["model_id"]
        except Exception:
            pass
        record_lesson(
            store, tenant_id, subject_type=subject_type, subject=subject,
            outcome=outcome,
            category=("none" if outcome == "completed"
                      else classify_failure_text(cause)),
            cause=cause or f"mission {outcome}",
            evidence_id=mission_id, source_kind="mission",
            source_id=mission_id)
    except Exception:
        pass


def _mark_mission_failed(store, tenant_id: str, mission_id: str,
                         error: str | None = None) -> None:
    marked = False
    try:
        fresh = store.get_mission(tenant_id, mission_id)
        if fresh and fresh["status"] in ("draft", "planned", "running", "awaiting_approval", "paused"):
            store.set_mission_status(tenant_id, mission_id, "failed")
            # Audit R (bug #3): the mission's open tasks close with it —
            # a failed mission must not keep a 'running' research task.
            store.close_open_tasks(tenant_id, mission_id, "failed")
            marked = True
    except Exception:
        pass
    if marked:
        _mission_lesson_quietly(store, tenant_id, mission_id, "failed",
                                cause=error or "mission failed")


def _close_tasks_quietly(store, tenant_id: str, mission_id: str, status: str) -> None:
    try:
        store.close_open_tasks(tenant_id, mission_id, status)
    except Exception:
        pass


async def run_job_inline_async(store, orchestrator: Orchestrator, job_id: str,
                               owner: str = "api-inline") -> dict:
    """Inline execution: claim THE REQUESTED job, run it, surface failures
    to the caller immediately (no backoff). Shares the audit trail with
    the external worker.

    v21.4: the claim is targeted (claim_job_by_id) — the old code
    claimed the oldest queued job in the whole store, so any backlog
    (another mission's job, a schedule job) made inline runs execute
    the wrong job or fail while the requested job sat stranded. The
    kill switch is honoured here too: in the default inline mode this
    IS the worker, and it previously ignored the flag entirely."""
    job = store.claim_job_by_id(owner, job_id)
    if job is None:
        raise RuntimeError("Inline worker could not claim the enqueued job")
    tenant_id, mission_id = job["tenant_id"], job["mission_id"]
    if store.get_flag("kill_switch") == "on":
        store.requeue_job(job_id, 30, refund_attempt=True, owner=owner)
        return {"job_id": job_id, "result": "kill_switch_hold"}
    mission = store.get_mission(tenant_id, mission_id)
    if mission and mission["status"] == "cancelled":
        store.complete_job(job_id, status="cancelled", owner=owner)
        _close_tasks_quietly(store, tenant_id, mission_id, "cancelled")
        _mission_lesson_quietly(store, tenant_id, mission_id, "cancelled",
                                cause="cancelled by the user")
        return {"job_id": job_id, "result": "cancelled_before_start"}
    if mission and mission["status"] == "paused":
        store.requeue_job(job_id, 5, refund_attempt=True, owner=owner)
        return {"job_id": job_id, "result": "paused_waiting"}
    try:
        result = await _run_with_heartbeat(store, job_id, owner,
                                           _dispatch(store, orchestrator, job))
        store.complete_job(job_id, status="completed", owner=owner)
        return {"job_id": job_id, "result": "completed", "detail": result}
    except MissionPaused:
        store.requeue_job(job_id, 3, refund_attempt=True, owner=owner)
        return {"job_id": job_id, "result": "paused_mid_run"}
    except MissionCancelled:
        store.complete_job(job_id, status="cancelled", owner=owner)
        _close_tasks_quietly(store, tenant_id, mission_id, "cancelled")
        _mission_lesson_quietly(store, tenant_id, mission_id, "cancelled",
                                cause="cancelled by the user")
        return {"job_id": job_id, "result": "cancelled_mid_run"}
    except Exception as exc:
        # Summary only in the job row (API-visible); the API layer logs
        # the full exception server-side before answering the client.
        store.fail_job_terminal(job_id, f"{type(exc).__name__}: {exc}",
                                owner=owner)
        _mark_mission_failed(store, tenant_id, mission_id,
                             error=f"{type(exc).__name__}: {exc}")
        raise


def run_job_inline(store, orchestrator: Orchestrator, job_id: str, owner: str = "api-inline") -> dict:
    """Sync wrapper for non-event-loop callers (tests, scripts)."""
    return asyncio.run(run_job_inline_async(store, orchestrator, job_id, owner))


def _tick_schedules(store) -> None:
    """v15: scheduled missions fire from the worker tick — creating the
    mission + job is the schedule module's job; executing it is the
    queue's. A scheduler failure never blocks job processing."""
    try:
        from .schedules import tick_schedules
        outcome = tick_schedules(store)
        for f in outcome["fired"]:
            print(f"[agentqon-worker] schedule {f['schedule_id']} fired "
                  f"mission {f['mission_id']}", flush=True)
        for e in outcome["errors"]:
            print(f"[agentqon-worker] schedule {e['schedule_id']} failed: "
                  f"{e['error']}", flush=True)
    except Exception:
        traceback.print_exc()


def _tick_telegram(store) -> None:
    """v16: push finished telegram-origin missions back to their chats
    (app/telegram.py outbox). A no-op when the channel is not
    configured; a delivery failure never blocks job processing."""
    try:
        from .telegram import tick_telegram_outbox
        outcome = tick_telegram_outbox(store)
        for p in outcome["pushed"]:
            print(f"[agentqon-worker] telegram result pushed for mission "
                  f"{p['mission_id']}", flush=True)
        for e in outcome["errors"]:
            print(f"[agentqon-worker] telegram push for mission "
                  f"{e['mission_id']} failed: {e['error']}", flush=True)
    except Exception:
        traceback.print_exc()


def _tick_whatsapp(store) -> None:
    """v17: push finished whatsapp-origin missions back to their
    numbers (app/whatsapp.py outbox, window-aware). A no-op when the
    channel is not configured; a delivery failure never blocks job
    processing."""
    try:
        from .whatsapp import tick_whatsapp_outbox
        outcome = tick_whatsapp_outbox(store)
        for p in outcome["pushed"]:
            print(f"[agentqon-worker] whatsapp result pushed for mission "
                  f"{p['mission_id']}", flush=True)
        for a in outcome.get("alerted", []):
            print(f"[agentqon-worker] whatsapp template alert sent for "
                  f"mission {a['mission_id']} (window closed)", flush=True)
        for h in outcome.get("held", []):
            print(f"[agentqon-worker] whatsapp result held for mission "
                  f"{h['mission_id']} (window closed, no template)",
                  flush=True)
        for e in outcome["errors"]:
            print(f"[agentqon-worker] whatsapp push for mission "
                  f"{e['mission_id']} failed: {e['error']}", flush=True)
    except Exception:
        traceback.print_exc()


def drain(store, orchestrator: Orchestrator, owner: str, only_job_id: str | None = None,
          max_jobs: int = 50) -> list[dict]:
    _tick_schedules(store)
    results = []
    for _ in range(max_jobs):
        try:
            r = process_one(store, orchestrator, owner)
        except Exception as exc:
            # Transient store errors (SQLite "database is locked" while
            # the API process holds a write) must not kill a drain.
            results.append({"result": "store_error",
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
            break
        if r is None:
            break
        results.append(r)
        if only_job_id and r["job_id"] == only_job_id:
            break
    _tick_telegram(store)
    _tick_whatsapp(store)
    return results


def _poll_once(store, orchestrator: Orchestrator, owner: str) -> bool:
    """One worker-loop iteration. Returns True when a job was processed.

    Audit B (2026-10-12): the loop previously called process_one
    unguarded, so a transient store error — reproduced live as
    sqlite3.OperationalError "database is locked" from claim_job while
    the API process was bulk-writing the catalog on the shared SQLite
    file — terminated the worker process outright, silently stranding
    every queued job until a human restarted it. A queue worker must
    treat store contention as weather: log it, back off one poll
    interval, and claim on a later pass."""
    try:
        _tick_schedules(store)
        _tick_telegram(store)
        _tick_whatsapp(store)
        r = process_one(store, orchestrator, owner)
        if r is not None:
            print(f"[agentqon-worker] {r}", flush=True)
        return r is not None
    except Exception:
        traceback.print_exc()
        return False


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="AgentQon queue worker")
    ap.add_argument("--once", action="store_true", help="drain the queue once and exit")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between polls when idle")
    args = ap.parse_args()
    owner = f"{socket.gethostname()}-{os.getpid()}"
    store = default_store()
    orchestrator = build_orchestrator(store)
    print(f"[agentqon-worker] {owner} ready (backend={'postgres' if store.pg else 'sqlite'})", flush=True)
    if args.once:
        results = drain(store, orchestrator, owner)
        print(f"[agentqon-worker] drained {len(results)} job(s): {results}", flush=True)
        return
    while True:
        if not _poll_once(store, orchestrator, owner):
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
