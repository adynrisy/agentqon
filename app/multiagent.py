"""AgentQon — multi-agent scheduling, dedup, debate, acceptance, tool
filtering, and tool-reliability tracking (W5 gaps 1–6, §5-89/94/95/97).

Stdlib-only. The scheduler runs task callables; it does not call the
model itself, so it can be tested with plain functions and wired to
the real runner by the merger without changing its rules.

W5-1 scheduler rules:
  * A task runs only after every dependency has COMPLETED (a failed
    dependency blocks its dependents — they are reported blocked, not
    silently skipped; §5-95).
  * Independent tasks run concurrently up to max_concurrency (§5-89).
  * Budget and approval are hooks, not re-implementations: the caller
    passes budget_ok(task) / approval_ok(task) callables that consult
    the existing machinery (Store.reserve_budget, the approval gate).
    When a hook says no, the task waits and the reason is recorded.
  * Every dispatch records WHY the task ran when it did
    (dispatch_log entries, plain words).
  * Stop propagation (§5-94): stop_task() marks a task stopped and
    every dependent blocked, with the reason recorded.

W5-2 duplicate-work detection: a task's signature is its kind plus
its normalized description plus its sorted input artifacts plus its
sorted acceptance-criterion ids. Only results recorded as VERIFIED
are ever reused; unverified results are stored nowhere reusable.
A reuse returns the original result plus a reuse record for the
mission record.

W5-3 debate controls: max rounds, evidence required before a claim
stands, stopping conditions (max rounds reached; no new evidence in
a round; two quiet rounds in a row meaning further discussion is
unlikely to improve the decision; evidence-backed consensus),
escalation when conflicts survive all rounds. Votes are tallied for
information only — voting alone is NEVER treated as truth. Conflicts
with no deciding evidence are recorded as remaining uncertainty.

W5-4 acceptance criteria: criteria come from the plan (see
criteria_from_plan()), each evaluated against the task output with
a pass/fail and a plain-words detail. The producing agent's own
claim about its output is never an input to the check.

W5-5 tool filtering: ROLE_TOOL_ALLOWLIST maps a task role to the
session tools it may use. enforce_tool() refuses anything outside
the list at execution time (the runner calls it before dispatching
a tool call — a prompt suggestion alone is not enforcement).

W5-6 reliability: ToolReliability records every call (outcome,
duration, retries) and reports success rate, timeout rate, average
duration, error frequency, and retry count per tool. The scheduler
asks it for a scheduling note per task so a failing tool is visible
in scheduling decisions — and distinguishable from a failing model
(the note names the TOOL, and a model failure is recorded under the
model's name, never folded into a tool's stats).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .domain import new_id, now_iso

# ---------------------------------------------------------------- task spec

@dataclass
class TaskSpec:
    task_id: str
    mission_id: str = ""
    kind: str = "general"
    description: str = ""
    dependencies: list = field(default_factory=list)
    input_artifact_ids: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)  # criterion dicts
    role: str = "researcher"
    agent_id: str = ""
    primary_tool: str = ""  # tool whose reliability matters most for this task


# ---------------------------------------------------------------- W5-5 tool filtering

ROLE_TOOL_ALLOWLIST: dict[str, tuple] = {
    # A researcher reads and searches; it never deploys, never writes.
    "researcher": ("fs_read", "fs_list", "fs_search", "web_read"),
    # A reviewer reads only — no write tools it should not use, no shell.
    "reviewer": ("fs_read", "fs_list", "web_read"),
    # A writer reads and writes files, but never runs shell commands.
    "writer": ("fs_read", "fs_write", "fs_edit", "fs_list", "fs_search"),
    # A deployer is the only role with shell execution (deployment).
    "deployer": ("fs_read", "fs_write", "fs_edit", "fs_list", "fs_search",
                 "shell_run", "web_read"),
    # Unknown roles get read-only: fail toward asking, never toward power.
    "default": ("fs_read", "fs_list", "fs_search", "web_read"),
}


def allowed_tools_for(role: str) -> tuple:
    return ROLE_TOOL_ALLOWLIST.get(role, ROLE_TOOL_ALLOWLIST["default"])


def enforce_tool(tool: str, allowed: tuple) -> tuple[bool, str]:
    """Enforced at execution (W5-5): (True, '') when allowed, else
    (False, plain-words refusal). The runner must refuse the call —
    returning False here and running anyway would be a prompt-only
    filter, which this gap forbids."""
    if tool in allowed:
        return True, ""
    return False, (f"tool {tool!r} is not allowed for this task — the "
                   f"task's tool list does not include it, so the call "
                   f"is refused rather than run")


# ---------------------------------------------------------------- W5-6 reliability

@dataclass
class ToolStats:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    cancelled: int = 0
    total_duration_ms: float = 0.0
    retries: int = 0

    def view(self) -> dict:
        calls = max(1, self.calls)
        return {
            "calls": self.calls,
            "success_rate": round(self.successes / calls, 4),
            "timeout_rate": round(self.timeouts / calls, 4),
            "failure_rate": round(self.failures / calls, 4),
            "average_duration_ms": round(self.total_duration_ms / calls, 3),
            "error_frequency": self.failures + self.timeouts,
            "retries": self.retries,
        }


class ToolReliability:
    """Per-tool reliability ledger (W5-6). Outcomes use the shared
    vocabulary: success | failure | timeout | cancelled. A timeout
    counts toward the timeout rate, never toward failure proof."""

    def __init__(self) -> None:
        self._stats: dict[str, ToolStats] = {}
        self._lock = threading.Lock()

    def record(self, tool: str, outcome: str, duration_ms: float = 0.0,
               retries: int = 0) -> None:
        with self._lock:
            st = self._stats.setdefault(tool, ToolStats())
            st.calls += 1
            st.total_duration_ms += max(0.0, duration_ms)
            st.retries += max(0, retries)
            if outcome == "success":
                st.successes += 1
            elif outcome == "failure":
                st.failures += 1
            elif outcome == "timeout":
                st.timeouts += 1
            elif outcome == "cancelled":
                st.cancelled += 1

    def view(self, tool: str) -> dict:
        return self._stats.get(tool, ToolStats()).view()

    def mission_record(self) -> dict:
        """The reliability view that rides on the mission record."""
        return {tool: st.view() for tool, st in sorted(self._stats.items())}

    def scheduling_note(self, task: TaskSpec) -> str:
        """Plain-words note for scheduling decisions. Empty when the
        task's primary tool looks healthy; names the failing TOOL
        (never the model) when it does not."""
        if not task.primary_tool:
            return ""
        view = self.view(task.primary_tool)
        if view["calls"] >= 5 and view["success_rate"] < 0.5:
            return (f"tool {task.primary_tool} has succeeded in only "
                    f"{view['success_rate']:.0%} of its last "
                    f"{view['calls']} uses — the task still runs, but "
                    f"its result should be checked before it is trusted")
        if view["calls"] >= 5 and view["timeout_rate"] >= 0.5:
            return (f"tool {task.primary_tool} timed out in "
                    f"{view['timeout_rate']:.0%} of its last "
                    f"{view['calls']} uses — allow extra time for this task")
        return ""


# ---------------------------------------------------------------- W5-2 duplicate work

def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def task_signature(task: TaskSpec) -> str:
    """Stable signature for duplicate detection: kind + normalized
    description + sorted inputs + sorted criterion ids. Two tasks
    with the same signature would do substantially the same work."""
    payload = {
        "kind": task.kind,
        "description": _normalize_text(task.description),
        "inputs": sorted(task.input_artifact_ids),
        "criteria": sorted(str(c.get("id", "")) for c in task.acceptance_criteria),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()


class VerifiedResultStore:
    """Reuse store (W5-2). Only VERIFIED results are stored; an
    unverified result is never stored and therefore never reused.
    find_reuse() returns (result, reuse_record) or (None, None)."""

    def __init__(self) -> None:
        self._by_signature: dict[str, dict] = {}

    def record_verified(self, task: TaskSpec, result: Any,
                        verified_by: str) -> bool:
        """Store a result as verified. Returns False (and stores
        nothing) when the caller does not vouch verification."""
        if not verified_by:
            return False
        self._by_signature[task_signature(task)] = {
            "task_id": task.task_id,
            "mission_id": task.mission_id,
            "result": result,
            "verified_by": verified_by,
            "verified_at": now_iso(),
        }
        return True

    def find_reuse(self, task: TaskSpec) -> tuple[Any, dict | None]:
        entry = self._by_signature.get(task_signature(task))
        if entry is None:
            return None, None
        record = {
            "reused_from_task_id": entry["task_id"],
            "verified_by": entry["verified_by"],
            "verified_at": entry["verified_at"],
            "reason": ("this task would repeat work already done and "
                       "verified, so the verified result is reused "
                       "instead of doing the work again"),
        }
        return entry["result"], record


# ---------------------------------------------------------------- W5-4 acceptance criteria

def _has_claims(output: Any) -> bool:
    if isinstance(output, dict):
        claims = output.get("claims", output.get("candidates", []))
        return isinstance(claims, list) and len(claims) >= 1
    return False


def _claims_have_sources(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    claims = output.get("claims", [])
    if not claims:
        return False
    return all(isinstance(c, dict) and c.get("source_ids") for c in claims)


def _critique_targets_claim(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return bool(output.get("target_claim") or output.get("target_candidate_id"))


def _critique_has_evidence(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return bool(output.get("evidence_or_test") or output.get("evidence"))


def _two_distinct_candidates(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    cands = output.get("candidates", [])
    if len(cands) < 2:
        return False
    strategies = {c.get("strategy") for c in cands if isinstance(c, dict)}
    return len(strategies) >= 2


def _evaluation_record_present(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return bool(output.get("evaluation") or output.get("evaluation_record"))


# Plan test names (app/planner.py acceptance_tests) -> plain-words
# criteria with real checks. Criteria come from the PLAN, never from
# the producing agent's own claim about its output.
PLAN_CRITERIA: dict[str, tuple[str, Callable]] = {
    "has_claims": ("the output contains at least one claim", _has_claims),
    "claims_have_sources": ("every claim names its sources", _claims_have_sources),
    "schema_valid": ("the output matches its declared shape", lambda o: isinstance(o, dict) and bool(o)),
    "critique_targets_claim": ("the critique names the claim it criticises", _critique_targets_claim),
    "critique_has_evidence_or_test": ("the critique gives evidence or a test", _critique_has_evidence),
    "two_distinct_candidates": ("the output holds two candidates with different approaches", _two_distinct_candidates),
    "evaluation_record_present": ("an evaluation record is attached", _evaluation_record_present),
}


def criteria_from_plan(acceptance_tests: list[str]) -> list[dict]:
    """Build explicit criteria from the plan's acceptance test names.
    Unknown test names become a criterion the checker reports as
    'no check defined' (failed honestly) rather than silently passed."""
    out = []
    for name in acceptance_tests:
        if name in PLAN_CRITERIA:
            description, _check = PLAN_CRITERIA[name]
            out.append({"id": name, "description": description})
        else:
            out.append({"id": name,
                        "description": f"plan test {name} (no check defined)",
                        "no_check": True})
    return out


def evaluate_criteria(criteria: list[dict], output: Any) -> dict:
    """Evaluate output against criteria (W5-4). Returns per-criterion
    pass/fail with plain-words details plus an overall verdict. The
    producing agent's own claim is never consulted."""
    results = []
    for crit in criteria:
        cid = crit.get("id", "")
        description = crit.get("description", cid)
        if crit.get("no_check"):
            results.append({"id": cid, "description": description,
                            "passed": False,
                            "detail": "no check is defined for this "
                                      "criterion, so it cannot pass"})
            continue
        check = PLAN_CRITERIA.get(cid, (None, None))[1]
        if check is None and "check" in crit and callable(crit["check"]):
            check = crit["check"]
        if check is None:
            results.append({"id": cid, "description": description,
                            "passed": False,
                            "detail": "no check is defined for this "
                                      "criterion, so it cannot pass"})
            continue
        try:
            passed = bool(check(output))
        except Exception:
            passed = False
        results.append({
            "id": cid, "description": description, "passed": passed,
            "detail": ("criterion met" if passed
                       else "criterion not met by the output"),
        })
    overall = all(r["passed"] for r in results) if results else False
    return {"criteria": results, "passed": overall,
            "summary": ("all acceptance criteria met" if overall
                        else "some acceptance criteria were not met")}


