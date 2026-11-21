"""Token estimation + reconciliation (W6 gap 4).

No tokenizer library is in the pinned dependency set, and the
no-new-dependency rule forbids adding one, so the estimator is a
documented heuristic: roughly one token per four characters of text
(the long-standing rough rule for English text), plus a fixed
overhead per message and per tool definition for the framing the
provider adds. Every value this module produces is labelled an
ESTIMATE — it is a planning number, never a bill. After a call, the
estimate is reconciled against the provider's reported usage
(`reconcile`), and the error is recorded by the caller (see
store.record_token_reconciliation). A large, persistent error is a
signal to re-check the estimator, not to hide it.
"""
from __future__ import annotations

import json
import math

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
TOOL_OVERHEAD_TOKENS = 16


def _text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _content_tokens(content) -> int:
    """Tokens for one message's content: a plain string, or a list of
    content parts (text parts, image parts — an image part counts a
    fixed estimate, since its token cost is provider-defined and
    unknowable here; the estimate flag carries that uncertainty)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return _text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _text_tokens(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    total += _text_tokens(str(part.get("text") or ""))
                elif part.get("type") in ("image_url", "image"):
                    total += 85  # rough image-part estimate, flagged as estimate
                else:
                    total += _text_tokens(json.dumps(part, default=str))
        return total
    return _text_tokens(str(content))


def estimate_tokens(messages=None, tools=None, skills_text: str = "",
                    history=None) -> dict:
    """Estimate input tokens before a request. `messages` are the
    messages about to be sent; `history` is extra prior turns folded
    in separately (callers pass one or the other, never both for the
    same turns). Returns input estimate + breakdown, always with
    `is_estimate: True`."""
    total = 0
    breakdown = {"messages": 0, "tools": 0, "skills": 0, "history": 0}
    for msg in (messages or []):
        if not isinstance(msg, dict):
            continue
        n = _content_tokens(msg.get("content")) + MESSAGE_OVERHEAD_TOKENS
        if msg.get("role"):
            n += 1
        # Tool calls ride inside assistant messages; their arguments
        # are text the provider must read too.
        for call in (msg.get("tool_calls") or []):
            if isinstance(call, dict):
                fn = call.get("function") or {}
                n += _text_tokens(json.dumps(fn.get("arguments") or "",
                                             default=str))
        total += n
        breakdown["messages"] += n
    for tool in (tools or []):
        n = _text_tokens(json.dumps(tool, default=str)) + TOOL_OVERHEAD_TOKENS
        total += n
        breakdown["tools"] += n
    if skills_text:
        n = _text_tokens(skills_text)
        total += n
        breakdown["skills"] += n
    for turn in (history or []):
        if isinstance(turn, dict):
            n = _content_tokens(turn.get("content")) + MESSAGE_OVERHEAD_TOKENS
        else:
            n = _text_tokens(str(turn))
        total += n
        breakdown["history"] += n
    return {"estimated_input_tokens": total, "breakdown": breakdown,
            "is_estimate": True, "method": "chars/4 heuristic"}


def estimate_output_tokens(max_tokens: int | None) -> dict:
    """The output estimate is just the caller's cap (or unknown when
    uncapped) — the model decides the real length."""
    if max_tokens is None:
        return {"estimated_output_tokens": None, "is_estimate": True}
    return {"estimated_output_tokens": int(max_tokens), "is_estimate": True}


def reconcile(estimated_input: int | None, estimated_output: int | None,
              reported: dict) -> dict:
    """Compare an estimate against provider-reported usage.
    `reported` is the normalized usage dict (see modelrequest).
    Missing reported values yield error None (unknown), never 0 —
    a missing report is not a perfect estimate."""
    def _err(est, rep):
        if est is None or rep is None:
            return None, None
        err = rep - est
        pct = (err / rep * 100.0) if rep else (0.0 if err == 0 else None)
        return err, (round(pct, 2) if pct is not None else None)

    in_err, in_pct = _err(estimated_input, reported.get("input_tokens"))
    out_err, out_pct = _err(estimated_output, reported.get("output_tokens"))
    return {
        "estimated_input_tokens": estimated_input,
        "reported_input_tokens": reported.get("input_tokens"),
        "input_error": in_err, "input_error_pct": in_pct,
        "estimated_output_tokens": estimated_output,
        "reported_output_tokens": reported.get("output_tokens"),
        "output_error": out_err, "output_error_pct": out_pct,
    }
