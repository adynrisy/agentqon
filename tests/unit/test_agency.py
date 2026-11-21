"""Agency roster: parsing, import, lifecycle discipline, persona runs."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.agency import import_roster, parse_agent_file, roster_dir
from app.registry import rank_agents
from app.store import Store


def test_parse_real_roster_file():
    root = roster_dir()
    parsed = parse_agent_file(root / "design" / "design-cinelock-muse.md")
    assert parsed is not None
    assert parsed["meta"]["name"]
    assert len(parsed["body"]) > 500


def test_import_installs_active_agents_with_personas(tmp_path):
    store = Store(str(tmp_path / "agency.db"))
    result = import_roster(store, "org_imp")
    assert result["total"] >= 275, result  # 280 agents; strategy/ playbooks have no frontmatter and are not agents
    agents = store.list_agents("org_imp")
    agency = [a for a in agents if a["agent_id"].startswith("agency.")]
    assert len(agency) == result["total"]
    assert all(a["lifecycle"] == "active" for a in agency)
    # v13 standing order: imported agents are ACTIVE, so the scheduler
    # pool includes advisory-division agency agents (research.web)
    ranked = rank_agents(agents, ["research.web"])
    assert any(a["agent_id"].startswith("agency.") for a in ranked)
    # personas stored, incl. the custom CineLock Muse — under its
    # renamed AgentQon id (the shipped map resolves the legacy id)
    from app.display_names import new_agent_id
    persona = store.get_persona(
        new_agent_id("agency.design.design-cinelock-muse")
        or "agency.design.design-cinelock-muse")
    assert persona and len(persona) > 500
    # research-mapped divisions carry research.web; engineering does not
    mkt = next(a for a in agency if a["agent_id"].startswith("agency.marketing."))
    assert "research.web" in mkt["capabilities"]
    eng = next(a for a in agency if a["agent_id"].startswith("agency.engineering."))
    assert "research.web" not in eng["capabilities"]
    assert "persona.task" in eng["capabilities"]


def test_reimport_preserves_lifecycle(tmp_path):
    store = Store(str(tmp_path / "agency2.db"))
    import_roster(store, "org_imp2")
    agents = store.list_agents("org_imp2")
    target = next(a for a in agents if a["agent_id"].startswith("agency.marketing."))
    store.set_agent_lifecycle("org_imp2", target["agent_version_id"], "active")
    import_roster(store, "org_imp2")
    after = next(a for a in store.list_agents("org_imp2")
                 if a["agent_id"] == target["agent_id"])
    assert after["lifecycle"] == "active"


def test_persona_reaches_llm_system_prompt():
    from app.adapters import AgentRequest
    from app.config import load_settings
    from app.llm_adapter import LLMResearchAdapter

    adapter = LLMResearchAdapter(load_settings())

    class FakeResult:
        provider_request_id = "x"
        usage = {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}
        data = {"title": "T", "summary": "S",
                "claims": [{"text": "c", "source_ids": ["src_seed_smb_survey_2026"],
                            "confidence_category": "supported"}],
                "assumptions": [], "risks": [], "estimated_cost_usd": 1,
                "time_to_learn": "1 week"}

    class FakeClient:
        async def chat_json(self, system, user, **kw):
            assert "VOICE OF THE BRAND" in system or "brand" in system.lower()
            assert "Platform rules" in system
            return FakeResult()

    adapter.client = FakeClient()
    req = AgentRequest(mission_id="m", task_id="t", objective="o",
                       deliverables=["d"], output_schema={})
    manifest = {"agent_id": "agency.marketing.x", "version": "1.0.0",
                "strategy": "persona",
                "persona_prompt": "You are the VOICE OF THE BRAND guardian."}
    resp = asyncio.run(adapter.invoke(req, manifest))
    assert resp.output["title"] == "T"
