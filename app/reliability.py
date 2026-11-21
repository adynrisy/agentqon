"""AgentQon — reliability & privacy shared utilities (gap-security).

One module, stdlib only, holding the cross-cutting rules other
workstreams keep re-implementing ad hoc:

TIMEZONE POLICY (stated once, used everywhere here): every timestamp
this module emits is UTC, ISO-8601 with an explicit offset
(``datetime.now(timezone.utc).isoformat()``). Local time is a display
choice for the UI; storage and logs are always UTC.

RETENTION POLICIES (stated explicitly, and SEPARATE):
  - operational logs: short-lived, ``LOG_RETENTION_DAYS`` (7 days);
  - audit records (security log, permission changes, approvals):
    longer, ``AUDIT_RETENTION_DAYS`` (365 days).
Nothing here deletes audit records on the log schedule, or vice versa.

Secrets: detection is ACTIVE (``detect_secrets`` finds credentials,
tokens and private identifiers in text before it spreads), and every
log line and error report is scrubbed on the way out (``scrub_secrets``,
``sanitize_error_report``). A finding is recorded by KIND only — the
value itself is never written anywhere.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG_RETENTION_DAYS = 7
AUDIT_RETENTION_DAYS = 365
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_MAX_BACKUPS = 5
SESSION_TTL_SECONDS = 3600.0
APPROVAL_TTL_SECONDS = 900.0
PERMISSION_CACHE_TTL_SECONDS = 300.0


def utc_now_iso() -> str:
    """The one timestamp shape: UTC, ISO-8601, explicit offset."""
    return datetime.now(timezone.utc).isoformat()


def new_correlation_id() -> str:
    return uuid.uuid4().hex


# ------------------------------------------------------------ correlation
_correlation: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "gap_correlation", default={})


def set_correlation(mission_id=None, provider_request_id=None,
                    tool_execution_id=None, correlation_id=None) -> dict:
    """Set the end-to-end correlation context (mission → model call →
    tool call → log line). Returns the context dict; every log line
    written under it carries these ids."""
    ctx = {"correlation_id": correlation_id or new_correlation_id(),
           "mission_id": mission_id,
           "provider_request_id": provider_request_id,
           "tool_execution_id": tool_execution_id}
    _correlation.set(ctx)
    return ctx


def get_correlation() -> dict:
    return dict(_correlation.get())


def new_provider_request_id() -> str:
    return "pr_" + uuid.uuid4().hex[:20]


def new_tool_execution_id() -> str:
    return "te_" + uuid.uuid4().hex[:20]


# ------------------------------------------------------------ secrets
_SECRET_PATTERNS = [
    ("api_key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?([^\s'\"]+)")),
    ("bearer_token", re.compile(r"(?i)bearer\s+([A-Za-z0-9\-._~+/=]{8,})")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]+)")),
    ("secret", re.compile(r"(?i)(client[_-]?secret|secret[_-]?key)\s*[:=]\s*['\"]?([^\s'\"]+)")),
    ("token_assign", re.compile(r"(?i)(access[_-]?token|auth[_-]?token|session[_-]?token|token)\s*[:=]\s*['\"]?([^\s'\"]+)")),
    ("sk_token", re.compile(r"sk-[A-Za-z0-9\-_]{8,}")),
    ("email_private", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]


def detect_secrets(text: str) -> list[dict]:
    """ACTIVE detection: return one finding per secret KIND found
    (kind + a short, value-free hint). Never returns the value."""
    if not text:
        return []
    found: list[dict] = []
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append({"kind": kind,
                          "hint": "a value matching this pattern was present"})
    return found


def scrub_secrets(text: str) -> str:
    """Replace secret-shaped values with ``[removed]``. Idempotent."""
    if not text:
        return text
    out = text
    for _kind, pattern in _SECRET_PATTERNS:
        # Patterns with a capture group for the value: scrub the value.
        def _repl(m: "re.Match") -> str:
            if m.lastindex and m.lastindex >= 1:
                # Keep the label part, drop the value part.
                full = m.group(0)
                value = m.group(m.lastindex)
                return full[: full.rfind(value)] + "[removed]"
            return "[removed]"
        try:
            out = pattern.sub(_repl, out)
        except Exception:
            out = pattern.sub("[removed]", out)
    return out


def sanitize_error_report(report) -> dict:
    """Scrub an error report for the outside world: secrets removed,
    private fields dropped, never the raw object. Returns a plain
    dict with plain-words fields only."""
    if isinstance(report, dict):
        raw = dict(report)
    else:
        raw = {"error": str(report)}
    private_keys = {"api_key", "token", "secret", "password", "authorization",
                    "cookie", "private_key", "email", "phone", "address"}
    clean: dict = {}
    for key, value in raw.items():
        if key.lower() in private_keys:
            continue
        clean[key] = scrub_secrets(str(value)) if isinstance(value, str) else value
    if "error" in clean:
        clean["error"] = scrub_secrets(str(clean["error"]))
    clean.setdefault("note", "private details were removed from this report")
    return clean


# ------------------------------------------------------------ structured logs
LOG_LEVELS = ("debug", "info", "warning", "error")


class StructuredLogger:
    """Machine-parseable JSON-lines log with consistent fields.

    Every line: ts (UTC ISO), level, event, message, correlation_id,
    mission_id, provider_request_id, tool_execution_id, fields.
    Level filtering drops below ``min_level``. Rotation caps the file
    (``LOG_MAX_BYTES`` + ``LOG_MAX_BACKUPS``) so logs can never fill
    the disk unbounded. Secret findings are counted by kind — the
    values are scrubbed before the line is written."""

    def __init__(self, path: Path | str, min_level: str = "info",
                 max_bytes: int = LOG_MAX_BYTES,
                 max_backups: int = LOG_MAX_BACKUPS):
        self.path = Path(path)
        self.min_level = min_level if min_level in LOG_LEVELS else "info"
        self.max_bytes = max_bytes
        self.max_backups = max_backups
        self.secret_findings: list[dict] = []
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
            for i in range(self.max_backups - 1, 0, -1):
                older = self.path.with_suffix(f".{i}.log")
                newer = self.path.with_suffix(f".{i + 1}.log")
                if older.exists():
                    older.replace(newer)
            self.path.replace(self.path.with_suffix(".1.log"))
        except OSError:
            pass

    def log(self, level: str, event: str, message: str,
            fields: dict | None = None, correlation: dict | None = None) -> dict | None:
        if LOG_LEVELS.index(level) < LOG_LEVELS.index(self.min_level):
            return None
        ctx = correlation if correlation is not None else get_correlation()
        clean_message = scrub_secrets(message)
        findings = detect_secrets(message)
        for finding in findings:
            self.secret_findings.append(
                {"kind": finding["kind"], "event": event, "at": utc_now_iso()})
        record = {"ts": utc_now_iso(), "level": level, "event": event,
                  "message": clean_message,
                  "correlation_id": ctx.get("correlation_id"),
                  "mission_id": ctx.get("mission_id"),
                  "provider_request_id": ctx.get("provider_request_id"),
                  "tool_execution_id": ctx.get("tool_execution_id"),
                  "fields": {k: scrub_secrets(str(v)) for k, v in (fields or {}).items()}}
        with self._lock:
            self._rotate()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        return record

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def search(self, correlation_id: str | None = None,
               event: str | None = None, level: str | None = None) -> list[dict]:
        """Searchable logs: filter stored lines by correlation, event, level."""
        rows = self.read_all()
        return [r for r in rows
                if (correlation_id is None or r.get("correlation_id") == correlation_id)
                and (event is None or r.get("event") == event)
                and (level is None or r.get("level") == level)]


def retention_policy_text() -> str:
    """The two retention policies, stated explicitly and separately."""
    return (f"Operational logs are kept {LOG_RETENTION_DAYS} days and then "
            f"removed. Audit records (security events, permission changes, "
            f"approvals) are kept {AUDIT_RETENTION_DAYS} days. The two "
            f"schedules are separate: audit records are never removed on "
            f"the operational-log schedule.")


def prune_old_files(directory: Path | str, days: int, suffix: str = ".log") -> int:
    """Remove files older than ``days``. Returns the count removed."""
    cutoff = time.time() - days * 86400
    removed = 0
    for path in Path(directory).glob(f"*{suffix}"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def export_logs(records: list[dict], include_sensitive: bool = False) -> list[dict]:
    """Log export: the user can take their logs out. Sensitive data is
    EXCLUDED by default — opt-in only, and even then scrubbed."""
    out = []
    for record in records:
        row = dict(record)
        if not include_sensitive:
            row.pop("fields", None)
            row["message"] = scrub_secrets(str(row.get("message", "")))
        out.append(row)
    return out


# ------------------------------------------------------------ trace sampling
class TraceSampler:
    """Sample, don't drown: a trace is kept when its stable hash falls
    under the rate. The same trace id always gets the same decision."""

    def __init__(self, rate: float = 0.1):
        self.rate = min(max(rate, 0.0), 1.0)

    def keep(self, trace_id: str) -> bool:
        digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF <= self.rate


# ------------------------------------------------------------ pressure
def check_pressure(path: str | os.PathLike | None = None) -> list[str]:
    """Disk-space + memory-pressure monitoring, in plain words, BEFORE
    failure. Returns warnings (empty = all clear)."""
    warnings: list[str] = []
    try:
        usage = shutil.disk_usage(str(path or "/"))
        free_pct = usage.free / usage.total * 100 if usage.total else 100
        if free_pct < 10:
            warnings.append(
                f"disk space is low: only {free_pct:.0f}% free — "
                "free up space before starting large work")
        elif free_pct < 20:
            warnings.append(
                f"disk space is getting low: {free_pct:.0f}% free")
    except OSError:
        pass
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        fields = dict(line.split(":", 1) for line in meminfo.splitlines() if ":" in line)
        total = int(fields["MemTotal"].split()[0])
        avail = int(fields["MemAvailable"].split()[0])
        avail_pct = avail / total * 100 if total else 100
        if avail_pct < 10:
            warnings.append(
                f"memory is low: only {avail_pct:.0f}% available — "
                "close other work before starting a large mission")
    except (OSError, KeyError, ValueError, IndexError):
        pass  # non-Linux hosts: disk check above is the signal
    return warnings


# ------------------------------------------------------------ backup verification
def backup_status(backup_path: str | os.PathLike | None,
                  restore_tested: bool, restore_tested_at: str | None = None) -> dict:
    """A backup that was never restore-tested is reported as
    UNVERIFIED, never as safe. Plain words only."""
    exists = bool(backup_path) and Path(backup_path).exists()
    if not exists:
        return {"status": "missing",
                "plain": "no backup was found — nothing is saved to restore from"}
    if not restore_tested:
        return {"status": "unverified",
                "plain": "a backup exists but it has never been restore-tested, "
                         "so it cannot be counted as safe yet"}
    return {"status": "verified",
            "plain": "backup exists and a restore was tested"
                    + (f" on {restore_tested_at}" if restore_tested_at else "")}


# ------------------------------------------------------------ startup + schema
def validate_startup(checks: dict) -> list[str]:
    """Fail-fast startup validation. ``checks`` maps a name to True
    (healthy) or a plain-words reason string (unhealthy). Returns the
    list of plain-words reasons that must block startup."""
    reasons = []
    for name, value in checks.items():
        if value is not True:
            reasons.append(str(value) if isinstance(value, str)
                           else f"{name} is not ready — the harness cannot start safely")
    return reasons


SCHEMA_VERSION_KEY = "gap_schema_version"


def check_schema_version(store, expected_version: int) -> dict:
    """Database migration check at boot: the schema version recorded
    in the database must match the version the code expects. A
    mismatch is a plain-words boot refusal, not a silent drift."""
    try:
        store._exec("CREATE TABLE IF NOT EXISTS gap_schema_meta"
                    " (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = store._one("SELECT value FROM gap_schema_meta WHERE key=?",
                         (SCHEMA_VERSION_KEY,))
        if row is None:
            store._exec("INSERT INTO gap_schema_meta VALUES (?, ?)",
                        (SCHEMA_VERSION_KEY, str(expected_version)))
            return {"ok": True, "recorded": expected_version,
                    "plain": "schema version recorded for the first time"}
        recorded = int(row["value"])
        if recorded != expected_version:
            return {"ok": False, "recorded": recorded, "expected": expected_version,
                    "plain": (f"the saved data was made for schema version "
                              f"{recorded} but this harness expects version "
                              f"{expected_version} — run the migration "
                              f"before starting")}
        return {"ok": True, "recorded": recorded,
                "plain": "saved data matches this harness version"}
    except Exception as exc:
        return {"ok": False, "plain": f"could not check the saved data version: {exc}"}


# ------------------------------------------------------------ graceful shutdown
class GracefulShutdown:
    """In-flight work finishes or checkpoints — never silently dropped.
    ``begin_work``/``finish_work`` track live work; ``shutdown`` runs
    registered checkpoints and reports what finished vs checkpointed."""

    def __init__(self):
        self._in_flight: set = set()
        self._checkpoints: dict[str, callable] = {}
        self._lock = threading.Lock()

    def begin_work(self, work_id: str) -> None:
        with self._lock:
            self._in_flight.add(work_id)

    def finish_work(self, work_id: str) -> None:
        with self._lock:
            self._in_flight.discard(work_id)

    def register_checkpoint(self, name: str, fn) -> None:
        self._checkpoints[name] = fn

    def shutdown(self) -> dict:
        with self._lock:
            pending = sorted(self._in_flight)
        checkpointed = []
        for name, fn in self._checkpoints.items():
            try:
                fn()
                checkpointed.append(name)
            except Exception:
                continue
        return {"pending_work": pending,
                "checkpointed": checkpointed,
                "plain": ("all running work was checkpointed"
                          if checkpointed or not pending
                          else "some work could not be checkpointed — see the log")}


# ------------------------------------------------------------ sessions + approvals
@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    ttl_seconds: float = SESSION_TTL_SECONDS

    def expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.last_seen_at) > self.ttl_seconds

    def touch(self, now: float | None = None) -> None:
        self.last_seen_at = time.time() if now is None else now


class SessionStore:
    """Sessions expire: a session idle past its lifetime stops working
    until the user signs in again. No silent immortality."""

    def __init__(self, ttl_seconds: float = SESSION_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str) -> Session:
        session = Session(session_id=new_correlation_id(), user_id=user_id,
                          ttl_seconds=self.ttl_seconds)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str, now: float | None = None) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.expired(now):
            return None
        session.touch(now)
        return session

    def expire_stale(self, now: float | None = None) -> int:
        with self._lock:
            stale = [sid for sid, s in self._sessions.items() if s.expired(now)]
            for sid in stale:
                del self._sessions[sid]
        return len(stale)


@dataclass
class Approval:
    approval_id: str
    user_id: str
    action: str
    issued_at: float = field(default_factory=time.time)
    ttl_seconds: float = APPROVAL_TTL_SECONDS

    def fresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.issued_at) <= self.ttl_seconds


def revalidate_approval(approval: Approval | None, action: str,
                        now: float | None = None) -> tuple[bool, str]:
    """Permission revalidation before sensitive actions: a stale (or
    missing, or wrong-action) approval NEVER authorizes a sensitive
    step. The caller must ask the user again."""
    if approval is None:
        return False, "no approval was given for this step — ask the user first"
    if approval.action != action:
        return False, "the approval given was for a different step — ask the user again"
    if not approval.fresh(now):
        return False, "the approval has expired — ask the user again"
    return True, "approval is current"


# ------------------------------------------------------------ permission cache + audit
class PermissionCache:
    """Cached permission answers EXPIRE. A cached 'yes' from ten
    minutes ago is not a 'yes' now."""

    def __init__(self, ttl_seconds: float = PERMISSION_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}
        self._audit: list[dict] = []
        self._lock = threading.Lock()

    def set(self, key: str, allowed: bool, now: float | None = None,
            actor: str = "user") -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._cache[key] = (allowed, now)
            self._audit.append({"at": utc_now_iso(), "event": "permission.changed",
                                "key": key, "allowed": allowed, "actor": actor})

    def get(self, key: str, now: float | None = None) -> bool | None:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._cache.get(key)
        if entry is None:
            return None
        allowed, stored_at = entry
        if (now - stored_at) > self.ttl_seconds:
            with self._lock:
                self._cache.pop(key, None)
            return None
        return allowed

    def audit_log(self) -> list[dict]:
        with self._lock:
            return list(self._audit)


def audit_permission_change(cache: PermissionCache, key: str, allowed: bool,
                            actor: str = "user") -> dict:
    """Permission-change auditing: every grant/revoke/change is
    recorded (who, what, when) — the audit entry, not just the cache."""
    cache.set(key, allowed, actor=actor)
    return cache.audit_log()[-1]


class SecurityEventDeduper:
    """Security-event dedup: the same event repeated within the window
    is counted, not re-logged — the log stays readable during a storm."""

    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self._seen: dict[str, dict] = {}
        self._lock = threading.Lock()

    def should_log(self, event: str, key: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        dedup_key = f"{event}:{key}"
        with self._lock:
            entry = self._seen.get(dedup_key)
            if entry is None or (now - entry["first_at"]) > self.window_seconds:
                self._seen[dedup_key] = {"first_at": now, "suppressed": 0}
                return {"log": True, "suppressed": 0}
            entry["suppressed"] += 1
            return {"log": False, "suppressed": entry["suppressed"]}


def normalize_rate_limit_key(key: str) -> str:
    """Rate-limit key normalization: the same caller must always land
    in the same bucket — case, surrounding space, and separator
    differences must not create a second, unmetered bucket."""
    normalized = (key or "").strip().lower()
    normalized = re.sub(r"[\s_\-]+", ":", normalized)
    return normalized


@dataclass
class RetentionControls:
    """Data-retention controls: the user sets how long their data is
    kept, per data kind. Defaults are the stated policies."""
    log_days: int = LOG_RETENTION_DAYS
    audit_days: int = AUDIT_RETENTION_DAYS
    mission_days: int = 365

    def deadline(self, kind: str, created_at_iso: str) -> str:
        days = {"log": self.log_days, "audit": self.audit_days,
                "mission": self.mission_days}.get(kind, self.log_days)
        try:
            created = datetime.fromisoformat(created_at_iso)
        except ValueError:
            created = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        from datetime import timedelta
        return (created + timedelta(days=days)).isoformat()

    def plain(self) -> str:
        return (f"your logs are kept {self.log_days} days, audit records "
                f"{self.audit_days} days, and missions {self.mission_days} "
                f"days — you can change these at any time")
