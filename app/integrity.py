"""AgentQon — correctness / orchestration integrity (gap-seventhdoc, group A).

Pure, deterministic cores. No model calls, no I/O, stdlib only. These
generalize the structural invariants already in the tree (planner
acyclicity, the jobs_one_active_run partial unique index, the
over-reservation guard) into a runtime monitor — they do not duplicate
those mechanisms, they CHECK their observable consequences.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Optional

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
REQUIRED_ARTIFACT_CHECKS = ("schema_valid", "citations_checked")


def _alert(rule: str, ok: bool, reason: str) -> dict:
    return {"rule": rule, "ok": ok, "reason": reason}


def check_invariants(mission: dict, approved_actions: list[dict],
                     executed_actions: list[dict], artifacts: list[dict],
                     plan_tasks: list[dict] | None = None,
                     active_runs: list[dict] | None = None,
                     spent: float = 0.0, reserved: float = 0.0,
                     ceiling: float = 0.0) -> list[dict]:
    """Runtime invariant monitor. Returns one alert record per rule.

    INV-1: a completed mission has a valid terminal status.
    INV-2: approved actions match executed actions (same set, no extras).
    INV-3: every published artifact passes the required checks.
    INV-PLAN: the plan graph is acyclic (observable consequence of
      planner.assert_acyclic, re-checked here, not re-implemented).
    INV-ONE-RUN: at most one active run.
    INV-RESERVE: spent + reserved never exceeds the ceiling (the
      over-reservation guard's observable consequence).
    """
    alerts = []
    status = mission.get("status", "")
    if status == "completed":
        alerts.append(_alert("INV-1", True,
                             "Mission is marked completed, a valid final state."))
    elif status in TERMINAL_STATUSES:
        alerts.append(_alert("INV-1", True,
                             f"Mission is in final state '{status}'."))
    else:
        alerts.append(_alert("INV-1", False,
                             "Mission claims to be finished but its state is "
                             f"'{status}', which is not a final state."))
    approved = {a.get("action_id") for a in approved_actions}
    executed = {a.get("action_id") for a in executed_actions}
    if executed - approved:
        alerts.append(_alert("INV-2", False,
                             "Actions ran that were never approved: "
                             + ", ".join(sorted(executed - approved)) + "."))
    elif approved - executed:
        alerts.append(_alert("INV-2", False,
                             "Approved actions never ran: "
                             + ", ".join(sorted(approved - executed)) + "."))
    else:
        alerts.append(_alert("INV-2", True,
                             "Every action that ran was approved, and every "
                             "approved action ran."))
    bad = [a.get("artifact_id", "?") for a in artifacts
           if a.get("published") and not all(
               a.get("checks", {}).get(c) for c in REQUIRED_ARTIFACT_CHECKS)]
    if bad:
        alerts.append(_alert("INV-3", False,
                             "Published work is missing required checks: "
                             + ", ".join(bad) + "."))
    else:
        alerts.append(_alert("INV-3", True,
                             "All published work passed the required checks."))
    if plan_tasks is not None:
        try:
            from .planner import assert_acyclic
            from .domain import TaskContract
            objs = []
            for t in plan_tasks:
                if isinstance(t, dict):
                    objs.append(TaskContract(task_id=t["task_id"],
                                             mission_id=t.get("mission_id", ""),
                                             dependencies=t.get("dependencies", [])))
                else:
                    objs.append(t)
            assert_acyclic(objs)
            alerts.append(_alert("INV-PLAN", True, "The plan has no cycles."))
        except Exception as exc:
            alerts.append(_alert("INV-PLAN", False,
                                 f"The plan contains a cycle: {exc}"))
    if active_runs is not None:
        active = [r for r in active_runs if r.get("status") == "active"]
        alerts.append(_alert("INV-ONE-RUN", len(active) <= 1,
                             "Only one run is active." if len(active) <= 1
                             else f"{len(active)} runs are active at once."))
    if ceiling:
        ok = spent + reserved <= ceiling
        alerts.append(_alert("INV-RESERVE", ok,
                             "Spending plus reserved spending stays within "
                             "the ceiling." if ok else
                             "Spending plus reserved spending is over the ceiling."))
    return alerts


def audit_plan_vs_execution(plan_steps: list[dict], tool_calls: list[dict],
                            file_changes: list[str], assignments: dict,
                            deliverables: list[str]) -> dict:
    """Compare the approved plan against what actually happened.

    A plan step may name a tool, files, an agent and a deliverable.
    Steps with no matching evidence are 'silently skipped'; steps whose
    evidence differs (different agent, different tool) are 'changed'.
    """
    called_tools = {c.get("tool") for c in tool_calls}
    skipped, changed, matched = [], [], []
    for step in plan_steps:
        sid = step.get("step_id", "?")
        problems = []
        if step.get("tool") and step["tool"] not in called_tools:
            problems.append(f"tool {step['tool']} was never called")
        if step.get("files") and not any(f in file_changes for f in step["files"]):
            problems.append("none of its files changed")
        if step.get("agent") and assignments.get(sid) not in (None, step["agent"]):
            problems.append(f"ran under agent {assignments.get(sid)} "
                            f"instead of {step['agent']}")
        if step.get("deliverable") and step["deliverable"] not in deliverables:
            problems.append(f"deliverable {step['deliverable']} is missing")
        if not problems:
            matched.append(sid)
        elif step.get("tool") and step["tool"] not in called_tools:
            skipped.append({"step_id": sid, "reason": "; ".join(problems)})
        else:
            changed.append({"step_id": sid, "reason": "; ".join(problems)})
    return {"matched": matched, "skipped": skipped, "changed": changed,
            "findings": [f"Step {s['step_id']} skipped: {s['reason']}" for s in skipped]
            + [f"Step {c['step_id']} changed: {c['reason']}" for c in changed]}


# --- Property-based orchestrator testing (A3) ---------------------------
ORCH_EVENTS = ("approve", "retry", "cancel", "timeout", "agent_fail",
               "complete_step")


def generate_orchestration_sequences(seed: int, count: int = 50,
                                     length: int = 12) -> list[list[str]]:
    """Seeded generator: reproducible sequences of approvals, retries,
    cancellations, timeouts and agent failures. Seeded, so a found bug
    replays exactly."""
    rng = random.Random(seed)
    return [[rng.choice(ORCH_EVENTS) for _ in range(length)]
            for _ in range(count)]


class NaiveOrchestratorModel:
    """Deliberately buggy model: completing an already-completed step
    succeeds silently (the planted bug the generator must find)."""

    def __init__(self):
        self.completed: set[str] = set()
        self.bugs: list[str] = []

    def apply(self, event: str) -> None:
        if event == "complete_step":
            if "step-1" in self.completed:
                self.bugs.append("step-1 completed twice")
            self.completed.add("step-1")


class SafeOrchestratorModel:
    """Correct model: a second completion of the same step is refused."""

    def __init__(self):
        self.completed: set[str] = set()
        self.bugs: list[str] = []
        self.refusals: list[str] = []

    def apply(self, event: str) -> None:
        if event == "complete_step":
            if "step-1" in self.completed:
                self.refusals.append("step-1 already completed; refused")
                return
            self.completed.add("step-1")


def find_orchestration_bugs(sequences: list[list[str]], model_cls) -> list[dict]:
    """Run every sequence through a fresh model; report sequences that
    trigger a bug. Returns [] for a model with no bug."""
    found = []
    for i, seq in enumerate(sequences):
        model = model_cls()
        for event in seq:
            model.apply(event)
        if getattr(model, "bugs", None):
            found.append({"sequence_index": i, "sequence": seq,
                          "bugs": list(model.bugs)})
            break
    return found


# --- Race-condition reproduction lab (A4) -------------------------------
def replay_race(ops: list[dict]) -> dict:
    """Replay simultaneous operations. An op: {actor, kind, target,
    version, value}. kind: edit_file | complete_step | update_state.
    Same-file concurrent edits with different values conflict; a second
    completion of a step conflicts; a state update with a version older
    than the current one is stale and rejected — never silently merged."""
    files: dict[str, dict] = {}
    completed_steps: set[str] = set()
    state_version: dict[str, int] = {}
    applied, conflicts, rejected = [], [], []
    for op in ops:
        kind, target = op.get("kind"), op.get("target")
        if kind == "edit_file":
            cur = files.get(target)
            if cur and cur["actor"] != op["actor"] and cur["value"] != op["value"]:
                conflicts.append({"op": op, "reason":
                                  f"Two writers changed {target} at once with "
                                  "different content."})
                continue
            files[target] = {"actor": op["actor"], "value": op["value"]}
            applied.append(op)
        elif kind == "complete_step":
            if target in completed_steps:
                conflicts.append({"op": op, "reason":
                                  f"Step {target} was completed twice."})
                continue
            completed_steps.add(target)
            applied.append(op)
        elif kind == "update_state":
            cur_v = state_version.get(target, -1)
            if op.get("version", 0) < cur_v:
                rejected.append({"op": op, "reason":
                                 f"State update for {target} is out of order "
                                 f"(version {op.get('version')} after {cur_v})."})
                continue
            state_version[target] = op.get("version", 0)
            applied.append(op)
        else:
            rejected.append({"op": op, "reason": "Unknown operation kind."})
    return {"applied": applied, "conflicts": conflicts, "rejected": rejected}


# --- Completion proof generator (A5) ------------------------------------
def build_proof_map(requirements: list[dict], evidence: dict) -> dict:
    """Link EACH requirement to evidence. evidence[req_id] may carry
    test_results, output_files, validation_results, unresolved_exceptions.
    A polished response alone (only 'response_text') is NEVER proof.
    An unresolved exception leaves the requirement unproven."""
    rows = []
    for req in requirements:
        rid = req["requirement_id"]
        ev = evidence.get(rid, {})
        problems = []
        if ev.get("unresolved_exceptions"):
            problems.append(f"{len(ev['unresolved_exceptions'])} unresolved "
                            "exception(s) remain")
        has_real = bool(ev.get("test_results") or ev.get("output_files")
                        or ev.get("validation_results"))
        if not has_real:
            problems.append("no test results, output files, or validation "
                            "results on record")
        if ev.get("response_text") and not has_real:
            problems.append("a written response alone is not proof")
        rows.append({"requirement_id": rid,
                     "requirement": req.get("text", ""),
                     "proven": not problems,
                     "reason": "Evidence on record covers this requirement."
                     if not problems else "; ".join(problems),
                     "evidence": {k: ev[k] for k in
                                  ("test_results", "output_files",
                                   "validation_results",
                                   "unresolved_exceptions") if k in ev}})
    proven = [r for r in rows if r["proven"]]
    return {"rows": rows, "proven_count": len(proven),
            "total_count": len(rows),
            "verdict": "proven" if rows and len(proven) == len(rows)
            else "not_proven",
            "proof_pack_note": "This map is the per-requirement companion to "
            "the Proof Pack (app/proofpack.py): the pack labels claims from "
            "citations; this map labels requirements from evidence."}
