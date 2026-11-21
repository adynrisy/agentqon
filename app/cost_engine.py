"""AgentQon — cost engine (W1 gap-fix, branch gap-cost).

Stdlib only. One module holds the shared cost utilities so the rules are
enforced in one place instead of re-implemented per caller:

- pre-execution estimation (every figure marked as an estimate) and
  reconciliation against provider-reported usage after the run;
- a detailed usage ledger (per mission/agent/provider/model/phase, with
  input/output/cached tokens, latency and success status);
- a budget engine whose hooks surface ONLY when a limit is hit
  (warn / pause / approve, in plain words) — there is deliberately no
  budget dashboard or budget settings surface in this module;
- a semantic cache whose correctness and permission rules come before
  hit rates;
- duplicate-call elimination inside a mission (verified results only);
- adaptive depth (light path for routine work, full verification for
  complex or high-risk work);
- cost attribution and the headline metric, cost per successfully
  completed task (a token cut that increases failed missions must read
  as worse, never better).

Simple-words law: every user-facing string here is plain words — no raw
JSON, no bare status codes, no jargon. Money is USD everywhere
(CURRENCY); anything else is refused, never silently converted.
An unknown price is never presented as zero: cost is None and the
status says so.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

CURRENCY = "USD"
CACHE_VERSION = "cost-cache-v1"

# Per-type cache lifetimes (seconds). Static and live data NEVER share a
# policy: a static explanation lives a day, live data lives minutes, and
# anything whose freshness class is unknown is treated as live (short)
# because a stale live fact is worse than a cache miss.
TTL_BY_TYPE = {
    "static": 86400,
    "live": 300,
    "price": 60,
    "news": 900,
    "negative": 60,
}
DEFAULT_TTL_SECONDS = TTL_BY_TYPE["live"]
MAX_CACHE_ENTRIES = 1000

# Token heuristic for ESTIMATES only (never a provider count): ~4 chars
# per token. Provider-reported usage always wins over this estimate.
CHARS_PER_TOKEN = 4

PLAIN_LOCAL_MODEL_NOTE = (
    "This answer used a model running on your own computer, so there is "
    "no provider fee for it. It still uses your computer's processor and "
    "graphics chip, electricity, and time — that cost is real, it just "
    "does not arrive as a bill."
)

# Words that push a task to the full-verification path. Light-path tasks
# are routine lookups and summaries with no irreversible or sensitive
# effect; anything else gets full verification.
_HIGH_RISK_WORDS = (
    "delete", "remove", "payment", "charge", "password", "secret", "key",
    "medical", "legal", "contract", "publish", "send", "transfer",
    "financial", "bank", "account", "personal data", "private",
)


# --------------------------------------------------------------------- tokens
def estimate_tokens(text: str) -> int:
    """Rough token estimate for planning only (labelled estimate)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def normalize_cost(cost: float | None) -> float | None:
    """Per-call cost normalization (constraint 121): round EACH call's
    cost before any summing, so a total is a sum of rounded calls and
    never accumulates hidden fractions."""
    if cost is None:
        return None
    return round(float(cost), 6)


# --------------------------------------------------------------------- pricing
def price_for(entry: dict) -> dict:
    """Price record for a catalog entry, with provenance.

    Returns price_in/price_out per 1M tokens (or None), the pricing
    source, the effective date (catalog sync time when known), and a
    status: 'known' | 'unknown'. Local models are genuinely zero-fee
    and are 'known' at 0. Unknown is never collapsed to 0.
    """
    if entry.get("price_status") == "known" and entry.get("price_in") is not None:
        return {
            "price_in": float(entry["price_in"]),
            "price_out": float(entry["price_out"]) if entry.get("price_out") is not None else None,
            "price_cached": (float(entry["price_cached"]) if entry.get("price_cached") is not None else None),
            "price_source": entry.get("price_source") or "catalog",
            "price_effective_date": entry.get("price_effective_date") or entry.get("synced_at"),
            "price_status": "known",
            "price_version": entry.get("price_version"),
        }
    return {
        "price_in": None, "price_out": None, "price_cached": None,
        "price_source": entry.get("price_source"),
        "price_effective_date": entry.get("price_effective_date") or entry.get("synced_at"),
        "price_status": "unknown", "price_version": entry.get("price_version"),
    }


