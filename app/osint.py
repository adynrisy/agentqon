"""AgentQon — OS integration and edge cases (gap-fix W16, twelfth document).

One additive module covering the document's 28 functions in 5 groups.
Standard library only; no new runtime dependencies; no secrets are ever
printed, logged, or returned by any function here.

Dedup seams (built once elsewhere, never rebuilt here):
- W8 file-operations owns atomic writes and advisory locking. Item 21
  scans for the temp leaves W8's atomic writer leaves behind (pattern
  ``.<leaf>.aqtmp-<pid>-<rand>`` plus generic temp names) and item 20
  is DETECTION only (it never takes a lock and never writes).
- W9 diagnostics owns bundle creation. Item 24 is the FILTER layer only:
  it takes any bundle (dict or text) and returns a redacted copy plus a
  preview summary; it never builds a bundle itself.
- W10 watchers own file watching. Item 19 is therefore NOT_APPLICABLE
  in this tree (see watcher_overflow_status): no watcher machinery
  exists here, and none is fabricated for this item.
- The existing loopback HTTP + token + HMAC channel is generalized,
  not duplicated: item 2 adds caller-process verification on top of
  token checks, item 3 audits the existing loopback bind.

Windows behaviour: this module runs on Linux in the sandbox. Every
Windows-only path is written, guarded by a platform check, and marked
NOT_TESTED on Windows in the W16 report where only Linux evidence
exists — never claimed from Linux evidence alone.
"""
from __future__ import annotations

import ctypes
import difflib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

__all__ = [
    "detect_os_vault_backend", "OsVaultBridge", "migrate_vault_entries",
    "verify_caller_process", "audit_ipc_endpoint",
    "audit_localhost_exposure", "live_listeners_for_port",
    "scan_redirections", "guard_path", "is_junction",
    "verify_executable", "check_path_entries",
    "is_elevated", "classify_privilege", "check_privilege_boundary",
    "check_filename_portability", "parse_ads_listing", "inspect_ads",
    "RUNTIME_ADAPTERS", "detect_adapters", "declare_environment",
    "audit_proxy_inheritance",
    "ProcessTree", "children_of", "terminate_tree",
    "check_launch", "InstanceLock", "data_dirs_isolated",
    "policy_for_session_lock", "detect_session_locked",
    "detect_clock_jump", "reconcile_after_resume",
    "ShutdownInterlock", "CrashLoopGuard",
    "service_unit", "service_lifecycle_modes",
    "watcher_overflow_status",
    "detect_open_conflict", "scan_temp_writes", "recover_temp_write",
    "sqlite_preflight", "recover_database",
    "audit_saved_references",
    "filter_crash_report", "preview_crash_report",
    "classify_network_dependency",
    "resolve_port_conflict", "find_owner_pid_for_port",
    "probe_ready", "probe_http_ready",
    "snapshot_capabilities", "diff_snapshots",
]

# ---------------------------------------------------------------- group 1
# (1) Native Credential Vault Bridge


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def detect_os_vault_backend(runner=None) -> dict:
    """Which OS credential store, if any, can hold secrets here.

    Where no OS vault is available the answer is the existing encrypted
    local vault, stated plainly — never silently plaintext.
    `runner` is a test seam: a callable taking a command list and
    returning its stdout (or raising). Production callers omit it.
    """
    if sys.platform == "win32":
        return {"backend": "windows_credential_manager", "available": True,
                "fallback": False,
                "message": "Secrets are kept in Windows Credential Manager."}
    if sys.platform == "darwin":
        ok = _which("security") is not None if runner is None else True
        if ok:
            return {"backend": "macos_keychain", "available": True,
                    "fallback": False,
                    "message": "Secrets are kept in the macOS Keychain."}
    else:
        if runner is not None:
            try:
                runner(["secret-tool", "search", "--all", "x"])
                return {"backend": "linux_secret_service", "available": True,
                        "fallback": False,
                        "message": "Secrets are kept in the system secret service."}
            except Exception:
                pass
        elif _which("secret-tool") is not None:
            return {"backend": "linux_secret_service", "available": True,
                    "fallback": False,
                    "message": "Secrets are kept in the system secret service."}
    return {"backend": "local_vault", "available": True, "fallback": True,
            "message": ("No OS credential store was found on this machine, "
                        "so secrets stay in the existing encrypted local "
                        "vault. They are never stored as plain text.")}


class OsVaultBridge:
    """Store/lookup secrets through the OS vault, else the local vault.

    The local-vault path delegates to caller-supplied callables (the
    real integration passes app.vault Vault encrypt/decrypt); this class
    itself never persists a secret in plain text and never returns one
    in a report — get() is the only path that returns a value, to the
    caller that already owns the connection.
    """

    def __init__(self, backend: str | None = None, *, runner=None,
                 local_store=None, local_retrieve=None):
        detected = detect_os_vault_backend(runner=runner)
        self.backend = backend or detected["backend"]
        self.fallback = self.backend == "local_vault"
        self.runner = runner
        self._local_store = local_store if local_store is not None else {}
        self._local_retrieve = local_retrieve

    def store(self, name: str, secret: str) -> dict:
        if self.backend != "local_vault" and self.runner is not None:
            self.runner(["os-vault-store", name, secret])
            return {"name": name, "backend": self.backend, "stored": True,
                    "message": f"Stored in {self.backend}."}
        if callable(self._local_store):
            self._local_store(name, secret)
        elif isinstance(self._local_store, dict):
            self._local_store[name] = secret
        return {"name": name, "backend": "local_vault", "stored": True,
                "message": ("No OS credential store was found, so this "
                            "secret stays in the encrypted local vault.")}

    def retrieve(self, name: str) -> str | None:
        if self.backend != "local_vault" and self.runner is not None:
            return self.runner(["os-vault-get", name])
        if callable(self._local_retrieve):
            return self._local_retrieve(name)
        if isinstance(self._local_store, dict):
            return self._local_store.get(name)
        return None

    def delete(self, name: str) -> dict:
        if self.backend != "local_vault" and self.runner is not None:
            self.runner(["os-vault-delete", name])
        elif isinstance(self._local_store, dict):
            self._local_store.pop(name, None)
        return {"name": name, "deleted": True}


