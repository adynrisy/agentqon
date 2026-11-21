"""W6 gap 1 — capability probes (six separate stamps).

A fake GatewayClient-shaped object answers from a script, so no
network is involved. Each probe is tested in its pass, fail, and
not_tested shapes where the design distinguishes them.
"""
import asyncio

import pytest

from app.gateway_client import GatewayError
from app.modelprobes import (PROBE_NAMES, all_not_tested,
                             probe_capabilities)


class FakeClient:
    """Answers _request by payload shape. `mode` selects behaviour:
    full (does everything), text_only (rejects tools + images),
    silent (empty content), dead (raises)."""

    def __init__(self, mode="full"):
        self.mode = mode
        self.calls = []

    async def _request(self, method, path, json=None):
        self.calls.append(json or {})
        payload = json or {}
        if self.mode == "dead":
            raise GatewayError("provider_unavailable", "down")
        messages = payload.get("messages") or []
        content = messages[0].get("content") if messages else ""
        if isinstance(content, list):
            if self.mode == "text_only":
                raise GatewayError("unsupported", "no image input", status=400)
            return {"choices": [{"message": {"content": "white"},
                                 "finish_reason": "stop"}]}
        if payload.get("tools"):
            if self.mode == "text_only":
                raise GatewayError("bad_request", "tools not supported", status=400)
            return {"choices": [{"message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "Paris"}'}}]},
                "finish_reason": "tool_calls"}]}
        if payload.get("response_format"):
            if self.mode == "silent":
                return {"choices": [{"message": {"content": ""},
                                     "finish_reason": "stop"}]}
            return {"choices": [{"message": {"content": '{"name": "Ada", "age": 36}'},
                                 "finish_reason": "stop"}]}
        if isinstance(content, str) and "MARKER-WORD-ALPHA" in content:
            return {"choices": [{"message": {"content": "MARKER-WORD-ALPHA"},
                                 "finish_reason": "stop"}]}
        if isinstance(content, str) and "17 + 25" in content:
            if self.mode == "silent":
                return {"choices": [{"message": {"content": "about forty-two"},
                                     "finish_reason": "stop"}]}
            return {"choices": [{"message": {"content": "42"},
                                 "finish_reason": "stop"}]}
        return {"choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}]}

    async def chat_stream(self, model, messages):
        if self.mode == "dead":
            raise GatewayError("provider_unavailable", "down")
        if self.mode == "text_only":
            raise GatewayError("unsupported", "no streaming", status=400)
        yield {"type": "delta", "text": "One "}
        yield {"type": "delta", "text": "two."}
        yield {"type": "done", "usage": {}, "finish_reason": "completed"}


def run(coro):
    return asyncio.run(coro)


def test_six_probes_exist_and_are_separate():
    assert PROBE_NAMES == ("tool_calling", "structured_output", "vision",
                           "context_size", "streaming", "task_performance")
    stamps = run(probe_capabilities(FakeClient("full"), "m"))
    assert set(stamps) == set(PROBE_NAMES)
    assert all(s.status == "pass" for s in stamps.values())


def test_vision_not_tested_when_provider_rejects_images():
    stamps = run(probe_capabilities(FakeClient("text_only"), "m"))
    assert stamps["vision"].status == "not_tested"
    assert "image" in stamps["vision"].reason
    # ...but tool-calling rejection is an attempted feature: fail, not not_tested.
    assert stamps["tool_calling"].status == "fail"
    # Streaming rejection is likewise a fail.
    assert stamps["streaming"].status == "fail"


def test_task_probe_requires_exact_answer():
    stamps = run(probe_capabilities(FakeClient("silent"), "m"))
    assert stamps["task_performance"].status == "fail"
    assert stamps["structured_output"].status == "fail"


def test_dead_model_fails_probes_but_stamps_exist():
    stamps = run(probe_capabilities(FakeClient("dead"), "m"))
    assert set(stamps) == set(PROBE_NAMES)
    assert all(s.status == "fail" for s in stamps.values())


def test_all_not_tested_when_basic_check_failed():
    stamps = all_not_tested("The model did not answer the basic check.")
    assert all(s.status == "not_tested" for s in stamps.values())
    assert all("basic check" in s.reason for s in stamps.values())


def test_probe_results_carry_latency_and_reason():
    stamps = run(probe_capabilities(FakeClient("full"), "m"))
    for stamp in stamps.values():
        assert isinstance(stamp.latency_ms, int) and stamp.latency_ms >= 0
        assert stamp.reason
        assert set(stamp.as_dict()) == {"status", "reason", "latency_ms", "detail"}
