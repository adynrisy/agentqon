"""Model-request shared utilities (Expansion 2 §1).

One small, pure module — not hundreds of features — covering the
request/response micro-behaviours every model call needs: fingerprint,
canonical parameters, null removal, empty-message filtering, role and
order validation, tool-schema validation, output-limit maths,
per-model sampling compatibility, stop validation, size estimation,
completeness / emptiness / malformed detection, finish-reason
normalization, provider correlation, duration split, usage
normalization, retry eligibility, and downstream size enforcement.

Plain-words law: every error this module raises or returns is a
sentence a user can act on — never a raw code, never JSON.

Retry distinction (R19), stated plainly because it is the item most
often gotten wrong:
  - transient: the provider was busy, down, or unreachable, or the
    request timed out BEFORE any result existed. Safe to retry.
  - malformed: the provider answered, but the answer is not a usable
    response (missing choices, wrong types, unparseable tool args).
    Retrying the same request as if it were weather does not fix a
    malformed answer — it is never retried as transient.
  - uncertain_action: the request may or may not have happened
    (e.g. a timeout on a call that changes something outside the
    model, like a tool execution). Never blindly retried: a second
    attempt could perform the action twice.
  - permanent: the request itself was refused (auth, bad request,
    unknown model). Never retried.
Chat completions are generations, not actions: they change nothing
outside the provider except billing, so a chat-completion timeout is
transient here, NOT an uncertain action. The gateway's existing
timeout retry for chat completions is consistent with this rule;
this module is the place the rule is written down and tested.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- roles
VALID_ROLES = ("system", "developer", "user", "assistant", "tool")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Finish reasons: provider spellings -> common categories. A length
# stop is NEVER success: it means the model was cut off mid-answer.
_FINISH_MAP = {
    "stop": "completed", "end_turn": "completed", "completed": "completed",
    "length": "length_limit", "max_tokens": "length_limit",
    "content_filter": "content_filtered", "content_filtered": "content_filtered",
    "guardrail": "content_filtered",
    "tool_calls": "tool_call", "tool_call": "tool_call", "function_call": "tool_call",
    "error": "error", "failed": "error",
}
_SUCCESS_FINISH = ("completed", "tool_call")


def normalize_finish_reason(raw) -> str:
    """Map any provider finish reason onto a common category.
    Unknown spellings -> "unknown", never silently "completed"."""
    if raw is None:
        return "unknown"
    return _FINISH_MAP.get(str(raw).strip().lower(), "unknown")


def finish_is_success(normalized: str) -> bool:
    """Only a clean finish or a tool call counts as success. In
    particular "length_limit" (cut off) is NOT success."""
    return normalized in _SUCCESS_FINISH


# ---------------------------------------------------------------- request
def drop_nulls(value):
    """Recursively remove None-valued fields (R3). Providers reject
    or misread explicit nulls for optional parameters; a field the
    caller did not set simply does not travel."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def normalize_params(params: dict, model_caps: dict | None = None) -> tuple[dict, list[str]]:
    """Canonical internal form (R2) + per-model sampling
    compatibility (R9). Unknown parameters are dropped with a
    warning (they would be rejected or ignored unpredictably);
    unsupported sampling parameters are dropped with a warning;
    out-of-range sampling values are clamped with a warning.
    Returns (canonical_params, warnings)."""
    caps = model_caps or {}
    warnings: list[str] = []
    known = {"model", "messages", "temperature", "top_p", "max_tokens",
             "stop", "tools", "tool_choice", "stream", "response_format",
             "seed", "frequency_penalty", "presence_penalty"}
    out: dict = {}
    for key, value in (params or {}).items():
        if key not in known:
            warnings.append(f"Ignored the unsupported setting '{key}'.")
            continue
        out[key] = value
    out = drop_nulls(out)
    # Sampling compatibility: caps declare what the model accepts.
    # Absent caps mean "accept the standard set" (the historical
    # behaviour) — a missing capability record never silently drops
    # a parameter the model might support.
    if caps.get("supports_temperature") is False and "temperature" in out:
        warnings.append("This model does not accept a temperature setting, so it was left out.")
        del out["temperature"]
    if caps.get("supports_top_p") is False and "top_p" in out:
        warnings.append("This model does not accept a top-p setting, so it was left out.")
        del out["top_p"]
    if caps.get("supports_tools") is False and ("tools" in out or "tool_choice" in out):
        warnings.append("This model does not accept tool definitions, so they were left out.")
        out.pop("tools", None)
        out.pop("tool_choice", None)
    if caps.get("supports_stop") is False and "stop" in out:
        warnings.append("This model does not accept stop sequences, so they were left out.")
        del out["stop"]
    if caps.get("supports_streaming") is False and out.get("stream") is True:
        warnings.append("This model does not support streaming, so the reply will arrive whole.")
        out["stream"] = False
    if "temperature" in out:
        try:
            t = float(out["temperature"])
        except (TypeError, ValueError):
            warnings.append("The temperature setting was not a number, so it was left out.")
            del out["temperature"]
        else:
            lo = float(caps.get("min_temperature", 0.0))
            hi = float(caps.get("max_temperature", 2.0))
            if t < lo or t > hi:
                warnings.append(f"The temperature was adjusted into this model's range ({lo} to {hi}).")
                out["temperature"] = min(max(t, lo), hi)
            else:
                out["temperature"] = t
    if "top_p" in out:
        try:
            p = float(out["top_p"])
        except (TypeError, ValueError):
            warnings.append("The top-p setting was not a number, so it was left out.")
            del out["top_p"]
        else:
            if p < 0.0 or p > 1.0:
                warnings.append("The top-p setting was adjusted into its range (0 to 1).")
                out["top_p"] = min(max(p, 0.0), 1.0)
            else:
                out["top_p"] = p
    return out, warnings


