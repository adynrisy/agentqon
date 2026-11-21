"""AgentQon — evaluation science, operations/governance, operability
(gap-seventhdoc, groups E, F, G). Pure deterministic cores, stdlib only.

Seams (documented interfaces, NOT_TESTED until integration):
- W1 budget engine: G35 takes the agreed ceiling as a caller-supplied
  value and validates it against the internal ceiling; it implements
  NO second ceiling.
- W11 change-impact preview: G38 blast-radius preview is the preview
  CORE (affected sets over a dependency graph); the UI layer must reuse
  it, not build a second previewer.
- Per-model self-test health stamps (G40) and W4 M6 skill versions
  (B10) are caller-supplied inputs, not re-implemented probing.
- The evolve/lessons system (E27) is the lesson source; this module
  adds the regression-test records lessons lack.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# ---------------------------------------------------------------- E22
def record_feedback(feedback: list[dict], reviewer: str, criterion: str,
                    item_id: str, score: float) -> dict:
    rec = {"reviewer": reviewer, "criterion": criterion,
           "item_id": item_id, "score": float(score)}
    feedback.append(rec)
    return rec


def calibrate_reviewers(feedback: list[dict]) -> dict:
    """Compare reviewer judgments: inconsistent scoring (one reviewer
    far from the rest on the same items) and criteria reviewers
    interpret differently (high spread on one criterion)."""
    by_reviewer: dict[str, list[float]] = {}
    by_criterion: dict[str, list[float]] = {}
    for f in feedback:
        by_reviewer.setdefault(f["reviewer"], []).append(f["score"])
        by_criterion.setdefault(f["criterion"], []).append(f["score"])
    reviewer_means = {r: statistics.mean(v) for r, v in by_reviewer.items()
                      if v}
    overall = statistics.mean([f["score"] for f in feedback]) if feedback else 0.0
    inconsistent = [r for r, m in reviewer_means.items()
                    if abs(m - overall) >= 2.0 and len(by_reviewer[r]) >= 2]
    criterion_spread = {}
    differing = []
    for c, v in by_criterion.items():
        spread = (max(v) - min(v)) if len(v) >= 2 else 0.0
        criterion_spread[c] = spread
        if spread >= 3.0:
            differing.append(c)
    return {"reviewer_means": reviewer_means, "overall_mean": overall,
            "inconsistent_reviewers": inconsistent,
            "criteria_interpreted_differently": differing,
            "criterion_spread": criterion_spread}


# ---------------------------------------------------------------- E23
def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def contamination_check(eval_questions: list[str],
                        sources: dict[str, list[str]]) -> list[dict]:
    """Overlap between evaluation questions and training examples,
    prompt libraries, cached answers, previously exposed cases — where
    sources are available. Overlap is REPORTED, never silently used."""
    hits = []
    for q in eval_questions:
        qt = _tokens(q)
        if not qt:
            continue
        for source_name, examples in sources.items():
            for ex in examples:
                et = _tokens(ex)
                if not et:
                    continue
                overlap = len(qt & et) / len(qt | et)
                if overlap >= 0.5:
                    hits.append({"question": q, "source": source_name,
                                 "example": ex, "overlap": round(overlap, 3),
                                 "reason": "This evaluation question overlaps "
                                 "a previously exposed example."})
    return hits


# ---------------------------------------------------------------- E24
def benchmark_stats(trials: list[float]) -> dict:
    """Repeated trials: variability, sample size, uncertainty. One run
    never supports a superiority claim."""
    if not trials:
        return {"n": 0, "mean": None, "stdev": None,
                "uncertainty": None, "note": "No trials on record."}
    mean = statistics.mean(trials)
    stdev = statistics.stdev(trials) if len(trials) >= 2 else 0.0
    uncertainty = 1.96 * stdev / math.sqrt(len(trials)) if trials else None
    return {"n": len(trials), "mean": round(mean, 4),
            "stdev": round(stdev, 4),
            "uncertainty": round(uncertainty, 4) if uncertainty is not None else None}


def compare_benchmarks(a_trials: list[float], b_trials: list[float]) -> dict:
    """Superiority is declared ONLY with repeated trials on both sides
    and an uncertainty band that excludes zero difference. One
    successful run never declares superiority."""
    if len(a_trials) < 2 or len(b_trials) < 2:
        return {"superior": None,
                "reason": "Not enough trials: superiority needs repeated "
                "runs, not a single result."}
    a, b = benchmark_stats(a_trials), benchmark_stats(b_trials)
    diff = a["mean"] - b["mean"]
    band = (a["uncertainty"] or 0) + (b["uncertainty"] or 0)
    if diff > band:
        return {"superior": "a", "difference": round(diff, 4),
                "reason": "Repeated trials put A ahead beyond uncertainty."}
    if -diff > band:
        return {"superior": "b", "difference": round(diff, 4),
                "reason": "Repeated trials put B ahead beyond uncertainty."}
    return {"superior": None, "difference": round(diff, 4),
            "reason": "The difference is within uncertainty; no winner "
            "is declared."}


# ---------------------------------------------------------------- E25
def blind_compare(output_a: dict, output_b: dict, score_fn: Callable) -> dict:
    """Compare two outputs without revealing identities to reviewers:
    reviewers see 'first' / 'second' only; identities are revealed
    after scoring, in the returned record (never before)."""
    first, second = output_a, output_b
    scores = {"first": score_fn(first["content"]),
              "second": score_fn(second["content"])}
    winner = "first" if scores["first"] > scores["second"] else (
        "second" if scores["second"] > scores["first"] else "tie")
    revealed = {"first": first["id"], "second": second["id"]}
    return {"reviewer_view": {"first": first["content"],
                              "second": second["content"]},
            "scores": scores, "winner_label": winner,
            "revealed_after_scoring": revealed,
            "winner_id": revealed[winner] if winner != "tie" else None}


# ---------------------------------------------------------------- E26
def detect_gaming(score: float, required_features_present: list[bool],
                  superficial_checks_passed: bool) -> dict:
    """High score while violating the real objective = gaming: the
    superficial checks pass but a required feature is omitted."""
    missing = sum(1 for p in required_features_present if not p)
    gaming = score >= 0.8 and missing > 0
    return {"gaming": gaming,
            "reason": ("Flagged: a high score with required work missing — "
                       "the checks passed are superficial." if gaming else
                       "No gaming signal: score and required work agree."),
            "missing_required_count": missing,
            "superficial_checks_passed": superficial_checks_passed}


# ---------------------------------------------------------------- E27
def mistake_to_regression(mistakes: list[dict], mistake_id: str,
                          description: str, repro: str) -> dict:
    """A confirmed mistake becomes a reproducible test record, to be
    re-run after prompt / skill / model / orchestration changes."""
    rec = {"mistake_id": mistake_id, "description": description,
           "repro": repro, "status": "open", "runs": []}
    mistakes.append(rec)
    return rec


def run_regressions(mistakes: list[dict], change: str,
                    check_fn: Callable[[dict], bool]) -> list[dict]:
    results = []
    for m in mistakes:
        passed = bool(check_fn(m))
        m["runs"].append({"change": change, "passed": passed})
        m["status"] = "passing" if passed else "still_failing"
        results.append({"mistake_id": m["mistake_id"], "passed": passed})
    return results


# ---------------------------------------------------------------- F28/F29
def disaster_drill(scenario: str, restore_fn: Callable[[], bool]) -> dict:
    """Simulate database loss, corrupted artifacts, unavailable
    providers, interrupted workers. Recovery is VERIFIED by actually
    restoring — a backup merely existing proves nothing."""
    try:
        recovered = bool(restore_fn())
    except Exception as exc:
        recovered = False
        detail = f"Restore failed: {exc}"
    else:
        detail = "Restore check passed." if recovered else \
            "Restore check failed: the backup exists but recovery did not work."
    return {"scenario": scenario, "recovered": recovered, "detail": detail}


def recovery_objectives(objectives: dict, actual: dict) -> dict:
    """objectives/actual per scope: {rto_minutes, rpo_minutes}.
    Reports, per scope, whether recovery time and data-loss targets
    were met."""
    rows = {}
    for scope, obj in objectives.items():
        act = actual.get(scope, {})
        rows[scope] = {
            "rto_met": act.get("recovery_minutes", 10 ** 9) <= obj["rto_minutes"],
            "rpo_met": act.get("data_loss_minutes", 10 ** 9) <= obj["rpo_minutes"],
            "recovery_minutes": act.get("recovery_minutes"),
            "data_loss_minutes": act.get("data_loss_minutes")}
    return {"scopes": rows}


# ---------------------------------------------------------------- F30
def offline_capability_map(capabilities: list[dict],
                           provider_available: bool) -> dict:
    """capabilities: {name, needs_network}. When cloud providers are
    unavailable, local-model / cached / local-tool work continues;
    network-required capabilities are named as blocked."""
    rows = []
    for c in capabilities:
        if c.get("needs_network") and not provider_available:
            rows.append({"capability": c["name"], "status": "blocked",
                         "reason": "This needs the network, which is unavailable."})
        elif c.get("needs_network"):
            rows.append({"capability": c["name"], "status": "works",
                         "reason": "Provider is available."})
        else:
            rows.append({"capability": c["name"], "status": "works",
                         "reason": "Local model, cached reference, or local "
                         "tool — no cloud provider needed."})
    return {"provider_available": provider_available, "capabilities": rows,
            "blocked": [r["capability"] for r in rows if r["status"] == "blocked"]}


# ---------------------------------------------------------------- F31
def tenant_isolation_probe(tenant_a: str, tenant_b: str,
                           resources: list[dict],
                           access_fn: Callable[[str, dict], bool]) -> list[dict]:
    """Probe whether one tenant can reach another's files, logs, agent
    sessions, memory, artifacts. ANY hit is a failure finding."""
    violations = []
    for res in resources:
        if res.get("tenant_id") == tenant_b and access_fn(tenant_a, res):
            violations.append({"resource": res.get("resource_id"),
                               "kind": res.get("kind"),
                               "reason": f"Tenant {tenant_a} reached a "
                               f"{res.get('kind')} belonging to another tenant."})
    return violations


# ---------------------------------------------------------------- F32
def add_policy_exception(register: list[dict], policy: str,
                         justification: str, approver: str, scope: str,
                         expires_at: str) -> dict:
    rec = {"policy": policy, "justification": justification,
           "approver": approver, "scope": scope, "expires_at": expires_at}
    register.append(rec)
    return rec


def exception_active(rec: dict, now_iso: Optional[str] = None) -> bool:
    """Expired exceptions no longer apply — checked at decision time."""
    now = now_iso or datetime.now(timezone.utc).isoformat()
    return now < rec["expires_at"]


# ---------------------------------------------------------------- F33
def package_compliance_evidence(access_records: list, config_snapshot: dict,
                                execution_logs: list, approvals: list,
                                test_evidence: list,
                                retention_info: dict) -> dict:
    """Assemble a reviewable evidence package. BINDING: the package is
    NEVER labelled compliant merely because it exists — 'compliant'
    stays None; a human verdict is separate."""
    return {"label": "evidence package",
            "compliant": None,
            "verdict_note": "This package gathers evidence; it does not "
            "declare compliance. A reviewer decides that separately.",
            "access_records": access_records, "config_snapshot": config_snapshot,
            "execution_logs": execution_logs, "approvals": approvals,
            "test_evidence": test_evidence, "retention_info": retention_info}


# ---------------------------------------------------------------- F34
DEPENDENCY_FAILURE_EFFECTS = {"database": "blocked", "provider": "impaired",
                              "worker": "impaired", "credential": "blocked",
                              "local_model": "impaired", "tool": "impaired",
                              "external_service": "impaired"}


def dependency_map(capabilities: dict[str, list[str]],
                   failed: str) -> dict:
    """capabilities: name -> dependencies. What breaks when one
    dependency fails: blocked / impaired / works."""
    rows = {}
    for name, deps in capabilities.items():
        if failed in deps:
            rows[name] = ("blocked" if failed in ("database", "credential")
                          else "impaired")
        else:
            rows[name] = "works"
    return {"failed_dependency": failed, "capabilities": rows}


# ---------------------------------------------------------------- G35
INTERNAL_CEILING_USD = 1000.0  # mirrors MISSION_BUDGET_CEILING_USD (domain.py)


def build_mission_contract(deliverables: list[str], deadline: Optional[str],
                           quality_threshold: float,
                           agreed_budget_ceiling_usd: Optional[float],
                           permitted_actions: list[str],
                           acceptance_tests: list[str],
                           internal_ceiling_usd: float = INTERNAL_CEILING_USD) -> dict:
    """Mission contract BEFORE execution. Deadline is surfaced (the
    field already exists in MissionConstraints — verified in-tree);
    the agreed ceiling is caller-supplied (W1 budget engine interface)
    and validated against the internal ceiling — never a second ceiling.
    Quality threshold, permitted actions, acceptance tests required."""
    problems = []
    if not deliverables:
        problems.append("deliverables are required")
    if deadline:
        try:
            datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            problems.append("deadline is not a valid date/time")
    if not (0.0 < quality_threshold <= 1.0):
        problems.append("quality threshold must be between 0 and 1")
    if agreed_budget_ceiling_usd is not None and \
            agreed_budget_ceiling_usd > internal_ceiling_usd:
        problems.append("agreed budget ceiling is above the platform ceiling")
    if not acceptance_tests:
        problems.append("acceptance tests are required")
    if problems:
        raise ValueError("Invalid mission contract: " + "; ".join(problems))
    return {"deliverables": list(deliverables), "deadline": deadline,
            "quality_threshold": quality_threshold,
            "agreed_budget_ceiling_usd": agreed_budget_ceiling_usd,
            "permitted_actions": list(permitted_actions),
            "acceptance_tests": list(acceptance_tests),
            "amendments": [], "version": 1}


def amend_contract(contract: dict, changes: dict,
                   explicit: bool, amended_by: str = "") -> dict:
    """Changes require an EXPLICIT amendment. A silent edit is rejected
    and the contract is untouched."""
    if not explicit:
        return {"amended": False, "contract": contract,
                "reason": "Rejected: contract changes need an explicit "
                "amendment, not a silent edit."}
    new = dict(contract)
    new.update(changes)
    new["amendments"] = list(contract.get("amendments", [])) + [
        {"changes": dict(changes), "amended_by": amended_by}]
    new["version"] = contract.get("version", 1) + 1
    return {"amended": True, "contract": new,
            "reason": "Amendment recorded."}


# ---------------------------------------------------------------- G36/G37
SLA_CLASSES = {
    "quick": {"response_minutes": 15, "max_wait_minutes": 60,
              "completion_minutes": 240},
    "standard": {"response_minutes": 60, "max_wait_minutes": 480,
                 "completion_minutes": 2880},
    "deep": {"response_minutes": 240, "max_wait_minutes": 1440,
             "completion_minutes": 10080},
}


def check_sla(task_class: str, response_minutes: float,
              wait_minutes: float, completion_minutes: Optional[float] = None) -> dict:
    """Missed targets are reported SEPARATELY from task quality: this
    result says nothing about how good the work was."""
    targets = SLA_CLASSES.get(task_class)
    if targets is None:
        return {"task_class": task_class, "missed": ["unknown task class"],
                "quality_note": "SLA says nothing about task quality."}
    missed = []
    if response_minutes > targets["response_minutes"]:
        missed.append("response_time")
    if wait_minutes > targets["max_wait_minutes"]:
        missed.append("max_wait")
    if completion_minutes is not None and \
            completion_minutes > targets["completion_minutes"]:
        missed.append("completion_target")
    return {"task_class": task_class, "missed": missed,
            "escalate": bool(missed),
            "quality_note": "SLA says nothing about task quality."}


def prioritize_workload(tasks: list[dict]) -> list[dict]:
    """Priority by urgency, dependencies satisfied, impact, resource
    demand, deadlines — NOT arrival order. Blocked tasks (unsatisfied
    dependencies) sink regardless of urgency."""
    def score(t: dict) -> float:
        if t.get("dependencies_unsatisfied"):
            return -1000.0
        s = t.get("urgency", 0) * 3.0 + t.get("impact", 0) * 2.0
        s -= t.get("resource_demand", 0) * 0.5
        deadline = t.get("deadline_days")
        if deadline is not None:
            s += max(0.0, 10.0 - deadline)
        return s

    ranked = sorted(tasks, key=score, reverse=True)
    for i, t in enumerate(ranked):
        t["priority_rank"] = i + 1
        t["priority_score"] = round(score(t), 2)
    return ranked


# ---------------------------------------------------------------- G38
def blast_radius_preview(action: dict, graph: dict) -> dict:
    """Before a high-impact action, estimate affected files, services,
    missions, users, dependent artifacts. graph: {dependents: {node:
    [nodes that depend on it]}}. Transitive closure over dependents.
    This is the preview CORE W11's change-impact preview can reuse."""
    start = action.get("target", "")
    seen, frontier = set(), [start]
    while frontier:
        node = frontier.pop()
        if node in seen:
            continue
        seen.add(node)
        frontier.extend(graph.get("dependents", {}).get(node, []))
    seen.discard(start)
    by_kind: dict[str, list[str]] = {}
    for node in sorted(seen):
        kind = graph.get("kinds", {}).get(node, "unknown")
        by_kind.setdefault(kind, []).append(node)
    return {"action": action.get("action_id", "?"), "target": start,
            "affected": by_kind,
            "affected_count": len(seen),
            "note": "Estimate from the dependency graph; check the "
            "affected list before confirming."}