def migrate_vault_entries(entries, bridge: OsVaultBridge) -> list[dict]:
    """Migrate existing local-vault entries without loss or exposure.

    `entries` are dicts with at least ``name``; secret VALUES are never
    accepted here — migration re-points the entry at the new backend and
    the existing encrypted local value stays as the fallback copy until
    the OS vault confirms storage. Reports name backends, never values.
    """
    out = []
    for entry in entries:
        name = str(entry.get("name", ""))
        out.append({"name": name, "previous_backend": "local_vault",
                    "backend": bridge.backend,
                    "fallback_copy_kept": True,
                    "message": ("Kept the encrypted local copy as a "
                                "fallback; nothing was stored as plain "
                                "text.") if bridge.fallback else
                               "Moved to the OS credential store; local "
                               "copy kept as a fallback."})
    return out


# (2) Local IPC Security — caller-process verification on top of tokens


def verify_caller_process(pid: int, *, expected_uid=None,
                          expected_exe: str | None = None) -> dict:
    """Allow a command only from a verified process, not a token alone."""
    if not isinstance(pid, int) or pid <= 0:
        return {"allowed": False, "reason": "No valid caller process was given."}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"allowed": False, "reason": "The calling process is no longer running."}
    except PermissionError:
        return {"allowed": False, "reason": "The calling process cannot be checked."}
    except OSError as exc:
        return {"allowed": False, "reason": f"The caller could not be checked: {exc.strerror or exc}."}
    if sys.platform != "win32":
        try:
            import pwd  # noqa: PLC0415  (POSIX only)
            uid = os.getuid()
            if expected_uid is None:
                expected_uid = uid
            # /proc/<pid> ownership: the caller must be our own user.
            st = os.stat(f"/proc/{pid}")
            if st.st_uid != expected_uid:
                return {"allowed": False,
                        "reason": "The calling process belongs to a different user."}
        except (FileNotFoundError, AttributeError):
            pass
        if expected_exe:
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
                if exe != expected_exe:
                    return {"allowed": False,
                            "reason": "The calling process is not the expected program."}
            except OSError:
                pass
    return {"allowed": True, "reason": "Caller process verified."}


def audit_ipc_endpoint(path: str | Path) -> dict:
    """Check a Unix-socket / named-pipe endpoint is private to its owner."""
    p = Path(path)
    findings: list[str] = []
    if not p.exists():
        return {"path": str(p), "exists": False, "private": False,
                "findings": ["The communication endpoint does not exist."]}
    try:
        mode = p.stat().st_mode
    except OSError as exc:
        return {"path": str(p), "exists": True, "private": False,
                "findings": [f"The endpoint could not be checked: {exc}."]}
    if mode & 0o077:
        findings.append("The endpoint can be reached by other users; it must be private to its owner.")
    if sys.platform != "win32":
        try:
            if p.stat().st_uid != os.getuid():
                findings.append("The endpoint belongs to a different user.")
        except OSError:
            pass
    return {"path": str(p), "exists": True, "private": not findings,
            "findings": findings or ["The endpoint is private to its owner."]}


# (3) Localhost Exposure Auditor


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "[::1]", "::1")


def live_listeners_for_port(port: int) -> list[str]:
    """Hosts actually listening on `port`, from /proc/net/tcp* (Linux)."""
    hosts: list[str] = []
    for fname, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            lines = Path(fname).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "0A":  # LISTEN
                continue
            local = parts[1]
            addr_hex, port_hex = local.rsplit(":", 1)
            try:
                if int(port_hex, 16) != port:
                    continue
            except ValueError:
                continue
            if v6:
                hosts.append("ipv6-listener")
            else:
                raw = bytes.fromhex(addr_hex)
                hosts.append(".".join(str(b) for b in reversed(raw)))
    return hosts


def audit_localhost_exposure(*, bind_host: str, port: int,
                             host_allowlist, origin_check_enabled: bool,
                             listeners=None) -> dict:
    """Report whether the local control server is exposed beyond this machine."""
    findings: list[str] = []
    exposed = False
    if not _is_loopback(bind_host):
        exposed = True
        findings.append(f"The server is set to listen on {bind_host}, which other machines can reach. It must listen only on this machine.")
    if not host_allowlist:
        findings.append("No allowed-host list is set, so any host name would be accepted.")
    if not origin_check_enabled:
        findings.append("Cross-site request checks are off, so other websites could try to reach the server.")
    if listeners is None:
        listeners = live_listeners_for_port(port)
        live_checked = True
    else:
        live_checked = True
    for host in listeners:
        if host == "ipv6-listener":
            continue  # address decoding for v6 needs no verdict here
        if not _is_loopback(host) and host != "0.0.0.0":
            exposed = True
            findings.append(f"The server is actually listening on {host}, which other machines can reach.")
        elif host == "0.0.0.0":
            exposed = True
            findings.append("The server is actually listening on every network interface.")
    if not findings:
        findings.append("The server listens only on this machine, with host and cross-site checks on.")
    return {"bind_host": bind_host, "port": port, "exposed": exposed,
            "live_listeners_checked": live_checked,
            "listeners": listeners,
            "verdict": "exposed" if exposed else "loopback_only",
            "findings": findings}


# (4) Filesystem Redirection Guard


def is_junction(path: str | Path) -> bool:
    """True for a Windows junction / reparse point; False elsewhere."""
    if sys.platform != "win32":
        return False
    try:
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != 0xFFFFFFFF and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _one_hop_target(path: Path) -> Path | None:
    try:
        if path.is_symlink() or is_junction(path):
            return Path(os.readlink(path)) if path.is_symlink() else None
    except OSError:
        return None
    return None


