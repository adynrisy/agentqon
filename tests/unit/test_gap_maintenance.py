"""W7 §13 dev/maintenance as a system."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import platform_maintenance as pmaint


def test_unused_dependency_detection():
    result = pmaint.find_unused_dependencies(
        "requests\nflask\n",
        {"app/x.py": "import requests\n"})
    assert result["unused"] == ["flask"]


def test_dead_code_detection_is_heuristic_and_review_only():
    result = pmaint.find_dead_code({
        "app/x.py": "def used():\n    pass\n\ndef orphan():\n    pass\n\nused()\n",
    })
    names = [d["name"] for d in result["possibly_dead"]]
    assert "orphan" in names and "used" not in names
    assert "Review before deleting" in result["note"]


def test_annotation_enforcement_on_platform_modules():
    here = os.path.dirname(__file__)
    app_dir = os.path.join(here, "..", "..", "app")
    failures = []
    for name in ("platform_benchmarks", "platform_config", "platform_usage",
                 "platform_history", "platform_templates", "platform_backup",
                 "platform_plugins", "platform_workers", "platform_multimodal",
                 "platform_api", "platform_improve", "platform_society",
                 "platform_interface", "platform_maintenance"):
        path = os.path.join(app_dir, name + ".py")
        with open(path) as handle:
            result = pmaint.check_annotations(handle.read())
        if not result["annotated_ok"]:
            failures.append((name, result["unannotated"]))
    assert failures == [], f"unannotated public functions: {failures}"


def test_lint_rules_single_ruleset():
    result = pmaint.check_lint_rules({
        "a.py": "x = 1\t\n",
        "b.py": "x = 1\n",
    })
    assert "a.py" in result["violations"]
    assert "b.py" not in result["violations"]


def test_flake_tracking_system_with_known_queue_flake_as_one_entry():
    tracker = pmaint.FlakeTracker()
    # The known queue timing flake is ONE entry in the system, not the system.
    tracker.record("tests/unit/test_queue.py::test_failed_job_retries_with_backoff_then_dies",
                   "passed")
    tracker.record("tests/unit/test_queue.py::test_failed_job_retries_with_backoff_then_dies",
                   "failed")
    tracker.record("tests/unit/test_queue.py::test_failed_job_retries_with_backoff_then_dies",
                   "passed")
    tracker.record("tests/unit/test_gap_platform.py::test_money_format_consistent",
                   "passed")
    tracker.record("tests/unit/test_gap_platform.py::test_money_format_consistent",
                   "passed")
    flaky = tracker.flaky_tests()
    assert len(flaky) == 1
    assert "test_failed_job_retries" in flaky[0]["test"]
    assert flaky[0]["flake_rate"] == round(1 / 3, 4)


def test_environment_diff_flag_cleanup_deprecation_impact_notes():
    diff = pmaint.diff_environments({"python": "3.12", "platform": "linux"},
                                    {"python": "3.11", "platform": "linux"})
    assert diff["identical"] is False
    assert "python" in diff["differences"]
    flags = pmaint.FlagRegistry()
    flags.register("old_flag", status="removed", introduced="v20")
    assert flags.stale_flags({"old_flag"})
    assert flags.stale_flags(set()) == []
    deps = pmaint.DeprecationRegistry()
    deps.deprecate("/v1/old", replacement="/v1/new", remove_in="v24")
    assert deps.list()[0]["replacement"] == "/v1/new"
    impact = pmaint.change_impact(
        ["app/platform_usage.py"],
        {"app/usage_view.py": "from app.platform_usage import breakdown\n",
         "tests/unit/test_gap_platform.py": "from app import platform_usage\n"})
    assert "app/usage_view.py" in impact["affected_modules"]
    notes = pmaint.generate_release_notes([
        {"title": "Cost breakdowns", "area": "platform"},
        {"title": "Benchmark datasets", "area": "platform"},
    ])
    assert "## platform" in notes and "Cost breakdowns" in notes
