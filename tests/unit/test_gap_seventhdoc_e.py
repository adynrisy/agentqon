"""Gap-seventhdoc group E: evaluation science."""
from app.eval_ops import (
    benchmark_stats, blind_compare, calibrate_reviewers, compare_benchmarks,
    contamination_check, detect_gaming, mistake_to_regression,
    run_regressions, record_feedback)


def test_e22_calibration_finds_inconsistent_reviewer_and_criterion():
    fb = []
    for i in range(3):
        record_feedback(fb, "alice", "clarity", f"item{i}", 10.0)
        record_feedback(fb, "bob", "clarity", f"item{i}", 1.0)
        record_feedback(fb, "alice", "accuracy", f"item{i}", 7.0)
        record_feedback(fb, "bob", "accuracy", f"item{i}", 7.0)
    out = calibrate_reviewers(fb)
    assert "alice" in out["inconsistent_reviewers"] or \
        "bob" in out["inconsistent_reviewers"]
    assert "clarity" in out["criteria_interpreted_differently"]
    assert "accuracy" not in out["criteria_interpreted_differently"]


def test_e23_contamination_reports_overlap():
    hits = contamination_check(
        ["What is the capital of France and its river?"],
        {"training": ["What is the capital of France and its river called?"],
         "prompts": ["Write a poem about cats"]})
    assert len(hits) == 1 and hits[0]["source"] == "training"
    assert hits[0]["overlap"] >= 0.5


def test_e24_benchmark_never_declares_superiority_after_one_run():
    assert compare_benchmarks([9.0], [8.0])["superior"] is None
    stats = benchmark_stats([8.0, 9.0, 10.0])
    assert stats["n"] == 3 and stats["mean"] == 9.0
    out = compare_benchmarks([9.0, 9.2, 9.1], [5.0, 5.1, 5.2])
    assert out["superior"] == "a"
    close = compare_benchmarks([8.0, 9.0, 10.0], [8.1, 9.0, 9.9])
    assert close["superior"] is None


def test_e25_blind_compare_hides_identities_until_scoring():
    out = blind_compare({"id": "model-x", "content": "good answer"},
                        {"id": "model-y", "content": "bad"},
                        lambda c: len(c))
    assert "model-x" not in str(out["reviewer_view"])
    assert out["winner_id"] == "model-x"
    assert out["revealed_after_scoring"]["first"] == "model-x"


def test_e26_gaming_detector_flags_high_score_missing_feature():
    assert detect_gaming(0.95, [True, False], True)["gaming"] is True
    assert detect_gaming(0.95, [True, True], True)["gaming"] is False
    assert detect_gaming(0.5, [False], True)["gaming"] is False


def test_e27_feedback_becomes_regression_test():
    mistakes = []
    mistake_to_regression(mistakes, "m1", "Dropped citations",
                          "run mission X, check citations")
    results = run_regressions(mistakes, "prompt change",
                              lambda m: False)
    assert results == [{"mistake_id": "m1", "passed": False}]
    assert mistakes[0]["status"] == "still_failing"
    results = run_regressions(mistakes, "fix", lambda m: True)
    assert mistakes[0]["status"] == "passing"
