"""Model registry helpers (W6 gaps 2 + 3, expansion A).

The catalog (app/catalog.py) remains the store of record for prices
and listings. This module is the honest presentation + profile layer
on top of it:

  - badges: shown ONLY where a probe stamped the capability. An
    advertised capability ("the provider lists it") never becomes a
    badge, and an unprobed capability shows as "not tested", never
    as a claim (A1).
  - prices: a price the provider published is "known"; a price
    somebody estimated is shown WITH the word "estimate" wherever
    it appears (M2). An unknown price is never shown as $0.
  - latency: observed per-call latencies, averaged with a plain
    record of how many observations back them (M2, refreshable —
    every new probe/call replaces the average; nothing here is a
    permanent hardcoded number).
  - task-specific profiles (M3, A6): a score per (model, task
    class), built from probe stamps relevant to that class plus
    recorded capability history. There is deliberately NO universal
    ranking: the same model scores differently for extraction than
    for debugging, and the caller must name the task class.
  - context warnings (A2), unsupported-feature warnings (A3), and
    model-switch notices (A4) — all in plain words.

Task classes reuse the platform's task types (app/tasktypes.py)
plus the three classes the gap names explicitly; unknown classes
fall back to the general profile, which is documented, not hidden.
"""
from __future__ import annotations

# Capabilities a badge can show. Only these three are badged, exactly
# as the gap requires; the other probes feed profiles, not badges.
BADGE_CAPABILITIES = ("tool_calling", "vision", "structured_output")

# Which probe capabilities matter for each task class. A class with
# no entry here uses the general set (all probes, equal weight).
TASK_CLASS_RELEVANCE = {
    "extraction": {"structured_output": 2.0, "task_performance": 1.0,
                   "context_size": 1.0},
    "debugging": {"tool_calling": 1.0, "task_performance": 2.0,
                  "context_size": 1.0},
    "classification": {"task_performance": 2.0, "structured_output": 1.0},
    "writing": {"task_performance": 1.0, "context_size": 1.0},
    "code": {"tool_calling": 1.0, "task_performance": 2.0},
    "research": {"context_size": 2.0, "structured_output": 1.0,
                 "task_performance": 1.0},
    "qa": {"task_performance": 1.0},
    "strategy": {"task_performance": 1.0, "context_size": 1.0},
    "plan": {"task_performance": 1.0, "structured_output": 1.0},
    "general": {"task_performance": 1.0, "structured_output": 1.0,
                "tool_calling": 1.0, "vision": 1.0, "context_size": 1.0,
                "streaming": 1.0},
}
TASK_CLASSES = tuple(TASK_CLASS_RELEVANCE.keys())

_STATUS_SCORE = {"pass": 1.0, "fail": 0.0, "not_tested": 0.5}
# not_tested scores neutral (0.5): an untested capability is neither
# evidence for nor against the model — but the profile response
# always names how many of its inputs were not_tested, so a neutral
# score can never masquerade as measured strength.

CONTEXT_WARN_FRACTION = 0.80


def badges_for(latest: dict) -> dict:
    """Badges from the LATEST probe stamps only (A1). `latest` maps
    capability -> status string (as latest_capability_badges returns)
    or -> {"status": ...} stamp dict; both are accepted. Missing
    capability -> the badge value is "not_tested" — the UI renders
    that as "not tested", never as a capability claim."""
    out = {}
    for cap in BADGE_CAPABILITIES:
        value = latest.get(cap)
        if isinstance(value, dict):
            value = value.get("status")
        out[cap] = value if value in ("pass", "fail", "not_tested") else "not_tested"
    return out


def price_display(entry: dict) -> dict:
    """Price presentation (M2): value + whether it is an estimate +
    plain-words text. Unknown/stale prices carry no number at all."""
    status = entry.get("price_status")
    if status == "estimated":
        pin, pout = entry.get("price_in"), entry.get("price_out")
        if pin is None:
            return {"text": "price unknown", "is_estimate": True,
                    "price_in": None, "price_out": None}
        return {"text": f"${pin} / ${pout} per 1M tokens (estimate)",
                "is_estimate": True, "price_in": pin, "price_out": pout}
    if status == "known" and entry.get("price_in") is not None:
        return {"text": f"${entry['price_in']} / ${entry.get('price_out')} per 1M tokens",
                "is_estimate": False, "price_in": entry.get("price_in"),
                "price_out": entry.get("price_out")}
    if status == "stale":
        return {"text": "price unknown (last price is out of date)",
                "is_estimate": False, "price_in": None, "price_out": None}
    return {"text": "price unknown", "is_estimate": False,
            "price_in": None, "price_out": None}


def record_latency(observations: list[float], latency_ms: float) -> dict:
    """Observed latency (M2, refreshable): append one observation
    and return the running average + count. Callers persist the
    returned average; a new observation always moves it."""
    obs = list(observations or []) + [float(latency_ms)]
    return {"avg_latency_ms": round(sum(obs) / len(obs), 1),
            "observations": len(obs)}


