"""Gap-fix W8 (file operations): typed copy/move/delete/mkdir/stat,
atomic writes, backups, diff preview, create-vs-overwrite, ranged
reads, binary handling, truncation flags, locking, permissions,
write-size cap, append, the shell residual, case/long paths, and
existence semantics — each requirement verified by a named test.
"""
import asyncio
import os
import stat
import sys
import threading
from pathlib import Path

import fcntl
import pytest

from app import agent_loop, agent_tools
from app.config import Settings
from app.store import Store

TENANT = "org_gap_fileops_test"
UNKNOWN_ENTRY = {"price_status": "unknown", "price_in": None,
                 "price_out": None}


def _exec(tool, args, *, root, policy=None, approved_paths=None):
    return asyncio.run(agent_tools.execute_tool(
        tool, args, root=root, policy=policy,
        approved_paths=approved_paths))


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


def _no_temps(root):
    return [p for p in Path(root).rglob("*") if ".aqtmp" in p.name]


# ------------------------------------------------------------ R1: typed tools
def test_copy_inside_ok(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("copy me")
    result = _exec("fs_copy", {"path": "a.txt", "destination": "b.txt"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "b.txt").read_text() == "copy me"
    assert result["created"] is True
    assert _no_temps(proj) == []


def test_copy_outside_ungranted_refused_at_execution(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (proj / "a.txt").write_text("stay")
    dest = elsewhere / "b.txt"
    result = _exec("fs_copy", {"path": "a.txt",
                               "destination": str(dest)},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert not dest.exists()


def test_copy_granted_ok(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (proj / "a.txt").write_text("granted copy")
    dest = elsewhere / "b.txt"
    result = _exec("fs_copy", {"path": "a.txt",
                               "destination": str(dest)},
                   root=proj,
                   policy=_roam_policy(tmp_path, granted=[dest]))
    assert result["ok"] is True
    assert dest.read_text() == "granted copy"


def test_copy_into_own_files_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("nope")
    target = tmp_path / "install" / "code.py"
    result = _exec("fs_copy", {"path": "a.txt",
                               "destination": str(target)},
                   root=proj,
                   policy=_roam_policy(tmp_path, granted=[target]))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert not target.exists()


def test_copy_symlink_source_copies_target_content(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "real.txt").write_text("real content")
    (proj / "link.txt").symlink_to(elsewhere / "real.txt")
    # Roam reads may follow the link out (jailed reads correctly
    # refuse it — see test_write_through_symlink_pointing_outside).
    result = _exec("fs_copy", {"path": "link.txt",
                               "destination": "copied.txt"},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is True
    assert (proj / "copied.txt").read_text() == "real content"
    assert not (proj / "copied.txt").is_symlink()


def test_move_preserves_mode_and_removes_source(tmp_path):
    proj, _ = _layout(tmp_path)
    src = proj / "a.sh"
    src.write_text("echo hi")
    os.chmod(src, 0o751)
    result = _exec("fs_move", {"path": "a.sh", "destination": "b.sh"},
                   root=proj)
    assert result["ok"] is True
    assert result["source_removed"] is True
    assert not src.exists()
    assert (proj / "b.sh").read_text() == "echo hi"
    assert stat.S_IMODE((proj / "b.sh").stat().st_mode) == 0o751


def test_rename_is_move_in_same_dir(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "old.txt").write_text("renamed")
    result = _exec("fs_rename", {"path": "old.txt",
                                 "destination": "new.txt"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "new.txt").read_text() == "renamed"
    assert not (proj / "old.txt").exists()


def test_move_partial_failure_reported_plainly(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("partial")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated unlink failure")

    monkeypatch.setattr(agent_tools, "_delete_file", boom)
    result = _exec("fs_move", {"path": "a.txt", "destination": "b.txt"},
                   root=proj)
    assert result["ok"] is False
    assert result["destination_written"] is True
    assert result["source_removed"] is False
    assert "both files are still there" in result["error"]
    assert (proj / "a.txt").read_text() == "partial"
    assert (proj / "b.txt").read_text() == "partial"


def test_move_outside_ungranted_refused_and_source_kept(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (proj / "a.txt").write_text("keep me")
    dest = elsewhere / "b.txt"
    result = _exec("fs_move", {"path": "a.txt",
                               "destination": str(dest)},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert (proj / "a.txt").read_text() == "keep me"
    assert not dest.exists()


def test_delete_inside_ok(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("gone")
    result = _exec("fs_delete", {"path": "a.txt"}, root=proj)
    assert result["ok"] is True
    assert result["deleted"] is True
    assert not (proj / "a.txt").exists()


def test_delete_outside_ungranted_refused(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "victim.txt"
    target.write_text("do not touch")
    result = _exec("fs_delete", {"path": str(target)},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert target.read_text() == "do not touch"


def test_delete_own_files_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    target = tmp_path / "install" / "code.py"
    target.write_text("payload")
    result = _exec("fs_delete", {"path": str(target)},
                   root=proj,
                   policy=_roam_policy(tmp_path, granted=[target]))
    assert result["ok"] is False
    assert result.get("refused") == "own_files"
    assert target.read_text() == "payload"


def test_delete_directory_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "dir").mkdir()
    result = _exec("fs_delete", {"path": "dir"}, root=proj)
    assert result["ok"] is False
    assert "folder" in result["error"]
    assert (proj / "dir").is_dir()


def test_delete_symlink_refused_and_target_kept(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "real.txt").write_text("precious")
    (proj / "link.txt").symlink_to(elsewhere / "real.txt")
    result = _exec("fs_delete", {"path": "link.txt"}, root=proj)
    assert result["ok"] is False
    assert "link" in result["error"]
    assert (proj / "link.txt").is_symlink()
    assert (elsewhere / "real.txt").read_text() == "precious"


def test_mkdir_nested_ok(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_mkdir", {"path": "a/b/c"}, root=proj)
    assert result["ok"] is True
    assert result["created"] is True
    assert (proj / "a" / "b" / "c").is_dir()
    again = _exec("fs_mkdir", {"path": "a/b/c"}, root=proj)
    assert again["ok"] is True
    assert again["already_exists"] is True


def test_mkdir_outside_ungranted_refused(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    dest = elsewhere / "newdir"
    result = _exec("fs_mkdir", {"path": str(dest)},
                   root=proj, policy=_roam_policy(tmp_path))
    assert result["ok"] is False
    assert result.get("refused") == "no_permission"
    assert not dest.exists()


def test_new_tools_gate_outranks_auto_approve(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (proj / "a.txt").write_text("x")
    policy = _roam_policy(tmp_path)
    for tool, args in [
            ("fs_copy", {"path": "a.txt",
                         "destination": str(elsewhere / "b.txt")}),
            ("fs_move", {"path": "a.txt",
                         "destination": str(elsewhere / "b.txt")}),
            ("fs_delete", {"path": str(elsewhere / "victim.txt")}),
            ("fs_mkdir", {"path": str(elsewhere / "dir")})]:
        needs, reason = agent_tools.tool_requires_approval(
            tool, args, True, root=proj, policy=policy)
        assert needs is True, tool
        assert str(elsewhere) in reason, tool
    # A granted copy destination needs no second ask.
    dest = elsewhere / "b.txt"
    granted = _roam_policy(tmp_path, granted=[dest])
    needs, _ = agent_tools.tool_requires_approval(
        "fs_copy", {"path": "a.txt", "destination": str(dest)},
        False, root=proj, policy=granted)
    assert needs is False


def test_delete_gate_always_parks_even_inside_and_auto(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("x")
    policy = _roam_policy(tmp_path)
    for auto in (False, True):
        needs, reason = agent_tools.tool_requires_approval(
            "fs_delete", {"path": "a.txt"}, auto,
            root=proj, policy=policy)
        assert needs is True
        assert "a.txt" in reason
    # Own files never park — execution refuses them outright.
    needs, _ = agent_tools.tool_requires_approval(
        "fs_delete", {"path": str(tmp_path / "install" / "x.py")},
        False, root=proj, policy=policy)
    assert needs is False


def test_write_through_symlink_pointing_outside_still_refused(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    (elsewhere / "victim.txt").write_text("original")
    (proj / "link.txt").symlink_to(elsewhere / "victim.txt")
    result = _exec("fs_write", {"path": "link.txt", "content": "evil"},
                   root=proj)
    assert result["ok"] is False
    assert result.get("refused") == "workspace_escape"
    assert (elsewhere / "victim.txt").read_text() == "original"


# ------------------------------------------------- R2: atomic writes
def test_write_is_temp_plus_replace_and_fsync(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    renames = []
    real_rename = os.rename

    def spy(*args, **kwargs):
        renames.append(args)
        return real_rename(*args, **kwargs)

    fsync_calls = []
    real_fsync = os.fsync

    def fsync_spy(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "rename", spy)
    monkeypatch.setattr(os, "fsync", fsync_spy)
    result = _exec("fs_write", {"path": "a.txt", "content": "atomic"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "a.txt").read_text() == "atomic"
    assert any(".aqtmp" in str(a[0]) for a in renames), renames
    assert fsync_calls, "no fsync ran during the write"
    assert _no_temps(proj) == []


def test_failed_write_leaves_original_intact(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("original")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated rename failure")

    monkeypatch.setattr(os, "rename", boom)
    result = _exec("fs_write", {"path": "a.txt",
                                "content": "replacement"},
                   root=proj)
    assert result["ok"] is False
    assert (proj / "a.txt").read_text() == "original"
    assert _no_temps(proj) == []


# ------------------------------------------------------- R3: backups
def test_overwrite_keeps_backup_with_old_content(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("version one")
    result = _exec("fs_write", {"path": "a.txt",
                                "content": "version two"},
                   root=proj)
    assert result["ok"] is True
    assert result["overwritten"] is True
    assert result["backup"] == "a.txt.aqbak"
    assert (proj / "a.txt.aqbak").read_text() == "version one"
    assert (proj / "a.txt").read_text() == "version two"


def test_edit_keeps_backup(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("hello world")
    result = _exec("fs_edit", {"path": "a.txt", "old": "world",
                               "new": "there"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "a.txt.aqbak").read_text() == "hello world"
    assert (proj / "a.txt").read_text() == "hello there"


def test_create_has_no_backup(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "new.txt", "content": "x"},
                   root=proj)
    assert result["created"] is True
    assert result["backup"] is None
    assert not (proj / "new.txt.aqbak").exists()


def test_outside_granted_write_proceeds_without_backup_and_says_so(
        tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "granted.txt"
    target.write_text("old outside")
    result = _exec("fs_write", {"path": str(target),
                                "content": "new outside"},
                   root=proj,
                   policy=_roam_policy(tmp_path, granted=[target]))
    assert result["ok"] is True
    assert target.read_text() == "new outside"
    # The backup would sit next to the granted file — a different
    # exact file, so it is not permitted: no backup, said plainly.
    assert result["backup"] is None
    assert result["backup_note"] is not None
    assert "not backed up" in result["backup_note"]
    assert not (elsewhere / "granted.txt.aqbak").exists()


def test_backup_preserves_mode(tmp_path):
    proj, _ = _layout(tmp_path)
    target = proj / "a.txt"
    target.write_text("private")
    os.chmod(target, 0o600)
    _exec("fs_write", {"path": "a.txt", "content": "new"}, root=proj)
    assert stat.S_IMODE((proj / "a.txt.aqbak").stat().st_mode) == 0o600


# --------------------------------------------------- R4: diff preview
def test_write_preview_new_file(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "n.txt", "content": "hi",
                                "preview": True},
                   root=proj)
    assert result["ok"] is True
    assert result["preview"] is True
    assert result["created"] is True
    assert not (proj / "n.txt").exists()


def test_write_preview_overwrite_diff(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("old line\n")
    result = _exec("fs_write", {"path": "a.txt",
                                "content": "new line\n",
                                "preview": True},
                   root=proj)
    assert result["preview"] is True
    assert "-old line" in result["diff"]
    assert "+new line" in result["diff"]
    assert (proj / "a.txt").read_text() == "old line\n"
    assert not (proj / "a.txt.aqbak").exists()


def test_edit_preview_diff(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("alpha beta\n")
    result = _exec("fs_edit", {"path": "a.txt", "old": "beta",
                               "new": "gamma", "preview": True},
                   root=proj)
    assert result["preview"] is True
    assert "-alpha beta" in result["diff"]
    assert "+alpha gamma" in result["diff"]
    assert (proj / "a.txt").read_text() == "alpha beta\n"


def test_write_preview_of_directory_plain_error(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "dir").mkdir()
    result = _exec("fs_write", {"path": "dir", "content": "x",
                                "preview": True},
                   root=proj)
    assert result["ok"] is False
    assert "folder" in result["error"]


def test_preview_needs_no_approval(tmp_path):
    proj, _ = _layout(tmp_path)
    needs, _ = agent_tools.tool_requires_approval(
        "fs_write", {"path": "a.txt", "content": "x", "preview": True},
        False, root=proj, policy=_roam_policy(tmp_path))
    assert needs is False


def test_append_preview_writes_nothing(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("base")
    result = _exec("fs_write", {"path": "a.txt", "content": " more",
                                "append": True, "preview": True},
                   root=proj)
    assert result["preview"] is True
    assert (proj / "a.txt").read_text() == "base"


# ------------------------------------------- R5: create vs overwrite
def test_write_create_vs_overwrite_flags(tmp_path):
    proj, _ = _layout(tmp_path)
    first = _exec("fs_write", {"path": "a.txt", "content": "one"},
                  root=proj)
    assert first["created"] is True
    assert first["overwritten"] is False
    second = _exec("fs_write", {"path": "a.txt", "content": "two"},
                   root=proj)
    assert second["created"] is False
    assert second["overwritten"] is True


def test_overwrite_approval_reason_names_file(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("old")
    needs, reason = agent_tools.tool_requires_approval(
        "fs_write", {"path": "a.txt", "content": "new"}, False,
        root=proj, policy=_roam_policy(tmp_path))
    assert needs is True
    assert "would overwrite the existing file" in reason
    assert "a.txt" in reason
    needs, reason = agent_tools.tool_requires_approval(
        "fs_write", {"path": "fresh.txt", "content": "new"}, False,
        root=proj, policy=_roam_policy(tmp_path))
    assert needs is True
    assert "would overwrite" not in reason


# ----------------------------------------------- R6: ranged reads
def test_read_offset_limit_lines(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("".join(f"line{i}\n" for i in range(30)))
    result = _exec("fs_read", {"path": "a.txt", "offset": 10,
                               "limit": 5},
                   root=proj)
    assert result["ok"] is True
    assert result["content"] == "".join(f"line{i}\n" for i in range(10, 15))
    assert result["total_lines"] == 30
    assert result["lines_returned"] == 5
    assert result["truncated"] is True  # lines beyond the window


def test_read_offset_beyond_eof(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("only\n")
    result = _exec("fs_read", {"path": "a.txt", "offset": 99}, root=proj)
    assert result["ok"] is True
    assert result["content"] == ""
    assert result["truncated"] is False


def test_read_line_window_then_char_cap(tmp_path):
    proj, _ = _layout(tmp_path)
    lines = ["x" * 15000 + "\n" for _ in range(3)]
    (proj / "big.txt").write_text("".join(lines))
    whole = _exec("fs_read", {"path": "big.txt"}, root=proj)
    assert whole["truncated"] is True
    assert len(whole["content"]) == 20000
    assert whole["chars"] == 45003
    tail = _exec("fs_read", {"path": "big.txt", "offset": 2}, root=proj)
    assert tail["truncated"] is False
    assert tail["content"] == lines[2]


# ------------------------------------------------- R7: binary handling
def test_read_binary_refused_plain_words(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    result = _exec("fs_read", {"path": "blob.bin"}, root=proj)
    assert result["ok"] is False
    assert "not readable text" in result["error"]
    assert "content" not in result
    assert result.get("binary") is True


def test_read_nonutf8_refused_without_replacement_chars(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "legacy.txt").write_bytes(b"\xff\xfe\x00hello")
    result = _exec("fs_read", {"path": "legacy.txt"}, root=proj)
    assert result["ok"] is False
    assert "\ufffd" not in str(result)


def test_edit_binary_refused_and_file_unchanged(tmp_path):
    proj, _ = _layout(tmp_path)
    blob = b"keep\x00me\xff intact"
    (proj / "blob.bin").write_bytes(blob)
    result = _exec("fs_edit", {"path": "blob.bin", "old": "keep",
                               "new": "drop"},
                   root=proj)
    assert result["ok"] is False
    assert "not readable text" in result["error"]
    assert (proj / "blob.bin").read_bytes() == blob


# -------------------------------------------- R8: truncation flags
def test_list_truncation_flag_and_total(tmp_path):
    proj, _ = _layout(tmp_path)
    for i in range(205):
        (proj / f"f{i:03}.txt").write_text("x")
    result = _exec("fs_list", {"path": "."}, root=proj)
    assert result["total"] == 205
    assert len(result["entries"]) == 200
    assert result["truncated"] is True
    (proj / "small").mkdir()
    (proj / "small" / "one.txt").write_text("x")
    small = _exec("fs_list", {"path": "small"}, root=proj)
    assert small["total"] == 1
    assert small["truncated"] is False


def test_search_truncation_flag_and_total(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "many.txt").write_text("\n".join(
        f"needle {i}" for i in range(60)))
    (proj / "few.txt").write_text("needle once")
    result = _exec("fs_search", {"query": "needle"}, root=proj)
    assert result["total"] == 61
    assert len(result["matches"]) == 50
    assert result["truncated"] is True
    one = _exec("fs_search", {"query": "once"}, root=proj)
    assert one["total"] == 1
    assert one["truncated"] is False


def test_shell_truncation_flags_and_totals(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("shell_run",
                   {"argv": [sys.executable, "-c",
                             "import sys; sys.stdout.write('y'*20000)"]},
                   root=proj)
    assert result["ok"] is True
    assert result["stdout_truncated"] is True
    assert result["stdout_chars"] == 20000
    assert len(result["stdout"]) == 8000
    assert result["stderr_truncated"] is False
    assert result["stderr_chars"] == 0


def test_web_read_truncation_flag(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    import app.reach as reach

    def fake_execute(tool, args):
        return {"status": "ok", "title": "t",
                "text": "z" * 25000}

    monkeypatch.setattr(reach, "execute", fake_execute)
    result = asyncio.run(agent_tools._web_read(
        proj, {"url": "https://example.invalid/"}, reach_channels=None))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["chars"] == 25000
    assert len(result["text"]) == 20000


# --------------------------------------------------------- R9: locking
def test_concurrent_writes_serialized(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "shared.txt").write_text("original")
    contents = ["A" * 5000, "B" * 5000]
    results = []
    errors = []

    def worker(text):
        try:
            results.append(_exec(
                "fs_write", {"path": "shared.txt", "content": text},
                root=proj))
        except Exception as exc:  # noqa - surfaced for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(c,))
               for c in contents for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(r["ok"] is True and r["locked"] is True for r in results)
    final = (proj / "shared.txt").read_text()
    assert final in contents, "torn mixture written"
    backup = (proj / "shared.txt.aqbak").read_text()
    assert backup in contents + ["original"]
    assert _no_temps(proj) == []


def test_lock_timeout_fails_plainly_and_releases(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("original")
    monkeypatch.setattr(agent_tools, "LOCK_TIMEOUT_S", 0.3)
    dir_fd = os.open(proj, os.O_RDONLY)
    try:
        fcntl.flock(dir_fd, fcntl.LOCK_EX)
        result = _exec("fs_write", {"path": "a.txt",
                                    "content": "blocked"},
                       root=proj)
        assert result["ok"] is False
        assert result.get("lock_timeout") is True
        assert "still in progress" in result["error"]
        assert (proj / "a.txt").read_text() == "original"
    finally:
        fcntl.flock(dir_fd, fcntl.LOCK_UN)
        os.close(dir_fd)
    # After release, the same write succeeds: no stuck lock.
    result = _exec("fs_write", {"path": "a.txt", "content": "after"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "a.txt").read_text() == "after"


# --------------------------------------------------- R10: permissions
def test_new_file_mode_0644(tmp_path):
    proj, _ = _layout(tmp_path)
    _exec("fs_write", {"path": "a.txt", "content": "x"}, root=proj)
    assert stat.S_IMODE((proj / "a.txt").stat().st_mode) == 0o644


def test_overwrite_and_edit_preserve_existing_mode(tmp_path):
    proj, _ = _layout(tmp_path)
    for mode in (0o600, 0o755):
        target = proj / f"f{mode:o}.txt"
        target.write_text("old")
        os.chmod(target, mode)
        _exec("fs_write", {"path": target.name, "content": "new"},
              root=proj)
        assert stat.S_IMODE(target.stat().st_mode) == mode
        _exec("fs_edit", {"path": target.name, "old": "new",
                          "new": "newer"},
              root=proj)
        assert stat.S_IMODE(target.stat().st_mode) == mode


def test_copy_preserves_source_mode(tmp_path):
    proj, _ = _layout(tmp_path)
    src = proj / "a.sh"
    src.write_text("run")
    os.chmod(src, 0o751)
    dest = proj / "b.sh"
    dest.write_text("old")
    os.chmod(dest, 0o600)
    result = _exec("fs_copy", {"path": "a.sh", "destination": "b.sh"},
                   root=proj)
    assert result["ok"] is True
    assert stat.S_IMODE(dest.stat().st_mode) == 0o751


# ------------------------------------------------------- R11: fs_stat
def test_stat_file_fields(tmp_path):
    proj, _ = _layout(tmp_path)
    target = proj / "a.txt"
    target.write_text("12345")
    os.chmod(target, 0o640)
    result = _exec("fs_stat", {"path": "a.txt"}, root=proj)
    assert result["ok"] is True
    assert result["type"] == "file"
    assert result["size"] == 5
    assert result["mode"] == "0o640"
    assert "mtime" in result and "mtime_epoch" in result
    assert result["link"] is False


def test_stat_dir_and_link_fields(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "dir").mkdir()
    (proj / "real.txt").write_text("x")
    (proj / "link.txt").symlink_to(proj / "real.txt")
    assert _exec("fs_stat", {"path": "dir"}, root=proj)["type"] == "dir"
    link = _exec("fs_stat", {"path": "link.txt"}, root=proj)
    assert link["type"] == "file"
    assert link["link"] is True
    assert "real.txt" in link["target"]


def test_stat_missing_plain_and_own_files_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    missing = _exec("fs_stat", {"path": "nope.txt"}, root=proj)
    assert missing["ok"] is False
    assert "no such file" in missing["error"]
    (tmp_path / "install" / "code.py").write_text("payload")
    refused = _exec("fs_stat",
                    {"path": str(tmp_path / "install" / "code.py")},
                    root=proj, policy=_roam_policy(tmp_path))
    assert refused["ok"] is False
    assert refused.get("refused") == "own_files"


def test_stat_needs_no_approval(tmp_path):
    proj, _ = _layout(tmp_path)
    needs, _ = agent_tools.tool_requires_approval(
        "fs_stat", {"path": "a.txt"}, False,
        root=proj, policy=_roam_policy(tmp_path))
    assert needs is False


# ------------------------------------------------ R12: write-size limit
def test_write_over_cap_refused_plain_words(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "big.txt",
                                "content": "x" * 1000001},
                   root=proj)
    assert result["ok"] is False
    assert "too long" in result["error"]
    assert not (proj / "big.txt").exists()


def test_write_at_cap_ok(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "big.txt",
                                "content": "x" * 1000000},
                   root=proj)
    assert result["ok"] is True
    assert (proj / "big.txt").stat().st_size == 1000000


def test_edit_result_over_cap_refused(tmp_path):
    proj, _ = _layout(tmp_path)
    original = "A" + "y" * 999999  # exactly at the cap
    assert _exec("fs_write", {"path": "big.txt", "content": original},
                 root=proj)["ok"] is True
    result = _exec("fs_edit", {"path": "big.txt", "old": "A",
                               "new": "AB"},
                   root=proj)
    assert result["ok"] is False
    assert "too long" in result["error"]
    assert (proj / "big.txt").read_text() == original


def test_copy_over_cap_refused(tmp_path, monkeypatch):
    proj, _ = _layout(tmp_path)
    (proj / "a.txt").write_text("12345678901")
    monkeypatch.setattr(agent_tools, "MAX_COPY_BYTES", 10)
    result = _exec("fs_copy", {"path": "a.txt", "destination": "b.txt"},
                   root=proj)
    assert result["ok"] is False
    assert "too large" in result["error"]


# --------------------------------------------------------- R13: append
def test_append_adds_without_rewriting(tmp_path):
    proj, _ = _layout(tmp_path)
    target = proj / "a.txt"
    target.write_text("hello")
    os.chmod(target, 0o600)
    result = _exec("fs_write", {"path": "a.txt", "content": " world",
                                "append": True},
                   root=proj)
    assert result["ok"] is True
    assert result["appended"] is True
    assert target.read_text() == "hello world"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert (proj / "a.txt.aqbak").read_text() == "hello"


def test_append_creates_new_file(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "new.txt", "content": "hi",
                                "append": True},
                   root=proj)
    assert result["ok"] is True
    assert result["created"] is True
    assert (proj / "new.txt").read_text() == "hi"


# -------------------------------------------- R14: shell residual
def test_shell_cp_mv_rm_unasked_in_auto_approve(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    policy = _roam_policy(tmp_path)
    for exe in ("cp", "mv", "rm"):
        needs, _ = agent_tools.tool_requires_approval(
            "shell_run", {"argv": [exe, "a", "b"]}, True,
            root=proj, policy=policy)
        assert needs is False, exe
    assert agent_tools.classify_shell(["cp", "a", "b"]) == "mutating"
    # An opaque payload is unasked too: the argv scan that would
    # be needed to catch it does not exist, by evaluation (see
    # report §14) — python -c can copy, move, or delete anything.
    needs, _ = agent_tools.tool_requires_approval(
        "shell_run", {"argv": [sys.executable, "-c",
                               "import shutil; shutil.copy('a','b')"]},
        True, root=proj, policy=policy)
    assert needs is False
    # Even a shell rm aimed at AgentQon's own files is unasked in
    # local auto-approve mode: no own-files check exists on shell.
    needs, _ = agent_tools.tool_requires_approval(
        "shell_run", {"argv": ["rm",
                               str(tmp_path / "install" / "code.py")]},
        True, root=proj, policy=policy)
    assert needs is False


# ------------------------------------ R15: case + long paths
def test_case_sensitive_distinct_files_linux(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "Foo.txt").write_text("upper")
    (proj / "foo.txt").write_text("lower")
    upper = _exec("fs_read", {"path": "Foo.txt"}, root=proj)
    lower = _exec("fs_read", {"path": "foo.txt"}, root=proj)
    assert upper["content"] == "upper"
    assert lower["content"] == "lower"


def test_deep_path_over_260_chars_ok_linux(tmp_path):
    proj, _ = _layout(tmp_path)
    rel = "/".join(["d" * 60] * 5 + ["deep.txt"])
    assert len(str(proj / rel)) > 260
    result = _exec("fs_write", {"path": rel, "content": "deep"},
                   root=proj)
    assert result["ok"] is True
    assert (proj / rel).read_text() == "deep"


def test_overlong_component_plain_error(tmp_path):
    proj, _ = _layout(tmp_path)
    result = _exec("fs_write", {"path": "x" * 300, "content": "x"},
                   root=proj)
    assert result["ok"] is False
    assert "too long" in result["error"]


# --------------------------------------- R16: existence semantics
def test_read_missing_plain_and_directory_distinct(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "dir").mkdir()
    missing = _exec("fs_read", {"path": "nope.txt"}, root=proj)
    assert missing["ok"] is False
    assert "no such file" in missing["error"]
    is_dir = _exec("fs_read", {"path": "dir"}, root=proj)
    assert is_dir["ok"] is False
    assert "folder" in is_dir["error"]
    assert "no such file" not in is_dir["error"]


def test_read_empty_file_ok(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "empty.txt").write_text("")
    result = _exec("fs_read", {"path": "empty.txt"}, root=proj)
    assert result["ok"] is True
    assert result["content"] == ""
    assert result["chars"] == 0
    assert result["truncated"] is False


def test_write_to_directory_plain_error(tmp_path):
    proj, _ = _layout(tmp_path)
    (proj / "dir").mkdir()
    result = _exec("fs_write", {"path": "dir", "content": "x"},
                   root=proj)
    assert result["ok"] is False
    assert "folder" in result["error"]


# ------------------------------------------- loop-level: approvals
class _StubGateway:
    def __init__(self, script):
        self.script = list(script)

    async def chat_json(self, model, system, user, temperature=0.4):
        item = self.script.pop(0)
        if callable(item):
            item = item(user)
        return item, {"input_tokens": 100, "output_tokens": 50,
                      "model": model}


def _runner(tmp_path, script):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path / "data", auth_mode="dev")
    gateway = _StubGateway(script)

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model-1", UNKNOWN_ENTRY, \
            {"id": "conn_test", "kind": "direct_provider"}

    return agent_loop.AgentRunner(store, settings, factory)


def _tool(tool, args):
    return {"action": "tool", "tool": tool, "args": args,
            "thought": "step"}


def _done(summary="finished"):
    return {"action": "done", "summary": summary}


def test_loop_delete_parks_even_in_auto_approve(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    target = proj / "doomed.txt"
    target.write_text("bye")
    runner = _runner(tmp_path, [
        _tool("fs_delete", {"path": "doomed.txt"}),
        # An observation after the mutation, so the verify-after-
        # build guard accepts the completion claim that follows.
        _tool("fs_list", {"path": "."}),
        _done("deleted"),
    ])
    session = runner.create_session(TENANT, "tester", "delete a file",
                                    workspace_root=str(proj),
                                    auto_approve_rw=True)
    parked = asyncio.run(runner.run(TENANT, session["id"]))
    assert parked["status"] == "awaiting_approval"
    assert "doomed.txt" in parked["pending_action"]["reason"]
    assert parked["pending_action"].get("grant_path") is None
    assert target.exists()
    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=True))
    assert done["status"] == "completed"
    assert not target.exists()
    # A delete approval grants nothing for the future.
    assert done["write_grants"] == []


def test_loop_mkdir_outside_approved_via_one_shot(tmp_path):
    proj, elsewhere = _layout(tmp_path)
    dest = elsewhere / "approved-dir"
    runner = _runner(tmp_path, [
        _tool("fs_mkdir", {"path": str(dest)}),
        _tool("fs_list", {"path": str(dest)}),
        _done("made"),
    ])
    session = runner.create_session(TENANT, "tester", "make a folder",
                                    workspace_root=str(proj),
                                    auto_approve_rw=True)
    parked = asyncio.run(runner.run(TENANT, session["id"]))
    assert parked["status"] == "awaiting_approval"
    assert str(dest.resolve()) in parked["pending_action"]["reason"]
    assert parked["pending_action"].get("grant_path") is None
    done = asyncio.run(runner.resolve_approval(TENANT, session["id"],
                                               approve=True))
    assert done["status"] == "completed"
    assert dest.is_dir()
    assert done["write_grants"] == []
