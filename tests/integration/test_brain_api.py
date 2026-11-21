"""Brain (v21.2) integration: AgentQon as a brain for external agents.

One scoped key (aqb_…, stored hashed, shown once) authorises exactly
one endpoint — POST /v1/brain, which runs the real mission pipeline.
The matrix pinned here:

  missing / wrong / non-brain credentials on /v1/brain  -> 401
  a brain key presented anywhere else                    -> denied
    (accounts mode: not a session; Shield mode: not the local token)
  Shield still applies to /v1/brain itself (Host allowlist)
  rotation invalidates the old key; revoke kills the key
  no model connected -> the platform's honest 503, never a fake answer
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

import app.main as main
from app.config import Settings
from app.orchestrator import Orchestrator
from app.shield import reset_nonce_cache
from tests.conftest import hdr

LOCAL_TOKEN = "<MASKED_SECRET_001>"
CHANNEL_SECRET = "<MASKED_SECRET_002>"


def _create_key(client, tenant, label="test-bot"):
    r = client.post("/v1/brain/keys", headers=hdr(tenant),
                    json={"label": label})
    assert r.status_code == 201, r.text
    return r.json()


def _brain(client, key, body=None, **headers):
    h = {"Authorization": f"Bearer {key}", **headers}
    return client.post("/v1/brain", headers=h,
                       json=body or {"prompt": "What should we do first?"})


# ------------------------------------------------------------- auth matrix
def test_brain_missing_and_wrong_key_refused(client, tenant):
    _create_key(client, tenant)
    r = client.post("/v1/brain", json={"prompt": "hi"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "BRAIN_KEY_INVALID"
    r = _brain(client, "aqb_" + "x" * 40)
    assert r.status_code == 401
    # a session-shaped token is not a brain key either
    r = _brain(client, "omk_" + "x" * 40)
    assert r.status_code == 401


def test_brain_valid_key_reaches_pipeline(client, tenant):
    created = _create_key(client, tenant)
    key = created["key"]
    assert key.startswith("aqb_")
    r = _brain(client, key, {"prompt": "Compare two launch strategies"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "completed"
    assert out["answer"]  # the stub-LLM panel's real deliverable text
    assert out["mission_id"]
    # the mission is ordinary platform data, owned by the key's tenant
    detail = client.get(f"/v1/missions/{out['mission_id']}",
                        headers=hdr(tenant))
    assert detail.status_code == 200
    assert detail.json()["mission"]["status"] == "completed"


def test_brain_key_status_never_leaks_the_key(client, tenant):
    created = _create_key(client, tenant, label="leak-check")
    status = client.get("/v1/brain/keys", headers=hdr(tenant))
    assert status.status_code == 200
    body = status.json()
    assert body["active"] is True and body["label"] == "leak-check"
    assert created["key"] not in status.text
    assert body["key_prefix"] == created["key"][:12]


def test_brain_rotation_invalidates_old_key(client, tenant):
    first = _create_key(client, tenant)["key"]
    assert _brain(client, first).status_code == 200
    second = _create_key(client, tenant)["key"]
    assert second != first
    assert _brain(client, first).status_code == 401
    assert _brain(client, second).status_code == 200


def test_brain_revoke_kills_the_key(client, tenant):
    key = _create_key(client, tenant)["key"]
    r = client.delete("/v1/brain/keys", headers=hdr(tenant))
    assert r.status_code == 200 and r.json()["active"] is False
    assert _brain(client, key).status_code == 401
    status = client.get("/v1/brain/keys", headers=hdr(tenant)).json()
    assert status["active"] is False


# ------------------------------------------- the key opens nothing else
@pytest.fixture()
def accounts(monkeypatch):
    new = dataclasses.replace(main.settings, auth_mode="accounts",
                              rate_login_per_min=100, rate_run_per_min=100,
                              rate_validate_per_min=100)
    monkeypatch.setattr(main, "settings", new)
    return new


def test_brain_key_is_not_a_session(client, accounts):
    email = f"brain_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post("/v1/auth/register",
                      json={"email": email, "password": "long-enough-password"})
    assert reg.status_code == 201, reg.text
    session, tenant_id = reg.json()["token"], reg.json()["tenant_id"]
    auth = {"Authorization": f"Bearer {session}", "X-Tenant-Id": tenant_id}
    created = client.post("/v1/brain/keys", headers=auth,
                          json={"label": "acct-bot"})
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    # the brain key alone reaches the pipeline (tenant comes from the key)
    assert _brain(client, key).status_code == 200
    # …and nothing else: account + business surfaces all refuse it
    for method, path in (("GET", "/v1/auth/me"), ("GET", "/v1/missions"),
                         ("GET", "/v1/agents"), ("GET", "/v1/brain/keys")):
        r = client.request(method, path, headers={
            "Authorization": f"Bearer {key}", "X-Tenant-Id": tenant_id})
        assert r.status_code in (401, 403), (method, path, r.status_code)


@pytest.fixture()
def shielded(monkeypatch):
    reset_nonce_cache()
    new = dataclasses.replace(main.settings, local_token=LOCAL_TOKEN,
                              channel_secret=CHANNEL_SECRET,
                              packaged=True)
    monkeypatch.setattr(main, "settings", new)
    return new


def test_brain_key_under_shield(client, shielded, tenant):
    host = {"Host": "127.0.0.1:8000"}
    created = client.post("/v1/brain/keys", headers={
        **host, "X-Tenant-Id": tenant,
        "X-OmniMind-Local-Token": LOCAL_TOKEN}, json={"label": "shield-bot"})
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    # brain key alone opens the brain endpoint (no local token needed)…
    r = _brain(client, key, **host)
    assert r.status_code == 200, r.text
    # …but it is not the local token: every other route still refuses it
    r = client.get("/v1/agents", headers={
        **host, "X-Tenant-Id": tenant, "Authorization": f"Bearer {key}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "LOCAL_TOKEN_REQUIRED"
    # …and Shield is not bypassed: a foreign Host is refused even with
    # a valid brain key
    r = _brain(client, key, **{"Host": "evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------------- behaviour
def test_brain_no_model_is_the_honest_503(client, tenant, monkeypatch):
    key = _create_key(client, tenant)["key"]
    dead = Orchestrator(main.store, settings=Settings(
        llm_base_url="http://127.0.0.1:9/v1", llm_api_key="ollama",
        llm_base_url_explicit=True), vault=main.vault)
    monkeypatch.setattr(main, "orchestrator", dead)
    r = _brain(client, key)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_not_connected"


def test_brain_session_memory_roundtrip(client, tenant):
    key = _create_key(client, tenant)["key"]
    body = {"prompt": "Remember: our launch codename is Bluefin",
            "session": "ext-1"}
    assert _brain(client, key, body).status_code == 200
    facts = client.get("/v1/memory", headers=hdr(tenant),
                       params={"scope": "session",
                               "scope_ref": "ext-1"}).json()["facts"]
    assert any(f["source"] == "brain" for f in facts)
    # a second call in the same session still answers (facts ground it)
    r = _brain(client, key, {"prompt": "What is our codename?",
                             "session": "ext-1"})
    assert r.status_code == 200 and r.json()["session"] == "ext-1"
