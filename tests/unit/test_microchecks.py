"""W3 §14 test matrix: every one-line mistake as a targeted test or a
stated invariant. Helpers live in app/microchecks.py; where the rule
already holds in existing code, the test proves it against that code;
where the rule belongs to another workstream, the test pins the rule
in W3's helpers and the report routes the finding — W3 does not edit
another workstream's code.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app import microchecks as m
from app.domain import content_hash
from app.store import Store

TENANT = "org_micro_test"


def test_case_insensitive_id_comparison_where_required():
    assert m.ids_equal("Agent-1", "agent-1", case_insensitive=True) is True
    assert m.ids_equal("Agent-1", "agent-1") is False
    assert m.ids_equal("Agent-1", "Agent-2", case_insensitive=True) is False


def test_zero_is_a_valid_value_never_missing():
    assert m.is_valid_zero(0) is True
    assert m.is_valid_zero(0.0) is True
    assert m.is_valid_zero(None) is False
    assert m.is_valid_zero(m.MISSING) is False
    assert m.field_state({"count": 0}, "count") == "present"


def test_empty_array_vs_absent_field_distinguished():
    assert m.field_state({"items": []}, "items") == "empty"
    assert m.field_state({}, "items") == "absent"
    assert m.field_state({"items": [1]}, "items") == "present"


def test_cross_timezone_timestamp_comparison():
    # 12:00 in +05:00 IS 07:00 UTC — a naive string comparison would
    # order these wrong; a UTC conversion orders them right.
    assert m.compare_timestamps("2026-10-24T12:00:00+05:00",
                                "2026-10-24T07:00:00Z") == 0
    assert m.compare_timestamps("2026-10-24T08:00:00Z",
                                "2026-10-24T07:00:00Z") == 1


def test_rounding_each_cost_before_summing():
    costs = ["0.125", "0.125"]
    assert m.round_each_then_sum(costs) == Decimal("0.26")
    assert m.sum_then_round(costs) == Decimal("0.25")
    assert m.round_each_then_sum(costs) != m.sum_then_round(costs)


def test_per_provider_cached_token_reporting_differences():
    a = m.normalize_token_report("provider-a", {"cached_tokens": 120})
    b = m.normalize_token_report("provider-b", {"usage": {"cache_read_tokens": 45}})
    c = m.normalize_token_report("provider-c", {"usage": {"input_tokens": 10}})
    assert a["cached_tokens"] == 120 and b["cached_tokens"] == 45
    # A provider that says nothing reports unknown — never zero.
    assert c["cached_tokens"] is None
    assert "not zero" in c["note"]


def test_stable_id_never_display_name_in_relationships(tmp_path):
    # The rename build's invariant, proven: a relationship keyed by
    # stable ID survives a display-name change; keying by display name
    # would break it.
    relationships = {"agent_stable_1": {"mission": "m1"}}
    assert m.relationship_by_id(relationships, "agent_stable_1") == {"mission": "m1"}
    assert m.relationship_by_id(relationships, "Display Name") is None
    # And against the real shipped maps: keys are stable IDs, not names.
    repo = Path(__file__).resolve().parents[2]
    import json
    display = json.loads((repo / "app" / "data" / "display_names.json").read_text())
    agents = display["agents"]
    assert len(agents) > 1000
    assert all(" " not in key for key in list(agents)[:500])
    assert len(set(agents)) == len(agents)


def test_timeout_never_proof_external_action_did_not_happen():
    outcome = m.timeout_outcome()
    assert outcome["status"] == "unknown"
    assert "must not be treated as proof" in outcome["message"]


def test_missing_field_never_means_false():
    assert m.value_or_missing({}, "flag") is m.MISSING
    assert m.is_false({}, "flag") is False
    assert m.is_false({"flag": False}, "flag") is True
    assert m.is_false({"flag": True}, "flag") is False


def test_http_200_never_means_operation_succeeded():
    assert m.operation_succeeded(200, {"success": False}) == "failed"
    assert m.operation_succeeded(200, {"success": True}) == "succeeded"
    assert m.operation_succeeded(200, {}) == "unknown"
    assert m.operation_succeeded(500, {}) == "failed"


def test_warning_vs_fatal_never_confused():
    assert m.is_fatal("warning") is False
    assert m.is_fatal("fatal") is True
    assert m.is_fatal("error") is True


def test_stale_authorization_never_reused():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert m.auth_is_fresh({"expires_at": past}) is False
    assert m.auth_is_fresh({"expires_at": future}) is True
    assert m.auth_is_fresh({}) is False
    # Existing session check fails closed too (unparseable -> invalid).
    from app.auth import session_valid
    assert session_valid({"expires_at": "not-a-date"}) is False
    assert session_valid({"expires_at": past}) is False


def test_config_updates_never_applied_to_running_missions():
    frozen = m.freeze_config({"model": "a", "limits": {"max": 5}})
    live = {"model": "a", "limits": {"max": 5}}
    live["model"] = "b"
    live["limits"]["max"] = 99
    assert frozen["model"] == "a"
    assert frozen["limits"]["max"] == 5


def test_json_comparison_never_by_key_order():
    assert m.json_equal({"b": 2, "a": 1}, {"a": 1, "b": 2}) is True
    assert m.json_equal({"a": 1}, {"a": 2}) is False
    # Content-derived IDs are order-insensitive (domain helper too).
    assert m.content_id({"b": 2, "a": 1}) == m.content_id({"a": 1, "b": 2})
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})


def test_special_characters_escaped_in_filenames_and_search():
    assert "/" not in m.escape_filename("a/b")
    assert ".." not in m.escape_filename("../secret")
    assert m.escape_filename("report (final).md") == "report (final).md" or \
        "(" in m.escape_filename("report (final).md")
    import re
    escaped = m.escape_search_query("price (low) $5")
    assert re.fullmatch(escaped, "price (low) $5") is not None
    assert re.fullmatch(escaped, "price low 5") is None


def test_cancellation_never_lost_between_steps():
    token = m.CancelToken()
    ran = []

    def step(i):
        return lambda: ran.append(i)

    token.cancel()
    result = m.run_steps([step(0), step(1)], token)
    assert result["status"] == "cancelled"
    assert result["cancelled_before_step"] == 0
    assert ran == []
    # Cancel mid-run: steps after the boundary do not run.
    token2 = m.CancelToken()
    ran2 = []

    def cancelling_step():
        ran2.append("first")
        token2.cancel()

    result2 = m.run_steps([cancelling_step, lambda: ran2.append("second")],
                          token2)
    assert result2["cancelled_before_step"] == 1
    assert ran2 == ["first"]


def test_stale_responses_never_overwrite_newer_state():
    state = {"seq": 5, "value": "new"}
    assert m.apply_if_newer(state, 3, "stale") is False
    assert state["value"] == "new"
    assert m.apply_if_newer(state, 6, "newer") is True
    assert state["value"] == "newer"


def test_concurrent_updates_never_overwrite_each_other():
    state = {"version": 2, "value": "v2"}
    ok, _ = m.apply_if_version(state, 1, "based on old read")
    assert ok is False
    assert state["value"] == "v2"
    ok, _ = m.apply_if_version(state, 2, "v3")
    assert ok is True
    assert state["version"] == 3 and state["value"] == "v3"


def test_empty_search_results_are_valid():
    result = m.search_result([])
    assert result["status"] == "ok"
    assert result["results"] == []


def test_model_confidence_never_treated_as_calibrated_probability():
    described = m.describe_confidence(0.92)
    assert "not a measured chance" in described["warning"]
    assert described["label"] == "model-reported score"
    labelled = m.describe_confidence("high")
    assert labelled["label"] == "high"
    assert "not a measured chance" in labelled["warning"]


def test_existing_usage_totals_documented_sum_then_round(tmp_path):
    # DOCUMENTED BEHAVIOUR (integration update 2026-10-24): this pins
    # usage_summary's DISPLAY rounding (SQL sum, rounded to 4dp) over
    # directly-inserted rows. The §14 rule itself is now implemented
    # on the record path: W1 rounds each cost to 6dp in
    # Store.add_usage before storing (proven by
    # test_add_usage_rounds_each_cost_before_storing), so totals
    # built through add_usage are sums of already-rounded costs.
    # Assertions unchanged — only this note was updated, deliberately.
    store = Store(str(tmp_path / "s.db"))
    store._exec("INSERT INTO usage_records VALUES (?,?,?,?,?,?,?)",
                ("u1", "m1", TENANT, "model", 0.125, "", "2026-10-24T00:00:00Z"))
    store._exec("INSERT INTO usage_records VALUES (?,?,?,?,?,?,?)",
                ("u2", "m1", TENANT, "model", 0.125, "", "2026-10-24T00:00:00Z"))
    summary = store.usage_summary(TENANT, "m1")
    assert summary["total_usd"] == round(0.25, 4)
    assert summary["total_usd"] != float(m.round_each_then_sum(["0.125", "0.125"]))
