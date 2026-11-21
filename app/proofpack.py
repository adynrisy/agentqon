"""AgentQon — Proof Pack (v15).

The trust artifact for a completed mission: ONE structured document
that shows not just what the platform concluded, but what the
conclusion rests on. It is assembled entirely from records the system
already keeps — mission contract, candidates, evaluation records,
critiques and the source library — and re-rendered as a downloadable
Markdown artifact through the existing artifact store.

Confidence labels are COMPUTED here, per claim, from recorded
evidence. They are never copied from an agent's self-assessment and
never hard-coded per mission:

  verified  — the claim carries at least one citation that passes the
              evaluator's own citation checks (source retrieved,
              excerpt present, source kind can support the claim —
              app/evaluator.py check_citations), the claim is not
              contested, and no high-severity critique is open against
              the candidate that made it.
  assumed   — the claim carries no source at all. It may still be
              true; the platform simply has no evidence for it, and
              the pack says so.
  estimated — everything in between: sources attached but the citation
              check fails (missing excerpt, unsupported kind), the
              claim is contested, or a high-severity critique is open
              against its candidate. Simulation-derived material also
              lands here: a mission promoted out of a simulation world
              has its brief section labelled estimated (its premise is
              a SIMULATED ESTIMATE until real evidence replaces it).

Section labels roll up from claim labels: a candidate is "verified"
only if every claim in it is verified, "assumed" if none of its claims
carries any source, and "estimated" otherwise. The mission's overall
label is the selected candidate's label, or — with no selection yet —
the weakest candidate label (verified > estimated > assumed).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Optional

from .domain import Candidate, SourceRecord, now_iso
from .evaluator import check_citations

PACK_VERSION = "proof-pack-v1"
_LABEL_RANK = {"verified": 3, "estimated": 2, "assumed": 1}


def _claim_label(claim: dict, citation_ok: bool, candidate_has_high: bool) -> tuple[str, str]:
    """(label, reason) for one claim — reasons cite the actual evidence
    state, so the pack explains itself."""
    sids = claim.get("source_ids") or []
    if not sids:
        return "assumed", "no source attached — nothing on record backs this claim"
    if citation_ok and not candidate_has_high \
            and claim.get("confidence_category") != "contested":
        return "verified", (f"{len(sids)} citation(s) pass the evidence checks "
                            "and no high-severity critique is open")
    bits = []
    if not citation_ok:
        bits.append("attached citation(s) fail the evidence checks "
                    "(missing source/excerpt, or the source kind cannot support the claim)")
    if claim.get("confidence_category") == "contested":
        bits.append("the claim is contested")
    if candidate_has_high:
        bits.append("a high-severity critique is open against this candidate")
    return "estimated", "; ".join(bits)


def _section_label(claim_labels: list[str], claims: list[dict]) -> str:
    if claim_labels and all(l == "verified" for l in claim_labels):
        return "verified"
    if not any((c.get("source_ids") or []) for c in claims):
        return "assumed"
    return "estimated"


def build_proof_pack(store, tenant_id: str, mission_id: str) -> Optional[dict]:
    """Assemble the proof pack dict for a mission, or None if the
    mission does not exist in this tenant. Status gating (completed
    only) is the endpoint's job."""
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None:
        return None
    candidates = store.list_candidates(tenant_id, mission_id)
    evaluations = {e["candidate_id"]: e
                   for e in store.list_evaluations(tenant_id, mission_id)}
    critiques = store.list_critiques(tenant_id, mission_id)
    tenant_sources = {s["source_id"]: s for s in store.list_sources(tenant_id)}
    events = store.list_events(tenant_id, mission_id)
    sim_promoted = any(
        e.get("event_type") == "MissionCreated"
        and (e.get("payload") or {}).get("origin") == "simulation-promotion"
        for e in events)

    src_objs = {}
    for sid, s in tenant_sources.items():
        try:
            src_objs[sid] = SourceRecord(**s)
        except Exception:
            continue  # a malformed record simply fails its citation checks

    high_by_candidate: dict[str, list[dict]] = {}
    for c in critiques:
        if c.get("severity") == "high":
            high_by_candidate.setdefault(c.get("target_candidate_id", ""), []).append(c)

    cited_by: dict[str, list[str]] = {}
    counts = {"verified": 0, "estimated": 0, "assumed": 0}
    cand_sections = []
    for cand in candidates:
        cand_obj = Candidate(**deepcopy(cand))
        citation_tests = check_citations(cand_obj, src_objs)
        ok_by_claim = {}
        for t in citation_tests:
            if t["name"].startswith("citation:"):
                ok_by_claim[t["name"].split(":", 1)[1]] = bool(t["passed"])
        has_high = bool(high_by_candidate.get(cand["candidate_id"]))
        claim_rows = []
        labels = []
        for claim in cand.get("claims", []):
            label, reason = _claim_label(
                claim, ok_by_claim.get(claim.get("claim_id", ""), False), has_high)
            labels.append(label)
            counts[label] += 1
            for sid in claim.get("source_ids") or []:
                cited_by.setdefault(sid, []).append(cand["candidate_id"])
            claim_rows.append({
                "claim_id": claim.get("claim_id"), "text": claim.get("text", ""),
                "confidence_category": claim.get("confidence_category"),
                "verification_status": claim.get("verification_status"),
                "source_ids": claim.get("source_ids") or [],
                "label": label, "label_reason": reason})
        section = _section_label(labels, cand.get("claims", []))
        ev = evaluations.get(cand["candidate_id"])
        cand_sections.append({
            "candidate_id": cand["candidate_id"], "title": cand.get("title", ""),
            "strategy": cand.get("strategy", ""), "agent_version_id": cand.get("agent_version_id", ""),
            "status": cand.get("status", "proposed"),
            "selected": mission.get("selected_candidate_id") == cand["candidate_id"],
            "deliverable": cand.get("deliverable"),
            "confidence_label": section,
            "evaluation": ({"rubric_version": ev.get("rubric_version"),
                            "confidence": ev.get("confidence"),
                            "dimensions": {k: v.get("score")
                                           for k, v in (ev.get("dimensions") or {}).items()},
                            "deterministic_tests_passed":
                                sum(1 for t in ev.get("deterministic_tests", []) if t.get("passed")),
                            "deterministic_tests_total": len(ev.get("deterministic_tests", []))}
                           if ev else None),
            "claims": claim_rows})

    source_rows = []
    for sid in sorted(cited_by):
        s = tenant_sources.get(sid)
        if s is None:
            source_rows.append({"source_id": sid, "retrieved": False,
                                "cited_by": cited_by[sid],
                                "note": "cited but no retrieved source record exists"})
            continue
        source_rows.append({
            "source_id": sid, "title": s.get("title", ""), "kind": s.get("kind", ""),
            "url": s.get("url"), "publisher": s.get("publisher"),
            "published_at": s.get("published_at"), "retrieved_at": s.get("retrieved_at"),
            "retrieval_method": s.get("retrieval_method"),
            "content_hash": s.get("content_hash"),
            "excerpt_present": bool((s.get("excerpt") or "").strip()),
            "retrieved": True, "cited_by": cited_by[sid]})

    selected_id = mission.get("selected_candidate_id")
    selected_section = next((c for c in cand_sections
                             if c["candidate_id"] == selected_id), None)
    if selected_section is not None:
        overall = selected_section["confidence_label"]
        overall_reason = f"the selected candidate's roll-up ({overall})"
    elif cand_sections:
        overall = min((c["confidence_label"] for c in cand_sections),
                      key=lambda l: _LABEL_RANK[l])
        overall_reason = ("no candidate selected yet — the weakest candidate "
                          "roll-up stands for the mission")
    else:
        overall, overall_reason = "assumed", "the mission produced no candidates"

    brief_label, brief_reason = overall, f"inherits the mission roll-up: {overall_reason}"
    if sim_promoted:
        brief_label = "estimated"
        brief_reason = ("this mission was promoted from a simulation world — its "
                        "premise is a SIMULATED ESTIMATE until real evidence "
                        "replaces it")

    contract = mission.get("contract") or {}
    return {
        "kind": "proof_pack", "pack_version": PACK_VERSION,
        "mission_id": mission_id, "title": mission.get("title", ""),
        "status": mission.get("status", ""),
        "task_type": contract.get("task_type") or "strategy",
        "data_classification": contract.get("data_classification", "internal"),
        "generated_at": now_iso(),
        "brief": {"objective": contract.get("objective", ""),
                  "deliverables": contract.get("deliverables", []),
                  "acceptance": contract.get("acceptance", []),
                  "confidence_label": brief_label, "label_reason": brief_reason},
        "selected_candidate_id": selected_id,
        "candidates": cand_sections,
        "critiques": [{"critique_id": c.get("critique_id"),
                       "target_candidate_id": c.get("target_candidate_id"),
                       "severity": c.get("severity"), "issue": c.get("issue", ""),
                       "target_claim": c.get("target_claim", ""),
                       "suggested_correction": c.get("suggested_correction", ""),
                       "critic_agent_version_id": c.get("critic_agent_version_id", "")}
                      for c in critiques],
        "sources": source_rows,
        "confidence_summary": {
            "overall": overall, "overall_reason": overall_reason,
            "claims_total": sum(counts.values()), **counts},
        "honesty_note": ("Labels are computed from the recorded evidence (citation "
                         "checks, critique severities, source records) at pack "
                         "generation time — never from an agent's self-assessment."),
    }


