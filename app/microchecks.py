"""AgentQon — §14 one-line-mistake guards (W3 test-matrix deliverable).

Small, shared, stdlib-only helpers — each one pins down a mistake that
is easy to write in one line and expensive to debug later. Where the
harness already avoids a mistake, the helper states the correct rule
and the test proves it against the real code or data; where another
workstream owns the code, this module does NOT change that code — the
test matrix reports the finding for the coordinator to route.

Simple-words law applies to every message here too.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

# A value that is present-but-absent is not the same as False, 0, "" or
# []. Code that treats a missing field as False invents an answer.
MISSING = object()


def ids_equal(a: str, b: str, *, case_insensitive: bool = False) -> bool:
    """ID comparison. IDs are compared case-insensitively only where
    the caller says so — silently lower-casing everywhere would merge
    IDs that only differ by case."""
    if case_insensitive:
        return str(a).lower() == str(b).lower()
    return str(a) == str(b)


def field_state(record: dict, key: str) -> str:
    """'absent' | 'empty' | 'present'. An empty list is NOT an absent
    field: an empty result is a real answer, a missing field is not."""
    if key not in record:
        return "absent"
    value = record[key]
    if value is None:
        return "absent"
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return "empty"
    return "present"


def is_valid_zero(value: Any) -> bool:
    """0 is a valid value, never a missing one. Only None / MISSING
    count as missing here."""
    return value is not None and value is not MISSING


def value_or_missing(record: dict, key: str) -> Any:
    """The field's value, or the MISSING sentinel — never False for a
    field that is simply not there."""
    return record[key] if key in record else MISSING


def is_false(record: dict, key: str) -> bool:
    """True only when the field is present AND False. A missing field
    is never False."""
    return key in record and record[key] is False


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp to an aware UTC datetime. A timestamp
    with no timezone is treated as UTC and the assumption is stated by
    the caller-visible docstring, never silently mixed with aware ones."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compare_timestamps(a: str, b: str) -> int:
    """Cross-timezone comparison done correctly: both sides convert
    to UTC first. Returns -1, 0 or 1."""
    ta, tb = parse_timestamp(a), parse_timestamp(b)
    return (ta > tb) - (ta < tb)


def round_each_then_sum(costs: list[str | float | Decimal]) -> Decimal:
    """Round EACH cost to cents first, then add. Summing first and
    rounding once gives a different (wrong, by this rule) total."""
    total = Decimal("0")
    for cost in costs:
        total += Decimal(str(cost)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    return total


def sum_then_round(costs: list[str | float | Decimal]) -> Decimal:
    """The WRONG order, kept here so tests can prove the two differ."""
    total = sum((Decimal(str(c)) for c in costs), Decimal("0"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Per-provider cached-token reporting differs: one provider calls the
# field "cached_tokens", another "cache_read_tokens", a third nests it
# under "usage". Normalising to one shape — and reporting "not
# reported" instead of 0 when a provider says nothing — stops a missing
# report being read as "nothing was cached".
_TOKEN_KEYS = ("cached_tokens", "cache_read_tokens",
               "prompt_tokens_cached", "cached_input_tokens")


def normalize_token_report(provider: str, report: dict) -> dict:
    usage = report.get("usage", report) if isinstance(report, dict) else {}
    for key in _TOKEN_KEYS:
        if isinstance(usage, dict) and key in usage:
            return {"provider": provider, "cached_tokens": usage[key],
                    "reported_as": key,
                    "note": f"{provider} reports cached tokens as {key}."}
    return {"provider": provider, "cached_tokens": None,
            "reported_as": None,
            "note": (f"{provider} did not report cached tokens "
                     "separately, so the count is unknown — not zero.")}


def timeout_outcome() -> dict:
    """A timeout never proves an external action did not happen. The
    only honest outcome is 'unknown'."""
    return {"status": "unknown",
            "message": ("The wait ran out before an answer came back, so "
                        "it is unknown whether the outside action "
                        "happened. It must not be treated as proof that "
                        "it did not happen.")}


def operation_succeeded(status_code: int, body: dict | None) -> str:
    """An HTTP 200 never means the operation succeeded on its own.
    The body's own success field decides; with no success field at all,
    the outcome is unknown, not success."""
    body = body or {}
    for key in ("success", "ok", "succeeded"):
        if key in body:
            return "succeeded" if body[key] else "failed"
    if 200 <= status_code < 300:
        return "unknown"
    return "failed"


def is_fatal(severity: str) -> bool:
    """Warning and fatal are never confused: only 'fatal' (and its
    plain synonym 'error' at the top level) is fatal."""
    return str(severity).lower() in ("fatal", "error")


def auth_is_fresh(auth: dict, now: datetime | None = None) -> bool:
    """Stale authorization is never reused: an expired token counts
    as no authorization at all."""
    expires = auth.get("expires_at")
    if not expires:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        return parse_timestamp(str(expires)) > now
    except ValueError:
        return False


def freeze_config(config: dict) -> dict:
    """A mission keeps the settings it started with. Later config
    updates change future missions, never one already running — this
    deep copy is the frozen record of what the mission started with."""
    return deepcopy(config)


def json_equal(a: Any, b: Any) -> bool:
    """JSON comparison by content, never by key order."""
    return json.dumps(a, sort_keys=True, default=str) == \
        json.dumps(b, sort_keys=True, default=str)


def content_id(data: Any) -> str:
    """A content-derived ID that is order-insensitive: the same
    content in a different key order yields the same ID."""
    raw = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def escape_filename(name: str) -> str:
    """Escape special characters in a filename: separators, control
    characters and shell-special characters become '-', and parent
    hops ('..') cannot survive."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(name))
    text = text.replace("..", "--").strip(" .")
    return text or "file"


