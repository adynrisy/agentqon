"""V3: routing engine — hard filters first, deterministic scoring,
unknown price never treated as free (Master Build §105.4/§114)."""
import pytest

from app.router import NoEligibleModel, RouteRequest, route


def conn(cid, *, state="active", kind="direct_provider", allowed=None, blocked=None,
         policy=None):
    return {"id": cid, "state": state, "kind": kind, "allowed_models": allowed or [],
            "blocked_models": blocked or [], "data_policy": policy or {},
            "tenant_id": "org_x"}


def entry(cid, mid, *, pin=None, pout=None, pstatus="known", deprecated=False):
    return {"connection_id": cid, "model_id": mid, "provider": "p", "display_name": mid,
            "capabilities": {"chat": "advertised"}, "price_in": pin, "price_out": pout,
            "price_status": pstatus, "health": "ok", "last_seen": "2026-10-10T00:00:00+00:00",
            "deprecated": deprecated}


def req(**kw):
    base = dict(tenant_id="org_x", mode="balanced")
    base.update(kw)
    return RouteRequest(**base)


def test_lowest_cost_picks_cheaper_known_price():
    conns = {"c1": conn("c1")}
    entries = [entry("c1", "pricey", pin=10.0, pout=30.0),
               entry("c1", "cheap", pin=0.5, pout=1.0)]
    d = route(entries, conns, {}, req(mode="lowest_cost_acceptable"))
    assert d["model_id"] == "cheap" and d["estimate_confidence"] == "high"


def test_hard_filters_blocked_allowlist_inactive_classification():
    conns = {"c1": conn("c1", blocked=["bad-model"]),
             "c2": conn("c2", state="revoked"),
             "c3": conn("c3", allowed=["only-this"])}
    entries = [entry("c1", "bad-model"), entry("c2", "ghost"),
               entry("c3", "not-allowed"), entry("c3", "only-this")]
    d = route(entries, conns, {}, req())
    assert d["model_id"] == "only-this"
    assert d["filtered_counts"]["blocked"] == 1
    assert d["filtered_counts"]["connection_inactive"] == 1
    assert d["filtered_counts"]["not_allowlisted"] == 1
    # Confidential missions cannot use a plain direct provider connection.
    with pytest.raises(NoEligibleModel):
        route([entry("c1", "m", pin=1, pout=1)], {"c1": conn("c1")}, {},
              req(data_classification="confidential"))
    # ...but a local endpoint qualifies.
    local = {"c9": conn("c9", kind="local_endpoint")}
    d = route([entry("c9", "local-m", pin=0, pout=0)], local, {},
              req(data_classification="confidential"))
    assert d["model_id"] == "local-m"


def test_confidential_allows_byok_kind_with_local_url():
    # v21.3: locality is derived from the stored URL, not the kind — an
    # API-key (direct_provider) connection whose base_url sits on the
    # user's own machine qualifies as local for confidential missions.
    conns = {"c7": {**conn("c7"), "base_url": "http://127.0.0.1:11434/v1"}}
    d = route([entry("c7", "lan-m", pin=1, pout=1)], conns, {},
              req(data_classification="confidential"))
    assert d["model_id"] == "lan-m"
    assert "local endpoint: no provider egress" in d["reason_codes"]
    # The same kind with a public URL still does not qualify.
    conns = {"c8": {**conn("c8"), "base_url": "https://api.openai.com/v1"}}
    with pytest.raises(NoEligibleModel):
        route([entry("c8", "cloud-m", pin=1, pout=1)], conns, {},
              req(data_classification="confidential"))


def test_pinned_mode_and_no_eligible():
    conns = {"c1": conn("c1")}
    entries = [entry("c1", "a"), entry("c1", "b")]
    d = route(entries, conns, {}, req(mode="pinned_model", pinned_model_id="b"))
    assert d["model_id"] == "b"
    with pytest.raises(NoEligibleModel) as exc:
        route(entries, conns, {}, req(mode="pinned_model", pinned_model_id="zzz"))
    assert exc.value.code == "NO_ELIGIBLE_MODEL"
    assert exc.value.filtered_counts["pinned_mismatch"] == 2


def test_unknown_price_needs_reserve_within_budget():
    conns = {"c1": conn("c1")}
    entries = [entry("c1", "mystery", pstatus="unknown")]
    # Budget smaller than the unknown-price reserve -> filtered out.
    with pytest.raises(NoEligibleModel) as exc:
        route(entries, conns, {}, req(budget_remaining_usd=0.10,
                                      unknown_price_reserve_usd=0.50))
    assert exc.value.filtered_counts["budget_unknown_price"] == 1
    # With room for the reserve it routes, labelled unknown — never $0.
    d = route(entries, conns, {}, req(budget_remaining_usd=5.0,
                                      unknown_price_reserve_usd=0.50))
    assert d["estimated_cost"] is None and d["estimate_confidence"] == "unknown"


def test_deterministic_for_fixed_inputs():
    conns = {"c1": conn("c1")}
    entries = [entry("c1", "m2", pin=1, pout=1), entry("c1", "m1", pin=1, pout=1)]
    d1 = route(entries, conns, {}, req(mode="lowest_cost_acceptable"))
    d2 = route(list(reversed(entries)), conns, {}, req(mode="lowest_cost_acceptable"))
    assert d1["model_id"] == d2["model_id"] == "m1"  # stable tie-break
