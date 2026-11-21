"""W14 tenth audit document: one named test per capability (42).

Pure unit tests over the new stdlib modules; no live providers, no model
calls, no invented telemetry. Clocks are injected where time matters.
"""
import hashlib
import json
import tempfile
import os

from app import tenth_comms as comms
from app import tenth_distributed as dist
from app import tenth_governance as gov
from app import tenth_identity as ident
from app import tenth_index as index
from app import tenth_local as local
from app import tenth_meta as meta
from app import tenth_strategic as strat


# ---------------------------------------------------------------- Group 1
def test_verifier_lab_known_answers():
    lab = meta.VerifierReliabilityLab()
    lab.add_case(meta.KnownCase("good-1", is_defect=False, expected_pass=True))
    lab.add_case(meta.KnownCase("bad-1", is_defect=True, expected_pass=False))
    lab.add_case(meta.KnownCase("bad-2", is_defect=True, expected_pass=False))
    # A sloppy validator that passes everything.
    result = lab.run(lambda case: True)
    assert result.false_passes == 2
    assert result.missed_defects == 2
    assert result.false_failures == 0
    assert "wrongly passed 2" in result.plain_summary()


def test_oracle_independent_checks():
    gen = meta.OracleGenerator()
    calc = gen.calculation(lambda xs: sum(xs), lambda xs: sum(reversed(xs)), [1, 2, 3])
    assert calc.agrees and calc.primary == 6 and calc.independent
    bad = gen.calculation(lambda xs: 99, lambda xs: sum(xs), [1, 2, 3])
    assert not bad.agrees
    parsed = gen.file_check('{"a": 1}', json.loads)
    assert parsed.agrees and parsed.oracle == {"a": 1}
    broken = gen.file_check("{nope", json.loads)
    assert not broken.agrees
    app_check = gen.application_check(lambda: (True, "integration ran"))
    assert app_check.agrees


def test_drift_monitor_flags_regression():
    mon = meta.ValidatorDriftMonitor(tolerance=0.10)
    good = meta.LabResult(10, 0, 0, 0, 0, 0.0, 0.0, [])
    bad = meta.LabResult(10, 5, 0, 5, 0, 0.5, 0.0, [])
    mon.record("v1", good)
    assert mon.check()["drift"] is False  # single run: not enough evidence
    mon.record("v2", bad)
    assert mon.check()["drift"] is True


def test_diversity_score_axes():
    checks = [
        meta.CheckDescription("a", "model-x", "prompt-1", "evidence-1", "path-1"),
        meta.CheckDescription("b", "model-x", "prompt-1", "evidence-1", "path-1"),
    ]
    assert meta.diversity_score(checks)["score"] == 0.0  # role separation only
    checks[1] = meta.CheckDescription("b", "model-y", "prompt-2", "evidence-2", "path-2")
    assert meta.diversity_score(checks)["score"] == 1.0


def test_false_confidence_flags():
    flagged = meta.detect_false_confidence({
        "claimed_reliability": "high", "tests_run": False, "evidence": "",
        "validator_calibrated": False})
    assert flagged["false_confidence"] and len(flagged["flags"]) >= 3
    clean = meta.detect_false_confidence({
        "claimed_reliability": "high", "tests_run": True, "evidence": "run log",
        "validator_calibrated": True, "evidence_strength": "strong"})
    assert not clean["false_confidence"]


# ---------------------------------------------------------------- Group 2
def test_jit_broker_short_lived_scoped():
    now = [1000.0]
    broker = ident.JITCredentialBroker(now_fn=lambda: now[0])
    issued = broker.issue(scope="read-only", operation="fetch", resource="file-a",
                          ttl_seconds=60)
    assert broker.validate(issued["token"], operation="fetch", resource="file-a")["ok"]
    # Single-use: second use fails.
    assert not broker.validate(issued["token"], operation="fetch", resource="file-a")["ok"]
    issued2 = broker.issue(scope="read-only", operation="fetch", resource="file-a",
                           ttl_seconds=60, single_use=False)
    assert not broker.validate(issued2["token"], operation="write",
                               resource="file-a")["ok"]
    now[0] += 61
    assert not broker.validate(issued2["token"], operation="fetch",
                               resource="file-a")["ok"]