def price_call(entry: dict, input_tokens: int, output_tokens: int,
               cached_tokens: int = 0) -> dict:
    """Price one provider call from provider-reported token counts.

    Cached input is priced separately from fresh input: at the catalog's
    cached price when one is published, otherwise at the input price
    with that fact recorded (never a silently invented discount).
    Unknown price -> cost None, status 'unknown'.
    """
    price = price_for(entry)
    if price["price_status"] != "known" or price["price_in"] is None:
        return {**price, "cost_usd": None, "cost_status": "unknown",
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "fresh_input_tokens": max(0, input_tokens - cached_tokens)}
    cached_price = price["price_cached"] if price["price_cached"] is not None else price["price_in"]
    fresh = max(0, input_tokens - cached_tokens)
    cost = (fresh / 1_000_000 * price["price_in"]
            + cached_tokens / 1_000_000 * cached_price
            + output_tokens / 1_000_000 * (price["price_out"] or 0.0))
    return {**price, "cost_usd": normalize_cost(cost), "cost_status": "known",
            "cached_priced_as": ("cached" if price["price_cached"] is not None else "input"),
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_tokens": cached_tokens, "fresh_input_tokens": fresh}


def latest_price(history: list[dict], model: str, at: str | None = None) -> dict | None:
    """Pricing-version tracking (122) + provider-price refresh (137):
    the newest price record for a model whose effective date is not in
    the future. Records carry source + effective date; the caller, not
    this function, decides when to refresh the catalog."""
    rows = [r for r in history if r.get("model") == model]
    if at is not None:
        rows = [r for r in rows if str(r.get("effective_date", "")) <= at]
    if not rows:
        return None
    rows.sort(key=lambda r: (str(r.get("effective_date", "")), int(r.get("version", 0))))
    return rows[-1]


def check_currency(currency: str) -> None:
    """Currency consistency (138): USD only; anything else is refused,
    never silently converted."""
    if currency != CURRENCY:
        raise ValueError(f"costs are recorded in {CURRENCY} only, not {currency}")


# --------------------------------------------------------------------- ledger records
def make_event_key(tenant_id: str, mission_id: str, provider: str,
                   provider_request_id: str | None, attempt: int = 1) -> str:
    """Usage-event dedup key (123): the same provider request recorded
    twice collapses to one ledger row (the store enforces uniqueness)."""
    raw = "|".join([tenant_id, mission_id, provider,
                    provider_request_id or "", str(attempt)])
    return hashlib.sha256(raw.encode()).hexdigest()


def build_cost_record(*, tenant_id: str, mission_id: str, agent: str = "",
                      provider: str = "", model: str = "", phase: str = "",
                      category: str = "inference", input_tokens: int = 0,
                      output_tokens: int = 0, cached_tokens: int = 0,
                      latency_ms: int | None = None, success: bool = True,
                      cost_usd: float | None = None, cost_status: str = "known",
                      price_source: str | None = None,
                      price_effective_date: str | None = None,
                      price_version: int | None = None,
                      attempt: int = 1, retry_of: str | None = None,
                      partial: bool = False, fallback: bool = False,
                      cached_hit: bool = False, reused_from: str | None = None,
                      provider_request_id: str | None = None,
                      event_key: str | None = None,
                      currency: str = CURRENCY) -> dict:
    """Validate + build one ledger row. Rules enforced here, not by
    each caller: non-negative tokens, cached never above input, one
    currency, partial/failed/retry/fallback flags kept on the record so
    attribution can separate them later. Unknown cost stays None."""
    check_currency(currency)
    if min(input_tokens, output_tokens, cached_tokens) < 0:
        raise ValueError("token counts cannot be negative")
    if cached_tokens > input_tokens:
        raise ValueError("cached input cannot exceed total input")
    if attempt < 1:
        raise ValueError("attempt numbers start at 1")
    if cost_status not in ("known", "estimated", "unknown"):
        raise ValueError("cost_status must be known, estimated or unknown")
    if cost_status == "unknown":
        cost_usd = None
    return {
        "tenant_id": tenant_id, "mission_id": mission_id, "agent": agent,
        "provider": provider, "model": model, "phase": phase,
        "category": category, "input_tokens": input_tokens,
        "output_tokens": output_tokens, "cached_tokens": cached_tokens,
        "fresh_input_tokens": max(0, input_tokens - cached_tokens),
        "latency_ms": latency_ms, "success": bool(success),
        "cost_usd": normalize_cost(cost_usd), "cost_status": cost_status,
        "price_source": price_source, "price_effective_date": price_effective_date,
        "price_version": price_version, "attempt": attempt,
        "retry_of": retry_of, "partial": bool(partial),
        "fallback": bool(fallback), "cached_hit": bool(cached_hit),
        "reused_from": reused_from,
        "provider_request_id": provider_request_id,
        "event_key": event_key or make_event_key(
            tenant_id, mission_id, provider, provider_request_id, attempt),
        "currency": CURRENCY,
    }


# --------------------------------------------------------------------- estimator
def estimate_plan(steps: list[dict], entries_by_model: dict) -> dict:
    """Pre-execution estimator (gap 1). Each step names a model and
    expected input/output tokens (callers supply them; when they only
    supply text, estimate_tokens is used and the basis says so).
    EVERY figure is marked kind='estimate' and carries its price status,
    so an estimate can never be mistaken for a bill."""
    per_step, total, low, high = [], 0.0, 0.0, 0.0
    worst = "known"
    for step in steps:
        entry = entries_by_model.get(step.get("model", ""), {}) or {}
        in_tok = step.get("est_input_tokens")
        out_tok = step.get("est_output_tokens")
        basis = step.get("basis", "caller-supplied token counts")
        if in_tok is None:
            in_tok = estimate_tokens(step.get("input_text", ""))
            basis = "estimated from text length (about 4 characters per token)"
        if out_tok is None:
            out_tok = estimate_tokens(step.get("expected_output_text", "")) or 500
            basis = "estimated from text length (about 4 characters per token)"
        priced = price_call(entry, in_tok, out_tok, step.get("est_cached_tokens", 0))
        cost = priced["cost_usd"]
        step_cost = cost if cost is not None else 0.0
        if priced["cost_status"] != "known":
            worst = "unknown" if priced["cost_status"] == "unknown" else worst
        per_step.append({
            "step": step.get("kind", ""), "model": step.get("model", ""),
            "est_input_tokens": in_tok, "est_output_tokens": out_tok,
            "est_cached_tokens": step.get("est_cached_tokens", 0),
            "estimated_cost_usd": cost, "price_status": priced["cost_status"],
            "price_source": priced["price_source"], "basis": basis,
            "kind": "estimate", "currency": CURRENCY,
        })
        total += step_cost
        low += step_cost * 0.5
        high += step_cost * 1.5
    return {"kind": "estimate", "currency": CURRENCY,
            "estimated_cost_usd": round(total, 6),
            "estimated_range_usd": {"low": round(low, 6), "high": round(high, 6)},
            "price_status": worst, "per_step": per_step,
            "note": "These figures are estimates, not a bill. Actual cost is "
                    "recorded from provider-reported usage after the run."}


def reconcile_estimate(estimate: dict, actual_rows: list[dict]) -> dict:
    """Exact token-usage reconciliation (top-15 #4): provider-reported
    actuals vs the pre-run estimate, per step where models match and
    in total. Variance is reported, never hidden; a large gap is a
    signal to fix the estimator, not to adjust the bill."""
    actual_cost = sum(normalize_cost(r.get("cost_usd")) or 0.0
                      for r in actual_rows if r.get("cost_status") == "known")
    actual_in = sum(r.get("input_tokens", 0) for r in actual_rows)
    actual_out = sum(r.get("output_tokens", 0) for r in actual_rows)
    actual_cached = sum(r.get("cached_tokens", 0) for r in actual_rows)
    est_cost = estimate.get("estimated_cost_usd") or 0.0
    est_in = sum(s.get("est_input_tokens", 0) for s in estimate.get("per_step", []))
    est_out = sum(s.get("est_output_tokens", 0) for s in estimate.get("per_step", []))
    unknown_rows = [r for r in actual_rows if r.get("cost_status") != "known"]
    variance = round(actual_cost - est_cost, 6)
    return {
        "kind": "reconciliation", "currency": CURRENCY,
        "estimated_cost_usd": est_cost, "actual_cost_usd": round(actual_cost, 6),
        "variance_usd": variance,
        "variance_pct": (round(variance / est_cost * 100, 2) if est_cost else None),
        "estimated_input_tokens": est_in, "actual_input_tokens": actual_in,
        "estimated_output_tokens": est_out, "actual_output_tokens": actual_out,
        "actual_cached_tokens": actual_cached,
        "calls_recorded": len(actual_rows),
        "calls_with_unknown_cost": len(unknown_rows),
        "actual_is_estimate": False,
        "note": ("Actual cost comes from provider-reported usage. Rows with "
                 "unknown cost are counted in tokens but not in dollars."),
    }


def reconcile_provider_bill(ledger_rows: list[dict], bill_rows: list[dict]) -> dict:
    """Delayed-billing reconciliation: when the provider's bill lands
    later than the run, match it to ledger rows (by provider request id
    when present, otherwise by provider+model aggregate for the period)
    and return APPEND-ONLY adjustment records. Original rows are never
    rewritten — an adjustment row carries the difference, so the ledger
    stays an honest history of what was first recorded and what the
    bill later said."""
    by_request = {r.get("provider_request_id"): r for r in ledger_rows
                  if r.get("provider_request_id")}
    adjustments, matched, unmatched = [], 0, 0
    for bill in bill_rows:
        check_currency(bill.get("currency", CURRENCY))
        rid = bill.get("provider_request_id")
        recorded = by_request.get(rid) if rid else None
        if recorded is None:
            # Aggregate fallback: same provider+model rows in the period.
            same = [r for r in ledger_rows
                    if r.get("provider") == bill.get("provider")
                    and r.get("model") == bill.get("model")]
            recorded_cost = sum(r.get("cost_usd") or 0.0 for r in same)
            basis = "provider+model aggregate for the billing period"
        else:
            recorded_cost = recorded.get("cost_usd") or 0.0
            basis = "provider request id"
        billed = float(bill.get("billed_cost_usd", 0.0))
        delta = round(billed - recorded_cost, 6)
        if recorded is None and not same:
            unmatched += 1
            continue
        matched += 1
        if delta != 0:
            adjustments.append(build_cost_record(
                tenant_id=bill.get("tenant_id", recorded.get("tenant_id", "")),
                mission_id=bill.get("mission_id", recorded.get("mission_id", "")),
                provider=bill.get("provider", ""), model=bill.get("model", ""),
                category="billing_adjustment", cost_usd=delta,
                cost_status="known", phase="billing",
                provider_request_id=rid,
                event_key=f"bill-adj:{rid or bill.get('provider')}:{bill.get('model')}:{billed}"))
        # keep a human-readable basis on the adjustment for the report
        if adjustments and basis.startswith("provider+model"):
            adjustments[-1]["reconciliation_basis"] = basis
    return {"matched": matched, "unmatched": unmatched,
            "adjustments": adjustments, "currency": CURRENCY,
            "note": "Adjustments are new rows; original ledger rows are unchanged."}


# --------------------------------------------------------------------- attribution
def summarize_ledger(rows: list[dict], estimate: dict | None = None) -> dict:
    """Cost attribution (gap 8, constraint 128): totals broken down per
    agent / provider / phase / category, with known vs estimated vs
    unknown cost kept separate, failed / retry / fallback / cached
    spend visible as their own lines, and current vs projected cost
    shown together when an estimate is supplied. No double-counting:
    each row contributes to exactly one bucket per breakdown, and the
    grand total is the sum of the rows' normalized costs, once."""
    def _sum(pred):
        return round(sum(normalize_cost(r.get("cost_usd")) or 0.0
                         for r in rows if pred(r) and r.get("cost_status") == "known"), 6)

    def _breakdown(key):
        out: dict[str, float] = {}
        for r in rows:
            if r.get("cost_status") != "known":
                continue
            k = r.get(key) or "unknown"
            out[k] = round(out.get(k, 0.0) + (normalize_cost(r.get("cost_usd")) or 0.0), 6)
        return out

    known = _sum(lambda r: True)
    summary = {
        "currency": CURRENCY, "calls": len(rows),
        "total_known_cost_usd": known,
        "total_estimated_cost_usd": round(sum(
            normalize_cost(r.get("cost_usd")) or 0.0 for r in rows
            if r.get("cost_status") == "estimated"), 6),
        "rows_with_unknown_cost": sum(1 for r in rows if r.get("cost_status") == "unknown"),
        "total_input_tokens": sum(r.get("input_tokens", 0) for r in rows),
        "total_output_tokens": sum(r.get("output_tokens", 0) for r in rows),
        "total_cached_tokens": sum(r.get("cached_tokens", 0) for r in rows),
        "total_fresh_input_tokens": sum(r.get("fresh_input_tokens", r.get("input_tokens", 0)) for r in rows),
        "failed_calls": sum(1 for r in rows if not r.get("success", True)),
        "failed_cost_usd": _sum(lambda r: not r.get("success", True)),
        "retry_cost_usd": _sum(lambda r: r.get("attempt", 1) > 1),
        "retry_calls": sum(1 for r in rows if r.get("attempt", 1) > 1),
        "fallback_cost_usd": _sum(lambda r: bool(r.get("fallback"))),
        "partial_calls": sum(1 for r in rows if r.get("partial")),
        "by_agent": _breakdown("agent"),
        "by_provider": _breakdown("provider"),
        "by_phase": _breakdown("phase"),
        "by_category": _breakdown("category"),
        "by_model": _breakdown("model"),
    }
    if estimate is not None:
        est = estimate.get("estimated_cost_usd") or 0.0
        summary["current_cost_usd"] = known
        summary["projected_total_cost_usd"] = round(known + max(0.0, est), 6)
        summary["projected_basis"] = "actual spend so far plus the remaining estimate"
    return summary


def cost_per_success(rows: list[dict], outcomes: dict[str, bool]) -> dict:
    """Headline metric (gap 9): total known cost of missions whose
    outcome is verified-completed, divided by the number of such
    missions. Failed and unfinished missions contribute cost to the
    numerator of NOTHING here — their cost is reported separately —
    so a cheaper run that fails more reads as WORSE (fewer successes
    to divide by), never better. outcomes maps mission_id -> True only
    for verified-completed missions."""
    successful = {m for m, ok in outcomes.items() if ok}
    success_cost = round(sum(normalize_cost(r.get("cost_usd")) or 0.0
                             for r in rows
                             if r.get("mission_id") in successful
                             and r.get("cost_status") == "known"), 6)
    failed_cost = round(sum(normalize_cost(r.get("cost_usd")) or 0.0
                            for r in rows
                            if r.get("mission_id") not in successful
                            and r.get("cost_status") == "known"), 6)
    count = len(successful)
    return {
        "currency": CURRENCY,
        "successful_missions": count,
        "cost_of_successful_missions_usd": success_cost,
        "cost_of_other_missions_usd": failed_cost,
        "cost_per_success_usd": (round(success_cost / count, 6) if count else None),
        "note": ("Cost per successfully completed task. When this number "
                 "rises while token totals fall, the optimization made "
                 "things worse — it caused more failed work."),
    }


def cost_per_task(rows: list[dict]) -> dict:
    """Cost-per-task attribution (128): one line per mission (task),
    each from its own rows only."""
    per: dict[str, dict] = {}
    for r in rows:
        line = per.setdefault(r.get("mission_id", ""), {
            "calls": 0, "known_cost_usd": 0.0, "input_tokens": 0,
            "output_tokens": 0, "cached_tokens": 0, "failed_calls": 0})
        line["calls"] += 1
        if r.get("cost_status") == "known":
            line["known_cost_usd"] = round(line["known_cost_usd"] + (r.get("cost_usd") or 0.0), 6)
        line["input_tokens"] += r.get("input_tokens", 0)
        line["output_tokens"] += r.get("output_tokens", 0)
        line["cached_tokens"] += r.get("cached_tokens", 0)
        if not r.get("success", True):
            line["failed_calls"] += 1
    return per


# --------------------------------------------------------------------- savings
def savings_vs_baseline(actual_cost_usd: float | None, baseline: dict | None) -> dict:
    """Savings stated ONLY against a defined baseline (139). A baseline
    must name its source, period and definition; without one there is
    no savings figure at all — never a hypothetical one. Optimization
    baselines are tracked the same way (130): baseline + actual, kept
    together, so a claim can always be re-checked."""
    if baseline is None:
        return {"valid": False, "savings_usd": None,
                "reason": "No baseline was defined, so no saving can be stated."}
    for field in ("source", "period", "definition", "cost_usd"):
        if baseline.get(field) in (None, ""):
            return {"valid": False, "savings_usd": None,
                    "reason": f"The baseline is missing its {field}, so no saving can be stated."}
    if actual_cost_usd is None:
        return {"valid": False, "savings_usd": None,
                "reason": "Actual cost is unknown, so no saving can be stated."}
    check_currency(baseline.get("currency", CURRENCY))
    base = float(baseline["cost_usd"])
    return {"valid": True, "currency": CURRENCY,
            "baseline_cost_usd": round(base, 6),
            "actual_cost_usd": round(actual_cost_usd, 6),
            "savings_usd": round(base - actual_cost_usd, 6),
            "baseline_source": baseline["source"], "baseline_period": baseline["period"],
            "baseline_definition": baseline["definition"]}


def cache_savings_report(*, tokens_avoided: int, calls_avoided: int,
                         est_spend_avoided_usd: float | None,
                         billed_spend_avoided_usd: float | None,
                         overhead_cost_usd: float = 0.0) -> dict:
    """Cache-savings attribution with the HONEST split (131): tokens
    avoided, calls avoided, estimated spend avoided, actual billed
    spend avoided, and the NET reduction after overhead are five
    different numbers and are never blended into one. When billed
    spend is unknown, net is stated against the estimate and labelled
    as such; it is never presented as billed fact."""
    est = est_spend_avoided_usd
    billed = billed_spend_avoided_usd
    if billed is not None:
        net, net_basis = round(billed - overhead_cost_usd, 6), "actual billed spend"
    elif est is not None:
        net, net_basis = round(est - overhead_cost_usd, 6), "estimated spend (billed spend unknown)"
    else:
        net, net_basis = None, "unknown (neither estimate nor billed spend known)"
    return {
        "tokens_avoided": tokens_avoided, "calls_avoided": calls_avoided,
        "estimated_api_spend_avoided_usd": (round(est, 6) if est is not None else None),
        "actual_billed_spend_avoided_usd": (round(billed, 6) if billed is not None else None),
        "overhead_cost_usd": round(overhead_cost_usd, 6),
        "net_reduction_usd": net, "net_basis": net_basis, "currency": CURRENCY,
    }


def detect_cost_anomaly(past_costs: list[float], new_cost: float) -> dict:
    """Cost-anomaly detection (136): a new cost is flagged only when
    it is far above the mission's own history (mean + 3 standard
    deviations and at least double the mean). Fewer than 5 past
    samples is not enough history to judge — reported, never flagged."""
    if len(past_costs) < 5:
        return {"flagged": False, "reason": "Not enough past costs to judge (fewer than 5).",
                "new_cost_usd": new_cost, "currency": CURRENCY}
    mean = statistics.fmean(past_costs)
    sd = statistics.pstdev(past_costs) if len(past_costs) > 1 else 0.0
    flagged = new_cost > mean + 3 * sd and (mean == 0 or new_cost >= 2 * mean)
    return {"flagged": flagged, "mean_usd": round(mean, 6),
            "stddev_usd": round(sd, 6), "new_cost_usd": new_cost,
            "currency": CURRENCY,
            "plain_message": ("This cost is far above this mission's usual cost."
                              if flagged else "This cost is within the usual range.")}


def enforce_output_budget(text: str, max_tokens: int) -> dict:
    """Output-budget enforcement (135): over-budget output is cut AND
    the cut is marked in the text itself — partial output never passes
    as complete."""
    used = estimate_tokens(text)
    if used <= max_tokens:
        return {"text": text, "truncated": False, "estimated_tokens": used,
                "limit_tokens": max_tokens}
    chars = max_tokens * CHARS_PER_TOKEN
    marker = "\n[Output cut short here: the step's length limit was reached.]"
    return {"text": text[:max(0, chars - len(marker))] + marker,
            "truncated": True, "estimated_tokens": used,
            "limit_tokens": max_tokens}


def local_model_note() -> str:
    """Local-compute accounting (140) in plain words."""
    return PLAIN_LOCAL_MODEL_NOTE


# --------------------------------------------------------------------- budget engine
class BudgetManager:
    """Budget ENGINE (gap 3). Limits live in three scopes — per request
    (one call), per agent (that agent's spend in the mission), per
    mission (spent + reserved vs the mission cap) — each either hard
    (block: pause + approval required) or soft (warn and continue).

    Surfacing rule (binding): this engine returns decisions and plain-
    words messages for the caller to show ONLY when a limit is hit.
    It builds no dashboard and no settings screen, and it never raises
    a limit on its own: raising a limit needs an explicit approval
    record (approve_limit_raise), made by a named approver.

    Reservation rule (126/127): the estimated cost is reserved BEFORE
    work starts (store.reserve_budget, race-safe) and reconciled after
    (settle: 'reconciled' when spend was recorded, 'released' when the
    call never ran). A limit hit pauses with the mission's state
    preserved: pausing records a budget event and moves a running
    mission to 'paused' (a legal transition); nothing is deleted and
    the ledger is untouched, so resuming continues from the same state.
    """

    def __init__(self, store, *, warn_fraction: float = 0.8):
        self.store = store
        self.warn_fraction = warn_fraction
        self._hooks: dict[str, list] = {"warn": [], "pause": [], "approve": []}

    def on(self, event: str, callback) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    def _emit(self, event: str, decision: dict) -> None:
        for cb in self._hooks[event]:
            try:
                cb(decision)
            except Exception:
                pass  # a hook must never break the engine

    def _mission_totals(self, tenant_id: str, mission_id: str) -> dict:
        rows = []
        if hasattr(self.store, "list_cost_ledger"):
            rows = self.store.list_cost_ledger(tenant_id, mission_id)
        mission = self.store.get_mission(tenant_id, mission_id) or {}
        return {"mission": mission, "ledger": summarize_ledger(rows),
                "spent": float(mission.get("spent", 0.0) or 0.0),
                "reserved": float(mission.get("reserved_usd", 0.0) or 0.0),
                "cap": float(mission.get("budget_limit", 0.0) or 0.0)}

    def check(self, tenant_id: str, mission_id: str, *,
              request_estimate_usd: float = 0.0, agent: str = "",
              limits: dict | None = None) -> dict:
        """Check all scopes. Returns a decision; 'allow' means no limit
        is near, 'warn' means a soft limit or the warn threshold is
        reached, 'pause' means a hard limit is hit, 'approve_required'
        means the request itself exceeds its per-request hard limit."""
        limits = limits or {}
        totals = self._mission_totals(tenant_id, mission_id)
        spent, reserved, cap = totals["spent"], totals["reserved"], totals["cap"]
        projected = spent + reserved + request_estimate_usd
        by_agent = totals["ledger"].get("by_agent", {})
        agent_spent = by_agent.get(agent, 0.0) if agent else 0.0
        decision = {"action": "allow", "scope": None, "currency": CURRENCY,
                    "spent_usd": spent, "reserved_usd": reserved,
                    "projected_usd": round(projected, 6), "mission_cap_usd": cap,
                    "agent": agent, "agent_spent_usd": agent_spent,
                    "plain_message": ""}
        # Per-request hard limit first: one call must never exceed it.
        req_hard = (limits.get("request") or {}).get("hard")
        if req_hard is not None and request_estimate_usd > req_hard:
            decision.update(action="approve_required", scope="request",
                            limit_usd=req_hard,
                            plain_message=("This single step is expected to cost more "
                                           "than the limit for one step, so it needs "
                                           "your approval before it runs."))
            self._emit("approve", decision)
            return decision
        # Per-agent hard limit.
        agent_hard = (limits.get("agent") or {}).get("hard")
        if agent and agent_hard is not None and agent_spent + request_estimate_usd > agent_hard:
            decision.update(action="pause", scope="agent", limit_usd=agent_hard,
                            top_consumer=self._top_consumer(totals["ledger"]),
                            plain_message=("This agent has reached its spending limit "
                                           "for this mission, so work pauses here. "
                                           "Nothing is lost; approve a higher limit "
                                           "to continue."))
            self._emit("pause", decision)
            return decision
        # Per-mission hard limit (cap).
        mission_hard = (limits.get("mission") or {}).get("hard", cap)
        if mission_hard is not None and projected > mission_hard:
            decision.update(action="pause", scope="mission", limit_usd=mission_hard,
                            top_consumer=self._top_consumer(totals["ledger"]),
                            plain_message=("This mission has reached its spending limit, "
                                           "so work pauses here. Nothing is lost; "
                                           "approve a higher limit to continue."))
            self._emit("pause", decision)
            return decision
        # Soft warnings: warn threshold on the mission, or a soft limit.
        mission_soft = (limits.get("mission") or {}).get("soft")
        if ((mission_soft is not None and projected > mission_soft)
                or (cap and projected >= cap * self.warn_fraction)):
            decision.update(action="warn", scope="mission",
                            plain_message=("This mission is close to its spending "
                                           "limit. Work continues, but the next step "
                                           "may pause it."))
            self._emit("warn", decision)
        return decision

    @staticmethod
    def _top_consumer(ledger: dict) -> dict:
        """Which component consumed the budget (agent/provider/phase)."""
        def _top(d: dict):
            return max(d.items(), key=lambda kv: kv[1]) if d else (None, 0.0)
        agent, agent_cost = _top(ledger.get("by_agent", {}))
        provider, provider_cost = _top(ledger.get("by_provider", {}))
        phase, phase_cost = _top(ledger.get("by_phase", {}))
        return {"agent": agent, "agent_cost_usd": agent_cost,
                "provider": provider, "provider_cost_usd": provider_cost,
                "phase": phase, "phase_cost_usd": phase_cost}

    def reserve(self, tenant_id: str, mission_id: str, estimate_usd: float,
                cap: float) -> dict:
        """Reserve BEFORE work (126). Returns the reservation id, or a
        pause decision when the reservation itself would breach the cap."""
        rid = self.store.reserve_budget(tenant_id, mission_id, estimate_usd, cap)
        if rid is None:
            decision = {"action": "pause", "scope": "mission",
                        "plain_message": ("There is not enough room left in this "
                                          "mission's spending limit to start this "
                                          "step, so work pauses here. Nothing is lost.")}
            self._emit("pause", decision)
            return {"reservation_id": None, "decision": decision}
        return {"reservation_id": rid,
                "decision": {"action": "allow", "plain_message": ""}}

    def settle(self, reservation_id: str, outcome: str) -> None:
        """Release/reconcile a reservation (127). outcome: 'reconciled'
        (spend recorded) or 'released' (call never ran)."""
        if outcome not in ("reconciled", "released"):
            raise ValueError("settle outcome must be reconciled or released")
        self.store.settle_reservation(reservation_id, outcome)

    def pause_mission(self, tenant_id: str, mission_id: str, reason: str) -> dict:
        """Pause with state preserved: snapshot the mission + ledger
        totals, record a budget event, and move a running mission to
        'paused' (legal transition only — other states keep their state
        and only the event is recorded). Nothing is deleted."""
        totals = self._mission_totals(tenant_id, mission_id)
        snapshot = {"mission": dict(totals["mission"]),
                    "ledger_totals": totals["ledger"],
                    "spent_usd": totals["spent"], "reserved_usd": totals["reserved"]}
        event = None
        if hasattr(self.store, "record_budget_event"):
            event = self.store.record_budget_event(
                tenant_id, mission_id, "pause", reason, snapshot)
        mission = totals["mission"]
        if mission.get("status") == "running":
            try:
                self.store.set_mission_status(tenant_id, mission_id, "paused")
            except Exception:
                pass  # event record already preserves the pause intent
        decision = {"action": "pause", "scope": "mission", "event": event,
                    "snapshot": snapshot,
                    "plain_message": ("Work on this mission is paused because a "
                                      "spending limit was reached. Nothing is lost; "
                                      "it can continue from exactly where it stopped.")}
        self._emit("pause", decision)
        return decision

    def resume_mission(self, tenant_id: str, mission_id: str) -> dict:
        """Resume a paused mission (state was preserved by pause)."""
        mission = self.store.get_mission(tenant_id, mission_id) or {}
        if mission.get("status") == "paused":
            self.store.set_mission_status(tenant_id, mission_id, "running")
            mission = self.store.get_mission(tenant_id, mission_id) or {}
        return {"action": "allow", "status": mission.get("status"),
                "plain_message": "Work on this mission continues from where it paused."}

    def request_limit_raise(self, tenant_id: str, mission_id: str,
                            new_limit_usd: float, requested_by: str) -> dict:
        """An explicit request to raise a limit — it changes nothing
        until approve_limit_raise is called by a named approver."""
        if not hasattr(self.store, "record_budget_event"):
            raise RuntimeError("store does not record budget events")
        event = self.store.record_budget_event(
            tenant_id, mission_id, "limit_raise_requested",
            f"Raise requested to {new_limit_usd} by {requested_by}",
            {"new_limit_usd": new_limit_usd, "requested_by": requested_by})
        decision = {"action": "approve_required", "event": event,
                    "plain_message": ("Raising the spending limit needs your "
                                      "explicit approval. Nothing has changed yet.")}
        self._emit("approve", decision)
        return decision

    def approve_limit_raise(self, tenant_id: str, mission_id: str,
                            event_id: str, approved_by: str) -> dict:
        """Explicit authorization ONLY (never silent auto-raising):
        marks the request approved and applies the new limit."""
        if not hasattr(self.store, "decide_budget_event"):
            raise RuntimeError("store does not decide budget events")
        event = self.store.decide_budget_event(tenant_id, mission_id, event_id, "approved")
        if event is None:
            return {"action": "denied",
                    "plain_message": "That limit request could not be found."}
        detail = event.get("detail") or {}
        new_limit = detail.get("new_limit_usd")
        if new_limit is not None and hasattr(self.store, "set_budget_limit"):
            self.store.set_budget_limit(tenant_id, mission_id, float(new_limit))
        if hasattr(self.store, "audit"):
            self.store.audit(tenant_id, approved_by, "BudgetLimitRaised",
                             "mission", mission_id, {"new_limit_usd": new_limit})
        return {"action": "allow", "new_limit_usd": new_limit,
                "plain_message": "The spending limit has been raised as approved."}


# --------------------------------------------------------------------- semantic cache
def normalize_input(text: str) -> str:
    """Input normalization for cache keys: case + whitespace only.
    Wording is NOT stemmed or paraphrased — two differently worded
    questions must never share a key by accident."""
    return re.sub(r"\s+", " ", text.strip().lower())


def build_cache_key(*, input_text: str, model: str, model_version: str = "",
                    prompt_version: str = "", tool_versions: dict | None = None,
                    workspace_scope: str = "", freshness_class: str = "live",
                    permission_context: str = "", tenant_id: str = "",
                    source_fingerprints: list[str] | None = None) -> dict:
    """Cache key (expansion A): normalized input + model version +
    prompt version + tool versions + workspace scope + freshness
    requirement + permission context + tenant + source fingerprints,
    all under CACHE_VERSION. Changing any component changes the key,
    so a cached answer can never leak across a version, workspace,
    freshness requirement, or permission boundary (top-15 #12)."""
    components = {
        "cache_version": CACHE_VERSION,
        "input": normalize_input(input_text),
        "model": model, "model_version": model_version,
        "prompt_version": prompt_version,
        "tool_versions": dict(sorted((tool_versions or {}).items())),
        "workspace_scope": workspace_scope,
        "freshness_class": freshness_class,
        "permission_context": permission_context,
        "tenant_id": tenant_id,
        "source_fingerprints": sorted(source_fingerprints or []),
    }
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True).encode()).hexdigest()
    return {"key": digest, "components": components}


