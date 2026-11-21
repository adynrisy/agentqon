"""v21.4 hardening regression tests — backend audit fixes.

Each test pins one audited defect:
  1. orchestrator holds no per-run state on the shared instance;
  2. persona briefs are tenant-scoped for custom agents;
  3. queue claiming is atomic across processes, leases heartbeat,
     and completion/failure writes are fenced on lease_owner;
  4. the inline runner claims the requested job, not the oldest;
  5. vault env parsing accepts the documented rotation setup;
  6. brain-key rotation is atomic — exactly one active key per tenant;
  7. the kill switch holds inline runs too;
  9. jobs store/serve error summaries, never tracebacks;
 10. a decided approval can never be flipped;
 11. netpolicy validates ports and unwraps IPv4-mapped IPv6.
"""
import asyncio
import ipaddress
import os
import tempfile
import threading

import pytest
from cryptography.fernet import Fernet

from app.domain import StateConflict
from app.netpolicy import (UrlPolicyError, _ip_blocked,
                           assert_resolved_host_safe, validate_endpoint_url)
from app.orchestrator import Orchestrator
from app.store import Store
from app.vault import Vault, VaultError
from app.worker import _run_with_heartbeat, run_job_inline


def make_store(tmp):
    return Store(f"{tmp}/h.db", f"{tmp}/artifacts")


CONTRACT = {"objective": "x", "deliverables": ["Brief"], "acceptance": [],
            "constraints": {"max_cost_usd": 10}, "tools": {"allow": [], "deny": []},
            "approval_policy": {"external_side_effects": "required"},
            "data_classification": "internal"}


# ------------------------------------------------------------- 1. orchestrator

def test_orchestrator_instance_holds_no_per_run_state():
    with tempfile.TemporaryDirectory() as tmp:
        orch = Orchestrator(make_store(tmp))
        for attr in ("_runtime", "_strict_byok", "_model_state", "_active_traces"):
            assert not hasattr(orch, attr), attr


# ------------------------------------------------------------- 2. personas

def test_persona_briefs_are_tenant_scoped():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        s.put_persona("custom.helper", "ALPHA brief", tenant_id="org_a")
        s.put_persona("custom.helper", "BRAVO brief", tenant_id="org_b")
        assert s.get_persona("custom.helper", "org_a") == "ALPHA brief"
        assert s.get_persona("custom.helper", "org_b") == "BRAVO brief"
        # No shared-scope brief exists for a custom agent.
        assert s.get_persona("custom.helper") is None
        # Deleting one tenant's brief leaves the other's intact.
        assert s.delete_persona("custom.helper", tenant_id="org_a") is True
        assert s.get_persona("custom.helper", "org_a") is None
        assert s.get_persona("custom.helper", "org_b") == "BRAVO brief"


def test_roster_personas_stay_shared_with_tenant_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        s.put_persona("agency.design.x", "roster brief")  # shared scope
        assert s.get_persona("agency.design.x") == "roster brief"
        # A tenant without its own brief falls back to the shared one.
        assert s.get_persona("agency.design.x", "org_a") == "roster brief"
        # A tenant-scoped override wins for that tenant only.
        s.put_persona("agency.design.x", "override", tenant_id="org_a")
        assert s.get_persona("agency.design.x", "org_a") == "override"
        assert s.get_persona("agency.design.x", "org_b") == "roster brief"


# ------------------------------------------------------------- 3. queue

def test_two_stores_cannot_claim_the_same_job():
    """Two Store instances on one database file = two processes."""
    with tempfile.TemporaryDirectory() as tmp:
        a = make_store(tmp)
        b = Store(f"{tmp}/h.db", f"{tmp}/artifacts")
        job = a.enqueue_job("org", "mis_1")
        claimed_a = a.claim_job("worker-a")
        claimed_b = b.claim_job("worker-b")
        assert claimed_a is not None and claimed_a["id"] == job["id"]
        assert claimed_b is None