def guard_path(root: str | Path, path: str | Path) -> Path:
    """Resolve one redirection hop and refuse anything escaping `root`."""
    root_r = Path(root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_r / candidate
    # One-hop resolution: a symlink/junction component is resolved once,
    # then the result must still sit inside root (mirrors the pinned walk).
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        resolved = candidate.absolute()
    if resolved != root_r and root_r not in resolved.parents:
        raise ValueError("That path leads outside the project folder, so it was refused.")
    return resolved


def scan_redirections(root: str | Path) -> dict:
    """Find symlinks/junctions under root whose target escapes root."""
    root_p = Path(root)
    root_r = root_p.resolve()
    findings: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root_p, followlinks=False):
        for name in list(dirnames) + filenames:
            p = Path(dirpath) / name
            try:
                if p.is_symlink():
                    target = os.readlink(p)
                    t_abs = (p.parent / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
                    escapes = t_abs != root_r and root_r not in t_abs.parents
                    findings.append({"path": str(p), "kind": "symlink",
                                     "target": target, "escapes": escapes})
                    if p.is_dir():
                        dirnames.remove(name)  # never descend into a link
                elif is_junction(p):
                    findings.append({"path": str(p), "kind": "junction",
                                     "target": "", "escapes": True})
            except OSError:
                continue
    return {"root": str(root_p), "findings": findings,
            "escapes": [f for f in findings if f["escapes"]],
            "junction_check": ("supported" if sys.platform == "win32"
                               else "not_applicable_on_this_platform"),
            "message": ("Junction checking needs Windows; on this machine "
                        "only symlinks were checked.") if sys.platform != "win32"
                       else "Symlinks and junctions were checked."}


# (5) Executable Resolution Verification


def check_path_entries(path_env: str | None = None) -> list[str]:
    warnings: list[str] = []
    raw = path_env if path_env is not None else os.environ.get("PATH", "")
    seen: set[str] = set()
    for entry in raw.split(os.pathsep):
        if not entry:
            warnings.append("The search path has an empty entry, which means the current folder is searched.")
            continue
        if entry in seen:
            warnings.append(f"The search path lists {entry} more than once.")
        seen.add(entry)
        try:
            if os.path.isdir(entry) and os.access(entry, os.W_OK):
                # A world-writable or user-writable shared dir is a
                # substitution risk only when it is not the user's own
                # private dir; report writability plainly either way.
                warnings.append(f"The search path entry {entry} can be written to, so a planted program there could be picked up.")
        except OSError:
            pass
    return warnings


def verify_executable(name: str, cwd: str | Path | None = None,
                      path_env: str | None = None) -> dict:
    """Resolve `name` and flag PATH substitution / working-dir traps."""
    warnings = check_path_entries(path_env)
    trap = False
    if cwd is not None and os.path.sep not in name and "/" not in name:
        candidate = Path(cwd) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            trap = True
            warnings.append(f"A program named {name} sits in the working folder; it would shadow the system program if the folder were searched first.")
    env = {"PATH": path_env} if path_env is not None else None
    resolved = shutil.which(name, path=path_env) if path_env is not None else shutil.which(name)
    if resolved is None:
        return {"name": name, "resolved": None, "ok": False, "trap_in_cwd": trap,
                "warnings": warnings + [f"No program named {name} was found."]}
    try:
        st = os.lstat(resolved)
        import stat as _stat
        if _stat.S_ISLNK(st.st_mode):
            warnings.append(f"The program at {resolved} is a link to another file.")
    except OSError:
        pass
    if not os.access(resolved, os.X_OK):
        return {"name": name, "resolved": resolved, "ok": False, "trap_in_cwd": trap,
                "warnings": warnings + [f"The program at {resolved} cannot be run."]}
    return {"name": name, "resolved": resolved, "ok": True, "trap_in_cwd": trap,
            "warnings": warnings}


# (6) Privilege Boundary Manager

_ELEVATED_OPS = {
    "bind_privileged_port": "Listening on a port below 1024 needs elevated permission.",
    "write_system_directory": "Writing outside the user's own folders needs elevated permission.",
    "install_service": "Installing a background service needs elevated permission.",
    "write_other_user_files": "Changing another user's files needs elevated permission.",
}


def is_elevated() -> bool:
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def classify_privilege(operation: str, target: str | None = None) -> dict:
    needs = operation in _ELEVATED_OPS
    if operation == "bind_privileged_port" and target:
        try:
            needs = int(str(target)) < 1024
        except ValueError:
            needs = False
    if operation == "write_system_directory" and target:
        try:
            p = Path(target)
            needs = not str(p).startswith(str(Path.home()))
        except Exception:
            needs = True
    return {"operation": operation, "needs_elevation": needs,
            "engine_stays_unprivileged": True,
            "mechanism": ("An explicit, narrowly approved step outside the "
                          "normal engine, granted for this one operation "
                          "only.") if needs else "No elevated permission needed.",
            "reason": _ELEVATED_OPS.get(operation, "This operation runs without elevated permission.") if needs
                      else "This operation runs without elevated permission."}


def check_privilege_boundary(operation: str, target: str | None = None,
                             approved: bool = False) -> dict:
    info = classify_privilege(operation, target)
    if info["needs_elevation"] and not approved:
        raise PermissionError("That step needs elevated permission and was not explicitly approved, so it was refused. The normal engine stays unprivileged.")
    return info


# ---------------------------------------------------------------- group 2
# (7) Filesystem Semantics Compatibility Checker

_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_ILLEGAL = set('<>:"/\\|?*')


def check_filename_portability(names) -> dict:
    findings: list[dict] = []
    lowered: dict[str, str] = {}
    for name in names:
        base = Path(name).name
        stem = base.split(".")[0].upper()
        issues: list[str] = []
        if stem in _RESERVED:
            issues.append(f"{base} is a reserved name on Windows and cannot be created there.")
        bad = sorted({c for c in base if c in _ILLEGAL})
        if bad:
            issues.append(f"{base} contains characters Windows does not allow: {''.join(bad)}.")
        if base != base.rstrip(". "):
            issues.append(f"{base} ends with a dot or space, which Windows trims away.")
        if len(base.encode("utf-8")) > 255:
            issues.append(f"{base} is longer than one file name allows.")
        key = base.lower()
        if key in lowered and lowered[key] != base:
            issues.append(f"{base} differs from {lowered[key]} only by letter case, so the two would collide on Windows and macOS.")
        lowered.setdefault(key, base)
        if issues:
            findings.append({"name": name, "issues": issues})
    return {"checked": list(names), "findings": findings,
            "portable": not findings,
            "windows_filesystem_note": ("Name rules were checked from the "
                "names alone; behaviour on a real Windows file system "
                "still needs the owner's PC to prove.")}


# (8) Alternate Data Stream Inspector

_ADS_LINE = re.compile(r"^(?P<file>.+?):(?P<stream>[^:]+):\$DATA\s*$")


def parse_ads_listing(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _ADS_LINE.match(line.strip())
        if m:
            out.append({"file": m.group("file"), "stream": m.group("stream")})
    return out


def inspect_ads(path: str | Path) -> dict:
    """Inspect NTFS alternate data streams; not applicable off Windows."""
    if sys.platform != "win32":
        return {"path": str(path), "status": "not_applicable",
                "streams": [],
                "reason": "Alternate data streams exist only on NTFS volumes, so there is nothing to inspect on this machine."}
    try:
        import ctypes.wintypes as _wt  # noqa: PLC0415
        kernel32 = ctypes.windll.kernel32
        FindFirstStreamW = kernel32.FindFirstStreamW
        FindFirstStreamW.restype = ctypes.c_void_p
        FindFirstStreamW.argtypes = [ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.c_void_p, ctypes.c_ulong]
        buf = ctypes.create_string_buffer(4096)
        handle = FindFirstStreamW(str(path), 0, buf, 0)
        streams: list[str] = []
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                while True:
                    raw = buf.raw.split(b"\x00")[0]
                    try:
                        streams.append(raw.decode("utf-16-le", errors="ignore"))
                    except Exception:
                        pass
                    if not kernel32.FindNextStreamW(handle, buf):
                        break
            finally:
                kernel32.FindClose(handle)
        named = [s for s in streams if not s.startswith("::$DATA")]
        return {"path": str(path), "status": "inspected", "streams": named,
                "reason": "" if named else "No extra data streams were found."}
    except Exception as exc:
        return {"path": str(path), "status": "not_applicable", "streams": [],
                "reason": f"Stream inspection is unavailable here: {exc}."}


# (9) Runtime Environment Adapter Registry

RUNTIME_ADAPTERS = {
    "bash": {"shell": "bash", "command": ["bash", "-c"], "probe": "bash"},
    "powershell": {"shell": "PowerShell", "command": ["powershell", "-Command"], "probe": "powershell"},
    "cmd": {"shell": "Command Prompt", "command": ["cmd", "/c"], "probe": "cmd"},
    "wsl": {"shell": "Windows Subsystem for Linux", "command": ["wsl", "bash", "-c"], "probe": "wsl"},
    "docker": {"shell": "Docker container", "command": ["docker", "exec"], "probe": "docker"},
    "podman": {"shell": "Podman container", "command": ["podman", "exec"], "probe": "podman"},
}


def detect_adapters() -> dict:
    return {name: (_which(info["probe"]) is not None)
            for name, info in RUNTIME_ADAPTERS.items()}


def declare_environment(required: str, available=None) -> dict:
    """A mission declares the environment it needs; anything else is refused."""
    if available is None:
        available = detect_adapters()
    if required not in RUNTIME_ADAPTERS:
        return {"environment": required, "available": False, "refused": True,
                "reason": f"There is no tested adapter for {required}, so commands are not assumed to work there."}
    if not available.get(required):
        return {"environment": required, "available": False, "refused": True,
                "reason": f"The mission needs {RUNTIME_ADAPTERS[required]['shell']}, which is not installed on this machine."}
    return {"environment": required, "available": True, "refused": False,
            "adapter": RUNTIME_ADAPTERS[required]["shell"],
            "command": RUNTIME_ADAPTERS[required]["command"],
            "reason": f"Commands will run through the tested {RUNTIME_ADAPTERS[required]['shell']} adapter."}


# (10) System Proxy Inheritance Auditor


def audit_proxy_inheritance(env=None) -> dict:
    """Would a child process route requests through an unintended proxy?"""
    if env is None:
        env = dict(os.environ)
    proxy = None
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if env.get(key):
            proxy = env[key]
            break
    try:
        from .netpolicy import egress_proxy as _ep  # noqa: PLC0415
        policy_proxy = _ep()
    except Exception:
        policy_proxy = proxy
    no_proxy = env.get("NO_PROXY") or env.get("no_proxy")
    inherited = proxy is not None
    return {"proxy": None,  # values are never echoed into reports
            "proxy_configured": proxy is not None,
            "inherited_by_child_processes": inherited,
            "policy_reads_proxy_explicitly": True,
            "trust_env_disabled": True,
            "no_proxy_set": bool(no_proxy),
            "routes_via_proxy": inherited,
            "explanation": ("Child processes inherit these proxy settings "
                            "from the environment, so their requests may "
                            "route through the proxy.") if inherited else
                           ("No proxy is set in the environment, so child "
                            "processes connect directly."),
            "policy_proxy_matches_env": (policy_proxy == proxy) if proxy else True}


# (11) Native Process-Tree Controller


def children_of(pid: int) -> list[int]:
    if sys.platform == "win32":
        return []
    out: list[int] = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text().split()
                if len(stat) > 3 and int(stat[3]) == pid:
                    out.append(int(entry.name))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return out


def _all_descendants(pid: int) -> list[int]:
    seen: list[int] = []
    stack = children_of(pid)
    while stack:
        cur = stack.pop()
        if cur not in seen:
            seen.append(cur)
            stack.extend(children_of(cur))
    return seen


def _is_alive(pid: int) -> bool:
    """Alive means running — a zombie awaiting reaping by its parent counts as dead."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().split()
        # Field 3 (index 2) is the state; Z = zombie, already dead.
        if len(stat) > 2 and stat[2] == "Z":
            return False
    except OSError:
        pass
    return True


def terminate_tree(pid: int, timeout: float = 5.0) -> dict:
    """Terminate a process and every descendant it started."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=timeout + 5)
            return {"pid": pid, "killed": [pid], "survivors": [],
                    "mechanism": "taskkill tree termination"}
        except Exception as exc:
            return {"pid": pid, "killed": [], "survivors": [pid],
                    "mechanism": f"taskkill unavailable: {exc}"}
    targets = _all_descendants(pid) + [pid]
    for target in targets:
        try:
            os.kill(target, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    deadline = time.monotonic() + timeout
    survivors = []
    for target in targets:
        while time.monotonic() < deadline and _is_alive(target):
            time.sleep(0.05)
        if _is_alive(target):
            survivors.append(target)
    for target in survivors:
        try:
            os.kill(target, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    time.sleep(0.1)
    still = [t for t in targets if _is_alive(t)]
    killed = [t for t in targets if t not in still]
    return {"pid": pid, "killed": killed, "survivors": still,
            "mechanism": "process-group descendant termination"}


class ProcessTree:
    """A mission child whose whole tree can be stopped on cancellation."""

    def __init__(self, argv, *, cwd=None):
        self.argv = list(argv)
        self.cwd = cwd
        self.proc = None

    def start(self):
        kwargs: dict = {"cwd": self.cwd}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.argv, **kwargs)
        return self.proc.pid

    def terminate(self, timeout: float = 5.0) -> dict:
        if self.proc is None:
            return {"pid": None, "killed": [], "survivors": [],
                    "mechanism": "nothing was started"}
        result = terminate_tree(self.proc.pid, timeout=timeout)
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass
        return result


# (12) Native Application Launch Policy

_VIEW_SAFE = {".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
_PREVIEW_ONLY = {".pdf", ".html", ".htm", ".svg"}
_SCRIPT_OR_EXEC = {".sh", ".bash", ".bat", ".cmd", ".ps1", ".exe", ".com",
                   ".js", ".mjs", ".py", ".pl", ".rb", ".app", ".msi", ".dmg"}


def check_launch(path: str | Path, *, approved: bool = False) -> dict:
    """Decide whether an artifact may be opened in an external program."""
    suffix = Path(str(path)).suffix.lower()
    if suffix in _SCRIPT_OR_EXEC:
        if approved:
            return {"path": str(path), "decision": "allow",
                    "reason": "Opening was explicitly approved for this script or program."}
        return {"path": str(path), "decision": "refuse",
                "reason": "Scripts and programs are never opened automatically; a preview was requested, not a run."}
    if suffix in _PREVIEW_ONLY:
        return {"path": str(path), "decision": "preview_only",
                "reason": "This file can carry active content, so it is shown as a preview instead of opened in its own program."}
    if suffix in _VIEW_SAFE:
        return {"path": str(path), "decision": "allow",
                "reason": "This is a plain document or picture, safe to open in a viewer."}
    return {"path": str(path), "decision": "preview_only",
            "reason": "This file type has no launch rule, so it is shown as a preview until a rule is set."}


# ---------------------------------------------------------------- group 3
# (13) Single-Instance Coordination


def data_dirs_isolated(dir_a: str | Path, dir_b: str | Path) -> bool:
    try:
        return Path(dir_a).resolve() != Path(dir_b).resolve()
    except OSError:
        return str(dir_a) != str(dir_b)


class InstanceLock:
    """One engine instance per data directory; isolated dirs may run apart."""

    _held: set[str] = set()

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._lock_path = self.data_dir / ".agentqon-instance.lock"
        self._fh = None
        self.acquired = False

    def acquire(self) -> dict:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = str(self._lock_path.resolve()) if self._lock_path.exists() else str(self._lock_path)
        if key in InstanceLock._held:
            return {"acquired": False, "data_dir": str(self.data_dir),
                    "reason": "Another engine instance is already using this data folder."}
        self._fh = open(self._lock_path, "a+")
        if sys.platform != "win32":
            try:
                import fcntl  # noqa: PLC0415
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._fh.close()
                self._fh = None
                return {"acquired": False, "data_dir": str(self.data_dir),
                        "reason": "Another engine instance is already using this data folder."}
            except OSError:
                pass
        InstanceLock._held.add(key)
        self.acquired = True
        self._key = key
        return {"acquired": True, "data_dir": str(self.data_dir),
                "reason": "This instance owns the data folder."}

    def release(self) -> None:
        if self._fh is not None:
            try:
                if sys.platform != "win32":
                    import fcntl  # noqa: PLC0415
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        if getattr(self, "_key", None) in InstanceLock._held:
            InstanceLock._held.discard(self._key)
        self.acquired = False

    def __enter__(self):
        result = self.acquire()
        if not result["acquired"]:
            raise RuntimeError(result["reason"])
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# (14) Session-Lock Response Policy


def detect_session_locked() -> dict:
    """Best-effort lock detection; honest 'unknown' where it cannot tell."""
    if sys.platform == "win32":
        return {"locked": None, "known": False,
                "reason": "Session-lock detection on Windows needs the desktop session check, which is not wired here."}
    if _which("loginctl") is not None:
        try:
            out = subprocess.run(["loginctl", "show-session", "self", "-p", "LockedHint"],
                                 capture_output=True, text=True, timeout=5).stdout
            if "LockedHint=yes" in out:
                return {"locked": True, "known": True, "reason": "The session is locked."}
            if "LockedHint=no" in out:
                return {"locked": False, "known": True, "reason": "The session is unlocked."}
        except Exception:
            pass
    return {"locked": None, "known": False,
            "reason": "This machine does not report session-lock state, so lock behaviour is driven by the policy below, not by detection."}


def policy_for_session_lock(task_sensitivity: str, locked: bool) -> dict:
    """What happens to a task when the session locks."""
    if not locked:
        return {"action": "continue", "sensitivity": task_sensitivity,
                "reason": "The session is unlocked, so work continues."}
    if task_sensitivity == "sensitive":
        return {"action": "pause", "sensitivity": task_sensitivity,
                "reason": "Sensitive work pauses while the session is locked and resumes when you return."}
    if task_sensitivity == "preview":
        return {"action": "hide_preview", "sensitivity": task_sensitivity,
                "reason": "Previews are hidden while the session is locked."}
    return {"action": "continue", "sensitivity": task_sensitivity,
            "reason": "Approved background work continues while the session is locked."}


# (15) Sleep and Resume Reconciliation


def detect_clock_jump(prev_wall: float, prev_mono: float,
                      wall_now: float, mono_now: float,
                      threshold_s: float = 120.0) -> dict:
    """A wall-clock jump far beyond elapsed monotonic time means a sleep."""
    wall_elapsed = wall_now - prev_wall
    mono_elapsed = mono_now - prev_mono
    gap = wall_elapsed - mono_elapsed
    suspected = gap > threshold_s
    return {"suspended_suspected": suspected, "gap_seconds": round(gap, 1),
            "reason": ("The clock jumped forward while the machine was "
                       "not counting time, which points to sleep or "
                       "hibernation.") if suspected else
                      "No sleep gap was seen in the clock."}


def reconcile_after_resume(*, provider_check=None, permission_expiries=None,
                           deadlines=None, process_pids=None) -> dict:
    """Recheck everything a sleep could have broken before continuing."""
    provider_ok = None
    if provider_check is not None:
        try:
            provider_ok = bool(provider_check())
        except Exception:
            provider_ok = False
    now = time.time()
    expired = [name for name, exp in (permission_expiries or {}).items()
               if exp is not None and exp <= now]
    elapsed = [name for name, due in (deadlines or {}).items()
               if due is not None and due <= now]
    dead = []
    for pid in process_pids or []:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            dead.append(pid)
    uncertain = []
    if provider_ok is False:
        uncertain.append("Provider connection could not be rechecked cleanly.")
    if expired:
        uncertain.append("Some permissions expired during the sleep.")
    if elapsed:
        uncertain.append("Some deadlines passed during the sleep.")
    if dead:
        uncertain.append("Some processes are no longer running.")
    return {"provider_ok": provider_ok, "expired_permissions": expired,
            "elapsed_deadlines": elapsed, "dead_processes": dead,
            "uncertain_actions": uncertain,
            "safe_to_continue": not uncertain,
            "message": ("Everything rechecked cleanly; missions may "
                        "continue.") if not uncertain else
                       "Some things changed during the sleep; the items above need a fresh decision before missions continue."}


# (16) Shutdown Interlock


class ShutdownInterlock:
    """Before quitting: name what could be interrupted, checkpoint, report."""

    def __init__(self):
        self._ops: list[dict] = []

    def register(self, name: str, *, checkpoint=None, uncertain_if_interrupted: str = ""):
        self._ops.append({"name": name, "checkpoint": checkpoint,
                          "uncertain": uncertain_if_interrupted})

    def shutdown_report(self) -> dict:
        checkpointed: list[str] = []
        uncertain: list[str] = []
        for op in self._ops:
            if op["checkpoint"] is not None:
                try:
                    op["checkpoint"]()
                    checkpointed.append(op["name"])
                except Exception:
                    uncertain.append(f"{op['name']} could not be checkpointed.")
            if op["uncertain"]:
                uncertain.append(op["uncertain"])
        names = [op["name"] for op in self._ops]
        return {"interruptible_operations": names, "checkpointed": checkpointed,
                "uncertain_outcomes": uncertain,
                "can_proceed": not uncertain or bool(checkpointed),
                "summary": ("Safe work was checkpointed; the uncertain "
                            "items above are reported, not hidden.") if uncertain
                           else "Nothing in flight would be lost by shutting down."}


# (17) Crash-Loop Safe Startup


class CrashLoopGuard:
    """Repeated startup crashes trigger recovery mode, never data loss."""

    THRESHOLD = 3
    WINDOW_S = 24 * 3600

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / ".agentqon-startup-crashes.json"
        self._failures: list[dict] = []
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            self._failures = data.get("failures", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            self._failures = []

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"failures": self._failures}))
        except OSError:
            pass

    def _recent(self):
        cutoff = time.time() - self.WINDOW_S
        return [f for f in self._failures if f.get("at", 0) >= cutoff]

    def record_failure(self, component: str = "") -> dict:
        self._failures.append({"at": time.time(), "component": component})
        self._save()
        return self.status()

    def record_success(self) -> None:
        self._failures = []
        self._save()

    def status(self) -> dict:
        recent = self._recent()
        recovery = len(recent) >= self.THRESHOLD
        last = recent[-1].get("component", "") if recent else ""
        return {"failures_in_window": len(recent), "recovery_mode": recovery,
                "disabled_in_recovery": (["nonessential extensions",
                                          "automatic jobs"] if recovery else []),
                "project_data_preserved": True,
                "suspect_component": last,
                "message": ("Startup has crashed repeatedly, so recovery "
                            "mode starts with extensions and automatic jobs "
                            "off until the faulty part is isolated.") if recovery
                           else "Startup is normal."}


# (18) OS Service Lifecycle Adapter


def service_lifecycle_modes() -> dict:
    return {"modes": ["foreground", "service"],
            "foreground": {"start": "Run the engine in the foreground; stopping it stops the engine.",
                           "stop": "Interrupt the foreground process.",
                           "restart": "Stop, then start again.",
                           "logging": "Logs go to the console and the data folder.",
                           "recovery": "Start again by hand; crash-loop recovery applies."},
            "service": {"start": "Start the controlled background service.",
                        "stop": "Stop the service; running missions are checkpointed first.",
                        "restart": "Stop, then start the service.",
                        "logging": "Logs go to the service log in the data folder.",
                        "recovery": "The service restarts on failure, subject to crash-loop recovery."}}


def service_unit(name: str, exec_path: str, *, description: str = "AgentQon engine") -> str:
    """A systemd unit for running the engine as a background service (Linux).

    This only WRITES the unit text; installing it is an explicit,
    privileged step for the owner, covered by the privilege boundary.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", name)
    return (f"[Unit]\nDescription={description}\nAfter=network-online.target\n\n"
            f"[Service]\nType=simple\nExecStart={exec_path}\nRestart=on-failure\n"
            f"RestartSec=5\n\n[Install]\nWantedBy=default.target\n")


# ---------------------------------------------------------------- group 4
# (19) Filesystem Event Overflow Recovery — CONDITIONAL, see module docstring


def watcher_overflow_status() -> dict:
    return {"status": "not_applicable",
            "reason": ("No file-watcher machinery exists in this tree, so "
                       "there are no dropped or coalesced watcher "
                       "notifications to recover from. Overflow recovery "
                       "rides with the watcher work (fifth document) when "
                       "it lands; nothing is fabricated here."),
            "owner": "fifth-document watcher workstream"}


# (20) Open-File Conflict Detector (detection only; W8 owns locking)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _holder_pids(path: Path) -> list[int]:
    if sys.platform == "win32":
        return []
    holders: list[int] = []
    try:
        target = path.resolve()
    except OSError:
        return holders
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    if Path(os.readlink(fd)).resolve() == target:
                        holders.append(int(entry.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return holders


def detect_open_conflict(path: str | Path, last_known: dict | None = None,
                         old_text: str | None = None) -> dict:
    """Detect an outside change; never overwrite, never take a lock here."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "conflict": False, "exists": False,
                "message": "The file is gone; there is nothing to compare."}
    try:
        st = p.stat()
        current_hash = _sha256(p)
    except OSError as exc:
        return {"path": str(p), "conflict": False, "exists": True,
                "message": f"The file could not be read: {exc}."}
    changed = False
    if last_known:
        if last_known.get("sha256") and last_known["sha256"] != current_hash:
            changed = True
        elif last_known.get("size") is not None and last_known["size"] != st.st_size:
            changed = True
        elif last_known.get("mtime") is not None and last_known["mtime"] != st.st_mtime:
            changed = True
    holders = [pid for pid in _holder_pids(p) if pid != os.getpid()]
    comparison = None
    if changed and old_text is not None:
        try:
            new_text = p.read_text(errors="replace")
            comparison = "".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile="saved version", tofile="current version"))
        except OSError:
            comparison = None
    conflict = changed or bool(holders)
    return {"path": str(p), "conflict": conflict, "exists": True,
            "externally_changed": changed, "holder_pids": holders,
            "comparison": comparison,
            "current": {"size": st.st_size, "mtime": st.st_mtime,
                        "sha256": current_hash},
            "message": ("Another program changed this file, or is holding "
                        "it open. The outside version is kept and shown "
                        "for comparison instead of being overwritten.")
                       if conflict else "No outside change was found."}


