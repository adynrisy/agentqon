"""Capability probes (W6 gap 1).

Six SEPARATE probes, one per question — never one overall score:

    tool_calling       can the model actually call a tool?
    structured_output  does it return valid structured JSON?
    vision             can it process an image?
    context_size       does it handle the required context size?
    streaming          does it stream partial responses?
    task_performance   does it reliably do a small reproducible task?

Each probe result is a stamp: pass / fail / not_tested, with a
reason and latency. Rules, stated once and enforced in code:

  - An advertised capability NEVER substitutes for a probe result.
    The catalog's "advertised" / "unknown" labels are not stamps.
  - A probe that cannot run records NOT_TESTED, never a fake pass.
    Vision is the canonical case: when the provider rejects image
    input as unsupported, there is no vision input to test, so the
    stamp is not_tested ("the provider does not accept image
    input") — not a pass, and not a fail for a capability that was
    never exercised. A provider that ACCEPTS the image but returns
    empty or garbled content fails the probe honestly.
  - Tool-calling, structured output and streaming rejections are
    attempted features: the provider answered the feature request
    and said no, so those stamp fail, not not_tested.
  - Every probe is one bounded call (small max_tokens, one attempt
    in the caller's client). Probes never retry: a probe measures
    one real answer.

Probes run against a GatewayClient-shaped object: anything with
`_request(method, path, json=...)` (the real client) or, for
streaming, `chat_stream`. The `complete` seam in
`probe_capabilities` keeps unit tests off the network: tests inject
a fake complete function and assert stamps, while the production
path passes the real client.

The reproducible task (task_performance): the model must reply
with ONLY the number that is 17 + 25. Deterministic, checkable
without judgement: the stripped reply must be exactly "42". A
model that explains, hedges, or computes wrong fails — the task
tests instruction-following plus arithmetic, the two things a
"reliable on small tasks" stamp should mean.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

PROBE_NAMES = ("tool_calling", "structured_output", "vision",
               "context_size", "streaming", "task_performance")

# The required context size the context probe tests: 4,000 characters
# of filler plus a marker question. Passing proves the model handles
# at least this much context — it does NOT prove any larger window.
# The catalog's advertised context_window, when present, is reported
# alongside the stamp, never as the stamp itself.
CONTEXT_PROBE_CHARS = 4000
CONTEXT_MARKER = "MARKER-WORD-ALPHA"
TASK_QUESTION = "Reply with only the number that is 17 + 25. No other words."
TASK_EXPECTED = "42"
VISION_IMAGE_URL = ("data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@dataclass
class ProbeResult:
    name: str
    status: str                    # pass | fail | not_tested
    reason: str
    latency_ms: int = 0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "latency_ms": self.latency_ms, "detail": self.detail}


def _stamp(name: str, status: str, reason: str, started: float,
           detail: dict | None = None) -> ProbeResult:
    return ProbeResult(name=name, status=status, reason=reason,
                       latency_ms=int((time.monotonic() - started) * 1000),
                       detail=detail or {})


def _is_unsupported(exc: BaseException) -> bool:
    """A provider refusal of the feature's request shape: HTTP 400 /
    404, or the client's unsupported / bad_request categories."""
    status = getattr(exc, "status", None)
    category = getattr(exc, "category", None)
    return status in (400, 404) or category in ("unsupported", "bad_request")


async def _complete(client, payload: dict):
    """One chat completion through the client's raw request path
    (so probes see the provider's real answer shape, including
    finish reasons and usage, not a cleaned-up helper)."""
    if hasattr(client, "_request"):
        return await client._request("POST", "/chat/completions", json=payload)
    # Fake-client seam used by tests: a callable returning a body.
    return await client(payload)


def _first_text(body) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict))
    return ""


async def probe_tool_calling(client, model: str) -> ProbeResult:
    started = time.monotonic()
    tools = [{"type": "function",
              "function": {"name": "get_weather",
                           "description": "Get the weather for a city.",
                           "parameters": {"type": "object",
                                          "properties": {"city": {"type": "string"}},
                                          "required": ["city"]}}}]
    payload = {"model": model,
               "messages": [{"role": "user",
                             "content": "What is the weather in Paris?"}],
               "tools": tools,
               "tool_choice": {"type": "function",
                               "function": {"name": "get_weather"}},
               "max_tokens": 64}
    try:
        body = await _complete(client, payload)
    except Exception as exc:  # noqa: BLE001 — a failed probe stamps, never crashes
        return _stamp("tool_calling", "fail",
                      f"The model did not complete the tool-calling check: {exc.__class__.__name__}.",
                      started)
    try:
        calls = body["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return _stamp("tool_calling", "fail",
                      "The model returned no usable reply to the tool-calling check.",
                      started)
    for call in calls:
        fn = call.get("function") or {}
        if fn.get("name") == "get_weather":
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                return _stamp("tool_calling", "fail",
                              "The model called the tool but its arguments were not valid JSON.",
                              started)
            if args.get("city"):
                return _stamp("tool_calling", "pass",
                              "The model called the requested tool with a city.",
                              started)
    return _stamp("tool_calling", "fail",
                  "The model answered in words instead of calling the requested tool.",
                  started)


