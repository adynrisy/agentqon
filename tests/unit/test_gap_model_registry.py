"""W6 gaps 2 + 3 and expansion A — registry, profiles, warnings.

Badges, prices/estimates, latency, task profiles, context warnings,
unsupported warnings, switch notices, and the refresh helper (with a
fake gateway, no network).
"""
import asyncio

import pytest

from app import model_registry as reg
from app.catalog import normalize_model, public_catalog_entry, refresh_models_for_connection
from app.gateway_client import GatewayClient
from app.store import Store


# A1 badges: only from probes, never from advertised claims
def test_a1_badges_only_from_probe_stamps():
    badges = reg.badges_for({"tool_calling": "pass", "vision": "fail"})
    assert badges == {"tool_calling": "pass", "vision": "fail",
                      "structured_output": "not_tested"}
    # An advertised capability in the catalog is NOT a badge.
    entry = normalize_model({"id": "m", "capabilities": {}},
                            kind="direct_provider")
    assert entry["capabilities"]["tool_calling"] == "unknown"


# M2 prices: estimates marked, unknown never $0
def test_m2_price_display_marks_estimates():
    known = public_catalog_entry(normalize_model(
        {"id": "m", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        kind="direct_provider"))
    assert known["price_display"]["is_estimate"] is False
    est = public_catalog_entry(normalize_model(
        {"id": "m", "pricing": {"prompt": "0.000001"}, "price_estimated": True},
        kind="direct_provider"))
    assert est["price_status"] == "estimated"
    assert est["price_display"]["is_estimate"] is True
    assert "estimate" in est["price_display"]["text"]
    unknown = public_catalog_entry(normalize_model({"id": "m"}, kind="direct_provider"))
    assert unknown["price_display"]["price_in"] is None
    assert "$0" not in unknown["price_display"]["text"]


# M2 latency: refreshable average
def test_m2_latency_average_moves_with_new_observations():
    first = reg.record_latency([], 100)
    assert first["avg_latency_ms"] == 100 and first["observations"] == 1
    second = reg.record_latency([100], 300)
    assert second["avg_latency_ms"] == 200 and second["observations"] == 2


# M3/A6 task-specific profiles, not one universal ranking
def test_m3_profiles_differ_by_task_class():
    stamps = {"structured_output": {"status": "pass"},
              "task_performance": {"status": "pass"},
              "tool_calling": {"status": "fail"},
              "context_size": {"status": "pass"}}
    extraction = reg.profile_score(stamps, [], "extraction")
    debugging = reg.profile_score(stamps, [], "debugging")
    assert extraction["score"] != debugging["score"]
    assert extraction["task_class"] == "extraction"
    # History blends in: a perfect history lifts a weak stamp score.
    weak = {"structured_output": {"status": "fail"},
            "task_performance": {"status": "fail"},
            "context_size": {"status": "fail"}}
    no_hist = reg.profile_score(weak, [], "extraction")
    with_hist = reg.profile_score(weak, [
        {"capability": "structured_output", "status": "pass", "task_class": "extraction"},
        {"capability": "structured_output", "status": "pass", "task_class": "extraction"},
    ], "extraction")
    assert with_hist["score"] > no_hist["score"]
    assert with_hist["history_pass_rate"] == 1.0
    # History for a DIFFERENT class does not leak in.
    other = reg.profile_score(weak, [
        {"capability": "structured_output", "status": "pass", "task_class": "debugging"},
    ], "extraction")
    assert other["history_observations"] == 0


def test_m3_profile_ranking_is_per_class(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"), str(tmp_path / "art"))
    models = [{"connection_id": "c1", "model_id": "cheap"},
              {"connection_id": "c1", "model_id": "smart"}]
    badges = {("c1", "cheap"): {"structured_output": "fail", "task_performance": "pass"},
              ("c1", "smart"): {"structured_output": "pass", "task_performance": "pass"}}
    ranked = reg.profile_scores_for_models(models, badges, {}, "extraction")
    assert ranked[0]["model_id"] == "smart"
    assert ranked[0]["task_class"] == "extraction"


# A2 context warning before failure
def test_a2_context_warning_thresholds():
    assert reg.context_warning(100, 1000) is None
    warn = reg.context_warning(850, 1000)
    assert warn and "85%" in warn and "context limit" in warn
    over = reg.context_warning(1200, 1000)
    assert over and "larger than" in over
    assert reg.context_warning(900, None) is None


# A3 unsupported-feature warning
def test_a3_unsupported_warning_plain_words():
    warn = reg.unsupported_warning("vision", "fail", "m")
    assert warn and "images" in warn and "cannot" in warn
    assert reg.unsupported_warning("vision", "pass", "m") is None
    assert reg.unsupported_warning("vision", None, "m") is None
    untested = reg.unsupported_warning("streaming", "not_tested", "m")
    assert untested and "not been tested" in untested


# A4 model-switch notice wording shape
def test_a4_model_switch_notice_shape():
    notice = reg.model_switch_notice("primary-a", "fallback-b")
    assert notice == ("Primary model primary-a unavailable. "
                      "Switched to the configured fallback model fallback-b. "
                      "Verification requirements remain enabled.")
    off = reg.model_switch_notice("primary-a", "fallback-b",
                                  verification_enabled=False)
    assert "not enabled" in off


# A5 refresh removes stale entries (fake gateway lists only one model)
def test_a5_refresh_removes_stale_and_unallowlisted(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"), str(tmp_path / "art"))
    tenant = "t1"
    conn = {"id": "c1", "kind": "direct_provider", "base_url": "http://x.test/v1",
            "allowed_models": ["kept-model"], "blocked_models": []}
    for mid in ("kept-model", "gone-model"):
        store.upsert_catalog_model(tenant, "c1", {
            "model_id": mid, "provider": "", "display_name": mid,
            "capabilities": {}, "price_in": None, "price_out": None,
            "price_source": None, "price_status": "unknown", "health": "ok"})

    class _FakeGw:
        async def list_models(self):
            return [{"id": "kept-model"}, {"id": "new-model"}]

    result = asyncio.run(refresh_models_for_connection(
        store, tenant, conn, "secret", client=_FakeGw()))
    assert result["status"] == "completed"
    live = {e["model_id"] for e in store.list_catalog(tenant, "c1")
            if not e["deprecated"]}
    assert live == {"kept-model"}
    assert result["removed_stale"] >= 1


# Store capability history: latest badges + history order
def test_a6_capability_history_roundtrip(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"), str(tmp_path / "art"))
    store.record_capability_result("t", "c1", "m", "vision", "fail")
    store.record_capability_result("t", "c1", "m", "vision", "pass")
    badges = store.latest_capability_badges("t")
    assert badges[("c1", "m")]["vision"] == "pass"
    hist = store.capability_history("t", "m")
    assert [r["status"] for r in hist] == ["fail", "pass"]