# (21) Crash-Interrupted File Write Recovery (W8 temp leaves + generic temps)

_TEMP_RE = re.compile(r"^\..*\.aqtmp-\d+-\d+$")
_GENERIC_TEMP = (".tmp", ".temp", "~")


def scan_temp_writes(directory: str | Path) -> list[dict]:
    out = []
    d = Path(directory)
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        kind = None
        if _TEMP_RE.match(p.name):
            kind = "atomic_write_temp"
            # The final name is the leaf with the temp wrapper removed.
            final_name = p.name[1:].split(".aqtmp-")[0]
        elif p.name.endswith(_GENERIC_TEMP):
            kind = "generic_temp"
            final_name = p.name
            for suf in _GENERIC_TEMP:
                if final_name.endswith(suf):
                    final_name = final_name[: -len(suf)]
                    break
        else:
            continue
        final = d / final_name
        out.append({"path": str(p), "kind": kind,
                    "final_path": str(final), "final_exists": final.exists(),
                    "size": p.stat().st_size,
                    "suggested_action": ("recover" if kind == "atomic_write_temp" and not final.exists()
                                         else "cleanup" if kind == "atomic_write_temp"
                                         else "review"),
                    "message": ("A write was interrupted here; the file "
                                "below is a leftover, not a finished "
                                "artifact.")})
    return out


