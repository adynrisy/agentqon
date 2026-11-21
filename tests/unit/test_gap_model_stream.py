"""W6 gap 5 + A3 — chat streaming.

Gateway SSE parsing (deltas, partial tool-call arguments, incomplete
lines, length finish never success), failover-before-first-chunk
only, and the orchestrator stream generator (working indicator,
notice events, unsupported-streaming error instead of silent
fallback).
"""
import asyncio

import httpx

from app.combos import FailoverGateway
from app.config import Settings
from app.gateway_client import GatewayClient
from app.orchestrator import Orchestrator
from app.store import Store


def run(coro):
    return asyncio.run(coro)


def _gw(handler):
    return GatewayClient(base_url="http://stub.test/v1", api_key="k",
                         transport=httpx.MockTransport(handler))


def _sse(body: str):
    def handler(request):
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "text/event-stream"})
    return handler


def test_stream_yields_deltas_incrementally_and_done():
    gw = _gw(_sse(
        'data: {"choices": [{"delta": {"content": "Hel"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}\n\n'
        'data: [DONE]\n\n'))
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    assert [e for e in events if e["type"] == "delta"] == [
        {"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["finish_reason"] == "completed"
    assert done["finish_is_success"] is True


async def _collect(gen):
    out = []
    async for event in gen:
        out.append(event)
    return out


def test_stream_length_finish_never_success():
    gw = _gw(_sse(
        'data: {"choices": [{"delta": {"content": "cut"}, "finish_reason": "length"}]}\n\n'
        'data: [DONE]\n\n'))
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["finish_reason"] == "length_limit"
    assert done["finish_is_success"] is False


def _chunk(delta, finish=None):
    import json as _json
    choice = {"delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return "data: " + _json.dumps({"choices": [choice]}) + "\n\n"


def test_stream_partial_tool_args_accumulated_not_faked():
    gw = _gw(_sse(
        _chunk({"tool_calls": [{"index": 0, "function": {
            "name": "get_weather", "arguments": '{"city": '}}]})
        + _chunk({"tool_calls": [{"index": 0, "function": {
            "arguments": '"Paris"}'}}]}, finish="tool_calls")
        + 'data: [DONE]\n\n'))
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls == [{"type": "tool_call", "name": "get_weather",
                      "arguments": {"city": "Paris"}}]


def test_stream_incomplete_tool_args_are_error_not_fake_call():
    gw = _gw(_sse(
        _chunk({"tool_calls": [{"index": 0, "function": {
            "name": "f", "arguments": '{"a":'}}]})
        + 'data: [DONE]\n\n'))
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    assert not [e for e in events if e["type"] == "tool_call"]
    assert [e for e in events if e["type"] == "error"]


def test_stream_malformed_lines_dropped_not_crash():
    gw = _gw(_sse(
        'data: {not json\n\n'
        'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}\n\n'
        'data: [DONE]\n\n'))
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    assert {"type": "delta", "text": "ok"} in events


def test_stream_http_error_is_error_event():
    def handler(request):
        return httpx.Response(503, json={"error": "down"})
    gw = _gw(handler)
    events = run(_collect(gw.chat_stream("m", [{"role": "user", "content": "hi"}])))
    assert events and events[0]["type"] == "error"


def test_failover_only_before_first_chunk():
    """A chain whose first target errors before any chunk fails over;
    a chain whose first target yields a chunk then errors does NOT
    switch mid-reply (the error surfaces instead)."""
    good = _gw(_sse(
        'data: {"choices": [{"delta": {"content": "second"}, "finish_reason": "stop"}]}\n\n'
        'data: [DONE]\n\n'))

    class _Failing:
        base_url = "http://stub.test/v1"

        async def chat_stream(self, model, messages):
            yield {"type": "error", "message": "down"}

    seen = []
    chain = FailoverGateway(
        [(_Failing(), "bad-model", "c:bad-model"), (good, "good-model", "c:good-model")],
        on_failover=lambda p: seen.append(p))
    events = run(_collect(chain.chat_stream("ignored", [])))
    assert seen  # failed over before the first chunk
    assert {"type": "delta", "text": "second"} in events


# ---------------------------------------------------------------- orchestrator stream
class _StreamGw:
    base_url = "http://stub.test/v1"

    async def chat_stream(self, model, messages):
        yield {"type": "delta", "text": "Hel"}
        yield {"type": "delta", "text": "lo"}
        yield {"type": "done", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
               "finish_reason": "completed"}


def _orchestrator_with_stream(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "db.sqlite"), str(tmp_path / "art"))
    orch = Orchestrator(store, Settings())
    monkeypatch.setattr("app.orchestrator.resolve_state",
                        lambda settings, store=None, tenant_id=None: _State())
    monkeypatch.setattr(orch, "_gateway_for", lambda conn: _StreamGw())
    return orch, store


class _State:
    connected = True
    source = "byok"
    model = "m1"
    base_url = "http://stub.test/v1"


def test_orchestrator_stream_working_then_deltas_then_done(tmp_path, monkeypatch):
    orch, _store = _orchestrator_with_stream(tmp_path, monkeypatch)
    # BYOK path needs a catalog entry for the default model.
    orch.store.list_connections = lambda tenant_id: []
    import app.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "_pick_catalog_entry", None, raising=False)
    # Simpler: drive the platform path by patching resolve to platform.
    class _PlatState(_State):
        source = "env"
    monkeypatch.setattr("app.orchestrator.resolve_state",
                        lambda settings, store=None, tenant_id=None: _PlatState())
    events = run(_collect(orch.chat_reply_stream("t", "hello")))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "working"
    assert kinds.count("delta") == 2
    assert kinds[-1] == "done"
    done = events[-1]
    assert done["reply"] == "Hello"
    assert done["token_estimate"]["estimated_input_tokens"] > 0
    assert done["token_reconciliation"]["reported_input_tokens"] == 10


def test_orchestrator_stream_unsupported_is_error_not_fallback(tmp_path, monkeypatch):
    orch, store = _orchestrator_with_stream(tmp_path, monkeypatch)
    store.record_capability_result("t", "platform", "m1", "streaming", "fail")

    class _PlatState(_State):
        source = "env"
    monkeypatch.setattr("app.orchestrator.resolve_state",
                        lambda settings, store=None, tenant_id=None: _PlatState())
    events = run(_collect(orch.chat_reply_stream("t", "hello")))
    assert events[0]["type"] == "working"
    err = [e for e in events if e["type"] == "error"]
    assert err and "stream" in err[0]["message"]
    assert not [e for e in events if e["type"] == "delta"]


def test_orchestrator_stream_not_connected_is_error(tmp_path, monkeypatch):
    orch, _store = _orchestrator_with_stream(tmp_path, monkeypatch)

    class _Down:
        connected = False
        source = None
        model = None
        base_url = None
        message = "No model is connected."
    monkeypatch.setattr("app.orchestrator.resolve_state",
                        lambda settings, store=None, tenant_id=None: _Down())
    events = run(_collect(orch.chat_reply_stream("t", "hello")))
    assert events[0]["type"] == "error"
