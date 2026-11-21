"""WhatsApp self-serve onboarding (v18) unit tests: the Embedded
Signup exchange against a stub Graph API (token stored encrypted,
scope gate stores nothing), per-tenant client resolution, webhook
routing by metadata.phone_number_id, the outbox tick pushing via
the tenant's own number, and disconnect (unsubscribe + shred)."""
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app import whatsapp as wa
from app import whatsapp_onboard as ob
from app.config import Settings
from app.registry import seed_registry
from app.store import Store
from app.vault import Vault
from app.whatsapp_client import resolve_client, tenant_client
from tests.helpers_stub_whatsapp import BOTH_SCOPES, start_stub

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
WA_ID = "15557654321"
PNID_A = "555000111"
WABA_A = "WABA_A"
TOKEN_A = "LONG_SHORT_codeA"  # stub: code -> SHORT_<code> -> LONG_<short>


@pytest.fixture(scope="module")
def stub_server():
    server, stub, base = start_stub()
    yield stub, base
    server.shutdown()


@pytest.fixture()
def env(stub_server):
    stub, base = stub_server
    stub.calls.clear()
    stub.debug_scopes = list(BOTH_SCOPES)
    return stub, base


def _store(tmp):
    return Store(f"{tmp}/t.db", f"{tmp}/artifacts")


def _onboard_cfg(base, **over):
    cfg = {"whatsapp_api_base": base, "meta_app_id": "stub-app-id",
           "whatsapp_config_id": "cfg-1",
           "whatsapp_app_secret": "app-secret"}
    cfg.update(over)
    return Settings(**cfg)


def _route_cfg(base, **over):
    cfg = {"whatsapp_token": "envtok", "whatsapp_phone_number_id": "12345",
           "whatsapp_api_base": base, "whatsapp_app_secret": "app-secret",
           "whatsapp_verify_token": "vtok"}
    cfg.update(over)
    return Settings(**cfg)


def _onboard(store, vault, base, tenant="org_a", user="user_a",
             code="codeA", waba=WABA_A, pnid=PNID_A):
    return ob.exchange_signup(store, vault, tenant, user, code, waba,
                              pnid, cfg=_onboard_cfg(base))


