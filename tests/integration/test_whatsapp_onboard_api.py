"""WhatsApp self-serve onboarding (v18) over HTTP: public-config in
both states, the Embedded Signup exchange end-to-end against a stub
Graph API (token stored encrypted, never exposed), the scope gate,
per-tenant isolation, webhook routing by metadata.phone_number_id
with sends on the tenant's own number/token, unknown numbers
ignored, and disconnect. No real Meta, no real secrets."""
import hashlib
import hmac
import itertools
import json

import pytest

from app.main import orchestrator as app_orchestrator
from app.main import store as app_store
from app.main import vault as app_vault
from app.worker import drain
from tests.conftest import hdr
from tests.helpers_stub_whatsapp import BOTH_SCOPES, start_stub

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
ENV_PNID = "1234567890"
PNID_A = "555000111"
PNID_B = "555000222"
WA_USER = "15557654321"
_wamids = itertools.count(1)


@pytest.fixture(scope="module")
def ob_stub():
    server, stub, base = start_stub()
    yield stub, base
    server.shutdown()


@pytest.fixture()
def ob_env(monkeypatch, ob_stub):
    stub, base = ob_stub
    monkeypatch.setenv("OMNIMIND_WHATSAPP_TOKEN", "EAATESTTOKEN")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_PHONE_NUMBER_ID", ENV_PNID)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_API_BASE", base)
    monkeypatch.setenv("OMNIMIND_META_APP_ID", "stub-app-id")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_CONFIG_ID", "stub-config-id")
    stub.calls.clear()
    stub.debug_scopes = list(BOTH_SCOPES)
    return stub


def _exchange(client, tenant, code="codeA", waba="WABA_A", pnid=PNID_A):
    return client.post("/v1/whatsapp/onboard/exchange",
                       headers=hdr(tenant),
                       json={"code": code, "waba_id": waba,
                             "phone_number_id": pnid})


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body,
                                hashlib.sha256).hexdigest()


def _text(wa_id, text, pnid=None):
    n = next(_wamids)
    value = {"contacts": [{"wa_id": wa_id, "profile": {"name": "Zohaib"}}],
             "messages": [{"from": wa_id, "id": f"wamid.v18.{n}",
                           "timestamp": "1767225600", "type": "text",
                           "text": {"body": text}}]}
    if pnid is not None:
        value["metadata"] = {"display_phone_number": "+1 555-000-111",
                             "phone_number_id": pnid}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [
                {"field": "messages", "value": value}]}]}


def _post(client, payload):
    raw = json.dumps(payload).encode()
    return client.post("/v1/channels/whatsapp/webhook", content=raw,
                       headers={"Content-Type": "application/json",
                                "X-Hub-Signature-256": _sign(raw)})


def _wa_conn(tenant):
    return next((c for c in app_store.list_connections(tenant)
                 if c["kind"] == "whatsapp"), None)


# ------------------------------------------------------------- config
def test_public_config_both_states(client, tenant, ob_env, monkeypatch):
    r = client.get("/v1/whatsapp/onboard/public-config",
                   headers=hdr(tenant))
    assert r.status_code == 200
    assert r.json() == {"configured": True, "app_id": "stub-app-id",
                        "config_id": "stub-config-id"}
    monkeypatch.delenv("OMNIMIND_META_APP_ID")
    r = client.get("/v1/whatsapp/onboard/public-config",
                   headers=hdr(tenant))
    assert r.json()["configured"] is False
    # and the exchange refuses while unconfigured
    r = _exchange(client, tenant)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "WHATSAPP_ONBOARD_NOT_CONFIGURED"


