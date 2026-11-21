"""Shield H3 (v20.2): role guards on the two tenant-wide control
surfaces the audit found unguarded — agent lifecycle changes and the
WhatsApp business connection (Embedded Signup exchange + disconnect).

Negative tests first: viewer and member must get 403; admin and
owner succeed. Runs in accounts mode, same fixture pattern as
test_accounts_api.py.
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

import app.main as main

PW = "long-enough-password"


@pytest.fixture()
def accounts(monkeypatch):
    new = dataclasses.replace(main.settings, auth_mode="accounts",
                              rate_login_per_min=100, rate_run_per_min=100,
                              rate_validate_per_min=100)
    monkeypatch.setattr(main, "settings", new)
    return new


def _register(client, email=None):
    email = email or f"u_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/v1/auth/register",
                    json={"email": email, "password": PW})
    assert r.status_code == 201, r.text
    return r.json()


def _hdr(token, tenant_id):
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


@pytest.fixture()
def cast(client, accounts):
    """An owner plus viewer/member/admin members of the owner's tenant."""
    owner = _register(client)
    tenant_id = owner["tenant_id"]
    people = {"owner": owner}
    for role in ("viewer", "member", "admin"):
        person = _register(client)
        r = client.post("/v1/tenants/members",
                        headers=_hdr(owner["token"], tenant_id),
                        json={"email": person["user"]["email"],
                              "role": role})
        assert r.status_code == 201, r.text
        people[role] = person
    return tenant_id, people


def test_lifecycle_requires_admin(client, cast):
    tenant_id, people = cast
    agents = client.get("/v1/agents",
                        headers=_hdr(people["owner"]["token"], tenant_id)
                        ).json()["agents"]
    agent = next(a for a in agents if a["lifecycle"] == "active")
    url = f"/v1/agents/{agent['agent_version_id']}/lifecycle"
    for role in ("viewer", "member"):
        r = client.post(url, headers=_hdr(people[role]["token"], tenant_id),
                        json={"lifecycle": "quarantined"})
        assert r.status_code == 403, (role, r.text)
    r = client.post(url, headers=_hdr(people["admin"]["token"], tenant_id),
                    json={"lifecycle": "quarantined"})
    assert r.status_code == 200, r.text
    # quarantined -> testing is a legal transition for the owner too
    r = client.post(url, headers=_hdr(people["owner"]["token"], tenant_id),
                    json={"lifecycle": "testing"})
    assert r.status_code == 200, r.text


def test_whatsapp_exchange_requires_admin(client, cast):
    tenant_id, people = cast
    body = {"code": "c", "waba_id": "1", "phone_number_id": "2"}
    for role in ("viewer", "member"):
        r = client.post("/v1/whatsapp/onboard/exchange",
                        headers=_hdr(people[role]["token"], tenant_id),
                        json=body)
        assert r.status_code == 403, (role, r.text)
    # admin passes the guard and reaches the configuration check
    # (onboarding is not configured in the test env -> 503, not 403)
    r = client.post("/v1/whatsapp/onboard/exchange",
                    headers=_hdr(people["admin"]["token"], tenant_id),
                    json=body)
    assert r.status_code == 503, r.text


def test_whatsapp_disconnect_requires_admin(client, cast):
    tenant_id, people = cast
    for role in ("viewer", "member"):
        r = client.delete("/v1/whatsapp/connection",
                          headers=_hdr(people[role]["token"], tenant_id))
        assert r.status_code == 403, (role, r.text)
    r = client.delete("/v1/whatsapp/connection",
                      headers=_hdr(people["admin"]["token"], tenant_id))
    assert r.status_code == 200, r.text
    assert r.json()["disconnected"] is False  # nothing was connected