def test_delegation_chain_within_authority():
    chain = ident.DelegationChain()
    chain.delegate("owner", "agent-a", ["read"])
    chain.delegate("agent-a", "agent-b", ["read"])
    assert chain.verify(actor="agent-b", action="read")["ok"]
    assert not chain.verify(actor="agent-b", action="write")["ok"]


def test_context_binding_reapproval_on_change():
    binding = ident.ApprovalBinding.build("write", "notes.txt",
                                          {"content": "hi"}, "v1")
    ok = ident.check_binding(binding, action="write", resource="notes.txt",
                             parameters={"content": "hi"}, version="v1")
    assert ok["ok"]
    changed = ident.check_binding(binding, action="write", resource="notes.txt",
                                  parameters={"content": "BYE"}, version="v1")
    assert not changed["ok"] and "parameters" in changed["changed"]
    # Seam: a stored per-file grant must not authorize a changed action.
    assert not ident.check_grant_reuse(
        binding, action="write", resource="other.txt",
        parameters={"content": "hi"}, version="v1")["ok"]


def test_tripwire_broader_access():
    trip = ident.EscalationTripwire()
    trip.set_baseline("agent-a", resources=["file-a"], resource_classes=["file"],
                      methods=["read"])
    assert not trip.check("agent-a", resource="file-a", resource_class="file",
                          method="read")["trip"]
    hit = trip.check("agent-a", resource="admin-panel", resource_class="admin",
                     method="admin")
    assert hit["trip"] and len(hit["reasons"]) == 3
    assert trip.trips


def test_revocation_propagates_to_sessions():
    reg = ident.RevocationRegistry()
    reg.register_use("cred-1", "session-a")
    reg.register_use("cred-1", "worker-b")
    result = reg.revoke("cred-1", policy="terminate")
    assert result["affected_holders"] == ["session-a", "worker-b"]
    assert reg.is_revoked("cred-1")


def test_separation_of_duties():
    single = ident.SeparationOfDuties(identities=["owner"])
    assert single.check(operation="publish", proposer="owner", reviewer=None,
                        sensitive=True)["ok"]  # no faked second person
    assert single.check(operation="publish", proposer="owner", reviewer=None,
                        sensitive=True)["single_user"] is True
    multi = ident.SeparationOfDuties(identities=["alice", "bob"])
    assert not multi.check(operation="publish", proposer="alice", reviewer="alice",
                           sensitive=True)["ok"]
    assert multi.check(operation="publish", proposer="alice", reviewer="bob",
                       sensitive=True)["ok"]


# ---------------------------------------------------------------- Group 3
def test_retry_storm_pacing():
    now = [0.0]
    guard = dist.RetryStormGuard(window_seconds=60, threshold=3,
                                 now_fn=lambda: now[0])
    assert not guard.record_retry()["storm"]
    assert not guard.record_retry()["storm"]
    storm = guard.record_retry()
    assert storm["storm"] and storm["backoff_seconds"] > 0
    assert storm["concurrency_limit"] == 2


def test_bulkhead_isolates_failure():
    bulk = dist.Bulkhead(limit=1, failure_threshold=2)
    assert bulk.acquire("provider-x")["ok"]
    assert not bulk.acquire("provider-x")["ok"]  # at capacity
    bulk.release("provider-x", failed=True)
    assert bulk.acquire("provider-x")["ok"]
    bulk.release("provider-x", failed=True)
    assert bulk.isolated("provider-x")
    assert not bulk.acquire("provider-x")["ok"]
    assert bulk.acquire("provider-y")["ok"]  # unrelated dependency unaffected