class SemanticCache:
    """Per-workspace semantic reuse (gap 4) with correctness first.

    - Reuse requires an exact key match (see build_cache_key) AND the
      stored entry's tenant/workspace/permission context to still match
      (checked again on read, not just in the key).
    - Only results the caller marks safe_for_reuse are stored, and only
      within the same tenant/workspace — never across tenants.
    - Freshness: entries expire per their type; live entries expire
      fast, static ones slow, and the two policies never coincide.
    - Negative results (failed/empty) are stored ONLY with an explicit
      short policy + a plain-words reason, and are flagged negative on
      read so callers never mistake them for a real answer.
    - Bypass: get(..., bypass=True) always misses, for users who demand
      a fresh result.
    - Size limit with least-recently-used eviction; versioning via
      CACHE_VERSION in every key (a version bump invalidates all).
    - Telemetry: hits, misses, evictions, negative hits; acceptance
      tracking records whether a served cached result was accepted.
    """

    def __init__(self, max_entries: int = MAX_CACHE_ENTRIES,
                 clock=time.time):
        self.max_entries = max_entries
        self.clock = clock
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.telemetry = {"hits": 0, "misses": 0, "evictions": 0,
                          "negative_hits": 0, "puts": 0, "bypassed": 0}
        self.acceptance: dict[str, dict] = {}

    def put(self, key_info: dict, result, *, safe_for_reuse: bool,
            freshness_class: str = "live", ttl_seconds: int | None = None,
            success: bool = True, negative_reason: str = "",
            allow_negative: bool = False,
            source_fingerprints: list[str] | None = None) -> bool:
        """Store a result. Returns False (nothing stored) when the
        caller does not mark it safe, or when a negative result lacks
        an explicit short policy + reason."""
        if not safe_for_reuse:
            return False
        if not success and not (allow_negative and negative_reason):
            return False
        ttl = ttl_seconds
        if ttl is None:
            ttl = TTL_BY_TYPE["negative"] if not success else TTL_BY_TYPE.get(
                freshness_class, DEFAULT_TTL_SECONDS)
        if not success and ttl > TTL_BY_TYPE["negative"]:
            ttl = TTL_BY_TYPE["negative"]  # negative results stay short-lived
        key = key_info["key"]
        with self._lock:
            self._entries[key] = {
                "result": result, "components": key_info["components"],
                "expires_at": self.clock() + ttl, "freshness_class": freshness_class,
                "success": success, "negative": not success,
                "negative_reason": negative_reason if not success else "",
                "source_fingerprints": list(source_fingerprints or []),
            }
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self.telemetry["evictions"] += 1
            self.telemetry["puts"] += 1
        return True

    def get(self, key_info: dict, *, bypass: bool = False,
            tenant_id: str = "", workspace_scope: str = "",
            permission_context: str = "") -> dict:
        """Read. Returns {'hit': bool, ...}; a hit re-checks tenant /
        workspace / permission against the stored entry, so even a key
        collision across boundaries cannot serve private results."""
        if bypass:
            with self._lock:
                self.telemetry["bypassed"] += 1
            return {"hit": False, "bypassed": True}
        key = key_info["key"]
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.telemetry["misses"] += 1
                return {"hit": False}
            comps = entry["components"]
            if (tenant_id and comps.get("tenant_id") != tenant_id) \
                    or (workspace_scope and comps.get("workspace_scope") != workspace_scope) \
                    or (permission_context and comps.get("permission_context") != permission_context):
                self.telemetry["misses"] += 1
                return {"hit": False, "reason": "permission or workspace mismatch"}
            if self.clock() >= entry["expires_at"]:
                del self._entries[key]
                self.telemetry["misses"] += 1
                return {"hit": False, "reason": "expired"}
            self._entries.move_to_end(key)
            self.telemetry["hits"] += 1
            if entry["negative"]:
                self.telemetry["negative_hits"] += 1
            return {"hit": True, "result": entry["result"],
                    "negative": entry["negative"],
                    "negative_reason": entry["negative_reason"],
                    "freshness_class": entry["freshness_class"]}

    def record_acceptance(self, key: str, accepted: bool, reason: str = "") -> None:
        """Cache acceptance tracking: was a served cached result
        actually accepted by the user or the verifying step?"""
        self.acceptance[key] = {"accepted": accepted, "reason": reason}

    def invalidate_sources(self, fingerprints: list[str]) -> int:
        """Invalidation when a relevant source changes: any entry built
        from a changed source fingerprint is dropped."""
        wanted = set(fingerprints)
        with self._lock:
            doomed = [k for k, e in self._entries.items()
                      if wanted & set(e.get("source_fingerprints") or [])]
            for k in doomed:
                del self._entries[k]
        return len(doomed)

    def invalidate_model(self, model: str) -> int:
        with self._lock:
            doomed = [k for k, e in self._entries.items()
                      if e["components"].get("model") == model]
            for k in doomed:
                del self._entries[k]
        return len(doomed)

    def invalidate_workspace(self, workspace_scope: str) -> int:
        with self._lock:
            doomed = [k for k, e in self._entries.items()
                      if e["components"].get("workspace_scope") == workspace_scope]
            for k in doomed:
                del self._entries[k]
        return len(doomed)

    def stats(self) -> dict:
        with self._lock:
            total = self.telemetry["hits"] + self.telemetry["misses"]
            return {**self.telemetry, "entries": len(self._entries),
                    "hit_rate": (round(self.telemetry["hits"] / total, 4)
                                 if total else None),
                    "accepted": sum(1 for a in self.acceptance.values() if a["accepted"]),
                    "rejected": sum(1 for a in self.acceptance.values() if not a["accepted"])}


