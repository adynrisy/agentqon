"""Mission export (Master Build V3 §7/§16): a self-contained decision
brief in Markdown. Contains candidates, claims with their sources,
critiques, evaluation scores, route decisions and the cost ledger —
never credentials or connection secrets (none exist in mission data by
construction; connection ids are omitted deliberately anyway).
"""
from __future__ import annotations


def mission_markdown(detail: dict) -> str:
    m = detail["mission"]
    lines = [f"# {m['title']}", "",
             f"- Mission: `{m['id']}`  Status: **{m['status']}**",
             f"- Objective: {m['contract']['objective']}",
             f"- Spent (known): ${m['spent']:.2f}",
             f"- Data classification: {m['contract'].get('data_classification', 'internal')}", ""]
    decisions = detail.get("route_decisions") or []
    if decisions:
        lines += ["## Model routing", ""]
        for d in decisions:
            lines.append(f"- {d['role']}: **{d['model_id']}** via `{d['connection_id']}` "
                         f"(mode {d['mode']}, estimate confidence {d['estimate_confidence']}) — "
                         + "; ".join(d["reason_codes"]))
        lines.append("")
    evals = {e["candidate_id"]: e for e in detail["evaluations"]}
    for cand in detail["candidates"]:
        version = f" (v{cand.get('version', 1)}" + \
            (f", revises {cand['revises']}" if cand.get("revises") else "") + ")"
        lines += [f"## Candidate — {cand['title']}{version}", "",
                  f"Strategy: {cand['strategy']} · Agent: {cand['agent_version_id']} · "
                  f"Status: {cand['status']}", "", cand["summary"], "", "### Claims", ""]
        for cl in cand["claims"]:
            srcs = ", ".join(cl["source_ids"]) or "NO SOURCE"
            lines.append(f"- [{cl['confidence_category']}/{cl['verification_status']}] "
                         f"{cl['text']} (sources: {srcs})")
        ev = evals.get(cand["candidate_id"])
        if ev:
            dims = ", ".join(f"{k}={v['score']}" for k, v in ev["dimensions"].items())
            passed = sum(1 for t in ev["deterministic_tests"] if t["passed"])
            lines += ["", f"Evaluation (rubric {ev['rubric_version']}): {dims} · "
                          f"deterministic tests {passed}/{len(ev['deterministic_tests'])} passed"]
        lines.append("")
    if detail["critiques"]:
        lines += ["## Critiques", ""]
        for c in detail["critiques"]:
            lines.append(f"- [{c['severity']}] {c['issue']} — suggested correction: "
                         f"{c.get('suggested_correction', '')}")
        lines.append("")
    usage = detail["usage"]
    lines += ["## Usage (known costs)", "",
              f"Total: ${usage['total_usd']:.4f} — " +
              ", ".join(f"{k}: ${v:.4f}" for k, v in usage["by_category"].items()),
              "", "_Unknown-price model calls are recorded in the ledger with their "
              "token counts and are not included above; they are never shown as $0._"]
    return "\n".join(lines) + "\n"