def test_load_shedding_preserves_critical():
    jobs = [{"id": "run-1", "kind": "execution"},
            {"id": "cancel-1", "kind": "cancellation"},
            {"id": "report-1", "kind": "optional_analysis"},
            {"id": "bg-1", "kind": "background"}]
    result = dist.shed_load(jobs, load=0.95)
    assert result["shedding"]
    assert result["kept"] == ["run-1", "cancel-1"]
    assert result["deferred"] == ["report-1", "bg-1"]
    assert not dist.shed_load(jobs, load=0.2)["shedding"]


def test_clock_skew_and_ordering():
    mon = dist.ClockSkewMonitor(skew_tolerance_seconds=5.0)
    assert not mon.observe_clock("w1", worker_time=100, reference_time=101)["skewed"]
    assert mon.observe_clock("w1", worker_time=200, reference_time=100)["skewed"]
    assert "w1" in mon.skewed_workers
    assert mon.observe_event("w1", 1)["ordered"]
    assert mon.observe_event("w1", 2)["ordered"]
    assert not mon.observe_event("w1", 2)["ordered"]  # out of order


def test_backpressure_controller():
    ctrl = dist.BackpressureController(capacity=2)
    assert ctrl.push("a")["accepted"] and ctrl.push("b")["accepted"]
    full = ctrl.push("c")
    assert not full["accepted"] and full["signal"] == "slow_down"
    assert ctrl.drain() == ["a", "b"]  # order preserved, nothing dropped silently
    assert ctrl.buffered == 0


def test_work_lease_recovery_fencing():
    now = [0.0]
    mgr = dist.WorkLeaseManager(now_fn=lambda: now[0])
    lease = mgr.acquire("task-1", "worker-a", ttl_seconds=10)
    assert lease["ok"]
    assert not mgr.acquire("task-1", "worker-b")["ok"]  # still owned
    assert mgr.heartbeat("task-1", "worker-a", ttl_seconds=10)["ok"]
    now[0] += 11  # lease lapses with no heartbeat
    assert mgr.recover_abandoned() == ["task-1"]
    lease2 = mgr.acquire("task-1", "worker-b", ttl_seconds=10)
    assert lease2["ok"]
    # The old holder's token can no longer commit.
    assert not mgr.commit("task-1", lease["fencing_token"])["ok"]
    assert mgr.commit("task-1", lease2["fencing_token"])["ok"]
    assert not mgr.commit("task-1", lease2["fencing_token"])["ok"]  # already committed


def test_failover_gate_blocks_incompatible():
    required = {"tool_calling": True, "structured_outputs": True,
                "min_context_size": 8000, "constraints": ["json-only"]}
    weak = {"tool_calling": True, "structured_outputs": False,
            "context_size": 4000, "constraints": []}
    result = dist.failover_gate(weak, required=required)
    assert not result["ok"] and len(result["missing"]) == 3
    strong = {"tool_calling": True, "structured_outputs": True,
              "context_size": 16000, "constraints": ["json-only"]}
    assert dist.failover_gate(strong, required=required)["ok"]


# ---------------------------------------------------------------- Group 4
def test_message_contract_registry():
    reg = comms.MessageContractRegistry()
    reg.register("assignment", 1, required_fields=["task", "agent"])
    reg.register("assignment", 2, required_fields=["task", "agent", "deadline"])
    reg.register_migration("assignment", 1, 2,
                           lambda m: {**m, "deadline": "none set"})
    assert reg.validate({"kind": "assignment", "version": 1, "task": "t",
                         "agent": "a"})["ok"]
    assert not reg.validate({"kind": "assignment", "version": 1,
                             "task": "t"})["ok"]
    migrated = reg.validate({"kind": "assignment", "version": 1, "task": "t",
                             "agent": "a"}, target_version=2)
    assert migrated["ok"] and migrated["migrated"]
    assert migrated["message"]["deadline"] == "none set"
    assert not reg.validate({"kind": "assignment", "version": 9})["ok"]


