"""AgentQon — agent-session tools (v22, R1).

The tools an agent session may call. Every tool is confined to the
session's workspace root: a path that escapes it — via `..`, an
absolute path outside it, or a symlink pointing out — is REFUSED,
never followed. Shell execution is argv-only (a list, executed
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
import errno
import os
from pathlib import Path
from typing import Any, Callable, Optional

MAX_READ_CHARS = 20_000
MAX_OUTPUT_CHARS = 8_000
MAX_LIST_ENTRIES = 200
MAX_SEARCH_MATCHES = 50
MAX_SEARCH_FILE_BYTES = 512_000
DEFAULT_SHELL_TIMEOUT_S = 60.0
MAX_SHELL_TIMEOUT_S = 300.0

TOOLS = ("fs_read", "fs_write", "fs_edit", "fs_list", "fs_search",
         "shell_run", "web_read")
READ_ONLY_TOOLS = ("fs_read", "fs_list", "fs_search", "web_read")


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
                           auto_approve_rw: bool) -> tuple[bool, str]:
    """(needs_approval, reason). The permission gate every tool call
    passes through before execution."""
    if tool in READ_ONLY_TOOLS:
        return False, ""
    if tool in ("fs_write", "fs_edit"):
        if auto_approve_rw:
            return False, ""
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


def _fs_read(root: Path, args: dict) -> dict:
    path = resolve_in_workspace(root, str(args.get("path", "")))
    if not path.is_file():
        return _err(f"no such file: {args.get('path')}")
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_READ_CHARS
    return {"ok": True, "path": _rel(root, path),
            "content": text[:MAX_READ_CHARS], "truncated": truncated,
            "chars": len(text)}


def _write_text_confined(root: Path, rel: str, content: str) -> Path:
    """Write `content` to `rel` inside the workspace, hardened
    against the check-then-write symlink race (audit F10).

    Containment is proven, parents are created, and containment is
    re-proven (the pre-existing mitigation). On platforms with
    dir_fd support the write then walks the path by directory FILE
    DESCRIPTORS: each parent component is opened relative to the
    previous directory's fd with O_NOFOLLOW, so a component swapped
    for a symlink mid-write fails the open instead of redirecting
    the write outside — the fds pin the actual directories traversed.
    A leaf that is itself a symlink is followed exactly once, by
    hand: its target must re-prove containment, and the target is
    then written through the same pinned walk. Where dir_fd is
    unavailable (e.g. Windows), the double-prove + write stands."""
    path = resolve_in_workspace(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_in_workspace(root, rel)
    data = content.encode("utf-8")
    if os.open not in os.supports_dir_fd \
            or not hasattr(os, "O_NOFOLLOW"):
        resolved.write_bytes(data)
        return resolved
    root_resolved = Path(root).resolve()
    parts = resolved.relative_to(root_resolved).parts
    dir_fd = os.open(root_resolved, os.O_RDONLY | os.O_DIRECTORY)
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
            # Leaf is a symlink: resolve its target, prove the target
            # stays inside, and write the target directly (its own
            # leaf open is still O_NOFOLLOW via the recursive call's
            # walk — one manual hop, never a chain).
            target = Path(os.readlink(leaf, dir_fd=dir_fd))
            if not target.is_absolute():
                target = (resolved.parent / target)
            target = target.resolve()
            if target != root_resolved \
                    and root_resolved not in target.parents:
                raise WorkspaceEscape(
                    f"path {rel!r} escapes the session workspace")
            return _write_text_confined(
                root, str(target.relative_to(root_resolved)), content)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return resolved
    finally:
        os.close(dir_fd)


def _fs_write(root: Path, args: dict) -> dict:
    content = args.get("content")
    if not isinstance(content, str):
        return _err("fs_write requires string 'content'")
    resolved = _write_text_confined(root, str(args.get("path", "")), content)
    return {"ok": True, "path": _rel(root, resolved),
            "bytes": len(content.encode("utf-8"))}


def _fs_edit(root: Path, args: dict) -> dict:
    path = resolve_in_workspace(root, str(args.get("path", "")))
    if not path.is_file():
        return _err(f"no such file: {args.get('path')}")
    old, new = args.get("old"), args.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        return _err("fs_edit requires non-empty string 'old' and string 'new'")
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count != 1:
        return _err(f"fs_edit anchor occurs {count} times "
                    "(exactly 1 required); nothing changed")
    _write_text_confined(root, str(args.get("path", "")),
                         text.replace(old, new))
    return {"ok": True, "path": _rel(root, path), "replacements": 1}


def _fs_list(root: Path, args: dict) -> dict:
    path = resolve_in_workspace(root, str(args.get("path", ".")))
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
        if len(entries) >= MAX_LIST_ENTRIES:
            break
    return {"ok": True, "path": _rel(root, path), "entries": entries}


def _fs_search(root: Path, args: dict, data_dir) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return _err("fs_search requires a 'query'")
    base = resolve_in_workspace(root, str(args.get("path", ".")))
    matches: list[dict] = []
    needle = query.lower()
    if base.is_file():
        files = [base]
    elif base.is_dir():
        files = [p for p in base.rglob("*") if p.is_file()]
    else:
        return _err(f"no such path: {args.get('path', '.')}")
    for path in files:
        if len(matches) >= MAX_SEARCH_MATCHES:
            break
        try:
            # Never follow a file-symlink out of the workspace.
            resolve_in_workspace(root, _rel(root, path))
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
        except (WorkspaceEscape, OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                matches.append({"path": _rel(root, path), "line": lineno,
                                "text": line.strip()[:200], "kind": "text"})
                if len(matches) >= MAX_SEARCH_MATCHES:
                    break
    result: dict[str, Any] = {"ok": True, "query": query,
                              "matches": matches, "count": len(matches)}
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
                     confine_paths: bool = False) -> dict:
    argv = _normalize_argv(args)
    if argv is None:
        return _err("shell_run requires 'argv' (a list of arguments)")
    if confine_paths:
        escape = _shell_escape_arg(root, argv)
        if escape is not None:
            return _err(
                f"refused: shell argument {escape!r} escapes the "
                "session workspace (hosted-mode confinement)",
                refused="workspace_escape")
    try:
        timeout = float(args.get("timeout_s", DEFAULT_SHELL_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout = DEFAULT_SHELL_TIMEOUT_S
    timeout = min(max(timeout, 1.0), MAX_SHELL_TIMEOUT_S)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(Path(root).resolve()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return _err(f"executable not found: {argv[0]}")
    except OSError as exc:
        return _err(f"could not start {argv[0]}: {exc}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return _err(f"command timed out after {timeout:g}s", argv=argv,
                    timed_out=True)
    stdout = out.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    stderr = err.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": stdout, "stderr": stderr, "argv": argv}


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
    text = str(result.get("text", ""))[:MAX_READ_CHARS]
    return {"ok": True, "url": url, "title": result.get("title", ""),
            "text": text, "chars": result.get("chars", len(text))}


async def execute_tool(tool: str, args: dict, *, root: Path,
                       data_dir=None, confine_paths: bool = False,
                       reach_channels: Optional[set] = None,
                       web_audit: Optional[Callable] = None) -> dict:
    """Dispatch one tool call. Workspace confinement is enforced per
    tool; an escape attempt is a refused result (ok=False), and the
    refusal is what the agent observes. `confine_paths` adds the
    hosted-mode shell confinement (F3); `reach_channels` +
    `web_audit` route web_read through the gateway's channel
    enablement + audit path (F4)."""
    args = args if isinstance(args, dict) else {}
    try:
        if tool == "fs_read":
            return _fs_read(root, args)
        if tool == "fs_write":
            return _fs_write(root, args)
        if tool == "fs_edit":
            return _fs_edit(root, args)
        if tool == "fs_list":
            return _fs_list(root, args)
        if tool == "fs_search":
            return _fs_search(root, args, data_dir)
        if tool == "shell_run":
            return await _shell_run(root, args, confine_paths=confine_paths)
        if tool == "web_read":
            return await _web_read(root, args,
                                   reach_channels=reach_channels,
                                   web_audit=web_audit)
    except WorkspaceEscape as exc:
        return _err(f"refused: {exc}", refused="workspace_escape")
    except Exception as exc:  # a tool bug must surface as an observation
        return _err(f"tool error: {type(exc).__name__}: {exc}")
    return _err(f"unknown tool: {tool}")