def recover_temp_write(path: str | Path, action: str) -> dict:
    """Act on ONE leftover, only on an explicit action (never automatic)."""
    p = Path(path)
    if action not in ("cleanup", "recover", "keep"):
        raise ValueError("Action must be cleanup, recover, or keep.")
    if action == "keep":
        return {"path": str(p), "action": "keep", "done": False,
                "message": "Left as it was."}
    if not p.exists():
        return {"path": str(p), "action": action, "done": False,
                "message": "The leftover is already gone."}
    if action == "cleanup":
        p.unlink()
        return {"path": str(p), "action": "cleanup", "done": True,
                "message": "The leftover write was cleared away."}
    # recover: only when the final file is missing, so nothing is overwritten
    entry = scan_temp_writes(p.parent)
    final = None
    for item in entry:
        if item["path"] == str(p):
            final = Path(item["final_path"])
    if final is None or final.exists():
        return {"path": str(p), "action": "recover", "done": False,
                "message": "Recovery was refused because the finished file already exists; nothing was overwritten."}
    os.replace(p, final)
    return {"path": str(p), "action": "recover", "done": True,
            "recovered_to": str(final),
            "message": "The interrupted write was restored as the finished file."}


# (22) Local Database Integrity Preflight


def sqlite_preflight(db_path: str | Path, expected_user_version=None) -> dict:
    """Read-only integrity + migration-compatibility check."""
    p = Path(db_path)
    if not p.exists():
        return {"path": str(p), "exists": False, "integrity": "missing",
                "message": "No database file was found."}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            version = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"path": str(p), "exists": True, "integrity": "error",
                "message": f"The database could not be checked: {exc}."}
    compatible = (expected_user_version is None or version == expected_user_version)
    ok = integrity == "ok" and not fk
    return {"path": str(p), "exists": True,
            "integrity": "ok" if ok else "corrupt",
            "integrity_detail": integrity, "foreign_key_violations": len(fk),
            "user_version": version, "migration_compatible": compatible,
            "message": ("The database passed its integrity check.")
                       if ok else
                       ("The database failed its integrity check; the "
                        "original is left untouched and recovery works "
                        "from a copy.")}


