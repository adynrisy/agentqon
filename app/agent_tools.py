"""AgentQon — agent-session tools (v22, R1).

The tools an agent session may call: fs_read, fs_write, fs_edit,
fs_list, fs_search, fs_copy, fs_move/fs_rename, fs_delete,
fs_mkdir, fs_stat, shell_run, web_read. Path confinement runs under a
PathPolicy: the JAILED policy (hosted/accounts mode, and the default
for direct callers) confines every tool to the session's workspace
root — a path that escapes it, via `..`, an absolute path outside
it, or a symlink pointing out, is REFUSED, never followed. The
FREE-ROAM policy (local mode) lets reads roam the machine under the
owner's Write Law (see the free-roam section below): writes stay in
the home project or on an exact user-granted file, and AgentQon's
own files are off limits in every mode — and the new copy/move/
delete/mkdir tools enforce exactly the same law as fs_write/fs_edit.
Writes are atomic (temp file in the same folder + rename + fsync),
backed up before overwrite (when the backup location is itself
permitted), previewable without writing, advisory-locked per
folder, and plain about truncation everywhere. Shell execution is argv-only (a list, executed
directly; never a shell string, never shell=True), so metacharacters
in an argument are inert data. Results are plain dicts; an expected
failure (missing file, escape attempt, non-zero exit) is a result
with ok=False, not an exception — the agent observes it and adapts.

Approval classification lives here too (tool_requires_approval):
read-only tools never need approval; file writes need it unless the
user explicitly put the session in auto-approve-read-write mode;
shell commands are classified — a strict allowlist of commands AND
argument shapes runs free, anything mutating needs approval (the
auto mode covers it), and network-capable commands need approval in
EVERY mode. The allowlist is argument-aware (security audit F1/F2):
`env`/`printenv` are NOT read-only (one dumps the server process
environment, the other is a program launcher), and argument shapes
that write files, delete, or execute a program (`find -delete` /
`-exec`, `rg --pre`, `git diff --output`, `git branch <name>`, …)
reclassify the call as mutating no matter what argv[0] says.
"""
from __future__ import annotations

import asyncio
import difflib
import errno
import os
import random
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .tooldefs import filter_env as _filter_env
from .tooldefs import resource_limits_preexec as _resource_preexec
try:
    import fcntl
except ImportError:  # Windows has no fcntl
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX has no msvcrt
    msvcrt = None

MAX_READ_CHARS = 20_000
MAX_OUTPUT_CHARS = 8_000
MAX_LIST_ENTRIES = 200
MAX_SEARCH_MATCHES = 50
MAX_SEARCH_FILE_BYTES = 512_000
# One write call may carry at most this many characters of generated
# text: 50x the read cap. Agent writes are normally kilobytes; the
# cap stops a runaway or model-dump write, not a real file. Copies
# move an existing file rather than generated text, so they carry a
# separate byte cap instead (see MAX_COPY_BYTES).
MAX_WRITE_CHARS = 1_000_000
MAX_COPY_BYTES = 100_000_000
MAX_DIFF_LINES = 200
LOCK_TIMEOUT_S = 10.0
BACKUP_SUFFIX = ".aqbak"
DEFAULT_SHELL_TIMEOUT_S = 60.0
MAX_SHELL_TIMEOUT_S = 300.0

TOOLS = ("fs_read", "fs_write", "fs_edit", "fs_list", "fs_search",
         "fs_copy", "fs_move", "fs_rename", "fs_delete", "fs_mkdir",
         "fs_stat", "shell_run", "web_read")
READ_ONLY_TOOLS = ("fs_read", "fs_list", "fs_search", "fs_stat",
                   "web_read")
# Mutation tools beyond the original two, for the approval gate and
# the loop's verify-after-build tracking. fs_rename is an alias of
# fs_move (a move whose destination sits in the same folder).
MUTATION_TOOLS = ("fs_write", "fs_edit", "fs_copy", "fs_move",
                  "fs_rename", "fs_delete", "fs_mkdir")


class WorkspaceEscape(Exception):
    """A tool path resolved outside the session workspace root."""


def resolve_in_workspace(root: Path, rel: str) -> Path:
    """Resolve `rel` against the workspace root and PROVE the result
    stays inside it. Path.resolve() follows symlinks, so a symlink
    inside the workspace pointing outside is caught by the same
    containment check as a literal `..`."""
    root_resolved = Path(root).resolve()
    candidate = Path(rel)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise WorkspaceEscape(
            f"path {rel!r} escapes the session workspace")
    return resolved


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


# ------------------------------------------------------------ free-roam
# Free-roam (local mode only): like the terminal agent CLIs, the
# session's home project is the folder the harness was launched in,
# and the agent may READ anywhere on the machine the OS user can —
# under the owner's WRITE LAW: a file may be changed only inside the
# home project, or when the user has given permission for that exact
# file (the grant flow in the agent loop). AgentQon's OWN files are
# off limits in every mode and no grant can open them: the data dir
# (settings, vault key, database, every tenant's data) and the
# install/payload tree. A PathPolicy with roam=False is the jailed
# posture — every path must stay inside the workspace root, exactly
# the pre-roam behaviour, and the only posture hosted (accounts)
# mode ever gets.

class OwnFilesRefused(Exception):
    """A path inside AgentQon's own files (data dir or install tree)."""


