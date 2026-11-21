"""v21 Track 3 — scoped long-term memory: curation (dedupe /
supersede), tenant isolation, secret redaction, provenance-labelled
retrieval, the write rule, deterministic extraction, and prompt
injection of the retrieved context."""
import asyncio
import json

import httpx

from app.adapters import AgentRequest
from app.config import Settings
from app.llm_adapter import LLMResearchAdapter
from app.memory import (add_fact, can_write, extract_from_mission,
                        list_facts, memory_context, redact_text)
from app.store import Store


def _store(tmp_path):
    return Store(str(tmp_path / "mem.db"))


def test_add_dedupe_supersede(tmp_path):
    s = _store(tmp_path)
    t = "org_a"
    first = add_fact(s, t, "user", "", "preference:summary-style",
                     "User prefers short summaries.")
    again = add_fact(s, t, "user", "", "preference:summary-style",
                     "User prefers short summaries.")
    assert again["memory_id"] == first["memory_id"]  # dedupe no-op
    newer = add_fact(s, t, "user", "", "preference:summary-style",
                     "User prefers detailed summaries with sources.")
    assert newer["supersedes"] == first["memory_id"]
    active = list_facts(s, t, scope="user")
    assert [m["fact"] for m in active] == \
        ["User prefers detailed summaries with sources."]
    everything = list_facts(s, t, scope="user", include_superseded=True)
    assert len(everything) == 2
    assert {m["status"] for m in everything} == {"active", "superseded"}


def test_scopes_and_isolation(tmp_path):
    s = _store(tmp_path)
    add_fact(s, "org_a", "agent", "agent.research.alpha@1.2.0",
             "selected:strategy", "Won a strategy mission.")
    add_fact(s, "org_b", "user", "", "outcome:strategy", "Other tenant fact.")
    a_facts = list_facts(s, "org_a")
    assert len(a_facts) == 1 and a_facts[0]["scope"] == "agent"
    assert list_facts(s, "org_a", scope="user") == []
    assert list_facts(s, "org_b", scope="agent") == []
    assert s.get_memory("org_b", a_facts[0]["memory_id"]) is None


def test_redaction_never_stores_raw_secrets(tmp_path):
    assert "sk-live" not in redact_text("key is sk-live-abcdef123456")
    assert "[redacted]" in redact_text("token: ghp_abcdef1234567890")
    s = _store(tmp_path)
    rec = add_fact(s, "org_a", "user", "", "note",
                   "The API key is sk-proj-1234567890abcdef for the tool.")
    assert "sk-proj" not in rec["fact"] and "[redacted]" in rec["fact"]
    stored = s.get_memory("org_a", rec["memory_id"])
    assert "sk-proj" not in stored["fact"]


def test_write_rule():
    assert can_write("owner", "u1", "agent", "agent.x@1") is True
    assert can_write("admin", "u1", "session", "m1") is True
    assert can_write("member", "u1", "user", "") is True
    assert can_write("member", "u1", "user", "u1") is True
    assert can_write("member", "u1", "user", "someone-else") is False
    assert can_write("member", "u1", "agent", "agent.x@1") is False
    assert can_write("viewer", "u1", "user", "") is False


def test_memory_context_has_provenance_labels(tmp_path):
    s = _store(tmp_path)
    t = "org_a"
    assert memory_context(s, t) == ""
    add_fact(s, t, "user", "", "outcome:strategy", "Chose niche-first.",
             mission_id="m_123")
    add_fact(s, t, "agent", "agent.research.beta@1.2.0", "selected:code",
             "Produced the selected candidate.")
    ctx = memory_context(s, t, task_type="code")
    assert "[memory:user · outcome:strategy · from mission m_123" in ctx
    assert "[memory:agent agent.research.beta@1.2.0" in ctx


def test_extract_from_mission_writes_three_scopes(tmp_path):
    s = _store(tmp_path)
    t = "org_a"
    mission = s.create_mission(t, "user1", "Launch compare", {
        "objective": "Compare launch strategies", "task_type": "strategy",
        "deliverables": ["brief"], "acceptance": ["ok"]}, 10.0)
    mid = mission["id"]
    s.set_mission_status(t, mid, "completed")
    cand = {"candidate_id": "cand_1", "mission_id": mid, "title": "Niche first",
            "strategy": "niche-first", "summary": "Win one segment first.",
            "agent_version_id": "agent.research.alpha@1.2.0", "claims": [],
            "assumptions": [], "risks": [], "estimated_cost_usd": 1.0,
            "time_to_learn": "2 weeks", "deliverable": None, "version": 1,
            "revises": None}
    s.put_candidate(t, cand)
    s.put_evaluation(t, mid, {"evaluation_id": "ev_1", "candidate_id": "cand_1",
                              "dimensions": {"evidence": {"score": 0.9},
                                             "cost": {"score": 0.7}}})
    facts = extract_from_mission(s, t, mid)
    assert {f["scope"] for f in facts} == {"user", "agent", "session"}
    user_fact = next(f for f in facts if f["scope"] == "user")
    assert user_fact["topic"] == "outcome:strategy"
    assert "Niche first" in user_fact["fact"]
    agent_fact = next(f for f in facts if f["scope"] == "agent")
    assert agent_fact["scope_ref"] == "agent.research.alpha@1.2.0"
    assert "0.80" in agent_fact["fact"]
    # replay: extraction is idempotent through curation dedupe
    extract_from_mission(s, t, mid)
    assert len(list_facts(s, t)) == 3


def test_context_reaches_the_model_prompt():
    """The retrieved memory block is appended to the adapter's user
    prompt as labelled background (it never enters the source
    library, so it can never be cited as evidence)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["user"] = body["messages"][1]["content"]
        payload = {"title": "T", "summary": "S",
                   "claims": [{"text": "c", "source_ids": [],
                               "confidence_category": "unsupported"}],
                   "assumptions": [], "risks": [],
                   "estimated_cost_usd": 1, "time_to_learn": "1 week",
                   "deliverable": "D"}
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    settings = Settings(llm_base_url="http://stub.test/v1",
                        llm_api_key="test-key")
    adapter = LLMResearchAdapter(
        settings, transport=httpx.MockTransport(handler))
    req = AgentRequest(
        mission_id="m", task_id="t", objective="Write a draft",
        deliverables=["draft"], output_schema={}, task_type="writing",
        context="- [memory:user · outcome:writing] Earlier draft won.")
    manifest = {"agent_id": "agent.writer", "version": "1.0.0",
                "strategy": "niche-first"}
    asyncio.run(adapter.invoke(req, manifest))
    assert "Long-term memory relevant to this mission" in captured["user"]
    assert "[memory:user · outcome:writing] Earlier draft won." in captured["user"]
    assert "NOT evidence" in captured["user"]
