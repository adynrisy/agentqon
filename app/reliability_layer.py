"""AgentQon — Reliability and Intelligence Layer (gap workstream W13).

Pure, deterministic engines for the eighth audit document's 40 candidates,
organised as the program's five subsystems RESTATED (not a sixth
architecture):

  L1 User Goal and Mission Contract  -> Workflow Engine / Project Context
  L2 Planning and Agent Coordination -> Workflow Engine
  L3 Execution and Trust Controls    -> Trust and Policy Engine
  L4 Independent Verification        -> Testing and Reproducibility Lab
  L5 Verified Deliverable / Learning -> Operations Center / Artifact

Standing principle: never claim completion merely because agents say so.
Every function returns plain-words messages, never mutates its inputs, and
fails closed (unknown -> flagged / denied, never assumed fine).

Notes on adjacent material (generalised, not duplicated):
  * "mutation" elsewhere in this codebase means file writes (fs_write /
    fs_edit). Item 19 here is MUTATION TESTING: small defects planted in
    testable code to see whether the test suite catches them.
  * The critic's source-checking is adjacent to item 1, not the detector;
    the panel's evidence-based resolution is adjacent to item 7, not the
    weighting; the Proof Pack's claim/citation labels are the seed for
    item 30, generalised to passage granularity.
  * Item 23 is for USER projects; the product's own pack-gate / clean-room
    verifies AgentQon releases and is a different checker.
  * Item 39 reads estimate and provider-reported records; it never
    recomputes provider usage itself. The W1 estimator/ledger lives on
    another branch and is not merged here, so this module accepts plain
    record dicts shaped like the store usage summary and item 39's
    end-to-end W1 wiring is NOT_TESTED until integration.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy

LAYER_CONTRACTS = {
    "L1": {"name": "User Goal and Mission Contract",
           "subsystem": "Workflow Engine", "system": "Project Context",
           "inputs": "objective and constraints",
           "outputs": "a checkable contract plus per-step contribution scores",
           "failures": "vague objective, unapproved scope expansion"},
    "L2": {"name": "Planning and Agent Coordination",
           "subsystem": "Workflow Engine", "system": "Workflow Engine",
           "inputs": "plan and agent advertisements",
           "outputs": "assigned, non-redundant, escalatable work",
           "failures": "no capable agent, redundant agents, restart instead of escalate"},
    "L3": {"name": "Execution and Trust Controls",
           "subsystem": "Trust and Policy Engine", "system": "Permission",
           "inputs": "work, permissions and budgets",
           "outputs": "allowed or denied work, forecasts, gated work",
           "failures": "entitlement denied, forecast exceeded, starvation"},
    "L4": {"name": "Independent Verification",
           "subsystem": "Testing and Reproducibility Lab", "system": "Verification",
           "inputs": "claims, evidence and code",
           "outputs": "weighted verdict, reproduced artifact, killed or surviving mutants",
           "failures": "silent replacement, vote counting, uniform verification"},
    "L5": {"name": "Verified Deliverable and Learning Loop",
           "subsystem": "Operations Center", "system": "Artifact",
           "inputs": "verified results and change events",
           "outputs": "deliverable, expiry, heatmap, reconciled cost",
           "failures": "stale conclusion shipped, unreconciled cost"},
}

_STOP = set("a an the and or of to in on for with by is are was were be been "
           "it its this that from as at into over under".split())
_NEG = {"not", "no", "never", "none", "without", "cannot", "fails", "failed"}
_DRIFT_THRESHOLD = 0.15
_AGREE_THRESHOLD = 0.35
_CITATION_THRESHOLD = 0.30


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _plain(msg: str) -> str:
    return msg


# ---------------------------------------------------------------- L4 / item 1
def detect_dissonance(earlier_findings, new_conclusion):
    """A new conclusion contradicting an earlier VERIFIED finding must be
    reconciled, never silently replace it. Contradiction here is
    deterministic: shared key terms with opposite polarity (a negation
    word present on one side only), or an explicitly declared clash."""
    new_text = (new_conclusion or {}).get("statement", "")
    new_tok = _tokens(new_text)
    new_neg = bool(_NEG & set(re.findall(r"[a-z]+", new_text.lower())))
    clashes = []
    for f in earlier_findings or []:
        if not f.get("verified"):
            continue
        stmt = f.get("statement", "")
        tok = _tokens(stmt)
        neg = bool(_NEG & set(re.findall(r"[a-z]+", stmt.lower())))
        explicit = f.get("id") in ((new_conclusion or {}).get("contradicts_ids") or [])
        if explicit or (len(new_tok & tok) >= 2 and neg != new_neg):
            clashes.append(f.get("id"))
    if clashes:
        return {"conflict": True, "action": "reconcile",
                "conflicting_findings": clashes,
                "message": _plain("This new conclusion disagrees with an earlier "
                                  "checked finding, so the disagreement must be "
                                  "worked through before either one is used.")}
    return {"conflict": False, "action": "accept",
            "conflicting_findings": [],
            "message": _plain("No earlier checked finding disagrees with this conclusion.")}


# ---------------------------------------------------------------- L2 / item 2
def measure_drift(objective, steps):
    """Per-step contribution to the original objective. A step counts as
    on-track when its words overlap the objective; a low-overlap step
    that was never approved is both a detour and scope expansion."""
    obj = _tokens(objective or "")
    out = []
    for s in steps or []:
        score = round(_overlap(obj, _tokens(s.get("description", ""))), 3)
        detour = score < _DRIFT_THRESHOLD
        expansion = detour and not s.get("approved", False)
        out.append({"step_id": s.get("id"), "contribution": score,
                    "detour": detour, "scope_expansion": expansion,
                    "message": _plain("This step drifts from the original goal.")
                    if detour else _plain("This step serves the original goal.")})
    return {"steps": out,
            "detours": [s["step_id"] for s in out if s["detour"]],
            "expansions": [s["step_id"] for s in out if s["scope_expansion"]]}


# ---------------------------------------------------------------- L1 / item 3
def knowledge_boundary(question, context_text="", tools=None, evidence=None):
    """Is the question answerable reliably from what is on hand? If not,
    name the smallest additional observation: the least-covered key term."""
    have = _tokens(context_text or "")
    for t in tools or []:
        have |= _tokens(t.get("name", "") + " " + t.get("description", ""))
    for e in evidence or []:
        have |= _tokens(e.get("text", ""))
    q = _tokens(question or "")
    missing = sorted(q - have)
    if not missing:
        return {"answerable": True, "missing_terms": [],
                "smallest_observation": None,
                "message": _plain("The question can be answered from what is on hand.")}
    # Least-covered term = the missing term itself; prefer one that also
    # appears in the question's key phrases (all missing terms qualify).
    return {"answerable": False, "missing_terms": missing,
            "smallest_observation": missing[0],
            "message": _plain(f"One more observation is needed first: about "
                              f"'{missing[0]}'.")}


# ---------------------------------------------------------------- L2 / item 4
def counterfactual_analyze(plans, failures):
    """Compare alternative plans under tool failure, false assumptions and
    unavailable dependencies. Survivability = share of steps not blocked."""
    fails = set(failures or [])
    ranked = []
    for p in plans or []:
        steps = p.get("steps") or []
        blocked = [s.get("id") for s in steps
                   if (set(s.get("needs_tools") or []) & fails)
                   or (set(s.get("needs_dependencies") or []) & fails)
                   or (set(s.get("assumptions") or []) & fails)]
        surv = round(1.0 - (len(blocked) / len(steps)) if steps else 0.0, 3)
        ranked.append({"plan_id": p.get("id"), "survivability": surv,
                       "blocked_steps": blocked})
    ranked.sort(key=lambda r: (-r["survivability"], r["plan_id"] or ""))
    return {"ranking": ranked,
            "best_plan": ranked[0]["plan_id"] if ranked else None}


# ---------------------------------------------------------------- L4 / item 5
def reasoning_stability(variants):
    """Equivalent tasks under controlled wording/context variations.
    Differing conclusions across wording-only variants = wording
    instability; differing conclusions where a material fact changed =
    genuine sensitivity."""
    by_conclusion: dict[str, list] = {}
    for v in variants or []:
        by_conclusion.setdefault(v.get("conclusion", ""), []).append(v)
    conclusions = list(by_conclusion)
    unstable = len(conclusions) > 1
    if not unstable:
        return {"stable": True, "classification": "stable",
                "conclusions": conclusions,
                "message": _plain("The conclusion holds steady across variations.")}
    material = any(v.get("material_change", False) for v in variants or [])
    cls = "genuine_sensitivity" if material else "wording_instability"
    return {"stable": False, "classification": cls,
            "conclusions": conclusions,
            "message": _plain("The conclusion changes across variations: "
                              "a real difference in the facts.")
            if material else _plain("The conclusion changes with wording "
                                    "alone, so it cannot be trusted yet.")}


# ---------------------------------------------------------------- L1 / item 6
_RULES = [
    ("database", "prerequisite", "a database needs a backup and a way to move old data"),
    ("api", "compatibility", "an outside service needs sign-in details and a limit on how often it is called"),
    ("file", "output constraint", "a file result needs a stated format and size limit"),
    ("email", "operational", "sending mail needs a sender address and a way to handle bounces"),
    ("payment", "operational", "taking payment needs a receipt and a refund path"),
    ("password", "operational", "accounts need a safe way to reset a forgotten password"),
    ("schedule", "operational", "a repeating job needs a stated time zone and a failure notice"),
    ("upload", "output constraint", "an upload needs a stated file type and size limit"),
]


def discover_hidden_requirements(spec_text):
    """Implied prerequisites, compatibility needs, output constraints and
    operational requirements that the result needs in order to work."""
    text = (spec_text or "").lower()
    found = []
    for keyword, category, requirement in _RULES:
        if keyword in text:
            found.append({"category": category, "trigger": keyword,
                          "requirement": requirement})
    return {"hidden_requirements": found,
            "message": _plain(f"{len(found)} implied requirement(s) found.")}


# ---------------------------------------------------------------- L4 / item 7
def evidence_weighted_consensus(votes):
    """Formal panel weighting — NEVER a simple vote. Weight = evidence
    quality x source independence x test backing x (1 - uncertainty/2)."""
    totals: dict[str, float] = {}
    for v in votes or []:
        w = float(v.get("evidence_quality", 0.0))
        w *= 1.5 if v.get("source_independent") else 1.0
        w *= 1.25 if v.get("tests_passed") else 1.0
        w *= 1.0 - 0.5 * float(v.get("uncertainty", 0.0))
        totals[v.get("conclusion", "")] = totals.get(v.get("conclusion", ""), 0.0) + w
    if not totals:
        return {"winner": None, "weights": {}, "by_vote_count": {},
                "message": _plain("No panel votes were recorded.")}
    winner = max(sorted(totals), key=lambda c: totals[c])
    counts: dict[str, int] = {}
    for v in votes or []:
        counts[v.get("conclusion", "")] = counts.get(v.get("conclusion", ""), 0) + 1
    return {"winner": winner,
            "weights": {k: round(v, 3) for k, v in totals.items()},
            "by_vote_count": counts,
            "message": _plain(f"The weighted result is '{winner}'.")}


# ---------------------------------------------------------------- L5 / item 8
def expire_conclusions(conclusions, changes):
    """Mark a conclusion potentially stale when a supporting version,
    outside data item, dependency or assumption changes."""
    changed = set(changes or [])
    out = []
    for c in conclusions or []:
        hit = sorted(changed & set(c.get("supports") or []))
        out.append({"conclusion_id": c.get("id"), "stale": bool(hit),
                    "changed_supports": hit,
                    "message": _plain("This conclusion may be out of date.")
                    if hit else _plain("This conclusion still stands.")})
    return {"conclusions": out,
            "stale": [o["conclusion_id"] for o in out if o["stale"]]}


# ---------------------------------------------------------------- L2 / item 9
def negotiate_capabilities(agents, task):
    """Agents advertise tool schemas, context limits, output formats and
    task capabilities BEFORE assignment; only a full match is eligible."""
    req_caps = set((task or {}).get("required_capabilities") or [])
    need_ctx = int((task or {}).get("needed_context_tokens") or 0)
    need_fmt = (task or {}).get("output_format")
    out = []
    for a in agents or []:
        caps = set(a.get("capabilities") or [])
        missing = sorted(req_caps - caps)
        fmt_ok = (need_fmt is None) or (need_fmt in (a.get("output_formats") or []))
        ctx_ok = int(a.get("context_limit_tokens") or 0) >= need_ctx
        eligible = not missing and fmt_ok and ctx_ok
        out.append({"agent_id": a.get("id"), "eligible": eligible,
                    "missing_capabilities": missing,
                    "format_ok": fmt_ok, "context_ok": ctx_ok})
    return {"agents": out,
            "eligible": [o["agent_id"] for o in out if o["eligible"]]}


# ---------------------------------------------------------------- L2 / item 10
def semantic_agreement(interpretations):
    """Two models' readings of key terms checked for consistency BEFORE a
    complex mission is split. A term agrees when definitions share
    enough key words."""
    terms: dict[str, list] = {}
    for interp in interpretations or []:
        for term, definition in (interp.get("terms") or {}).items():
            terms.setdefault(term, []).append(_tokens(definition))
    out = []
    for term, defs in sorted(terms.items()):
        agree = True
        if len(defs) >= 2:
            agree = all(_overlap(defs[0], d) >= _AGREE_THRESHOLD for d in defs[1:])
        out.append({"term": term, "agree": agree,
                    "message": _plain("Both readings of this term match.")
                    if agree else _plain("The readings of this term disagree.")})
    return {"terms": out, "disagreements": [o["term"] for o in out if not o["agree"]]}


# ---------------------------------------------------------------- L4 / item 11
def independent_evidence(reviews, first_agent_source):
    """Reviewers must obtain their OWN evidence. Agreeing after only
    reading the first agent's answer is not independent evidence."""
    out = []
    for r in reviews or []:
        own = bool(r.get("evidence_source")) and r.get("evidence_source") != first_agent_source
        compliant = own
        out.append({"reviewer": r.get("reviewer"), "independent": compliant,
                    "agrees": bool(r.get("agrees_with_first")),
                    "message": _plain("This reviewer gathered their own evidence.")
                    if compliant else _plain("This reviewer only repeated the first answer.")})
    return {"reviews": out,
            "non_independent": [o["reviewer"] for o in out if not o["independent"]]}