class OutsideWriteRefused(Exception):
    """An outside-project write with no user grant for that file."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            f"the user has not given permission to modify {path} — "
            "it is outside the project. Ask the user for permission "
            "for that exact file, or work without changing it")


@dataclass(frozen=True)
class PathPolicy:
    """The path policy for one session's tool calls. `granted_files`
    holds resolved absolute paths the user has approved for writing,
    one exact file per grant, for the rest of the session."""

    roam: bool = False
    data_dir: Optional[Path] = None
    install_dir: Optional[Path] = None
    granted_files: frozenset = frozenset()


def install_dir() -> Path:
    """The harness's own install/payload tree, derived from this
    file's location: the parent of the app package — the repo root in
    a dev checkout, the staged server tree in the packaged payload.
    Robust to both layouts because the app package rides inside each."""
    return Path(__file__).resolve().parent.parent


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _own_files_zone(resolved: Path, root_resolved: Path,
                    policy: PathPolicy) -> bool:
    """True when `resolved` sits in AgentQon's own files under this
    policy. The session's own workspace always wins: a local default
    workspace legitimately lives under the data dir (and, in a dev
    checkout, under the install tree), and the home project is the
    agent's to work in — it is everything ELSE of AgentQon's that is
    off limits."""
    if _inside(resolved, root_resolved):
        return False
    for base in (policy.data_dir, policy.install_dir):
        if base is not None and _inside(resolved, Path(base).resolve()):
            return True
    return False


def resolve_read_path(root: Path, rel: str,
                      policy: Optional[PathPolicy] = None) -> Path:
    """Resolve a READ path (fs_read / fs_list / fs_search). Jailed
    policy: exactly resolve_in_workspace. Roam policy: relative paths
    resolve against the home project, absolute paths are taken as
    given, `..` may roam — the result may sit anywhere EXCEPT
    AgentQon's own files (symlinks are resolved first, so a link
    pointing into the off-limits ground is refused, never followed)."""
    if policy is None or not policy.roam:
        return resolve_in_workspace(root, rel)
    root_resolved = Path(root).resolve()
    candidate = Path(rel)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    if _own_files_zone(resolved, root_resolved, policy):
        raise OwnFilesRefused(
            f"path {rel!r} escapes the session workspace into "
            "AgentQon's own files — AgentQon never opens or changes "
            "its own files")
    return resolved


def resolve_write_path(root: Path, rel: str,
                       policy: Optional[PathPolicy] = None
                       ) -> tuple[Path, bool]:
    """Resolve a WRITE path (fs_write / fs_edit) to (resolved,
    inside_home). Roam policy enforces the Write Law: inside the
    home project is free; AgentQon's own files are refused outright;
    an outside file needs the user's grant for that exact file,
    else OutsideWriteRefused (carrying the resolved path) tells the
    caller — the approval gate grants per file, never per folder."""
    root_resolved = Path(root).resolve()
    if policy is None or not policy.roam:
        return resolve_in_workspace(root, rel), True
    candidate = Path(rel)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    if _inside(resolved, root_resolved):
        return resolved, True
    if _own_files_zone(resolved, root_resolved, policy):
        raise OwnFilesRefused(
            "AgentQon never modifies its own files — that path is "
            "one of them")
    if resolved in policy.granted_files:
        return resolved, False
    raise OutsideWriteRefused(resolved)


def outside_write_target(root: Path, rel: str,
                         policy: Optional[PathPolicy]) -> Optional[Path]:
    """The resolved outside file a write would need a grant for, or
    None when the write needs no grant: inside the home project,
    already granted, AgentQon's own files (no grant can ever open
    those — the write itself will refuse), or any write at all under
    a jailed policy."""
    if policy is None or not policy.roam:
        return None
    try:
        resolve_write_path(root, rel, policy)
    except OutsideWriteRefused as exc:
        return exc.path
    except (OwnFilesRefused, WorkspaceEscape, OSError, RuntimeError):
        return None
    return None


# ------------------------------------------------------- file-op helpers
# Gap-fix W8 (file operations): atomic writes, backups, locking,
# binary detection, and the typed copy/move/delete/mkdir/stat tools
# all build on these helpers. The Write Law is unchanged: every
# helper below resolves through resolve_write_path /
# resolve_read_path, so inside-home writes stay free, outside writes
# need the exact granted file, and AgentQon's own files refuse even
# with a grant — for the new tools exactly as for fs_write/fs_edit.

class PlainToolError(Exception):
    """A failure already worded in plain words (no 'tool error:'
    prefix is added by execute_tool)."""


class LockTimeout(Exception):
    """A folder lock was held longer than LOCK_TIMEOUT_S."""


def _is_binary(raw: bytes) -> bool:
    """True when `raw` is not plain UTF-8 text: a null byte in the
    first 8 KB, or bytes that do not decode as UTF-8. Such files are
    refused in plain words instead of being mangled with replacement
    characters (the old fs_read/fs_edit behaviour)."""
    if b"\x00" in raw[:8192]:
        return True
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _fsync_best_effort(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError:
        pass


def _temp_leaf_name(leaf: str) -> str:
    return f".{leaf}.aqtmp-{os.getpid()}-{random.randrange(1 << 30)}"


def _atomic_write_bytes(anchor: Path, target: Path, data: bytes,
                        hop_ok: Callable[[Path], bool],
                        hop_refusal: str, *, mode: int,
                        force_mode: bool = False) -> Path:
    """Write `data` to the already-proven absolute `target`
    ATOMICALLY: a temp file in the SAME directory, fsync, then
    os.replace onto the target, then a best-effort fsync of the
    directory. A crash mid-write leaves either the old file or the
    new file, never a half-written one, and a failed write cleans
    up its temp file on every path. The pinned-walk discipline of
    the old _pinned_write is kept: on POSIX the walk uses directory
    file descriptors with O_NOFOLLOW, so a parent swapped for a
    symlink mid-write fails instead of redirecting the write, and a
    symlink leaf is followed one hop at a time only when hop_ok
    allows the landing place. Where dir_fd is unavailable (e.g.
    Windows) the same temp+replace runs in the target's directory
    with best-effort fsync — stated as a fallback, not faked.
    Mode: a new file gets `mode`; an existing file keeps its mode,
    unless force_mode (copies carry the source mode even over an
    existing destination)."""
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        anchor_resolved = Path(anchor).resolve()
        parts = target.relative_to(anchor_resolved).parts
        if not parts:
            raise PlainToolError("that path is a folder, not a file")
        dir_fd = os.open(anchor_resolved,
                         os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                                  | os.O_NOFOLLOW, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            leaf = parts[-1]
            try:
                link = os.readlink(leaf, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENOENT):
                    link = None
                else:
                    raise
            if link is not None:
                target2 = Path(link)
                if not target2.is_absolute():
                    target2 = target.parent / target2
                target2 = target2.resolve()
                if not hop_ok(target2):
                    raise WorkspaceEscape(hop_refusal)
                return _atomic_write_bytes(anchor, target2, data,
                                           hop_ok, hop_refusal,
                                           mode=mode,
                                           force_mode=force_mode)
            try:
                existing_mode = stat.S_IMODE(
                    os.stat(leaf, dir_fd=dir_fd).st_mode)
            except OSError:
                existing_mode = None
            eff_mode = mode if force_mode or existing_mode is None \
                else existing_mode
            temp = _temp_leaf_name(leaf)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         eff_mode, dir_fd=dir_fd)
            try:
                try:
                    os.fchmod(fd, eff_mode)
                except (AttributeError, OSError):
                    pass
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                try:
                    os.unlink(temp, dir_fd=dir_fd)
                except OSError:
                    pass
                raise
            try:
                os.rename(temp, leaf, src_dir_fd=dir_fd,
                          dst_dir_fd=dir_fd)
            except BaseException:
                try:
                    os.unlink(temp, dir_fd=dir_fd)
                except OSError:
                    pass
                raise
            _fsync_best_effort(dir_fd)
            return target
        finally:
            os.close(dir_fd)
    # Fallback (no dir_fd, e.g. Windows): same temp+replace in the
    # target's own directory, fsync best-effort, symlink leaf
    # followed one hop by hand as above.
    if os.path.islink(target):
        target2 = Path(os.readlink(target))
        if not target2.is_absolute():
            target2 = target.parent / target2
        target2 = target2.resolve()
        if not hop_ok(target2):
            raise WorkspaceEscape(hop_refusal)
        return _atomic_write_bytes(anchor, target2, data, hop_ok,
                                   hop_refusal, mode=mode,
                                   force_mode=force_mode)
    try:
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        existing_mode = None
    eff_mode = mode if force_mode or existing_mode is None \
        else existing_mode
    temp = target.parent / _temp_leaf_name(target.name)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     eff_mode)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                _fsync_best_effort(fh.fileno())
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        try:
            os.chmod(temp, eff_mode)
        except OSError:
            pass
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(target.parent, os.O_RDONLY)
        try:
            _fsync_best_effort(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return target


def _pinned_write(anchor: Path, target: Path, content: str,
                  hop_ok: Callable[[Path], bool],
                  hop_refusal: str) -> Path:
    """Backwards-compatible wrapper for the old in-place pinned
    writer: the same call shape, now atomic (temp+replace)."""
    return _atomic_write_bytes(anchor, target, content.encode("utf-8"),
                               hop_ok, hop_refusal, mode=0o644)


def _pinned_append(anchor: Path, target: Path, data: bytes,
                   hop_ok: Callable[[Path], bool],
                   hop_refusal: str, *, mode: int) -> Path:
    """Append `data` to `target` WITHOUT rewriting the whole file:
    an O_APPEND open through the pinned walk (POSIX) or a plain
    append open (fallback), fsync afterwards. Appending is not
    atomic — a crash mid-append can leave partial appended bytes —
    but the pre-append backup (see _do_write) still protects the
    previous version. An existing file keeps its mode; a new file
    gets `mode`."""
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        anchor_resolved = Path(anchor).resolve()
        parts = target.relative_to(anchor_resolved).parts
        if not parts:
            raise PlainToolError("that path is a folder, not a file")
        dir_fd = os.open(anchor_resolved,
                         os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                                  | os.O_NOFOLLOW, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            leaf = parts[-1]
            try:
                link = os.readlink(leaf, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENOENT):
                    link = None
                else:
                    raise
            if link is not None:
                target2 = Path(link)
                if not target2.is_absolute():
                    target2 = target.parent / target2
                target2 = target2.resolve()
                if not hop_ok(target2):
                    raise WorkspaceEscape(hop_refusal)
                return _pinned_append(anchor, target2, data, hop_ok,
                                      hop_refusal, mode=mode)
            fd = os.open(leaf, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                         | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
            with os.fdopen(fd, "ab") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            return target
        finally:
            os.close(dir_fd)
    if os.path.islink(target):
        target2 = Path(os.readlink(target))
        if not target2.is_absolute():
            target2 = target.parent / target2
        target2 = target2.resolve()
        if not hop_ok(target2):
            raise WorkspaceEscape(hop_refusal)
        return _pinned_append(anchor, target2, data, hop_ok,
                              hop_refusal, mode=mode)
    with open(target, "ab") as fh:
        fh.write(data)
        fh.flush()
        _fsync_best_effort(fh.fileno())
    return target


@contextmanager
def _operation_lock(directory: Path, target: Path):
    """Advisory lock for one file operation, held while the caller
    backs up and writes. POSIX: flock on the destination folder's
    directory descriptor — one lock per folder serializes every
    harness writer in that folder, and it survives the temp+rename
    (the lock is on the folder, not the replaced file). Windows:
    msvcrt locks the existing target file itself when there is one.
    Timeout: LOCK_TIMEOUT_S, then LockTimeout — the write FAILS
    plainly rather than proceeding unlocked or waiting forever.
    flock releases automatically if the holder's process dies, so a
    stale lock cannot deadlock a later write. Yields
    (locked, lock_note); when no locking is available the caller
    still proceeds, but says so plainly in the result."""
    if fcntl is not None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EWOULDBLOCK,
                                         errno.EACCES):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            "another file change in this folder is "
                            "still in progress, so this change was "
                            "not made. Try again in a moment.")
                    time.sleep(0.05)
            try:
                yield (True, None)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
    elif msvcrt is not None and target.exists() and target.is_file():
        fh = open(target, "r+b")
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            "another file change in this folder is "
                            "still in progress, so this change was "
                            "not made. Try again in a moment.")
                    time.sleep(0.05)
            try:
                yield (True, None)
            finally:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            fh.close()
    else:
        yield (False, "file locking is not available on this "
                      "computer for this file, so the change ran "
                      "without a lock")


def _permitted_backup_path(root: Path, resolved: Path,
                           policy: Optional[PathPolicy]
                           ) -> Optional[Path]:
    """The backup location for `resolved`, or None when that
    location is not itself permitted under the Write Law. A backup
    next to an outside granted file is itself an outside write to a
    different exact file, so it is NOT permitted — the write then
    proceeds without a backup and says so (see _do_write)."""
    backup_raw = Path(str(resolved) + BACKUP_SUFFIX)
    try:
        if policy is not None and policy.roam:
            backup, _ = resolve_write_path(root, str(backup_raw),
                                           policy)
        else:
            backup = resolve_in_workspace(root, str(backup_raw))
    except Exception:
        return None
    return backup


def _backup_for_write(root: Path, resolved: Path,
                      policy: Optional[PathPolicy], *,
                      anchor: Path, hop_ok: Callable[[Path], bool]
                      ) -> tuple[Optional[str], Optional[str]]:
    """Preserve the previous version of `resolved` before it is
    overwritten, edited, appended to, or copied over. Returns
    (backup_display_path_or_None, note_or_None). A missing file
    needs no backup; an unpermitted or unsaveable backup degrades
    plainly — the write still proceeds, and the result says there
    is no backup, never silently and never by violating the law."""
    if not resolved.is_file():
        return None, None
    backup = _permitted_backup_path(root, resolved, policy)
    if backup is None:
        return None, ("the previous version was not backed up "
                      "because its backup location is outside the "
                      "files this session may change")
    try:
        data = resolved.read_bytes()
        mode = stat.S_IMODE(resolved.stat().st_mode)
        _atomic_write_bytes(anchor, backup, data, hop_ok,
                            "the backup file is a link to a place "
                            "this write is not allowed to reach",
                            mode=mode, force_mode=True)
    except Exception:
        return None, ("the backup could not be saved, so the change "
                      "went ahead without one")
    return _rel(root, backup), None


def _do_write(root: Path, rel: str, policy: Optional[PathPolicy],
              data: bytes, *, mode: int, force_mode: bool = False,
              append: bool = False) -> tuple[Path, dict]:
    """The single write path for fs_write, fs_edit, fs_copy and
    fs_move destinations: resolve under the policy, create parents,
    re-prove containment, lock the parent folder, back up an
    existing file, then write atomically (or append). Returns
    (resolved, info) where info carries create-vs-overwrite, the
    backup location or the plain reason there is none, and the lock
    outcome. Raises the policy exceptions, PlainToolError, and
    LockTimeout for the caller to report in plain words."""
    root_resolved = Path(root).resolve()
    if policy is not None and policy.roam:
        resolved, _ = resolve_write_path(root, rel, policy)
        anchor = Path(resolved.anchor)
        hop_ok: Callable[[Path], bool] = (
            lambda t: _inside(t, root_resolved)
            or t in policy.granted_files)
        hop_refusal = ("that file is a link to a place this write "
                       "is not allowed to reach")
    else:
        resolved = resolve_in_workspace(root, rel)
        anchor = root_resolved
        hop_ok = lambda t: _inside(t, root_resolved)
        hop_refusal = f"path {rel!r} escapes the session workspace"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if policy is not None and policy.roam:
        resolved, _ = resolve_write_path(root, rel, policy)
    else:
        resolved = resolve_in_workspace(root, rel)
    if resolved.is_dir():
        raise PlainToolError("that path is a folder, not a file")
    existed = resolved.is_file()
    with _operation_lock(resolved.parent, resolved) as (locked,
                                                        lock_note):
        backup, backup_note = _backup_for_write(
            root, resolved, policy, anchor=anchor, hop_ok=hop_ok) \
            if existed else (None, None)
        if append and existed:
            _pinned_append(anchor, resolved, data, hop_ok,
                           hop_refusal, mode=mode)
        else:
            _atomic_write_bytes(anchor, resolved, data, hop_ok,
                                hop_refusal, mode=mode,
                                force_mode=force_mode)
    info = {"created": not existed, "overwritten": existed,
            "backup": backup, "backup_note": backup_note,
            "locked": locked, "lock_note": lock_note}
    return resolved, info


def _pinned_unlink(anchor: Path, target: Path) -> None:
    """Unlink `target` through the pinned parent walk (POSIX), so a
    parent swapped for a symlink mid-delete fails instead of
    redirecting the delete; fallback is a plain unlink."""
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        anchor_resolved = Path(anchor).resolve()
        parts = target.relative_to(anchor_resolved).parts
        if len(parts) < 1:
            raise PlainToolError(
                "cannot delete the workspace folder itself")
        dir_fd = os.open(anchor_resolved,
                         os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                                  | os.O_NOFOLLOW, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            os.unlink(parts[-1], dir_fd=dir_fd)
            return
        finally:
            os.close(dir_fd)
    os.unlink(target)


def _delete_file(root: Path, rel: str,
                 policy: Optional[PathPolicy]) -> Path:
    """Delete one file under the policy. Symlinks are refused
    plainly (deleting a link must never silently delete its
    target), folders are refused (no recursive delete, ever), and
    the unlink runs under the parent-folder lock. Returns the
    resolved path deleted."""
    root_resolved = Path(root).resolve()
    if policy is not None and policy.roam:
        resolved, _ = resolve_write_path(root, rel, policy)
        anchor = Path(resolved.anchor)
    else:
        resolved = resolve_in_workspace(root, rel)
        anchor = root_resolved
    lexical = Path(rel) if Path(rel).is_absolute() \
        else root_resolved / rel
    if os.path.islink(lexical):
        raise PlainToolError(
            "that path is a link, not a file — deleting a link is "
            "not supported, so the file it points to was left alone")
    if not resolved.exists():
        raise PlainToolError(f"no such file: {rel}")
    if resolved.is_dir():
        raise PlainToolError(
            "that path is a folder, not a file — deleting a folder "
            "is not supported")
    with _operation_lock(resolved.parent, resolved):
        _pinned_unlink(anchor, resolved)
    return resolved


def _effective_policy(policy: Optional[PathPolicy],
                      approved_paths: Optional[set] = None
                      ) -> PathPolicy:
    """The policy a tool call actually runs under: the session
    policy plus any one-shot paths the loop just approved for this
    call only (see approved_paths_for). One-shot approvals never
    become session grants — a delete or folder approval must not
    quietly open future writes to that path. Under a jailed policy
    grants are ignored entirely (confinement, not grants, rules)."""
    if policy is None:
        return PathPolicy()
    if not approved_paths or not policy.roam:
        return policy
    return PathPolicy(
        roam=True, data_dir=policy.data_dir,
        install_dir=policy.install_dir,
        granted_files=frozenset(policy.granted_files)
        | frozenset(Path(p) for p in approved_paths))


def approved_paths_for(tool: str, args: dict, root: Path,
                       policy: Optional[PathPolicy]) -> set:
    """The resolved paths one loop approval covers for the new
    tools: the destination for copy/move, the path for delete and
    mkdir, and BOTH ends for a move (its source must be deletable
    too). Best-effort — a path that cannot even be resolved is
    skipped, and execution still enforces the policy."""
    if tool in ("fs_copy", "fs_move", "fs_rename"):
        rels = [str(args.get("path", "")),
                str(args.get("destination", args.get("dest", "")))]
    elif tool in ("fs_delete", "fs_mkdir"):
        rels = [str(args.get("path", ""))]
    else:
        return set()
    root_resolved = Path(root).resolve()
    out = set()
    for rel in rels:
        if not rel:
            continue
        try:
            cand = Path(rel)
            if not cand.is_absolute():
                cand = root_resolved / cand
            out.add(cand.resolve())
        except Exception:
            continue
    return out


def _outside_target_for(tool: str, root: Path, args: dict,
                        policy: Optional[PathPolicy]
                        ) -> Optional[Path]:
    """The resolved outside file a new-tool call would need
    permission for, or None when it needs no grant (inside the home
    project, already granted, AgentQon's own files — execution
    refuses those outright — or any call under a jailed policy)."""
    if policy is None or not policy.roam:
        return None
    if tool in ("fs_write", "fs_edit"):
        rel = str(args.get("path", ""))
    elif tool in ("fs_copy", "fs_move", "fs_rename"):
        rel = str(args.get("destination", args.get("dest", "")))
    elif tool in ("fs_delete", "fs_mkdir"):
        rel = str(args.get("path", ""))
    else:
        return None
    if not rel:
        return None
    return outside_write_target(root, rel, policy)


def _diff_text(old: Optional[str], new: str, *,
               from_label: str, to_label: str
               ) -> tuple[Optional[str], bool]:
    """A unified diff of old → new, capped at MAX_DIFF_LINES with a
    truncation flag. None when there is no old version (a new file)
    or the old version is not readable text."""
    if old is None:
        return None, False
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=from_label, tofile=to_label))
    lines = diff.splitlines(keepends=True)
    if len(lines) > MAX_DIFF_LINES:
        return "".join(lines[:MAX_DIFF_LINES]), True
    return diff, False


# ---------------------------------------------------------- shell classes
# The read-only set is a strict allowlist of commands whose ARGUMENT
# SHAPES are then screened by _read_only_args_ok (audit F1/F2).
# `env` and `printenv` are deliberately ABSENT: env with arguments is
# a program launcher (env cat /etc/passwd) and both dump the server
# process environment — where this product keeps its vault key and
# provider secrets — so they classify as mutating (approval-gated)
# like any other unrecognized command.
_READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "pwd", "echo", "wc", "grep",
    "rg", "find", "which", "whereis", "stat", "file", "tree",
    "true", "false", "whoami", "hostname", "date",
}
_GIT_READ_ONLY = {"status", "log", "diff", "show", "branch",
                  "rev-parse", "ls-files", "describe", "blame"}
_GIT_NETWORK = {"fetch", "pull", "push", "clone", "remote", "ls-remote",
                "submodule"}
_NETWORK_COMMANDS = {
    "curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp",
    "ftp", "telnet", "pip", "pip3", "npm", "npx", "yarn", "pnpm",
    "cargo", "gem", "apt", "apt-get", "brew",
}

# Argument tokens that write files, delete, or execute a program no
# matter which nominally read-only command carries them (the generic
# backstop; the per-command screens below add the structural cases).
_DANGEROUS_ARG_TOKENS = {
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprintf",
    "-fls", "-fprint", "-fprint0", "--pre", "--pre-glob",
    "--hostname-bin", "--output", "--ext-diff", "--textconv",
    "--set-upstream-to", "--unset-upstream", "--edit-description",
}
_DANGEROUS_ARG_PREFIXES = ("--output=", "--pre=", "--pre-glob=",
                           "--hostname-bin=", "-fprint")
# (-exec/-execdir are exact tokens above — a prefix rule would also
# catch find's harmless -executable predicate.)
# `git branch` is a read-only listing ONLY in these pure-display
# shapes; any other shape (a positional name, a value-taking flag, a
# delete/move/upstream flag) creates or mutates a branch.
_GIT_BRANCH_DISPLAY_FLAGS = {
    "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--list", "--show-current", "-q", "--quiet", "--color",
    "--no-color", "--column", "--no-column",
}
# `hostname` with no argument (or display-only flags) prints the
# name; a positional argument SETS it, and -F sets it from a file.
_HOSTNAME_DISPLAY_FLAGS = {
    "-f", "--fqdn", "-s", "--short", "-i", "--ip-address",
    "-d", "--domain", "-y", "--yp", "--nis", "-a", "--alias",
}


def _read_only_args_ok(exe: str, args: list[str]) -> bool:
    """Argument-shape screen for the read-only allowlist: True only
    when no argument writes a file, deletes, or executes a program.
    Any doubt returns False (the call classifies as mutating and is
    approval-gated) — the screen fails toward asking.

    The generic token screen runs on lowercased args (the dangerous
    spellings are case-fixed in the tools themselves), but the
    per-command screens below consult the ORIGINAL case wherever the
    tool's own flags are case-sensitive (file -C vs -c, hostname
    -F vs -f mean different things)."""
    original = [str(a) for a in args]
    lowered = [a.lower() for a in original]
    for arg in lowered:
        if arg in _DANGEROUS_ARG_TOKENS:
            return False
        if any(arg.startswith(p) for p in _DANGEROUS_ARG_PREFIXES):
            return False
    if exe == "hostname":
        # A positional argument SETS the hostname; -F/--file sets it
        # from a file (case-sensitive: -f is --fqdn, a display flag).
        if any(a in ("-F", "--file") or a.startswith("--file=")
               for a in original):
            return False
        if any(not a.startswith("-") or a == "-" for a in original):
            return False
        return all(a.lower() in _HOSTNAME_DISPLAY_FLAGS for a in original)
    if exe == "date":
        for arg in lowered:
            if arg == "--set" or arg.startswith("--set="):
                return False
            # Bundled short flags: -s (also -us, -su…) sets the clock.
            if arg.startswith("-") and not arg.startswith("--") \
                    and "s" in arg[1:]:
                return False
        return True
    if exe == "file":
        # -C/--compile WRITES a compiled magic file (case-sensitive:
        # -c is --check, a read). Bundled clusters containing C too.
        for arg in original:
            if arg == "--compile" or arg.startswith("--compile="):
                return False
            if arg.startswith("-") and not arg.startswith("--") \
                    and "C" in arg[1:]:
                return False
        return True
    if exe == "tree":
        # -o writes the listing to a file.
        for arg in lowered:
            if arg == "-o" or (arg.startswith("-")
                               and not arg.startswith("--")
                               and "o" in arg[1:]):
                return False
        return True
    # find/rg/grep and the rest: the generic token screen above is
    # their screen (find -delete/-exec/-ok/-fprintf/-fls/-fprint*,
    # rg --pre/--pre-glob/--hostname-bin all land in it).
    return True


def _git_read_only_args_ok(sub: str, args: list[str]) -> bool:
    """The git subcommand screen: the generic dangerous-token screen
    (already applied to the full argv) plus the structural case —
    `git branch` lists only in pure-display shapes."""
    if sub == "branch":
        return all(str(a).lower() in _GIT_BRANCH_DISPLAY_FLAGS
                   for a in args)
    return True


def classify_shell(argv: list[str]) -> str:
    """'read_only' | 'mutating' | 'network' for an argv vector.
    Anything unrecognized — or recognized with a dangerous argument
    shape — is mutating: the classifier fails toward asking, never
    toward running."""
    if not argv:
        return "mutating"
    exe = Path(str(argv[0])).name.lower()
    if exe in _NETWORK_COMMANDS:
        return "network"
    if exe == "git":
        sub = str(argv[1]).lower() if len(argv) > 1 else ""
        if sub in _GIT_NETWORK:
            return "network"
        if sub not in _GIT_READ_ONLY:
            return "mutating"
        rest = [str(a) for a in argv[2:]]
        lowered = [a.lower() for a in rest]
        for arg in lowered:
            if arg in _DANGEROUS_ARG_TOKENS:
                return "mutating"
            if any(arg.startswith(p) for p in _DANGEROUS_ARG_PREFIXES):
                return "mutating"
        return "read_only" if _git_read_only_args_ok(sub, rest) \
            else "mutating"
    if exe in _READ_ONLY_COMMANDS:
        rest = [str(a) for a in argv[1:]]
        return "read_only" if _read_only_args_ok(exe, rest) else "mutating"
    return "mutating"


def _normalize_argv(args: dict) -> list[str] | None:
    """The argv vector for a shell_run call. Preferred shape:
    {"argv": [...]}. A bare {"command": "..."} string is treated as
    ONE argv element — a single executable name — never split, never
    handed to a shell; its metacharacters are inert."""
    argv = args.get("argv")
    if isinstance(argv, list) and argv:
        return [str(a) for a in argv]
    command = args.get("command")
    if isinstance(command, str) and command:
        extra = args.get("args")
        tail = [str(a) for a in extra] if isinstance(extra, list) else []
        return [command] + tail
    return None


def tool_requires_approval(tool: str, args: dict,
                           auto_approve_rw: bool, *,
                           root: Optional[Path] = None,
                           policy: Optional[PathPolicy] = None
                           ) -> tuple[bool, str]:
    """(needs_approval, reason). The permission gate every tool call
    passes through before execution."""
    if tool in READ_ONLY_TOOLS:
        return False, ""
    if tool in MUTATION_TOOLS:
        # A preview writes nothing, so it never needs approval — the
        # real write still goes through the gate below.
        if tool in ("fs_write", "fs_edit") and args.get("preview"):
            return False, ""
        if policy is not None and policy.roam and root is not None:
            # The Write Law outranks auto-approve: a file outside the
            # home project needs the user's permission for that exact
            # file even in auto-approve mode, and the ask names the
            # file in plain words. AgentQon's own files never park —
            # no approval can allow them; execution refuses them. Any
            # doubt in classification falls through to the normal
            # gate below (which fails toward asking).
            target = _outside_target_for(tool, root, args, policy)
            if target is not None:
                verb = {"fs_delete": "delete",
                        "fs_copy": "copy to",
                        "fs_move": "move to",
                        "fs_rename": "move to",
                        "fs_mkdir": "create a folder"}.get(
                            tool, "write")
                return True, (f"{verb} outside your project: "
                              f"{target}")
            try:
                check_rel = str(args.get(
                    "destination", args.get("dest", ""))) \
                    if tool in ("fs_copy", "fs_move", "fs_rename") \
                    else str(args.get("path", ""))
                _, inside_home = resolve_write_path(
                    root, check_rel, policy)
            except OwnFilesRefused:
                return False, ""
            except Exception:
                pass  # unclassifiable — the normal gate decides
            else:
                if not inside_home and tool != "fs_delete":
                    # A granted outside file: the user's grant IS
                    # the permission for that file — no second ask.
                    # (fs_delete is the exception: it always asks,
                    # even for a granted file — see below.)
                    return False, ""
        if tool == "fs_delete":
            # PROPOSAL (W8, for review before integration):
            # deleting always asks, even in auto-approve mode.
            # Deletion is irreversible, and the session prompt
            # already promises approval for that exact file; one
            # ask per (rare) delete is the safe default. If the
            # reviewer prefers auto-approve coverage for deletes,
            # this branch is the one line to change.
            return True, (
                f"delete this file: {args.get('path', '')} — "
                "deleting needs your approval for that exact file")
        if auto_approve_rw:
            return False, ""
        # Manual mode: an approval that fires for overwriting names
        # the existing file, so the approver knows what is at stake.
        if tool in ("fs_write", "fs_edit", "fs_copy", "fs_move",
                    "fs_rename"):
            dest_rel = str(args.get(
                "destination", args.get("dest", ""))) \
                if tool in ("fs_copy", "fs_move", "fs_rename") \
                else str(args.get("path", ""))
            try:
                if policy is not None and policy.roam \
                        and root is not None:
                    existing, _ = resolve_write_path(
                        root, dest_rel, policy)
                elif root is not None:
                    existing = resolve_in_workspace(root, dest_rel)
                else:
                    existing = None
                if existing is not None and existing.is_file():
                    return True, (
                        f"{tool} would overwrite the existing "
                        f"file: {existing}")
            except Exception:
                pass
        return True, f"{tool} modifies files in the workspace"
    if tool == "shell_run":
        argv = _normalize_argv(args)
        if argv is None:
            return True, "shell_run without a valid argv"
        kind = classify_shell(argv)
        if kind == "read_only":
            return False, ""
        if kind == "network":
            return True, (f"shell command reaches the network: "
                          f"{Path(argv[0]).name}")
        if auto_approve_rw:
            return False, ""
        return True, f"shell command may modify the system: {argv[0]}"
    return True, f"unknown tool {tool!r} requires approval"


# ---------------------------------------------------------------- tools
def _err(message: str, **extra) -> dict:
    return {"ok": False, "error": message, **extra}


def _parse_nonneg_int(value: Any, name: str) -> Optional[int]:
    """A whole-number argument (True/False do not count), or None
    when the argument was absent. Raises PlainToolError in plain
    words for anything else."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlainToolError(
            f"{name} must be a whole number")
    if value < 0:
        raise PlainToolError(f"{name} cannot be negative")
    return value