# ------------------------------------------------------------- exchange
def test_exchange_end_to_end(client, tenant, ob_env):
    stub = ob_env
    r = _exchange(client, tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["display_phone_number"] == "+1 555-0100"
    assert body["verified_name"] == "Stub Business"
    assert body["waba_id"] == "WABA_A" and body["phone_number_id"] == PNID_A

    # the Graph conversation happened in the right order
    methods = [c["method"] for c in stub.calls]
    assert methods == ["oauth/access_token", "oauth/access_token",
                       "debug_token", "subscribed_apps", "phone_number"]
    assert stub.calls[3]["_auth"] == "Bearer LONG_SHORT_codeA"

    # status endpoint agrees; another tenant sees nothing
    mine = client.get("/v1/whatsapp/connection",
                      headers=hdr(tenant)).json()
    assert mine["connected"] is True and mine["phone_number_id"] == PNID_A
    other = client.get("/v1/whatsapp/connection",
                       headers=hdr(tenant + "_other")).json()
    assert other["connected"] is False

    # the token is stored ENCRYPTED and never leaks through the API
    conn = _wa_conn(tenant)
    assert conn is not None and conn["state"] == "active"
    row = app_store.get_secret(tenant, conn["id"])
    assert row["ciphertext"] != "LONG_SHORT_codeA"
    assert app_vault.decrypt(row["key_version"],
                             row["ciphertext"]) == "LONG_SHORT_codeA"
    listing = client.get("/v1/connections", headers=hdr(tenant))
    assert "LONG_SHORT_codeA" not in listing.text
    assert "whatsapp" in listing.text


def test_exchange_missing_scope_is_422_and_stores_nothing(client, tenant,
                                                          ob_env):
    stub = ob_env
    stub.debug_scopes = ["whatsapp_business_management"]  # messaging missing
    r = _exchange(client, tenant, code="codeScope")
    assert r.status_code == 422, r.text
    assert "whatsapp_business_messaging" in r.json()["error"]["message"]
    assert client.get("/v1/whatsapp/connection",
                      headers=hdr(tenant)).json()["connected"] is False
    assert _wa_conn(tenant) is None


# ------------------------------------------------- routing + per-tenant sends
def test_webhook_routes_to_the_onboarded_tenant(client, tenant, ob_env):
    stub = ob_env
    tenant_b = tenant + "_b"
    # unique number ids: the session store is shared across tests,
    # and a phone_number_id routes to exactly ONE tenant — reusing
    # another test's number would (correctly) route away from here.
    pnid_a, pnid_b = "555100111", "555100222"
    assert _exchange(client, tenant, code="codeA2", waba="WABA_A2",
                     pnid=pnid_a).status_code == 200
    assert _exchange(client, tenant_b, code="codeB2", waba="WABA_B2",
                     pnid=pnid_b).status_code == 200
    stub.calls.clear()

    # link the user number to tenant A — THROUGH tenant A's number
    code = client.post("/v1/channels/whatsapp/link-code",
                       headers=hdr(tenant)).json()["code"]
    r = _post(client, _text(WA_USER, f"LINK {code}", pnid=pnid_a))
    assert r.status_code == 200 and r.json()["processed"] == 1
    greetings = [c for c in stub.messages()
                 if c.get("type") == "text"
                 and "Linked" in (c.get("text") or {}).get("body", "")]
    assert len(greetings) == 1
    assert greetings[0]["_path"] == f"/v21.0/{pnid_a}/messages"
    assert greetings[0]["_auth"] == "Bearer LONG_SHORT_codeA2"

    # a mission sent to A's number is created in A's workspace only
    _post(client, _text(WA_USER, "Compare launch strategies for "
                                 "invoicing tools", pnid=pnid_a))
    mine = client.get("/v1/missions", headers=hdr(tenant)).json()["missions"]
    m_a = next(m for m in mine if m["title"].startswith("Compare launch"))
    theirs = client.get("/v1/missions",
                        headers=hdr(tenant_b)).json()["missions"]
    assert not any(m["title"].startswith("Compare launch") for m in theirs)

    # a delivery for a number nobody onboarded is ignored, in silence
    before = len(stub.messages())
    r = _post(client, _text("15550006666", "hello?", pnid="555999999"))
    assert r.status_code == 200
    assert r.json()["ignored"] == 1 and r.json()["processed"] == 0
    assert len(stub.messages()) == before

    # the worker completes A's mission; the result push goes out via
    # A's OWN number + token (the tick's per-tenant resolution)
    drain(app_store, app_orchestrator, "v18-test-worker")
    rec = app_store.get_whatsapp_outbox(tenant,
                                        f"mission_result:{m_a['id']}")
    assert rec["status"] == "sent"
    pushes = [c for c in stub.messages()
              if c.get("type") == "text"
              and "Mission completed" in (c.get("text") or {}).get("body", "")]
    assert len(pushes) == 1
    assert pushes[0]["_path"] == f"/v21.0/{pnid_a}/messages"
    assert pushes[0]["_auth"] == "Bearer LONG_SHORT_codeA2"


# ------------------------------------------------------------- disconnect
def test_disconnect_unsubscribes_and_shreds(client, tenant, ob_env):
    stub = ob_env
    assert _exchange(client, tenant).status_code == 200
    conn = _wa_conn(tenant)
    assert conn is not None
    r = client.delete("/v1/whatsapp/connection", headers=hdr(tenant))
    assert r.json() == {"disconnected": True}
    deletes = [c for c in stub.calls
               if c["method"] == "subscribed_apps_delete"]
    assert len(deletes) == 1
    assert deletes[0]["_path"].endswith("/WABA_A/subscribed_apps")
    assert client.get("/v1/whatsapp/connection",
                      headers=hdr(tenant)).json()["connected"] is False
    # the credential is crypto-shredded, and routing forgets the number
    assert app_store.get_secret(tenant, conn["id"])["key_version"] \
        == "shredded"
    r = client.delete("/v1/whatsapp/connection", headers=hdr(tenant))
    assert r.json() == {"disconnected": False}
