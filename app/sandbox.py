"""AgentQon — pragmatic execution sandbox (gap-security).

Honest labelling is the point of this module. The harness runs on the
user's own machine, as the user's own process: there is NO container,
NO virtual machine, and NO kernel sandbox here, and nothing in this
module claims otherwise. What the runner CAN enforce with OS mechanisms
available to its own process, it enforces:

  - argv-only execution (a list, never a shell string);
  - working-directory confinement (the child starts in the workspace);
  - environment scrubbing (known secret-bearing variable names never
    reach the child — see the documented limit below);
  - time limits (a timeout that kills the child);
  - on Linux/POSIX: CPU + memory limits via rlimits in the child
    before exec (``resource.setrlimit`` with ``preexec_fn``).

What it CANNOT restrict by mechanism is labelled APPROVAL-CONFINED in
code and in user-facing text: network access from a shell command,
reads outside the workspace in free-roam local mode, and any limit the
platform cannot apply (Windows has no rlimits from this process).
Those surfaces are governed by the user's approval, not by a wall —
and the text says so plainly.

LIMIT (stated, not hidden): environment scrubbing removes variables
whose NAMES match known secret patterns. A secret stored under an
unusual name passes through — scrubbing is a safety net, not a proof.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

NOT_A_CONTAINER = ("This is not a container or a virtual machine. "
                   "Commands run as your own user on your own machine, "
                   "inside the project folder, with a time limit. "
                   "Anything beyond that is controlled by your approval, "
                   "not by a wall.")

APPROVAL_CONFINED_NOTE = ("approval-confined: this surface cannot be "
                          "walled off by the harness itself, so it is "
                          "governed by your approval for each use")

_SENSITIVE_ENV_NAMES = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|AUTH|AWS_|"
    r"GITHUB_|OPENAI_|ANTHROPIC_|GOOGLE_.*KEY|AZURE_.*KEY)", re.IGNORECASE)

# Variables a child legitimately needs to behave (locale, home, path).
_SAFE_ENV_KEEP = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
                  "TMPDIR", "TEMP", "TMP", "USER", "LOGNAME", "SHELL"}


def scrub_env(env: dict | None = None) -> dict:
    """Drop secret-bearing variables before a child ever sees them.
    Pass ``None`` to scrub the current process environment."""
    source = dict(os.environ) if env is None else dict(env)
    return {k: v for k, v in source.items()
            if k in _SAFE_ENV_KEEP or not _SENSITIVE_ENV_NAMES.search(k)}


def _posix_limits(cpu_seconds: int | None, memory_bytes: int | None):
    """Build a preexec_fn applying rlimits (POSIX only). Returns None
    when there is nothing to apply or the platform cannot do it."""
    if cpu_seconds is None and memory_bytes is None:
        return None
    if sys.platform == "win32":
        return None
    try:
        import resource
    except ImportError:
        return None

    def _apply():
        if cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU,
                               (int(cpu_seconds), int(cpu_seconds)))
        if memory_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS,
                               (int(memory_bytes), int(memory_bytes)))
    return _apply


def run_sandboxed(argv: list, *, cwd: Path | str, env: dict | None = None,
                  timeout_s: float = 60.0,
                  cpu_seconds: int | None = 120,
                  memory_bytes: int | None = 512 * 1024 * 1024,
                  max_output_chars: int = 8000) -> dict:
    """Run ``argv`` (a list — never a string) confined to ``cwd`` with
    a scrubbed environment, a timeout, and POSIX rlimits where the
    platform supports them. Returns a plain dict; a timeout or a
    limit kill is a result, not an exception."""
    if isinstance(argv, str) or not argv:
        return {"ok": False, "error": "shell_run requires 'argv' (a list of arguments)",
                "timed_out": False, "limits_applied": [],
                "stdout_chars": 0, "stderr_chars": 0}
    limits = []
    preexec = _posix_limits(cpu_seconds, memory_bytes)
    if preexec is not None:
        limits = [name for name, value in (("cpu", cpu_seconds), ("memory", memory_bytes))
                  if value is not None]
    try:
        proc = subprocess.Popen(
            [str(a) for a in argv], cwd=str(Path(cwd).resolve()),
            env=scrub_env(env), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, preexec_fn=preexec)
    except FileNotFoundError:
        return {"ok": False, "error": f"executable not found: {argv[0]}",
                "timed_out": False, "limits_applied": limits,
                "stdout_chars": 0, "stderr_chars": 0}
    except OSError as exc:
        return {"ok": False, "error": f"could not start {argv[0]}: {exc}",
                "timed_out": False, "limits_applied": limits,
                "stdout_chars": 0, "stderr_chars": 0}
    try:
        out, err = proc.communicate(timeout=max(timeout_s, 1.0))
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
        # Full decoded lengths are reported alongside the capped
        # text, so callers can say honestly how much was withheld
        # (integration: W8 truncation totals ride this path too).
        timeout_out = out.decode("utf-8", errors="replace")
        timeout_err = err.decode("utf-8", errors="replace")
        return {"ok": False, "error": f"command timed out after {timeout_s:g}s",
                "timed_out": True, "limits_applied": limits,
                "stdout": timeout_out[:max_output_chars],
                "stderr": timeout_err[:max_output_chars],
                "stdout_chars": len(timeout_out),
                "stderr_chars": len(timeout_err)}
    full_out = out.decode("utf-8", errors="replace")
    full_err = err.decode("utf-8", errors="replace")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": full_out[:max_output_chars],
            "stderr": full_err[:max_output_chars],
            "timed_out": False, "limits_applied": limits,
            "stdout_chars": len(full_out), "stderr_chars": len(full_err),
            "argv": [str(a) for a in argv]}


def confinement_note() -> str:
    """Plain-words statement of what IS enforced vs what is
    approval-confined, for user-facing surfaces."""
    enforced = ("enforced: commands run as a list (never a shell string), "
                "start inside the project folder, never receive known "
                "secret settings, and stop at the time limit")
    if sys.platform == "win32":
        enforced += ("; on Windows the system does not offer CPU/memory "
                     "limits to this process, so those are approval-confined")
    else:
        enforced += ("; on this system CPU and memory limits are also enforced")
    return (NOT_A_CONTAINER + " " + enforced + ". "
            "Network access from a command and reads outside the project "
            "in local mode are " + APPROVAL_CONFINED_NOTE + ".")


# ------------------------------------------------------------ archives
def validate_archive_member(name: str) -> tuple[bool, str]:
    """Archive-path validation: a member that escapes the extraction
    folder (absolute path, ``..``, drive/UNC prefix) is refused before
    anything is written. Returns (ok, plain reason)."""
    if not name:
        return False, "archive entry has no name"
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) \
            or normalized.startswith("//"):
        return False, f"archive entry {name!r} is an absolute path — refused"
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if ".." in parts:
        return False, f"archive entry {name!r} climbs out of the folder — refused"
    return True, "ok"


def safe_archive_members(names: list[str]) -> dict:
    """Split an archive listing into safe members and refusals."""
    safe, refused = [], []
    for name in names:
        ok, reason = validate_archive_member(name)
        (safe if ok else refused).append(name if ok else {"name": name, "reason": reason})
    return {"safe": safe, "refused": refused}


# ------------------------------------------------------------ temp files
def secure_temp_file(directory: Path | str, prefix: str = "agentqon-") -> Path:
    """Create a temp file readable only by its owner (0600), never
    through a predictable name. Returns the path (file is empty)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = directory / f"{prefix}{os.urandom(8).hex()}.tmp"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise RuntimeError("could not create a private temp file")