def test_entity_resolution_aliases():
    vocab = comms.Vocabulary()
    assert vocab.register("req-1", kind="requirement",
                          aliases=["login requirement", "req login"])["ok"]
    resolved = vocab.resolve("login requirement")
    assert resolved["ok"] and resolved["canonical_id"] == "req-1"
    clash = vocab.register("req-2", kind="requirement",
                           aliases=["login requirement"])
    assert not clash["ok"]
    assert not vocab.resolve("something unknown")["ok"]


def test_decision_dependency_reversal():
    tracker = comms.DecisionDependencyTracker()
    tracker.record_decision("d1", summary="Use local storage")
    tracker.link("d1", kind="action", ref="write-plan")
    tracker.link("d1", kind="artifact", ref="plan.md")
    result = tracker.reverse("d1")
    assert result["ok"] and len(result["impacted"]) == 2
    assert not tracker.reverse("nope")["ok"]


def test_commitment_registry_gap():
    reg = comms.CommitmentRegistry()
    reg.promise("c1", kind="deliverable", description="Deliver the report")
    reg.promise("c2", kind="restriction", description="No raw data in output")
    result = reg.compare({"c1": True})
    assert len(result["gaps"]) == 1 and result["gaps"][0]["commitment_id"] == "c2"


def test_communication_loss_detector():
    now = [0.0]
    det = comms.CommunicationLossDetector(ack_timeout=10, progress_timeout=30,
                                          now_fn=lambda: now[0])
    det.assign("a1")
    now[0] += 11
    lost = det.check("a1")
    assert lost["state"] == "lost" and lost["recovery"] == "reassign"
    det.assign("a2")
    det.acknowledge("a2")
    now[0] += 31
    assert det.check("a2")["state"] == "stalled"
    det.progress("a2")
    assert det.check("a2")["state"] == "healthy"


def test_handoff_compression_preserves():
    original = {"critical_constraints": ["no raw data", "budget cap"],
                "critical_decisions": ["use local storage"]}
    full = {"constraints": ["no raw data", "budget cap"],
            "decisions": ["use local storage"]}
    assert comms.handoff_compression_test(original, full)["preserved"]
    slim = {"constraints": ["no raw data"], "decisions": []}
    result = comms.handoff_compression_test(original, slim)
    assert not result["preserved"] and len(result["missing"]) == 2


# ---------------------------------------------------------------- Group 5
def test_rights_ledger_attribution():
    ledger = gov.RightsLedger()
    ledger.record(gov.RightsEntry("e1", source="Example Lib", kind="code",
                                  license="MIT", incorporated_into="report tool"))
    record = ledger.attribution_record()
    assert len(record) == 1 and "Example Lib" in record[0]["attribution"]
    ledger.record(gov.RightsEntry("e2", source="Unknown Clip", kind="media",
                                  license="unknown", incorporated_into="video"))
    assert ledger.unlicensed() == ["e2"]


def test_residency_enforcement():
    enforcer = gov.ResidencyEnforcer(approved_regions=["eu-west"])
    assert enforcer.check(storage_region="local",
                          processing_location="eu-west")["ok"]
    assert not enforcer.check(storage_region="us-east",
                              processing_location="eu-west")["ok"]
    assert enforcer.check(storage_region="local",
                          processing_location="local")["ok"]


def test_consent_expiry():
    now = [0.0]
    reg = gov.ConsentRegistry(now_fn=lambda: now[0])
    reg.grant("k1", subject="user", data="notes", purpose="summary",
              expires_at=100.0)
    assert reg.check("k1", data="notes", purpose="summary")["ok"]
    assert not reg.check("k1", data="notes", purpose="ads")["ok"]
    now[0] += 101
    assert not reg.check("k1", data="notes", purpose="summary")["ok"]
    assert reg.sweep_expired() == ["k1"]


def test_release_gate_blocks_restricted():
    gate = gov.ReleaseGate(restricted_markers=["SECRET-KEY"],
                           required_warnings=["Draft: not final"])
    assert not gate.inspect("contains SECRET-KEY here")["ok"]
    assert not gate.inspect("clean text")["ok"]  # warning missing
    assert gate.inspect("clean text\nDraft: not final")["ok"]