def test_concurrent_claims_across_stores_are_all_distinct():
    with tempfile.TemporaryDirectory() as tmp:
        a = make_store(tmp)
        b = Store(f"{tmp}/h.db", f"{tmp}/artifacts")
        jobs = [a.enqueue_job("org", f"mis_{i}") for i in range(6)]
        claimed = []
        stores = [a, b, a, b, a, b, a, b]
        for i, s in enumerate(stores):
            got = s.claim_job(f"w{i}")
            if got:
                claimed.append(got["id"])
        assert sorted(claimed) == sorted(j["id"] for j in jobs)


def test_stale_owner_is_fenced_out_of_terminal_writes():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org", "mis_1")
        s.claim_job("owner-a", lease_seconds=120)
        # owner-a dies; its lease expires and owner-b reclaims the job.
        s._exec("UPDATE jobs SET lease_expires='2000-01-01T00:00:00Z' WHERE id=?",
                (job["id"],))
        reclaimed = s.claim_job("owner-b")
        assert reclaimed["lease_owner"] == "owner-b"
        # The stale owner can no longer complete, fail, requeue or
        # heartbeat the job it lost.
        s.complete_job(job["id"], owner="owner-a")
        out = s.fail_job(job["id"], "stale failure", owner="owner-a")
        assert out.get("fenced") is True
        s.requeue_job(job["id"], 0, owner="owner-a")
        assert s.heartbeat_job(job["id"], owner="owner-a") is False
        row = s.get_job("org", job["id"])
        assert row["status"] == "running" and row["lease_owner"] == "owner-b"
        # The live owner can.
        assert s.heartbeat_job(job["id"], owner="owner-b") is True
        s.complete_job(job["id"], owner="owner-b")
        assert s.get_job("org", job["id"])["status"] == "completed"


def test_heartbeat_loop_extends_lease_during_long_run():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org", "mis_1")
        claimed = s.claim_job("w", lease_seconds=1)
        before = claimed["lease_expires"]
        beats = []
        real_hb = s.heartbeat_job

        def counting_hb(job_id, lease_seconds=120, owner=None):
            beats.append(job_id)
            return real_hb(job_id, lease_seconds=lease_seconds, owner=owner)
        s.heartbeat_job = counting_hb

        async def slow():
            await asyncio.sleep(0.8)
            return "done"

        result = asyncio.run(_run_with_heartbeat(
            s, job["id"], "w", slow(), interval=0.02))
        assert result == "done"
        # The lease was renewed repeatedly while the job ran (each
        # heartbeat is a committing write, so the count is bounded by
        # this sandbox's fsync latency, not by the interval alone).
        assert len(beats) >= 2
        after = s.get_job("org", job["id"])["lease_expires"]
        assert after > before


# ------------------------------------------------------------- 4 + 7. inline runner

class _RecordingOrchestrator:
    """Duck-typed orchestrator: records which missions it was asked to
    run and completes instantly."""
    def __init__(self):
        self.ran = []

    async def run_mission(self, tenant_id, mission_id, job_id=None):
        # job_id: the worker passes the owning job's id since the
        # 2026-10-22 backend-audit B3 repair (duplicate-run ownership).
        self.ran.append(mission_id)
        return {"mission_id": mission_id}

    async def revise_candidate(self, *args):
        raise AssertionError("not used here")


def _mission(store, tenant, title):
    return store.create_mission(tenant, "tester", title, CONTRACT, 10.0)


def test_inline_runner_runs_the_requested_job_not_the_oldest():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        orch = _RecordingOrchestrator()
        m_old = _mission(s, "org", "old")
        m_new = _mission(s, "org", "new")
        job_old = s.enqueue_job("org", m_old["id"])   # queued first
        job_new = s.enqueue_job("org", m_new["id"])
        out = run_job_inline(s, orch, job_new["id"])
        assert out["result"] == "completed"
        assert orch.ran == [m_new["id"]]  # ONLY the requested mission ran
        assert s.get_job("org", job_new["id"])["status"] == "completed"
        # The older job was never touched.
        assert s.get_job("org", job_old["id"])["status"] == "queued"