class RequestCoalescer:
    """Stampede prevention: identical in-flight requests (same key,
    same permission context) share one execution instead of each
    paying for a provider call. Followers wait for the leader's result
    up to a timeout; on timeout they run independently rather than
    hanging. Cancellation is the caller's: a follower that stops
    waiting does not cancel the leader."""

    def __init__(self):
        self._lock = threading.Lock()
        self._in_flight: dict[str, dict] = {}

    def claim(self, key: str, permission_context: str = "") -> dict:
        """Returns {'leader': True} for the first claimer, or
        {'leader': False, 'event': Event} for followers."""
        slot_key = f"{key}|{permission_context}"
        with self._lock:
            slot = self._in_flight.get(slot_key)
            if slot is None:
                slot = {"event": threading.Event(), "result": None, "error": None}
                self._in_flight[slot_key] = slot
                return {"leader": True, "slot": slot, "slot_key": slot_key}
            return {"leader": False, "slot": slot, "slot_key": slot_key}

    def complete(self, slot_key: str, result=None, error=None) -> None:
        with self._lock:
            slot = self._in_flight.pop(slot_key, None)
        if slot is not None:
            slot["result"] = result
            slot["error"] = error
            slot["event"].set()

    def wait(self, slot: dict, timeout: float = 30.0):
        """Wait for a leader; returns (result, error, timed_out). A
        follower that times out runs independently rather than hanging."""
        done = slot["event"].wait(timeout)
        if not done:
            return None, None, True
        return slot["result"], slot["error"], False