# ---------------------------------------------------------------- L3 / item 12
_SUPERFICIAL = {"output_length", "tool_call_count", "completion_only",
                "word_count", "steps_taken"}


def detect_incentive_conflicts(evaluation_rules):
    """Flag evaluation rules that reward superficial completion, excessive
    tool use or output length over actual task success."""
    out = []
    for r in evaluation_rules or []:
        rewards = set(r.get("rewards") or [])
        bad = sorted(rewards & _SUPERFICIAL)
        checks_success = bool(r.get("checks_task_success"))
        flagged = bool(bad) and not checks_success
        out.append({"rule_id": r.get("id"), "flagged": flagged,
                    "superficial_rewards": bad,
                    "message": _plain("This rule rewards surface signs, not real success.")
                    if flagged else _plain("This rule is tied to real success.")})
    return {"rules": out, "flagged": [o["rule_id"] for o in out if o["flagged"]]}


# ---------------------------------------------------------------- L2 / item 13
def validate_transfer(expected, receiver_summary):
    """After a handoff, verify the receiver names the goal, constraints,
    current state and next action. A field passes on key-word overlap."""
    out = []
    for field in ("goal", "constraints", "current_state", "next_action"):
        exp = _tokens(str(expected.get(field, "")))
        got = _tokens(str((receiver_summary or {}).get(field, "")))
        ok = bool(exp) and _overlap(exp, got) >= _AGREE_THRESHOLD
        out.append({"field": field, "understood": ok,
                    "message": _plain(f"The receiver has the {field.replace('_', ' ')}.")
                    if ok else _plain(f"The receiver is missing the {field.replace('_', ' ')}.")})
    return {"fields": out, "valid": all(o["understood"] for o in out),
            "missing": [o["field"] for o in out if not o["understood"]]}