def test_boundary_transform_audit():
    audit = gov.BoundaryAudit()
    audit.record_crossing(data_id="d1", from_boundary="project-a",
                          to_boundary="provider", transforms=["redaction"])
    audit.record_crossing(data_id="d2", from_boundary="project-a",
                          to_boundary="provider", transforms=[])
    assert len(audit.audit_for("d1")) == 1
    assert audit.audit_for("d1")[0]["transforms"] == ["redaction"]
    assert len(audit.untransformed_crossings()) == 1


def test_privacy_aggregation_minimized():
    records = [
        {"category": "chat", "quality_score": 4.0,
         "prompt": "my private question here"},
        {"category": "chat", "quality_score": 2.0, "prompt": "another secret"},
        {"category": "mission", "quality_score": 5.0, "raw": "sensitive"},
    ]
    report = gov.aggregate_reports(records)
    assert report["groups"]["chat"] == {"count": 2, "average_quality": 3.0}
    assert "private question" not in json.dumps(report)
    assert "sensitive" not in json.dumps(report)


# ---------------------------------------------------------------- Group 6
def test_thermal_scheduling_no_invented_telemetry():
    jobs = [{"id": "urgent-1", "urgent": True}, {"id": "bg-1", "urgent": False}]
    no_data = local.thermal_schedule(telemetry=None, pending_jobs=jobs)
    assert no_data["action"] == "normal"  # never invents throttling
    hot = local.thermal_schedule(
        telemetry={"throttling": True, "sustained_seconds": 120},
        pending_jobs=jobs)
    assert hot["action"] == "reduce_concurrency"
    assert hot["kept"] == ["urgent-1"] and hot["deferred"] == ["bg-1"]


def test_energy_aware_routing():
    modes = [
        {"name": "big", "energy": 10, "cost": 5, "latency": 1, "quality": 9},
        {"name": "small", "energy": 2, "cost": 1, "latency": 3, "quality": 7},
    ]
    assert local.route_by_energy(modes, on_battery=False)["choice"] == "small"
    assert local.route_by_energy(modes, on_battery=True)["choice"] == "small"
    assert local.route_by_energy([], on_battery=True)["choice"] is None


def test_warm_state_manager():
    mgr = local.WarmStateManager()
    mgr.set_state("m1", "ready")
    assert mgr.plan("m1")["reload"] is False
    mgr.set_state("m1", "loading")
    assert mgr.plan("m1")["action"] == "wait"
    mgr.record_load_time("m2", 10.0)
    mgr.record_load_time("m2", 14.0)
    assert mgr.average_load_time("m2") == 12.0
    plan = mgr.plan("m2")
    assert plan["reload"] and plan["expected_load_seconds"] == 12.0


def test_contention_forecast():
    result = local.contention_forecast(
        device_limits={"ram_mb": 8000},
        running=[{"ram_mb": 6000, "bandwidth_share": 0.6}],
        candidate={"ram_mb": 3000, "bandwidth_share": 0.5})
    assert not result["fits"] and len(result["problems"]) == 2
    ok = local.contention_forecast(
        device_limits={"ram_mb": 8000},
        running=[{"ram_mb": 1000, "bandwidth_share": 0.2}],
        candidate={"ram_mb": 1000, "bandwidth_share": 0.2})
    assert ok["fits"]


def test_model_integrity_checksum():
    with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
        fh.write(b"model bytes")
        path = fh.name
    try:
        digest = hashlib.sha256(b"model bytes").hexdigest()
        assert local.verify_model_file(path, expected_sha256=digest)["ok"]
        assert not local.verify_model_file(path, expected_sha256="0" * 64)["ok"]
        no_trust = local.verify_model_file(path)
        assert not no_trust["ok"] and "cannot be verified" in no_trust["note"]
    finally:
        os.unlink(path)


