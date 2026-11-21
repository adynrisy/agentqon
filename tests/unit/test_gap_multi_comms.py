"""W5 gap-fix: multi-agent comms (expansion D) + §5 coordination."""
import time

import pytest

from app.comms import (AgentAuditLog, AgentWatchdog, AssignmentRegistry,
                       ChainLog, ConcurrencyGate, DeliveryLog,
                       ResultDedup, TaskLease, TaskOwnerRegistry,
                       TaskStateRegistry, check_handoff_complete,
                       check_output_length, check_size, flag_contradiction,
                       is_fresh, make_envelope, make_evidence_ref,
                       make_handoff, resume_or_reassign, validate_envelope)
from app.domain import now_iso


def test_envelope_complete_and_validated():
    env = make_envelope("a1", "a2", "m1", "t1", "handoff",
                        {"state": "requested"})
    assert validate_envelope(env) == []
    for field in ("sender", "recipient", "mission_id", "task_id", "type",
                  "timestamp", "correlation_id"):
        assert env[field]
    broken = dict(env)
    del broken["recipient"]
    assert any("recipient" in e for e in validate_envelope(broken))


def test_handoff_states_and_completeness():
    ok, missing = check_handoff_complete(
        make_handoff("requested", "t1", ask="do the research"))
    assert ok and missing == []
    ok, missing = check_handoff_complete(make_handoff("incomplete", "t1"))
    assert not ok and len(missing) == 2  # missing + reason
    with pytest.raises(ValueError):
        make_handoff("freeform", "t1", text="whatever")


def test_message_dedup_processes_once():
    log = DeliveryLog()
    assert log.process_once("m-1") is True
    assert log.process_once("m-1") is False


def test_stale_message_flagged_not_acted_on():
    reg = TaskStateRegistry()
    v1 = reg.set_state("t1", "running")
    reg.set_state("t1", "completed")
    stale = make_envelope("a1", "a2", "m1", "t1", "update", {},
                          task_state_version=v1)
    flagged, reason = reg.is_stale(stale)
    assert flagged and "older state" in reason
    fresh = make_envelope("a1", "a2", "m1", "t1", "update", {},
                          task_state_version=reg.current_version("t1"))
    assert reg.is_stale(fresh)[0] is False


def test_correlation_chain_traceable():
    chain = ChainLog()
    env1 = make_envelope("a1", "a2", "m1", "t1", "request", {},
                         correlation_id="corr-9")
    env2 = make_envelope("a2", "a1", "m1", "t1", "reply", {},
                         correlation_id="corr-9")
    chain.append(env1)
    chain.append(env2)
    assert len(chain.chain_for("corr-9")) == 2


def test_timeout_policy_declares_silent_agent():
    watch = AgentWatchdog(timeout_seconds=10)
    watch.register("a1", now=1000.0)
    assert watch.timed_out_agents(now=1005.0) == []
    timed_out = watch.timed_out_agents(now=1015.0)
    assert len(timed_out) == 1
    assert timed_out[0]["status"] == "timed out by policy"
    # Declared once: a second check does not re-declare.
    assert watch.timed_out_agents(now=1020.0) == []


def test_heartbeat_resets_timeout_and_idle_cleanup():
    watch = AgentWatchdog(timeout_seconds=10, idle_seconds=30)
    watch.register("a1", now=1000.0)
    watch.heartbeat("a1", now=1009.0)
    assert watch.timed_out_agents(now=1015.0) == []
    assert watch.cleanup_idle(now=1050.0) == ["a1"]


def test_single_owner_enforced_and_transfer():
    owners = TaskOwnerRegistry()
    assert owners.claim("t1", "a1")[0] is True
    ok, reason = owners.claim("t1", "a2")
    assert ok is False and "already owned" in reason
    assert owners.validate_owner("t1", "a1") is True
    assert owners.validate_owner("t1", "a2") is False
    assert owners.transfer("t1", "a1", "a2")[0] is True
    assert owners.owner("t1") == "a2"
    assert owners.release("t1", "a2") is True


def test_message_size_limit_refuses():
    env = make_envelope("a1", "a2", "m1", "t1", "data",
                        {"blob": "x" * 100000})
    ok, reason = check_size(env)
    assert ok is False and "too large" in reason


def test_evidence_reference_carries_no_document_body():
    ref = make_evidence_ref("src-1", "artifact:art-9", verified=True)
    d = ref.to_dict()
    assert "body" not in d and "text" not in d and "content" not in d
    assert d["pointer"] == "artifact:art-9"
    with pytest.raises(ValueError):
        make_evidence_ref("src-1", "artifact:art-9", verified=False)


def test_task_lease_expiry_never_repeats_irreversible_actions():
    lease = TaskLease(task_id="t1", agent_id="a1", expires_at=1000.0,
                      completed_action_keys=["email-1", "purchase-7"])
    assert lease.expired(now=1001.0) is True
    plan = resume_or_reassign(lease, new_agent_id="a2",
                              inspection="agent stopped responding")
    assert plan["state"] == "reassigned"
    assert plan["skip_action_keys"] == ["email-1", "purchase-7"]
    assert "never repeated" in plan["rule"]


def test_assignment_ack_and_duplicate_assignment():
    reg = AssignmentRegistry()
    assert reg.assign("t1", "a1")[0] is True
    ok, reason = reg.assign("t1", "a2")
    assert ok is False and "already assigned" in reason
    assert reg.acknowledge("t1", "a1") is True
    assert reg.acknowledge("t1", "a2") is False
    assert reg.acknowledge_handoff("h-1") is True
    assert reg.acknowledge_handoff("h-1") is False


def test_replacement_continues_task_state():
    reg = AssignmentRegistry()
    reg.assign("t1", "a1")
    out = reg.replace_agent("t1", "a1", "a2", "agent timed out")
    assert out["replaced"] is True
    assert "not repeated" in out["rule"]
    assert reg.replace_agent("t1", "a1", "a3", "x")["replaced"] is False


def test_output_length_limit():
    assert check_output_length("short")[0] is True
    ok, reason = check_output_length("x" * 300000)
    assert ok is False and "too long" in reason


def test_shared_result_freshness():
    fresh = {"recorded_at": now_iso()}
    assert is_fresh(fresh, max_age_seconds=3600) is True
    assert is_fresh(fresh, max_age_seconds=0) is False
    assert is_fresh({}, max_age_seconds=3600) is False
    assert is_fresh({"recorded_at": "not-a-time"}, max_age_seconds=3600) is False


def test_concurrency_gate():
    gate = ConcurrencyGate(2)
    assert gate.acquire() and gate.acquire()
    assert gate.acquire() is False
    gate.release()
    assert gate.acquire() is True


def test_lifecycle_audit_log():
    log = AgentAuditLog()
    log.record("a1", "assigned", "task t1")
    log.record("a1", "timed_out", "no heartbeat")
    events = log.events_for("a1")
    assert [e["event"] for e in events] == ["assigned", "timed_out"]
    assert log.events_for("unknown") == []