# ---------------------------------------------------------------- G39
def explain_decision(decision: str, rule: str, evidence: dict,
                     outcome: str) -> str:
    """When blocking, selecting a model, refusing a retry, or
    escalating: the actual rule, the evidence, and the reason — in
    plain words. Generalizes refusal reason codes + gateway
    classification + failover records + routing preview."""
    bits = [f"Decision: {decision}.", f"Rule applied: {rule}."]
    if evidence:
        bits.append("Evidence: " + "; ".join(
            f"{k} is {v}" for k, v in evidence.items()) + ".")
    bits.append(f"Outcome: {outcome}.")
    return " ".join(bits)


# ---------------------------------------------------------------- G40
def capability_degradation_map(features: dict[str, list[str]],
                               health: dict[str, str]) -> dict:
    """health: model/tool/provider -> 'working' / 'not_working'
    (caller-supplied self-test stamps, not re-implemented probing).
    A feature works if all its dependencies work, is impaired if some
    fail, blocked if all fail."""
    rows = {}
    for name, deps in features.items():
        states = [health.get(d, "not_working") for d in deps]
        if all(s == "working" for s in states):
            rows[name] = "works"
        elif all(s != "working" for s in states):
            rows[name] = "blocked"
        else:
            rows[name] = "impaired"
    return {"features": rows}