def _fs_read(root: Path, args: dict,
             policy: Optional[PathPolicy] = None) -> dict:
    path = resolve_read_path(root, str(args.get("path", "")), policy)
    if not path.exists():
        return _err(f"no such file: {args.get('path')}")
    if path.is_dir():
        return _err(f"that path is a folder, not a file: "
                    f"{args.get('path')}")
    raw = path.read_bytes()
    if _is_binary(raw):
        return _err("this file is not readable text, so it cannot "
                    "be shown as text", binary=True, size=len(raw))
    text = raw.decode("utf-8")
    # Paging: offset/limit are LINES (offset = lines skipped,
    # limit = lines returned). The line window is taken first, then
    # the 20,000-character cap — so a long file is readable in
    # full, window by window, and `truncated` is True whenever
    # either cut withheld anything. A capped read SAYS it is
    # partial, in plain words (W5 section 3-53): the note names
    # what was actually shown — the window, or the character cap.
    offset = _parse_nonneg_int(args.get("offset"), "offset") or 0
    limit = _parse_nonneg_int(args.get("limit"), "limit")
    lines = text.splitlines(keepends=True)
    window = lines[offset:] if limit is None \
        else lines[offset:offset + limit]
    window_text = "".join(window)
    line_cut = offset + len(window) < len(lines)
    char_cut = len(window_text) > MAX_READ_CHARS
    truncated = line_cut or char_cut
    note = ""
    if truncated:
        if char_cut and offset == 0 and limit is None:
            note = (f"content cut short here: showing the first "
                    f"{MAX_READ_CHARS:,} characters of {len(text):,} "
                    "in total")
        elif char_cut:
            note = (f"content cut short here: showing the first "
                    f"{MAX_READ_CHARS:,} characters of the selected "
                    "lines")
        else:
            note = (f"content cut short here: showing lines "
                    f"{offset + 1} to {offset + len(window)} of "
                    f"{len(lines):,} lines in total")
    return {"ok": True, "path": _rel(root, path),
            "content": window_text[:MAX_READ_CHARS],
            "truncated": truncated, "note": note, "chars": len(text),
            "total_lines": len(lines), "offset": offset,
            "limit": limit,
            "lines_returned": len(window)}


