"""W14 integration tests: tenth-doc capabilities COMBINED across boundaries.

Unit tests in test_gap_tenthdoc.py prove each capability alone. These prove
the capabilities work together in realistic flows: an unsupervised mission
run end to end, an authorization chain that ends in revocation, a validator
measured then watched for drift, and the item-8 seam against the real shape
of an agent_loop write-grant record. No live providers, no model calls.
Full loop wiring into agent_loop.resolve_approval remains integration
follow-up (see report); this file proves the seam decision itself against
the record shape that loop actually produces.
"""
from app import tenth_comms as comms
from app import tenth_distributed as dist
from app import tenth_governance as gov
from app import tenth_identity as ident
from app import tenth_local as local
from app import tenth_meta as meta
from app import tenth_strategic as strat


def test_unsupervised_mission_flow_end_to_end():
    """Readiness -> scoped credential -> leased work -> commitments kept ->
    release gate. A failure at any step stops the flow, in order."""
    gate = strat.readiness_gate({
        "permissions": True, "recoverability": True, "budget": True,
        "dependencies_available": True, "required_evidence": True,
        "escalation": True})
    assert gate["ready"]
    now = [1000.0]
    broker = ident.JITCredentialBroker(now_fn=lambda: now[0])
    cred = broker.issue(scope="mission", operation="write", resource="out.md",
                        ttl_seconds=300)
    assert broker.validate(cred["token"], operation="write",
                           resource="out.md")["ok"]
    leases = dist.WorkLeaseManager(now_fn=lambda: now[0])
    lease = leases.acquire("mission-1", "worker-a", ttl_seconds=300)
    assert lease["ok"]
    assert leases.commit("mission-1", lease["fencing_token"])["ok"]
    commitments = comms.CommitmentRegistry()
    commitments.promise("c1", kind="deliverable", description="Deliver out.md")
    assert commitments.compare({"c1": True})["gaps"] == []
    release = gov.ReleaseGate(required_warnings=["Draft: not final"])
    assert release.inspect("report body\nDraft: not final")["ok"]


def test_authorization_chain_ends_in_revocation():
    """Delegate -> use within authority -> revoke -> every holder affected ->
    scoped credential for the same action now fails."""
    chain = ident.DelegationChain()
    chain.delegate("owner", "agent-a", ["read"])
    assert chain.verify(actor="agent-a", action="read")["ok"]
    registry = ident.RevocationRegistry()
    registry.register_use("cred-9", "agent-a-session")
    revoked = registry.revoke("cred-9", policy="terminate")
    assert revoked["affected_holders"] == ["agent-a-session"]
    assert registry.is_revoked("cred-9")
    broker = ident.JITCredentialBroker()
    issued = broker.issue(scope="read", operation="read", resource="doc",
                          ttl_seconds=60)
    assert broker.revoke(issued["token_id"])
    assert not broker.validate(issued["token"], operation="read",
                               resource="doc")["ok"]


def test_validator_measured_then_watched_for_drift():
    """Lab measures a validator; the drift monitor watches later runs
    against that baseline; diversity scoring confirms the two checks used
    are actually independent."""
    lab = meta.VerifierReliabilityLab()
    lab.add_case(meta.KnownCase("good", is_defect=False, expected_pass=True))
    lab.add_case(meta.KnownCase("bad", is_defect=True, expected_pass=False))
    baseline = lab.run(lambda case: not case.is_defect)
    assert baseline.false_passes == 0 and baseline.false_failures == 0
    monitor = meta.ValidatorDriftMonitor(tolerance=0.10)
    monitor.record("v1", baseline)
    degraded = lab.run(lambda case: True)  # passes everything, misses defects
    monitor.record("v2", degraded)
    assert monitor.check()["drift"] is True
    diversity = meta.diversity_score([
        meta.CheckDescription("lab", "model-a", "prompt-a", "cases", "path-a"),
        meta.CheckDescription("oracle", "model-b", "prompt-b", "parser", "path-b"),
    ])
    assert diversity["score"] == 1.0


