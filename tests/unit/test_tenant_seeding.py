"""Regression test for the cross-tenant row-stealing bug found in live
Phase 2 verification: agents/sources/idempotency rows are keyed per
tenant. Seeding tenant B must never move or hide tenant A's rows."""
import tempfile

from app.registry import seed_registry
from app.sources import SEED_SOURCES
from app.store import Store


def test_interleaved_tenants_keep_their_own_registry_and_sources():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        for tenant in ("org_a", "org_b"):
            seed_registry(s, tenant)
            for src in SEED_SOURCES:
                s.put_source(tenant, src.model_dump())
        # Both tenants fully seeded, independently.
        assert len(s.list_agents("org_a")) == 3
        assert len(s.list_agents("org_b")) == 3
        assert len(s.list_sources("org_a")) == len(SEED_SOURCES)
        assert len(s.list_sources("org_b")) == len(SEED_SOURCES)
        # Mutating B's registry leaves A's untouched.
        alpha_b = next(a for a in s.list_agents("org_b") if a["agent_id"] == "agent.research.alpha")
        s.set_agent_lifecycle("org_b", alpha_b["agent_version_id"], "quarantined")
        alpha_a = next(a for a in s.list_agents("org_a") if a["agent_id"] == "agent.research.alpha")
        assert alpha_a["lifecycle"] == "active"


def test_same_idempotency_key_is_independent_per_tenant():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
        s.save_idempotent("org_a", "shared-key", "hash-a", "mis_a")
        s.save_idempotent("org_b", "shared-key", "hash-b", "mis_b")
        assert s.find_idempotent("org_a", "shared-key")["mission_id"] == "mis_a"
        assert s.find_idempotent("org_b", "shared-key")["mission_id"] == "mis_b"