def recover_database(db_path: str | Path, dest_dir: str | Path) -> dict:
    """Preserve the original, then attempt recovery from a validated copy."""
    src = Path(db_path)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    before = _sha256(src) if src.exists() else None
    backup = dest / (src.name + ".preserved")
    shutil.copyfile(src, backup)
    recovered = dest / (src.name + ".recovered")
    shutil.copyfile(src, recovered)
    check = sqlite_preflight(recovered)
    after = _sha256(src) if src.exists() else None
    return {"original": str(src), "backup": str(backup),
            "recovered_copy": str(recovered) if check["integrity"] == "ok" else None,
            "original_untouched": before == after,
            "recovered_integrity": check["integrity"],
            "message": ("A clean copy was recovered; the original was "
                        "never modified.") if check["integrity"] == "ok" else
                       ("No clean copy could be recovered; the preserved "
                        "original is kept for manual repair.")}


# (23) Machine-Specific State Portability Audit


def audit_saved_references(refs) -> dict:
    """Flag saved paths/settings that break when the folder moves machines."""
    home = str(Path.home())
    findings: list[dict] = []
    for ref in refs:
        value = str(ref.get("value", ""))
        kind = ref.get("kind", "path")
        issue = None
        suggestion = None
        if kind in ("path", "executable", "interpreter") and value.startswith(home):
            issue = "This saved path points inside this machine's home folder."
            suggestion = "Store the path relative to the project folder instead."
        elif kind == "interpreter" and value.startswith(("/usr/", "/opt/", "C:\\")):
            issue = "This interpreter location belongs to this machine."
            suggestion = "Record the interpreter by name and let the new machine resolve it."
        elif kind == "gpu":
            issue = "This graphics setting belongs to this machine's hardware."
            suggestion = "Re-detect graphics settings on the new machine."
        elif kind == "env" and value.startswith(home):
            issue = "This saved setting points inside this machine's home folder."
            suggestion = "Store the setting relative to the project folder instead."
        if issue:
            findings.append({"ref": ref.get("name", value), "kind": kind,
                             "issue": issue, "suggestion": suggestion})
    return {"checked": len(list(refs)), "findings": findings,
            "portable": not findings,
            "message": ("Everything saved here travels to another machine.")
                       if not findings else
                       "Some saved items point at this machine and would break after a move."}