def profile_score(stamps: dict, history: list[dict] | None,
                  task_class: str) -> dict:
    """Task-specific profile score (M3, A6). `stamps` are the latest
    probe stamps (capability -> {"status"}); `history` rows are
    recorded capability results for this model in this task class
    (each {"capability", "status"}). Score: relevance-weighted mean
    of stamp scores, blended 50/50 with the history pass rate when
    history exists. No universal ranking is produced — the caller
    must pass a task class, and the response names it."""
    relevance = TASK_CLASS_RELEVANCE.get(task_class,
                                         TASK_CLASS_RELEVANCE["general"])
    total_w = 0.0
    weighted = 0.0
    tested = 0
    untested = 0
    for cap, weight in relevance.items():
        status = (stamps.get(cap) or {}).get("status", "not_tested")
        if status == "not_tested":
            untested += 1
        else:
            tested += 1
        weighted += weight * _STATUS_SCORE.get(status, 0.5)
        total_w += weight
    stamp_score = weighted / total_w if total_w else 0.5
    # History rows recorded without a task class are general probe
    # evidence: they count for every class (the relevance weights
    # above already make the score class-specific). Rows recorded
    # for a DIFFERENT class do not count here.
    hist = [row for row in (history or [])
            if row.get("task_class") in (task_class, None)]
    if hist:
        passes = sum(1 for row in hist if row.get("status") == "pass")
        hist_rate = passes / len(hist)
        score = round(0.5 * stamp_score + 0.5 * hist_rate, 4)
    else:
        hist_rate = None
        score = round(stamp_score, 4)
    return {"task_class": task_class, "score": score,
            "stamp_score": round(stamp_score, 4),
            "history_pass_rate": hist_rate,
            "history_observations": len(hist),
            "capabilities_tested": tested,
            "capabilities_not_tested": untested}


def profile_scores_for_models(models: list[dict], badges_by_model: dict,
                              history_by_model: dict, task_class: str) -> list[dict]:
    """Profile scores for a model list, best first for THIS task
    class only. Ties break on model id for determinism."""
    scored = []
    for entry in models:
        key = (entry.get("connection_id"), entry.get("model_id"))
        stamps = {cap: {"status": status}
                  for cap, status in (badges_by_model.get(key) or {}).items()}
        # Badges cover only three capabilities; the full stamp set is
        # completed from the latest history row per capability —
        # profiles never claim a capability the probes did not stamp.
        # History is oldest-first, so a later row is fresher:
        for row in (history_by_model.get(key) or []):
            stamps[row.get("capability")] = {"status": row.get("status")}
        prof = profile_score(stamps, history_by_model.get(key) or [], task_class)
        scored.append({"model_id": entry.get("model_id"),
                       "connection_id": entry.get("connection_id"),
                       **prof})
    scored.sort(key=lambda s: (-s["score"], s["model_id"]))
    return scored


def context_warning(estimated_tokens: int | None,
                    context_window: int | None) -> str | None:
    """Context-limit awareness (A2): warn BEFORE the call fails.
    At/above 80% of the window: a plain-words warning. At/above the
    window: a refusal-to-silently-fail message (the caller must
    shorten or switch — the warning is returned, the call is not
    quietly sent to fail)."""
    if estimated_tokens is None or not context_window:
        return None
    frac = estimated_tokens / context_window
    if frac >= 1.0:
        return ("This request is larger than the model's context limit, "
                "so it cannot be sent as it stands — shorten the "
                "conversation or pick a model with a larger context.")
    if frac >= CONTEXT_WARN_FRACTION:
        pct = int(frac * 100)
        return (f"This request uses about {pct}% of the model's "
                "context limit — close to the limit, so a longer reply "
                "may be cut short.")
    return None


def unsupported_warning(feature: str, status: str | None,
                        model_id: str) -> str | None:
    """Unsupported-feature warning (A3): plain words, what cannot be
    done and why. `status` is the probe stamp for the feature
    (pass/fail/not_tested/None). A pass or an unknown (None) yields
    no warning — unknown is not a claim of failure; only a stamped
    fail warns, and not_tested explains itself as untested."""
    feature_words = {"vision": "work with images",
                     "tools": "use tools",
                     "streaming": "stream its reply as it is written",
                     "structured_output": "return structured data"}
    what = feature_words.get(feature, f"use {feature}")
    if status == "fail":
        return (f"This model cannot {what}: its capability check for "
                f"that feature did not pass, so the request cannot be "
                "done by this model as asked.")
    if status == "not_tested":
        return (f"It is not known whether this model can {what} — it "
                "has not been tested for that feature yet.")
    return None


def model_switch_notice(primary_model: str, fallback_model: str,
                        verification_enabled: bool = True) -> str:
    """Model-switch notification (A4): the exact wording shape the
    gap requires, with real model names. The verification sentence
    is kept whenever verification stays on; when it does not, the
    notice says so plainly instead of dropping the subject."""
    base = (f"Primary model {primary_model} unavailable. "
            f"Switched to the configured fallback model {fallback_model}.")
    if verification_enabled:
        return base + " Verification requirements remain enabled."
    return base + " Verification requirements are not enabled for this fallback."
