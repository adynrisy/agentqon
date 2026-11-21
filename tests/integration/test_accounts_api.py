"""Phase 4 integration: accounts mode end-to-end.

Registration, sessions, tenant membership + role enforcement, OIDC
bearer tokens, brute-force rate limiting, logout revocation. The whole
file runs with OMNIMIND_AUTH_MODE replaced by "accounts" on the shared
test app; dev-mode behaviour is covered by every other test file.
"""
from __future__ import annotations

import dataclasses
import time
import uuid

import pytest

import app.main as main
from tests.conftest import MISSION_BODY


@pytest.fixture()
def accounts(monkeypatch):
    new = dataclasses.replace(main.settings, auth_mode="accounts",
                              rate_login_per_min=100, rate_run_per_min=100,
                              rate_validate_per_min=100)
    monkeypatch.setattr(main, "settings", new)
    return new


def _register(client, email=None, password="long-enough-password", tenant_id=None):
    email = email or f"u_{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "password": password}
    if tenant_id:
        body["tenant_id"] = tenant_id
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _hdr(token, tenant_id):
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def test_register_login_me_logout(client, accounts):
    reg = _register(client)
    token, tenant_id = reg["token"], reg["tenant_id"]
    assert reg["role"] == "owner"
    me = client.get("/v1/auth/me", headers=_hdr(token, tenant_id))
    assert me.status_code == 200 and me.json()["email"] == reg["user"]["email"]
    # duplicate registration rejected
    dup = client.post("/v1/auth/register",
                      json={"email": reg["user"]["email"], "password": "long-enough-password"})
    assert dup.status_code == 409
    # short password rejected
    short = client.post("/v1/auth/register",
                        json={"email": "shorty@example.com", "password": "short"})
    assert short.status_code == 400
    # login works, wrong password doesn't
    ok = client.post("/v1/auth/login", json={"email": reg["user"]["email"],
                                             "password": "long-enough-password"})
    assert ok.status_code == 200 and ok.json()["token"].startswith("omk_")
    bad = client.post("/v1/auth/login", json={"email": reg["user"]["email"],
                                              "password": "wrong-password-1"})
    assert bad.status_code == 401
    # logout revokes the session
    client.post("/v1/auth/logout", headers=_hdr(token, tenant_id))
    gone = client.get("/v1/auth/me", headers=_hdr(token, tenant_id))
    assert gone.status_code == 401


def test_accounts_mode_requires_auth_and_membership(client, accounts):
    # no credential at all
    r = client.get("/v1/missions", headers={"X-Tenant-Id": "org_demo"})
    assert r.status_code == 401
    # garbage credential
    r = client.get("/v1/missions", headers=_hdr("omk_garbage", "org_demo"))
    assert r.status_code == 401
    # valid user, tenant they don't belong to -> 404 (existence hidden)
    reg = _register(client)
    r = client.get("/v1/missions", headers=_hdr(reg["token"], "org_someone_else"))
    assert r.status_code == 404
    # their own tenant works
    r = client.get("/v1/missions", headers=_hdr(reg["token"], reg["tenant_id"]))
    assert r.status_code == 200


def test_role_enforcement_viewer_member_admin(client, accounts):
    owner = _register(client)
    t = owner["tenant_id"]
    oh = _hdr(owner["token"], t)
    # owner runs a mission end-to-end in accounts mode (stub model)
    created = client.post("/v1/missions", headers={**oh, "Idempotency-Key": "acct-run-001"},
                          json=MISSION_BODY)
    assert created.status_code in (200, 201), created.text
    mid = created.json()["mission_id"]
    ran = client.post(f"/v1/missions/{mid}/run", headers=oh)
    assert ran.status_code == 200 and ran.json()["mission"]["status"] == "completed"

    # a second user, added as viewer
    viewer = _register(client)
    add = client.post("/v1/tenants/members", headers=oh,
                      json={"email": viewer["user"]["email"], "role": "viewer"})
    assert add.status_code == 201, add.text
    vh = _hdr(viewer["token"], t)
    assert client.get("/v1/missions", headers=vh).status_code == 200  # can read
    blocked = client.post("/v1/missions", headers={**vh, "Idempotency-Key": "viewer-try-01"},
                          json=MISSION_BODY)
    assert blocked.status_code == 403  # cannot create
    blocked2 = client.post("/v1/connections", headers=vh, json={
        "kind": "direct_provider", "label": "x", "base_url": "https://api.example.com/v1",
        "api_key": "sk-test-1234"})
    assert blocked2.status_code == 403  # cannot touch connections

    # promote to member: can create missions, still cannot manage connections
    promoted = client.patch(f"/v1/tenants/members/{viewer['user']['id']}",
                            headers=oh, json={"role": "member"})
    assert promoted.status_code == 200
    ok_create = client.post("/v1/missions", headers={**vh, "Idempotency-Key": "member-ok-001"},
                            json=MISSION_BODY)
    assert ok_create.status_code in (200, 201)
    assert client.post("/v1/connections", headers=vh, json={
        "kind": "direct_provider", "label": "x", "base_url": "https://api.example.com/v1",
        "api_key": "sk-test-1234"}).status_code == 403

    # only an owner can grant owner; removal of last owner is refused
    other = _register(client)
    client.post("/v1/tenants/members", headers=oh,
                json={"email": other["user"]["email"], "role": "admin"})
    ah = _hdr(other["token"], t)
    assert client.post("/v1/tenants/members", headers=ah,
                       json={"email": viewer["user"]["email"], "role": "owner"}
                       ).status_code == 403
    assert client.delete(f"/v1/tenants/members/{owner['user']['id']}",
                         headers=oh).status_code == 409


def test_login_rate_limit(client, monkeypatch):
    new = dataclasses.replace(main.settings, auth_mode="accounts", rate_login_per_min=3)
    monkeypatch.setattr(main, "settings", new)
    email = f"rl_{uuid.uuid4().hex[:8]}@example.com"
    client.post("/v1/auth/register", json={"email": email, "password": "long-enough-password"})
    codes = []
    for _ in range(4):
        r = client.post("/v1/auth/login", json={"email": email, "password": "wrong-password-x"})
        codes.append(r.status_code)
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429 and int(r.headers.get("Retry-After", "0")) >= 1


def test_oidc_bearer_jit_and_membership(client, monkeypatch):
    from tests.unit.test_auth import _make_oidc
    verifier, mint, claims = _make_oidc()
    claims = {**claims, "email": f"oidc_{uuid.uuid4().hex[:6]}@example.com"}
    new = dataclasses.replace(main.settings, auth_mode="accounts",
                              oidc_issuer="https://idp.example.com",
                              oidc_client_id="omnimind-app")
    monkeypatch.setattr(main, "settings", new)
    monkeypatch.setattr(main, "oidc_verifier", verifier)
    token = mint(claims)
    # JIT-provisioned user can read /me...
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == claims["email"]
    # ...but belongs to no tenant until an owner adds them
    r = client.get("/v1/missions", headers={"Authorization": f"Bearer {token}",
                                            "X-Tenant-Id": "org_demo"})
    assert r.status_code == 404
    # forged token is rejected
    forged = token.split(".")[0] + ".e30." + token.split(".")[2]
    assert client.get("/v1/auth/me",
                      headers={"Authorization": f"Bearer {forged}"}).status_code == 401
