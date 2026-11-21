"""Routed-call failure labelling (audit B, 2026-10-12).

Reproduced live: a writing mission whose provider call SUCCEEDED but
whose model output lacked a deliverable was reported to the owner as
"BYOK connection call failed (unknown)" — blaming the connection and
hiding the real reason. In the strict-BYOK branch of _invoke_routed,
only a GatewayError is a connection failure; adapter/output errors
must surface as ModelCallFailed with the true reason.
"""
import asyncio
import tempfile

import pytest

from app.config import Settings
from app.gateway_client import GatewayError
from app.orchestrator import Orchestrator, RunContext
from app.store import Store

TENANT = "org_labelling"


def _setup(tmp):
    store = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
    contract = {"objective": "Write something", "deliverables": ["Brief"],
                "acceptance": ["ok"], "constraints": {"max_cost_usd": 10.0},
                "tools": {"allow": ["web.read"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal",
                "connection_id": "conn_x"}
    mission = store.create_mission(TENANT, "alice", "Labelling", contract, 10.0)
    orch = Orchestrator(store, settings=Settings())
    ctx = RunContext(strict_byok=True)
    ctx.runtime["researcher"] = {
        "adapter": object(), "version": "v-test",
        "decision": {"estimated_cost": None},
        "conn": {"id": "conn_x"}, "entry": {"model_id": "stub-model-1"}}
    return store, orch, ctx, mission


def _events(store, mission_id):
    rows = store.list_events(TENANT, mission_id)
    return [(r["event_type"], str(r.get("payload"))) for r in rows]


def test_adapter_output_error_is_not_blamed_on_connection():
    with tempfile.TemporaryDirectory() as tmp:
        store, orch, ctx, mission = _setup(tmp)

        async def bad_output(adapter):
            raise RuntimeError("LLM general output contained no deliverable")

        with pytest.raises(RuntimeError) as err:
            asyncio.run(orch._invoke_routed(
                TENANT, mission, "researcher", bad_output, {}, ctx=ctx))
        msg = str(err.value)
        assert "Model output failed" in msg
        assert "no deliverable" in msg
        assert "connection call failed" not in msg
        types = [t for t, _ in _events(store, mission["id"])]
        assert "ModelCallFailed" in types
        assert "ConnectionCallFailed" not in types


def test_gateway_error_stays_a_connection_failure():
    with tempfile.TemporaryDirectory() as tmp:
        store, orch, ctx, mission = _setup(tmp)

        async def provider_down(adapter):
            raise GatewayError("timeout", "gateway timed out")

        with pytest.raises(RuntimeError) as err:
            asyncio.run(orch._invoke_routed(
                TENANT, mission, "researcher", provider_down, {}, ctx=ctx))
        assert "BYOK connection call failed (timeout)" in str(err.value)
        payloads = [p for t, p in _events(store, mission["id"])
                    if t == "ConnectionCallFailed"]
        assert payloads and "timeout" in payloads[0]
