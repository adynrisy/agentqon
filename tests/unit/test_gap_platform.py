"""W7 PLATFORM & UI — original gaps + expansions A, C, D, E (part), F hooks.

Run with --noconftest (these tests need no server fixtures and no
live model; every model interaction is a stub callable).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import platform_api as papi
from app import platform_backup as pbackup
from app import platform_benchmarks as pbench
from app import platform_config as pconfig
from app import platform_history as phist
from app import platform_improve as pimp
from app import platform_multimodal as pmm
from app import platform_plugins as pplug
from app import platform_templates as ptemp
from app import platform_usage as pusage
from app import platform_workers as pwork


# ---------------------------------------------------------------- W7.1 benchmarks
def _stub_runner(task):
    # A stub that answers every brief with its expected points covered.
    text = " ".join(task.expected_points) + " and more detail here"
    return text, 0.002, 0.5


def test_benchmark_tasks_cover_many_kinds():
    assert len(pbench.BENCHMARK_TASKS) >= 8
    assert len(pbench.TASK_KINDS) >= 5


def test_benchmark_experiment_records_everything_for_reproduction():
    summary = pbench.run_experiment(
        "exp-1", model="model-a", prompt_version="p1",
        skill_versions={"skill-x": "1.0"}, configuration={"budget_cap_usd": 5},
        run_fn=_stub_runner)
    assert summary["runs"] == len(pbench.BENCHMARK_TASKS)
    assert summary["success_rate"] == 1.0
    assert summary["total_cost_usd"] > 0
    assert summary["reproduction"]["model"] == "model-a"
    assert summary["reproduction"]["prompt_version"] == "p1"
    assert summary["reproduction"]["skill_versions"] == {"skill-x": "1.0"}
    assert summary["reproduction"]["configuration"] == {"budget_cap_usd": 5}
    assert summary["representative"] is True


def test_benchmark_refuses_to_run_without_a_runner():
    try:
        pbench.run_experiment("exp-x", model="m", prompt_version="p")
    except ValueError as exc:
        assert "never invents" in str(exc)
    else:
        raise AssertionError("benchmark without a runner must be refused")


def test_benchmark_comparison_shows_cost_and_quality_together():
    a = pbench.run_experiment("a", model="m1", prompt_version="p",
                              run_fn=_stub_runner)
    b = pbench.run_experiment("b", model="m2", prompt_version="p",
                              run_fn=lambda t: ("nothing relevant", 0.001, 0.2))
    compared = pbench.compare_experiments([a, b])
    assert compared["comparable"] is True
    assert compared["best_by_success_then_cost"] == "a"


# ---------------------------------------------------------------- W7.2 + A config
def _store(tmp=None):
    path = os.path.join(tempfile.mkdtemp(), "config.json")
    return pconfig.ConfigStore(path)


def test_config_validation_names_field_and_range():
    errors = pconfig.validate_config({"research_max_fetches": 99})
    assert errors and "research_max_fetches" in errors[0]
    assert "0 and 50" in errors[0]


def test_config_versioning_snapshot_and_explicit_recorded_rollback():
    store = _store()
    store.set({"research_max_fetches": 10}, note="raise fetch limit")
    assert store.current_version == 2
    snap = store.snapshot_for_mission("mission-1")
    assert snap["config_version"] == 2
    assert snap["values"]["research_max_fetches"] == 10
    store.set({"research_max_fetches": 3}, note="lower it")
    rolled = store.rollback(2, reason="the lower limit hurt results")
    assert rolled["restored_version"] == 2
    assert store.current["research_max_fetches"] == 10
    assert store.snapshot_of("mission-1")["values"]["research_max_fetches"] == 10
    assert any(a["action"] == "rollback" and a["reason"] for a in store.audit)


def test_config_rollback_needs_reason_and_known_version():
    store = _store()
    for kwargs, fragment in (
            ({"to_version": 99, "reason": "x"}, "no configuration version"),
            ({"to_version": 1, "reason": ""}, "needs a reason")):
        try:
            store.rollback(**kwargs)
        except ValueError as exc:
            assert fragment in str(exc)
        else:
            raise AssertionError("rollback must be refused")


def test_config_precedence_admin_limits_cannot_be_loosened():
    resolved = pconfig.resolve(admin={"budget_cap_usd": 100.0},
                               project={"budget_cap_usd": 50.0},
                               user={"budget_cap_usd": 10.0})
    assert resolved["budget_cap_usd"] == 100.0  # admin wins
    try:
        pconfig.resolve(admin={"budget_cap_usd": 100.0},
                        project={"budget_cap_usd": 500.0})
    except ValueError as exc:
        assert "admin hard limit" in str(exc)
    else:
        raise AssertionError("loosening an admin limit must be refused")


def test_config_preview_and_reset_scope():
    store = _store()
    preview = pconfig.preview_change(store.current, {"research_max_fetches": 9})
    assert preview["changed"]["research_max_fetches"]["to"] == 9
    assert preview["affects_current_missions"] is False
    sentinel = os.path.join(tempfile.mkdtemp(), "project-file.txt")
    open(sentinel, "w").write("project data")
    reset = store.reset_to_defaults()
    assert "only the configuration file changes" in reset["scope"]
    assert open(sentinel).read() == "project data"  # reset never deletes it
    assert reset["values"] == pconfig.DEFAULTS


def test_config_export_redacts_secrets_and_import_refuses_markers():
    store = _store()
    store.set({"llm_api_key": "real-secret-value"})
    exported = store.export()
    assert exported["values"]["llm_api_key"].startswith("(hidden")
    try:
        store.import_config(exported)
    except ValueError as exc:
        assert "hidden secret marker" in str(exc)
    else:
        raise AssertionError("importing a redacted marker must be refused")
    good = {"format": "agentqon-config", "values": {"research_max_fetches": 7}}
    assert store.import_config(good)["values"]["research_max_fetches"] == 7


def test_config_change_preview_affects_surfaces():
    preview = pconfig.preview_change(pconfig.DEFAULTS, {"rate_run_per_min": 5})
    assert preview["affects"] == ["sign-in and request limits"]


# ---------------------------------------------------------------- W7.3 usage
def test_usage_breakdown_per_agent_provider_and_cache_baseline():
    records = [
        {"agent": "planner", "provider": "prov-a", "cost_usd": 0.10,
         "cached_tokens": 1000, "price_in_per_million": 2.0, "verified": True},
        {"agent": "writer", "provider": "prov-b", "cost_usd": 0.05,
         "verified": False},
    ]
    out = pusage.breakdown(records)
    assert out["per_agent_usd"] == {"planner": 0.10, "writer": 0.05}
    assert out["per_provider_usd"] == {"prov-a": 0.10, "prov-b": 0.05}
    assert out["verified_usd"] == 0.10 and out["estimated_usd"] == 0.05
    assert out["cache"]["savings_kind"] == "estimated"
    assert "no cache at all" in out["cache"]["baseline"]
    assert out["cache"]["baseline_cost_usd"] == 0.002


def test_money_format_consistent():
    assert pusage.format_money(0.0025) == "$0.0025"
    assert pusage.format_money(2.5) == "$2.50"


# ---------------------------------------------------------------- W7.4 history
def test_model_history_plain_words_and_too_few_to_judge():
    history = phist.ModelHistory()
    history.record(model="m1", success=True, latency_seconds=1.0,
                   cost_usd=0.01, source="self-test")
    summary = history.summary("m1")
    assert summary["success_rate"] == 1.0
    assert summary["enough_to_judge"] is False
    assert "too few to judge" in summary["plain_words"]
    assert history.summary("never-seen")["runs"] == 0


# ---------------------------------------------------------------- W7.5 templates
def test_template_saves_pattern_not_outputs_and_instantiates_fresh():
    store = ptemp.TemplateStore()
    try:
        store.save("t1", {"name": "x", "plan_shape": [], "agent_roles": ["a"],
                          "verification_steps": ["check"]})
    except ptemp.TemplateError as exc:
        assert "at least one step" in str(exc)
    else:
        raise AssertionError("step-less template must be refused")
    try:
        store.save("t2", {"name": "x", "plan_shape": ["do it"],
                          "agent_roles": ["a"], "verification_steps": ["check"],
                          "outputs": "old answer"})
    except ptemp.TemplateError as exc:
        assert "must not store" in str(exc)
    else:
        raise AssertionError("template storing outputs must be refused")
    store.save("t3", {"name": "Brief", "plan_shape": ["draft", "review"],
                      "agent_roles": ["writer"],
                      "verification_steps": ["check facts"]},
               source_mission_id="mission-9")
    draft = store.instantiate("t3", title="New brief")
    assert draft["plan_shape"] == ["draft", "review"]
    assert draft["prior_outputs"] is None


# ---------------------------------------------------------------- W7.6 backup
def test_backup_export_restore_and_checksum_refusal():
    bundle = pbackup.export_state(
        tenant_id="tenant-1",
        sections={"config": {"a": 1}, "templates": []})
    assert bundle["format_version"] == 1
    preview = pbackup.restore_preview(bundle)
    assert preview["valid"] is True
    target: dict = {}
    report = pbackup.restore_state(bundle, target)
    assert report["restored"] == ["config", "templates"]
    assert "secrets were never part" in report["note"]
    tampered = dict(bundle, sections={"config": {"a": 2}})
    assert pbackup.validate_bundle(tampered)  # checksum mismatch flagged
    try:
        pbackup.restore_state(tampered, {})
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("tampered backup must be refused")


# ---------------------------------------------------------------- W7.7 plugins
def test_plugin_install_refuses_core_denied_and_enforces_declared():
    registry = pplug.PluginRegistry()
    try:
        registry.install({"name": "bad", "version": "1.0.0",
                          "permissions": ["read_vault"]}, tenant_id="t1")
    except pplug.PluginError as exc:
        assert "never be granted" in str(exc)
    else:
        raise AssertionError("core-denied permission must refuse install")
    registry.install({"name": "good", "version": "1.2.3",
                      "permissions": ["read_missions"], "tools": ["search"]},
                     tenant_id="t1")
    assert registry.check(name="good", tenant_id="t1",
                          permission="read_missions")["allowed"] is True
    denied = registry.check(name="good", tenant_id="t1",
                            permission="create_missions")
    assert denied["allowed"] is False and "did not declare" in denied["reason"]
    other = registry.check(name="good", tenant_id="t2",
                           permission="read_missions")
    assert other["allowed"] is False and "another tenant" in other["reason"]


# ---------------------------------------------------------------- W7.8 workers
class _FakeStore:
    def __init__(self):
        self.claimed = []

    def claim_job(self, owner):
        self.claimed.append(owner)
        return {"id": "job-1", "tenant_id": "t1", "mission_id": "m1"}

    def usage_summary(self, tenant_id, mission_id):
        return {"by_category": {}, "total_usd": 0.5}


def test_worker_registry_stale_and_claim_uses_existing_queue():
    registry = pwork.WorkerRegistry()
    registry.register("worker-a")
    assert len(registry.live_workers()) == 1
    assert registry.stale_workers(now=registry._workers["worker-a"]["last_heartbeat"] + 1000)
    store = _FakeStore()
    assert pwork.claim_for_worker(store, "worker-a")["id"] == "job-1"
    assert store.claimed == ["worker-a"]


def test_worker_guards_state_and_budget():
    assert pwork.check_mission_runnable({"status": "cancelled"})["allowed"] is False
    assert pwork.check_mission_runnable({"status": "running"})["allowed"] is True
    store = _FakeStore()
    assert pwork.check_budget(store, "t1", "m1", 0.10)["allowed"] is False
    assert pwork.check_budget(store, "t1", "m1", 5.0)["allowed"] is True


# ---------------------------------------------------------------- W7.9 multimodal
def test_multimodal_gate_only_probed_support_and_never_fakes():
    gate = pmm.CapabilityGate()
    gate.record_probe("model-a", {"image": (True, "probed")})
    gate.record_probe("model-b", {"image": (True, "declared")})
    assert gate.gate(model="model-a", kind="image", payload="IMG")["allowed"] is True
    refused = gate.gate(model="model-b", kind="image", payload="IMG")
    assert refused["allowed"] is False and "not been verified by probe" in refused["reason"]
    assert "payload" not in refused
    unprobed = gate.gate(model="model-c", kind="audio", payload="AUD")
    assert unprobed["allowed"] is False and "not been probed" in unprobed["reason"]


# ---------------------------------------------------------------- C API
def test_rate_limiter_plain_words_breach():
    limiter = papi.RateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("k")["allowed"] is True
    assert limiter.allow("k")["allowed"] is True
    breach = limiter.allow("k")
    assert breach["allowed"] is False
    assert "Too many requests" in breach["reason"]
    assert breach["retry_after_seconds"] > 0


def test_webhook_verifier_signature_and_dedup():
    verifier = papi.WebhookVerifier("secret-1")
    body = b'{"event": 1}'
    sig = papi.sign_webhook("secret-1", body)
    assert verifier.verify(body=body, signature=sig, event_id="e1")["accepted"] is True
    assert verifier.verify(body=body, signature=sig, event_id="e1")["accepted"] is False
    bad = verifier.verify(body=body, signature="0" * 64, event_id="e2")
    assert bad["accepted"] is False


def test_deprecation_names_replacement_and_rotation_grace():
    out = papi.deprecated_response({"ok": True}, replacement="/v1/new",
                                   endpoint="/v1/old")
    assert out["deprecated"] is True and out["replacement"] == "/v1/new"
    store = {"KEY": "old-value"}
    rotation = papi.SecretRotation(store, grace_seconds=3600)
    rotation.rotate("KEY", "new-value")
    assert rotation.valid("KEY", "new-value") is True
    assert rotation.valid("KEY", "old-value") is True  # grace period
    assert rotation.valid("KEY", "old-value", now=9999999999) is False


def test_graceful_degradation_optional_only():
    degraded = papi.degrade("live_research", "provider timed out")
    assert degraded["continue_degraded"] is True
    assert degraded["mission_failed"] is False
    try:
        papi.degrade("model_call", "provider down")
    except ValueError as exc:
        assert "not an optional integration" in str(exc)
    else:
        raise AssertionError("core failures must not degrade")


# ---------------------------------------------------------------- D improve
def test_failure_links_config_and_promotion_needs_representative_evidence():
    failure = pimp.link_failure(mission_id="m1", error="boom",
                                config_version=7, model="mx",
                                skill_versions={"s": "2"})
    assert failure["config_version"] == 7
    anecdote = pimp.promote_model(
        model="mx", category="brief",
        verified_outcomes=[{"verified": True, "task_id": "t1", "success": True}])
    assert anecdote["promoted"] is False and "anecdote" in anecdote["reason"]
    evidence = [{"verified": True, "task_id": f"t{i}", "success": True}
                for i in range(5)]
    promoted = pimp.promote_model(model="mx", category="brief",
                                  verified_outcomes=evidence)
    assert promoted["promoted"] is True


def test_skill_regression_drift_and_cost_quality_together():
    before = [{"success": True}] * 8 + [{"success": False}] * 2
    after = [{"success": True}] * 2 + [{"success": False}] * 8
    assert pimp.detect_skill_regression("s", before=before, after=after)["flagged"] is True
    assert pimp.detect_drift(baseline=before, recent=after)["flagged"] is True
    assert pimp.require_cost_and_quality({"total_cost_usd": 1.0})["ok"] is False
    assert pimp.require_cost_and_quality(
        {"total_cost_usd": 1.0, "mean_quality": 0.8})["ok"] is True
    compared = pimp.compare_routing_policies({
        "p1": [{"task_id": "t1", "success": True, "cost_usd": 0.1, "quality": 0.9}],
        "p2": [{"task_id": "t1", "success": False, "cost_usd": 0.1, "quality": 0.2}]})
    assert compared["comparable"] is True
    evaluated = pimp.evaluate_change(
        proposed_config={"research_max_fetches": 3}, reference_tasks=["t1"],
        scorer=lambda t, c: {"success": True, "cost_usd": 0.1, "quality": 0.8})
    assert evaluated["applied"] is False  # evaluation never applies
