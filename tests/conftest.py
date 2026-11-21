import os
import tempfile
import uuid

# Isolated data dir for the whole test session — must be set before app import.
os.environ["OMNIMIND_DATA"] = tempfile.mkdtemp(prefix="omnimind-tests-")
os.environ["OMNIMIND_RESEARCH_MODE"] = "seed"  # Phase 5: tests opt into live explicitly

# v20: the product has no canned generation mode — a run with no model
# connected is refused. The suite therefore points the server's env
# endpoint at the deterministic stub LLM (tests/stub_llm.py, test-only)
# through the ordinary OMNIMIND_LLM_* configuration seam.
from tests import stub_llm as _stub_llm

os.environ["OMNIMIND_LLM_BASE_URL"] = _stub_llm.start()
os.environ["OMNIMIND_LLM_API_KEY"] = "stub-key"
os.environ["OMNIMIND_LLM_MODEL"] = _stub_llm.STUB_MODEL

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture()
def tenant():
    """A fresh tenant per test gives real tenant isolation between tests."""
    return f"org_test_{uuid.uuid4().hex[:8]}"


def hdr(tenant_id: str, key: str | None = None):
    h = {"X-Tenant-Id": tenant_id, "X-User-Id": "tester"}
    if key:
        h["Idempotency-Key"] = key
    return h


MISSION_BODY = {
    "title": "Compare launch strategies",
    "contract": {
        "objective": "Compare launch strategies for a new SMB invoicing product",
        "deliverables": ["Decision brief", "Risk register"],
        "acceptance": ["Material claims cite sources", "Two distinct options"],
        "constraints": {"max_cost_usd": 10},
        "tools": {"allow": ["web.read", "artifact.write", "artifact.read"],
                  "deny": ["email.send"]},
        "approval_policy": {"external_side_effects": "required"},
        "data_classification": "internal",
    },
}