# ---------------------------------------------------------------- L4 / item 14
def monitor_capability_drift(baseline, current, provider_change):
    """Re-run a stable suite when the provider changes model version,
    endpoint, system behaviour or structured-output support, and compare."""
    base = {t.get("test_id"): t for t in baseline or []}
    cur = {t.get("test_id"): t for t in current or []}
    regressions = []
    for tid, b in base.items():
        c = cur.get(tid)
        if c is None:
            regressions.append(tid)
        elif b.get("passed") and not c.get("passed"):
            regressions.append(tid)
        elif b.get("output_shape") != c.get("output_shape"):
            regressions.append(tid)
    rerun = bool(provider_change) or bool(regressions)
    return {"provider_change": bool(provider_change), "rerun_required": rerun,
            "regressions": sorted(regressions),
            "message": _plain("The provider change needs a fresh check of the stable suite.")
            if provider_change else _plain("No provider change was reported.")}


# ---------------------------------------------------------------- L2 / item 15
def redundancy_optimizer(agent_outputs):
    """Do several agents add genuinely independent value, or repeat the
    same analysis? High word overlap with the same evidence = duplicate."""
    outs = agent_outputs or []
    duplicates = []
    for i in range(len(outs)):
        for j in range(i + 1, len(outs)):
            a, b = outs[i], outs[j]
            sim = _overlap(_tokens(a.get("analysis", "")), _tokens(b.get("analysis", "")))
            same_ev = bool(set(a.get("evidence_sources") or []) &
                           set(b.get("evidence_sources") or []))
            if sim >= 0.6 and same_ev:
                duplicates.append((a.get("agent_id"), b.get("agent_id")))
    return {"duplicate_pairs": [list(p) for p in duplicates],
            "independent_agents": [o.get("agent_id") for o in outs
                                   if not any(o.get("agent_id") in p for p in duplicates)],
            "message": _plain(f"{len(duplicates)} duplicate pair(s) found.")}