def test_inline_runner_honours_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        orch = _RecordingOrchestrator()
        m = _mission(s, "org", "held")
        job = s.enqueue_job("org", m["id"])
        s.set_flag("kill_switch", "on")
        try:
            out = run_job_inline(s, orch, job["id"])
        finally:
            s.set_flag("kill_switch", "off")
        assert out["result"] == "kill_switch_hold"
        assert orch.ran == []
        row = s.get_job("org", job["id"])
        assert row["status"] == "queued" and row["attempts"] == 0  # refunded


# ------------------------------------------------------------- 5. vault env

def _clear_vault_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("OMNIMIND_VAULT_KEY"):
            monkeypatch.delenv(name, raising=False)


def test_vault_env_main_key_plus_version_var(monkeypatch, tmp_path):
    """The audited breakage: OMNIMIND_VAULT_KEY_VERSION=v1 next to a
    main key used to poison the key set with a bogus 'version' key."""
    _clear_vault_env(monkeypatch)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("OMNIMIND_VAULT_KEY", key)
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_VERSION", "v1")
    v = Vault.from_env(tmp_path)
    assert v.active_version == "v1"
    version, token = v.encrypt("secret")
    assert v.decrypt(version, token) == "secret"


def test_vault_env_rotation_versions(monkeypatch, tmp_path):
    _clear_vault_env(monkeypatch)
    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    old = Vault({"v1": k1}, "v1")
    _, old_token = old.encrypt("rotated-secret")
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_V1", k1.decode())
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_V2", k2.decode())
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_VERSION", "v2")
    v = Vault.from_env(tmp_path)
    assert v.active_version == "v2"
    assert v.decrypt("v1", old_token) == "rotated-secret"  # old stays readable
    version, token = v.encrypt("fresh")
    assert version == "v2" and v.decrypt("v2", token) == "fresh"


def test_vault_env_versioned_key_only(monkeypatch, tmp_path):
    _clear_vault_env(monkeypatch)
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_V2", Fernet.generate_key().decode())
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_VERSION", "v2")
    v = Vault.from_env(tmp_path)
    assert v.active_version == "v2"


def test_vault_env_still_rejects_garbage_keys(monkeypatch, tmp_path):
    _clear_vault_env(monkeypatch)
    monkeypatch.setenv("OMNIMIND_VAULT_KEY_V1", "not-a-fernet-key")
    with pytest.raises(VaultError):
        Vault.from_env(tmp_path)


# ------------------------------------------------------------- 6. brain keys

def test_brain_key_rotation_leaves_exactly_one_active():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        s.create_brain_key("org", "u", "first", "hash-1", "aqb_1")
        s.create_brain_key("org", "u", "second", "hash-2", "aqb_2")
        s.create_brain_key("org", "u", "third", "hash-3", "aqb_3")
        active = s._all("SELECT * FROM brain_keys WHERE tenant_id='org' AND revoked=0")
        assert len(active) == 1 and active[0]["key_hash"] == "hash-3"
        assert s.get_active_brain_key("org")["key_hash"] == "hash-3"


def test_brain_key_concurrent_creates_still_one_active():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        errors = []

        def create(i):
            try:
                s.create_brain_key("org", "u", f"k{i}", f"hash-{i}", f"aqb_{i}")
            except Exception as exc:  # pragma: no cover - failure surface
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        active = s._all("SELECT * FROM brain_keys WHERE tenant_id='org' AND revoked=0")
        assert len(active) == 1


# ------------------------------------------------------------- 9. error hygiene