# ---------------------------------------------------------------- W5-3 debate

@dataclass
class DebateClaim:
    claim_id: str
    agent_id: str
    text: str
    evidence: list = field(default_factory=list)  # evidence references


class Debate:
    """Formal panel debate (W5-3).

    Rules: at most `max_rounds` rounds. A claim STANDS only when it
    carries evidence (evidence_required). Each round, claims gain
    evidence or lose standing; contradictions flagged by the caller
    (claim-id pairs) resolve only when one side has evidence and the
    other does not — otherwise both stay as remaining uncertainty.
    Votes are tallied for information and NEVER decide the verdict.
    """

    def __init__(self, max_rounds: int = 3, evidence_required: bool = True):
        self.max_rounds = max(1, max_rounds)
        self.evidence_required = evidence_required

    def run(self, claims: list[DebateClaim],
            contradictions: list[tuple[str, str]] | None = None,
            votes: dict[str, int] | None = None,
            rounds_of_evidence: list[dict[str, list]] | None = None) -> dict:
        """rounds_of_evidence: per round, {claim_id: [new evidence]}.
        A round with no new evidence anywhere is a quiet round; two
        quiet rounds in a row stop the debate early ('further
        discussion is unlikely to improve the decision')."""
        contradictions = contradictions or []
        votes = votes or {}
        rounds_of_evidence = rounds_of_evidence or []
        by_id = {c.claim_id: c for c in claims}
        evidence: dict[str, list] = {c.claim_id: list(c.evidence) for c in claims}
        standing: dict[str, bool] = {}
        quiet_rounds = 0
        rounds_used = 0
        stop_reason = ""
        for round_no in range(1, self.max_rounds + 1):
            rounds_used = round_no
            new_evidence = rounds_of_evidence[round_no - 1] \
                if round_no - 1 < len(rounds_of_evidence) else {}
            added_any = False
            for cid, items in new_evidence.items():
                if cid in evidence and items:
                    evidence[cid].extend(items)
                    added_any = True
            for cid in evidence:
                standing[cid] = bool(evidence[cid]) if self.evidence_required else True
            if not added_any:
                quiet_rounds += 1
            else:
                quiet_rounds = 0
            # Evidence-backed consensus: every claim standing, no
            # unresolved contradiction -> stop early, decision made.
            unresolved = self._unresolved(contradictions, evidence)
            if all(standing.values()) and not unresolved and claims:
                stop_reason = ("all claims carry evidence and no "
                               "contradiction remains unresolved")
                break
            if quiet_rounds >= 2:
                stop_reason = ("further discussion is unlikely to "
                               "improve the decision: two rounds in a "
                               "row added no new evidence")
                break
        else:
            stop_reason = "maximum rounds reached"
        if not stop_reason:
            stop_reason = "maximum rounds reached"
        unresolved = self._unresolved(contradictions, evidence)
        standing_claims = [cid for cid, ok in standing.items() if ok]
        escalated = bool(unresolved)
        uncertainty = []
        for a, b in unresolved:
            uncertainty.append({
                "claims": [a, b],
                "status": "remaining uncertainty",
                "detail": ("the two claims conflict and neither side "
                           "has deciding evidence, so the conflict is "
                           "recorded as unresolved rather than settled "
                           "by a vote"),
            })
        return {
            "rounds_used": rounds_used,
            "max_rounds": self.max_rounds,
            "standing_claims": standing_claims,
            "claims_without_evidence": [cid for cid, ok in standing.items()
                                        if not ok],
            "stop_reason": stop_reason,
            "escalated": escalated,
            "escalation_reason": ("conflicting claims remain after all "
                                  "rounds; a person must decide"
                                  if escalated else ""),
            "remaining_uncertainty": uncertainty,
            "vote_tally": dict(votes),
            "vote_note": ("votes are recorded for information only and "
                          "never decide the outcome"),
        }

    @staticmethod
    def _unresolved(contradictions: list[tuple[str, str]],
                    evidence: dict[str, list]) -> list[tuple[str, str]]:
        out = []
        for a, b in contradictions:
            a_ev, b_ev = bool(evidence.get(a)), bool(evidence.get(b))
            if a_ev == b_ev:
                # Both evidenced or both unevidenced: no deciding evidence.
                out.append((a, b))
        return out