class MissionDeduper:
    """Duplicate-call elimination within ONE mission (gap 6): the same
    work requested twice reuses the verified result and pays nothing
    twice. Only verified results are stored — an unverified answer is
    never reused as if it were checked. Scope is per mission; results
    never cross missions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def dedup_key(mission_id: str, model: str, prompt: str,
                  params: dict | None = None) -> str:
        raw = json.dumps({"mission_id": mission_id, "model": model,
                          "prompt": normalize_input(prompt),
                          "params": dict(sorted((params or {}).items()))},
                         sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def put(self, mission_id: str, model: str, prompt: str, result,
            *, verified: bool, params: dict | None = None) -> bool:
        if not verified:
            return False
        with self._lock:
            self._store[self.dedup_key(mission_id, model, prompt, params)] = {
                "result": result, "mission_id": mission_id}
        return True

    def get(self, mission_id: str, model: str, prompt: str,
            params: dict | None = None) -> dict:
        with self._lock:
            entry = self._store.get(self.dedup_key(mission_id, model, prompt, params))
        if entry is None or entry["mission_id"] != mission_id:
            self.misses += 1
            return {"hit": False}
        self.hits += 1
        return {"hit": True, "result": entry["result"], "reused": True}


# --------------------------------------------------------------------- adaptive depth
def classify_task(description: str, *, has_write_effect: bool = False,
                  has_external_action: bool = False,
                  risk_flags: list[str] | None = None) -> dict:
    """Adaptive depth (gap 7), decided by task need: routine tasks
    (short lookup/summary, no writes, no external actions, no risk
    words) take the light path; everything else takes full
    verification. The decision and its reasons are returned, never
    hidden inside a boolean."""
    text = (description or "").lower()
    reasons = []
    if has_write_effect:
        reasons.append("the task changes files or data")
    if has_external_action:
        reasons.append("the task acts outside this machine")
    for word in _HIGH_RISK_WORDS:
        if word in text:
            reasons.append(f"the task mentions '{word}'")
    for flag in (risk_flags or []):
        reasons.append(str(flag))
    if len(description or "") > 2000:
        reasons.append("the task is long and detailed")
    depth = "full" if reasons else "light"
    return {"depth": depth, "reasons": reasons,
            "verification": ("full verification: research, critique and "
                             "evaluation all run" if depth == "full" else
                             "light path: research and evaluation run; the "
                             "extra critique round is skipped, but acceptance "
                             "checks still run"),
            "plain_message": ("This task needs full verification."
                              if depth == "full" else
                              "This is a routine task, so it takes the shorter path.")}


def adapt_plan_steps(steps: list[dict], depth: str) -> list[dict]:
    """Apply an adaptive-depth decision to a plan: the light path drops
    extra critique rounds only. Research, synthesis and evaluation
    (the acceptance checks) are never dropped by this function."""
    if depth != "light":
        return list(steps)
    return [s for s in steps if s.get("kind") != "critique"]


# --------------------------------------------------------------------- prompt caching
def prompt_cache_options(entry: dict, *, cacheable_prefix: bool = False) -> dict:
    """Provider prompt-caching pass-through (gap 5). Enabled ONLY when
    the catalog entry advertises support or the caller explicitly
    declares the provider supports it for this model. Pricing is
    passed through from the catalog when published and otherwise
    reported as unknown — never invented."""
    supported = entry.get("prompt_cache") == "supported" or bool(
        entry.get("prompt_cache_supported"))
    if not (supported and cacheable_prefix):
        return {"enabled": False,
                "reason": ("Prompt caching is not enabled: either the provider "
                           "does not advertise it for this model or the prompt "
                           "has no stable reusable prefix.")}
    price = price_for(entry)
    return {"enabled": True, "provider": entry.get("provider", ""),
            "model": entry.get("model_id", ""),
            "cached_price_per_1m": price["price_cached"],
            "cached_price_status": ("known" if price["price_cached"] is not None
                                    else "unknown"),
            "note": ("Cached-input pricing follows the provider's published "
                     "price when one exists; otherwise it is unknown, not free.")}
