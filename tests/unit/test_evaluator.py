"""Evidence engine tests (Ch.11) and adapter contracts (Ch.7).

v20: the adapter-contract tests run against the real LLM adapters
driven by the tests-only stub model (tests/stub_llm.py) — the product
no longer ships canned adapters to test against."""
import asyncio

from app.adapters import AgentRequest
from app.domain import Candidate, Claim
from app.evaluator import check_citations, evaluate_candidate
from app.llm_adapter import LLMCriticAdapter, LLMResearchAdapter
from app.registry import SEED_AGENTS
from app.sources import SEED_SOURCES
from tests import stub_llm

SOURCES = {s.source_id: s for s in SEED_SOURCES}


def cand_with(claims):
    return Candidate(mission_id="mis_x", title="T", strategy="niche-first",
                     summary="S", agent_version_id="a@1", claims=claims,
                     assumptions=["A"], risks=["R"], estimated_cost_usd=500)


def test_claim_without_source_fails_citation_check():
    tests = check_citations(cand_with([Claim(text="Unsupported claim")]), SOURCES)
    assert tests[0]["passed"] is False


def test_opinion_cannot_support_unqualified_factual_claim():
    c = Claim(text="Partnerships replace sales teams.", source_ids=["src_seed_founder_opinion_2026"])
    tests = check_citations(cand_with([c]), SOURCES)
    assert tests[0]["passed"] is False


def test_contested_claim_with_opinion_source_is_labelled_not_theatre():
    c = Claim(text="Partnerships replace sales teams.", source_ids=["src_seed_founder_opinion_2026"],
              confidence_category="contested")
    tests = check_citations(cand_with([c]), SOURCES)
    assert tests[0]["passed"] is True


def test_well_sourced_candidate_scores_full_evidence():
    c = Claim(text="68% adopted via community.", source_ids=["src_seed_smb_survey_2026"])
    ev = evaluate_candidate(cand_with([c, c]), [], SOURCES)
    assert ev.dimensions["evidence"].score == 5.0
    assert ev.rubric_version == "research-v1"
    assert all(t["passed"] for t in ev.deterministic_tests)


def _llm_adapters():
    settings = stub_llm.settings()
    return (LLMResearchAdapter(settings, transport=stub_llm.transport()),
            LLMCriticAdapter(settings, transport=stub_llm.transport()))


def test_adapter_contract_both_research_agents():
    research, _ = _llm_adapters()
    for manifest in [a.model_dump() for a in SEED_AGENTS if a.strategy in ("niche-first", "partnership-led")]:
        req = AgentRequest(mission_id="m", task_id="t", objective="Launch a thing",
                           deliverables=["Brief"], output_schema={})
        resp = asyncio.run(research.invoke(req, manifest))
        out = resp.output
        for key in ("title", "strategy", "summary", "claims", "assumptions", "risks"):
            assert key in out, f"{manifest['agent_id']} missing {key}"
        # Usage is recorded from the provider's token counts (the stub
        # model is local and $0 — cost is known-zero, never invented).
        assert resp.usage["input_tokens"] == 100 and resp.usage["cost_usd"] == 0.0


def test_two_agents_produce_genuinely_distinct_strategies():
    research, _ = _llm_adapters()
    req = AgentRequest(mission_id="m", task_id="t", objective="Launch", deliverables=[], output_schema={})
    outs = [asyncio.run(research.invoke(req, a.model_dump())).output
            for a in SEED_AGENTS if a.strategy in ("niche-first", "partnership-led")]
    assert outs[0]["strategy"] != outs[1]["strategy"]
    assert outs[0]["estimated_cost_usd"] != outs[1]["estimated_cost_usd"]


def test_critic_targets_the_contested_claim():
    research, critic = _llm_adapters()
    req = AgentRequest(mission_id="m", task_id="t", objective="Launch", deliverables=[], output_schema={})
    beta = [a.model_dump() for a in SEED_AGENTS if a.strategy == "partnership-led"][0]
    out = asyncio.run(research.invoke(req, beta)).output
    from app.domain import Candidate as C
    cand = C(mission_id="m", title=out["title"], strategy=out["strategy"], summary=out["summary"],
             agent_version_id=out["agent_version_id"], claims=out["claims"],
             assumptions=out["assumptions"], risks=out["risks"],
             estimated_cost_usd=out["estimated_cost_usd"]).model_dump()
    creq = AgentRequest(mission_id="m", task_id="t2", objective="Launch", deliverables=[],
                        output_schema={"candidates": [cand]})
    crit_manifest = [a.model_dump() for a in SEED_AGENTS if a.strategy == "critic"][0]
    resp = asyncio.run(critic.invoke(creq, crit_manifest))
    critiques = resp.output["critiques"]
    assert len(critiques) == 1 and critiques[0]["severity"] == "high"
    assert "sales team" in critiques[0]["target_claim"]