def escape_search_query(query: str) -> str:
    """Escape special characters in a search string so a user's
    punctuation is searched literally, never read as a pattern."""
    return re.escape(str(query))


class CancelledError(Exception):
    """Cancellation, raised between steps — never swallowed."""


class CancelToken:
    """Cancellation that is never lost between execution steps: every
    step checks the token before it runs, and a cancelled run reports
    exactly where it stopped."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def check(self) -> None:
        if self.cancelled:
            raise CancelledError("The run was cancelled.")


def run_steps(steps: list, token: CancelToken) -> dict:
    """Run steps in order, checking cancellation BETWEEN steps (and
    before the first). A cancelled run stops at the next boundary and
    says which step it stopped before."""
    done = []
    for index, step in enumerate(steps):
        try:
            token.check()
        except CancelledError:
            return {"completed": done, "cancelled_before_step": index,
                    "status": "cancelled"}
        step()
        done.append(index)
    return {"completed": done, "cancelled_before_step": None,
            "status": "completed"}


def apply_if_newer(state: dict, seq: int, value: Any) -> bool:
    """A stale response never overwrites newer state: an update only
    applies when its sequence number is newer than the state's."""
    if seq <= int(state.get("seq", 0)):
        return False
    state["seq"] = seq
    state["value"] = value
    return True


def apply_if_version(state: dict, expected_version: int,
                     value: Any) -> tuple[bool, str]:
    """Concurrent updates never overwrite each other silently: an
    update applies only when the caller saw the current version. On a
    mismatch nothing changes, and the caller is told to re-read."""
    if int(state.get("version", 0)) != int(expected_version):
        return False, ("The record changed since it was last read, so "
                       "this update was not applied. Read it again and "
                       "try once more.")
    state["version"] = int(expected_version) + 1
    state["value"] = value
    return True, "Update applied."


def search_result(items: list) -> dict:
    """Empty search results are a valid answer, not an error."""
    return {"results": list(items), "status": "ok",
            "message": ("No matches were found." if not items else
                        f"{len(items)} match(es) found.")}


def describe_confidence(confidence: Any) -> dict:
    """A model confidence value is never treated as a calibrated
    chance. A number is reported as a model-reported score only, with
    the warning attached; a label stays a label."""
    if isinstance(confidence, (int, float)):
        return {"label": "model-reported score",
                "value": confidence,
                "warning": ("This number is a score the model reported. "
                            "It is not a measured chance that the answer "
                            "is right, and it must not be used as one.")}
    return {"label": str(confidence), "value": None,
            "warning": ("Confidence words are rough guidance, not a "
                        "measured chance.")}


def relationship_by_id(relationships: dict, stable_id: str) -> Any:
    """Relationships are keyed by stable ID, never by display name:
    renaming the display name must not break, move or merge a
    relationship."""
    return relationships.get(stable_id)