def validate_stop_sequences(stop) -> tuple[list[str], list[str]]:
    """Stop-sequence validation (R10): a list of at most 4
    non-empty, distinct strings. Returns (cleaned, errors)."""
    errors: list[str] = []
    if stop is None:
        return [], []
    if isinstance(stop, str):
        stop = [stop]
    if not isinstance(stop, list):
        return [], ["Stop sequences must be a list of short phrases."]
    cleaned: list[str] = []
    for item in stop:
        if not isinstance(item, str) or not item.strip():
            errors.append("A stop sequence was empty, so it was left out.")
            continue
        if len(item) > 100:
            errors.append("A stop sequence was too long (over 100 characters), so it was left out.")
            continue
        if item in cleaned:
            errors.append(f"The stop sequence '{item}' was listed twice, so the repeat was left out.")
            continue
        cleaned.append(item)
    if len(cleaned) > 4:
        errors.append("Only the first 4 stop sequences are kept; the rest were left out.")
        cleaned = cleaned[:4]
    return cleaned, errors


def validate_tool_schema(tools) -> list[str]:
    """Tool-schema validation (R7, hardened): every tool must be a
    function tool with a valid name, a description, and — when
    parameters are given — an object schema whose required keys are
    all declared properties. Duplicate names are errors (the
    provider could not tell them apart). Returns plain-words errors
    (empty = valid)."""
    errors: list[str] = []
    if tools is None:
        return errors
    if not isinstance(tools, list):
        return ["Tool definitions must be a list."]
    seen: set[str] = set()
    for i, tool in enumerate(tools):
        where = f"Tool {i + 1}"
        if not isinstance(tool, dict):
            errors.append(f"{where} is not a tool definition.")
            continue
        if tool.get("type") != "function":
            errors.append(f"{where} must be a function tool.")
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            errors.append(f"{where} is missing its function details.")
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
            errors.append(f"{where} has an invalid name — use letters, numbers, dashes and underscores only.")
            continue
        if name in seen:
            errors.append(f"Two tools are named '{name}' — tool names must be unique.")
            continue
        seen.add(name)
        if fn.get("description") is not None and not isinstance(fn["description"], str):
            errors.append(f"Tool '{name}' has a description that is not text.")
        params = fn.get("parameters")
        if params is not None:
            if not isinstance(params, dict) or params.get("type") != "object":
                errors.append(f"Tool '{name}' parameters must be an object schema.")
                continue
            props = params.get("properties")
            if props is not None and not isinstance(props, dict):
                errors.append(f"Tool '{name}' parameters must list their properties.")
                continue
            required = params.get("required") or []
            if not isinstance(required, list):
                errors.append(f"Tool '{name}' required list must be a list of property names.")
                continue
            for req in required:
                if props is not None and req not in props:
                    errors.append(f"Tool '{name}' requires '{req}' but does not declare it.")
    return errors