def _pinned_write(anchor: Path, target: Path, content: str,
                  hop_ok: Callable[[Path], bool],
                  hop_refusal: str) -> Path:
    """Write `content` to the already-proven absolute `target`,
    hardened against the check-then-write symlink race (audit F10).

    On platforms with dir_fd support the write walks the path by
    directory FILE DESCRIPTORS from `anchor`: each parent component
    is opened relative to the previous directory's fd with
    O_NOFOLLOW, so a component swapped for a symlink mid-write fails
    the open instead of redirecting the write — the fds pin the
    actual directories traversed. A leaf that is itself a symlink is
    followed one hop at a time, by hand: the hop target must satisfy
    hop_ok, and the target is then written through the same pinned
    walk. Where dir_fd is unavailable (e.g. Windows), the proven
    path is written directly."""
    data = content.encode("utf-8")
    if os.open not in os.supports_dir_fd \
            or not hasattr(os, "O_NOFOLLOW"):
        target.write_bytes(data)
        return target
    anchor_resolved = Path(anchor).resolve()
    parts = target.relative_to(anchor_resolved).parts
    dir_fd = os.open(anchor_resolved, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY
                              | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        leaf = parts[-1] if parts else ""
        try:
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            # Leaf is a symlink: resolve its target, prove the hop
            # is allowed to land there, and write the target directly
            # (its own leaf open is still O_NOFOLLOW via the
            # recursive call's walk).
            target2 = Path(os.readlink(leaf, dir_fd=dir_fd))
            if not target2.is_absolute():
                target2 = (target.parent / target2)
            target2 = target2.resolve()
            if not hop_ok(target2):
                raise WorkspaceEscape(hop_refusal)
            return _pinned_write(anchor, target2, content,
                                 hop_ok, hop_refusal)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return target
    finally:
        os.close(dir_fd)


def _preview_write(root: Path, args: dict, content: str,
                   append: bool,
                   policy: Optional[PathPolicy] = None) -> dict:
    """The dry-run for fs_write: what WOULD change, without writing
    anything — no file, parent, backup, or lock is created. The
    preview is advisory: the file may change between preview and
    the real write. It still refuses AgentQon's own files (read
    policy) but does not need a write grant, because it changes
    nothing."""
    rel = str(args.get("path", ""))
    path = resolve_read_path(root, rel, policy)
    if path.is_dir():
        return _err(f"that path is a folder, not a file: {rel}")
    existed = path.is_file()
    old_text = None
    diff_note = None
    if existed:
        raw = path.read_bytes()
        if _is_binary(raw):
            diff_note = ("the existing file is not readable text, "
                         "so no diff can be shown for it")
        else:
            old_text = raw.decode("utf-8")
    new_text = (old_text + content) if (append and old_text) else content
    diff, diff_truncated = _diff_text(
        old_text, new_text, from_label="before", to_label="after")
    backup, backup_note = (None, None)
    if existed:
        backup_path = _permitted_backup_path(root, path, policy)
        if backup_path is None:
            backup_note = ("no backup would be kept because its "
                           "backup location is outside the files "
                           "this session may change")
        else:
            backup = _rel(root, backup_path)
    return {"ok": True, "preview": True, "path": _rel(root, path),
            "created": not existed, "overwritten": existed,
            "appended": bool(append),
            "bytes": len(content.encode("utf-8")),
            "diff": diff, "diff_truncated": diff_truncated,
            "diff_note": diff_note, "backup": backup,
            "backup_note": backup_note}


def _fs_write(root: Path, args: dict,
              policy: Optional[PathPolicy] = None,
              approved_paths: Optional[set] = None) -> dict:
    content = args.get("content")
    if not isinstance(content, str):
        return _err("fs_write requires string 'content'")
    if len(content) > MAX_WRITE_CHARS:
        return _err("this content is too long to write in one go "
                    f"(limit {MAX_WRITE_CHARS:,} characters). "
                    "Split it into smaller writes.")
    append = bool(args.get("append"))
    if args.get("preview"):
        return _preview_write(root, args, content, append, policy)
    eff_policy = _effective_policy(policy, approved_paths)
    resolved, info = _do_write(
        root, str(args.get("path", "")), eff_policy,
        content.encode("utf-8"), mode=0o644, append=append)
    return {"ok": True, "path": _rel(root, resolved),
            "bytes": len(content.encode("utf-8")),
            "appended": append, **info}


def _fs_edit(root: Path, args: dict,
             policy: Optional[PathPolicy] = None,
             approved_paths: Optional[set] = None) -> dict:
    path = resolve_read_path(root, str(args.get("path", "")), policy)
    if not path.exists():
        return _err(f"no such file: {args.get('path')}")
    if path.is_dir():
        return _err(f"that path is a folder, not a file: "
                    f"{args.get('path')}")
    old, new = args.get("old"), args.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        return _err("fs_edit requires non-empty string 'old' and string 'new'")
    # Strict decode: a non-UTF-8 file is refused, never read with
    # replacement characters and written back mangled (audit item
    # 17 — the old code corrupted every non-UTF-8 byte it touched).
    raw = path.read_bytes()
    if _is_binary(raw):
        return _err("this file is not readable text, so it cannot "
                    "be edited as text — editing it would corrupt "
                    "bytes the edit does not touch",
                    binary=True, size=len(raw))
    text = raw.decode("utf-8")
    count = text.count(old)
    if count != 1:
        return _err(f"fs_edit anchor occurs {count} times "
                    "(exactly 1 required); nothing changed")
    replaced = text.replace(old, new)
    if len(replaced) > MAX_WRITE_CHARS:
        return _err("the file after this edit would be too long "
                    f"(limit {MAX_WRITE_CHARS:,} characters). "
                    "Make the change in smaller steps.")
    if args.get("preview"):
        diff, diff_truncated = _diff_text(
            text, replaced, from_label="before", to_label="after")
        backup, backup_note = (None, None)
        backup_path = _permitted_backup_path(root, path, policy)
        if backup_path is None:
            backup_note = ("no backup would be kept because its "
                           "backup location is outside the files "
                           "this session may change")
        else:
            backup = _rel(root, backup_path)
        return {"ok": True, "preview": True,
                "path": _rel(root, path), "created": False,
                "overwritten": True, "replacements": 1,
                "diff": diff, "diff_truncated": diff_truncated,
                "backup": backup, "backup_note": backup_note}
    eff_policy = _effective_policy(policy, approved_paths)
    resolved, info = _do_write(
        root, str(args.get("path", "")), eff_policy,
        replaced.encode("utf-8"), mode=0o644)
    return {"ok": True, "path": _rel(root, resolved),
            "replacements": 1, **info}


def _fs_list(root: Path, args: dict,
             policy: Optional[PathPolicy] = None) -> dict:
    path = resolve_read_path(root, str(args.get("path", ".")), policy)
    if not path.is_dir():
        return _err(f"not a directory: {args.get('path', '.')}")
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: p.name):
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        entries.append({"name": child.name,
                        "type": "dir" if is_dir else "file", "size": size})
    # Full collection, not an early break: `total` is the exact
    # entry count, so `truncated` (total over the cap) is honest.
    # The listing was already materialized for sorting, so the
    # only cost of the full scan is per-entry stat calls.
    total = len(entries)
    truncated = total > MAX_LIST_ENTRIES
    # W5 §3-53/C4: a capped listing SAYS it is partial, in plain words.
    note = ""
    if truncated:
        note = (f"listing cut short here: showing the first "
                f"{MAX_LIST_ENTRIES} entries")
    return {"ok": True, "path": _rel(root, path),
            "entries": entries[:MAX_LIST_ENTRIES],
            "count": min(total, MAX_LIST_ENTRIES), "total": total,
            "truncated": truncated, "note": note}


