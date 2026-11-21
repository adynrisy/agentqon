"""AgentQon — mission management (expansion E).

Naming, pause/resume safety, checkpoints, cloning, dependency
visualization, failure summaries, retention, and deletion — over
mission dicts and the existing Store, with no new tables.

E1 naming: rename_mission() writes the title through
Store.rename_mission(); the title is what every view already shows
(mission_detail, list_missions), so a rename is visible everywhere
the mission appears without a second display path.
E2 pause/resume: pause() uses the existing mission state machine
(running -> paused). resume() runs the caller's reconcile()
callback FIRST — reconcile compares saved state with external
systems and reports what changed — and builds a resume plan that
EXCLUDES irreversible steps already done (the irreversible ledger).
Blindly repeating an irreversible step on resume is refused by
construction: the plan lists skipped steps explicitly.
E3 checkpoints: CheckpointStore creates user-visible checkpoints
(id, label, time, snapshot) and restores them. Snapshots are caller
data (the mission's saved state); the store keeps them in memory
and the caller may persist them as artifacts via the existing
put_artifact mechanism.
E4 cloning: clone_mission() copies a mission's SHAPE (title,
contract, deliverables) into a new mission dict with a fresh id
and fresh state — results, tasks, and history do not carry over.
E5 dependency visualization: dependency_view() renders the DAG as
plain-words lines ("Task B waits for Task A") plus levels, so
dependencies are SHOWN, not just stored.
E6 failure summaries: failure_summary() is one plain-words
paragraph: what failed and why.
E7 retention: RETENTION_POLICY states what is kept and for how
long, in plain words; apply_retention() sorts records into kept
and due-for-removal by age — it never deletes by itself.
E8 deletion: deletion_scope() states the full scope BEFORE any
confirmation; delete_with_confirmation() deletes only when the
caller passes the exact confirmation token for that scope, through
Store.delete_mission() — mission record, tasks, runs, artifacts,
candidates, evaluations, critiques, events, tool invocations,
approvals, usage records, and idempotency keys for the mission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .domain import (MISSION_TRANSITIONS, assert_transition, new_id,
                     now_iso)

# ---------------------------------------------------------------- E7 retention

RETENTION_POLICY = {
    "mission_records": "kept for 12 months after the mission finishes",
    "task_records": "kept for 12 months after the mission finishes",
    "artifacts": "kept for 12 months after the mission finishes",
    "evaluation_records": "kept for 12 months after the mission finishes",
    "usage_records": "kept for 12 months after the mission finishes",
    "event_log": "kept for 90 days after the mission finishes",
    "drafts": "kept until the user deletes them or sends the message",
}


def retention_statement() -> str:
    """What is kept, for how long, stated (E7)."""
    lines = ["How long things are kept:"]
    for what, how_long in RETENTION_POLICY.items():
        lines.append(f"  - {what.replace('_', ' ')}: {how_long}.")
    return "\n".join(lines)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_retention(records: list[dict], kind: str,
                    now: datetime | None = None) -> dict:
    """Sort records into kept vs due-for-removal by age (E7).
    `kind` selects the policy line; unknown kinds keep everything
    (never delete on an unknown rule). Returns the lists; the
    caller performs any removal."""
    limits = {"mission_records": 365, "task_records": 365,
              "artifacts": 365, "evaluation_records": 365,
              "usage_records": 365, "event_log": 90}
    limit_days = limits.get(kind)
    at = now or datetime.now(timezone.utc)
    kept, due = [], []
    for rec in records:
        stamp = _parse_time(rec.get("finished_at") or rec.get("created_at") or "")
        if limit_days is None or stamp is None:
            kept.append(rec)
        elif (at - stamp).days > limit_days:
            due.append(rec)
        else:
            kept.append(rec)
    return {"kept": kept, "due_for_removal": due}


# ---------------------------------------------------------------- E1/E2

def rename_mission(store: Any, tenant_id: str, mission_id: str,
                   title: str) -> dict | None:
    """User-settable mission naming (E1). Blank titles are refused
    — a mission must always have a name to show."""
    title = (title or "").strip()
    if not title:
        raise ValueError("a mission needs a name — a blank name is not accepted")
    if len(title) > 160:
        raise ValueError("a mission name must be at most 160 characters")
    store.rename_mission(tenant_id, mission_id, title)
    return store.get_mission(tenant_id, mission_id)


def pause_mission(mission: dict) -> dict:
    """Pause (E2): running -> paused via the existing state machine.
    Any other state refuses — the machine, not a guess, decides."""
    assert_transition(mission["status"], "paused", MISSION_TRANSITIONS,
                      "mission")
    mission["status"] = "paused"
    return mission


def resume_mission(mission: dict, reconcile: Any,
                   irreversible_done: list[str] | None = None,
                   planned_steps: list[str] | None = None) -> dict:
    """Resume with safety (E2). reconcile() runs FIRST and must
    return a report dict (what changed outside since the pause).
    The resume plan then EXCLUDES every irreversible step already
    done — those steps are listed as skipped, never repeated."""
    if mission["status"] != "paused":
        raise ValueError("only a paused mission can be resumed")
    report = reconcile(mission) if callable(reconcile) else {}
    done = set(irreversible_done or [])
    steps = [s for s in (planned_steps or []) if s not in done]
    skipped = [s for s in (planned_steps or []) if s in done]
    assert_transition(mission["status"], "running", MISSION_TRANSITIONS,
                      "mission")
    mission["status"] = "running"
    return {
        "mission": mission,
        "reconciliation": report,
        "resume_plan": steps,
        "skipped_irreversible_steps": skipped,
        "rule": ("reconciliation ran before continuing, and steps "
                 "already done are skipped, never repeated"),
    }


# ---------------------------------------------------------------- E3 checkpoints

@dataclass
class Checkpoint:
    checkpoint_id: str
    mission_id: str
    label: str
    snapshot: dict
    created_at: str = ""


class CheckpointStore:
    """User-visible checkpoints (E3)."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}

    def create(self, mission_id: str, label: str, snapshot: dict) -> Checkpoint:
        cp = Checkpoint(checkpoint_id=new_id("ckpt"), mission_id=mission_id,
                        label=label, snapshot=dict(snapshot),
                        created_at=now_iso())
        self._checkpoints[cp.checkpoint_id] = cp
        return cp

    def list_for(self, mission_id: str) -> list[dict]:
        """The checkpoints the user sees for a mission."""
        return [{"checkpoint_id": c.checkpoint_id, "label": c.label,
                 "created_at": c.created_at}
                for c in self._checkpoints.values()
                if c.mission_id == mission_id]

    def restore(self, checkpoint_id: str) -> dict:
        """Restore a checkpoint: returns its snapshot. Restoring
        never deletes later checkpoints — the user can go back
        again."""
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            raise KeyError(f"no checkpoint {checkpoint_id!r}")
        return {"checkpoint_id": cp.checkpoint_id,
                "mission_id": cp.mission_id, "label": cp.label,
                "snapshot": dict(cp.snapshot),
                "restored_at": now_iso()}


