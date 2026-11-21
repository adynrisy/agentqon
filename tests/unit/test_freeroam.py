"""Free-roam (local mode): the harness roams the machine like the
terminal agent CLIs, under the owner's Write Law.

Covers: launch-folder adoption as the home project (and its
fallbacks); roam reads anywhere EXCEPT AgentQon's own files (data
dir + install tree, symlink hops included, the session's own
workspace excepted); writes inside the home project unchanged;
outside writes gated per exact file through the existing approval
machinery (park → grant on approve → denial leaves the file
untouched), the gate outranking auto-approve; own-files writes
refused even with a would-be grant; and hosted (accounts) mode
proving the jailed posture is byte-for-byte the old one — no roam,
no grant path.
"""
import asyncio
from pathlib import Path

from app import agent_loop, agent_tools
from app.config import Settings
from app.store import Store

TENANT = "org_roam_test"

UNKNOWN_ENTRY = {"price_status": "unknown", "price_in": None,
                 "price_out": None}


class StubGateway:
    """Scripted chat_json, the same shape test_agent_loop uses."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def chat_json(self, model, system, user, temperature=0.4):
        self.prompts.append(user)
        self.systems.append(system)
        if not self.script:
            raise AssertionError("stub model script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(user)
        return item, {"input_tokens": 100, "output_tokens": 50,
                      "model": model}


def _runner(tmp_path, script, *, auth_mode="dev", launch_dir=None):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path / "data", auth_mode=auth_mode,
                        launch_dir=launch_dir)
    gateway = StubGateway(script)

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model-1", UNKNOWN_ENTRY, \
            {"id": "conn_test", "kind": "direct_provider"}

    return agent_loop.AgentRunner(store, settings, factory), store, gateway


def _tool(tool, args, thought="step"):
    return {"action": "tool", "tool": tool, "args": args,
            "thought": thought}


def _done(summary="finished"):
    return {"action": "done", "summary": summary}


def _run(runner, tenant, session_id):
    return asyncio.run(runner.run(tenant, session_id))


def _events(store, session_id):
    return store.list_events(TENANT, session_id)


def _exec(tool, args, *, root, policy=None):
    return asyncio.run(agent_tools.execute_tool(
        tool, args, root=root, policy=policy))


def _roam_policy(tmp_path, granted=()):
    return agent_tools.PathPolicy(
        roam=True, data_dir=tmp_path / "data",
        install_dir=tmp_path / "install",
        granted_files=frozenset(Path(p).resolve() for p in granted))


def _layout(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "install").mkdir(exist_ok=True)
    return proj, elsewhere


# ------------------------------------------------------------- roam reads
def test_roam_read_absolute_path_outside_workspace(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "note.txt").write_text("roaming read")
    result = _exec("fs_read", {"path": str(elsewhere / "note.txt")},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is True
    assert "roaming read" in result["content"]
    assert result["path"] == str((elsewhere / "note.txt").resolve())


def test_roam_read_relative_dotdot_also_roams(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "note.txt").write_text("via dotdot")
    result = _exec("fs_read", {"path": "../elsewhere/note.txt"},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is True
    assert "via dotdot" in result["content"]


def test_roam_read_into_data_dir_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    (tmp_path / "data" / "vaultish.txt").write_text("agentqon secret")
    result = _exec("fs_read", {"path": str(tmp_path / "data"
                                           / "vaultish.txt")},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert "agentqon secret" not in str(result)


def test_roam_read_into_install_dir_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    (tmp_path / "install" / "code.py").write_text("payload source")
    result = _exec("fs_read", {"path": str(tmp_path / "install"
                                           / "code.py")},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert "payload source" not in str(result)


def test_roam_read_symlink_into_data_dir_refused(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (tmp_path / "data" / "secret.txt").write_text("do not leak")
    (elsewhere / "link.txt").symlink_to(tmp_path / "data" / "secret.txt")
    result = _exec("fs_read", {"path": str(elsewhere / "link.txt")},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert "do not leak" not in str(result)
    # A directory symlink into the data dir is refused for listing too.
    (elsewhere / "linkdir").symlink_to(tmp_path / "data",
                                       target_is_directory=True)
    result = _exec("fs_list", {"path": str(elsewhere / "linkdir")},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"


def test_roam_read_own_workspace_under_data_dir_allowed(tmp_path):
    # The local default workspace legitimately lives under the data
    # dir — the own-files refusal must not eat the home project.
    ws = tmp_path / "data" / "agent-workspaces" / "ags_x"
    ws.mkdir(parents=True)
    (ws / "mine.txt").write_text("my own work")
    result = _exec("fs_read", {"path": "mine.txt"}, root=ws,
                   policy=_roam_policy(tmp_path))
    assert result["ok"] is True
    assert "my own work" in result["content"]


def test_roam_search_skips_own_files(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "open.txt").write_text("needle in the open")
    (tmp_path / "data" / "hidden.txt").write_text("needle in the vault")
    # A symlink inside the searched tree pointing at the data file:
    # the search walks past it but must never read through it.
    (elsewhere / "sneak.txt").symlink_to(tmp_path / "data" / "hidden.txt")
    result = _exec("fs_search", {"query": "needle",
                                 "path": str(elsewhere)},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is True
    paths = [m["path"] for m in result["matches"]]
    assert any("open.txt" in p for p in paths)
    assert not any("hidden.txt" in p or "sneak.txt" in p for p in paths)


# ------------------------------------------------------------ roam writes
def test_write_inside_home_project_unaffected(tmp_path):
    proj, _ = _layout(tmp_path)
    policy = _roam_policy(tmp_path)
    written = _exec("fs_write", {"path": "a/b.txt", "content": "one"},
                    root=proj, policy=policy)
    assert written["ok"] is True
    edited = _exec("fs_edit", {"path": "a/b.txt", "old": "one",
                               "new": "two"}, root=proj, policy=policy)
    assert edited["ok"] is True
    assert (proj / "a" / "b.txt").read_text() == "two"
    # An absolute path that lands inside the home project is a home
    # write too — no grant involved.
    written = _exec("fs_write", {"path": str(proj / "abs.txt"),
                                 "content": "three"},
                    root=proj, policy=policy)
    assert written["ok"] is True


def test_outside_write_without_grant_refused_at_execution(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "report.txt"
    result = _exec("fs_write", {"path": str(target), "content": "x"},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert "has not given permission" in result["error"]
    assert not target.exists()


def test_outside_write_with_grant_succeeds_for_that_file_only(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    granted_file = elsewhere / "granted.txt"
    other_file = elsewhere / "other.txt"
    policy = _roam_policy(tmp_path, granted=[granted_file])
    result = _exec("fs_write", {"path": str(granted_file),
                                "content": "approved"},
                   root=proj, policy=policy)
    assert result["ok"] is True
    assert granted_file.read_text() == "approved"
    # The grant names ONE file: its neighbour is still gated.
    result = _exec("fs_write", {"path": str(other_file),
                                "content": "sneak"},
                   root=proj, policy=policy)
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert not other_file.exists()


def test_write_into_install_dir_refused_even_with_grant(tmp_path):
    proj, _ = _layout(tmp_path)
    target = tmp_path / "install" / "code2.py"
    policy = _roam_policy(tmp_path, granted=[target])  # a would-be grant
    result = _exec("fs_write", {"path": str(target), "content": "x"},
                   root=proj, policy=policy)
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert "never modifies its own files" in result["error"]
    assert not target.exists()


def test_write_into_data_dir_refused_even_with_grant(tmp_path):
    proj, _ = _layout(tmp_path)
    target = tmp_path / "data" / "settings.json"
    policy = _roam_policy(tmp_path, granted=[target])
    result = _exec("fs_write", {"path": str(target), "content": "x"},
                   root=proj, policy=policy)
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert not target.exists()


def test_jailed_policy_still_refuses_everything_outside(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "note.txt").write_text("jailed")
    jailed = agent_tools.PathPolicy(roam=False)
    result = _exec("fs_read", {"path": str(elsewhere / "note.txt")},
                   root=proj, policy=jailed)
    assert result["ok"] is False
    assert result.get("refused") == "workspace_escape"
    result = _exec("fs_write", {"path": str(elsewhere / "w.txt"),
                                "content": "x"},
                   root=proj, policy=jailed)
    assert result["ok"] is False
    assert result.get("refused") == "workspace_escape"


# --------------------------------------------------------- approval gate
def test_approval_gate_classifies_outside_writes(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "report.txt"
    policy = _roam_policy(tmp_path)
    args = {"path": str(target), "content": "x"}
    # Ungranted outside write: parks, naming the exact file — even in
    # auto-approve mode (the Write Law outranks it).
    needs, reason = agent_tools.tool_requires_approval(
        "fs_write", args, False, root=proj, policy=policy)
    assert needs is True
    assert reason == f"write outside your project: {target.resolve()}"
    needs, _ = agent_tools.tool_requires_approval(
        "fs_write", args, True, root=proj, policy=policy)
    assert needs is True
    # Granted outside write: the grant IS the permission — no park.
    granted_policy = _roam_policy(tmp_path, granted=[target])
    needs, _ = agent_tools.tool_requires_approval(
        "fs_write", args, False, root=proj, policy=granted_policy)
    assert needs is False
    # AgentQon's own files: never park (no approval can allow them).
    needs, _ = agent_tools.tool_requires_approval(
        "fs_write", {"path": str(tmp_path / "install" / "x.py"),
                     "content": "x"}, False, root=proj, policy=policy)
    assert needs is False
    # Inside the home project the gate is exactly the old one.
    needs, reason = agent_tools.tool_requires_approval(
        "fs_write", {"path": "in.txt", "content": "x"}, False,
        root=proj, policy=policy)
    assert needs is True and "modifies files in the workspace" in reason
    needs, _ = agent_tools.tool_requires_approval(
        "fs_write", {"path": "in.txt", "content": "x"}, True,
        root=proj, policy=policy)
    assert needs is False


# ------------------------------------------------------------ grant flow
def test_outside_write_parks_and_denial_leaves_file_untouched(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "report.txt"
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": str(target), "content": "draft"}),
        _done("finished without changing it"),
    ])
    session = runner.create_session(TENANT, "tester", "write a report",
                                    workspace_root=str(proj),
                                    auto_approve_rw=True)
    parked = _run(runner, TENANT, session["id"])
    # Even with auto-approve on, the outside write parks — and the
    # ask names the exact file.
    assert parked["status"] == "awaiting_approval"
    assert parked["pending_action"]["grant_path"] == str(target.resolve())
    assert str(target.resolve()) in parked["pending_action"]["reason"]
    assert not target.exists()

    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=False))
    assert done["status"] == "completed"
    assert not target.exists(), "a denied outside write must never run"
    assert done["write_grants"] == []
    observations = [t for t in done["transcript"]
                    if t["role"] == "observation"]
    assert any("you do not have permission to modify" in t["text"]
               and str(target.resolve()) in t["text"]
               for t in observations)


def test_outside_write_grant_flow_grants_one_file_at_a_time(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    first = elsewhere / "first.txt"
    second = elsewhere / "second.txt"
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": str(first), "content": "one"}),
        _tool("fs_write", {"path": str(second), "content": "two"}),
        _tool("fs_read", {"path": str(second)}),
        _done("both written with permission"),
    ])
    session = runner.create_session(TENANT, "tester", "write two files",
                                    workspace_root=str(proj),
                                    auto_approve_rw=True)
    parked = _run(runner, TENANT, session["id"])
    assert parked["status"] == "awaiting_approval"
    assert parked["pending_action"]["grant_path"] == str(first.resolve())

    # Approving the first file executes it — and the SECOND outside
    # file parks again: a grant covers one exact file, not a folder.
    parked2 = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                                  approve=True))
    assert first.read_text() == "one"
    assert parked2["status"] == "awaiting_approval"
    assert parked2["pending_action"]["grant_path"] == str(second.resolve())
    assert not second.exists()

    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=True))
    assert done["status"] == "completed"
    assert second.read_text() == "two"
    assert done["write_grants"] == [str(first.resolve()),
                                    str(second.resolve())]
    granted_events = [e for e in _events(store, session["id"])
                      if e["event_type"] == "OutsideWriteGranted"]
    assert [e["payload"]["path"] for e in granted_events] == \
        [str(first.resolve()), str(second.resolve())]


# ------------------------------------------------------ hosted stays jailed
def _accounts_workspace(tmp_path):
    ws = (tmp_path / "data" / "agent-workspaces" / TENANT / "ws")
    ws.mkdir(parents=True)
    return ws


def test_accounts_mode_read_outside_workspace_still_refused(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "note.txt").write_text("hosted secret")
    ws = _accounts_workspace(tmp_path)
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_read", {"path": str(elsewhere / "note.txt")}),
        _done("could not read it"),
        _done("could not read it: it is outside the workspace"),
        _done("could not read it: it is outside the workspace"),
    ], auth_mode="accounts")
    session = runner.create_session(TENANT, "tester", "read a file",
                                    workspace_root=str(ws))
    assert runner._path_policy(session).roam is False
    done = _run(runner, TENANT, session["id"])
    results = [e for e in _events(store, session["id"])
               if e["event_type"] == "ToolResult"]
    assert results and results[0]["payload"]["ok"] is False
    assert "escapes the session workspace" in results[0]["payload"]["summary"]
    transcript = " ".join(t.get("text", "") for t in done["transcript"])
    assert "hosted secret" not in transcript


def test_accounts_mode_write_outside_still_refused_no_grants(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "must-not-exist.txt"
    ws = _accounts_workspace(tmp_path)
    runner, store, _ = _runner(tmp_path, [
        _tool("fs_write", {"path": str(target), "content": "x"}),
        _done("could not write it"),
        _done("could not write it: it is outside the workspace"),
        _done("could not write it: it is outside the workspace"),
    ], auth_mode="accounts")
    session = runner.create_session(TENANT, "tester", "write a file",
                                    workspace_root=str(ws),
                                    auto_approve_rw=True)
    done = _run(runner, TENANT, session["id"])
    assert not target.exists()
    # No grant path exists in hosted mode: nothing parked, no grants
    # recorded, the write was refused as a plain workspace escape.
    assert done["write_grants"] == []
    assert "ApprovalNeeded" not in \
        [e["event_type"] for e in _events(store, session["id"])]
    results = [e for e in _events(store, session["id"])
               if e["event_type"] == "ToolResult"]
    assert results and results[0]["payload"]["ok"] is False
    assert "escapes the session workspace" in results[0]["payload"]["summary"]


# ------------------------------------------------------- launch-folder home
def test_launch_folder_adopted_as_home_project(tmp_path):
    proj, _ = _layout(tmp_path)
    runner, store, gateway = _runner(tmp_path, [_done("nothing to do")],
                                     launch_dir=proj)
    session = runner.create_session(TENANT, "tester", "look around")
    assert session["workspace_root"] == str(proj.resolve())
    done = _run(runner, TENANT, session["id"])
    assert done["status"] == "completed"
    system = gateway.systems[0]
    assert "Home project" in system
    assert str(proj.resolve()) in system
    created = [e for e in _events(store, session["id"])
               if e["event_type"] == "AgentSessionCreated"]
    assert created[0]["payload"]["home_project"] == str(proj.resolve())


def test_explicit_workspace_wins_over_launch_folder(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    named = elsewhere / "named-project"
    runner, _, _ = _runner(tmp_path, [], launch_dir=proj)
    session = runner.create_session(TENANT, "tester", "goal",
                                    workspace_root=str(named))
    assert session["workspace_root"] == str(named.resolve())


def test_launch_folder_inside_install_tree_falls_back(tmp_path):
    app_dir = Path(agent_tools.__file__).resolve().parent
    runner, _, _ = _runner(tmp_path, [], launch_dir=app_dir)
    session = runner.create_session(TENANT, "tester", "goal")
    assert "agent-workspaces" in session["workspace_root"]
    assert session["workspace_root"].startswith(
        str((tmp_path / "data").resolve()))


def test_launch_folder_inside_data_dir_falls_back(tmp_path):
    runner, _, _ = _runner(tmp_path, [],
                           launch_dir=tmp_path / "data" / "sub")
    session = runner.create_session(TENANT, "tester", "goal")
    assert "agent-workspaces" in session["workspace_root"]


def test_launch_folder_missing_falls_back(tmp_path):
    runner, _, _ = _runner(tmp_path, [],
                           launch_dir=tmp_path / "no-such-folder")
    session = runner.create_session(TENANT, "tester", "goal")
    assert "agent-workspaces" in session["workspace_root"]


def test_accounts_mode_never_adopts_launch_folder(tmp_path):
    proj, _ = _layout(tmp_path)
    runner, _, _ = _runner(tmp_path, [], auth_mode="accounts",
                           launch_dir=proj)
    session = runner.create_session(TENANT, "tester", "goal")
    assert session["workspace_root"] != str(proj.resolve())
    assert "agent-workspaces" in session["workspace_root"]


# ------------------------------------------------------------ prompt + facts
def test_roam_law_in_local_prompt_absent_in_accounts(tmp_path):
    proj, _ = _layout(tmp_path)
    runner, _, gateway = _runner(tmp_path, [_done("ok")])
    session = runner.create_session(TENANT, "tester", "look",
                                    workspace_root=str(proj))
    _run(runner, TENANT, session["id"])
    system = gateway.systems[0]
    assert system.startswith(agent_loop.AGENT_SYSTEM_PROMPT)
    assert "Free-roam is ON" in system
    assert "Home project" in system
    assert "Never delete a file" in system

    ws = _accounts_workspace(tmp_path)
    runner2, _, gateway2 = _runner(tmp_path, [_done("ok")],
                                   auth_mode="accounts")
    session2 = runner2.create_session(TENANT, "tester", "look",
                                      workspace_root=str(ws))
    _run(runner2, TENANT, session2["id"])
    system2 = gateway2.systems[0]
    assert "Free-roam" not in system2
    assert "Home project" not in system2
