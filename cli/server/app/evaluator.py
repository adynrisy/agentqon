"""AgentQon — evaluation engine (Ch.11, Ch.14).

Layered checks, cheapest first: schema validity -> deterministic tests ->
citation checks (source exists, excerpt present, kind supports the claim)
-> dimension scoring on a versioned rubric. Dimension scores stay visible;
nothing is hidden inside one opaque total.
"""
from __future__ import annotations

from .domain import Candidate, DimensionScore, EvaluationRecord

RUBRIC_VERSION = "research-v1"


def check_citations(candidate: Candidate, sources: dict) -> list[dict]:
    """Citation checks per Ch.11: source retrieved, excerpt exists, and the
    source kind can actually support the claim (opinion != evidence)."""
    tests = []
    for claim in candidate.claims:
        if not claim.source_ids:
            tests.append({"name": f"citation:{claim.claim_id}", "passed": False,
                          "detail": "Claim has no source attached (citation theatre check)"})
            claim.verification_status = "checked"
            continue
        ok, details = True, []
        for sid in claim.source_ids:
            src = sources.get(sid)
            if src is None:
                ok = False
                details.append(f"{sid}: source not retrieved")
            elif not src.excerpt.strip():
                ok = False
                details.append(f"{sid}: no excerpt on record")
            elif src.kind == "opinion" and claim.confidence_category != "contested":
                ok = False
                details.append(f"{sid}: opinion source cannot support an unqualified factual claim")
        tests.append({"name": f"citation:{claim.claim_id}", "passed": ok,
                      "detail": "; ".join(details) if details else "source retrieved, excerpt present, kind supports claim"})
        claim.verification_status = "checked"
    return tests


def evaluate_candidate(candidate: Candidate, critiques: list[dict], sources: dict,
                       elapsed_ms: int = 0) -> EvaluationRecord:
    citation_tests = check_citations(candidate, sources)
    passed_citations = sum(1 for t in citation_tests if t["passed"])
    total_claims = max(1, len(candidate.claims))

    schema_tests = [
        {"name": "schema:candidate_fields", "passed": bool(candidate.title and candidate.summary and candidate.strategy),
         "detail": "title, summary and strategy present"},
        {"name": "schema:has_claims", "passed": len(candidate.claims) >= 2,
         "detail": f"{len(candidate.claims)} claims attached"},
        {"name": "schema:has_assumptions_and_risks", "passed": bool(candidate.assumptions and candidate.risks),
         "detail": f"{len(candidate.assumptions)} assumptions, {len(candidate.risks)} risks"},
        {"name": "distinct_strategy", "passed": bool(candidate.strategy),
         "detail": f"approach={candidate.strategy}"},
    ]
    all_tests = schema_tests + citation_tests
    schema_ok = all(t["passed"] for t in schema_tests)

    high_critiques = [c for c in critiques
                      if c["target_candidate_id"] == candidate.candidate_id and c["severity"] == "high"]
    evidence_score = round(5.0 * passed_citations / total_claims, 2)
    if high_critiques:
        evidence_score = max(1.0, evidence_score - 1.0)
    completeness = 5.0 if schema_ok else 2.5
    robustness = 4.0 if not high_critiques else 2.5
    cost_eff = 5.0 if candidate.estimated_cost_usd <= 1500 else (3.5 if candidate.estimated_cost_usd <= 3500 else 2.5)

    dims = {
        "evidence": DimensionScore(score=evidence_score,
                                   notes=f"{passed_citations}/{total_claims} claims pass citation checks"
                                         + ("; high-severity critique reduced score" if high_critiques else "")),
        "completeness": DimensionScore(score=completeness,
                                       notes="All requested sections present" if schema_ok else "Missing candidate fields"),
        "robustness": DimensionScore(score=robustness,
                                     notes="No high-severity critiques" if not high_critiques
                                           else f"{len(high_critiques)} high-severity critique(s) open"),
        "cost_efficiency": DimensionScore(score=cost_eff,
                                          notes=f"Estimated execution cost ${candidate.estimated_cost_usd:,.0f}"),
    }
    disagreements = [c["issue"] for c in high_critiques]
    return EvaluationRecord(
        candidate_id=candidate.candidate_id, rubric_version=RUBRIC_VERSION,
        dimensions=dims, deterministic_tests=all_tests, disagreements=disagreements,
        confidence="medium" if not high_critiques else "low",
        cost_usd=0.07, elapsed_ms=elapsed_ms)
