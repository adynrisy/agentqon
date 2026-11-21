"""V3 BYOK: connection lifecycle through the API (Master Build Part XXII).

Uses a local stub gateway. The API key must never appear in any response,
in the connections table, or in mission data — and tenant B must not be
able to see or use tenant A's connection (negative tests, §108)."""
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers_stub_gateway import start_stub

client = TestClient(app)
TENANT_A = {"X-Tenant-Id": "org_byok_a", "X-User-Id": "alice"}
TENANT_B = {"X-Tenant-Id": "org_byok_b", "X-User-Id": "bob"}
KEY = "test-key-1234"


@pytest.fixture(scope="module")
def stub():
    server, base = start_stub(expected_key=KEY)
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def _create(base_url: str, headers=TENANT_A, **over) -> dict:
    payload = {"kind": "omniroute_gateway", "label": "My gateway",
               "base_url": base_url, "api_key": KEY}
    payload.update(over)
    r = client.post("/v1/connections", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_masks_key_and_stores_only_ciphertext(stub):
    conn = _create(stub)
    assert conn["masked_key"] == "••••1234"
    assert KEY not in str(conn)
    # The metadata table never contains the key; the secret table holds
    # ciphertext only.
    from app.main import store
    row = store.get_connection(TENANT_A["X-Tenant-Id"], conn["id"])
    assert KEY not in str(row)
    secret = store.get_secret(TENANT_A["X-Tenant-Id"], conn["id"])
    assert secret and KEY not in secret["ciphertext"]


def test_validate_activates_and_syncs_catalog(stub):
    conn = _create(stub)
    r = client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "active" and body["models_count"] == 2
    models = client.get("/v1/models", headers=TENANT_A).json()["models"]
    mine = [m for m in models if m["connection_id"] == conn["id"]]
    assert {m["model_id"] for m in mine} == {"stub-model-1", "stub-model-2"}
    assert all(m["price_status"] == "unknown" for m in mine)  # never faked


def test_cross_tenant_connection_is_invisible(stub):
    conn = _create(stub)
    assert client.get(f"/v1/connections/{conn['id']}", headers=TENANT_B).status_code == 404
    assert client.post(f"/v1/connections/{conn['id']}/validate",
                       headers=TENANT_B).status_code == 404
    assert client.post(f"/v1/connections/{conn['id']}/revoke",
                       headers=TENANT_B).status_code == 404
    listing = client.get("/v1/connections", headers=TENANT_B).json()["connections"]
    assert all(c["id"] != conn["id"] for c in listing)


def test_wrong_key_fails_validation_as_auth_failed(stub):
    conn = _create(stub, api_key="wrong-key-9999")
    r = client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
    assert r.json()["state"] == "auth_failed"
    assert r.json()["last_error_category"] == "auth_failed"


def test_rotate_then_revoke_shreds_secret(stub):
    conn = _create(stub, api_key="wrong-key-9999")
    client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
    r = client.post(f"/v1/connections/{conn['id']}/rotate",
                    json={"api_key": KEY}, headers=TENANT_A)
    assert r.json()["masked_key"] == "••••1234"
    v = client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
    assert v.json()["state"] == "active"  # the rotated key is the one used
    r = client.post(f"/v1/connections/{conn['id']}/revoke", headers=TENANT_A)
    assert r.json()["state"] == "revoked"
    from app.main import store
    secret = store.get_secret(TENANT_A["X-Tenant-Id"], conn["id"])
    assert secret["ciphertext"] == "" and secret["key_version"] == "shredded"


def test_delete_requires_confirmation(stub):
    conn = _create(stub)
    r = client.request("DELETE", f"/v1/connections/{conn['id']}",
                       json={"confirm": False}, headers=TENANT_A)
    assert r.status_code == 400
    r = client.request("DELETE", f"/v1/connections/{conn['id']}",
                       json={"confirm": True}, headers=TENANT_A)
    assert r.status_code == 200
    assert client.get(f"/v1/connections/{conn['id']}", headers=TENANT_A).status_code == 404


def test_direct_provider_to_local_stub_needs_no_env_flag(stub, monkeypatch):
    # v21.3: an API-key (BYOK) connection may point at a local address
    # with no ALLOW_LOCAL_GATEWAY escape hatch — locality comes from
    # the URL, not the connection kind.
    monkeypatch.delenv("OMNIMIND_ALLOW_LOCAL_GATEWAY", raising=False)
    conn = _create(stub, kind="direct_provider", label="Local BYOK stub")
    assert conn["kind"] == "direct_provider" and conn["state"] == "draft"
    r = client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "active" and body["models_count"] == 2
    models = client.get("/v1/models", headers=TENANT_A).json()["models"]
    mine = [m for m in models if m["connection_id"] == conn["id"]]
    assert {m["model_id"] for m in mine} == {"stub-model-1", "stub-model-2"}


def test_local_endpoint_needs_no_key():
    server, base = start_stub(expected_key=None)  # a keyless local server
    try:
        conn = _create(base, kind="local_endpoint", api_key=None, label="Local stub")
        assert conn["state"] == "draft"
        v = client.post(f"/v1/connections/{conn['id']}/validate", headers=TENANT_A)
        assert v.json()["state"] == "active"
    finally:
        server.shutdown()