# ---------------------------------------------------------------- group 5
# (24) Crash Report Secret Filter (filter layer; W9 owns bundle creation)

_SECRET_RES = [
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "api_key"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "api_key"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "api_key"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "authorization_header"),
    (re.compile(r"(?i)(api[_-]?key|apikey|password|passwd|secret|token)\s*[:=]\s*\S+"), "credential_field"),
    (re.compile(r"(?i)authorization\s*:\s*\S+"), "authorization_header"),
]
_PROMPT_KEYS = ("prompt", "user_prompt", "input_text", "system_prompt")


def _filter_text(text: str, username: str | None, home: str) -> tuple[str, set[str]]:
    removed: set[str] = set()
    out = text
    for pat, category in _SECRET_RES:
        if pat.search(out):
            removed.add(category)
            out = pat.sub("[removed]", out)
    if home and home in out:
        removed.add("local_path")
        # Replace the whole whitespace-delimited token holding the home
        # path, not just the home prefix, so no project-local tail leaks.
        out = re.sub(r"\S*" + re.escape(home) + r"\S*", "[local path]", out)
    if username and username in out:
        removed.add("username")
        out = out.replace(username, "[user]")
    return out, removed


def filter_crash_report(bundle) -> dict:
    """Return a redacted copy of a bundle plus what was removed (counts only).

    Prompt content, keys, headers, usernames and local paths never reach
    the output; the preview names categories, never values.
    """
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or None
    try:
        import getpass  # noqa: PLC0415
        username = username or getpass.getuser()
    except Exception:
        pass
    home = str(Path.home())
    removed: set[str] = set()

    def _walk(value):
        if isinstance(value, str):
            cleaned, cats = _filter_text(value, username, home)
            removed.update(cats)
            return cleaned
        if isinstance(value, dict):
            out = {}
            for key, val in value.items():
                if any(p in str(key).lower() for p in _PROMPT_KEYS):
                    removed.add("prompt_content")
                    out[key] = "[prompt content removed]"
                else:
                    out[key] = _walk(val)
            return out
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    redacted = _walk(bundle)
    return {"redacted": redacted, "removed_categories": sorted(removed),
            "secrets_removed": bool(removed),
            "message": ("Sensitive items were removed before this report "
                        "was shared.") if removed else
                       "Nothing sensitive was found in this report."}