def validate_messages(messages) -> tuple[list[dict], list[str], list[str]]:
    """Role (R5), order (R6) and empty-message (R4) validation.
    Returns (cleaned_messages, warnings, errors). Cleaning rules:
    - empty/whitespace text messages are dropped, EXCEPT assistant
      messages carrying tool calls and tool-role messages (dropping
      those would break the tool-call chain — semantics preserved);
    - a system/developer message anywhere but first is an error
      (standing instructions must lead, never hide mid-conversation);
    - a tool message whose tool_call_id was never issued by a prior
      assistant message is an error;
    - an empty message list after cleaning is an error.
    Errors are plain words; callers must refuse the request when
    any error is present."""
    warnings: list[str] = []
    errors: list[str] = []
    if not isinstance(messages, list) or not messages:
        return [], [], ["The request has no messages to send."]
    cleaned: list[dict] = []
    issued_call_ids: set[str] = set()
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"Message {i + 1} is not a message.")
            continue
        role = msg.get("role")
        if role not in VALID_ROLES:
            errors.append(f"Message {i + 1} has an unknown role '{role}' — "
                          "roles must be system, developer, user, assistant or tool.")
            continue
        if role in ("system", "developer") and cleaned:
            errors.append(f"A {role} message appears after the conversation "
                          "started — standing instructions must come first.")
            continue
        content = msg.get("content")
        tool_calls = msg.get("tool_calls") or []
        if role == "tool":
            call_id = msg.get("tool_call_id")
            if not call_id:
                errors.append("A tool result is missing the id of the tool call it answers.")
                continue
            if call_id not in issued_call_ids:
                errors.append("A tool result answers a tool call that was never made.")
                continue
        if role == "assistant":
            for call in tool_calls:
                if isinstance(call, dict) and call.get("id"):
                    issued_call_ids.add(call["id"])
        is_empty_text = (content is None or
                         (isinstance(content, str) and not content.strip()) or
                         (isinstance(content, list) and not content))
        if is_empty_text and role not in ("tool",) and not tool_calls:
            warnings.append(f"An empty {role} message was left out.")
            continue
        cleaned.append(msg)
    if not cleaned:
        errors.append("Every message was empty, so there is nothing to send.")
    return cleaned, warnings, errors


def validate_request(params: dict, model_caps: dict | None = None) -> dict:
    """Single entry point: normalize + validate a whole request.
    Returns {request, warnings, errors}. `request` is the cleaned,
    canonical payload (or None when errors forbid sending)."""
    warnings: list[str] = []
    errors: list[str] = []
    normalized, norm_warnings = normalize_params(params, model_caps)
    warnings.extend(norm_warnings)
    messages, msg_warnings, msg_errors = validate_messages(
        normalized.get("messages") or [])
    warnings.extend(msg_warnings)
    errors.extend(msg_errors)
    if "tools" in normalized:
        errors.extend(validate_tool_schema(normalized["tools"]))
    if "stop" in normalized:
        cleaned_stop, stop_errors = validate_stop_sequences(normalized["stop"])
        warnings.extend(stop_errors)
        normalized["stop"] = cleaned_stop
    if errors:
        return {"request": None, "warnings": warnings, "errors": errors}
    normalized["messages"] = messages
    return {"request": normalized, "warnings": warnings, "errors": []}


