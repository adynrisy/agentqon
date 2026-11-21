"""Expansion 2 §1 — model-request shared utilities (W6).

Every item R1–R20 has at least one test here, named for the item.
Pure functions only: no network, no store.
"""
import pytest

from app import modelrequest as mr


# R1 fingerprinting
def test_r1_fingerprint_stable_and_sensitive():
    req = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
           "temperature": 0.7}
    assert mr.fingerprint_request(req) == mr.fingerprint_request(req)
    changed = dict(req, temperature=0.8)
    assert mr.fingerprint_request(changed) != mr.fingerprint_request(req)
    assert len(mr.fingerprint_request(req)) == 64


# R2 parameter normalization
def test_r2_unknown_params_dropped_with_warning():
    out, warnings = mr.normalize_params({"model": "m", "bogus": 1, "temperature": 0.5})
    assert "bogus" not in out and out["temperature"] == 0.5
    assert any("bogus" in w for w in warnings)


def test_r9_sampling_compat_drops_unsupported_and_clamps():
    out, warnings = mr.normalize_params(
        {"temperature": 5.0, "top_p": 0.5},
        {"supports_temperature": False, "max_temperature": 2.0})
    assert "temperature" not in out
    out2, warnings2 = mr.normalize_params(
        {"temperature": 9.0}, {"max_temperature": 2.0})
    assert out2["temperature"] == 2.0 and warnings2


# R3 null-field removal
def test_r3_nulls_removed_recursively():
    assert mr.drop_nulls({"a": 1, "b": None, "c": {"d": None, "e": 2},
                          "f": [1, None]}) == {"a": 1, "c": {"e": 2}, "f": [1, None]}


# R4/R5/R6 messages
def test_r4_empty_messages_dropped_but_tool_chain_preserved():
    msgs = [
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "c1"},
        {"role": "user", "content": "done?"},
    ]
    cleaned, warnings, errors = mr.validate_messages(msgs)
    assert errors == []
    assert [m["role"] for m in cleaned] == ["assistant", "tool", "user"]
    assert warnings  # the empty user message was reported


def test_r5_unknown_role_is_error():
    _c, _w, errors = mr.validate_messages([{"role": "wizard", "content": "hi"}])
    assert errors and "wizard" in errors[0]


def test_r6_system_after_start_and_orphan_tool_result_are_errors():
    _c, _w, errors = mr.validate_messages([
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "late rule"}])
    assert errors
    _c, _w, errors = mr.validate_messages([
        {"role": "tool", "content": "x", "tool_call_id": "never-issued"}])
    assert errors


def test_r6_all_empty_is_error():
    _c, _w, errors = mr.validate_messages([{"role": "user", "content": " "}])
    assert errors


