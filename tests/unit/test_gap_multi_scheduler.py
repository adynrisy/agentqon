"""W5 gap-fix: scheduler (W5-1), tool filtering enforcement (W5-5),
reliability tracking (W5-6), stop propagation (§5-94), dependency-block
reporting (§5-95), concurrency limits (§5-89)."""
import time

import pytest

from app import multiagent
from app.multiagent import (Scheduler, TaskSpec, ToolReliability,
                            allowed_tools_for, enforce_tool,
                            run_tool_checked)


def _runner_ok(task):
    return {"outcome": "success", "task_id": task.task_id}


def test_independent_tasks_run_concurrently_and_dependent_waits():
    started = {}

    def runner(task):
        started[task.task_id] = time.monotonic()
        if task.task_id == "slow":
            time.sleep(0.3)
        return {"outcome": "success"}

    tasks = [
        TaskSpec(task_id="slow", description="slow independent"),
        TaskSpec(task_id="fast", description="fast independent"),
        TaskSpec(task_id="child", description="waits", dependencies=["slow"]),
    ]
    out = Scheduler(max_concurrency=2).run(tasks, runner)
    assert out["states"] == {"slow": "completed", "fast": "completed",
                             "child": "completed"}
    # The child ran only after slow finished: its dispatch entry comes
    # after slow's finish entry in the log.
    log = out["dispatch_log"]
    slow_finish = next(i for i, e in enumerate(log)
                       if e["task_id"] == "slow" and "finished" in e["reason"])
    child_run = next(i for i, e in enumerate(log)
                     if e["task_id"] == "child" and e["reason"].startswith("ran now"))
    assert child_run > slow_finish


def test_concurrency_cap_respected():
    peak = {"n": 0}
    current = {"n": 0}

    def runner(task):
        current["n"] += 1
        peak["n"] = max(peak["n"], current["n"])
        time.sleep(0.05)
        current["n"] -= 1
        return {"outcome": "success"}

    tasks = [TaskSpec(task_id=f"t{i}", description=f"task {i}")
             for i in range(6)]
    Scheduler(max_concurrency=2).run(tasks, runner)
    assert peak["n"] <= 2


def test_failed_dependency_blocks_dependents_not_silently_skipped():
    def runner(task):
        return {"outcome": "failure" if task.task_id == "parent" else "success"}

    tasks = [TaskSpec(task_id="parent", description="p"),
             TaskSpec(task_id="child", description="c", dependencies=["parent"])]
    out = Scheduler().run(tasks, runner)
    assert out["states"]["child"] == "blocked"
    assert any("blocked because a dependency failed" in e["reason"]
               for e in out["dispatch_log"] if e["task_id"] == "child")


def test_dispatch_reasons_recorded_for_every_task():
    tasks = [TaskSpec(task_id="a", description="a"),
             TaskSpec(task_id="b", description="b", dependencies=["a"])]
    out = Scheduler().run(tasks, _runner_ok)
    ran = {e["task_id"] for e in out["dispatch_log"]
           if e["reason"].startswith("ran now")}
    assert ran == {"a", "b"}
    assert all("dependencies finished" in e["reason"]
               for e in out["dispatch_log"] if e["reason"].startswith("ran now"))


def test_budget_and_approval_hooks_hold_tasks_with_reasons():
    tasks = [TaskSpec(task_id="a", description="a")]
    out = Scheduler(budget_ok=lambda t: False).run(tasks, _runner_ok)
    assert out["states"]["a"] == "blocked"
    assert any("budget check" in e["reason"] for e in out["dispatch_log"])
    out = Scheduler(approval_ok=lambda t: False).run(tasks, _runner_ok)
    assert any("approval check" in e["reason"] for e in out["dispatch_log"])


def test_stop_propagates_to_dependents():
    sched = Scheduler()
    blocked = sched.stop_task("a", "the user stopped it",
                              dependents={"a": ["b"], "b": ["c"]})
    assert blocked == ["b", "c"]


def test_blocked_report_names_pending_dependencies():
    sched = Scheduler()
    task = TaskSpec(task_id="c", description="c", dependencies=["a", "b"])
    report = sched.blocked_report(task, {"a": "completed", "b": "running"})
    assert "b" in report and "waiting" in report
    assert sched.blocked_report(task, {"a": "completed",
                                       "b": "completed"}) == ""


def test_researcher_has_no_deploy_tool_and_reviewer_has_no_write_tool():
    assert "shell_run" not in allowed_tools_for("researcher")
    assert "fs_write" not in allowed_tools_for("reviewer")
    assert "fs_edit" not in allowed_tools_for("reviewer")
    ok, _ = enforce_tool("fs_read", allowed_tools_for("reviewer"))
    assert ok
    ok, reason = enforce_tool("shell_run", allowed_tools_for("researcher"))
    assert not ok and "not allowed" in reason


def test_reliability_stats_and_mission_record():
    rel = ToolReliability()
    for _ in range(6):
        rel.record("web_read", "success", duration_ms=100)
    rel.record("web_read", "timeout", duration_ms=5000)
    rel.record("web_read", "failure", duration_ms=50)
    rel.record("web_read", "success", duration_ms=100, retries=2)
    view = rel.view("web_read")
    assert view["calls"] == 9
    # Rates are rounded to 4 decimals in the view; compare loosely.
    assert view["success_rate"] == pytest.approx(7 / 9, abs=1e-3)
    assert view["timeout_rate"] == pytest.approx(1 / 9, abs=1e-3)
    assert view["average_duration_ms"] == pytest.approx(5750 / 9, abs=1e-2)
    assert view["error_frequency"] == 2
    assert view["retries"] == 2
    assert "web_read" in rel.mission_record()


def test_reliability_note_names_failing_tool_not_model():
    rel = ToolReliability()
    for _ in range(6):
        rel.record("shell_run", "failure", duration_ms=10)
    task = TaskSpec(task_id="t", description="t", primary_tool="shell_run")
    note = rel.scheduling_note(task)
    assert "shell_run" in note
    assert "model" not in note


def test_run_tool_checked_enforces_filter_at_execution(tmp_path):
    import asyncio
    # Researcher allowlist: shell_run refused BEFORE execution.
    out = asyncio.run(run_tool_checked(
        "shell_run", {"argv": ["echo", "hi"]}, root=tmp_path,
        allowed=allowed_tools_for("researcher")))
    assert out["ok"] is False
    assert out.get("refused") == "tool_not_allowed"
    # Same call with a deployer allowlist runs.
    out = asyncio.run(run_tool_checked(
        "shell_run", {"argv": ["echo", "hi"]}, root=tmp_path,
        allowed=allowed_tools_for("deployer")))
    assert out["ok"] is True
    assert out["outcome"] == "success"
    assert out["receipt"]["tool"] == "shell_run"


def test_run_tool_checked_rejects_unknown_arguments(tmp_path):
    import asyncio
    out = asyncio.run(run_tool_checked(
        "fs_read", {"path": "x.txt", "bogus_flag": True}, root=tmp_path,
        allowed=allowed_tools_for("researcher")))
    assert out["ok"] is False
    assert any("unknown argument" in e for e in out["errors"])