# ---------------------------------------------------------------- L2 / item 16
def escalation_router(mission_state):
    """Escalate ONLY the unresolved part to a specialist — never restart
    the whole workflow. Resolved parts and their evidence stay put."""
    unresolved = (mission_state or {}).get("unresolved_part") or {}
    if not unresolved:
        return {"escalate": False, "target_part": None, "restart": False,
                "message": _plain("Nothing is left unresolved, so no escalation is needed.")}
    return {"escalate": True, "target_part": unresolved.get("id"),
            "specialist_kind": unresolved.get("specialist_kind", "specialist"),
            "context": {"resolved_parts": (mission_state or {}).get("resolved_parts") or [],
                        "unresolved_description": unresolved.get("description", "")},
            "restart": False,
            "message": _plain("Only the unresolved part goes to a specialist; "
                              "the finished work stays as it is.")}


# ---------------------------------------------------------------- L4 / item 17
def architecture_check(modules, allowed_direction, approved_technologies=None):
    """Verify project rules: layering, module boundaries, dependency
    direction and approved technology choices. `allowed_direction` maps
    a layer to the layers it may depend on. Accepts a CodeGraph-style
    list of {name, layer, imports:[{module, layer, technology}]}."""
    approved = set(approved_technologies or [])
    violations = []
    for m in modules or []:
        layer = m.get("layer")
        allowed = set((allowed_direction or {}).get(layer, []))
        for imp in m.get("imports") or []:
            target_layer = imp.get("layer")
            if target_layer and target_layer not in allowed and target_layer != layer:
                violations.append({"module": m.get("name"), "import": imp.get("module"),
                                   "kind": "dependency_direction",
                                   "message": _plain(f"{m.get('name')} must not depend on the {target_layer} layer.")})
            tech = imp.get("technology")
            if approved and tech and tech not in approved:
                violations.append({"module": m.get("name"), "import": imp.get("module"),
                                   "kind": "unapproved_technology",
                                   "message": _plain(f"{tech} is not an approved technology here.")})
    return {"violations": violations, "clean": not violations}


def architecture_check_from_codegraph(modules, allowed_direction, approved_technologies=None):
    """Adapter: CodeGraph build output records import edges between
    files; callers map files to layers first (file -> layer dict passed
    as `modules` entries). This keeps one graph, not a second one."""
    return architecture_check(modules, allowed_direction, approved_technologies)


