"""v21.4 hardening regression tests — API/orchestrator level.

Pins the end-to-end shape of the audit fixes:
  1. two CONCURRENT runs on one shared Orchestrator never execute
     through each other's BYOK connection (per-run RunContext);
  2. same-named custom agents in two workspaces keep independent
     briefs through create / update / delete (tenant-scoped personas);
  7. the kill switch holds an inline replay, not just external workers;
  8. a cancel landing during the evaluation tail is never overwritten
     by the run completing;
  9. GET /v1/jobs never serves a traceback in last_error;
 10. re-deciding an approval is a 409, never a flip.
"""
import asyncio
import copy
import json
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import connections as conn_mod
from app.catalog import sync_connection_catalog
from app.config import Settings
from app.main import store as app_store
from app.orchestrator import Orchestrator
from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store
from app.vault import Vault
from tests.conftest import MISSION_BODY, hdr
from tests.helpers_stub_gateway import CANDIDATE


# ------------------------------------------------- marker stub gateways

def _start_marker_stub(marker: str, expected_key: str):
    """An OpenAI-compatible stub like helpers_stub_gateway's, except
    every candidate carries the server's marker in its title and chat
    calls sleep briefly — the delay guarantees two gathered runs
    interleave at every model call, which is exactly the schedule the
    old shared-state orchestrator corrupted."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _auth_ok(self):
            return self.headers.get("Authorization") == f"Bearer {expected_key}"

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            if self.path.endswith("/models"):
                return self._send(200, {"data": [{"id": "stub-model-1",
                                                  "owned_by": "stub"}]})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = payload.get("messages", [])
            system = messages[0]["content"] if messages else ""
            user = messages[1]["content"] if len(messages) > 1 else ""
            time.sleep(0.25)  # force the interleave
            if "critic" in system.lower():
                m = re.search(r"cand_[a-z0-9]+", user)
                content = {"critiques": [{
                    "candidate_id": m.group(0) if m else "cand_unknown",
                    "target_claim_id": None,
                    "issue": "Stub critique.", "severity": "medium",
                    "requested_action": "Add stronger sourcing."}]}
            else:
                content = {**CANDIDATE,
                           "title": f"Stub candidate {marker}"}
            return self._send(200, {
                "id": "chatcmpl-stub", "model": payload.get("model", "stub-model-1"),
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


TA, TB = "org_iso_a", "org_iso_b"
KEY_A, KEY_B = "key-alpha-111", "key-bravo-222"


@pytest.fixture()
def iso_env(monkeypatch):
    monkeypatch.setenv("OMNIMIND_ALLOW_LOCAL_GATEWAY", "1")
    srv_a, base_a = _start_marker_stub("ALPHA", KEY_A)
    srv_b, base_b = _start_marker_stub("BRAVO", KEY_B)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/iso.db", f"{tmp}/artifacts")
        vault = Vault.from_env(tmp)
        for t in (TA, TB):
            seed_registry(store, t)
            for s in SEED_SOURCES:
                store.put_source(t, s.model_dump())
        yield store, vault, base_a, base_b
    srv_a.shutdown()
    srv_b.shutdown()


async def _setup_connection(store, vault, tenant, base, key):
    conn = conn_mod.create_connection(store, vault, tenant, "alice", {
        "kind": "omniroute_gateway", "label": f"{tenant} gateway",
        "base_url": base, "api_key": key})
    conn = await conn_mod.validate_connection(store, vault, tenant, "alice",
                                              conn["id"])
    assert conn["state"] == "active"
    full = store.get_connection(tenant, conn["id"])
    secret = conn_mod.get_secret_for_call(store, vault, tenant, conn["id"])
    await sync_connection_catalog(store, tenant, full, secret)
    return conn


def _pinned_mission(store, tenant, connection_id):
    contract = {"objective": "Compare launch strategies",
                "deliverables": ["Brief"], "acceptance": ["Cited claims"],
                "constraints": {"max_cost_usd": 10.0},
                "tools": {"allow": ["web.read"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal",
                "connection_id": connection_id}
    return store.create_mission(tenant, "alice", "Pinned mission", contract, 10.0)


def test_concurrent_runs_never_cross_connections(iso_env):
    store, vault, base_a, base_b = iso_env
    conn_a = asyncio.run(_setup_connection(store, vault, TA, base_a, KEY_A))
    conn_b = asyncio.run(_setup_connection(store, vault, TB, base_b, KEY_B))
    m_a = _pinned_mission(store, TA, conn_a["id"])
    m_b = _pinned_mission(store, TB, conn_b["id"])
    settings = Settings(llm_base_url="http://127.0.0.1:9/v1",
                        llm_model="platform-model", llm_api_key="platform-key")
    orch = Orchestrator(store, settings=settings, vault=vault)  # ONE shared instance

    async def both():
        return await asyncio.gather(orch.run_mission(TA, m_a["id"]),
                                    orch.run_mission(TB, m_b["id"]))

    detail_a, detail_b = asyncio.run(both())
    assert detail_a["mission"]["status"] == "completed"
    assert detail_b["mission"]["status"] == "completed"
    titles_a = [c["title"] for c in detail_a["candidates"]]
    titles_b = [c["title"] for c in detail_b["candidates"]]
    assert titles_a and all("ALPHA" in t for t in titles_a), titles_a
    assert titles_b and all("BRAVO" in t for t in titles_b), titles_b
    assert all(d["connection_id"] == conn_a["id"]
               for d in detail_a["route_decisions"])
    assert all(d["connection_id"] == conn_b["id"]
               for d in detail_b["route_decisions"])


# ------------------------------------------------- custom-agent persona isolation

AGENT_ID = "custom.helper"
BODY_A = {"name": "Helper", "title": "Research aide",
          "description": "Helps", "instructions": "ALPHA brief: cite the moon ledger.",
          "task_types": ["research"]}
BODY_B = {"name": "Helper", "title": "Research aide",
          "description": "Helps", "instructions": "BRAVO brief: cite the sun ledger.",
          "task_types": ["research"]}


def test_same_named_custom_agents_keep_independent_briefs(client, tenant):
    from app.main import orchestrator
    other = f"{tenant}_b"
    r = client.post("/v1/agents/custom", headers=hdr(tenant), json=BODY_A)
    assert r.status_code == 201, r.text
    r = client.post("/v1/agents/custom", headers=hdr(other), json=BODY_B)
    assert r.status_code == 201, r.text

    brief_a = app_store.get_persona(AGENT_ID, tenant)
    brief_b = app_store.get_persona(AGENT_ID, other)
    assert "ALPHA brief" in brief_a and "BRAVO brief" not in brief_a
    assert "BRAVO brief" in brief_b and "ALPHA brief" not in brief_b

    # The orchestrator injects each workspace's OWN brief.
    enriched_a = orchestrator._with_persona(
        app_store.get_agent(tenant, AGENT_ID), tenant)
    enriched_b = orchestrator._with_persona(
        app_store.get_agent(other, AGENT_ID), other)
    assert "ALPHA brief" in enriched_a["persona_prompt"]
    assert "BRAVO brief" in enriched_b["persona_prompt"]

    # Updating B's brief leaves A's untouched.
    r = client.put(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(other),
                   json={**BODY_B, "instructions": "BRAVO brief v2: cite the sun ledger twice."})
    assert r.status_code == 200, r.text
    assert "ALPHA brief" in app_store.get_persona(AGENT_ID, tenant)
    assert "BRAVO brief v2" in app_store.get_persona(AGENT_ID, other)

    # Deleting A's agent removes A's brief only — B's agent still works.
    r = client.delete(f"/v1/agents/custom/{AGENT_ID}", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    assert app_store.get_persona(AGENT_ID, tenant) is None
    assert "BRAVO brief v2" in app_store.get_persona(AGENT_ID, other)
    assert client.get(f"/v1/agents/custom/{AGENT_ID}",
                      headers=hdr(other)).status_code == 200


# ------------------------------------------------- kill switch (inline replay)

def test_kill_switch_holds_inline_replay(client, tenant):
    job = app_store.enqueue_job(tenant, "mis_held")
    claimed = app_store.claim_job_by_id("test-worker", job["id"])
    assert claimed is not None
    app_store.fail_job_terminal(job["id"], "boom", owner="test-worker")
    assert app_store.get_job(tenant, job["id"])["status"] == "failed"
    app_store.set_flag("kill_switch", "on")
    try:
        r = client.post(f"/v1/jobs/{job['id']}/replay", headers=hdr(tenant),
                        json={})
    finally:
        app_store.set_flag("kill_switch", "off")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"  # held by the switch, not executed
    assert body["attempts"] == 0       # the hold refunds the claim


# ------------------------------------------------- final transition guard

def test_cancel_during_evaluation_tail_is_not_overwritten(client, tenant,
                                                           monkeypatch):
    import app.orchestrator as orch_mod
    body = copy.deepcopy(MISSION_BODY)
    m = client.post("/v1/missions", headers=hdr(tenant, "tail-test-1"), json=body)
    assert m.status_code in (200, 201), m.text
    mid = m.json()["mission_id"]

    real_evaluate = orch_mod.evaluate_candidate

    def cancelling_evaluate(cand, critiques, sources, elapsed_ms=0):
        result = real_evaluate(cand, critiques, sources,
                               elapsed_ms=elapsed_ms)
        # A user cancel lands while the run is in its evaluation tail.
        app_store.set_mission_status(tenant, mid, "cancelled")
        return result

    monkeypatch.setattr(orch_mod, "evaluate_candidate", cancelling_evaluate)
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    detail = run.json()
    assert detail["mission"]["status"] == "cancelled"
    event_types = [e["event_type"] for e in detail["events"]]
    assert "MissionCompleted" not in event_types


# ------------------------------------------------- jobs error hygiene (API)

FAKE_TRACEBACK = (
    "Traceback (most recent call last):\n"
    "  File \"/srv/app/worker.py\", line 97, in process_one\n"
    "RuntimeError: provider exploded")


def test_jobs_api_never_serves_tracebacks(client, tenant):
    job = app_store.enqueue_job(tenant, "mis_hygiene")
    # A legacy row, written before summaries were enforced.
    app_store._exec("UPDATE jobs SET last_error=? WHERE id=?",
                    (FAKE_TRACEBACK, job["id"]))
    listed = client.get("/v1/jobs", headers=hdr(tenant)).json()["jobs"]
    mine = [j for j in listed if j["id"] == job["id"]]
    assert mine and "Traceback" not in mine[0]["last_error"]
    assert "/srv/" not in mine[0]["last_error"]
    one = client.get(f"/v1/jobs/{job['id']}", headers=hdr(tenant)).json()
    assert one["last_error"] == "RuntimeError: provider exploded"


# ------------------------------------------------- approval re-decide (API)

def test_redeciding_an_approval_is_a_conflict(client, tenant):
    iid = app_store.put_invocation(tenant, "mis_apr", "web.read", "high",
                                   "pending", {})
    aid = app_store.put_approval(tenant, "mis_apr", iid, "preview")
    r = client.post("/v1/missions/mis_apr/approve", headers=hdr(tenant),
                    json={"approval_id": aid, "decision": "approved"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    r = client.post("/v1/missions/mis_apr/approve", headers=hdr(tenant),
                    json={"approval_id": aid, "decision": "denied"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "STATE_CONFLICT"
    row = app_store._one("SELECT * FROM approvals WHERE id=?", (aid,))
    assert row["status"] == "approved"  # the first decision stands