def proof_pack_markdown(pack: dict) -> str:
    """Readable rendering of the pack — same information, for humans.
    Mirrors app/exporter.py's brief style."""
    lines = [f"# 🧾 Proof Pack — {pack['title']}", "",
             f"- Mission: `{pack['mission_id']}` · Status: **{pack['status']}** · "
             f"Task type: {pack['task_type']} · Classification: {pack['data_classification']}",
             f"- Generated: {pack['generated_at']} · Pack format: {pack['pack_version']}",
             f"- **Overall confidence: {pack['confidence_summary']['overall']}** "
             f"({pack['confidence_summary']['overall_reason']})",
             f"- Claims on record: {pack['confidence_summary']['claims_total']} — "
             f"✅ verified {pack['confidence_summary']['verified']} · "
             f"🟡 estimated {pack['confidence_summary']['estimated']} · "
             f"⚪ assumed {pack['confidence_summary']['assumed']}", "",
             f"_{pack['honesty_note']}_", ""]
    b = pack["brief"]
    lines += ["## Mission brief", "",
              f"- Objective: {b['objective']}",
              f"- Deliverables: {', '.join(b['deliverables']) or '—'}",
              f"- Brief confidence: **{b['confidence_label']}** — {b['label_reason']}",
              ""]
    for c in pack["candidates"]:
        ev = c["evaluation"]
        lines += [f"## Candidate — {c['title']}", "",
                  f"Strategy: {c['strategy']} · Agent: `{c['agent_version_id']}` · "
                  f"{'SELECTED' if c['selected'] else c['status']} · "
                  f"**Section confidence: {c['confidence_label']}**", ""]
        if ev:
            dims = ", ".join(f"{k}={v}" for k, v in ev["dimensions"].items())
            lines += [f"Evaluation (rubric {ev['rubric_version']}, confidence "
                      f"{ev['confidence']}): {dims} · deterministic tests "
                      f"{ev['deterministic_tests_passed']}/"
                      f"{ev['deterministic_tests_total']} passed", ""]
        if c.get("deliverable"):
            lines += ["### Deliverable", "", c["deliverable"], ""]
        lines += ["### Claims and their evidence labels", ""]
        for cl in c["claims"]:
            srcs = ", ".join(f"`{s}`" for s in cl["source_ids"]) or "NO SOURCE"
            lines.append(f"- **[{cl['label']}]** {cl['text']} "
                         f"(sources: {srcs}) — {cl['label_reason']}")
        lines.append("")
    if pack["critiques"]:
        lines += ["## Critic findings", ""]
        for c in pack["critiques"]:
            lines.append(f"- [{c['severity']}] {c['issue']} — suggested "
                         f"correction: {c['suggested_correction']}")
        lines.append("")
    lines += ["## Cited sources", ""]
    if pack["sources"]:
        for s in pack["sources"]:
            if not s.get("retrieved"):
                lines.append(f"- `{s['source_id']}` — NOT RETRIEVED "
                             f"({s.get('note', '')})")
                continue
            prov = " · ".join(x for x in [
                s.get("publisher") or "", s.get("retrieval_method") or "",
                f"published {s['published_at']}" if s.get("published_at") else ""]
                if x)
            lines.append(f"- **{s['title']}** (`{s['source_id']}`, "
                         f"kind: {s['kind']})" + (f" — {prov}" if prov else ""))
            if s.get("url"):
                lines.append(f"  {s['url']}")
            lines.append(
                f"  Excerpt on record: {'yes' if s['excerpt_present'] else 'NO'}"
                + (f" · content hash `{s['content_hash'][:16]}…`"
                   if s.get("content_hash") else ""))
    else:
        lines.append("No claim in this mission cited a source.")
    lines.append("")
    return "\n".join(lines) + "\n"
