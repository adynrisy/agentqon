"""W1 cost engine: estimator, ledger, budget engine, cache, dedup,
adaptive depth, attribution, cost-per-success, savings, reconciliation.
No test file edits to existing suites; this file is new."""
import tempfile
import threading
import time

import pytest

from app import cost_engine as ce
from app.store import Store

ENTRY = {"price_in": 2.0, "price_out": 8.0, "price_status": "known",
         "price_source": "catalog", "price_effective_date": "2026-10-24"}
UNKNOWN_ENTRY = {"price_status": "unknown"}


def _mission(store, tenant="org_c", cap=10.0):
    # create_mission deliberately stores the internal ceiling, not the
    # passed limit (Simplification Law); set the test cap explicitly.
    m = store.create_mission(tenant, "u", "cost test",
                             {"objective": "x", "deliverables": ["d"], "acceptance": ["a"],
                              "constraints": {"max_cost_usd": cap}}, cap)
    store.set_budget_limit(tenant, m["id"], cap)
    return m["id"]


def _row(**kw):
    base = {"tenant_id": "org_c", "mission_id": "m1", "agent": "a1",
            "provider": "p1", "model": "mod", "phase": "research",
            "input_tokens": 1000, "output_tokens": 500,
            "cost_usd": 0.006, "cost_status": "known"}
    base.update(kw)
    return ce.build_cost_record(**base)


# ------------------------------------------------------------ pricing / estimate
def test_price_call_prices_cached_input_separately():
    priced = ce.price_call({**ENTRY, "price_cached": 0.5}, 10000, 1000, 4000)
    # fresh 6000 @2 + cached 4000 @0.5 + out 1000 @8 = 0.012+0.002+0.008
    assert priced["cost_usd"] == pytest.approx(0.022)
    assert priced["cached_tokens"] == 4000
    assert priced["fresh_input_tokens"] == 6000


def test_price_call_unknown_never_zero():
    priced = ce.price_call(UNKNOWN_ENTRY, 1000, 500)
    assert priced["cost_usd"] is None
    assert priced["cost_status"] == "unknown"


def test_estimate_plan_marks_everything_estimate():
    est = ce.estimate_plan(
        [{"kind": "research", "model": "mod", "est_input_tokens": 10000,
          "est_output_tokens": 1000}],
        {"mod": ENTRY})
    assert est["kind"] == "estimate"
    assert est["per_step"][0]["kind"] == "estimate"
    assert est["estimated_cost_usd"] == pytest.approx(0.028)
    assert "not a bill" in est["note"]


def test_estimate_plan_unknown_price_stays_unknown():
    est = ce.estimate_plan(
        [{"kind": "research", "model": "mod", "est_input_tokens": 1000,
          "est_output_tokens": 100}],
        {"mod": UNKNOWN_ENTRY})
    assert est["price_status"] == "unknown"
    assert est["per_step"][0]["estimated_cost_usd"] is None


def test_reconcile_estimate_exact_tokens_and_variance():
    est = ce.estimate_plan(
        [{"kind": "research", "model": "mod", "est_input_tokens": 10000,
          "est_output_tokens": 1000}], {"mod": ENTRY})
    actual = [_row(input_tokens=10000, output_tokens=1000, cost_usd=0.028)]
    rec = ce.reconcile_estimate(est, actual)
    assert rec["actual_input_tokens"] == 10000
    assert rec["variance_usd"] == pytest.approx(0.0)
    assert rec["actual_is_estimate"] is False


def test_reconcile_counts_unknown_cost_rows_not_dollars():
    est = ce.estimate_plan([], {})
    actual = [_row(cost_usd=None, cost_status="unknown")]
    rec = ce.reconcile_estimate(est, actual)
    assert rec["calls_with_unknown_cost"] == 1
    assert rec["actual_cost_usd"] == 0.0


def test_normalize_cost_rounds_each_call_before_summing():
    assert ce.normalize_cost(0.123456789) == 0.123457
    rows = [_row(cost_usd=0.0000004), _row(cost_usd=0.0000004)]
    assert ce.summarize_ledger(rows)["total_known_cost_usd"] == 0.0