# ---------------------------------------------------------------- W5-1 scheduler

class Scheduler:
    """Dependency-aware scheduler with parallel execution (W5-1).

    runner(task) -> result dict (any dict; outcome read from
    result.get('outcome') or 'success' when the call returns without
    raising). budget_ok / approval_ok are hooks into the EXISTING
    machinery; a hook returning False holds the task with the reason
    recorded, it never invents a budget or approval system.
    """

    def __init__(self, max_concurrency: int = 3,
                 reliability: ToolReliability | None = None,
                 budget_ok: Callable[[TaskSpec], bool] | None = None,
                 approval_ok: Callable[[TaskSpec], bool] | None = None):
        self.max_concurrency = max(1, max_concurrency)
        self.reliability = reliability
        self.budget_ok = budget_ok
        self.approval_ok = approval_ok
        self.dispatch_log: list[dict] = []
        self._stopped: set[str] = set()
        self._stop_reason: dict[str, str] = {}

    def stop_task(self, task_id: str, reason: str,
                  dependents: dict[str, list[str]] | None = None) -> list[str]:
        """Stop propagation (§5-94): the task and, when the caller
        supplies the dependency map, every dependent is blocked with
        the reason recorded. Returns the blocked dependent ids."""
        self._stopped.add(task_id)
        self._stop_reason[task_id] = reason
        blocked: list[str] = []
        if dependents:
            stack = list(dependents.get(task_id, []))
            while stack:
                dep = stack.pop()
                if dep not in self._stopped:
                    self._stopped.add(dep)
                    self._stop_reason[dep] = (
                        f"blocked because {task_id} stopped: {reason}")
                    blocked.append(dep)
                    stack.extend(dependents.get(dep, []))
        return blocked

    def blocked_report(self, task: TaskSpec,
                       states: dict[str, str]) -> str:
        """Dependency-block reporting (§5-95): plain words naming
        which dependencies are still holding this task back."""
        pending = [d for d in task.dependencies
                   if states.get(d) != "completed"]
        if not pending:
            return ""
        return (f"task {task.task_id} is waiting for "
                f"{', '.join(pending)} to finish first")

    def _log(self, task_id: str, reason: str, **extra) -> None:
        entry = {"task_id": task_id, "reason": reason,
                 "at": now_iso()}
        entry.update(extra)
        self.dispatch_log.append(entry)

    def run(self, tasks: list[TaskSpec],
            runner: Callable[[TaskSpec], dict]) -> dict:
        by_id = {t.task_id: t for t in tasks}
        states: dict[str, str] = {t.task_id: "queued" for t in tasks}
        results: dict[str, dict] = {}
        # Tasks whose dependencies failed or stopped never run: they
        # are reported blocked (§5-95), not silently skipped.
        dependents: dict[str, list[str]] = {t.task_id: [] for t in tasks}
        for t in tasks:
            for dep in t.dependencies:
                if dep in dependents:
                    dependents[dep].append(t.task_id)
        pending = list(tasks)
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            running: dict = {}
            while pending or running:
                # Hold checks first: budget / approval / stopped.
                ready = []
                for task in list(pending):
                    if task.task_id in self._stopped:
                        states[task.task_id] = "blocked"
                        self._log(task.task_id,
                                  f"blocked: {self._stop_reason.get(task.task_id, 'stopped')}")
                        pending.remove(task)
                        continue
                    failed_deps = [d for d in task.dependencies
                                   if states.get(d) in ("failed", "blocked")]
                    if failed_deps:
                        states[task.task_id] = "blocked"
                        self._log(task.task_id,
                                  f"blocked because a dependency failed: "
                                  f"{', '.join(failed_deps)}")
                        pending.remove(task)
                        continue
                    if not all(states.get(d) == "completed"
                               for d in task.dependencies):
                        continue
                    if self.budget_ok is not None and not self.budget_ok(task):
                        self._log(task.task_id,
                                  "held: the existing budget check did "
                                  "not allow this task to run yet")
                        continue
                    if self.approval_ok is not None and not self.approval_ok(task):
                        self._log(task.task_id,
                                  "held: the existing approval check did "
                                  "not allow this task to run yet")
                        continue
                    ready.append(task)
                # Reliability-aware ordering: healthy-tool tasks first.
                def _health_key(task: TaskSpec) -> int:
                    if self.reliability and task.primary_tool:
                        view = self.reliability.view(task.primary_tool)
                        if view["calls"] >= 5 and view["success_rate"] < 0.5:
                            return 1
                    return 0
                ready.sort(key=_health_key)
                slots = self.max_concurrency - len(running)
                for task in ready[:slots]:
                    pending.remove(task)
                    states[task.task_id] = "running"
                    note = ""
                    if self.reliability:
                        note = self.reliability.scheduling_note(task)
                    self._log(task.task_id,
                              "ran now: all dependencies finished and a "
                              "worker slot was free"
                              + (f"; note: {note}" if note else ""),
                              note=note)
                    fut = pool.submit(self._run_one, task, runner)
                    running[fut] = task
                if not running:
                    if pending:
                        # Nothing runnable and nothing running: the
                        # remaining tasks wait on dependencies that
                        # will never complete (e.g. an unknown id).
                        # Report them blocked instead of spinning.
                        for task in list(pending):
                            states[task.task_id] = "blocked"
                            self._log(task.task_id,
                                      self.blocked_report(task, states)
                                      or "blocked: dependencies cannot be met")
                            pending.remove(task)
                    break
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    task = running.pop(fut)
                    try:
                        result = fut.result()
                        outcome = result.get("outcome", "success") \
                            if isinstance(result, dict) else "success"
                    except Exception as exc:  # runner failure is a task failure
                        result = {"outcome": "failure",
                                  "detail": f"task failed: {exc}"}
                        outcome = "failure"
                    results[task.task_id] = result
                    states[task.task_id] = ("completed" if outcome == "success"
                                            else "failed" if outcome == "failure"
                                            else "blocked")
                    self._log(task.task_id,
                              f"finished with outcome: {outcome}")
        return {"states": states, "results": results,
                "dispatch_log": self.dispatch_log}

    @staticmethod
    def _run_one(task: TaskSpec, runner: Callable[[TaskSpec], dict]) -> dict:
        return runner(task)