def fingerprint_request(request: dict) -> str:
    """Stable fingerprint (R1): SHA-256 over the canonical JSON of
    the request (sorted keys, no secrets — requests carry no keys).
    The same request always fingerprints the same; any change to
    model, messages or parameters changes it."""
    canonical = json.dumps(request, sort_keys=True, default=str,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def output_limit(context_window: int | None, estimated_input_tokens: int,
                 budget_max_tokens: int | None = None,
                 reserve_tokens: int = 256) -> dict:
    """Output-limit calculation (R8): what remains of the context
    window after the input and a safety reserve, capped by the
    caller's budget. A non-positive remainder is an honest error,
    never a silent zero-token call."""
    if context_window is None:
        limit = budget_max_tokens
        return {"max_output_tokens": limit,
                "limited_by": "budget" if limit is not None else "none",
                "error": None}
    remaining = int(context_window) - int(estimated_input_tokens) - int(reserve_tokens)
    if remaining <= 0:
        return {"max_output_tokens": 0, "limited_by": "context",
                "error": "The request is too large for this model's context — "
                         "shorten the conversation or pick a model with a larger context."}
    if budget_max_tokens is not None:
        return {"max_output_tokens": min(remaining, int(budget_max_tokens)),
                "limited_by": ("budget" if budget_max_tokens < remaining else "context"),
                "error": None}
    return {"max_output_tokens": remaining, "limited_by": "context", "error": None}


def estimate_request_size(messages=None, tools=None) -> dict:
    """Request-size estimation (R11): estimated input tokens for the
    messages + tools, plus raw character count. An estimate, labelled
    as one (see token_est)."""
    from .token_est import estimate_tokens
    est = estimate_tokens(messages=messages, tools=tools)
    chars = 0
    for msg in (messages or []):
        if isinstance(msg, dict):
            chars += len(str(msg.get("content") or ""))
    chars += len(json.dumps(tools or [], default=str))
    return {"estimated_input_tokens": est["estimated_input_tokens"],
            "characters": chars, "is_estimate": True}


# ---------------------------------------------------------------- response
def is_empty_response(body) -> bool:
    """Empty-response detection (R14, hardened): no choices, or a
    first choice whose message has neither text nor tool calls."""
    if not isinstance(body, dict):
        return True
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return True
    first = choices[0]
    if not isinstance(first, dict):
        return True
    msg = first.get("message")
    if not isinstance(msg, dict):
        return True
    content = msg.get("content")
    calls = msg.get("tool_calls")
    content_empty = (content is None or
                     (isinstance(content, str) and not content.strip()) or
                     (isinstance(content, list) and not content))
    return content_empty and not calls


def malformed_response_reasons(body) -> list[str]:
    """Malformed-response detection (R15, hardened): structural
    problems that make the response unusable. Empty list = well-formed
    (well-formed is not the same as non-empty — see is_empty_response)."""
    reasons: list[str] = []
    if not isinstance(body, dict):
        return ["The provider's reply was not a structured response."]
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ["The provider's reply contained no choices."]
    first = choices[0]
    if not isinstance(first, dict):
        return ["The provider's first choice was not structured."]
    msg = first.get("message")
    if not isinstance(msg, dict):
        return ["The provider's reply had no message."]
    if msg.get("content") is not None and not isinstance(msg["content"], (str, list)):
        reasons.append("The provider's message text was not text.")
    calls = msg.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list):
            reasons.append("The provider's tool calls were not a list.")
        else:
            for call in calls:
                if not isinstance(call, dict):
                    reasons.append("A tool call was not structured.")
                    continue
                fn = call.get("function")
                if not isinstance(fn, dict) or not fn.get("name"):
                    reasons.append("A tool call was missing its function name.")
                    continue
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        json.loads(args)
                    except (ValueError, TypeError):
                        reasons.append(
                            f"Tool call '{fn.get('name')}' arguments were not valid JSON.")
    usage = body.get("usage")
    if usage is not None and not isinstance(usage, dict):
        reasons.append("The provider's usage report was not structured.")
    return reasons


def response_completeness(body) -> dict:
    """Response-completeness detection (R12): is this a finished,
    usable response, or was it cut off / left half-parsed? A message
    whose text ends inside an unclosed JSON object or whose tool-call
    arguments do not parse is incomplete, even when the provider
    claimed it finished."""
    reasons = malformed_response_reasons(body)
    if reasons:
        return {"complete": False, "reason": reasons[0], "details": reasons}
    if is_empty_response(body):
        return {"complete": False, "reason": "The provider's reply was empty.",
                "details": ["empty"]}
    msg = body["choices"][0]["message"]
    content = msg.get("content")
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            # A JSON-shaped reply that does not close is truncated.
            opens = stripped.count("{") + stripped.count("[")
            closes = stripped.count("}") + stripped.count("]")
            if opens > closes:
                return {"complete": False,
                        "reason": "The provider's reply was cut off mid-sentence.",
                        "details": ["unclosed-json"]}
    finish = normalize_finish_reason(body["choices"][0].get("finish_reason"))
    if finish == "length_limit":
        return {"complete": False,
                "reason": "The provider's reply was cut off at its length limit.",
                "details": ["length_limit"]}
    return {"complete": True, "reason": None, "details": []}


def normalize_usage(raw) -> dict:
    """Usage-field normalization across providers (R18): OpenAI-style
    prompt/completion tokens, Anthropic-style input/output tokens,
    and cached/reasoning detail blocks all land in one shape.
    `usage_complete` is False when the provider reported nothing —
    missing usage is unknown, never zero."""
    if not isinstance(raw, dict) or not raw:
        return {"input_tokens": None, "output_tokens": None,
                "total_tokens": None, "cached_tokens": None,
                "reasoning_tokens": None, "usage_complete": False}
    details_in = raw.get("prompt_tokens_details") or {}
    details_out = raw.get("completion_tokens_details") or {}
    in_tok = raw.get("prompt_tokens", raw.get("input_tokens"))
    out_tok = raw.get("completion_tokens", raw.get("output_tokens"))
    cached = details_in.get("cached_tokens")
    reasoning = details_out.get("reasoning_tokens")
    try:
        in_tok = int(in_tok) if in_tok is not None else None
    except (TypeError, ValueError):
        in_tok = None
    try:
        out_tok = int(out_tok) if out_tok is not None else None
    except (TypeError, ValueError):
        out_tok = None
    total = raw.get("total_tokens")
    if total is None and in_tok is not None and out_tok is not None:
        total = in_tok + out_tok
    return {"input_tokens": in_tok, "output_tokens": out_tok,
            "total_tokens": (int(total) if total is not None else None),
            "cached_tokens": cached, "reasoning_tokens": reasoning,
            "usage_complete": in_tok is not None or out_tok is not None}


