"""Regression tests for the 2026-10-26 backend fixes (redesign worktree).

1. Connection delete cleanup (conn-delete-bug spec): deleting a connection
   must purge its catalog rows from GET /v1/models (unfiltered and filtered
   by the deleted id) and must stop offering combos whose chain is emptied
   (disabled, not silently deleted — the user can see, delete, or repair it).
   GET /v1/models must also hide pre-existing orphans for connections that
   no longer exist (defence in depth for deletes that predate the fix).
2. Custom headers persistence (conn-advanced-settings §2.4): headers sent at
   creation used to be validated and echoed once, then dropped by
   put_connection because the connections table had no headers column.
   They must now persist across reload, validate, and PATCH, show masked
   on the card data, and actually be sent to the provider on calls.

Each test uses a fresh tenant so tests are independent of each other and
of test_connections_api.py's shared tenant.
"""
import json
import os
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers_stub_gateway import start_stub

client = TestClient(app)
KEY = "test-key-1234"
HEADER_NAME = "X-Test-Header"
HEADER_VALUE = "hdr-secret-9876"


def _hdr(tenant_id: str) -> dict:
    return {"X-Tenant-Id": tenant_id, "X-User-Id": "alice"}


def _tenant() -> str:
    return f"org_regr_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def stub():
    server, base = start_stub(expected_key=KEY)
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def _create(base_url: str, tenant_id: str, **over) -> dict:
    payload = {"kind": "direct_provider", "label": "Regression conn",
               "base_url": base_url, "api_key": KEY}
    payload.update(over)
    r = client.post("/v1/connections", json=payload, headers=_hdr(tenant_id))
    assert r.status_code == 201, r.text
    return r.json()


def _validate_and_model(tenant_id: str, conn_id: str) -> str:
    v = client.post(f"/v1/connections/{conn_id}/validate", headers=_hdr(tenant_id))
    assert v.status_code == 200, v.text
    assert v.json()["state"] == "active"
    models = client.get("/v1/models", headers=_hdr(tenant_id)).json()["models"]
    mine = [m for m in models if m["connection_id"] == conn_id]
    assert mine, "validated connection must have synced catalog rows"
    return mine[0]["model_id"]


def _make_combo(tenant_id: str, targets: list[dict]) -> dict:
    r = client.post("/v1/combos", json={
        "name": "regression combo", "strategy": "priority",
        "targets": targets, "enabled": True}, headers=_hdr(tenant_id))
    assert r.status_code == 201, r.text
    return r.json()


def test_delete_removes_models_and_disables_emptied_combo(stub):
    """Spec test: delete -> models gone everywhere, emptied combo disabled."""
    t = _tenant()
    conn = _create(stub, t)
    model_id = _validate_and_model(t, conn["id"])
    _make_combo(t, [{"connection_id": conn["id"], "model_id": model_id, "weight": 1}])
    assert client.get("/v1/combos/auto/preview",
                      headers=_hdr(t)).json()["chain"], "precondition: chain non-empty"

    r = client.request("DELETE", f"/v1/connections/{conn['id']}",
                       json={"confirm": True}, headers=_hdr(t))
    assert r.status_code == 200

    assert client.get("/v1/connections", headers=_hdr(t)).json()["connections"] == []
    assert client.get(f"/v1/connections/{conn['id']}",
                      headers=_hdr(t)).status_code == 404

    models = client.get("/v1/models", headers=_hdr(t)).json()["models"]
    assert all(m["connection_id"] != conn["id"] for m in models)
    mine = client.get(f"/v1/models?connection_id={conn['id']}",
                      headers=_hdr(t)).json()["models"]
    assert mine == []

    combos = client.get("/v1/combos", headers=_hdr(t)).json()["combos"]
    assert combos, "combo row survives (disabled, not destroyed) so the user can see it"
    assert not any(c["enabled"] and any(
        tg["connection_id"] == conn["id"] for tg in c["targets"]) for c in combos)
    assert combos[0]["enabled"] is False and combos[0]["targets"] == []
    chain = client.get("/v1/combos/auto/preview", headers=_hdr(t)).json()["chain"]
    assert all(c["connection_id"] != conn["id"] for c in chain)
    # Model state must no longer seat the deleted model. (In this pytest
    # env, conftest points the platform LLM at a stub, so resolve_state
    # legitimately falls back to that env source — the spec's literal
    # `connected is False` holds only on a server with no env LLM, which
    # the scratch repro below demonstrates. What must never happen here
    # is a byok seating of the deleted connection/model. The model-name
    # half of that check is NOT asserted: the shared stub serves
    # "stub-model-1" both as the deleted connection's synced model and
    # as the env fallback's model, so a name comparison cannot tell
    # them apart; source != "byok" is the discriminating assertion, and
    # the /v1/models assertions above already prove the catalog row
    # is gone.)
    state = client.get("/v1/model-state", headers=_hdr(t)).json()
    assert state["source"] != "byok"