# ---------------------------------------------------------------- enforced execution

async def run_tool_checked(tool: str, args: dict, *, root,
                           allowed: tuple, reliability: ToolReliability | None = None,
                           mission_id: str = "", task_id: str = "",
                           cancel_handle=None,
                           resource_limits: bool = False) -> dict:
    """The enforced execution path (W5-5 + C + §3): arguments are
    validated against the tool definition, the per-task filter is
    enforced, the call runs through agent_tools.execute_tool, the
    output is schema-checked, and reliability + a receipt are
    recorded. A refusal at any gate returns ok=False with plain
    words — it never runs the tool anyway."""
    from .agent_tools import execute_tool
    from .tooldefs import receipt as make_receipt
    from .tooldefs import timed_run, validate_args, validate_output
    clean, errors = validate_args(tool, args)
    if errors:
        return {"ok": False, "outcome": "failure", "errors": errors,
                "receipt": make_receipt(tool, args, "failure",
                                        mission_id=mission_id, task_id=task_id)}
    allowed_ok, reason = enforce_tool(tool, allowed)
    if not allowed_ok:
        return {"ok": False, "outcome": "failure", "errors": [reason],
                "refused": "tool_not_allowed",
                "receipt": make_receipt(tool, args, "failure",
                                        mission_id=mission_id, task_id=task_id)}
    with timed_run() as timer:
        result = await execute_tool(tool, clean, root=root,
                                    allowed_tools=allowed,
                                    cancel_handle=cancel_handle,
                                    resource_limits=resource_limits)
    outcome = result.get("outcome")
    if outcome not in ("success", "failure", "timeout", "cancelled"):
        outcome = "success" if result.get("ok") else "failure"
    if reliability is not None:
        reliability.record(tool, outcome, duration_ms=timer.duration_ms)
    output_errors = validate_output(tool, result)
    out = dict(result)
    out["outcome"] = outcome
    out["duration_ms"] = timer.duration_ms
    out["output_validation_errors"] = output_errors
    out["receipt"] = make_receipt(tool, clean, outcome,
                                  returncode=result.get("returncode"),
                                  duration_ms=timer.duration_ms,
                                  stdout_truncated=bool(result.get("stdout_truncated")),
                                  stderr_truncated=bool(result.get("stderr_truncated")),
                                  mission_id=mission_id, task_id=task_id)
    return out


# ---------------------------------------------------------------- §5-97 partial results

def accept_partial_result(result: dict) -> dict:
    """Partial-result acceptance (§5-97, kept): a partial result is
    accepted ONLY when marked partial, and it is never presented as
    complete. Returns the acceptance record."""
    partial = bool(result.get("partial"))
    return {
        "accepted": True,
        "complete": not partial,
        "presentation": ("partial result — shown as incomplete, never "
                         "as a finished answer" if partial
                         else "complete result"),
        "partial": partial,
    }