def _fs_search(root: Path, args: dict, data_dir,
               policy: Optional[PathPolicy] = None) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return _err("fs_search requires a 'query'")
    base = resolve_read_path(root, str(args.get("path", ".")), policy)
    matches: list[dict] = []
    needle = query.lower()
    if base.is_file():
        files = [base]
    elif base.is_dir():
        files = [p for p in base.rglob("*") if p.is_file()]
    else:
        return _err(f"no such path: {args.get('path', '.')}")
    total_matches = 0
    for path in files:
        try:
            if policy is not None and policy.roam:
                # Never read through a file-symlink into AgentQon's
                # own files, wherever the search is walking.
                if _own_files_zone(path.resolve(),
                                   Path(root).resolve(), policy):
                    continue
            else:
                # Never follow a file-symlink out of the workspace.
                resolve_in_workspace(root, _rel(root, path))
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
        except (WorkspaceEscape, OSError, RuntimeError,
                UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                total_matches += 1
                if len(matches) < MAX_SEARCH_MATCHES:
                    matches.append({"path": _rel(root, path),
                                    "line": lineno,
                                    "text": line.strip()[:200],
                                    "kind": "text"})
    # The scan completes (oversize and undecodable files are still
    # skipped, as before), so `total` is exact and `truncated` is
    # honest — unlike the old silent 50-match cut. A capped match
    # list SAYS it is partial, in plain words (W5 section 3-53).
    # An empty match list is SUCCESS with zero results (C11),
    # not failure — ok stays True with count 0.
    truncated = total_matches > MAX_SEARCH_MATCHES
    note = ""
    if truncated:
        note = (f"matches cut short here: showing at most the first "
                f"{MAX_SEARCH_MATCHES} matches")
    result: dict[str, Any] = {"ok": True, "query": query,
                              "matches": matches, "count": len(matches),
                              "total": total_matches,
                              "truncated": truncated, "note": note}
    # Codegraph-aware: when a graph exists for this workspace, symbol
    # matches ride along (built on demand by the code tools elsewhere;
    # here we only READ an existing graph — a search must stay fast).
    if data_dir is not None:
        try:
            from .codegraph import CodeGraph, graph_db_path
            db = graph_db_path(data_dir, root)
            if Path(db).exists():
                found = CodeGraph(db).search(query, limit=25)
                symbols = [{"path": r["path"], "line": r["line"],
                            "text": f"{r['kind']} {r['name']}",
                            "kind": "symbol"}
                           for r in found.get("results", [])]
                result["symbols"] = symbols
        except Exception:
            pass  # the graph is an enhancement, never a failure mode
    return result


def _fs_copy(root: Path, args: dict,
             policy: Optional[PathPolicy] = None,
             approved_paths: Optional[set] = None) -> dict:
    """Copy one FILE (folders are refused plainly — no recursive
    copy). The source is read under the read policy, the
    destination is written through _do_write, so the destination
    obeys the same enforcement as fs_write: inside the home project
    free, outside only the exact granted file, AgentQon's own files
    refused even with a grant, with backup and mode preservation
    (the source mode, even over an existing destination)."""
    src_arg = str(args.get("path", ""))
    dest_arg = str(args.get("destination", args.get("dest", "")))
    if not dest_arg:
        return _err("fs_copy requires 'path' and 'destination'")
    src = resolve_read_path(root, src_arg, policy)
    if not src.exists():
        return _err(f"no such file: {src_arg}")
    if src.is_dir():
        return _err(f"that path is a folder, not a file: {src_arg}")
    if src.stat().st_size > MAX_COPY_BYTES:
        return _err("that file is too large to copy "
                    f"(limit {MAX_COPY_BYTES:,} bytes)")
    eff_policy = _effective_policy(policy, approved_paths)
    dest_resolved, _ = _write_target_check(root, dest_arg, eff_policy)
    if dest_resolved.is_dir():
        return _err(f"that path is a folder, not a file: {dest_arg}")
    if src.resolve() == dest_resolved:
        return _err("the source and destination are the same file")
    data = src.read_bytes()
    mode = stat.S_IMODE(src.stat().st_mode)
    resolved, info = _do_write(root, dest_arg, eff_policy, data,
                               mode=mode, force_mode=True)
    return {"ok": True, "path": _rel(root, src),
            "destination": _rel(root, resolved),
            "bytes": len(data), "mode": oct(mode), **info}


def _write_target_check(root: Path, rel: str,
                        policy: Optional[PathPolicy]) -> tuple[Path, bool]:
    """The resolved destination of a copy/move under the policy
    (for the pre-checks in _fs_copy/_fs_move). Raises the policy
    exceptions exactly as _do_write would."""
    if policy is not None and policy.roam:
        return resolve_write_path(root, rel, policy)
    return resolve_in_workspace(root, rel), True


def _fs_move(root: Path, args: dict,
             policy: Optional[PathPolicy] = None,
             approved_paths: Optional[set] = None,
             tool: str = "fs_move") -> dict:
    """Move (fs_rename: same folder, new name) one file. Honestly
    copy-then-delete, never a bare rename: the destination is
    written atomically first (with backup and the source mode), and
    only then is the source unlinked — so a failed destination
    write leaves the source untouched, and a failed unlink after a
    successful write is reported plainly as a partial move (both
    files present) rather than hidden. Folders and symlinks are
    refused like fs_delete refuses them; the source must itself be
    deletable under the policy, not just readable."""
    src_arg = str(args.get("path", ""))
    dest_arg = str(args.get("destination", args.get("dest", "")))
    if not dest_arg:
        # Bug 9: fs_rename is an alias of fs_move sharing this
        # validator — name the tool actually called, so the error
        # does not misdirect the model (or the human reading the
        # transcript) to a different tool.
        return _err(f"{tool} requires 'path' and 'destination'")
    src = resolve_read_path(root, src_arg, policy)
    if not src.exists():
        return _err(f"no such file: {src_arg}")
    if src.is_dir():
        return _err(f"that path is a folder, not a file: {src_arg}")
    if src.stat().st_size > MAX_COPY_BYTES:
        return _err("that file is too large to move "
                    f"(limit {MAX_COPY_BYTES:,} bytes)")
    eff_policy = _effective_policy(policy, approved_paths)
    dest_resolved, _ = _write_target_check(root, dest_arg, eff_policy)
    if dest_resolved.is_dir():
        return _err(f"that path is a folder, not a file: {dest_arg}")
    if src.resolve() == dest_resolved:
        return _err("the source and destination are the same file")
    # The source must be deletable under the policy (inside the
    # home project, or the exact granted/approved file) — a
    # readable-but-unwritable outside file cannot be moved away.
    try:
        if eff_policy is not None and eff_policy.roam:
            resolve_write_path(root, src_arg, eff_policy)
        else:
            resolve_in_workspace(root, src_arg)
    except (OwnFilesRefused, OutsideWriteRefused):
        raise
    lexical = Path(src_arg) if Path(src_arg).is_absolute() \
        else Path(root).resolve() / src_arg
    if os.path.islink(lexical):
        return _err("that path is a link, not a file — moving a "
                    "link is not supported, so nothing was moved")
    data = src.read_bytes()
    mode = stat.S_IMODE(src.stat().st_mode)
    resolved, info = _do_write(root, dest_arg, eff_policy, data,
                               mode=mode, force_mode=True)
    try:
        _delete_file(root, src_arg, eff_policy)
    except Exception:
        return _err("the file was copied but its original could "
                    "not be removed, so both files are still there",
                    destination_written=True, source_removed=False,
                    destination=_rel(root, resolved))
    return {"ok": True, "path": _rel(root, src),
            "destination": _rel(root, resolved),
            "bytes": len(data), "mode": oct(mode),
            "source_removed": True, **info}


def _fs_delete(root: Path, args: dict,
               policy: Optional[PathPolicy] = None,
               approved_paths: Optional[set] = None) -> dict:
    """Delete one file through the typed tool (the old shell-only
    design is superseded). The approval gate always asks for a
    delete, even in auto-approve mode; execution still enforces
    the policy for direct callers. Folders and links are refused."""
    eff_policy = _effective_policy(policy, approved_paths)
    resolved = _delete_file(root, str(args.get("path", "")), eff_policy)
    return {"ok": True, "path": _rel(root, resolved),
            "deleted": True}


def _fs_mkdir(root: Path, args: dict,
              policy: Optional[PathPolicy] = None,
              approved_paths: Optional[set] = None) -> dict:
    """Create a folder (parents included, like fs_write's parent
    creation). An outside folder needs the loop's approval for
    that exact folder; the approval is one-shot and grants no file
    writes inside it. Creating an existing folder is a success
    that reports it already existed."""
    rel = str(args.get("path", ""))
    if not rel or rel == ".":
        return _err("fs_mkdir requires a folder 'path'")
    eff_policy = _effective_policy(policy, approved_paths)
    if eff_policy is not None and eff_policy.roam:
        resolved, _ = resolve_write_path(root, rel, eff_policy)
    else:
        resolved = resolve_in_workspace(root, rel)
    if resolved.is_file():
        return _err(f"that path is already a file: {rel}")
    existed = resolved.is_dir()
    resolved.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": _rel(root, resolved),
            "created": not existed, "already_exists": existed}


