"""Shield (v20.2) unit tests: startup refusals, M2 permission
enforcement, the security log, and the CLI channel HMAC
(sign/verify, +-120 s window, nonce replay cache)."""
from __future__ import annotations

import dataclasses
import os
import stat
import time

import pytest

from app.config import Settings
from app.shield import (assert_secure_startup, enforce_local_permissions,
                        reset_nonce_cache, security_log, sign_channel,
                        verify_channel)

SECRET = "unit-test-channel-secret"


def _settings(**kw) -> Settings:
    return dataclasses.replace(Settings(), **kw)


# ---------------------------------------------------------- startup refusals
def test_packaged_dev_without_token_refused():
    with pytest.raises(RuntimeError, match="local token"):
        assert_secure_startup(_settings(packaged=True, auth_mode="dev",
                                        local_token=""))


def test_packaged_dev_with_token_starts():
    assert_secure_startup(_settings(packaged=True, auth_mode="dev",
                                    local_token="tok"))
    assert_secure_startup(_settings(packaged=True, auth_mode="accounts"))


def test_nonloopback_bind_dev_refused_accounts_ok():
    with pytest.raises(RuntimeError, match="non-loopback"):
        assert_secure_startup(_settings(bind_host="0.0.0.0",
                                        auth_mode="dev"))
    assert_secure_startup(_settings(bind_host="0.0.0.0",
                                    auth_mode="accounts"))
    assert_secure_startup(_settings(bind_host="127.0.0.1", auth_mode="dev"))
    assert_secure_startup(_settings(bind_host="", auth_mode="dev"))


# ---------------------------------------------------------- M2 permissions
def test_enforce_local_permissions_tightens_loose_files(tmp_path):
    s = _settings(data_dir=tmp_path)
    (tmp_path / "artifacts").mkdir()
    db = tmp_path / "omnimind.db"
    db.write_bytes(b"x")
    key = tmp_path / "vault.key"
    key.write_bytes(b"k")
    for p in (tmp_path, tmp_path / "artifacts", db, key):
        os.chmod(p, 0o777)
    enforce_local_permissions(s)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "artifacts").stat().st_mode) == 0o700
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_security_log_appends_metadata_only(tmp_path):
    s = _settings(data_dir=tmp_path)
    security_log(s, "shield.test_event", path="/v1/agents", refused=True)
    log_file = tmp_path / "security.log"
    assert log_file.exists()
    line = log_file.read_text().strip()
    assert "shield.test_event" in line and "/v1/agents" in line
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


# ---------------------------------------------------------- channel HMAC
def setup_function():
    reset_nonce_cache()


def _sign(method="GET", path="/v1/agents", ts=None, nonce="n-1", body=b""):
    ts = str(int(time.time())) if ts is None else ts
    return ts, sign_channel(SECRET, method, path, ts, nonce, body)


def test_channel_roundtrip():
    ts, sig = _sign()
    ok, reason = verify_channel(SECRET, "GET", "/v1/agents", ts, "n-1",
                                sig, b"")
    assert ok, reason


def test_channel_replay_rejected():
    ts, sig = _sign()
    ok, _ = verify_channel(SECRET, "GET", "/v1/agents", ts, "n-1", sig, b"")
    assert ok
    ok, reason = verify_channel(SECRET, "GET", "/v1/agents", ts, "n-1",
                                sig, b"")
    assert not ok and "replay" in reason


def test_channel_stale_timestamp_rejected():
    old = str(int(time.time()) - 3600)
    sig = sign_channel(SECRET, "GET", "/v1/agents", old, "n-old", b"")
    ok, reason = verify_channel(SECRET, "GET", "/v1/agents", old, "n-old",
                                sig, b"")
    assert not ok and "window" in reason


def test_channel_tampered_body_rejected():
    ts, sig = _sign(method="POST", path="/v1/projects", nonce="n-2",
                    body=b'{"name":"a"}')
    ok, reason = verify_channel(SECRET, "POST", "/v1/projects", ts, "n-2",
                                sig, b'{"name":"b"}')
    assert not ok and "signature" in reason


def test_channel_wrong_secret_and_missing_headers():
    ts, sig = _sign()
    ok, _ = verify_channel("other-secret", "GET", "/v1/agents", ts, "n-1",
                           sig, b"")
    assert not ok
    ok, reason = verify_channel(SECRET, "GET", "/v1/agents", "", "", "", b"")
    assert not ok and "missing" in reason
    ok, reason = verify_channel("", "GET", "/v1/agents", ts, "n-1", sig, b"")
    assert not ok and "not configured" in reason