# R7 tool-schema validation (hardened)
def test_r7_tool_schema_valid_and_invalid():
    good = [{"type": "function", "function": {
        "name": "get_weather", "description": "d",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}]
    assert mr.validate_tool_schema(good) == []
    bad = [{"type": "function", "function": {
        "name": "get_weather", "description": "d",
        "parameters": {"type": "object", "properties": {},
                       "required": ["city"]}}}]
    assert mr.validate_tool_schema(bad)  # required but not declared
    dup = good + good
    assert mr.validate_tool_schema(dup)  # duplicate name
    assert mr.validate_tool_schema([{"type": "function", "function": {"name": "bad name!"}}])


# R8 output-limit calculation
def test_r8_output_limit_from_remaining_context():
    out = mr.output_limit(10000, 9000, budget_max_tokens=5000)
    assert out["max_output_tokens"] == 744  # 10000 - 9000 - 256 reserve
    over = mr.output_limit(4000, 5000)
    assert over["max_output_tokens"] == 0 and over["error"]
    assert "too large" in over["error"]


# R10 stop sequences
def test_r10_stop_validation():
    cleaned, errors = mr.validate_stop_sequences(["a", "", "a", "b", "c", "d", "e"])
    assert cleaned == ["a", "b", "c", "d"] and errors


# R11 request-size estimation
def test_r11_request_size_is_estimate():
    size = mr.estimate_request_size(
        [{"role": "user", "content": "hello world"}],
        [{"type": "function", "function": {"name": "f"}}])
    assert size["is_estimate"] is True and size["estimated_input_tokens"] > 0


# R12/R14/R15 response checks
def test_r12_completeness_length_and_truncated_json():
    complete = {"choices": [{"message": {"content": "done"},
                             "finish_reason": "stop"}]}
    assert mr.response_completeness(complete)["complete"] is True
    cut = {"choices": [{"message": {"content": '{"a": 1'},
                        "finish_reason": "length"}]}
    comp = mr.response_completeness(cut)
    assert comp["complete"] is False


def test_r14_empty_response_detection():
    assert mr.is_empty_response({"choices": [{"message": {"content": "  "}}]}) is True
    assert mr.is_empty_response({"choices": [{"message": {"content": "hi"}}]}) is False
    assert mr.is_empty_response({"choices": []}) is True
    assert mr.is_empty_response({}) is True


def test_r15_malformed_response_detection():
    assert mr.malformed_response_reasons({"choices": [{"message": {}}]}) == []
    reasons = mr.malformed_response_reasons({"choices": [{"message": {"tool_calls": [
        {"function": {"name": "f", "arguments": "{not json"}}]}}]})
    assert reasons  # unparseable tool arguments are malformed
    assert mr.malformed_response_reasons("not a dict")


# R13 finish-reason normalization — length is NEVER success
def test_r13_length_stop_never_reads_as_success():
    assert mr.normalize_finish_reason("length") == "length_limit"
    assert mr.normalize_finish_reason("max_tokens") == "length_limit"
    assert mr.finish_is_success("length_limit") is False
    assert mr.finish_is_success("completed") is True
    assert mr.finish_is_success("tool_call") is True
    assert mr.normalize_finish_reason("weird-new-reason") == "unknown"
    assert mr.finish_is_success("unknown") is False


# R16 provider correlation
def test_r16_provider_correlation_from_body_and_headers():
    corr = mr.provider_correlation({"id": "chatcmpl-1"}, {},
                                   request_fingerprint="abc")
    assert corr["provider_request_id"] == "chatcmpl-1"
    assert corr["request_fingerprint"] == "abc"
    corr2 = mr.provider_correlation({}, {"X-Request-Id": "req-9"})
    assert corr2["provider_request_id"] == "req-9"


# R17 duration split
def test_r17_duration_split_total_vs_generation():
    split = mr.split_duration(100.0, 100.5, 102.0)
    assert split.total_ms == 2000.0
    assert split.first_token_ms == 500.0
    assert split.generation_ms == 1500.0
    assert split.overhead_ms == 500.0


# R18 usage normalization across providers
def test_r18_usage_normalization():
    openai = mr.normalize_usage({"prompt_tokens": 10, "completion_tokens": 5,
                                 "total_tokens": 15})
    assert (openai["input_tokens"], openai["output_tokens"]) == (10, 5)
    anthropic = mr.normalize_usage({"input_tokens": 7, "output_tokens": 3,
                                    "prompt_tokens_details": {"cached_tokens": 2}})
    assert anthropic["input_tokens"] == 7 and anthropic["cached_tokens"] == 2
    missing = mr.normalize_usage({})
    assert missing["usage_complete"] is False
    assert missing["input_tokens"] is None  # unknown, never zero


# R19 retry eligibility distinctions
def test_r19_malformed_never_retried_as_transient():
    assert mr.classify_retry(malformed=True)["retryable"] is False
    assert mr.classify_retry(malformed=True)["kind"] == "malformed"
    assert mr.classify_retry(status=503)["retryable"] is True
    assert mr.classify_retry(status=503)["kind"] == "transient"


def test_r19_uncertain_action_never_blindly_retried():
    res = mr.classify_retry(uncertain_action=True)
    assert res["retryable"] is False and res["kind"] == "uncertain_action"
    # ...and it beats even a transient-looking status
    res2 = mr.classify_retry(status=503, uncertain_action=True)
    assert res2["kind"] == "uncertain_action"


def test_r19_permanent_never_retried():
    assert mr.classify_retry(status=401)["retryable"] is False
    assert mr.classify_retry(status=400)["kind"] == "permanent"


# R20 response-size enforcement
def test_r20_oversized_reply_flagged_and_marked():
    out = mr.enforce_response_size("x" * 200, max_chars=100)
    assert out["truncated"] is True and out["original_chars"] == 200
    assert "shortened" in out["text"]
    ok = mr.enforce_response_size("short")
    assert ok["truncated"] is False and ok["text"] == "short"


# Whole-request validation entry point
def test_validate_request_end_to_end():
    res = mr.validate_request({
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.7, "junk": True})
    assert res["errors"] == [] and res["request"] is not None
    assert "junk" not in res["request"] and res["warnings"]
    bad = mr.validate_request({"messages": [{"role": "user", "content": "hi"}],
                               "tools": [{"type": "function"}]})
    assert bad["errors"] and bad["request"] is None