# ------------------------------------------------------------- exchange
def test_exchange_happy_path_stores_encrypted_token(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        status = _onboard(store, vault, base)
        assert status["connected"] is True
        assert status["waba_id"] == WABA_A
        assert status["phone_number_id"] == PNID_A
        assert status["display_phone_number"] == "+1 555-0100"
        assert status["verified_name"] == "Stub Business"
        # the Graph conversation, in order: code swap, long-lived
        # swap, scope inspection, webhook subscription, number read
        methods = [c["method"] for c in stub.calls]
        assert methods == ["oauth/access_token", "oauth/access_token",
                           "debug_token", "subscribed_apps", "phone_number"]
        swap = stub.calls[0]["query"]
        assert swap["code"] == "codeA" and swap["client_id"] == "stub-app-id"
        long_swap = stub.calls[1]["query"]
        assert long_swap["grant_type"] == "fb_exchange_token"
        assert long_swap["fb_exchange_token"] == "SHORT_codeA"
        assert stub.calls[2]["query"]["input_token"] == TOKEN_A
        sub = stub.calls[3]
        assert sub["_path"] == f"/v21.0/{WABA_A}/subscribed_apps"
        assert sub["_auth"] == f"Bearer {TOKEN_A}"
        # stored as a whatsapp connection; the secret is ciphertext
        conn = next(c for c in store.list_connections("org_a")
                    if c["kind"] == "whatsapp")
        assert conn["state"] == "active"
        assert conn["data_policy"]["whatsapp"]["waba_id"] == WABA_A
        row = store.get_secret("org_a", conn["id"])
        assert row["ciphertext"] != TOKEN_A
        assert vault.decrypt(row["key_version"], row["ciphertext"]) == TOKEN_A
        assert TOKEN_A not in str(conn)  # metadata carries no plaintext


def test_exchange_missing_scope_stores_nothing(env):
    stub, base = env
    stub.debug_scopes = ["whatsapp_business_messaging"]  # management missing
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        with pytest.raises(ob.OnboardError) as caught:
            _onboard(store, vault, base)
        assert caught.value.status_code == 422
        assert "whatsapp_business_management" in caught.value.message
        assert store.list_connections("org_a") == []
        assert ob.connection_status(store, "org_a")["connected"] is False
        # and the flow stopped before subscribing / storing anything
        assert [c["method"] for c in stub.calls] == [
            "oauth/access_token", "oauth/access_token", "debug_token"]


def test_exchange_bad_code_stores_nothing(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        with pytest.raises(ob.OnboardError) as caught:
            _onboard(store, vault, base, code="bad-code")
        assert caught.value.status_code == 502
        assert store.list_connections("org_a") == []


def test_reconnect_replaces_previous_connection(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        _onboard(store, vault, base)
        first = next(c for c in store.list_connections("org_a")
                     if c["kind"] == "whatsapp")
        _onboard(store, vault, base, code="codeB", pnid="555000999")
        conns = [c for c in store.list_connections("org_a")
                 if c["kind"] == "whatsapp"]
        assert len(conns) == 1 and conns[0]["id"] != first["id"]
        assert conns[0]["data_policy"]["whatsapp"]["phone_number_id"] \
            == "555000999"
        # the replaced credential was shredded, not left readable
        assert store.get_secret("org_a", first["id"])["key_version"] \
            == "shredded"


# ------------------------------------------------------------ resolution
def test_resolver_prefers_tenant_then_env(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        cfg = _route_cfg(base)
        _onboard(store, vault, base)
        mine = resolve_client(store, vault, "org_a", cfg)
        assert mine is not None and mine._base.endswith(f"/{PNID_A}")
        other = resolve_client(store, vault, "org_b", cfg)
        assert other is not None and other._base.endswith("/12345")
        assert tenant_client(store, vault, "org_b", cfg) is None
        bare = Settings(whatsapp_api_base=base)  # no env channel either
        assert resolve_client(store, vault, "org_b", bare) is None


def test_tenant_lookup_by_phone_number_id(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        _onboard(store, vault, base)
        assert ob.tenant_for_phone_number_id(store, PNID_A) == "org_a"
        assert ob.tenant_for_phone_number_id(store, "999999") is None
        assert ob.tenant_for_phone_number_id(store, None) is None


def test_public_config_states():
    assert ob.public_config(Settings())["configured"] is False
    cfg = ob.public_config(Settings(meta_app_id="app-1",
                                    whatsapp_config_id="cfg-9"))
    assert cfg == {"configured": True, "app_id": "app-1",
                   "config_id": "cfg-9"}


# ------------------------------------------------------------- disconnect
def test_disconnect_unsubscribes_and_shreds(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        _onboard(store, vault, base)
        conn = next(c for c in store.list_connections("org_a")
                    if c["kind"] == "whatsapp")
        out = ob.disconnect(store, vault, "org_a", "user_a",
                            cfg=_onboard_cfg(base))
        assert out == {"disconnected": True}
        deletes = [c for c in stub.calls
                   if c["method"] == "subscribed_apps_delete"]
        assert len(deletes) == 1
        assert deletes[0]["_path"] == f"/v21.0/{WABA_A}/subscribed_apps"
        assert deletes[0]["_auth"] == f"Bearer {TOKEN_A}"
        assert ob.connection_status(store, "org_a")["connected"] is False
        assert store.get_secret("org_a", conn["id"])["key_version"] \
            == "shredded"
        again = ob.disconnect(store, vault, "org_a", "user_a",
                              cfg=_onboard_cfg(base))
        assert again == {"disconnected": False}


# ---------------------------------------------------------- webhook routing
def _routed_payload(wa_id, text, pnid, wamid):
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "+1 555-000-111",
                          "phone_number_id": pnid},
             "contacts": [{"wa_id": wa_id, "profile": {"name": "Zohaib"}}],
             "messages": [{"from": wa_id, "id": wamid, "timestamp": "1",
                           "type": "text", "text": {"body": text}}]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": WABA_A, "changes": [
                {"field": "messages", "value": value}]}]}


def _texts_via(stub, pnid):
    return [c for c in stub.messages()
            if c["_path"] == f"/v21.0/{pnid}/messages"
            and c.get("type") == "text"]


def test_webhook_routes_by_phone_number_id(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        seed_registry(store, "org_a")
        seed_registry(store, "org_b")
        _onboard(store, vault, base)
        cfg = _route_cfg(base)
        rec = wa.new_link_code(store, "org_a", "user_a", "member",
                               cfg=cfg, now=T0)
        r = asyncio.run(wa.handle_payload(
            store, None,
            _routed_payload(WA_ID, f"LINK {rec['code']}", PNID_A, "wamid.r1"),
            cfg=cfg, vault=vault, now=T0))
        assert r["processed"] == 1 and r["ignored"] == 0
        link = store.get_whatsapp_link(WA_ID)
        assert link is not None and link["tenant_id"] == "org_a"
        # the greeting went out via TENANT A's number + token
        greetings = _texts_via(stub, PNID_A)
        assert len(greetings) == 1
        assert greetings[0]["_auth"] == f"Bearer {TOKEN_A}"
        assert "Linked" in greetings[0]["text"]["body"]
        # a mission text on A's number lands in A's workspace only
        asyncio.run(wa.handle_payload(
            store, None,
            _routed_payload(WA_ID, "Compare launch strategies", PNID_A,
                            "wamid.r2"),
            cfg=cfg, vault=vault, now=T0))
        assert len(store.list_missions("org_a")) == 1
        assert store.list_missions("org_b") == []
        # a number nobody onboarded: ignored, silent, no mission
        before = len(stub.messages())
        r = asyncio.run(wa.handle_payload(
            store, None,
            _routed_payload("15550007777", "hello?", "999999999",
                            "wamid.r3"),
            cfg=cfg, vault=vault, now=T0))
        assert r["ignored"] == 1 and r["processed"] == 0
        assert len(stub.messages()) == before


def test_webhook_tenant_number_rejects_other_tenants_link_code(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        _onboard(store, vault, base)
        cfg = _route_cfg(base)
        foreign = wa.new_link_code(store, "org_b", "user_b", "member",
                                   cfg=cfg, now=T0)
        asyncio.run(wa.handle_payload(
            store, None,
            _routed_payload("15550007777", f"LINK {foreign['code']}",
                            PNID_A, "wamid.r9"),
            cfg=cfg, vault=vault, now=T0))
        # not redeemed, not consumed, no link created
        assert store.get_whatsapp_link("15550007777") is None
        assert store.get_whatsapp_code(foreign["code"]) is not None
        replies = _texts_via(stub, PNID_A)
        assert replies and "invalid, expired, or already used" \
            in replies[-1]["text"]["body"]


# ------------------------------------------------------------- outbox tick
def test_tick_pushes_via_tenant_client(env):
    stub, base = env
    with tempfile.TemporaryDirectory() as tmp:
        store, vault = _store(tmp), Vault.from_env(tmp)
        seed_registry(store, "org_a")
        _onboard(store, vault, base)
        cfg = _route_cfg(base)
        rec = wa.new_link_code(store, "org_a", "user_a", "owner",
                               cfg=cfg, now=T0)
        link = wa._redeem_code(store, rec["code"], WA_ID, "Zohaib", now=T0)
        outcome = wa.create_whatsapp_mission(store, link,
                                             "Compare launch strategies")
        mission = outcome["mission"]
        store.set_mission_status("org_a", mission["id"], "completed")
        out = wa.tick_whatsapp_outbox(store, cfg=cfg, vault=vault,
                                      now=T0 + timedelta(hours=1))
        assert [p["mission_id"] for p in out["pushed"]] == [mission["id"]]
        sends = _texts_via(stub, PNID_A)
        assert sends and all(
            c["_auth"] == f"Bearer {TOKEN_A}" for c in sends)
        assert "Mission completed" in sends[0]["text"]["body"]
