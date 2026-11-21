"""Gap-seventhdoc group B: trust-boundary security (execution boundary)."""
import pytest

from app.trust_boundary import (
    MemoryFirewall, attest_action, check_flow, combine_taint, detect_identity_change,
    egress_decide, taint, tripwire_check, trusted)


def test_b6_taint_blocks_shell_api_decision_without_review():
    item = taint("do this from the webpage", "webpage")
    assert check_flow(item, "shell_command")["allowed"] is False
    assert check_flow(item, "api_call")["allowed"] is False
    assert check_flow(item, "decision")["allowed"] is False
    assert check_flow(item, "shell_command", reviewed=True)["allowed"] is True
    assert check_flow(trusted("user typed this"), "shell_command")["allowed"] is True
    combined = {"value": "x", "taint": combine_taint(
        trusted("a"), taint("b", "email"))}
    assert combined["taint"] == "email"
    with pytest.raises(ValueError):
        taint("x", "model_hallucination")


def test_b7_tripwire_blocks_before_send_without_echoing_secret():
    secret = "sk-abcdefgh12345678"
    out = tripwire_check({"body": f"please use key {secret} now",
                          "log": "normal line"})
    assert out["blocked"] is True
    assert secret not in str(out["hits"])
    assert tripwire_check({"body": "hello world"})["blocked"] is False
    assert tripwire_check({"key": "-----BEGIN RSA PRIVATE KEY-----"})["blocked"] is True


def test_b8_egress_deny_by_default_for_sensitive_projects():
    policy = {"allowed_domains": ["api.example.com"],
              "allowed_services": ["search"]}
    assert egress_decide(policy, "api.example.com", "search",
                         "confidential")["allowed"] is True
    assert egress_decide(policy, "evil.example.com", "search",
                         "confidential")["allowed"] is False
    assert egress_decide(policy, "evil.example.com", "other",
                         "public")["allowed"] is False


def test_b9_memory_firewall_same_project_ok_cross_needs_grant():
    fw = MemoryFirewall()
    assert fw.can_read("t1", "p1", "p1")["allowed"] is True
    assert fw.can_read("t1", "p2", "p1")["allowed"] is False
    fw.grant_share("t1", "p1", "p2", approver="owner")
    assert fw.can_read("t1", "p2", "p1")["allowed"] is True
    # Another tenant cannot reuse the grant.
    assert fw.can_read("t2", "p2", "p1")["allowed"] is False
    assert any(a["action"] == "denied" for a in fw.audit)


def test_b10_attestation_detects_mid_mission_identity_change():
    base = attest_action("agent-v1", {"model": "m1"}, {"s": "1.0"}, ["web.read"])
    same = attest_action("agent-v1", {"model": "m1"}, {"s": "1.0"}, ["web.read"])
    changed = attest_action("agent-v1", {"model": "m2"}, {"s": "1.0"}, ["web.read"])
    assert detect_identity_change([base, same])["changed"] is False
    out = detect_identity_change([base, changed])
    assert out["changed"] is True and out["at_action"] == 1