# ---------------------------------------------------------------- E4/E5/E6

def clone_mission(mission: dict) -> dict:
    """Mission cloning (E4): the mission's SHAPE (title, contract,
    deliverables) starts a new mission. Results, tasks, runs, and
    history do not carry over — the clone is fresh."""
    contract = mission.get("contract", {})
    if isinstance(contract, str):
        import json as _json
        contract = _json.loads(contract)
    return {
        "id": new_id("mis"),
        "title": f"Copy of {mission.get('title', 'mission')}",
        "status": "draft",
        "contract": contract,
        "tasks": [],
        "results": [],
        "cloned_from": mission.get("id", ""),
    }


def dependency_view(tasks: list[dict]) -> dict:
    """Task dependency visualization (E5): plain-words lines plus
    levels (0 = no dependencies, 1 = waits for level 0, …)."""
    by_id = {t["task_id"]: t for t in tasks}
    lines = []
    levels: dict[str, int] = {}

    def level(task_id: str, seen: frozenset) -> int:
        if task_id in levels:
            return levels[task_id]
        if task_id in seen:  # cycle: report level 0, never spin
            return 0
        task = by_id.get(task_id, {})
        deps = [d for d in task.get("dependencies", []) if d in by_id]
        value = 0 if not deps else 1 + max(level(d, seen | {task_id})
                                           for d in deps)
        levels[task_id] = value
        return value

    for task in tasks:
        level(task["task_id"], frozenset())
        deps = task.get("dependencies", [])
        if not deps:
            lines.append(f"Task {task['task_id']} waits for nothing — "
                         f"it can start right away.")
        else:
            for dep in deps:
                lines.append(f"Task {task['task_id']} waits for task "
                             f"{dep} to finish first.")
    return {"lines": lines, "levels": levels,
            "edges": [(t["task_id"], d) for t in tasks
                      for d in t.get("dependencies", [])]}


def failure_summary(mission: dict, failed_tasks: list[dict]) -> str:
    """Failure summary (E6): one plain-words paragraph — what
    failed and why. No codes, no jargon."""
    title = mission.get("title", "this mission")
    if not failed_tasks:
        return (f"{title} failed, but no failed task was recorded — "
                f"the cause is not on record and needs checking.")
    parts = []
    for task in failed_tasks:
        reason = task.get("reason") or task.get("detail") or "no reason recorded"
        parts.append(f"task {task.get('task_id', '?')} failed because {reason}")
    joined = "; ".join(parts)
    return (f"{title} did not finish. What went wrong: {joined}. "
            f"Tasks that finished before the failure are kept; the "
            f"failed work can be tried again from where it stopped.")


# ---------------------------------------------------------------- E8 deletion

DELETION_SCOPE_TABLES = ("mission record", "tasks", "runs", "artifacts",
                         "candidates", "evaluations", "critiques",
                         "events", "tool invocations", "approvals",
                         "usage records", "idempotency keys")


def deletion_scope(mission: dict) -> dict:
    """The deletion scope, stated BEFORE confirmation (E8). The
    confirmation token is derived from the mission id + scope, so
    a confirmation for one mission cannot delete another."""
    scope_text = (f"Deleting mission {mission.get('id', '?')} "
                  f"({mission.get('title', 'mission')}) will also delete "
                  f"everything it kept: "
                  + ", ".join(DELETION_SCOPE_TABLES) + ". "
                  f"Agent records, memories, and other missions are "
                  f"not touched.")
    import hashlib as _hashlib
    token = _hashlib.sha256(
        f"{mission.get('id', '')}|{','.join(DELETION_SCOPE_TABLES)}".encode()
    ).hexdigest()[:16]
    return {"scope_text": scope_text, "confirmation_token": token}


def delete_with_confirmation(store: Any, tenant_id: str, mission: dict,
                             confirmation_token: str) -> dict:
    """Delete a mission including its retained data (E8) — only
    when the caller passes the exact token from deletion_scope().
    A wrong token deletes nothing."""
    scope = deletion_scope(mission)
    if confirmation_token != scope["confirmation_token"]:
        return {"deleted": False,
                "reason": "confirmation did not match the stated scope — "
                          "nothing was deleted"}
    counts = store.delete_mission(tenant_id, mission["id"])
    return {"deleted": True, "counts": counts,
            "scope_text": scope["scope_text"]}
