"""W16 twelfth-document gap fixes: OS integration and edge cases.

One test per requirement (28 items); item 19 asserts NOT_APPLICABLE.
No test edits existing behaviour; every test runs on Linux, and the
Windows-only paths assert their honest not-applicable / guarded form.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import osint as O


# (1) Native Credential Vault Bridge
def test_vault_bridge_os_backend_and_plain_fallback(tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return "ok"

    bridge = O.OsVaultBridge(runner=runner)
    assert bridge.backend == "linux_secret_service"
    stored = bridge.store("conn-a", "secret-value-never-in-reports")
    assert stored["backend"] == "linux_secret_service"
    assert "secret-value" not in json.dumps(stored)
    # The runner calls start with the backend-detection probe; the store
    # call itself carries the value to the OS vault — that is the vault
    # channel, not a report; reports above carry no value.
    assert any(c[0] == "os-vault-store" and c[1] == "conn-a" for c in calls)
    migrated = O.migrate_vault_entries([{"name": "conn-a"}], bridge)
    assert migrated[0]["fallback_copy_kept"] is True
    assert "secret-value" not in json.dumps(migrated)

    def failing_runner(cmd):
        raise OSError("no secret service")

    fallback = O.OsVaultBridge(runner=failing_runner)
    assert fallback.backend == "local_vault"
    assert fallback.fallback is True
    info = O.detect_os_vault_backend(runner=failing_runner)
    assert info["backend"] == "local_vault" and info["fallback"] is True
    assert "plain text" in info["message"] or "plain" in info["message"]


# (2) Local IPC Security
def test_ipc_caller_verification_and_endpoint_privacy(tmp_path):
    assert O.verify_caller_process(os.getpid())["allowed"] is True
    assert O.verify_caller_process(9999999)["allowed"] is False
    f = tmp_path / "sock"
    f.write_text("x")
    os.chmod(f, 0o644)
    assert O.audit_ipc_endpoint(f)["private"] is False
    os.chmod(f, 0o600)
    assert O.audit_ipc_endpoint(f)["private"] is True
    assert O.audit_ipc_endpoint(tmp_path / "missing")["exists"] is False


# (3) Localhost Exposure Auditor
def test_localhost_exposure_auditor():
    bad = O.audit_localhost_exposure(bind_host="0.0.0.0", port=8077,
                                     host_allowlist=["127.0.0.1"],
                                     origin_check_enabled=True, listeners=[])
    assert bad["exposed"] is True and bad["verdict"] == "exposed"
    good = O.audit_localhost_exposure(bind_host="127.0.0.1", port=8077,
                                      host_allowlist=["127.0.0.1", "localhost"],
                                      origin_check_enabled=True, listeners=[])
    assert good["exposed"] is False and good["verdict"] == "loopback_only"
    live = O.audit_localhost_exposure(bind_host="127.0.0.1", port=8077,
                                      host_allowlist=["127.0.0.1"],
                                      origin_check_enabled=True,
                                      listeners=["0.0.0.0"])
    assert live["exposed"] is True


# (4) Filesystem Redirection Guard
def test_redirection_guard(tmp_path):
    inside = tmp_path / "proj"
    outside = tmp_path / "outside.txt"
    inside.mkdir()
    outside.write_text("secret")
    (inside / "good.txt").write_text("ok")
    os.symlink(str(outside), str(inside / "escape-link"))
    os.symlink("good.txt", str(inside / "inner-link"))
    scan = O.scan_redirections(inside)
    escapes = {f["path"] for f in scan["escapes"]}
    assert any("escape-link" in p for p in escapes)
    assert not any("inner-link" in p for p in escapes)
    assert scan["junction_check"] == "not_applicable_on_this_platform"
    assert O.is_junction(inside / "good.txt") is False
    with pytest.raises(ValueError, match="outside the project folder"):
        O.guard_path(inside, outside)
    assert O.guard_path(inside, inside / "good.txt").exists()


# (5) Executable Resolution Verification
def test_executable_resolution(tmp_path):
    trap_dir = tmp_path / "cwd"
    trap_dir.mkdir()
    planted = trap_dir / "mycmd"
    planted.write_text("#!/bin/sh\necho planted\n")
    os.chmod(planted, 0o755)
    result = O.verify_executable("mycmd", cwd=trap_dir, path_env=str(tmp_path))
    assert result["trap_in_cwd"] is True
    assert any("working folder" in w for w in result["warnings"])
    py = O.verify_executable("python3")
    assert py["ok"] is True and py["resolved"]
    assert O.verify_executable("definitely-not-a-real-cmd-xyz")["ok"] is False
    assert any("written to" in w for w in O.check_path_entries(str(tmp_path)))


# (6) Privilege Boundary Manager
def test_privilege_boundary():
    assert O.classify_privilege("bind_privileged_port", "80")["needs_elevation"] is True
    assert O.classify_privilege("bind_privileged_port", "8077")["needs_elevation"] is False
    assert O.classify_privilege("write_system_directory", "/etc/x")["needs_elevation"] is True
    with pytest.raises(PermissionError, match="elevated permission"):
        O.check_privilege_boundary("install_service")
    assert O.check_privilege_boundary("install_service", approved=True)["needs_elevation"] is True
    assert O.classify_privilege("install_service")["engine_stays_unprivileged"] is True


# (7) Filesystem Semantics Compatibility Checker
def test_filename_portability():
    result = O.check_filename_portability(["CON.txt", "a.txt", "A.txt",
                                           "bad:name.txt", "trailing. ", "fine.txt"])
    names = [f["name"] for f in result["findings"]]
    assert "CON.txt" in names and "bad:name.txt" in names
    assert any("collide" in issue for f in result["findings"] for issue in f["issues"] if "A.txt" == f["name"])
    assert result["portable"] is False
    assert O.check_filename_portability(["fine.txt"])["portable"] is True


# (8) Alternate Data Stream Inspector
def test_ads_inspector():
    parsed = O.parse_ads_listing("report.txt:hidden:$DATA\nreport.txt::$DATA\n")
    assert parsed == [{"file": "report.txt", "stream": "hidden"}]
    result = O.inspect_ads("/tmp/whatever.txt")
    assert result["status"] == "not_applicable"
    assert "NTFS" in result["reason"]


# (9) Runtime Environment Adapter Registry
def test_runtime_adapter_registry():
    assert O.declare_environment("no-such-shell")["refused"] is True
    missing = O.declare_environment("powershell", available={"powershell": False})
    assert missing["refused"] is True
    ok = O.declare_environment("bash", available={"bash": True})
    assert ok["available"] is True and ok["command"] == ["bash", "-c"]
    detected = O.detect_adapters()
    assert set(detected) == set(O.RUNTIME_ADAPTERS)


# (10) System Proxy Inheritance Auditor
def test_proxy_inheritance_auditor():
    with_proxy = O.audit_proxy_inheritance(env={"HTTPS_PROXY": "http://proxy.example:8080"})
    assert with_proxy["proxy_configured"] is True
    assert with_proxy["inherited_by_child_processes"] is True
    assert with_proxy["proxy"] is None  # value never echoed
    assert with_proxy["trust_env_disabled"] is True
    without = O.audit_proxy_inheritance(env={})
    assert without["proxy_configured"] is False
    assert without["routes_via_proxy"] is False


# (11) Native Process-Tree Controller
def test_process_tree_termination(tmp_path):
    script = tmp_path / "spawner.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n")
    tree = O.ProcessTree([sys.executable, str(script)])
    pid = tree.start()
    time.sleep(1.0)
    kids = O.children_of(pid)
    assert kids, "spawner should have a child process"
    result = tree.terminate(timeout=5.0)
    assert result["survivors"] == []
    for victim in [pid] + kids:
        with pytest.raises(ProcessLookupError):
            os.kill(victim, 0)


# (12) Native Application Launch Policy
def test_launch_policy():
    assert O.check_launch("run.sh")["decision"] == "refuse"
    assert O.check_launch("run.sh", approved=True)["decision"] == "allow"
    assert O.check_launch("notes.txt")["decision"] == "allow"
    assert O.check_launch("doc.pdf")["decision"] == "preview_only"
    assert O.check_launch("mystery.xyz")["decision"] == "preview_only"


# (13) Single-Instance Coordination
def test_single_instance_coordination(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert O.data_dirs_isolated(a, b) is True
    assert O.data_dirs_isolated(a, a) is False
    first = O.InstanceLock(a)
    assert first.acquire()["acquired"] is True
    second = O.InstanceLock(a)
    assert second.acquire()["acquired"] is False
    other_dir = O.InstanceLock(b)
    assert other_dir.acquire()["acquired"] is True
    first.release()
    other_dir.release()
    third = O.InstanceLock(a)
    assert third.acquire()["acquired"] is True
    third.release()


# (14) Session-Lock Response Policy
def test_session_lock_policy():
    assert O.policy_for_session_lock("sensitive", True)["action"] == "pause"
    assert O.policy_for_session_lock("preview", True)["action"] == "hide_preview"
    assert O.policy_for_session_lock("background", True)["action"] == "continue"
    assert O.policy_for_session_lock("sensitive", False)["action"] == "continue"
    detected = O.detect_session_locked()
    assert "locked" in detected and "known" in detected


# (15) Sleep and Resume Reconciliation
def test_sleep_resume_reconciliation():
    jump = O.detect_clock_jump(1000.0, 500.0, 4600.0, 510.0)
    assert jump["suspended_suspected"] is True and jump["gap_seconds"] == pytest.approx(3590.0)
    calm = O.detect_clock_jump(1000.0, 500.0, 1010.0, 510.0)
    assert calm["suspended_suspected"] is False
    now = time.time()
    bad = O.reconcile_after_resume(provider_check=lambda: False,
                                   permission_expiries={"tok": now - 10},
                                   deadlines={"job": now - 5},
                                   process_pids=[9999999])
    assert bad["safe_to_continue"] is False
    assert bad["expired_permissions"] == ["tok"]
    assert bad["elapsed_deadlines"] == ["job"]
    good = O.reconcile_after_resume(provider_check=lambda: True,
                                    permission_expiries={"tok": now + 3600},
                                    deadlines={"job": now + 3600},
                                    process_pids=[os.getpid()])
    assert good["safe_to_continue"] is True and good["uncertain_actions"] == []


# (16) Shutdown Interlock
def test_shutdown_interlock():
    done = []
    lock = O.ShutdownInterlock()
    lock.register("draft-save", checkpoint=lambda: done.append("draft-save"),
                  uncertain_if_interrupted="The draft may not have been sent.")
    report = lock.shutdown_report()
    assert report["checkpointed"] == ["draft-save"]
    assert any("sent" in u for u in report["uncertain_outcomes"])
    assert report["interruptible_operations"] == ["draft-save"]
    empty = O.ShutdownInterlock().shutdown_report()
    assert empty["uncertain_outcomes"] == []


# (17) Crash-Loop Safe Startup
def test_crash_loop_safe_startup(tmp_path):
    guard = O.CrashLoopGuard(tmp_path)
    assert guard.status()["recovery_mode"] is False
    guard.record_failure("worker")
    guard.record_failure("worker")
    status = guard.record_failure("worker")
    assert status["recovery_mode"] is True
    assert "automatic jobs" in status["disabled_in_recovery"]
    assert status["project_data_preserved"] is True
    assert status["suspect_component"] == "worker"
    guard.record_success()
    assert guard.status()["recovery_mode"] is False


# (18) OS Service Lifecycle Adapter
def test_service_lifecycle_adapter():
    unit = O.service_unit("agentqon", "/usr/local/bin/agentqon")
    assert "ExecStart=/usr/local/bin/agentqon" in unit
    assert "[Service]" in unit
    modes = O.service_lifecycle_modes()
    assert modes["foreground"]["start"] and modes["service"]["recovery"]


# (19) Filesystem Event Overflow Recovery — CONDITIONAL
def test_watcher_overflow_not_applicable():
    status = O.watcher_overflow_status()
    assert status["status"] == "not_applicable"
    assert "watcher" in status["reason"].lower()


# (20) Open-File Conflict Detector
def test_open_file_conflict_detector(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("line one\n")
    known = {"size": f.stat().st_size, "mtime": f.stat().st_mtime,
             "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    calm = O.detect_open_conflict(f, known)
    assert calm["conflict"] is False
    f.write_text("line one\nline two changed\n")
    clash = O.detect_open_conflict(f, known, old_text="line one\n")
    assert clash["conflict"] is True
    assert clash["externally_changed"] is True
    assert "line two changed" in (clash["comparison"] or "")
    # Detection never writes: content stays the outside version.
    assert f.read_text() == "line one\nline two changed\n"


# (21) Crash-Interrupted File Write Recovery
def test_crash_interrupted_write_recovery(tmp_path):
    (tmp_path / "report.txt").write_text("old\n")  # final exists case
    leftover_done = tmp_path / ".report.txt.aqtmp-111-222"
    leftover_done.write_text("new\n")
    orphan = tmp_path / ".notes.txt.aqtmp-333-444"
    orphan.write_text("orphan body\n")
    generic = tmp_path / "draft.tmp"
    generic.write_text("draft\n")
    real = tmp_path / "real.txt"
    real.write_text("real\n")
    found = {item["path"]: item for item in O.scan_temp_writes(tmp_path)}
    assert str(leftover_done) in found and str(orphan) in found
    assert str(generic) in found and str(real) not in found
    assert found[str(orphan)]["suggested_action"] == "recover"
    assert found[str(leftover_done)]["suggested_action"] == "cleanup"
    recovered = O.recover_temp_write(orphan, "recover")
    assert recovered["done"] is True
    assert (tmp_path / "notes.txt").read_text() == "orphan body\n"
    refused = O.recover_temp_write(leftover_done, "recover")
    assert refused["done"] is False  # final exists: never overwritten
    assert (tmp_path / "report.txt").read_text() == "old\n"
    cleaned = O.recover_temp_write(leftover_done, "cleanup")
    assert cleaned["done"] is True and not leftover_done.exists()
    with pytest.raises(ValueError):
        O.recover_temp_write(generic, "explode")


# (22) Local Database Integrity Preflight
def test_database_integrity_preflight(tmp_path):
    db = tmp_path / "store.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t VALUES (1, 'x')")
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()
    ok = O.sqlite_preflight(db, expected_user_version=3)
    assert ok["integrity"] == "ok" and ok["migration_compatible"] is True
    assert O.sqlite_preflight(db, expected_user_version=4)["migration_compatible"] is False
    corrupt = tmp_path / "bad.db"
    corrupt.write_bytes(b"this is not a database at all" * 10)
    bad = O.sqlite_preflight(corrupt)
    assert bad["integrity"] in ("corrupt", "error")
    before = hashlib.sha256(corrupt.read_bytes()).hexdigest()
    rec = O.recover_database(corrupt, tmp_path / "recovery")
    assert rec["original_untouched"] is True
    assert hashlib.sha256(corrupt.read_bytes()).hexdigest() == before
    assert (tmp_path / "recovery" / "bad.db.preserved").exists()
    good_rec = O.recover_database(db, tmp_path / "recovery2")
    assert good_rec["original_untouched"] is True
    assert good_rec["recovered_copy"] is not None


# (23) Machine-Specific State Portability Audit
def test_portability_audit(tmp_path):
    home = str(Path.home())
    result = O.audit_saved_references([
        {"name": "workspace", "kind": "path", "value": f"{home}/projects/x"},
        {"name": "python", "kind": "interpreter", "value": "/usr/bin/python3"},
        {"name": "gpu", "kind": "gpu", "value": "Some GPU 4090"},
        {"name": "label", "kind": "path", "value": "relative/notes"},
    ])
    assert result["portable"] is False
    assert len(result["findings"]) == 3
    assert all("suggestion" in f for f in result["findings"])


# (24) Crash Report Secret Filter
def test_crash_report_secret_filter(tmp_path):
    home = str(Path.home())
    bundle = {"error": "call failed", "api_key": "sk-abcdefgh12345678",
              "headers": {"Authorization": "Bearer abcdefgh12345678"},
              "prompt": "my secret prompt words",
              "path": f"{home}/projects/secret-place/file.txt",
              "plain": "nothing sensitive"}
    filtered = O.filter_crash_report(bundle)
    dumped = json.dumps(filtered["redacted"])
    assert "sk-abcdefgh" not in dumped
    assert "Bearer abcdefgh" not in dumped
    assert "my secret prompt words" not in dumped
    assert "secret-place" not in dumped
    assert filtered["redacted"]["plain"] == "nothing sensitive"
    cats = filtered["removed_categories"]
    assert "api_key" in cats and "prompt_content" in cats
    assert "authorization_header" in cats and "local_path" in cats
    preview = O.preview_crash_report(bundle)
    assert "api_key" in preview["removed_categories"]
    assert "sk-abcdefgh" not in json.dumps(preview)


# (25) Local Network Dependency Classifier
def test_network_dependency_classifier():
    assert O.classify_network_dependency("Write a local summary from these notes")["kind"] == "offline_capable"
    web = O.classify_network_dependency("Fetch url and search the web for prices")
    assert web["needs_network"] is True and web["kind"] == "internet"
    reg = O.classify_network_dependency("Set up the project", tools=["pip install requests"])
    assert reg["kind"] == "registry"
    lic = O.classify_network_dependency("Activate against the license server")
    assert lic["kind"] == "license_server"


# (26) Application Port Conflict Resolver
def test_port_conflict_resolver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    busy = sock.getsockname()[1]
    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(("127.0.0.1", 0))
    alt = free.getsockname()[1]
    free.close()
    result = O.resolve_port_conflict(busy, candidates=[alt])
    assert result["chose_alternate"] is True and result["port"] == alt
    assert result["previous_owner_pid"] in (None, os.getpid())
    direct = O.resolve_port_conflict(alt)
    assert direct["port"] == alt and direct["chose_alternate"] is False
    sock.close()


# (27) Local Service Readiness Probe
def test_service_readiness_probe():
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return calls["n"] >= 3

    ready = O.probe_ready(check, timeout_s=5.0, interval_s=0.05)
    assert ready["ready"] is True and ready["attempts"] == 3
    never = O.probe_ready(lambda: False, timeout_s=0.3, interval_s=0.05)
    assert never["ready"] is False

    class Flaky(BaseHTTPRequestHandler):
        mode = "flaky"

        def do_GET(self):
            code = 503 if Flaky.mode == "flaky" else 200
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Flaky)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        not_ready = O.probe_http_ready(f"http://127.0.0.1:{port}/", timeout_s=1.0)
        assert not_ready["ready"] is False  # listening but answering 503 is NOT ready
        Flaky.mode = "ok"
        ready_http = O.probe_http_ready(f"http://127.0.0.1:{port}/", timeout_s=5.0)
        assert ready_http["ready"] is True
    finally:
        server.shutdown()
        server.server_close()


# (28) Runtime Capability Snapshot
def test_runtime_capability_snapshot():
    snap = O.snapshot_capabilities(["python3", "git", "definitely-missing-tool-xyz"])
    assert snap["collected_personal_data"] == []
    assert snap["tools"]["definitely-missing-tool-xyz"] == "missing"
    assert len(snap["fingerprint"]) == 64
    dumped = json.dumps(snap)
    assert str(Path.home()) not in dumped
    changed = dict(snap)
    changed["tools"] = dict(snap["tools"])
    changed["tools"]["git"] = "changed-version"
    diff = O.diff_snapshots(snap, changed)
    assert "tool:git" in diff["changed"] and diff["same"] is False
    assert O.diff_snapshots(snap, snap)["same"] is True
