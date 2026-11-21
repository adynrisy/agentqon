"""Regression tests for the 2026-10-22 security audit repair pass.

Findings: hidden_files/audit-2026-10-22/AUDIT_SECURITY.md (F1–F14).
The auditor's live repros were the failing-first evidence; these
tests pin the FIXED behaviour permanently. Mode-aware findings are
exercised in both modes: hosted (accounts) refuses/confines, local
(dev) keeps the user's freedom — the product's purpose.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import agent_loop, agent_tools, connections, skills, telegram
from app import whatsapp
from app.agent_tools import classify_shell, tool_requires_approval
from app.config import Settings
from app.connections import (LOCAL_TARGET_HOSTED_CATEGORY,
                             UrlPolicyError, create_connection,
                             validate_connection)
from app.modeltest import ModelSelfTester
from app.netpolicy import validate_endpoint_url  # noqa: F401  (docs link)
from app.store import Store
from app.vault import Vault

TENANT = "org_sec"


# ============================================================ F1 + F2
# The shell classifier: read-only is a strict allowlist of commands
# AND argument shapes. env/printenv are out entirely; any argument
# that writes, deletes, or executes reclassifies the call as gated.

DANGEROUS_ARGVS = [
    # the auditor's live repros
    ["env"], ["printenv"], ["env", "cat", "/etc/passwd"],
    ["printenv", "PATH"],
    ["find", ".", "-delete"],
    ["find", ".", "-exec", "rm", "{}", ";"],
    ["find", ".", "-execdir", "rm", "{}", ";"],
    ["rg", "--pre", "cat", "pattern"],
    ["git", "diff", "--output=/tmp/pwned"],
    ["git", "branch", "pwned"],
    # fresh attack shapes (doubt-driven pass on the fix)
    ["env", "FOO=bar", "id"], ["env", "-i", "sh"],
    ["find", ".", "-ok", "rm", "{}", ";"],
    ["find", ".", "-okdir", "rm", "{}", ";"],
    ["find", ".", "-fprintf", "/tmp/x", "%p\n"],
    ["find", ".", "-fls", "/tmp/x"], ["find", ".", "-fprint", "/tmp/x"],
    ["find", ".", "-fprint0", "/tmp/x"],
    ["find", "/tmp", "-name", "x", "-delete"],
    ["rg", "--pre-glob", "*.py", "pattern"], ["rg", "--pre=cat", "p"],
    ["rg", "--hostname-bin", "evil", "pattern"],
    ["git", "diff", "--output", "/tmp/x"],
    ["git", "diff", "--ext-diff"], ["git", "show", "--textconv", "HEAD"],
    ["git", "branch", "-d", "old"], ["git", "branch", "-D", "old"],
    ["git", "branch", "-m", "a", "b"], ["git", "branch", "-c", "a", "b"],
    ["git", "branch", "--set-upstream-to=origin/main"],
    ["git", "branch", "--edit-description"],
    ["git", "branch", "-a", "sneaky-positional"],
    ["git", "checkout", "main"], ["git", "switch", "main"],
    ["git", "clone", "https://example.com/x.git"],
    ["git", "config", "user.email", "a@b.c"],
    ["hostname", "newname"], ["hostname", "-F", "/etc/hostname"],
    ["hostname", "--file", "/etc/hostname"],
    ["date", "-s", "2020-01-01"], ["date", "--set=2020-01-01"],
    ["date", "-us", "2020-01-01"],
    ["file", "-C", "-m", "magic"], ["file", "--compile"],
    ["file", "-Cm", "magic"],
    ["tree", "-o", "/tmp/out"], ["tree", "-ao", "/tmp/out"],
    ["dd", "if=/dev/zero", "of=/tmp/x"], ["sh", "-c", "ls"],
    ["bash", "-c", "ls"], ["python3", "-c", "pass"],
    ["curl", "https://example.com"], ["tee", "/tmp/x"],
    ["git", "remote", "-v"],  # the whole remote family is network-class
]

SAFE_ARGVS = [
    ["ls"], ["ls", "-la"], ["pwd"], ["whoami"], ["true"], ["echo", "hi"],
    ["cat", "README.md"], ["head", "-n", "5", "f.txt"],
    ["tail", "-f", "app.log"], ["wc", "-l", "f.txt"], ["stat", "f.txt"],
    ["which", "python3"], ["grep", "-rn", "pat", "."],
    ["grep", "-C", "2", "pat", "f.txt"], ["grep", "-i", "PAT", "f"],
    ["rg", "pattern"], ["rg", "--files"], ["rg", "-n", "pat", "src"],
    ["find", ".", "-name", "*.py"], ["find", ".", "-type", "f", "-print"],
    ["find", ".", "-type", "f", "-executable"],
    ["find", ".", "-maxdepth", "2", "-name", "*.md", "-print"],
    ["git", "status"], ["git", "status", "--short"],
    ["git", "log", "--oneline", "-5"], ["git", "diff"],
    ["git", "diff", "HEAD"], ["git", "diff", "--stat"],
    ["git", "diff", "--cached"], ["git", "show", "HEAD"],
    ["git", "show", "HEAD:file.py"], ["git", "rev-parse", "HEAD"],
    ["git", "ls-files"], ["git", "blame", "f.py"],
    ["git", "describe", "--tags"],
    ["git", "branch"], ["git", "branch", "-a"], ["git", "branch", "-r"],
    ["git", "branch", "--list"], ["git", "branch", "-v"],
    ["git", "branch", "-vv"], ["git", "branch", "--show-current"],
    ["git", "branch", "--all", "--verbose"],
    ["hostname"], ["hostname", "-f"], ["hostname", "--fqdn"],
    ["date"], ["date", "-u"], ["date", "-r", "123456"],
    ["date", "+%Y-%m-%d"],
    ["file", "x.bin"], ["file", "-c", "magic.mgc"],
    ["tree"], ["tree", "-a", "-L", "2"], ["tree", "--noreport"],
]


@pytest.mark.parametrize("argv", DANGEROUS_ARGVS)
def test_f1_f2_dangerous_argv_is_approval_gated(argv):
    # gated = never read_only, and the permission gate asks (with the
    # session's auto-approve off — the default posture)
    assert classify_shell(argv) != "read_only", argv
    needs, _reason = tool_requires_approval(
        "shell_run", {"argv": argv}, auto_approve_rw=False)
    assert needs is True, argv


@pytest.mark.parametrize("argv", SAFE_ARGVS)
def test_f1_f2_safe_argv_stays_read_only(argv):
    assert classify_shell(argv) == "read_only", argv
    needs, _reason = tool_requires_approval(
        "shell_run", {"argv": argv}, auto_approve_rw=False)
    assert needs is False, argv


# ============================================================ F3
# Hosted-mode shell confinement + session workspace_root validation.

def _shell(root: Path, argv, data_dir, confine):
    return asyncio.run(agent_tools.execute_tool(
        "shell_run", {"argv": argv}, root=root,
        data_dir=str(data_dir), confine_paths=confine))


def test_f3_confined_shell_refuses_paths_outside_workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    result = _shell(root, ["cat", "/etc/passwd"], tmp_path, True)
    assert result["ok"] is False
    assert result["refused"] == "workspace_escape"
    result = _shell(root, ["cat", "../secret.txt"], tmp_path, True)
    assert result["refused"] == "workspace_escape"
    result = _shell(root, ["grep", "--file=/etc/passwd", "x"],
                    tmp_path, True)
    assert result["refused"] == "workspace_escape"
    result = _shell(root, ["./../outside.sh"], tmp_path, True)
    assert result["refused"] == "workspace_escape"


def test_f3_confined_shell_allows_workspace_paths(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "note.txt").write_text("inside", encoding="utf-8")
    result = _shell(root, ["cat", "note.txt"], tmp_path, True)
    assert result["ok"] is True and "inside" in result["stdout"]
    result = _shell(root, ["cat", str(root / "note.txt")],
                    tmp_path, True)
    assert result["ok"] is True
    result = _shell(root, ["ls", "-la"], tmp_path, True)
    assert result["ok"] is True


def test_f3_unconfined_shell_keeps_local_freedom(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    result = _shell(root, ["cat", "/etc/hostname"], tmp_path, False)
    assert result["ok"] is True  # the user's own machine, their call


def _runner(tmp_path, auth_mode):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path, auth_mode=auth_mode)

    def factory(tenant_id, on_rate_limited=None):
        return None, "stub-model", None, {"id": "c", "kind": "direct"}

    return agent_loop.AgentRunner(store, settings, factory), settings


def test_f3_hosted_session_root_must_be_tenant_area(tmp_path):
    runner, settings = _runner(tmp_path, "accounts")
    with pytest.raises(ValueError):
        runner.create_session(TENANT, "u1", "goal",
                              workspace_root=str(tmp_path / "elsewhere"))
    inside = (settings.data_dir / "agent-workspaces" / TENANT / "s1")
    session = runner.create_session(TENANT, "u1", "goal",
                                    workspace_root=str(inside))
    assert session["workspace_root"] == str(inside.resolve())
    default = runner.create_session(TENANT, "u1", "goal")
    assert "agent-workspaces" in default["workspace_root"]


def test_f3_local_session_root_is_free(tmp_path):
    runner, _ = _runner(tmp_path, "dev")
    anywhere = tmp_path / "my" / "project"
    session = runner.create_session(TENANT, "u1", "goal",
                                    workspace_root=str(anywhere))
    assert session["workspace_root"] == str(anywhere.resolve())


# ============================================================ F4
# Session web_read honours the gateway's reach-channel enablement
# and produces the gateway-shaped audit view — no direct bypass.

def _fake_reach(monkeypatch, calls):
    def fake_execute(tool, args):
        calls.append(args["url"])
        return {"status": "ok", "url": args["url"], "title": "Example",
                "text": "page body", "chars": 9}
    monkeypatch.setattr("app.reach.execute", fake_execute)


def test_f4_web_read_denied_without_channel_and_audited(
        tmp_path, monkeypatch):
    calls: list = []
    audits: list = []
    _fake_reach(monkeypatch, calls)
    result = asyncio.run(agent_tools.execute_tool(
        "web_read", {"url": "https://example.com/x"}, root=tmp_path,
        reach_channels=set(),
        web_audit=lambda a, r: audits.append((a, r))))
    assert result["status"] == "not_configured"
    assert calls == []                      # reach never invoked
    assert len(audits) == 1                 # the denial itself audited
    assert audits[0][1]["status"] == "not_configured"


def test_f4_web_read_allowed_with_channel_and_audited(
        tmp_path, monkeypatch):
    calls: list = []
    audits: list = []
    _fake_reach(monkeypatch, calls)
    result = asyncio.run(agent_tools.execute_tool(
        "web_read", {"url": "https://example.com/x?d=1"}, root=tmp_path,
        reach_channels={"web"},
        web_audit=lambda a, r: audits.append((a, r))))
    assert result["ok"] is True
    assert result["text"] == "page body"
    assert calls == ["https://example.com/x?d=1"]
    args_view, result_view = audits[0]
    assert result_view["text"] == "page body"   # gateway audit view


# ============================================================ F5
# Approvals are single-use: concurrent approve calls execute the
# parked action exactly once; run() cannot double-drive.

_UNKNOWN_ENTRY = {"price_status": "unknown", "price_in": None,
                  "price_out": None}


class _ScriptedGateway:
    def __init__(self, script):
        self.script = list(script)

    async def chat_json(self, model, system, user, temperature=0.4):
        item = self.script.pop(0)
        return item, {"input_tokens": 10, "output_tokens": 5,
                      "model": model}


def _awaiting_session(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path)
    gateway = _ScriptedGateway([
        {"action": "tool", "tool": "fs_write",
         "args": {"path": "out.txt", "content": "once"},
         "thought": "write"},
        # the verify-after-build guard wants a check before "done":
        # a read-back of the written file (read-only, no approval)
        {"action": "tool", "tool": "shell_run",
         "args": {"argv": ["cat", "out.txt"]}, "thought": "verify"},
        {"action": "done", "summary": "written"},
    ])

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model", _UNKNOWN_ENTRY, \
            {"id": "c", "kind": "direct"}

    runner = agent_loop.AgentRunner(store, settings, factory)
    root = tmp_path / "ws"
    session = runner.create_session(TENANT, "u1", "write a file",
                                    workspace_root=str(root))
    parked = asyncio.run(runner.run(TENANT, session["id"]))
    assert parked["status"] == "awaiting_approval"
    return runner, store, session, root


def test_f5_concurrent_approvals_execute_exactly_once(
        tmp_path, monkeypatch):
    runner, store, session, root = _awaiting_session(tmp_path, monkeypatch)
    executions: list = []
    original = agent_tools.execute_tool

    async def slow_execute(tool, *args, **kwargs):
        if tool == "fs_write":     # the parked action, specifically
            executions.append(1)
            await asyncio.sleep(0.3)   # the auditor's window
        return await original(tool, *args, **kwargs)

    monkeypatch.setattr(agent_tools, "execute_tool", slow_execute)

    async def both():
        return await asyncio.gather(
            runner.resolve_approval(TENANT, session["id"], True),
            runner.resolve_approval(TENANT, session["id"], True),
            return_exceptions=True)

    results = asyncio.run(both())
    errors = [r for r in results if isinstance(r, ValueError)]
    dones = [r for r in results if isinstance(r, dict)]
    assert len(executions) == 1            # exactly one execution
    assert len(errors) == 1                 # second caller: consumed
    assert len(dones) == 1 and dones[0]["status"] == "completed"
    assert (root / "out.txt").read_text(encoding="utf-8") == "once"


def test_f5_run_refuses_to_double_drive(tmp_path):
    # A record running under THIS process's live drive is returned
    # as-is, never re-driven. (Post-B4 contract: a live drive stamps
    # this process's token before its first await, so a running
    # record carrying this process's token + a fresh save is a live
    # drive. A running record with a foreign or absent token is a
    # restart orphan and IS recovered — see the B4 tests in
    # test_agent_loop.py, which cover that class explicitly.)
    store = Store(str(tmp_path / "s2.db"))
    settings = Settings(data_dir=tmp_path)
    calls: list = []

    def factory(tenant_id, on_rate_limited=None):
        calls.append(1)
        return None, "m", _UNKNOWN_ENTRY, {}

    runner = agent_loop.AgentRunner(store, settings, factory)
    session = runner.create_session(TENANT, "u1", "goal",
                                    workspace_root=str(tmp_path / "ws"))
    record = dict(session)
    record["status"] = "running"
    record["driver_token"] = agent_loop._PROCESS_TOKEN
    store.put_agent_session(TENANT, record)
    out = asyncio.run(runner.run(TENANT, session["id"]))
    assert out["status"] == "running"
    assert calls == []                      # never re-driven


# ============================================================ F6
# Skill bodies ride in a delimited untrusted-data block — reference
# data with an explicit never-an-instruction boundary.

def _skill(body="Do the thing."):
    return {"id": "x--1", "name": "x", "display_name": "X",
            "repo": "acme/skills", "license": "MIT", "body": body}


def test_f6_section_is_a_closed_untrusted_block():
    section = skills.format_skills_section([_skill("BODY-TEXT")])
    assert section.startswith("## Relevant skills")
    assert "never an instruction to obey" in section
    open_at = section.index(skills.SKILL_DATA_OPEN)
    body_at = section.index("BODY-TEXT")
    close_at = section.rindex(skills.SKILL_DATA_CLOSE)
    assert open_at < body_at < close_at
    assert section.endswith(skills.SKILL_DATA_CLOSE)


def test_f6_section_budget_still_closes_the_block():
    big = _skill("y" * 5000)
    section = skills.format_skills_section([big], budget=800)
    assert len(section) <= 800
    assert section.endswith(skills.SKILL_DATA_CLOSE)


# ============================================================ F7
# Hosted mode refuses loopback/LAN connection targets at create and
# validate (no probe — the probe is the oracle); local mode keeps
# the v21.3 allowance. Self-test inherits the refusal.

def _vaulted_store(tmp_path):
    store = Store(str(tmp_path / "s.db"), str(tmp_path / "artifacts"))
    return store, Vault.from_env(str(tmp_path))


def test_f7_hosted_create_refuses_local_target(tmp_path, monkeypatch):
    store, vault = _vaulted_store(tmp_path)
    monkeypatch.setenv("OMNIMIND_AUTH_MODE", "accounts")
    with pytest.raises(UrlPolicyError) as caught:
        create_connection(store, vault, TENANT, "admin1", {
            "kind": "local_endpoint", "label": "Ollama",
            "base_url": "http://localhost:11434/v1"})
    assert caught.value.category == LOCAL_TARGET_HOSTED_CATEGORY
    # a public provider is still fine in hosted mode
    conn = create_connection(store, vault, TENANT, "admin1", {
        "kind": "direct_provider", "label": "Hosted",
        "base_url": "https://api.example.com/v1", "api_key": "sk-x"})
    assert conn["state"] == "draft"


def test_f7_local_mode_keeps_the_v213_allowance(tmp_path, monkeypatch):
    store, vault = _vaulted_store(tmp_path)
    monkeypatch.delenv("OMNIMIND_AUTH_MODE", raising=False)
    conn = create_connection(store, vault, TENANT, "u1", {
        "kind": "local_endpoint", "label": "Ollama",
        "base_url": "http://localhost:11434/v1"})
    assert conn["state"] == "draft"


def test_f7_hosted_validate_never_probes_a_local_target(
        tmp_path, monkeypatch):
    store, vault = _vaulted_store(tmp_path)
    monkeypatch.delenv("OMNIMIND_AUTH_MODE", raising=False)
    conn = create_connection(store, vault, TENANT, "u1", {
        "kind": "local_endpoint", "label": "Ollama",
        "base_url": "http://127.0.0.1:9/v1"})
    monkeypatch.setenv("OMNIMIND_AUTH_MODE", "accounts")

    class _ExplodingClient:
        def __init__(self, *a, **k):
            raise AssertionError("a probe was attempted")

    monkeypatch.setattr(connections, "GatewayClient", _ExplodingClient)
    view = asyncio.run(validate_connection(
        store, vault, TENANT, "u1", conn["id"]))
    assert view["state"] == "degraded"
    assert view["last_error_category"] == LOCAL_TARGET_HOSTED_CATEGORY


def test_f7_self_test_skips_local_connections_in_hosted_mode(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    store.put_connection(TENANT, {
        "id": "conn_local", "tenant_id": TENANT, "owner_user_id": "t",
        "kind": "local_endpoint", "provider_key": "local_endpoint",
        "label": "Ollama", "base_url": "http://127.0.0.1:11434/v1",
        "state": "active", "masked_key": "", "allowed_models": ["m1"],
        "blocked_models": [], "budget_limit_usd": None,
        "routing_mode": "balanced", "data_policy": {},
        "last_validated_at": None, "last_error_category": None,
        "models_count": 1, "created_at": "2026-10-22T00:00:00Z",
        "updated_at": "2026-10-22T00:00:00Z", "deleted_at": None})

    class _Orch:
        def _platform_connection(self, base_url=None, model=None):
            return None, None

    hosted = ModelSelfTester(
        store, Settings(data_dir=tmp_path, auth_mode="accounts"),
        _Orch(), client_factory=lambda c, t: None)
    assert hosted._targets(TENANT) == []
    local = ModelSelfTester(
        store, Settings(data_dir=tmp_path, auth_mode="dev"),
        _Orch(), client_factory=lambda c, t: None)
    assert [t.connection_id for t in local._targets(TENANT)] == [
        "conn_local"]


# ============================================================ F8
# Channel links confer the LIVE membership role, not the snapshot.

def _link(role="admin"):
    return {"tenant_id": TENANT, "user_id": "u1", "role": role,
            "chat_id": "42"}


def test_f8_telegram_role_is_rechecked_in_accounts_mode(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    cfg = Settings(data_dir=tmp_path, auth_mode="accounts")
    link = _link("admin")                    # snapshot from link time
    store.upsert_membership(TENANT, "u1", "admin")
    assert telegram._can_act_now(store, link, cfg, "admin") is True
    store.upsert_membership(TENANT, "u1", "viewer")   # demoted
    assert telegram._effective_role(store, link, cfg) == "viewer"
    assert telegram._can_act_now(store, link, cfg, "member") is False
    store.remove_membership(TENANT, "u1")             # removed
    assert telegram._effective_role(store, link, cfg) is None
    assert telegram._can_act_now(store, link, cfg, "member") is False


def test_f8_whatsapp_role_is_rechecked_in_accounts_mode(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    cfg = Settings(data_dir=tmp_path, auth_mode="accounts")
    link = {"tenant_id": TENANT, "user_id": "u9", "role": "admin",
            "wa_id": "15551234567"}
    assert whatsapp._can_act_now(store, link, cfg, "member") is False
    store.upsert_membership(TENANT, "u9", "member")
    assert whatsapp._can_act_now(store, link, cfg, "member") is True


def test_f8_local_mode_keeps_the_snapshot(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    cfg = Settings(data_dir=tmp_path, auth_mode="dev")
    # no membership exists in the local product — the link is the
    # user's own and its snapshot stands
    assert telegram._can_act_now(store, _link("member"), cfg) is True


# ============================================================ F10
# fs_write/fs_edit are swap-safe: a symlink swapped into the path
# cannot redirect a write outside the workspace.

def _write(root: Path, rel: str, content: str):
    return asyncio.run(agent_tools.execute_tool(
        "fs_write", {"path": rel, "content": content}, root=root))


def test_f10_write_through_symlink_pointing_outside_is_refused(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    (root / "link.txt").symlink_to(victim)
    result = _write(root, "link.txt", "pwned")
    assert result["ok"] is False
    assert result["refused"] == "workspace_escape"
    assert victim.read_text(encoding="utf-8") == "original"


def test_f10_write_through_symlink_staying_inside_is_followed(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("old", encoding="utf-8")
    (root / "alias.txt").symlink_to(real)
    result = _write(root, "alias.txt", "new")
    assert result["ok"] is True
    assert real.read_text(encoding="utf-8") == "new"


def test_f10_write_under_symlinked_parent_pointing_outside(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sub").symlink_to(outside, target_is_directory=True)
    result = _write(root, "sub/x.txt", "pwned")
    assert result["ok"] is False
    assert not (outside / "x.txt").exists()


def test_f10_plain_nested_write_still_works(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    result = _write(root, "a/b/c.txt", "deep")
    assert result["ok"] is True
    assert (root / "a" / "b" / "c.txt").read_text(
        encoding="utf-8") == "deep"


# ============================================================ F11
# code.* tools: hosted mode confines the indexed workspace to the
# platform data area; local mode keeps the user's own projects.

def test_f11_hosted_code_tools_refuse_outside_workspace(
        tmp_path, monkeypatch):
    from app.gateway import ToolGateway
    store = Store(str(tmp_path / "s.db"), str(tmp_path / "artifacts"))
    gateway = ToolGateway(store)
    # inside the platform data area (the store's parent dir): allowed
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    # outside it: an arbitrary server directory
    elsewhere = Path("/tmp") / "aq-f11-outside"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "b.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setenv("OMNIMIND_AUTH_MODE", "accounts")
    result = gateway._execute_code(
        "code.search", {"workspace": str(elsewhere), "query": "y"})
    assert result["status"] == "denied"
    result = gateway._execute_code(
        "code.search", {"workspace": str(project), "query": "x"})
    assert result["status"] != "denied"
    monkeypatch.delenv("OMNIMIND_AUTH_MODE")
    result = gateway._execute_code(
        "code.search", {"workspace": str(elsewhere), "query": "y"})
    assert result["status"] != "denied"     # local freedom restored