def provider_correlation(body, headers=None, request_fingerprint: str | None = None) -> dict:
    """Provider-request correlation (R16): the provider's own request
    id (from the body or response headers) tied to our fingerprint,
    so a disputed call can be traced on both sides."""
    headers = headers or {}
    lowered = {str(k).lower(): v for k, v in headers.items()}
    req_id = None
    if isinstance(body, dict):
        req_id = body.get("id") or body.get("request_id")
    if not req_id:
        req_id = (lowered.get("x-request-id") or lowered.get("request-id")
                  or lowered.get("x-requestid"))
    return {"provider_request_id": (str(req_id) if req_id else None),
            "request_fingerprint": request_fingerprint}


@dataclass
class DurationSplit:
    """Duration split (R17): total wall time vs generation time
    (start → first token for streams, start → provider timing for
    whole replies). Overhead is the difference — queueing, network,
    framing — never hidden inside generation time."""
    total_ms: float
    generation_ms: float | None = None
    first_token_ms: float | None = None

    @property
    def overhead_ms(self) -> float | None:
        if self.generation_ms is None:
            return None
        return max(0.0, self.total_ms - self.generation_ms)

    def as_dict(self) -> dict:
        return {"total_ms": round(self.total_ms, 1),
                "generation_ms": (round(self.generation_ms, 1)
                                  if self.generation_ms is not None else None),
                "first_token_ms": (round(self.first_token_ms, 1)
                                   if self.first_token_ms is not None else None),
                "overhead_ms": (round(self.overhead_ms, 1)
                                if self.overhead_ms is not None else None)}


def split_duration(started: float, first_token_at: float | None,
                   ended: float) -> DurationSplit:
    """Build a DurationSplit from monotonic timestamps (seconds)."""
    total = (ended - started) * 1000.0
    first = ((first_token_at - started) * 1000.0) if first_token_at else None
    generation = ((ended - first_token_at) * 1000.0) if first_token_at else None
    return DurationSplit(total_ms=total, generation_ms=generation,
                         first_token_ms=first)


def classify_retry(error=None, status: int | None = None,
                   malformed: bool = False,
                   uncertain_action: bool = False) -> dict:
    """Retry eligibility (R19). Returns {retryable, kind, reason} in
    plain words. Precedence matters: malformed beats transient (a
    bad answer is not weather), and uncertain beats everything
    except permanent (a maybe-happened action must never be blindly
    repeated)."""
    if uncertain_action:
        return {"retryable": False, "kind": "uncertain_action",
                "reason": "It is unclear whether the action already happened, "
                          "so repeating it could do it twice — it must not be retried blindly."}
    if malformed:
        return {"retryable": False, "kind": "malformed",
                "reason": "The provider's reply was malformed — retrying the same "
                          "request would not fix a broken reply."}
    if error is not None:
        category = getattr(error, "category", None)
        if category in ("auth_failed", "bad_request", "unsupported", "blocked"):
            return {"retryable": False, "kind": "permanent",
                    "reason": "The provider refused the request itself — retrying would fail the same way."}
        if category in ("rate_limited", "timeout", "provider_unavailable"):
            return {"retryable": True, "kind": "transient",
                    "reason": "The provider was busy or unreachable — a retry may succeed."}
    if status is not None:
        if status in (429, 500, 502, 503, 504):
            return {"retryable": True, "kind": "transient",
                    "reason": "The provider was busy or unreachable — a retry may succeed."}
        if status in (400, 401, 403, 404, 422):
            return {"retryable": False, "kind": "permanent",
                    "reason": "The provider refused the request itself — retrying would fail the same way."}
    return {"retryable": False, "kind": "unknown",
            "reason": "The failure kind is unknown, so it is not retried."}


def enforce_response_size(text: str, max_chars: int = 100_000) -> dict:
    """Response-size enforcement downstream (R20): an oversized
    reply is truncated WITH a visible marker and a flag — never
    silently cut short, and never passed on unbounded."""
    if text is None:
        return {"text": "", "truncated": False, "original_chars": 0}
    if len(text) <= max_chars:
        return {"text": text, "truncated": False, "original_chars": len(text)}
    marker = "\n\n[Reply shortened: the full reply was longer than the display limit.]"
    kept = max_chars - len(marker)
    return {"text": text[:kept] + marker, "truncated": True,
            "original_chars": len(text)}
