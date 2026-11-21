"""AgentQon — Scheduled Missions (v15).

Recurring missions: a schedule is a stored mission specification plus
a cadence. The durable-queue worker's tick (app/worker.py — no
external cron anywhere) fires due schedules by doing exactly what a
human does at POST /v1/missions + /run: create the mission (same
contract validation, same task-type resolution) and enqueue a
`mission_run` job. From that moment the mission is an ordinary
mission — the classification gate on live web research, the budget
cap, approvals and the kill switch all apply unchanged, because the
schedule only ever writes the same contract fields a user would.

Cadence model (all times UTC — the platform's clock everywhere):
  interval_hours  {kind, every_hours: 1..720}   — fires every N hours
  daily           {kind, time_of_day: "HH:MM"}  — fires daily at HH:MM
  weekly          {kind, weekday: 0=Mon..6=Sun, time_of_day: "HH:MM"}

Catch-up rule (documented, deliberate): a schedule fires AT MOST ONCE
per tick however many occurrences were missed while the worker was
down, and next_run_at then advances to the first occurrence strictly
after the tick's `now`. Downtime produces one late run, never a storm
of backdated ones.

Time is injectable end to end: tick_schedules(store, now=...) takes
the clock as a parameter, which is what the tests drive — no test
sleeps, and no test waits for a wall-clock boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import (event_envelope, MISSION_BUDGET_CEILING_USD, new_id,
                     now_iso)
from .tasktypes import classify_task


class Cadence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(interval_hours|daily|weekly)$")
    every_hours: Optional[int] = Field(default=None, ge=1, le=720)
    time_of_day: Optional[str] = Field(
        default=None, pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$")
    weekday: Optional[int] = Field(default=None, ge=0, le=6)  # Monday = 0

    @model_validator(mode="after")
    def _fields_for_kind(self):
        if self.kind == "interval_hours" and self.every_hours is None:
            raise ValueError("interval_hours cadence requires every_hours")
        if self.kind in ("daily", "weekly") and self.time_of_day is None:
            raise ValueError(f"{self.kind} cadence requires time_of_day")
        if self.kind == "weekly" and self.weekday is None:
            raise ValueError("weekly cadence requires weekday (0=Monday)")
        return self


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=12000)
    deliverables: list[str] = Field(default_factory=lambda: ["Decision brief"],
                                    min_length=1, max_length=20)
    acceptance: list[str] = Field(
        default_factory=lambda: ["Material claims cite sources"],
        min_length=1, max_length=30)
    task_type: Optional[str] = Field(
        default=None,
        pattern="^(strategy|research|writing|code|plan|qa|general)$")
    data_classification: str = Field(
        default="internal", pattern="^(public|internal|confidential|restricted)$")
    # v21.3: not user-facing — schedules carry the internal ceiling.
    max_cost_usd: float = Field(default=MISSION_BUDGET_CEILING_USD, gt=0, le=1000)
    live_web: Optional[bool] = None  # -> contract.tools.live_web (the gate input)
    project_id: Optional[str] = None
    cadence: Cadence
    enabled: bool = True


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    cadence: Optional[Cadence] = None
    enabled: Optional[bool] = None


def _parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_next_run(cadence: dict, after: datetime) -> datetime:
    """The first cadence occurrence strictly after `after` (UTC)."""
    kind = cadence["kind"]
    if kind == "interval_hours":
        return after + timedelta(hours=int(cadence["every_hours"]))
    hh, mm = (int(p) for p in cadence["time_of_day"].split(":"))
    cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "daily":
        if cand <= after:
            cand += timedelta(days=1)
        return cand
    # weekly
    cand += timedelta(days=(int(cadence["weekday"]) - after.weekday()) % 7)
    if cand <= after:
        cand += timedelta(days=7)
    return cand


def build_schedule(tenant_id: str, created_by: str, body: ScheduleCreate,
                   now: Optional[datetime] = None) -> dict:
    """The stored schedule record. next_run_at is anchored at creation
    (first occurrence after now) — a schedule never fires retroactively
    for the moment it was created."""
    now = now or datetime.now(timezone.utc)
    cadence = body.cadence.model_dump()
    return {
        "schedule_id": new_id("sch"), "tenant_id": tenant_id,
        "created_by": created_by, "title": body.title,
        "mission_spec": {
            "objective": body.objective, "deliverables": body.deliverables,
            "acceptance": body.acceptance, "task_type": body.task_type,
            "data_classification": body.data_classification,
            "max_cost_usd": body.max_cost_usd, "live_web": body.live_web,
            "project_id": body.project_id},
        "cadence": cadence, "enabled": body.enabled,
        "next_run_at": _iso(compute_next_run(cadence, now)),
        "last_run_at": None, "last_mission_id": None, "run_count": 0,
        "created_at": _iso(now), "updated_at": _iso(now),
    }


def schedule_contract(schedule: dict) -> dict:
    """The mission contract a firing creates — field for field what the
    mission endpoint would store for the same request, including the
    creation-time task-type resolution (explicit value wins, else the
    deterministic classifier) and the live_web flag landing in
    tools.live_web, where the classification gate reads it."""
    spec = schedule["mission_spec"]
    return {
        "objective": spec["objective"],
        "deliverables": spec["deliverables"],
        "acceptance": spec["acceptance"],
        "constraints": {"max_cost_usd": spec["max_cost_usd"]},
        "tools": {"allow": ["web.read", "artifact.write", "artifact.read"],
                  "deny": [], "live_web": spec.get("live_web")},
        "approval_policy": {"external_side_effects": "required"},
        "data_classification": spec["data_classification"],
        "task_type": (spec.get("task_type")
                      or classify_task(spec["objective"])),
    }


def fire_schedule(store, schedule: dict, now: datetime) -> Optional[dict]:
    """Create + enqueue one mission from a schedule and advance the
    schedule. The job is an ordinary mission_run job; the worker (or
    the inline API drain) executes it through the normal path.

    Audit B10: the advance is CLAIMED before anything is created —
    the stored row moves to its next occurrence only if its
    next_run_at is still the value this caller read when it decided
    the schedule was due, so two worker processes ticking at the
    same moment cannot both fire it. A lost claim returns None and
    creates nothing. The trade is deliberate: if mission creation
    then fails, this occurrence is skipped (the tick reports the
    error) rather than risk a double firing — a duplicate mission
    spends real money twice; a missed occurrence is visible and the
    cadence continues."""
    tenant_id = schedule["tenant_id"]
    # Catch-up rule: however many occurrences were missed, the next run
    # is the first occurrence strictly after NOW — one late firing,
    # never a storm.
    claimed = dict(schedule)
    claimed["next_run_at"] = _iso(compute_next_run(schedule["cadence"], now))
    claimed["updated_at"] = _iso(now)
    if not store.claim_schedule_advance(tenant_id, schedule["schedule_id"],
                                        schedule["next_run_at"], claimed):
        return None
    contract = schedule_contract(schedule)
    mission = store.create_mission(
        tenant_id, schedule["created_by"], schedule["title"], contract,
        float(contract["constraints"]["max_cost_usd"]),
        project_id=schedule["mission_spec"].get("project_id"))
    store.put_event(tenant_id, event_envelope(
        tenant_id, "mission", mission["id"], "MissionCreated",
        {"title": schedule["title"], "origin": "schedule",
         "schedule_id": schedule["schedule_id"]}))
    job = store.enqueue_job(tenant_id, mission["id"], kind="mission_run")
    claimed["last_run_at"] = _iso(now)
    claimed["last_mission_id"] = mission["id"]
    claimed["run_count"] = int(schedule.get("run_count", 0)) + 1
    store.put_schedule(tenant_id, claimed)
    return {"schedule_id": schedule["schedule_id"], "tenant_id": tenant_id,
            "mission_id": mission["id"], "job_id": job["id"],
            "next_run_at": claimed["next_run_at"]}


def tick_schedules(store, now: Optional[datetime] = None) -> dict:
    """Fire every due, enabled schedule once. Called by the worker
    (drain + main loop); `now` is injectable for tests. A failing
    schedule is reported in `errors` and never blocks the others."""
    now = now or datetime.now(timezone.utc)
    fired, errors = [], []
    for schedule in store.list_all_schedules():
        if not schedule.get("enabled"):
            continue
        try:
            if _parse_iso(schedule["next_run_at"]) <= now:
                result = fire_schedule(store, schedule, now)
                if result is not None:  # None = another tick claimed it (B10)
                    fired.append(result)
        except Exception as exc:  # one bad schedule must not stop the tick
            errors.append({"schedule_id": schedule.get("schedule_id"),
                           "error": str(exc)[:300]})
    return {"fired": fired, "errors": errors}