# ---------------------------------------------------------------- L4 / item 18
def behavioral_compatibility(before_cases, after_cases):
    """Compare observable behaviour before and after a change: outputs,
    edge cases, error responses and outside interfaces."""
    after = {c.get("case_id"): c for c in after_cases or []}
    diffs = []
    for b in before_cases or []:
        a = after.get(b.get("case_id"))
        if a is None:
            diffs.append({"case_id": b.get("case_id"), "kind": "missing_case"})
        elif (a.get("output") != b.get("output") or a.get("error") != b.get("error")):
            diffs.append({"case_id": b.get("case_id"), "kind": "behavior_changed",
                          "before": b.get("output"), "after": a.get("output")})
    for cid in after:
        if cid not in {c.get("case_id") for c in before_cases or []}:
            diffs.append({"case_id": cid, "kind": "new_case"})
    return {"differences": diffs, "compatible": not any(d["kind"] == "behavior_changed"
                                                        for d in diffs)}


# ---------------------------------------------------------------- L4 / item 19
_MUTATIONS = [
    ("+", "-", "arithmetic operator swapped"),
    (">", "<", "comparison flipped"),
    ("==", "!=", "equality flipped"),
    ("True", "False", "true value flipped"),
    (" and ", " or ", "and changed to or"),
]


def generate_mutants(source):
    """Plant one small defect at a time. NOTE: this is mutation TESTING;
    the codebase's other use of 'mutation' (file writes) is unrelated."""
    mutants = []
    for old, new, desc in _MUTATIONS:
        if old in source:
            mutants.append({"description": desc,
                            "mutated_source": source.replace(old, new, 1)})
    return mutants


def run_mutation_testing(source, kill_check):
    """Score mutants against a caller-supplied kill check: kill_check
    returns True when the test suite catches the mutant. Surviving
    mutants reveal weak tests. Running a real suite per mutant is an
    integration step, not done inside this pure engine."""
    mutants = generate_mutants(source)
    killed, surviving = [], []
    for m in mutants:
        (killed if kill_check(m["mutated_source"]) else surviving).append(m["description"])
    total = len(mutants)
    return {"total": total, "killed": killed, "surviving": surviving,
            "score": round(len(killed) / total, 3) if total else 1.0,
            "message": _plain(f"{len(surviving)} mutant(s) survived — those tests are weak.")
            if surviving else _plain("Every planted defect was caught.")}


# ---------------------------------------------------------------- L4 / item 20
def minimal_fix_finder(patches):
    """Favour the smallest change that satisfies the requirements and
    passes the relevant tests. Non-passing or non-satisfying patches
    are never chosen, however small."""
    good = [p for p in patches or []
            if p.get("satisfies_requirements") and p.get("tests_pass")]
    if not good:
        return {"chosen": None, "message": _plain("No patch both meets the requirements and passes its tests.")}
    good.sort(key=lambda p: (int(p.get("lines_changed") or 0),
                             int(p.get("files_changed") or 0), p.get("id") or ""))
    return {"chosen": good[0].get("id"),
            "message": _plain(f"The smallest working patch is '{good[0].get('id')}'.")}


# ---------------------------------------------------------------- L4 / item 21
def coupling_analyzer(change_history, threshold=2):
    """Modules that frequently need coordinated edits. A pair changed
    together at least `threshold` times is tightly coupled."""
    pairs: dict[tuple, int] = {}
    for change in change_history or []:
        mods = sorted(set(change.get("modules") or []))
        for i in range(len(mods)):
            for j in range(i + 1, len(mods)):
                key = (mods[i], mods[j])
                pairs[key] = pairs.get(key, 0) + 1
    coupled = [{"modules": list(k), "joint_changes": v}
               for k, v in sorted(pairs.items()) if v >= threshold]
    return {"coupled_pairs": coupled,
            "risky": [c for c in coupled],
            "message": _plain(f"{len(coupled)} tightly coupled pair(s) found.")}


def flag_risky_change(changed_modules, coupled_pairs):
    """A change spanning a tightly coupled pair is flagged risky."""
    changed = set(changed_modules or [])
    hit = [c for c in coupled_pairs or [] if set(c.get("modules") or []) <= changed]
    return {"risky": bool(hit), "coupled_pairs_spanned": hit,
            "message": _plain("This change spans tightly coupled parts, so it needs extra care.")
            if hit else _plain("This change stays within loosely linked parts.")}


# ---------------------------------------------------------------- L4 / item 22
def dead_end_detector(features):
    """A feature complete in the interface but lacking a connected
    backend, stored data, real event handling or end-to-end behaviour
    is a dead end — for USER projects."""
    out = []
    for f in features or []:
        missing = [k for k in ("has_backend", "has_storage", "has_events", "end_to_end")
                   if not f.get(k)]
        out.append({"feature": f.get("name"), "dead_end": bool(missing),
                    "missing": missing,
                    "message": _plain("This feature looks finished but does nothing behind the screen.")
                    if missing else _plain("This feature works end to end.")})
    return {"features": out, "dead_ends": [o["feature"] for o in out if o["dead_end"]]}