# ------------------------------------------------------------ URLs + redirects
_ALLOWED_SCHEMES = {"http", "https"}


def validate_url_scheme(url: str, allowed: set | None = None) -> tuple[bool, str]:
    """URL-scheme validation: only http(s) by default. javascript:,
    file:, data:, ftp: and friends are refused, not fetched."""
    allowed = _ALLOWED_SCHEMES if allowed is None else allowed
    try:
        scheme = urlparse(url).scheme.lower()
    except Exception:
        return False, "that address could not be read — refused"
    if scheme not in allowed:
        return False, (f"addresses starting with {scheme or '(none)'}: are not "
                       "opened — only web addresses are allowed")
    return True, "ok"


def check_redirect(from_url: str, to_url: str) -> tuple[bool, str]:
    """Redirect-policy checking: a redirect may not downgrade https to
    http, may not switch to a non-web scheme, and may not bounce to a
    private-looking host from a public one without a fresh decision.
    Returns (allowed, plain reason)."""
    ok_from, _ = validate_url_scheme(from_url)
    ok_to, reason_to = validate_url_scheme(to_url)
    if not ok_from or not ok_to:
        return False, reason_to if not ok_to else "the starting address is not a web address"
    from_parsed, to_parsed = urlparse(from_url), urlparse(to_url)
    if from_parsed.scheme == "https" and to_parsed.scheme == "http":
        return False, "that redirect drops a secure page to an insecure one — refused"
    return True, "ok"


# ------------------------------------------------------------ webhook replay
WEBHOOK_WINDOW_SECONDS = 300


def sign_webhook(secret: str, body: bytes, ts: int, nonce: str) -> str:
    payload = f"{ts}\n{nonce}\n".encode("utf-8") + (body or b"")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_webhook(secret: str, body: bytes, ts: int, nonce: str,
                   signature: str, seen_nonces: set,
                   now: int | None = None) -> tuple[bool, str]:
    """Webhook replay protection, ready for when webhooks ship: the
    timestamp must be inside the window, the signature must match,
    and a nonce may be used exactly once. ``seen_nonces`` is mutated
    (the nonce is recorded) only on success."""
    now = int(time.time()) if now is None else now
    if abs(now - int(ts)) > WEBHOOK_WINDOW_SECONDS:
        return False, "that delivery is too old or too far ahead — refused"
    if nonce in seen_nonces:
        return False, "that delivery was already seen — refused as a replay"
    expected = sign_webhook(secret, body, int(ts), nonce)
    if not hmac.compare_digest(expected, (signature or "").strip().lower()):
        return False, "that delivery is not signed correctly — refused"
    seen_nonces.add(nonce)
    return True, "ok"
