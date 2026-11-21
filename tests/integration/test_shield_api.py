"""Shield (v20.2) integration: the packaged-harness surface.

C1 — a per-install local token gates every route except the minimal
exemptions; the packaged Host allowlist and cross-site Origin refusal
are enforced; the CLI's HMAC channel verifies end-to-end and refuses
replays, stale timestamps and tampered bodies (never a downgrade).

H4 — the WhatsApp webhook fails closed: an enabled channel without
an app secret refuses every delivery.
"""
from __future__ import annotations

import dataclasses
import time
import uuid

import pytest

import app.main as main
from app.shield import reset_nonce_cache, sign_channel

TOKEN = "local-token-for-tests"
SECRET = "channel-secret-for-tests"
HOST = {"Host": "127.0.0.1:8000"}


@pytest.fixture()
def shielded(monkeypatch):
    reset_nonce_cache()
    new = dataclasses.replace(main.settings, local_token=TOKEN,
                              channel_secret=SECRET, packaged=True)
    monkeypatch.setattr(main, "settings", new)
    return new


def _hdr(**extra):
    return {**HOST, **extra}


def test_health_is_the_only_open_api_surface(client, shielded):
    r = client.get("/health", headers=HOST)
    assert r.status_code == 200
    assert r.json()["shield"]["local_token"] is True


def test_health_states_token_requirement_unambiguously(client, shielded):
    """Audit R (X4): /health's auth_mode names the identity mode
    (dev-header vs accounts) — with a per-install token configured the
    middleware enforces it on every non-exempt path, and /health must
    say `token_required` in so many words so the pair can never be
    misread as 'auth is off'."""
    r = client.get("/health", headers=HOST)
    assert r.status_code == 200
    body = r.json()
    assert body["shield"]["token_required"] is True
    assert body["shield"]["local_token"] is True


def test_local_token_required_and_accepted(client, shielded, tenant):
    r = client.get("/v1/agents", headers=_hdr(**{"X-Tenant-Id": tenant}))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "LOCAL_TOKEN_REQUIRED"
    r = client.get("/v1/agents", headers=_hdr(
        **{"X-Tenant-Id": tenant, "X-OmniMind-Local-Token": TOKEN}))
    assert r.status_code == 200
    r = client.get("/v1/agents", headers=_hdr(
        **{"X-Tenant-Id": tenant, "X-OmniMind-Local-Token": "wrong"}))
    assert r.status_code == 401


def test_host_allowlist_refuses_foreign_host(client, shielded, tenant):
    r = client.get("/v1/agents", headers={
        "Host": "evil.example", "X-Tenant-Id": tenant,
        "X-OmniMind-Local-Token": TOKEN})
    assert r.status_code == 403
    for good in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
        r = client.get("/v1/agents", headers={
            "Host": good, "X-Tenant-Id": tenant,
            "X-OmniMind-Local-Token": TOKEN})
        assert r.status_code == 200, good


def test_cross_site_origin_refused_on_mutations(client, shielded, tenant):
    body = b'{"name": "shield-probe"}'
    r = client.post("/v1/projects", content=body, headers=_hdr(**{
        "X-Tenant-Id": tenant, "X-OmniMind-Local-Token": TOKEN,
        "Content-Type": "application/json",
        "Origin": "https://evil.example"}))
    assert r.status_code == 403
    r = client.post("/v1/projects", content=body, headers=_hdr(**{
        "X-Tenant-Id": tenant, "X-OmniMind-Local-Token": TOKEN,
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:8000"}))
    assert r.status_code == 201


def _signed_headers(method, path, body=b"", nonce=None, ts=None,
                    secret=SECRET):
    nonce = nonce or f"n-{uuid.uuid4().hex[:12]}"
    ts = ts or str(int(time.time()))
    sig = sign_channel(secret, method, path, ts, nonce, body)
    return {"X-OmniMind-Ts": ts, "X-OmniMind-Nonce": nonce,
            "X-OmniMind-Sig": sig}


def test_hmac_channel_accepts_signed_cli_request(client, shielded, tenant):
    h = _hdr(**{"X-Tenant-Id": tenant})
    h.update(_signed_headers("GET", "/v1/agents"))
    r = client.get("/v1/agents", headers=h)
    assert r.status_code == 200  # signature alone opens the channel


def test_hmac_channel_replay_refused(client, shielded, tenant):
    nonce = f"n-{uuid.uuid4().hex[:12]}"
    h = _hdr(**{"X-Tenant-Id": tenant})
    h.update(_signed_headers("GET", "/v1/agents", nonce=nonce))
    assert client.get("/v1/agents", headers=h).status_code == 200
    r = client.get("/v1/agents", headers=h)
    assert r.status_code == 403
    assert "replay" in r.json()["error"]["message"]


def test_hmac_channel_tampered_body_refused(client, shielded, tenant):
    body = b'{"name": "original"}'
    h = _hdr(**{"X-Tenant-Id": tenant, "Content-Type": "application/json"})
    h.update(_signed_headers("POST", "/v1/projects", body=body))
    r = client.post("/v1/projects", content=b'{"name": "tampered"}',
                    headers=h)
    assert r.status_code == 403


def test_hmac_channel_stale_timestamp_refused(client, shielded, tenant):
    stale = str(int(time.time()) - 3600)
    h = _hdr(**{"X-Tenant-Id": tenant})
    h.update(_signed_headers("GET", "/v1/agents", ts=stale))
    r = client.get("/v1/agents", headers=h)
    assert r.status_code == 403


def test_bad_signature_never_downgrades_to_token(client, shielded, tenant):
    """A request carrying BOTH a valid token and an invalid signature
    is refused: the signature door, once used, decides."""
    h = _hdr(**{"X-Tenant-Id": tenant, "X-OmniMind-Local-Token": TOKEN,
                "X-OmniMind-Ts": str(int(time.time())),
                "X-OmniMind-Nonce": "n-bad", "X-OmniMind-Sig": "0" * 64})
    r = client.get("/v1/agents", headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------- H4
def test_whatsapp_webhook_fails_closed_without_app_secret(
        client, monkeypatch):
    monkeypatch.setenv("OMNIMIND_WHATSAPP_TOKEN", "EAATOKEN")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.delenv("OMNIMIND_WHATSAPP_APP_SECRET", raising=False)
    r = client.post("/v1/channels/whatsapp/webhook",
                    json={"object": "whatsapp_business_account",
                          "entry": []})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "WHATSAPP_APP_SECRET_REQUIRED"


def test_whatsapp_webhook_unsigned_refused_with_secret(
        client, monkeypatch):
    monkeypatch.setenv("OMNIMIND_WHATSAPP_TOKEN", "EAATOKEN")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_APP_SECRET", "appsecret")
    r = client.post("/v1/channels/whatsapp/webhook",
                    json={"object": "whatsapp_business_account",
                          "entry": []})
    assert r.status_code == 403