def test_overclaim_detector():
    result = local.detect_overclaim(
        advertised={"tool_calling": True, "structured_outputs": True},
        observed={"tool_calling": {"success_rate": 0.4},
                  "structured_outputs": {"success_rate": 0.95}})
    assert len(result["overclaimed"]) == 1
    assert "tool_calling" in result["overclaimed"][0]
    clean = local.detect_overclaim(
        advertised={"tool_calling": True},
        observed={"tool_calling": {"success_rate": 0.95}})
    assert clean["overclaimed"] == []


# ---------------------------------------------------------------- Group 7
def test_semantic_diff_behavior_change():
    old = "The agent must never delete files.\nFix typo here."
    new = "The agent must never delete files.\nThe agent may delete files on approval.\nFix typo heer."
    result = strat.semantic_diff(old, new)
    assert result["behavior_changing"]
    assert result["behavior_added"]
    cosmetic = strat.semantic_diff("Fix typo here.", "Fix typo heer.")
    assert not cosmetic["behavior_changing"]


def test_consumer_compatibility():
    assert strat.consumer_compatibility(
        artifact_kind="json", content='{"a": 1}',
        schema={"required": ["a"]})["ok"]
    assert not strat.consumer_compatibility(
        artifact_kind="json", content='{"a": 1}',
        schema={"required": ["a", "b"]})["ok"]
    assert not strat.consumer_compatibility(
        artifact_kind="json", content="{broken")["ok"]
    assert strat.consumer_compatibility(
        artifact_kind="spreadsheet_csv",
        content="name,score\nalice,9\n")["ok"]
    assert not strat.consumer_compatibility(
        artifact_kind="spreadsheet_csv", content="alice,9\nbob\n")["ok"]


def test_readiness_gate_blocks():
    full = {"permissions": True, "recoverability": True, "budget": True,
            "dependencies_available": True, "required_evidence": True,
            "escalation": True}
    assert strat.readiness_gate(full)["ready"]
    partial = dict(full, budget=False, escalation=False)
    result = strat.readiness_gate(partial)
    assert not result["ready"] and result["missing"] == ["budget", "escalation"]


def test_feature_interaction_matrix():
    result = strat.run_interaction_tests(strat.builtin_interaction_scenarios())
    assert result["all_passed"] and len(result["results"]) == 2
    failing = strat.run_interaction_tests(
        [{"name": "bad combo", "run": lambda: (False, "broke")}])
    assert not failing["all_passed"]


def test_debt_register():
    reg = strat.DebtRegister()
    reg.add(strat.DebtEntry("d1", "Disabled check X", "high", "owner",
                            kind="disabled check"))
    reg.add(strat.DebtEntry("d2", "Workaround Y", "low", "owner"))
    assert len(reg.open_items()) == 2
    assert reg.resolve("d1")
    assert len(reg.open_items()) == 1
    assert not reg.resolve("nope")


def test_sunset_manager_migration():
    mgr = strat.SunsetManager()
    mgr.register("tool-old", dependents=["workflow-a"],
                 migration_path="use tool-new")
    announced = mgr.announce("tool-old")
    assert announced["ok"] and announced["dependents"] == ["workflow-a"]
    blocked = mgr.retire("tool-old")
    assert not blocked["ok"] and blocked["unmigrated"] == ["workflow-a"]
    assert mgr.mark_migrated("tool-old", "workflow-a")
    assert mgr.retire("tool-old")["ok"]


# ---------------------------------------------------------------- Index / seams
def test_tenth_index_covers_all_42():
    assert len(index.ITEMS) == 42
    numbers = [row[0] for row in index.ITEMS]
    assert numbers == list(range(1, 43))
    assert all(row[1] and row[2] and row[3] and row[4] for row in index.ITEMS)
    subsystems = {row[2] for row in index.ITEMS}
    assert subsystems <= {"WE", "TPE", "TRL", "OC", "EIP"}
    assert len(subsystems) == 5  # filed into the existing grouping, no new stream