# ---------------------------------------------------------------- L4 / item 23
_TIMESTAMP_RE = re.compile(r"(built_at|timestamp|generated_at)\s*[:=]\s*\S+", re.I)


def normalize_artifact(content):
    """Normalised contents for comparison: timestamp lines removed,
    trailing space trimmed. Hash comparison alone would flag every
    rebuild; normalisation isolates real differences."""
    lines = []
    for line in (content or "").splitlines():
        if _TIMESTAMP_RE.search(line):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def reproducibility_check(first_content, second_content):
    """Rebuild under controlled conditions and compare hashes or
    normalised contents. Distinct from the product's own pack-gate /
    clean-room, which verify AgentQon releases."""
    raw_same = hashlib.sha256((first_content or "").encode()).hexdigest() == \
        hashlib.sha256((second_content or "").encode()).hexdigest()
    norm_same = normalize_artifact(first_content) == normalize_artifact(second_content)
    reproducible = raw_same or norm_same
    return {"reproducible": reproducible, "identical": raw_same,
            "identical_after_normalisation": norm_same,
            "message": _plain("The rebuild matches.")
            if reproducible else _plain("The rebuild differs in a real way.")}


# ---------------------------------------------------------------- L5 / item 24
def ownership_map(files):
    """Generated files point back to the source definition that must
    change, so a fix is never overwritten by the next generation run."""
    out = []
    for f in files or []:
        if f.get("generated"):
            out.append({"file": f.get("path"), "kind": "generated",
                        "change_source": f.get("generator_source"),
                        "message": _plain(f"Change '{f.get('generator_source')}', not this file.")})
        else:
            out.append({"file": f.get("path"), "kind": "manual",
                        "change_source": f.get("path"),
                        "message": _plain("This file is maintained by hand.")})
    return {"files": out}


# ---------------------------------------------------------------- L4 / item 25
def source_independence(sources):
    """Several sources repeating the same original claim never count as
    independent confirmation. Independence counts distinct originals."""
    by_original: dict[str, list] = {}
    for s in sources or []:
        by_original.setdefault(s.get("original_source") or s.get("id"), []).append(s.get("id"))
    duplicates = {k: v for k, v in by_original.items() if len(v) > 1}
    return {"independent_count": len(by_original),
            "duplicated_reporting": duplicates,
            "message": _plain(f"{len(by_original)} independent source(s) back this claim.")}


# ---------------------------------------------------------------- L5 / item 26
def fact_dependency_graph(facts, conclusions):
    """Record which conclusions depend on which facts; a changed fact
    lists every conclusion needing a fresh look."""
    graph = {c.get("id"): list(c.get("depends_on") or []) for c in conclusions or []}
    return {"graph": graph}


def impacted_by_fact_change(conclusions, changed_fact_id):
    hit = [c.get("id") for c in conclusions or []
           if changed_fact_id in (c.get("depends_on") or [])]
    return {"changed_fact": changed_fact_id, "impacted_conclusions": hit,
            "message": _plain(f"{len(hit)} conclusion(s) need a fresh look.")}


# ---------------------------------------------------------------- L4 / item 27
def timestamp_resolver(records):
    """Keep publication time, event time, retrieval time and
    last-modified time distinct when reconciling changing information."""
    out = []
    for r in records or []:
        event = r.get("event_time")
        pub = r.get("publication_time")
        conflict = bool(event and pub and event > pub)
        out.append({"record_id": r.get("id"),
                    "event_time": event, "publication_time": pub,
                    "retrieval_time": r.get("retrieval_time"),
                    "last_modified_time": r.get("last_modified_time"),
                    "timeline_key": event or pub,
                    "time_conflict": conflict,
                    "message": _plain("This record claims an event after its own publication date.")
                    if conflict else _plain("This record's times line up.")})
    out.sort(key=lambda o: (o["timeline_key"] is None, o["timeline_key"] or ""))
    return {"records": out, "conflicts": [o["record_id"] for o in out if o["time_conflict"]]}


# ---------------------------------------------------------------- L5 / item 28
def knowledge_gap_heatmap(requirements, evidence):
    """Grade each mission requirement: strong, weak, none, or
    conflicting evidence."""
    by_req: dict[str, list] = {}
    for e in evidence or []:
        by_req.setdefault(e.get("requirement_id"), []).append(e)
    out = []
    for req in requirements or []:
        evs = by_req.get(req.get("id"), [])
        if any(e.get("conflicting") for e in evs):
            grade = "conflicting"
        elif any(e.get("strength") == "strong" for e in evs):
            grade = "strong"
        elif evs:
            grade = "weak"
        else:
            grade = "none"
        out.append({"requirement_id": req.get("id"), "grade": grade})
    return {"heatmap": out,
            "summary": {g: sum(1 for o in out if o["grade"] == g)
                        for g in ("strong", "weak", "none", "conflicting")}}


# ---------------------------------------------------------------- L3 / item 29
def entitlement_check(user_permissions, user_tenant, document):
    """Verify, BEFORE retrieving or summarising, that the agent may
    access this document under the current user's permissions. This
    generalises tenant scoping and the Write Law to per-document
    entitlement. No document content ever passes through this check."""
    required = document.get("required_permission")
    allowed = (required in (user_permissions or [])
               and document.get("tenant_id") == user_tenant)
    return {"allowed": allowed, "document_id": document.get("id"),
            "message": _plain("Access is allowed.")
            if allowed else _plain("Access is denied — check this before reading anything.")}


