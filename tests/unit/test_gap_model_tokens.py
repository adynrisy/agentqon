"""W6 gap 4 — token estimation reconciled with provider usage."""
import pytest

from app.modelrequest import normalize_usage
from app.token_est import estimate_tokens, reconcile
from app.store import Store


def test_estimate_counts_messages_tools_skills_history():
    est = estimate_tokens(
        messages=[{"role": "system", "content": "You are helpful."},
                  {"role": "user", "content": "Hello world, this is a test."}],
        tools=[{"type": "function", "function": {"name": "f",
                                                "description": "does things"}}],
        skills_text="Skill text here.",
        history=[{"role": "user", "content": "earlier turn"}])
    assert est["is_estimate"] is True
    assert est["estimated_input_tokens"] > 0
    assert est["breakdown"]["tools"] > 0
    assert est["breakdown"]["skills"] > 0
    assert est["breakdown"]["history"] > 0
    # Longer input estimates larger — monotonicity, not exactness.
    bigger = estimate_tokens(messages=[{"role": "user", "content": "x" * 400}])
    smaller = estimate_tokens(messages=[{"role": "user", "content": "x" * 40}])
    assert bigger["estimated_input_tokens"] > smaller["estimated_input_tokens"]


def test_estimate_handles_content_parts_and_tool_calls():
    est = estimate_tokens(messages=[
        {"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:..."}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "f", "arguments": '{"a": 1}'}}]},
    ])
    assert est["estimated_input_tokens"] > 85  # image part estimate included


def test_reconcile_reports_error_both_directions():
    rec = reconcile(100, None, {"input_tokens": 120, "output_tokens": 30})
    assert rec["input_error"] == 20
    assert rec["input_error_pct"] == pytest.approx(16.67, abs=0.01)
    assert rec["reported_output_tokens"] == 30
    assert rec["output_error"] is None  # no output estimate given
    over = reconcile(200, None, {"input_tokens": 100, "output_tokens": 5})
    assert over["input_error"] == -100
    assert over["input_error_pct"] == -100.0


def test_reconcile_missing_report_is_unknown_not_zero():
    rec = reconcile(100, 50, {"input_tokens": None, "output_tokens": None})
    assert rec["input_error"] is None and rec["output_error"] is None


def test_reconciliation_recorded_in_store(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"), str(tmp_path / "art"))
    rec = reconcile(100, None, normalize_usage(
        {"prompt_tokens": 110, "completion_tokens": 20}))
    store.record_token_reconciliation("t", "chat", "m", rec)
    rows = store.list_token_reconciliations("t", "chat")
    assert len(rows) == 1
    assert rows[0]["input_error"] == 10
    assert rows[0]["model_id"] == "m"