def test_delete_prunes_only_the_deleted_target(stub):
    """A combo spanning two connections keeps its surviving target enabled."""
    t = _tenant()
    conn_a = _create(stub, t, label="Conn A")
    model_a = _validate_and_model(t, conn_a["id"])
    conn_b = _create(stub, t, label="Conn B")
    model_b = _validate_and_model(t, conn_b["id"])
    _make_combo(t, [{"connection_id": conn_a["id"], "model_id": model_a, "weight": 1},
                      {"connection_id": conn_b["id"], "model_id": model_b, "weight": 1}])

    r = client.request("DELETE", f"/v1/connections/{conn_a['id']}",
                       json={"confirm": True}, headers=_hdr(t))
    assert r.status_code == 200

    combos = client.get("/v1/combos", headers=_hdr(t)).json()["combos"]
    assert len(combos) == 1
    assert combos[0]["enabled"] is True
    assert [tg["connection_id"] for tg in combos[0]["targets"]] == [conn_b["id"]]
    models = client.get("/v1/models", headers=_hdr(t)).json()["models"]
    assert all(m["connection_id"] != conn_a["id"] for m in models)
    assert any(m["connection_id"] == conn_b["id"] for m in models)


def test_models_endpoint_hides_preexisting_orphans(stub):
    """Part B: catalog rows for a connection that no longer exists are
    never served, even when the orphan predates delete-time cleanup."""
    t = _tenant()
    from app.main import store
    store.upsert_catalog_model(t, "conn_long_gone", {
        "model_id": "orphan-model-1", "provider": "stub",
        "display_name": "orphan-model-1", "capabilities": {},
        "health": "ok"})
    models = client.get("/v1/models", headers=_hdr(t)).json()["models"]
    assert all(m["model_id"] != "orphan-model-1" for m in models)
    mine = client.get("/v1/models?connection_id=conn_long_gone",
                      headers=_hdr(t)).json()["models"]
    assert mine == []


class _RecordingHandler(BaseHTTPRequestHandler):
    seen_headers: list[dict] = []

    def log_message(self, *args):  # silence
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _RecordingHandler.seen_headers.append(dict(self.headers))
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": "rec-model-1", "owned_by": "rec"}]})
        return self._send(404, {"error": "nope"})


@pytest.fixture()
def recording_stub():
    _RecordingHandler.seen_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    os.environ["OMNIMIND_ALLOW_LOCAL_GATEWAY"] = "1"
    yield base
    server.shutdown()
    os.environ.pop("OMNIMIND_ALLOW_LOCAL_GATEWAY", None)


def test_custom_headers_persist_masked_and_are_sent(recording_stub):
    """§2.4: create with headers -> reload shows them masked -> a real
    provider call (validate's model-list fetch) carries the header value
    -> validate and PATCH round-trips keep them, value never leaks."""
    t = _tenant()
    created = _create(recording_stub, t, headers={HEADER_NAME: HEADER_VALUE})
    assert created["headers_masked"] == {HEADER_NAME: "••••9876"}
    assert HEADER_VALUE not in json.dumps(created)

    detail = client.get(f"/v1/connections/{created['id']}", headers=_hdr(t)).json()
    assert detail["headers_masked"] == {HEADER_NAME: "••••9876"}
    assert HEADER_VALUE not in json.dumps(detail)

    v = client.post(f"/v1/connections/{created['id']}/validate", headers=_hdr(t))
    assert v.status_code == 200, v.text
    assert v.json()["state"] == "active"
    assert v.json()["headers_masked"] == {HEADER_NAME: "••••9876"}
    assert any(h.get(HEADER_NAME) == HEADER_VALUE
               for h in _RecordingHandler.seen_headers), \
        "validate's provider call must carry the custom header"

    reloaded = client.get(f"/v1/connections/{created['id']}", headers=_hdr(t)).json()
    assert reloaded["headers_masked"] == {HEADER_NAME: "••••9876"}
    assert HEADER_VALUE not in json.dumps(reloaded)

    p = client.patch(f"/v1/connections/{created['id']}",
                     json={"label": "Renamed"}, headers=_hdr(t))
    assert p.status_code == 200, p.text
    after_patch = client.get(f"/v1/connections/{created['id']}", headers=_hdr(t)).json()
    assert after_patch["headers_masked"] == {HEADER_NAME: "••••9876"}
    assert HEADER_VALUE not in json.dumps(after_patch)


def test_headers_column_migrated_for_preexisting_db(tmp_path):
    """A database created before the headers column existed gains it on
    open, and header round-trips work on the migrated table."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE connections ("
        " id TEXT NOT NULL, tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, provider_key TEXT NOT NULL, label TEXT NOT NULL,"
        " base_url TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft',"
        " masked_key TEXT NOT NULL DEFAULT '', allowed_models TEXT NOT NULL DEFAULT '[]',"
        " blocked_models TEXT NOT NULL DEFAULT '[]', budget_limit_usd REAL,"
        " routing_mode TEXT NOT NULL DEFAULT 'balanced', data_policy TEXT NOT NULL DEFAULT '{}',"
        " last_validated_at TEXT, last_error_category TEXT, models_count INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,"
        " PRIMARY KEY (tenant_id, id))")
    conn.commit()
    conn.close()

    from app.store import Store
    store = Store(str(db), str(tmp_path / "artifacts"))
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(connections)")]
    assert "headers" in cols
    record = {"id": "conn_mig", "owner_user_id": "u", "kind": "local_endpoint",
              "provider_key": "local_endpoint", "label": "Migrated",
              "base_url": "http://127.0.0.1:11434/v1", "state": "draft",
              "masked_key": "", "allowed_models": [], "blocked_models": [],
              "budget_limit_usd": None, "routing_mode": "balanced", "data_policy": {},
              "last_validated_at": None, "last_error_category": None, "models_count": 0,
              "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
              "deleted_at": None, "headers": {HEADER_NAME: HEADER_VALUE}}
    store.put_connection("org_mig", record)
    back = store.get_connection("org_mig", "conn_mig")
    assert back["headers"] == {HEADER_NAME: HEADER_VALUE}
