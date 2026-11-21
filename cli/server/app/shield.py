"""AgentQon Shield — server-side enforcement (v20.2, local-key mode).

Implements the server half of SHIELD_SPEC.md §B1/§C-13/§C-14:

  - assert_secure_startup(): the hard refusals. A packaged harness
    spawn (AGENTQON_PACKAGED=1) that still relies on dev-header auth
    without a per-install local token never starts (audit C1); dev
    auth on a declared non-loopback bind never starts (audit C2).
  - enforce_local_permissions(): M2 — the data directory and the
    secrets inside it are tightened at EVERY boot, including files
    created by older versions (data/ 0700, DB + vault.key 0600).
  - channel HMAC: the CLI signs each request with the per-install
    channel secret (method|path|ts|nonce|body-hash). Verification is
    constant-time, enforces a +-120 s window and a nonce replay cache.
    A presented-but-invalid signature is a hard refusal — there is no
    "signed OR token" fallback an attacker can downgrade to: the
    local token is only consulted when NO signature headers arrive
    (the browser UI path, which holds the token, never the secret).
  - security_log(): append-only, metadata-only security event log in
    the data dir (0600). Never logs secrets, tokens or bodies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

CHANNEL_WINDOW_SECONDS = 120

# ------------------------------------------------------------- HMAC channel
_nonce_lock = threading.Lock()
_nonce_seen: dict[str, float] = {}


def canonical_string(method: str, path: str, ts: str, nonce: str,
                     body: bytes) -> bytes:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([method.upper(), path, str(ts), nonce,
                      body_hash]).encode("utf-8")


def sign_channel(secret: str, method: str, path: str, ts: str,
                 nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"),
                    canonical_string(method, path, ts, nonce, body),
                    hashlib.sha256).hexdigest()


def verify_channel(secret: str, method: str, path: str, ts: str,
                   nonce: str, signature: str, body: bytes,
                   now: float | None = None) -> tuple[bool, str]:
    """Returns (ok, reason). Consumes the nonce on success."""
    if not secret:
        return False, "channel secret not configured"
    if not ts or not nonce or not signature:
        return False, "missing channel signature headers"
    try:
        ts_value = int(ts)
    except (TypeError, ValueError):
        return False, "bad channel timestamp"
    now = time.time() if now is None else now
    if abs(now - ts_value) > CHANNEL_WINDOW_SECONDS:
        return False, "channel timestamp outside the +-120s window"
    expected = sign_channel(secret, method, path, ts, nonce, body)
    if not hmac.compare_digest(expected, signature.strip().lower()):
        return False, "bad channel signature"
    with _nonce_lock:
        horizon = now - CHANNEL_WINDOW_SECONDS
        for n in [n for n, t in _nonce_seen.items() if t < horizon]:
            _nonce_seen.pop(n, None)
        if nonce in _nonce_seen:
            return False, "channel nonce replay"
        _nonce_seen[nonce] = ts_value
    return True, "ok"


def reset_nonce_cache() -> None:
    """Test seam: the replay cache is per-process state."""
    with _nonce_lock:
        _nonce_seen.clear()


# ------------------------------------------------------------- security log
def security_log(settings: Settings, event: str, **meta) -> None:
    """Append one metadata-only JSON line to <data>/security.log.

    Best-effort by design: a logging failure must never break the
    request path, but the file itself is created 0600 and tightened
    on every write.
    """
    try:
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "security.log"
        line = json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                           "event": event, **meta}) + "\n"
        fd = path.open("a", encoding="utf-8")
        try:
            fd.write(line)
        finally:
            fd.close()
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        log.warning("security_log write failed for event %s", event,
                    exc_info=True)


# ------------------------------------------------------------ M2 perms
def _chmod(path: Path, mode: int) -> None:
    try:
        if path.exists():
            path.chmod(mode)
    except OSError:
        pass


def enforce_local_permissions(settings: Settings) -> None:
    """Tighten the local data estate (audit M2). Idempotent and safe
    to run on every boot: directories 0700, the SQLite DB (+ WAL/SHM)
    and the vault key file 0600 — including pre-existing loose files.
    Hosted/Postgres deployments simply have fewer of these files."""
    data_dir = Path(settings.data_dir)
    _chmod(data_dir, 0o700)
    _chmod(settings.artifacts_dir, 0o700)
    _chmod(settings.sqlite_path, 0o600)
    _chmod(Path(str(settings.sqlite_path) + "-wal"), 0o600)
    _chmod(Path(str(settings.sqlite_path) + "-shm"), 0o600)
    _chmod(data_dir / "vault.key", 0o600)
    _chmod(data_dir / "security.log", 0o600)


# ------------------------------------------------------- startup checks
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", ""}


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def assert_secure_startup(settings: Settings) -> None:
    """Hard refusals (audit C1/C2). Raises RuntimeError; the caller
    (app.main import) lets it propagate so the process never serves
    in a forbidden configuration."""
    if settings.bind_host and not is_loopback_host(settings.bind_host) \
            and settings.auth_mode != "accounts":
        raise RuntimeError(
            "Shield refusal: dev-header auth (AGENTQON_AUTH_MODE="
            f"{settings.auth_mode}) must never serve on a non-loopback "
            f"bind ({settings.bind_host}). Set AGENTQON_AUTH_MODE="
            "accounts for hosted deployments.")
    if settings.packaged and settings.auth_mode != "accounts" \
            and not settings.local_token:
        raise RuntimeError(
            "Shield refusal: the packaged harness must not run on "
            "dev-header auth without a per-install local token "
            "(AGENTQON_LOCAL_TOKEN unset). Re-run `agentqon setup` — "
            "the CLI generates the token at install time.")


def boot_self_check(settings: Settings) -> str:
    """§C-13 boot self-check: permissions enforcement (M2) + the
    one-line Shield status the log and /health both carry. Never
    raises over hygiene — the hard refusals live in
    assert_secure_startup()."""
    enforce_local_permissions(settings)
    if settings.whatsapp_token and settings.whatsapp_phone_number_id \
            and not settings.whatsapp_app_secret:
        log.warning(
            "Shield: WhatsApp channel is configured WITHOUT an app "
            "secret — the webhook will refuse deliveries (fail "
            "closed) until AGENTQON_WHATSAPP_APP_SECRET is set.")
        security_log(settings, "shield.whatsapp_unsigned_channel")
    local = "token" if settings.local_token else "off"
    channel = "hmac" if settings.channel_secret else "off"
    status = (f"shield: packaged={'yes' if settings.packaged else 'no'} "
              f"auth={settings.auth_mode} local_token={local} "
              f"channel={channel} mode=local-key")
    log.info(status)
    security_log(settings, "shield.boot",
                 packaged=settings.packaged, auth_mode=settings.auth_mode,
                 local_token=bool(settings.local_token),
                 channel=bool(settings.channel_secret))
    return status