def _fs_stat(root: Path, args: dict,
             policy: Optional[PathPolicy] = None) -> dict:
    """File metadata under the read policy (size, modified time,
    mode, type) — the typed answer to 'is this file executable /
    how big / when did it change', without shell. A dedicated tool
    rather than fs_list enrichment: fs_list only covers a listed
    folder's children, while stat must answer for any single path
    (including a roam read outside the home project), and stuffing
    mode+mtime into every listing entry would bloat the
    observations the loop caps at 4,000 characters."""
    from datetime import datetime, timezone
    rel = str(args.get("path", ""))
    path = resolve_read_path(root, rel, policy)
    if not path.exists():
        return _err(f"no such file: {rel}")
    root_resolved = Path(root).resolve()
    lexical = Path(rel) if Path(rel).is_absolute() \
        else root_resolved / rel
    is_link = os.path.islink(lexical)
    st = path.stat()
    if st.st_mode and stat.S_ISDIR(st.st_mode):
        kind = "dir"
    elif stat.S_ISREG(st.st_mode):
        kind = "file"
    else:
        kind = "other"
    result: dict[str, Any] = {
        "ok": True, "path": _rel(root, path), "type": kind,
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc).isoformat(),
        "mtime_epoch": st.st_mtime,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "link": is_link}
    if is_link:
        result["target"] = str(path)
    return result