# ---------------------------------------------------------------- G41
CLASSIFICATION_POLICIES = {
    "public": {"storage": "any", "retrieval": "any", "sharing": "any",
               "logging": "full", "deletion": "standard"},
    "internal": {"storage": "platform_only", "retrieval": "tenant_only",
                 "sharing": "tenant_only", "logging": "full",
                 "deletion": "standard"},
    "confidential": {"storage": "platform_only", "retrieval": "tenant_only",
                     "sharing": "approved_only", "logging": "redacted",
                     "deletion": "secure"},
    "restricted": {"storage": "platform_only", "retrieval": "named_users",
                   "sharing": "never", "logging": "redacted",
                   "deletion": "secure_with_proof"},
}


def classification_decision(classification: str, operation: str) -> dict:
    """Lifecycle decision: classification connected to storage,
    retrieval, sharing, logging, deletion policies. Builds on the real
    classification field + the live gateway rule (confidential blocks
    high-risk tools)."""
    policy = CLASSIFICATION_POLICIES.get(classification)
    if policy is None:
        return {"allowed": False, "reason": "Unknown classification."}
    rule = policy.get(operation)
    if rule is None:
        return {"allowed": False, "reason": f"No policy for {operation}."}
    return {"allowed": True, "classification": classification,
            "operation": operation, "policy": rule,
            "reason": f"{classification} material: {operation} follows "
            f"the '{rule}' rule."}


# ---------------------------------------------------------------- G42
def transfer_ownership(handoff: dict, new_owner: str) -> dict:
    """Structured handoff required: current state, permissions,
    unresolved decisions, recovery instructions. A transfer without a
    complete handoff is rejected."""
    missing = [k for k in ("current_state", "permissions",
                           "unresolved_decisions", "recovery_instructions")
               if not handoff.get(k)]
    if missing:
        return {"transferred": False, "new_owner": None,
                "reason": "Transfer refused: the handoff is missing: "
                + ", ".join(missing) + "."}
    return {"transferred": True, "new_owner": new_owner,
            "handoff": dict(handoff),
            "reason": "Ownership moved with a complete handoff."}
