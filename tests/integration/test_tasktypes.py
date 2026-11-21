"""v12 general-purpose missions: the classifier routes any user task;
non-strategy missions produce real deliverables through the same
harness (suitability panel, critic, rubric). Since v20.1 a fresh
workspace auto-seeds the full roster, so missions are drafted by the
top-5 suitability panel rather than the two seed agents alone; the
seed pair still anchors strategy missions."""
from __future__ import annotations

import copy

import pytest

from app.tasktypes import classify_task
from tests.conftest import MISSION_BODY, hdr


@pytest.mark.parametrize("objective,expected", [
    ("Compare growth strategies for a Facebook page", "strategy"),
    ("Compare go-to-market strategies for an invoicing tool", "strategy"),
    ("Compare launch strategies for a tutoring marketplace", "strategy"),
    ("Write a professional email to my landlord about a leaking tap", "writing"),
    ("Draft a cover letter for a marketing job", "writing"),
    ("Write a Python function that removes duplicate words", "code"),
    ("Help me debug this Python error", "code"),
    ("Plan a 7-day study schedule for a biology exam", "plan"),
    ("Why do houseplants leaves turn yellow?", "qa"),
    ("Explain how rainbows form", "qa"),
    ("Research the market for electric scooters in Pakistan", "research"),
    ("Summarize the attached quarterly results", "general"),
])
def test_classifier(objective, expected):
    assert classify_task(objective) == expected


def test_classifier_explicit_wins():
    assert classify_task("Write an email", requested="qa") == "qa"
    assert classify_task("Write an email", requested="bogus") == "writing"


def _run(client, tenant, key, objective, task_type=None):
    body = copy.deepcopy(MISSION_BODY)
    body["title"] = objective[:60]
    body["contract"]["objective"] = objective
    if task_type:
        body["contract"]["task_type"] = task_type
    r = client.post("/v1/missions", headers=hdr(tenant, key), json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    return client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()


def test_writing_mission_produces_deliverables(client, tenant):
    d = _run(client, tenant, "ttsk-0001",
             "Write a professional email to my landlord about a leaking tap")
    assert d["mission"]["contract"]["task_type"] == "writing"
    assert d["mission"]["status"] == "completed"
    cands = d["candidates"]
    # Panel era (v20.1): five distinct drafting voices, each producing
    # the shaped deliverable for the task.
    assert len(cands) == 5
    assert len({c["agent_version_id"] for c in cands}) == 5
    for c in cands:
        assert c["deliverable"] and "Dear" in c["deliverable"]
        assert "leaking tap" in c["deliverable"].lower()


def test_code_mission_produces_code(client, tenant):
    d = _run(client, tenant, "ttsk-0002",
             "Write a Python function that removes duplicate words from a sentence")
    assert d["mission"]["contract"]["task_type"] == "code"
    for c in d["candidates"]:
        assert "```python" in c["deliverable"]
        assert "def remove_duplicate_words" in c["deliverable"]


def test_plan_and_qa_missions_complete(client, tenant):
    d = _run(client, tenant, "ttsk-0003", "Plan a 7-day study schedule for a biology exam")
    assert d["mission"]["contract"]["task_type"] == "plan"
    assert all("Phase 1" in c["deliverable"] for c in d["candidates"])
    d2 = _run(client, tenant, "ttsk-0004", "Why do houseplants leaves turn yellow?")
    assert d2["mission"]["contract"]["task_type"] == "qa"
    assert all(c["deliverable"] for c in d2["candidates"])


def test_strategy_pipeline_uses_panel(client, tenant):
    d = _run(client, tenant, "ttsk-0005",
             "Compare launch strategies for a new SMB invoicing product")
    assert d["mission"]["contract"]["task_type"] == "strategy"
    # The seed pair still anchors the panel with the two named
    # strategies; roster panelists draft alongside them, and strategy
    # candidates never carry a general-task deliverable.
    assert len(d["candidates"]) == 5
    assert {"niche-first", "partnership-led"} <= {
        c["strategy"] for c in d["candidates"]}
    assert all(c["deliverable"] is None for c in d["candidates"])


def test_explicit_task_type_respected(client, tenant):
    d = _run(client, tenant, "ttsk-0006",
             "Write an email about the project delay", task_type="qa")
    assert d["mission"]["contract"]["task_type"] == "qa"
    assert all(c["deliverable"] for c in d["candidates"])