def _shell_escape_arg(root: Path, argv: list[str]) -> Optional[str]:
    """Hosted-mode shell confinement (audit F3): the first argv
    element that would take the command OUTSIDE the workspace, or
    None. A path-bearing argument — an absolute path, a ./ or ../
    path, a ~ path, or any argument containing a path separator, plus
    the value side of --flag=value — must resolve inside the root.
    The executable itself (argv[0]) may be a bare PATH name, but an
    argv[0] carrying a path must also resolve inside the root."""
    root_resolved = Path(root).resolve()

    def _inside(candidate: str) -> bool:
        p = Path(candidate).expanduser()
        if not p.is_absolute():
            p = root_resolved / p
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved == root_resolved or root_resolved in resolved.parents

    for i, raw in enumerate(argv):
        arg = str(raw)
        candidates = [arg]
        if arg.startswith("--") and "=" in arg:
            candidates.append(arg.split("=", 1)[1])
        for cand in candidates:
            if not cand or cand == "-":
                continue
            if i == 0:
                if ("/" in cand or cand in (".", "..")) and not _inside(cand):
                    return arg
                continue
            if cand.startswith(("/", "./", "../", "~/")) or cand in (".", "..") \
                    or "/" in cand:
                if not _inside(cand):
                    return arg
    return None