# ---------------------------------------------------------------- L4 / item 30
def citation_granularity(claims, passages):
    """Each important claim must be backed by the SPECIFIC cited
    passage, not merely a document on the same topic. Generalises the
    Proof Pack's claim/citation labels to passage granularity."""
    by_id = {p.get("id"): p.get("text", "") for p in passages or []}
    out = []
    for c in claims or []:
        pid = c.get("citation_passage_id")
        passage = by_id.get(pid, "")
        score = _overlap(_tokens(c.get("text", "")), _tokens(passage))
        ok = bool(pid) and score >= _CITATION_THRESHOLD
        out.append({"claim_id": c.get("id"), "supported": ok,
                    "passage_overlap": round(score, 3),
                    "message": _plain("The cited passage itself backs this claim.")
                    if ok else _plain("The citation points at a topic, not at the passage that proves this claim.")})
    return {"claims": out, "unsupported": [o["claim_id"] for o in out if not o["supported"]]}


# ---------------------------------------------------------------- L5 / item 31
DEFAULT_FRESHNESS_BUDGETS = {
    "api_documentation": 90, "software_version": 30, "pricing": 30,
    "historical_background": 3650, "news": 7, "general": 365,
}


def freshness_budget(items, budgets=None):
    """Different acceptable freshness periods per information kind."""
    table = dict(DEFAULT_FRESHNESS_BUDGETS)
    table.update(budgets or {})
    out = []
    for it in items or []:
        limit = table.get(it.get("kind"), table["general"])
        stale = float(it.get("age_days") or 0) > limit
        out.append({"item_id": it.get("id"), "kind": it.get("kind"),
                    "budget_days": limit, "stale": stale,
                    "message": _plain("This information is past its freshness budget.")
                    if stale else _plain("This information is fresh enough.")})
    return {"items": out, "stale": [o["item_id"] for o in out if o["stale"]]}


# ---------------------------------------------------------------- L3 / item 32
def dispute_workflow(disagreements):
    """When trusted sources disagree: preserve BOTH claims, name the
    exact disagreement, and route the unresolved issue for
    investigation. The verdict resolves today; this preserves + routes
    instead of picking a winner."""
    out = []
    for d in disagreements or []:
        a, b = d.get("claim_a") or {}, d.get("claim_b") or {}
        differing = sorted(set(a) & set(b) and [k for k in a if k in b and a[k] != b[k]])
        out.append({"dispute_id": d.get("id"), "status": "open_for_investigation",
                    "preserved_claims": [deepcopy(a), deepcopy(b)],
                    "exact_disagreement": differing,
                    "resolved_to": None,
                    "message": _plain("Both claims are kept and the disagreement is sent for investigation.")})
    return {"disputes": out, "open": [o["dispute_id"] for o in out]}


# ---------------------------------------------------------------- L2 / item 33
def workload_shape(profile):
    """Classify a task to choose its execution strategy."""
    p = profile or {}
    if p.get("deadline_seconds") is not None and p["deadline_seconds"] <= 30:
        shape = "latency_sensitive"
    elif (p.get("verification_steps") or 0) >= 5 and \
            (p.get("verification_steps") or 0) >= (p.get("total_steps") or 1) // 2:
        shape = "verification_heavy"
    elif (p.get("memory_mb") or 0) >= 4096:
        shape = "memory_bound"
    elif (p.get("tool_calls") or 0) >= 50:
        shape = "tool_bound"
    elif (p.get("items") or 0) >= 1000:
        shape = "throughput_oriented"
    else:
        shape = "throughput_oriented"
    return {"shape": shape,
            "strategy": {"latency_sensitive": "run first, smallest crew, no batching",
                         "verification_heavy": "extra checking steps, slower but safer",
                         "memory_bound": "one heavy job at a time",
                         "tool_bound": "batch tool calls, cache results",
                         "throughput_oriented": "split into parallel batches"}[shape]}


# ---------------------------------------------------------------- L5 / item 34
def context_utility_profiler(trials):
    """Which context categories actually improve outcomes, and which
    expensive context adds little value."""
    by_cat: dict[str, list] = {}
    for t in trials or []:
        by_cat.setdefault(t.get("category"), []).append(t)
    out = []
    for cat, ts in sorted(by_cat.items()):
        avg_tokens = sum(float(t.get("tokens") or 0) for t in ts) / len(ts)
        avg_gain = sum(float(t.get("outcome_gain") or 0) for t in ts) / len(ts)
        low_value = avg_tokens >= 2000 and avg_gain <= 0.05
        out.append({"category": cat, "avg_tokens": round(avg_tokens, 1),
                    "avg_outcome_gain": round(avg_gain, 3),
                    "expensive_low_value": low_value})
    return {"categories": out,
            "expensive_low_value": [o["category"] for o in out if o["expensive_low_value"]]}