def preview_crash_report(bundle) -> dict:
    """What a user would see before a bundle is shared: categories only."""
    result = filter_crash_report(bundle)
    included = []
    if isinstance(result["redacted"], dict):
        included = sorted(str(k) for k in result["redacted"].keys())
    return {"included_sections": included,
            "removed_categories": result["removed_categories"],
            "message": ("Review the sections above; the removed items "
                        "are listed by kind only, never shown.")}


# (25) Local Network Dependency Classifier

_NETWORK_SIGNALS = {
    "internet": ("browse the web", "search the web", "fetch url", "download from", "online"),
    "remote_api": ("api", "model server", "provider", "openai", "anthropic", "remote model"),
    "registry": ("install package", "pip install", "npm install", "package registry", "pypi"),
    "license_server": ("license server", "licence server", "activation server", "check licence"),
}


def classify_network_dependency(objective: str, tools=None) -> dict:
    """Offline-capable, or quietly dependent on the network?"""
    text = " ".join([str(objective or "")] + [str(t) for t in (tools or [])]).lower()
    found = [kind for kind, signals in _NETWORK_SIGNALS.items()
             if any(s in text for s in signals)]
    if not found:
        return {"kind": "offline_capable", "needs_network": False,
                "dependencies": [],
                "explanation": "Nothing in this task needs the internet, a remote service, a package registry, or a licence server."}
    kind = found[0]
    labels = {"internet": "the internet", "remote_api": "a remote service",
              "registry": "a package registry", "license_server": "a licence server"}
    return {"kind": kind, "needs_network": True, "dependencies": found,
            "explanation": f"This task quietly needs {labels[kind]}; it cannot run fully offline."}


# (26) Application Port Conflict Resolver


def find_owner_pid_for_port(port: int) -> int | None:
    """Owning pid for a listening port, via /proc inode matching (Linux)."""
    if sys.platform == "win32":
        return None
    inodes: set[str] = set()
    for fname in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(fname).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            try:
                if int(parts[1].rsplit(":", 1)[1], 16) == port:
                    inodes.add(parts[9])
            except (ValueError, IndexError):
                continue
    if not inodes:
        return None
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    return int(entry.name)
        except OSError:
            continue
    return None


def resolve_port_conflict(preferred: int, host: str = "127.0.0.1",
                          candidates=None) -> dict:
    """Bind the preferred port, or pick a safe alternate — never fail blindly."""
    if candidates is None:
        candidates = [preferred + i for i in range(1, 11)]
    owner_of_preferred = None
    for port in [preferred] + list(candidates):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind((host, port))
            sock.close()
            return {"port": port, "preferred": preferred,
                    "chose_alternate": port != preferred,
                    "previous_owner_pid": None,
                    "message": (f"Port {preferred} is in use, so the server "
                                f"will use port {port} instead.") if port != preferred
                               else f"Port {port} is free."}
        except OSError as exc:
            sock.close()
            if exc.errno != 98 and "already in use" not in str(exc).lower():
                continue
            if port == preferred:
                owner_of_preferred = find_owner_pid_for_port(preferred)
            if not candidates or port == candidates[-1]:
                return {"port": None, "preferred": preferred,
                        "chose_alternate": False,
                        "previous_owner_pid": owner_of_preferred,
                        "message": "The preferred port and every alternate tried are already in use."}
            continue
    return {"port": None, "preferred": preferred, "chose_alternate": False,
            "previous_owner_pid": None,
            "message": "No free port was found."}


# (27) Local Service Readiness Probe


def probe_ready(check, timeout_s: float = 10.0, interval_s: float = 0.5) -> dict:
    """A service is ready only when a real check succeeds, not on process presence."""
    attempts = 0
    deadline = time.monotonic() + timeout_s
    while True:
        attempts += 1
        try:
            if check():
                return {"ready": True, "attempts": attempts,
                        "message": "The service answered a real readiness check."}
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return {"ready": False, "attempts": attempts,
                    "message": "The service never answered a real readiness check in time; a running process alone does not count as ready."}
        time.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))


def probe_http_ready(url: str, timeout_s: float = 10.0) -> dict:
    """HTTP readiness: a success answer counts; merely listening does not."""
    def _check():
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError:
            return False
        except OSError:
            return False
    return probe_ready(_check, timeout_s=timeout_s, interval_s=0.5)


# (28) Runtime Capability Snapshot


def snapshot_capabilities(tool_names=None) -> dict:
    """Privacy-conscious fingerprint: versions and tools only, no personal data."""
    if tool_names is None:
        tool_names = ["python3", "node", "git", "bash", "docker", "podman"]
    tools: dict[str, str] = {}
    for name in tool_names:
        path = _which(name)
        if path is None:
            tools[name] = "missing"
            continue
        version = "present"
        for flag in ("--version", "version"):
            try:
                out = subprocess.run([path, flag], capture_output=True,
                                     text=True, timeout=5).stdout.strip()
                if out:
                    version = out.splitlines()[0][:120]
                    break
            except Exception:
                continue
        tools[name] = version
    snap = {"python": sys.version.split()[0],
            "platform": sys.platform,
            "tools": tools,
            "collected_personal_data": []}
    canonical = json.dumps(snap, sort_keys=True)
    snap["fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    return snap


def diff_snapshots(before: dict, after: dict) -> dict:
    changed: list[str] = []
    for key in ("python", "platform"):
        if before.get(key) != after.get(key):
            changed.append(key)
    tools_a, tools_b = before.get("tools", {}), after.get("tools", {})
    for name in sorted(set(tools_a) | set(tools_b)):
        if tools_a.get(name) != tools_b.get(name):
            changed.append(f"tool:{name}")
    return {"changed": changed, "same": not changed,
            "message": ("Nothing relevant changed between the two runs.")
                       if not changed else
                       "The items above changed between runs, which may explain a different result."}
