"""Gap-seventhdoc groups F (operations/governance) and G (operability)."""
import pytest

from app.eval_ops import (
    add_policy_exception, amend_contract, blast_radius_preview,
    build_mission_contract, capability_degradation_map,
    classification_decision, check_sla, dependency_map, disaster_drill,
    exception_active, explain_decision, offline_capability_map,
    package_compliance_evidence, prioritize_workload,
    recovery_objectives, tenant_isolation_probe, transfer_ownership)


def test_f28_drill_verifies_restore_not_backup_existence():
    assert disaster_drill("db loss", lambda: True)["recovered"] is True
    out = disaster_drill("db loss", lambda: False)
    assert out["recovered"] is False
    assert "backup exists but recovery did not work" in out["detail"]


def test_f29_recovery_objectives_measured_per_scope():
    out = recovery_objectives(
        {"missions": {"rto_minutes": 60, "rpo_minutes": 15}},
        {"missions": {"recovery_minutes": 90, "data_loss_minutes": 5}})
    assert out["scopes"]["missions"]["rto_met"] is False
    assert out["scopes"]["missions"]["rpo_met"] is True


def test_f30_offline_mode_keeps_local_work_names_blocked():
    out = offline_capability_map(
        [{"name": "local chat", "needs_network": False},
         {"name": "live web research", "needs_network": True}],
        provider_available=False)
    assert out["blocked"] == ["live web research"]
    assert out["capabilities"][0]["status"] == "works"


def test_f31_tenant_probe_flags_cross_tenant_access():
    res = [{"tenant_id": "t2", "resource_id": "f1", "kind": "file"},
           {"tenant_id": "t1", "resource_id": "f2", "kind": "memory"}]
    violations = tenant_isolation_probe(
        "t1", "t2", res, lambda tenant, r: True)
    assert len(violations) == 1 and violations[0]["resource"] == "f1"
    assert tenant_isolation_probe("t1", "t2", res,
                                  lambda tenant, r: False) == []


def test_f32_exception_register_expiry_ends_it():
    reg = []
    rec = add_policy_exception(reg, "budget", "pilot", "owner", "mission-1",
                               "2026-01-01T00:00:00+00:00")
    assert exception_active(rec, "2025-06-01T00:00:00+00:00") is True
    assert exception_active(rec, "2026-06-01T00:00:00+00:00") is False


def test_f33_package_never_labels_itself_compliant():
    pkg = package_compliance_evidence(["a1"], {"v": 1}, ["log"], ["ap1"],
                                      ["t1"], {"days": 90})
    assert pkg["label"] == "evidence package"
    assert pkg["compliant"] is None


def test_f34_dependency_map_names_what_breaks():
    out = dependency_map({"chat": ["provider", "database"],
                          "local tools": ["local_model"]}, "provider")
    assert out["capabilities"]["chat"] == "impaired"
    assert out["capabilities"]["local tools"] == "works"


def test_g35_contract_and_explicit_amendment_only():
    c = build_mission_contract(["Brief"], "2026-11-01T00:00:00+00:00",
                               0.8, 50.0, ["web.read"], ["has_claims"])
    assert c["deadline"] == "2026-11-01T00:00:00+00:00"
    assert c["agreed_budget_ceiling_usd"] == 50.0
    silent = amend_contract(c, {"quality_threshold": 0.5}, explicit=False)
    assert silent["amended"] is False
    assert silent["contract"]["quality_threshold"] == 0.8
    amended = amend_contract(c, {"quality_threshold": 0.9}, explicit=True,
                             amended_by="owner")
    assert amended["amended"] is True
    assert amended["contract"]["version"] == 2
    assert len(amended["contract"]["amendments"]) == 1
    with pytest.raises(ValueError):
        build_mission_contract(["Brief"], None, 0.8, 5000.0,
                               ["web.read"], ["has_claims"])


def test_g36_sla_misses_reported_separate_from_quality():
    out = check_sla("quick", 30, 30)
    assert out["missed"] == ["response_time"]
    assert "quality" in out["quality_note"]
    assert check_sla("quick", 5, 10)["missed"] == []
    assert check_sla("quick", 5, 10, completion_minutes=9999)["missed"] == \
        ["completion_target"]


def test_g37_prioritizer_ignores_arrival_order_and_sinks_blocked():
    tasks = [{"id": "late", "urgency": 1, "impact": 1, "resource_demand": 0},
             {"id": "urgent", "urgency": 9, "impact": 9, "resource_demand": 1,
              "deadline_days": 1},
             {"id": "blocked-urgent", "urgency": 10, "impact": 10,
              "dependencies_unsatisfied": True}]
    ranked = prioritize_workload(tasks)
    assert [t["id"] for t in ranked] == ["urgent", "late", "blocked-urgent"]


def test_g38_blast_radius_is_transitive():
    graph = {"dependents": {"svc-a": ["svc-b", "file-1"],
                            "svc-b": ["mission-9"]},
             "kinds": {"svc-b": "service", "file-1": "file",
                       "mission-9": "mission"}}
    out = blast_radius_preview({"action_id": "x", "target": "svc-a"}, graph)
    assert out["affected_count"] == 3
    assert out["affected"]["mission"] == ["mission-9"]


def test_g39_explanation_names_rule_evidence_reason():
    text = explain_decision("block send", "confidential blocks external send",
                            {"classification": "confidential"},
                            "send was blocked")
    assert "confidential blocks external send" in text
    assert "classification is confidential" in text
    assert "send was blocked" in text


def test_g40_degradation_map_from_health_stamps():
    out = capability_degradation_map(
        {"chat": ["model-a"], "research": ["model-a", "model-b"]},
        {"model-a": "working", "model-b": "not_working"})
    assert out["features"] == {"chat": "works", "research": "impaired"}
    out2 = capability_degradation_map({"chat": ["model-a"]},
                                      {"model-a": "not_working"})
    assert out2["features"]["chat"] == "blocked"


def test_g41_classification_lifecycle_policies():
    assert classification_decision("restricted", "sharing")["policy"] == "never"
    assert classification_decision("confidential", "logging")["policy"] == "redacted"
    assert classification_decision("public", "sharing")["policy"] == "any"
    assert classification_decision("bogus", "sharing")["allowed"] is False


def test_g42_transfer_requires_complete_handoff():
    out = transfer_ownership({"current_state": "running"}, "new-owner")
    assert out["transferred"] is False
    full = {"current_state": "running", "permissions": ["web.read"],
            "unresolved_decisions": ["pick model"],
            "recovery_instructions": "restart worker"}
    out = transfer_ownership(full, "new-owner")
    assert out["transferred"] is True and out["new_owner"] == "new-owner"