# ---------------------------------------------------------------- L2 / item 35
def breakeven_estimator(single_time, agent_count, per_agent_time,
                        coordination_overhead, duplicated_context_cost, merge_cost):
    """Splitting saves time only after coordination, duplicated context
    and merge overhead are paid."""
    split_total = (per_agent_time + coordination_overhead
                   + duplicated_context_cost + merge_cost)
    saved = single_time - split_total
    return {"single_time": single_time, "split_total": round(split_total, 3),
            "time_saved": round(saved, 3), "worth_splitting": saved > 0,
            "agent_count": agent_count,
            "message": _plain("Splitting saves time.")
            if saved > 0 else _plain("Splitting costs more time than it saves.")}


# ---------------------------------------------------------------- L3 / item 36
def memory_pressure_forecast(available_mb, queued_loads_mb, concurrent_missions):
    """Predict BEFORE starting whether queued model loads plus
    concurrent missions exceed available RAM or shared memory."""
    needed = sum(float(x) for x in queued_loads_mb or [])
    for m in concurrent_missions or []:
        needed += float(m.get("memory_mb") or 0)
    headroom = float(available_mb) - needed
    return {"available_mb": float(available_mb), "needed_mb": needed,
            "headroom_mb": headroom, "would_exceed": headroom < 0,
            "message": _plain("Starting this would run past available memory.")
            if headroom < 0 else _plain("Memory is enough for this plan.")}


# ---------------------------------------------------------------- L3 / item 37
def starvation_detector(task_delay_events, threshold=3):
    """Tasks repeatedly delayed by scheduling, memory, provider limits
    or low priority are starving."""
    by_task: dict[str, list] = {}
    for e in task_delay_events or []:
        by_task.setdefault(e.get("task_id"), []).append(e)
    out = []
    for tid, evs in sorted(by_task.items()):
        causes = sorted({e.get("cause") for e in evs})
        starving = len(evs) >= threshold
        out.append({"task_id": tid, "delay_count": len(evs), "causes": causes,
                    "starving": starving,
                    "message": _plain("This task keeps getting pushed back.")
                    if starving else _plain("This task's delays are within bounds.")})
    return {"tasks": out, "starving": [o["task_id"] for o in out if o["starving"]]}


# ---------------------------------------------------------------- L4 / item 38
def adaptive_verification(risk, checks):
    """Deeper verification for high-risk changes, lighter checks for
    low-risk tasks — while MANDATORY safety and correctness checks are
    always preserved. Verification today is uniform; this is the fix."""
    mandatory = [c.get("id") for c in checks or [] if c.get("mandatory")]
    if risk == "high":
        chosen = [c.get("id") for c in checks or []]
    elif risk == "low":
        chosen = mandatory[:]
    else:
        chosen = [c.get("id") for c in checks or []
                  if c.get("mandatory") or c.get("risk_level") in ("medium", "high")]
    # Mandatory checks are always present, whatever the risk tier.
    for m in mandatory:
        if m not in chosen:
            chosen.append(m)
    return {"risk": risk, "checks_run": chosen, "mandatory_preserved": True,
            "message": _plain("Safety and correctness checks always run.")}


# ---------------------------------------------------------------- L5 / item 39
def cost_reconciliation(estimate_records, provider_records):
    """Reconcile AgentQon usage estimates against provider-reported
    usage and invoices where those records are available. This reads
    estimate and provider records — it never recomputes provider usage
    itself, so there is no second reconciliation computation."""
    est = {r.get("id"): float(r.get("estimated_cost") or 0) for r in estimate_records or []}
    prov = {r.get("id"): r for r in provider_records or []}
    out = []
    for rid, e in sorted(est.items()):
        p = prov.get(rid)
        reported = float(p.get("reported_cost")) if p and p.get("reported_cost") is not None else None
        variance = round(reported - e, 4) if reported is not None else None
        out.append({"id": rid, "estimated": e, "reported": reported,
                    "variance": variance,
                    "invoice_available": bool(p and p.get("invoice_available")),
                    "unmatched": reported is None})
    return {"reconciliation": out,
            "total_estimated": round(sum(est.values()), 4),
            "total_reported": round(sum(float(p.get("reported_cost") or 0)
                                        for p in prov.values()
                                        if p.get("reported_cost") is not None), 4),
            "unmatched": [o["id"] for o in out if o["unmatched"]]}


# ---------------------------------------------------------------- L4 / item 40
def efficiency_gate(baseline, current, justified_quality_gain=False, justification=""):
    """Block or flag a change causing a material rise in time, memory,
    tool calls or cost without a justified quality improvement."""
    threshold = 0.20
    rises = {}
    for metric in ("time_seconds", "memory_mb", "tool_calls", "cost_usd"):
        b, c = float((baseline or {}).get(metric) or 0), float((current or {}).get(metric) or 0)
        rises[metric] = round((c - b) / b, 3) if b else (0.0 if c == 0 else 1.0)
    material = {k: v for k, v in rises.items() if v > threshold}
    blocked = bool(material) and not (justified_quality_gain and justification)
    return {"rises": rises, "material_rises": material, "blocked": blocked,
            "verdict": "blocked" if blocked else "pass",
            "message": _plain("This change costs materially more without a justified gain.")
            if blocked else _plain("This change passes the efficiency check.")}