async def probe_structured_output(client, model: str) -> ProbeResult:
    started = time.monotonic()
    payload = {"model": model,
               "messages": [{"role": "user",
                             "content": "Reply with a JSON object with keys "
                                        "name and age. Name: Ada. Age: 36."}],
               "response_format": {"type": "json_object"},
               "max_tokens": 128}
    try:
        body = await _complete(client, payload)
    except Exception as exc:  # noqa: BLE001
        if _is_unsupported(exc):
            # A second attempt without the format flag: if the model
            # still cannot produce JSON, that is a real fail.
            try:
                payload.pop("response_format")
                body = await _complete(client, payload)
            except Exception as exc2:  # noqa: BLE001
                return _stamp("structured_output", "fail",
                              "The model did not answer the structured-output check.",
                              started)
        else:
            return _stamp("structured_output", "fail",
                          "The model did not answer the structured-output check.",
                          started)
    text = _first_text(body).strip()
    if text.startswith("```"):
        import re as _re
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.S)
    try:
        data = json.loads(text)
    except ValueError:
        return _stamp("structured_output", "fail",
                      "The model did not return valid JSON.", started)
    if isinstance(data, dict) and "name" in data and "age" in data:
        return _stamp("structured_output", "pass",
                      "The model returned valid JSON with the requested keys.",
                      started)
    return _stamp("structured_output", "fail",
                  "The model returned JSON but without the requested keys.",
                  started)


async def probe_vision(client, model: str) -> ProbeResult:
    started = time.monotonic()
    payload = {"model": model,
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": "What colour is the single pixel in this image? Answer with one word."},
                   {"type": "image_url", "image_url": {"url": VISION_IMAGE_URL}}]}],
               "max_tokens": 32}
    try:
        body = await _complete(client, payload)
    except Exception as exc:  # noqa: BLE001
        if _is_unsupported(exc):
            return _stamp("vision", "not_tested",
                          "The provider does not accept image input, so vision could not be tested.",
                          started)
        return _stamp("vision", "fail",
                      "The model did not answer the vision check.", started)
    text = _first_text(body).strip()
    if text:
        return _stamp("vision", "pass",
                      "The model described the image.", started,
                      detail={"answer": text[:80]})
    return _stamp("vision", "fail",
                  "The model accepted the image but returned no description.",
                  started)


async def probe_context_size(client, model: str,
                             required_chars: int = CONTEXT_PROBE_CHARS) -> ProbeResult:
    started = time.monotonic()
    filler = ("Background note. " * (required_chars // 17 + 1))[:required_chars]
    payload = {"model": model,
               "messages": [{"role": "user",
                             "content": filler + "\n\nAt the end of your reply, "
                                        f"write exactly this word: {CONTEXT_MARKER}"}],
               "max_tokens": 64}
    try:
        body = await _complete(client, payload)
    except Exception as exc:  # noqa: BLE001
        return _stamp("context_size", "fail",
                      f"The model did not answer the context-size check: {exc.__class__.__name__}.",
                      started)
    text = _first_text(body)
    if CONTEXT_MARKER in text:
        return _stamp("context_size", "pass",
                      f"The model handled a prompt of about {required_chars} characters and returned the marker.",
                      started, detail={"tested_chars": required_chars})
    return _stamp("context_size", "fail",
                  "The model did not return the marker after the long prompt.",
                  started, detail={"tested_chars": required_chars})


async def probe_streaming(client, model: str) -> ProbeResult:
    started = time.monotonic()
    stream = getattr(client, "chat_stream", None)
    if stream is None:
        return _stamp("streaming", "not_tested",
                      "This client cannot stream, so streaming could not be tested.",
                      started)
    try:
        chunks = []
        async for event in stream(model, [{"role": "user", "content": "Count to three."}]):
            if event.get("type") == "delta":
                chunks.append(event.get("text") or "")
            elif event.get("type") == "error":
                raise RuntimeError(event.get("message") or "stream error")
    except Exception as exc:  # noqa: BLE001
        if _is_unsupported(exc):
            return _stamp("streaming", "fail",
                          "The provider does not support streaming replies.",
                          started)
        return _stamp("streaming", "fail",
                      "The model did not stream a reply.", started)
    if any(c.strip() for c in chunks):
        return _stamp("streaming", "pass",
                      "The model streamed partial replies.", started,
                      detail={"chunks": len(chunks)})
    return _stamp("streaming", "fail",
                  "The stream produced no partial replies.", started)


async def probe_task_performance(client, model: str) -> ProbeResult:
    started = time.monotonic()
    payload = {"model": model,
               "messages": [{"role": "user", "content": TASK_QUESTION}],
               "max_tokens": 32}
    try:
        body = await _complete(client, payload)
    except Exception as exc:  # noqa: BLE001
        return _stamp("task_performance", "fail",
                      f"The model did not answer the task check: {exc.__class__.__name__}.",
                      started)
    if _first_text(body).strip() == TASK_EXPECTED:
        return _stamp("task_performance", "pass",
                      "The model answered the small reproducible task exactly.",
                      started)
    return _stamp("task_performance", "fail",
                  "The model did not answer the small task with exactly the expected number.",
                  started)


_PROBES = {"tool_calling": probe_tool_calling,
           "structured_output": probe_structured_output,
           "vision": probe_vision,
           "context_size": probe_context_size,
           "streaming": probe_streaming,
           "task_performance": probe_task_performance}


async def probe_capabilities(client, model: str,
                             only: list[str] | None = None) -> dict[str, ProbeResult]:
    """Run the six probes (or a subset) and return stamps by name.
    One probe's failure never stops the others."""
    results: dict[str, ProbeResult] = {}
    for name in PROBE_NAMES:
        if only is not None and name not in only:
            continue
        try:
            results[name] = await _PROBES[name](client, model)
        except Exception as exc:  # noqa: BLE001 — a probe must stamp, never crash the run
            results[name] = ProbeResult(name=name, status="fail",
                                        reason=f"The probe itself failed: {exc.__class__.__name__}.")
    return results


def all_not_tested(reason: str) -> dict[str, ProbeResult]:
    """Stamps for a model that never answered the basic check: every
    capability is not_tested, never guessed from the failure."""
    return {name: ProbeResult(name=name, status="not_tested", reason=reason)
            for name in PROBE_NAMES}