FAKE_TRACEBACK = (
    "Traceback (most recent call last):\n"
    "  File \"/home/hatch/workspace/omnimind-ai/app/worker.py\", line 97, in process_one\n"
    "    asyncio.run(_dispatch(store, orchestrator, job))\n"
    "RuntimeError: provider exploded at https://internal.example/x")


def test_job_errors_store_summaries_not_tracebacks():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org", "mis_1")
        s.claim_job("w")
        s.fail_job_terminal(job["id"], FAKE_TRACEBACK)
        row = s._one("SELECT last_error FROM jobs WHERE id=?", (job["id"],))
        stored = row["last_error"]
        assert "Traceback" not in stored and "/home/" not in stored
        assert stored == "RuntimeError: provider exploded at https://internal.example/x"
        assert len(stored) <= 300
        # And what the API serves is the same summary.
        assert s.get_job("org", job["id"])["last_error"] == stored


def test_legacy_traceback_rows_are_scrubbed_on_read():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        job = s.enqueue_job("org", "mis_1")
        s._exec("UPDATE jobs SET last_error=? WHERE id=?",
                (FAKE_TRACEBACK, job["id"]))
        served = s.list_jobs("org")[0]["last_error"]
        assert "Traceback" not in served and "/home/" not in served
        assert served.startswith("RuntimeError:")


# ------------------------------------------------------------- 10. approvals

def test_decided_approval_cannot_be_flipped():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_store(tmp)
        iid = s.put_invocation("org", "mis_1", "web.read", "high", "pending", {})
        aid = s.put_approval("org", "mis_1", iid, "preview")
        decided = s.decide_approval("org", "mis_1", aid, "approved")
        assert decided["status"] == "approved"
        with pytest.raises(StateConflict):
            s.decide_approval("org", "mis_1", aid, "denied")
        row = s._one("SELECT * FROM approvals WHERE id=?", (aid,))
        assert row["status"] == "approved"  # never flipped
        inv = s._one("SELECT * FROM tool_invocations WHERE id=?", (iid,))
        assert inv["status"] == "approved"
        # Unknown approval still reads as not-found (None), not a conflict.
        assert s.decide_approval("org", "mis_1", "apr_missing", "approved") is None


# ------------------------------------------------------------- 11. netpolicy

def test_url_policy_rejects_bad_ports():
    for url in ("http://127.0.0.1:99999/v1", "http://127.0.0.1:0/v1",
                "http://127.0.0.1:bad/v1", "https://api.example.com:70000/v1"):
        with pytest.raises(UrlPolicyError) as exc:
            validate_endpoint_url(url, kind="direct_provider")
        assert exc.value.category == "port", url
    ok = validate_endpoint_url("https://api.example.com:8443/v1",
                                kind="direct_provider")
    assert ok["host"] == "api.example.com"


def test_ipv4_mapped_ipv6_classifies_like_ipv4():
    mapped_loopback = ipaddress.ip_address("::ffff:127.0.0.1")
    assert _ip_blocked(mapped_loopback) is None  # loopback, like 127.0.0.1
    mapped_metadata = ipaddress.ip_address("::ffff:169.254.169.254")
    assert _ip_blocked(mapped_metadata) == "metadata_endpoint"
    ok = validate_endpoint_url("http://[::ffff:127.0.0.1]:11434/v1",
                               kind="direct_provider")
    assert ok["is_local"] is True
    with pytest.raises(UrlPolicyError) as exc:
        assert_resolved_host_safe("http://[::ffff:169.254.169.254]/latest")
    assert exc.value.category == "metadata_endpoint"
    with pytest.raises(UrlPolicyError) as exc:
        assert_resolved_host_safe("http://[::ffff:192.168.1.5]:8080/v1",
                                  allow_local=False)
    assert exc.value.category == "private_resolution"
    # The same mapped private address is fine for a local endpoint.
    assert_resolved_host_safe("http://[::ffff:192.168.1.5]:8080/v1",
                              allow_local=True)