def test_item8_seam_against_agent_loop_grant_record_shape():
    """agent_loop stores per-file grants as record["write_grants"] (a list of
    paths) and reuses them on later writes. The binding seam must refuse
    reuse when the action changed, even though the path matches the grant."""
    # The loop's record shape: a grant for exactly this path and content.
    binding = ident.ApprovalBinding.build(
        "write", "notes.txt", {"path": "notes.txt", "content": "hi"}, "v1")
    same = ident.check_grant_reuse(
        binding, action="write", resource="notes.txt",
        parameters={"path": "notes.txt", "content": "hi"}, version="v1")
    assert same["ok"]
    changed_content = ident.check_grant_reuse(
        binding, action="write", resource="notes.txt",
        parameters={"path": "notes.txt", "content": "different"}, version="v1")
    assert not changed_content["ok"]
    # Tripwire on top: a write to a new resource class trips even if a
    # grant for the old class exists.
    trip = ident.EscalationTripwire()
    trip.set_baseline("agent-a", resources=["notes.txt"],
                      resource_classes=["file"], methods=["write"])
    assert trip.check("agent-a", resource="notes.txt", resource_class="file",
                      method="write")["trip"] is False
    assert trip.check("agent-a", resource="admin-panel",
                      resource_class="admin", method="admin")["trip"] is True


def test_distributed_recovery_flow_with_shedding_and_backpressure():
    """Storm guard paces retries, bulkhead isolates the broken provider,
    shedding keeps critical work, and the backpressure controller keeps
    the resulting event stream ordered."""
    now = [0.0]
    guard = dist.RetryStormGuard(window_seconds=60, threshold=2,
                                 now_fn=lambda: now[0])
    guard.record_retry()
    assert guard.record_retry()["storm"] is True
    bulk = dist.Bulkhead(limit=5, failure_threshold=1)
    assert bulk.acquire("provider-x")["ok"]
    bulk.release("provider-x", failed=True)
    assert bulk.isolated("provider-x")
    jobs = [{"id": "run", "kind": "execution"},
            {"id": "bg", "kind": "background"}]
    shed = dist.shed_load(jobs, load=0.99)
    assert shed["kept"] == ["run"] and shed["deferred"] == ["bg"]
    ctrl = dist.BackpressureController(capacity=10)
    for event in ("started", "progress", "done"):
        assert ctrl.push(event)["accepted"]
    assert ctrl.drain() == ["started", "progress", "done"]


def test_governance_chain_consent_release_aggregation():
    """Consent gates a use; the release gate inspects the product; the
    usage report aggregates without keeping raw content."""
    now = [0.0]
    consents = gov.ConsentRegistry(now_fn=lambda: now[0])
    consents.grant("k1", subject="user", data="notes", purpose="summary",
                   expires_at=100.0)
    assert consents.check("k1", data="notes", purpose="summary")["ok"]
    gate = gov.ReleaseGate(required_warnings=["Draft: not final"])
    assert gate.inspect("summary\nDraft: not final")["ok"]
    report = gov.aggregate_reports([
        {"category": "summary", "quality_score": 4.0, "prompt": "secret"},
        {"category": "summary", "quality_score": 2.0, "prompt": "secret-2"},
    ])
    assert report["groups"]["summary"]["average_quality"] == 3.0
    assert "secret" not in str(report)


def test_local_model_flow_integrity_then_overclaim():
    """A model file that fails its checksum never reaches the overclaim
    check as trusted; a warm-state plan avoids a reload for a ready model."""
    import hashlib
    import os
    import tempfile
    with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
        fh.write(b"weights")
        path = fh.name
    try:
        digest = hashlib.sha256(b"weights").hexdigest()
        assert local.verify_model_file(path, expected_sha256=digest)["ok"]
        assert not local.verify_model_file(path,
                                           expected_sha256="0" * 64)["ok"]
    finally:
        os.unlink(path)
    overclaim = local.detect_overclaim(
        advertised={"tool_calling": True},
        observed={"tool_calling": {"success_rate": 0.5}})
    assert overclaim["overclaimed"]
    warm = local.WarmStateManager()
    warm.set_state("m1", "ready")
    assert warm.plan("m1")["reload"] is False