def test_add_usage_rounds_each_cost_before_storing():
    """W3 routed §14 finding — DECIDED: fixed at record precision.
    A dusty float must be stored rounded (6dp), so every total is a
    sum of already-rounded costs, never a sum of raw floats rounded
    only at display time. Display rounding (4dp in usage_summary) is
    presentation-only and is unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        s.add_usage("org_c", mid, "tools", 0.123456789, "dusty cost")
        s.add_usage("org_c", mid, "tools", 0.0000004, "sub-precision cost")
        rows = s._all("SELECT amount_usd FROM usage_records WHERE mission_id=?", (mid,))
        amounts = sorted(r["amount_usd"] for r in rows)
        assert amounts == [0.0, 0.123457]
        us = s.usage_summary("org_c", mid)
        assert us["total_usd"] == pytest.approx(0.1235, abs=0.00005)


def test_project_cost_summary_from_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        m1 = s.create_mission("org_c", "u", "p1 mission",
                              {"objective": "x", "deliverables": ["d"], "acceptance": ["a"],
                               "constraints": {"max_cost_usd": 10.0}}, 10.0, "proj-1")
        m2 = s.create_mission("org_c", "u", "other mission",
                              {"objective": "x", "deliverables": ["d"], "acceptance": ["a"],
                               "constraints": {"max_cost_usd": 10.0}}, 10.0, "proj-2")
        s.record_cost(_row(mission_id=m1["id"], cost_usd=0.10))
        s.record_cost(_row(mission_id=m2["id"], cost_usd=0.99))
        summ = s.project_cost_summary("org_c", "proj-1")
        assert summ["missions"] == 1
        assert summ["known_cost_usd"] == pytest.approx(0.10)
        assert s.project_cost_summary("org_c", "no-such-project")["known_cost_usd"] == 0.0


def test_currency_refused_not_converted():
    with pytest.raises(ValueError):
        ce.build_cost_record(tenant_id="t", mission_id="m", currency="EUR")


def test_build_cost_record_rejects_cached_above_input():
    with pytest.raises(ValueError):
        _row(input_tokens=100, cached_tokens=200)


# ------------------------------------------------------------ ledger in store
def test_record_cost_and_dedup_by_event_key():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        rec = _row(mission_id=mid, provider_request_id="req-1")
        rid = s.record_cost(rec)
        assert rid is not None
        assert s.record_cost(rec) is None  # same event: dedup, no double count
        assert len(s.list_cost_ledger("org_c", mid)) == 1
        mission = s.get_mission("org_c", mid)
        assert mission["spent"] == pytest.approx(0.006)


def test_record_cost_unknown_advances_no_spend():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        s.record_cost(_row(mission_id=mid, cost_usd=None, cost_status="unknown"))
        assert s.get_mission("org_c", mid)["spent"] == 0.0


def test_ledger_summary_breakdowns_and_cost_per_success():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        s.record_cost(_row(mission_id=mid, agent="a1", provider="p1",
                           phase="research", cost_usd=0.01))
        s.record_cost(_row(mission_id=mid, agent="a2", provider="p2",
                           phase="critique", cost_usd=0.02, success=False))
        summ = s.ledger_summary("org_c", mid)
        assert summ["by_agent"] == {"a1": 0.01, "a2": 0.02}
        assert summ["by_phase"]["critique"] == 0.02
        assert summ["failed_calls"] == 1
        # mission is draft, not completed -> no success yet
        assert summ["cost_per_success_usd"] is None
        s.set_mission_status("org_c", mid, "planned")
        s.set_mission_status("org_c", mid, "running")
        s.set_mission_status("org_c", mid, "completed")
        summ = s.ledger_summary("org_c", mid)
        assert summ["cost_per_success_usd"] == pytest.approx(0.03)


def test_usage_summary_merges_ledger_without_double_count():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        s.record_cost(_row(mission_id=mid, category="inference", cost_usd=0.01))
        us = s.usage_summary("org_c", mid)
        assert us["by_category"]["inference"] == pytest.approx(0.01)
        assert us["total_usd"] == pytest.approx(0.01)
        assert us["currency"] == "USD"
        assert us["by_agent"] == {"a1": 0.01}


def test_price_history_versioned_and_latest():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        v1 = s.record_price_change("org_c", "mod", price_in=2.0, price_out=8.0,
                                   price_source="catalog", effective_date="2026-10-01")
        v2 = s.record_price_change("org_c", "mod", price_in=3.0, price_out=9.0,
                                   price_source="catalog", effective_date="2026-10-20")
        assert v1["version"] == 1 and v2["version"] == 2
        assert s.latest_price("org_c", "mod")["price_in"] == 3.0
        assert ce.latest_price([{"model": "mod", "effective_date": "2026-10-01", "version": 1,
                                 "price_in": 2.0},
                                {"model": "mod", "effective_date": "2026-10-20", "version": 2,
                                 "price_in": 3.0}], "mod")["price_in"] == 3.0


def test_reconcile_provider_bill_append_only_adjustment():
    rows = [_row(provider_request_id="req-1", cost_usd=0.01)]
    bill = [{"provider": "p1", "model": "mod", "provider_request_id": "req-1",
             "billed_cost_usd": 0.015, "tenant_id": "org_c", "mission_id": "m1",
             "currency": "USD"}]
    rec = ce.reconcile_provider_bill(rows, bill)
    assert rec["matched"] == 1
    assert len(rec["adjustments"]) == 1
    assert rec["adjustments"][0]["category"] == "billing_adjustment"
    assert rec["adjustments"][0]["cost_usd"] == pytest.approx(0.005)
    assert rows[0]["cost_usd"] == 0.01  # original untouched


# ------------------------------------------------------------ budget engine
def _budget_store():
    tmp = tempfile.TemporaryDirectory()
    s = Store(f"{tmp.name}/t.db", f"{tmp.name}/a")
    mid = _mission(s, cap=1.0)
    return tmp, s, mid


def test_budget_check_warn_soft_and_pause_hard():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    s.record_cost(_row(mission_id=mid, cost_usd=0.85))
    d = bm.check("org_c", mid, request_estimate_usd=0.0)
    assert d["action"] == "warn"  # 0.85 >= 80% of 1.0 cap
    assert "close to" in d["plain_message"]
    d = bm.check("org_c", mid, request_estimate_usd=0.20)
    assert d["action"] == "pause"
    assert d["scope"] == "mission"
    tmp.cleanup()


def test_budget_per_agent_limit_and_top_consumer():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    s.record_cost(_row(mission_id=mid, agent="greedy", provider="p9",
                       phase="research", cost_usd=0.50))
    d = bm.check("org_c", mid, request_estimate_usd=0.10, agent="greedy",
                 limits={"agent": {"hard": 0.55}})
    assert d["action"] == "pause" and d["scope"] == "agent"
    assert d["top_consumer"]["agent"] == "greedy"
    tmp.cleanup()


def test_budget_per_request_hard_limit_needs_approval():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    d = bm.check("org_c", mid, request_estimate_usd=5.0,
                 limits={"request": {"hard": 1.0}})
    assert d["action"] == "approve_required"
    tmp.cleanup()


def test_budget_reserve_settle_and_pause_preserves_state():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    res = bm.reserve("org_c", mid, 0.40, 1.0)
    assert res["reservation_id"] is not None
    bm.settle(res["reservation_id"], "released")
    s.set_mission_status("org_c", mid, "planned")
    s.set_mission_status("org_c", mid, "running")
    s.record_cost(_row(mission_id=mid, cost_usd=0.30))
    paused = bm.pause_mission("org_c", mid, "limit reached in test")
    assert paused["snapshot"]["ledger_totals"]["total_known_cost_usd"] == pytest.approx(0.30)
    assert s.get_mission("org_c", mid)["status"] == "paused"
    assert len(s.list_cost_ledger("org_c", mid)) == 1  # nothing lost
    resumed = bm.resume_mission("org_c", mid)
    assert resumed["status"] == "running"
    tmp.cleanup()


def test_budget_limit_raise_needs_explicit_approval():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    req = bm.request_limit_raise("org_c", mid, 2.0, "tester")
    assert s.get_mission("org_c", mid)["budget_limit"] == 1.0  # unchanged
    out = bm.approve_limit_raise("org_c", mid, req["event"]["id"], "approver")
    assert out["new_limit_usd"] == 2.0
    assert s.get_mission("org_c", mid)["budget_limit"] == 2.0
    tmp.cleanup()


def test_concurrent_reservations_never_exceed_cap():
    tmp, s, mid = _budget_store()
    bm = ce.BudgetManager(s)
    results = []

    def worker():
        results.append(bm.reserve("org_c", mid, 0.30, 1.0)["reservation_id"])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(1 for r in results if r) == 3
    tmp.cleanup()


# ------------------------------------------------------------ cache
def _key(**kw):
    base = {"input_text": "Explain photosynthesis", "model": "mod",
            "model_version": "v1", "prompt_version": "p1",
            "workspace_scope": "ws1", "freshness_class": "static",
            "permission_context": "read", "tenant_id": "org_c"}
    base.update(kw)
    return ce.build_cache_key(**base)


def test_cache_key_changes_with_every_component():
    base = _key()
    for kw in ({"model_version": "v2"}, {"prompt_version": "p2"},
               {"workspace_scope": "ws2"}, {"freshness_class": "live"},
               {"permission_context": "admin"}, {"tenant_id": "org_x"},
               {"tool_versions": {"t": "2"}},
               {"source_fingerprints": ["s2"]}):
        assert _key(**kw)["key"] != base["key"]
    # case/whitespace normalization only: same question, different casing shares
    assert _key(input_text="  EXPLAIN photosynthesis ")["key"] == base["key"]


def test_cache_permission_isolation():
    cache = ce.SemanticCache()
    k = _key()
    assert cache.put(k, "answer", safe_for_reuse=True)
    assert cache.get(k, tenant_id="org_c", workspace_scope="ws1",
                     permission_context="read")["hit"] is True
    assert cache.get(k, tenant_id="org_other", workspace_scope="ws1",
                     permission_context="read")["hit"] is False
    assert cache.get(k, tenant_id="org_c", workspace_scope="ws1",
                     permission_context="admin")["hit"] is False


def test_cache_static_vs_live_ttl_never_share():
    assert ce.TTL_BY_TYPE["static"] != ce.TTL_BY_TYPE["live"]
    now = [1000.0]
    cache = ce.SemanticCache(clock=lambda: now[0])
    cache.put(_key(freshness_class="live"), "live-answer", safe_for_reuse=True,
              freshness_class="live")
    cache.put(_key(freshness_class="static"), "static-answer", safe_for_reuse=True,
              freshness_class="static")
    now[0] += 301  # live expired, static alive
    assert cache.get(_key(freshness_class="live"), tenant_id="org_c")["hit"] is False
    assert cache.get(_key(freshness_class="static"), tenant_id="org_c")["hit"] is True


def test_cache_negative_needs_explicit_policy_and_reason():
    cache = ce.SemanticCache()
    k = _key()
    assert cache.put(k, "", safe_for_reuse=True, success=False) is False
    assert cache.put(k, "", safe_for_reuse=True, success=False,
                     allow_negative=True, negative_reason="") is False
    assert cache.put(k, "", safe_for_reuse=True, success=False,
                     allow_negative=True,
                     negative_reason="No answer was found this time.") is True
    hit = cache.get(k, tenant_id="org_c")
    assert hit["hit"] is True and hit["negative"] is True
    assert "No answer" in hit["negative_reason"]


def test_cache_unsafe_results_never_stored_and_bypass_misses():
    cache = ce.SemanticCache()
    k = _key()
    assert cache.put(k, "secret", safe_for_reuse=False) is False
    cache.put(k, "answer", safe_for_reuse=True)
    assert cache.get(k, tenant_id="org_c", bypass=True)["hit"] is False
    assert cache.get(k, tenant_id="org_c")["hit"] is True


def test_cache_invalidation_on_source_change_and_size_limit():
    cache = ce.SemanticCache(max_entries=2)
    k1, k2, k3 = _key(), _key(input_text="Other question"), _key(input_text="Third")
    cache.put(k1, "a", safe_for_reuse=True, source_fingerprints=["src-1"])
    cache.put(k2, "b", safe_for_reuse=True, source_fingerprints=["src-2"])
    assert cache.invalidate_sources(["src-1"]) == 1
    assert cache.get(k1, tenant_id="org_c")["hit"] is False
    cache.put(k3, "c", safe_for_reuse=True)
    assert cache.stats()["entries"] == 2
    cache.record_acceptance(k2["key"], True)
    assert cache.stats()["accepted"] == 1


def test_cache_version_bump_changes_keys():
    assert ce.CACHE_VERSION
    old = ce.CACHE_VERSION
    ce.CACHE_VERSION = "cost-cache-v2"
    try:
        assert _key()["components"]["cache_version"] == "cost-cache-v2"
    finally:
        ce.CACHE_VERSION = old


def test_coalescer_leader_and_follower_share():
    coal = ce.RequestCoalescer()
    first = coal.claim("k1", "read")
    assert first["leader"] is True
    second = coal.claim("k1", "read")
    assert second["leader"] is False
    other_perm = coal.claim("k1", "admin")
    assert other_perm["leader"] is True  # different permission: no sharing
    coal.complete(first["slot_key"], result="shared")
    result, error, timed_out = coal.wait(second["slot"], timeout=2.0)
    assert timed_out is False and result == "shared"


# ------------------------------------------------------------ dedup / depth / prompt cache
def test_deduper_reuses_verified_only_within_mission():
    d = ce.MissionDeduper()
    assert d.put("m1", "mod", "Do X", "done", verified=False) is False
    assert d.get("m1", "mod", "Do X")["hit"] is False
    assert d.put("m1", "mod", "Do X", "done", verified=True) is True
    assert d.get("m1", "mod", "Do X")["hit"] is True
    assert d.get("m2", "mod", "Do X")["hit"] is False  # never across missions


def test_adaptive_depth_light_vs_full():
    light = ce.classify_task("Summarize this short note")
    assert light["depth"] == "light"
    full = ce.classify_task("Delete the old payment records", has_write_effect=True)
    assert full["depth"] == "full" and full["reasons"]
    steps = [{"kind": "research"}, {"kind": "critique"}, {"kind": "synthesis"}]
    assert [s["kind"] for s in ce.adapt_plan_steps(steps, "light")] == ["research", "synthesis"]
    assert len(ce.adapt_plan_steps(steps, "full")) == 3


def test_prompt_cache_pass_through_only_when_supported():
    entry = {"provider": "prov", "model_id": "mod"}
    assert ce.prompt_cache_options(entry, cacheable_prefix=True)["enabled"] is False
    entry2 = {**entry, "prompt_cache": "supported", "price_cached": 0.5,
              "price_in": 2.0, "price_status": "known"}
    opts = ce.prompt_cache_options(entry2, cacheable_prefix=True)
    assert opts["enabled"] is True
    assert opts["cached_price_per_1m"] == 0.5
    entry3 = {**entry, "prompt_cache": "supported"}  # no cached price published
    assert ce.prompt_cache_options(entry3, cacheable_prefix=True)["cached_price_status"] == "unknown"


def test_provider_usage_cached_tokens_spellings():
    from app.gateway_client import _provider_usage
    assert _provider_usage({"prompt_tokens": 100, "completion_tokens": 10,
                            "prompt_tokens_details": {"cached_tokens": 40}},
                           {"model": "m"}, "m")["cached_tokens"] == 40
    assert _provider_usage({"prompt_tokens": 100, "completion_tokens": 10,
                            "cached_tokens": 25}, {"model": "m"}, "m")["cached_tokens"] == 25


# ------------------------------------------------------------ attribution / success / savings
def test_cost_per_success_penalizes_failed_missions():
    rows = [_row(mission_id="good", cost_usd=0.10),
            _row(mission_id="bad", cost_usd=0.01)]
    good = ce.cost_per_success(rows, {"good": True, "bad": False})
    assert good["cost_per_success_usd"] == pytest.approx(0.10)
    # A cheaper run that fails more: only the cheap success counts, so
    # cost-per-success FALLS only if successes stay cheap — but with
    # zero successes it is undefined, never flattered to zero.
    rows2 = [_row(mission_id="cheap", cost_usd=0.001, success=False)]
    bad = ce.cost_per_success(rows2, {"cheap": False})
    assert bad["cost_per_success_usd"] is None
    assert bad["cost_of_other_missions_usd"] == pytest.approx(0.001)


def test_cost_optimization_regression_token_cut_causing_rework_is_worse():
    """Governing regression test (§2): halving tokens but doubling
    failures must NOT read as an improvement on the headline metric."""
    before_rows = [_row(mission_id=f"ok{i}", cost_usd=0.10) for i in range(4)]
    before = ce.cost_per_success(before_rows, {f"ok{i}": True for i in range(4)})
    after_rows = ([_row(mission_id="ok0", cost_usd=0.05)]
                  + [_row(mission_id=f"fail{i}", cost_usd=0.05, success=False)
                     for i in range(3)])
    after = ce.cost_per_success(
        after_rows, {"ok0": True, "fail0": False, "fail1": False, "fail2": False})
    assert before["successful_missions"] == 4
    assert after["successful_missions"] == 1
    assert after["cost_of_other_missions_usd"] > 0
    # Total spend fell, but successes collapsed: the honest read is worse.
    assert after["successful_missions"] < before["successful_missions"]


def test_savings_only_against_defined_baseline():
    bad = ce.savings_vs_baseline(0.5, None)
    assert bad["valid"] is False and bad["savings_usd"] is None
    bad2 = ce.savings_vs_baseline(0.5, {"source": "x", "period": "week",
                                        "definition": "same tasks, last week"})
    assert bad2["valid"] is False  # missing cost_usd
    good = ce.savings_vs_baseline(0.5, {"source": "last week ledger",
                                        "period": "2026-10-13 to 2026-10-19",
                                        "definition": "same ten tasks, no cache",
                                        "cost_usd": 2.0})
    assert good["valid"] is True and good["savings_usd"] == pytest.approx(1.5)


def test_cache_savings_honest_split():
    rep = ce.cache_savings_report(tokens_avoided=5000, calls_avoided=3,
                                  est_spend_avoided_usd=0.05,
                                  billed_spend_avoided_usd=None,
                                  overhead_cost_usd=0.01)
    assert rep["tokens_avoided"] == 5000 and rep["calls_avoided"] == 3
    assert rep["net_reduction_usd"] == pytest.approx(0.04)
    assert "estimated" in rep["net_basis"]
    rep2 = ce.cache_savings_report(tokens_avoided=5000, calls_avoided=3,
                                   est_spend_avoided_usd=0.05,
                                   billed_spend_avoided_usd=0.04,
                                   overhead_cost_usd=0.01)
    assert rep2["net_reduction_usd"] == pytest.approx(0.03)
    assert rep2["net_basis"] == "actual billed spend"


def test_anomaly_detection_needs_history_and_flags_spike():
    flat = ce.detect_cost_anomaly([0.01, 0.02], 5.0)
    assert flat["flagged"] is False  # not enough history to judge
    spike = ce.detect_cost_anomaly([0.10] * 10, 1.00)
    assert spike["flagged"] is True
    normal = ce.detect_cost_anomaly([0.10] * 10, 0.11)
    assert normal["flagged"] is False


def test_output_budget_marks_truncation():
    out = ce.enforce_output_budget("word " * 1000, max_tokens=10)
    assert out["truncated"] is True
    assert "cut short" in out["text"]
    ok = ce.enforce_output_budget("short", max_tokens=100)
    assert ok["truncated"] is False


def test_local_model_note_is_plain_words():
    note = ce.local_model_note()
    assert "no provider fee" in note
    assert "electricity" in note
    assert "{" not in note  # no raw JSON in user-facing text


def test_summarize_ledger_retry_fallback_partial_separate():
    rows = [_row(cost_usd=0.01, attempt=2, retry_of="r1"),
            _row(cost_usd=0.02, fallback=True),
            _row(cost_usd=0.03, partial=True, success=False)]
    summ = ce.summarize_ledger(rows)
    assert summ["retry_cost_usd"] == pytest.approx(0.01)
    assert summ["fallback_cost_usd"] == pytest.approx(0.02)
    assert summ["failed_cost_usd"] == pytest.approx(0.03)
    assert summ["partial_calls"] == 1
    assert summ["total_known_cost_usd"] == pytest.approx(0.06)


def test_current_vs_projected_shown_together():
    rows = [_row(cost_usd=0.10)]
    est = ce.estimate_plan([{"kind": "research", "model": "mod",
                             "est_input_tokens": 1000, "est_output_tokens": 100}],
                           {"mod": ENTRY})
    summ = ce.summarize_ledger(rows, estimate=est)
    assert summ["current_cost_usd"] == pytest.approx(0.10)
    assert summ["projected_total_cost_usd"] > 0.10
