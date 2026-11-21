"""API-level regression tests for the 2026-10-22 security audit
repair pass (hidden_files/audit-2026-10-22/AUDIT_SECURITY.md).

Unit-level pins live in tests/unit/test_security_audit_repair.py;
these cover the findings whose behaviour only exists at the HTTP
boundary: F3's clean 400 for a refused workspace_root, F7's sync
inheritance, F8's admin unlink path, and F12's per-IP auth brake.
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

import app.main as main
from tests.conftest import hdr


def _register(client, email=None, password="<redacted>"):
    email = email or f"sec_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/v1/auth/register",
                    json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def _accounts(monkeypatch, **overrides):
    new = dataclasses.replace(main.settings, auth_mode="accounts",
                              **overrides)
    monkeypatch.setattr(main, "settings", new)
    return new


def _bearer(reg):
    return {"Authorization": f"Bearer {reg['token']}",
            "X-Tenant-Id": reg["tenant_id"]}


def _saturate_ip_counters():
    """Fill this minute's per-IP auth buckets directly in the store
    (deterministic — no dependence on wall-clock window edges under
    load). Returns the keys seeded, for cleanup."""
    import time
    window = (int(time.time()) // 60) * 60
    keys = ("register-ip:testclient", "login-ip:testclient")
    for key in keys:
        cur = main.store._raw(
            "UPDATE rate_counters SET count=999"
            " WHERE key=? AND window_start=?", (key, window))
        if not (cur.rowcount or 0):
            main.store._raw(
                "INSERT INTO rate_counters (key, window_start, count)"
                " VALUES (?,?,999)", (key, window))
    main.store._commit()
    return keys


def _clear_ip_counters(keys):
    for key in keys:
        main.store._raw("DELETE FROM rate_counters WHERE key=?", (key,))
    main.store._commit()


# ------------------------------------------------------------ F12
def test_f12_login_and_register_have_a_per_ip_brake(client):
    # control: a fresh email registers fine (per-email + per-IP
    # buckets both empty for it)
    _register(client)
    keys = _saturate_ip_counters()
    try:
        # a FRESH email — its per-email bucket is empty — is refused
        # purely on the source-IP bucket: the spraying brake
        r = client.post("/v1/auth/register", json={
            "email": f"sec_{uuid.uuid4().hex[:8]}@example.com",
            "password": "long-enough-password"})
        assert r.status_code == 429
        # same for login: 429 before the credential check (not 401)
        r = client.post("/v1/auth/login", json={
            "email": f"spray_{uuid.uuid4().hex[:8]}@example.com",
            "password": "wrong-password-x"})
        assert r.status_code == 429
    finally:
        _clear_ip_counters(keys)


# ------------------------------------------------------------ F3
def test_f3_hosted_session_root_outside_tenant_area_is_a_clean_400(
        client, monkeypatch):
    reg = _register(client)
    _accounts(monkeypatch)
    r = client.post("/v1/agent-sessions", headers=_bearer(reg), json={
        "goal": "poke around", "workspace_root": "/etc"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    # the tenant's own area is accepted
    inside = (f"{main.settings.data_dir}/agent-workspaces/"
              f"{reg['tenant_id']}/ws1")
    r = client.post("/v1/agent-sessions", headers=_bearer(reg), json={
        "goal": "legit work", "workspace_root": inside})
    assert r.status_code == 201, r.text


# ------------------------------------------------------------ F7
def test_f7_hosted_sync_skips_local_connections_without_probing(
        client, monkeypatch):
    reg = _register(client)
    tenant_id = reg["tenant_id"]
    main.store.put_connection(tenant_id, {
        "id": "conn_local_api", "tenant_id": tenant_id,
        "owner_user_id": reg["user"]["id"], "kind": "local_endpoint",
        "provider_key": "local_endpoint", "label": "Ollama",
        "base_url": "http://127.0.0.1:9/v1", "state": "active",
        "masked_key": "", "allowed_models": [], "blocked_models": [],
        "budget_limit_usd": None, "routing_mode": "balanced",
        "data_policy": {}, "last_validated_at": None,
        "last_error_category": None, "models_count": 0,
        "created_at": "2026-10-22T00:00:00Z",
        "updated_at": "2026-10-22T00:00:00Z", "deleted_at": None})
    _accounts(monkeypatch)
    r = client.post("/v1/models/sync", headers=_bearer(reg))
    assert r.status_code == 200, r.text
    mine = [s for s in r.json()["syncs"]
            if s["connection_id"] == "conn_local_api"]
    assert len(mine) == 1
    assert mine[0]["status"] == "skipped"
    assert mine[0]["error"] == "local_target_in_hosted_mode"


# ------------------------------------------------------------ F8
def test_f8_admin_can_unlink_another_users_telegram_link(
        client, tenant, monkeypatch):
    monkeypatch.setenv("AGENTQON_TELEGRAM_BOT_TOKEN", "test-token")
    store = main.store
    store.put_telegram_link({
        "chat_id": "77001", "tenant_id": tenant, "user_id": "victim",
        "username": "victim", "role": "member",
        "linked_at": "2026-10-22T00:00:00Z"})
    store.put_telegram_link({
        "chat_id": "77002", "tenant_id": tenant, "user_id": "tester",
        "username": "tester", "role": "owner",
        "linked_at": "2026-10-22T00:00:00Z"})
    # the own-link endpoint removes ONLY the caller's link
    r = client.delete("/v1/channels/telegram/link", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["unlinked"] is True
    assert store.get_telegram_link_for_user(tenant, "victim") is not None
    # the admin path cuts the other user's link
    r = client.delete("/v1/channels/telegram/link/victim",
                      headers=hdr(tenant))
    assert r.status_code == 200, r.text
    assert r.json() == {"unlinked": True, "user_id": "victim"}
    assert store.get_telegram_link_for_user(tenant, "victim") is None
    r = client.delete("/v1/channels/telegram/link/victim",
                      headers=hdr(tenant))
    assert r.json()["unlinked"] is False
