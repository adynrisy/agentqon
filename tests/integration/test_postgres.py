"""PostgreSQL integration tests — run only when OMNIMIND_TEST_DATABASE_URL
points at a reachable Postgres (a dedicated test database). The same
repository interface and a full mission run are exercised against the
real production backend."""
import asyncio
import os
import uuid

import pytest

from app.orchestrator import Orchestrator
from tests.stub_llm import settings as stub_settings
from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store

DSN = os.environ.get("OMNIMIND_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="OMNIMIND_TEST_DATABASE_URL not set")


@pytest.fixture()
def pg_store(tmp_path):
    return Store(pg_dsn=DSN, artifacts_dir=str(tmp_path / "artifacts"))


def test_pg_mission_roundtrip(pg_store):
    tenant = f"org_pg_{uuid.uuid4().hex[:8]}"
    contract = {"objective": "Compare launch strategies", "deliverables": ["Brief"],
                "acceptance": ["Cited claims"], "constraints": {"max_cost_usd": 10},
                "tools": {"allow": ["web.read", "artifact.write"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal"}
    m = pg_store.create_mission(tenant, "tester", "PG mission", contract, 10.0)
    assert pg_store.get_mission(tenant, m["id"])["title"] == "PG mission"
    assert pg_store.get_mission("org_someone_else", m["id"]) is None  # tenant isolation in SQL


def test_pg_full_mission_and_queue(pg_store):
    tenant = f"org_pg_{uuid.uuid4().hex[:8]}"
    seed_registry(pg_store, tenant)
    for s in SEED_SOURCES:
        pg_store.put_source(tenant, s.model_dump())
    contract = {"objective": "Compare launch strategies for a new SMB invoicing product",
                "deliverables": ["Decision brief"], "acceptance": ["Cited claims", "Two options"],
                "constraints": {"max_cost_usd": 10},
                "tools": {"allow": ["web.read", "artifact.write", "artifact.read"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal"}
    m = pg_store.create_mission(tenant, "tester", "PG full run", contract, 10.0)
    job = pg_store.enqueue_job(tenant, m["id"])
    claimed = pg_store.claim_job("pg-worker")
    assert claimed["id"] == job["id"]
    orch = Orchestrator(pg_store, settings=stub_settings())
    detail = asyncio.run(orch.run_mission(tenant, m["id"]))
    pg_store.complete_job(job["id"])
    assert detail["mission"]["status"] == "completed"
    assert len(detail["candidates"]) == 2
    assert detail["usage"]["total_usd"] > 0
    # Upserts (agent re-seed, candidate re-put) must work under Postgres dialect too.
    seed_registry(pg_store, tenant)
    assert len(pg_store.list_agents(tenant)) == 3


def test_pg_budget_reservation_cycle(pg_store):
    """Reserve/settle on Postgres — guards the MAX vs GREATEST dialect
    bug that only a live PG run exposed (SQLite's MAX is scalar)."""
    tenant = f"org_pg_{uuid.uuid4().hex[:8]}"
    m = pg_store.create_mission(tenant, "u", "pg reservations",
                                {"objective": "x", "deliverables": ["d"],
                                 "acceptance": ["a"], "constraints": {"max_cost_usd": 5}}, 5.0)
    rid = pg_store.reserve_budget(tenant, m["id"], 1.25, 5.0)
    assert rid is not None
    assert pg_store.reserve_budget(tenant, m["id"], 4.0, 5.0) is None
    pg_store.settle_reservation(rid, "reconciled")
    fresh = pg_store.get_mission(tenant, m["id"])
    assert abs(fresh["reserved_usd"]) < 1e-9
