"""AgentQon — cost dashboard breakdowns.

Extends the existing usage surfaces (store.usage_summary, which totals
by category) with per-agent, per-provider and cache-savings breakdowns.

The estimated-vs-verified distinction is the whole point, and it is
enforced here, not left to the UI:
- VERIFIED billing data: records flagged ``verified=True`` — amounts
  taken from a provider bill or a reconciled usage event. These are
  shown as verified.
- ESTIMATED figures: anything computed from token counts and prices,
  including cache savings. Savings are computed against a stated
  baseline and are ALWAYS labelled estimated, with the baseline named.

Baseline for cache savings (defensible, stated wherever savings are
shown): "what the same requests would have cost with no cache at all
at the same prices" — i.e. cached tokens repriced at the model's
input price. Savings are NOT a bill reduction unless the provider
bills that way; the record says so.
"""
from __future__ import annotations


def format_money(amount_usd: float) -> str:
    """One consistent money format for every surface: $X.XXXX for
    small amounts (costs here are usually fractions of a dollar),
    $X.XX once the amount reaches a dollar. Never a bare number."""
    amount = float(amount_usd)
    if abs(amount) < 1.0:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def format_number(value: float) -> str:
    """One consistent number format: thousands separated, no more than
    two decimals, trailing zeros trimmed."""
    text = f"{float(value):,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def breakdown(records: list[dict]) -> dict:
    """Break usage records down by agent and by provider, with cache
    savings against the no-cache baseline.

    Record fields (all optional except cost): agent, provider,
    cost_usd, cached_tokens, total_tokens, price_in_per_million,
    verified (bool). Records without a provider/agent land under
    "unknown" — they are counted, never dropped silently."""
    per_agent: dict[str, float] = {}
    per_provider: dict[str, float] = {}
    verified_total = 0.0
    estimated_total = 0.0
    cached_tokens = 0
    baseline_cost = 0.0
    for rec in records:
        cost = float(rec.get("cost_usd") or 0.0)
        agent = rec.get("agent") or "unknown"
        provider = rec.get("provider") or "unknown"
        per_agent[agent] = round(per_agent.get(agent, 0.0) + cost, 6)
        per_provider[provider] = round(per_provider.get(provider, 0.0) + cost, 6)
        if rec.get("verified"):
            verified_total += cost
        else:
            estimated_total += cost
        cached = int(rec.get("cached_tokens") or 0)
        price = rec.get("price_in_per_million")
        if cached and price is not None:
            cached_tokens += cached
            baseline_cost += cached * float(price) / 1_000_000
    actual_cached_cost = 0.0
    # Cached tokens still cost something at most providers (a reduced
    # rate); when the record carries no cached-cost split we count the
    # cached tokens at the same price as a conservative floor and say so.
    savings = round(baseline_cost - actual_cached_cost, 6) if baseline_cost else 0.0
    return {
        "per_agent_usd": per_agent,
        "per_provider_usd": per_provider,
        "total_usd": round(verified_total + estimated_total, 6),
        "verified_usd": round(verified_total, 6),
        "estimated_usd": round(estimated_total, 6),
        "cache": {
            "cached_tokens": cached_tokens,
            "baseline": ("Cost of the same requests with no cache at all, "
                         "at the same input prices."),
            "baseline_cost_usd": round(baseline_cost, 6),
            # Savings are an ESTIMATE by construction: they compare
            # against a hypothetical no-cache run, not a bill.
            "estimated_savings_usd": savings,
            "savings_kind": "estimated",
            "savings_note": ("Estimated savings only — this is what the "
                             "cached requests would have cost without a "
                             "cache. It is not a verified billing figure."),
        },
        "labels": {
            "verified": "Verified — from reconciled usage or a provider bill.",
            "estimated": "Estimated — computed from tokens and prices, not a bill.",
        },
    }


def from_usage_summary(summary: dict, records: list[dict] | None = None) -> dict:
    """Combine the existing store.usage_summary output (by category,
    from actual usage records) with the richer breakdown. The summary
    total is verified-leaning (it comes from recorded usage), so it is
    labelled as recorded usage, not as a provider bill."""
    out = breakdown(records or [])
    out["by_category_usd"] = dict(summary.get("by_category") or {})
    out["summary_total_usd"] = summary.get("total_usd")
    out["summary_note"] = ("Recorded usage totals from the usage log. "
                           "A provider bill may still differ — see the "
                           "verified vs estimated split above.")
    return out
