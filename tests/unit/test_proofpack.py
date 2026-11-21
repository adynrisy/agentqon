"""Proof Pack tests (v15): confidence labels are computed from the
recorded evidence — citation checks, critique severities, source
records — never from agent self-assessment."""
import tempfile

from app.domain import Candidate, Claim, event_envelope
from app.proofpack import build_proof_pack, proof_pack_markdown
from app.store import Store

T = "org_pp"


def _store(tmp):
    return Store(f"{tmp}/pp.db", f"{tmp}/artifacts")


def _source(sid, kind, excerpt="A real excerpt with evidence in it."):
    return {"source_id": sid, "title": f"Source {sid}", "kind": kind,
            "published_at": "2026-01-01", "retrieved_at": "2026-01-02",
            "excerpt": excerpt, "url": None, "publisher": "example.org",
            "content_hash": "ab" * 32, "retrieval_method": "seed"}


def _seed_mission(s):
    mission = s.create_mission(T, "tester", "PP mission",
                                {"objective": "Decide the launch",
                                 "deliverables": ["Brief"], "acceptance": ["Cites"],
                                 "task_type": "strategy",
                                 "data_classification": "internal"}, 10.0)
    mid = mission["id"]
    s.put_source(T, _source("src_good", "primary"))
    s.put_source(T, _source("src_opinion", "opinion"))
    good = Candidate(mission_id=mid, title="Good", strategy="niche-first",
                     summary="s", agent_version_id="a@1",
                     claims=[Claim(text="Backed claim", source_ids=["src_good"])])
    mixed = Candidate(mission_id=mid, title="Mixed", strategy="partnership-led",
                      summary="s", agent_version_id="b@1",
                      claims=[Claim(text="Backed but critiqued",
                                    source_ids=["src_good"]),
                              Claim(text="Bare assertion", source_ids=[])])
    weak = Candidate(mission_id=mid, title="Weak", strategy="broad",
                     summary="s", agent_version_id="c@1",
                     claims=[Claim(text="Opinion dressed as fact",
                                   source_ids=["src_opinion"])])
    for c in (good, mixed, weak):
        s.put_candidate(T, c.model_dump())
    s.put_evaluation(T, mid, {
        "evaluation_id": "eval_good", "candidate_id": good.candidate_id,
        "rubric_version": "research-v1", "confidence": "medium",
        "dimensions": {"evidence": {"score": 5.0, "max": 5, "notes": ""}},
        "deterministic_tests": [{"name": "t", "passed": True}], 
        "disagreements": [], "cost_usd": 0.07, "elapsed_ms": 1})
    s.put_critique(T, mid, {
        "critique_id": "crt_1", "target_candidate_id": mixed.candidate_id,
        "target_claim": "Backed but critiqued", "issue": "Load-bearing gap",
        "evidence_or_test": "check", "severity": "high",
        "suggested_correction": "Fix it", "confidence": "high",
        "critic_agent_version_id": "critic@1"})
    s.set_mission_selected(T, mid, good.candidate_id)
    s.set_mission_status(T, mid, "completed")
    return mid, good, mixed, weak


def test_labels_are_computed_from_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid, good, mixed, weak = _seed_mission(s)
        pack = build_proof_pack(s, T, mid)
        assert pack is not None and pack["kind"] == "proof_pack"
        by_id = {c["candidate_id"]: c for c in pack["candidates"]}
        # fully backed, no high critique -> verified claim AND section
        assert by_id[good.candidate_id]["claims"][0]["label"] == "verified"
        assert by_id[good.candidate_id]["confidence_label"] == "verified"
        # cited but a high critique is open -> estimated; sourceless -> assumed
        mclaims = {c["text"]: c["label"] for c in by_id[mixed.candidate_id]["claims"]}
        assert mclaims["Backed but critiqued"] == "estimated"
        assert mclaims["Bare assertion"] == "assumed"
        assert by_id[mixed.candidate_id]["confidence_label"] == "estimated"
        # opinion source cannot support an unqualified factual claim
        assert by_id[weak.candidate_id]["claims"][0]["label"] == "estimated"
        # selected candidate verified -> mission overall verified
        assert pack["confidence_summary"]["overall"] == "verified"
        assert pack["confidence_summary"]["verified"] == 1
        assert pack["confidence_summary"]["assumed"] == 1
        assert pack["confidence_summary"]["estimated"] == 2
        # sources carry provenance + retrieval state
        srcs = {x["source_id"]: x for x in pack["sources"]}
        assert srcs["src_good"]["retrieved"] is True
        assert srcs["src_good"]["excerpt_present"] is True
        assert srcs["src_good"]["publisher"] == "example.org"
        assert good.candidate_id in srcs["src_good"]["cited_by"]
        # evaluation + critique ride along
        assert by_id[good.candidate_id]["evaluation"]["dimensions"]["evidence"] == 5.0
        assert pack["critiques"][0]["severity"] == "high"


def test_markdown_renders_labels_and_sources():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid, good, _, _ = _seed_mission(s)
        md = proof_pack_markdown(build_proof_pack(s, T, mid))
        assert "Proof Pack" in md and "[verified]" in md and "[assumed]" in md
        assert "Source src_good" in md and "Critic findings" in md


def test_simulation_promoted_brief_is_estimated():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        mid, _, _, _ = _seed_mission(s)
        s.put_event(T, event_envelope(T, "mission", mid, "MissionCreated",
                                      {"title": "PP mission",
                                       "origin": "simulation-promotion"}))
        pack = build_proof_pack(s, T, mid)
        assert pack["brief"]["confidence_label"] == "estimated"
        assert "SIMULATED ESTIMATE" in pack["brief"]["label_reason"]


def test_missing_mission_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        s = _store(tmp)
        assert build_proof_pack(s, T, "mis_nope") is None
        assert build_proof_pack(s, "org_other", "mis_nope") is None