async def _shell_run(root: Path, args: dict, *,
                     confine_paths: bool = False,
                     cancel_handle=None,
                     resource_limits: bool = False) -> dict:
    """W5 §3 hardening on top of the existing executor:
    * env is FILTERED (§3-49) — secret-looking variables never reach
      the child process (see tooldefs.filter_env).
    * stdout and stderr stay separate (§3-51); the exit code is the
      verdict (§3-52): ok is True only when returncode == 0, and
      success-looking text never overrides a non-zero exit.
    * truncation is MARKED (§3-53/C4) with a plain-words note.
    * duration is measured (§3-54); the outcome vocabulary is
      success | failure | timeout | cancelled (§3, top-15 #2) — a
      timeout is recorded as outcome "timeout" with timed_out True,
      never as failure proof and never as success.
    * cancellation propagates to the child (§3-56): a cancelled
      CancelHandle kills the process and the outcome is "cancelled".
    * resource_limits=True adds POSIX CPU/memory caps via
      preexec_fn (§3-55); where unsupported it is None and the time
      bound still applies (recorded in the result)."""
    argv = _normalize_argv(args)
    if argv is None:
        return _err("shell_run requires 'argv' (a list of arguments)",
                    outcome="failure")
    if confine_paths:
        escape = _shell_escape_arg(root, argv)
        if escape is not None:
            return _err(
                f"refused: shell argument {escape!r} escapes the "
                "session workspace (hosted-mode confinement)",
                refused="workspace_escape", outcome="failure")
    try:
        timeout = float(args.get("timeout_s", DEFAULT_SHELL_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout = DEFAULT_SHELL_TIMEOUT_S
    timeout = min(max(timeout, 1.0), MAX_SHELL_TIMEOUT_S)
    # Combined W2 + W5 shell execution (three-way resolution):
    # - Default POSIX path: W2's sandbox runner (scrubbed
    #   environment + CPU/memory limits in the child + timeout).
    #   W5's result fields are mapped onto it (duration,
    #   truncation flags with the plain-words note, outcome) and
    #   W8's true totals (stdout_chars/stderr_chars, reported by
    #   the sandbox alongside the capped text) so a capped
    #   result says exactly how much was withheld.
    # - Direct-spawn path: Windows (no rlimits to offer), and any
    #   POSIX call carrying a cancel_handle — a thread-run
    #   sandbox child cannot be killed, so cancellation
    #   propagation (section 3-56) requires the direct child the
    #   watcher can kill. Stated, not hidden.
    started = time.monotonic()
    if sys.platform != "win32" and cancel_handle is None:
        from .sandbox import run_sandboxed
        result = await asyncio.to_thread(
            run_sandboxed, argv, cwd=Path(root).resolve(),
            timeout_s=timeout)
        duration_ms = round((time.monotonic() - started) * 1000, 3)
        if result.get("timed_out"):
            return _err(result["error"], argv=argv, timed_out=True,
                        outcome="timeout", duration_ms=duration_ms)
        if not result.get("ok") and "returncode" not in result:
            return _err(result["error"], argv=argv, duration_ms=duration_ms)
        raw_out = str(result.get("stdout", ""))
        raw_err = str(result.get("stderr", ""))
        out_chars = result.get("stdout_chars", len(raw_out))
        err_chars = result.get("stderr_chars", len(raw_err))
        stdout_trunc = out_chars > MAX_OUTPUT_CHARS
        stderr_trunc = err_chars > MAX_OUTPUT_CHARS
        note = ""
        if stdout_trunc or stderr_trunc:
            note = ("output cut short here: showing the first "
                    f"{MAX_OUTPUT_CHARS:,} characters of each stream")
        limits = result.get("limits_applied", [])
        return {"ok": result["ok"], "returncode": result["returncode"],
                "outcome": "success" if result["ok"] else "failure",
                "stdout": raw_out[:MAX_OUTPUT_CHARS],
                "stderr": raw_err[:MAX_OUTPUT_CHARS],
                "stdout_truncated": stdout_trunc,
                "stderr_truncated": stderr_trunc,
                "note": note, "argv": argv,
                "limits_applied": limits,
                "resource_limits_applied": bool(limits),
                "stdout_chars": out_chars, "stderr_chars": err_chars,
                "duration_ms": duration_ms}
    preexec = _resource_preexec() if resource_limits else None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(Path(root).resolve()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_filter_env(), preexec_fn=preexec)
    except FileNotFoundError:
        return _err(f"executable not found: {argv[0]}", outcome="failure")
    except OSError as exc:
        return _err(f"could not start {argv[0]}: {exc}", outcome="failure")

    async def _watch_cancel() -> None:
        while proc.returncode is None:
            if cancel_handle is not None and cancel_handle.is_cancelled():
                proc.kill()
                return
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(_watch_cancel())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return _err(f"command timed out after {timeout:g}s", argv=argv,
                    timed_out=True, outcome="timeout",
                    duration_ms=round((time.monotonic() - started) * 1000, 3))
    finally:
        watcher.cancel()
    if cancel_handle is not None and cancel_handle.is_cancelled():
        return _err("command was cancelled", argv=argv, outcome="cancelled",
                    returncode=proc.returncode,
                    duration_ms=round((time.monotonic() - started) * 1000, 3))
    raw_out = out.decode("utf-8", errors="replace")
    raw_err = err.decode("utf-8", errors="replace")
    stdout_trunc = len(raw_out) > MAX_OUTPUT_CHARS
    stderr_trunc = len(raw_err) > MAX_OUTPUT_CHARS
    note = ""
    if stdout_trunc or stderr_trunc:
        note = ("output cut short here: showing the first "
                f"{MAX_OUTPUT_CHARS:,} characters of each stream")
    ok = proc.returncode == 0
    return {"ok": ok, "returncode": proc.returncode,
            "outcome": "success" if ok else "failure",
            "stdout": raw_out[:MAX_OUTPUT_CHARS],
            "stderr": raw_err[:MAX_OUTPUT_CHARS],
            "stdout_truncated": stdout_trunc, "stderr_truncated": stderr_trunc,
            "stdout_chars": len(raw_out), "stderr_chars": len(raw_err),
            "note": note, "argv": argv,
            "resource_limits_applied": bool(preexec),
            "duration_ms": round((time.monotonic() - started) * 1000, 3)}


async def _web_read(root: Path, args: dict, *,
                    reach_channels: Optional[set] = None,
                    web_audit: Optional[Callable] = None) -> dict:
    url = str(args.get("url", ""))
    if not url:
        return _err("web_read requires a 'url'")
    # Audit F4: agent-session web_read goes through the SAME gateway
    # channel-enablement the mission flow enforces (app/gateway.py:
    # a reach tool whose channel the operator has not enabled is
    # denied as not_configured) — it no longer reaches reach.execute
    # directly. reach_channels=None is the legacy direct-call shape
    # (unit seams); the production runner always passes the set.
    if reach_channels is not None:
        from .reach import channel_for
        channel = channel_for("reach.read")
        if channel not in reach_channels:
            denied = {"status": "not_configured", "tool": "reach.read",
                      "channel": channel,
                      "reason": "REACH_NOT_CONFIGURED",
                      "hint": "enable with AGENTQON_REACH_CHANNELS"}
            if web_audit is not None:
                web_audit({"url": url}, denied)
            return _err("web_read failed: not_configured: the web "
                        "reach channel is not enabled on this "
                        "deployment (AGENTQON_REACH_CHANNELS)",
                        status="not_configured")
    from .reach import execute as reach_execute
    result = reach_execute("reach.read", {"url": url})
    if web_audit is not None:
        # The mission gateway's audit discipline: the record carries
        # the redacted, truncated audit view — never a second full
        # copy of the fetched content.
        from .reach import audit_view
        web_audit({"url": url}, audit_view(result))
    if result.get("status") != "ok":
        return _err(f"web_read failed: {result.get('status')}: "
                    f"{result.get('reason', '')}".strip(),
                    status=result.get("status"))
    full_text = str(result.get("text", ""))
    truncated = len(full_text) > MAX_READ_CHARS
    note = ""
    if truncated:
        note = (f"text cut short here: showing the first "
                f"{MAX_READ_CHARS:,} characters of {len(full_text):,}")
    # Gap-security: retrieved web content is marked untrusted at the
    # boundary — it is data, never instructions (app/injection.py).
    # Callers that feed it into decisions must treat it as such.
    return {"ok": True, "url": url, "title": result.get("title", ""),
            "text": full_text[:MAX_READ_CHARS], "truncated": truncated,
            "note": note, "chars": result.get("chars", len(full_text)),
            "untrusted": True, "source": url, "source_kind": "web"}


async def execute_tool(tool: str, args: dict, *, root: Path,
                       data_dir=None, confine_paths: bool = False,
                       reach_channels: Optional[set] = None,
                       web_audit: Optional[Callable] = None,
                       policy: Optional[PathPolicy] = None,
                       untrusted=None,
                       allowed_tools: Optional[tuple] = None,
                       cancel_handle=None,
                       resource_limits: bool = False,
                       approved_paths: Optional[set] = None) -> dict:
    """Dispatch one tool call. Path confinement is enforced per tool
    under `policy`; a refused path is a refused result (ok=False),
    and the refusal is what the agent observes. No policy (or a
    jailed one) is the pre-roam posture: every path stays inside the
    workspace root. `confine_paths` adds the hosted-mode shell
    confinement (F3); `reach_channels` + `web_audit` route web_read
    through the gateway's channel enablement + audit path (F4).
    `untrusted` (gap-security) is a list of injection.UntrustedText
    items the call may derive from: when a sensitive tool call
    carries a hostile instruction from that content, the injection
    guard refuses it at this boundary — backend enforcement, not UI
    hiding. Callers that do not pass it keep the previous behaviour.
    W5-5: when `allowed_tools` is given, a tool outside the list is
    REFUSED here at execution (ok=False, refused="tool_not_allowed")
    — the per-task filter is enforced, not suggested. `cancel_handle`
    propagates caller cancellation into shell_run (§3-56);
    `resource_limits` adds POSIX CPU/memory caps (§3-55).
    `approved_paths` carries one-shot approvals the loop just
    granted for this call only — they never persist as grants."""
    args = args if isinstance(args, dict) else {}
    # Guard order (fixed, both enforced): injection first — a hostile
    # instruction in retrieved content refuses the call at this
    # boundary whatever the task list says — then the per-task
    # tool filter.
    if untrusted is not None:
        from .injection import guard_tool_call
        allowed, reason = guard_tool_call(tool, args, untrusted)
        if not allowed:
            return _err(reason, refused="injection")
    if allowed_tools is not None and tool not in allowed_tools:
        return _err(f"refused: tool {tool!r} is not allowed for this "
                    f"task — the task's tool list does not include it",
                    refused="tool_not_allowed", outcome="failure")
    try:
        if tool == "fs_read":
            return _fs_read(root, args, policy)
        if tool == "fs_write":
            return _fs_write(root, args, policy, approved_paths)
        if tool == "fs_edit":
            return _fs_edit(root, args, policy, approved_paths)
        if tool == "fs_list":
            return _fs_list(root, args, policy)
        if tool == "fs_search":
            return _fs_search(root, args, data_dir, policy)
        if tool == "fs_copy":
            return _fs_copy(root, args, policy, approved_paths)
        if tool in ("fs_move", "fs_rename"):
            return _fs_move(root, args, policy, approved_paths,
                            tool=tool)
        if tool == "fs_delete":
            return _fs_delete(root, args, policy, approved_paths)
        if tool == "fs_mkdir":
            return _fs_mkdir(root, args, policy, approved_paths)
        if tool == "fs_stat":
            return _fs_stat(root, args, policy)
        if tool == "shell_run":
            return await _shell_run(root, args, confine_paths=confine_paths,
                                    cancel_handle=cancel_handle,
                                    resource_limits=resource_limits)
        if tool == "web_read":
            return await _web_read(root, args,
                                   reach_channels=reach_channels,
                                   web_audit=web_audit)
    except WorkspaceEscape as exc:
        return _err(f"refused: {exc}", refused="workspace_escape")
    except OwnFilesRefused as exc:
        return _err(f"refused: {exc}", refused="own_files")
    except OutsideWriteRefused as exc:
        return _err(f"refused: {exc}", refused="no_permission",
                    path=str(exc.path))
    except PlainToolError as exc:
        return _err(str(exc))
    except LockTimeout as exc:
        return _err(str(exc), lock_timeout=True)
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            return _err("that path is too long for this computer "
                        "to use — shorten a folder or file name")
        return _err(f"tool error: {type(exc).__name__}: {exc}")
    except Exception as exc:  # a tool bug must surface as an observation
        return _err(f"tool error: {type(exc).__name__}: {exc}")
    return _err(f"unknown tool: {tool}")
