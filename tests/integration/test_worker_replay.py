"""Worker reliability cycle (Ch.19): a mission whose agents are quarantined
fails through the queue, lands in the dead-letter state, and — after the
cause is fixed — a controlled replay runs it to completion."""
import tempfile

from app.orchestrator import Orchestrator
from tests.stub_llm import settings as stub_settings
from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store
from app.worker import process_one

CONTRACT = {"objective": "Compare launch strategies", "deliverables": ["Brief"],
            "acceptance": ["Cited claims"], "constraints": {"max_cost_usd": 10},
            "tools": {"allow": ["web.read", "artifact.write", "artifact.read"], "deny": []},
            "approval_policy": {"external_side_effects": "required"},
            "data_classification": "internal"}


def _force_due(store, job_id):
    store._exec("UPDATE jobs SET available_at='2000-01-01T00:00:00Z' WHERE id=?", (job_id,))


def test_dead_letter_then_replay_completes_mission():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/w.db", f"{tmp}/artifacts")
        tenant = "org_replay"
        seed_registry(store, tenant)
        for s in SEED_SOURCES:
            store.put_source(tenant, s.model_dump())
        orch = Orchestrator(store, settings=stub_settings())

        # Break the world: quarantine both research agents.
        for a in store.list_agents(tenant):
            if "research" in a["agent_id"]:
                store.set_agent_lifecycle(tenant, a["agent_version_id"], "quarantined")

        mission = store.create_mission(tenant, "tester", "Replay mission", CONTRACT, 10.0)
        job = store.enqueue_job(tenant, mission["id"])

        results = []
        for _ in range(3):
            results.append(process_one(store, orch, "test-worker"))
            _force_due(store, job["id"])
        assert results[-1]["result"] == "dead"
        assert store.get_job(tenant, job["id"])["status"] == "dead"
        assert store.get_mission(tenant, mission["id"])["status"] == "failed"

        # Fix the cause, replay the dead job — the mission must now complete.
        for a in store.list_agents(tenant):
            if "research" in a["agent_id"]:
                store.set_agent_lifecycle(tenant, a["agent_version_id"], "active")
        replayed = store.replay_job(tenant, job["id"])
        assert replayed["status"] == "queued" and replayed["attempts"] == 0
        out = process_one(store, orch, "test-worker")
        assert out["result"] == "completed"
        detail = orch.mission_detail(tenant, mission["id"])
        assert detail["mission"]["status"] == "completed"
        assert len(detail["candidates"]) == 2
        event_types = [e["event_type"] for e in detail["events"]]
        assert "MissionResetForReplay" in event_types
