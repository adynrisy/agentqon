"""AgentQon — skill retrieval pipeline (W4 §4, gap-memory).

Replaces the fixed top-3 with the target behavior:

  rank -> remove incompatible -> remove near-duplicates ->
  select the strongest few (count from the task, never fixed) ->
  estimate combined context cost -> load detail only when needed
  -> re-retrieve if execution reveals a genuine gap.

Order of authority, in plain words: a tenant pin is the user's
explicit standing instruction, so pins lead; an exact name match
comes next; then score. Ties break deterministically (score,
coverage, skill id) so the same task always returns the same
list (R69).

Rules that keep the pipeline honest:

- Minimum relevance (R71): the existing floors (bm25 score and
  term coverage, measured on the ORIGINAL task terms) still
  gate. Synonym expansion (R64) only widens candidate recall;
  it never lowers the gate, so an unrelated task still returns
  nothing instead of a filled list of unrelated skills.
- Incompatible skills are REMOVED with a recorded reason, not
  silently demoted: missing dependencies, dependency cycles,
  failed compatibility, and deprecated skills (unless the
  caller explicitly asks for them) all appear in "removed".
- Confidence (R70) is an estimate from score + coverage,
  labelled as an estimate — never a measured probability.
- Caching (R77) is keyed by the library fingerprint plus the
  normalized query plus the tenant's pins, so a catalog change
  or a pin change is a cache miss, never a stale hit.
- Detail (stages 3-4) is never loaded by this pipeline:
  load_detail() from app/skill_lifecycle.py is the only path
  to reference materials and scripts/examples, and only when
  the caller asks for it.
"""
from __future__ import annotations

import re

from .skills import (_CANDIDATE_LIMIT, _stem, _terms, _term_coverage,
                     INJECT_BUDGET_CHARS, MIN_SKILL_SCORE,
                     MIN_TERM_COVERAGE, DEFAULT_TOP_K)
from .skill_lifecycle import (check_compatibility, check_dependencies,
                              detect_dependency_cycles, estimate_size,
                              normalize_skill_name)

# Narrow, generic synonym map for recall expansion (R64).
# Deliberately small: synonyms widen the candidate pool only;
# the relevance floors still run on the original terms.
SYNONYMS = {
    "test": ["testing"], "testing": ["test"],
    "debug": ["debugging"], "debugging": ["debug"],
    "fix": ["repair", "patch"], "repair": ["fix"],
    "review": ["audit", "check"], "check": ["review"],
    "write": ["author", "create"], "create": ["write", "make"],
    "deploy": ["release", "publish"], "release": ["deploy"],
    "secure": ["security"], "security": ["secure"],
    "speed": ["performance"], "performance": ["speed"],
}

_CACHE: dict[tuple, dict] = {}
_CACHE_MAX = 128

# Module-level use telemetry (R78): retrieval events and use
# events, keyed by tenant. A selected skill counts as "used"
# only when mark_skill_used() is called for it — selection
# alone is retrieval, not use.
_TELEMETRY: dict[str, dict] = {}


def _telemetry_for(tenant_id: str) -> dict:
    return _TELEMETRY.setdefault(
        tenant_id, {"retrieved": {}, "used": {}, "events": []})


def _expand_terms(terms: list[str]) -> list[str]:
    out = list(terms)
    for term in terms:
        for syn in SYNONYMS.get(term, []):
            if syn not in out:
                out.append(syn)
    return out


def _jaccard_tokens(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _body_tokens(skill: dict) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]*",
                          (f"{skill.get('name', '')} "
                           f"{skill.get('description', '')} "
                           f"{skill.get('body', '')}").lower()))


def retrieval_confidence(score: float, coverage: float) -> dict:
    """Retrieval confidence estimation (R70, TOP-15 #8).
    Combines the bm25 score (squashed to 0..1) with term
    coverage. The result is labelled an estimate in every
    rendering — it is not a measured probability."""
    score_norm = score / (score + 4.0) if score > 0 else 0.0
    value = round(0.6 * score_norm + 0.4 * coverage, 4)
    return {"confidence": value,
            "note": "Estimate from match strength, not a measured "
                    "probability."}


def retrieve_dynamic(store, tenant_id: str, text: str, settings, *,
                     k: int = DEFAULT_TOP_K,
                     min_score: float = MIN_SKILL_SCORE,
                     min_coverage: float = MIN_TERM_COVERAGE,
                     library=None,
                     category: str | None = None,
                     harness_version: str = "22.5.0",
                     model_capabilities: list[str] | None = None,
                     include_deprecated: bool = False,
                     registry=None) -> dict:
    """The §4 pipeline. Returns a dict with "skills" (the
    selected skill dicts, each carrying score, coverage,
    pinned, exact_match, confidence, estimated_chars),
    "removed" (each rejected candidate with a plain-words
    reason), "cost_estimate" (combined characters + budget),
    "effective_k" (how many the task asked for — never a fixed
    number), and "cache_hit"."""
    from .skills import get_library
    lib = library or get_library(settings)
    terms = _terms(text)
    if not terms or not lib.available():
        return {"skills": [], "removed": [], "cost_estimate": {
            "total_chars": 0, "budget_chars": INJECT_BUDGET_CHARS},
            "effective_k": 0, "cache_hit": False}
    try:
        pins = store.pinned_skill_ids(tenant_id)
    except Exception:
        pins = set()
    # The cache key must also capture registry state that
    # changes selection (deprecation), or a cached pre-
    # deprecation result would be served after deprecating.
    dep_key = (tuple(sorted(registry._deprecated))
               if registry is not None else ())
    cache_key = (lib.fingerprint(), " ".join(terms), tuple(sorted(pins)),
                 k, min_score, min_coverage, category or "",
                 harness_version, tuple(sorted(model_capabilities or [])),
                 include_deprecated, dep_key)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        result = {**cached, "cache_hit": True,
                  "skills": [dict(s) for s in cached["skills"]]}
        _record_retrieval(tenant_id, result["skills"], text)
        return result
    # Rank: FTS over original terms + synonyms (recall only).
    expanded = _expand_terms(terms)
    candidates, _ = lib.search(" ".join(expanded),
                               limit=_CANDIDATE_LIMIT)
    normalized_query = " ".join(terms)
    removed: list[dict] = []
    survivors = []
    seen_ids: set[str] = set()
    available_ids = {m["id"] for m in lib._load_metas()}
    dep_graph: dict[str, list[str]] = {}
    for cand in candidates:
        full = lib.get(cand["id"])
        if full is None:
            continue
        dep_graph[cand["id"]] = list(full.get("dependencies") or [])
    cycles = detect_dependency_cycles(dep_graph)
    cyclic_ids = {node for cycle in cycles for node in cycle}
    for cand in candidates:
        full = lib.get(cand["id"])
        if full is None:
            continue
        coverage = _term_coverage(terms, full)
        pinned = cand["id"] in pins
        # Exact-match prioritization (R63): every hyphen-part
        # of the normalized skill name appears among the
        # task's key terms (stem-compared), or the normalized
        # name equals the normalized query phrase.
        _name_norm = normalize_skill_name(full.get("name", ""))
        _stemmed_terms = {_stem(t) for t in terms}
        exact = bool(_name_norm) and (
            _name_norm == normalized_query.replace(" ", "-")
            or all(_stem(part) in _stemmed_terms
                   for part in _name_norm.split("-") if part))
        # Category filtering (R65): only when the caller asks
        # and the skill declares a category.
        if category and full.get("category"):
            if normalize_skill_name(full["category"]) != \
                    normalize_skill_name(category):
                removed.append({"skill_id": cand["id"],
                                "reason": "Not in the requested category."})
                continue
        # Deprecation (A7): deprecated skills are removed
        # unless explicitly requested, with the replacement named.
        if registry is not None and registry.deprecation_notice(full) \
                and not include_deprecated:
            removed.append({"skill_id": cand["id"],
                            "reason": registry.deprecation_notice(full)})
            continue
        # Dependency availability (R67) + cycle removal (R75).
        dep_check = check_dependencies(full, available_ids)
        if not dep_check["satisfied"]:
            removed.append({"skill_id": cand["id"],
                            "reason": "Missing dependencies: "
                            + ", ".join(dep_check["missing"]) + "."})
            continue
        if cand["id"] in cyclic_ids:
            removed.append({"skill_id": cand["id"],
                            "reason": "Its dependencies form a cycle."})
            continue
        compat = check_compatibility(full, harness_version,
                                     model_capabilities)
        if not compat["compatible"]:
            removed.append({"skill_id": cand["id"],
                            "reason": "; ".join(compat["problems"])})
            continue
        if pinned or (cand["score"] >= min_score
                      and coverage >= min_coverage):
            full["score"] = cand["score"]
            full["coverage"] = round(coverage, 4)
            full["pinned"] = pinned
            full["exact_match"] = bool(exact)
            conf = retrieval_confidence(cand["score"], coverage)
            full["confidence"] = conf["confidence"]
            full["confidence_note"] = conf["note"]
            full["estimated_chars"] = estimate_size(full)
            survivors.append(full)
    # Near-duplicate suppression (R68): same normalized name or
    # body overlap >= 0.85 — keep the higher-ranked one.
    survivors.sort(key=lambda c: (not c["pinned"], not c["exact_match"],
                                  -c["score"], -c["coverage"], c["id"]))
    deduped: list[dict] = []
    for cand in survivors:
        dup = False
        for kept in deduped:
            same_name = (normalize_skill_name(cand.get("name", ""))
                         and normalize_skill_name(cand.get("name", ""))
                         == normalize_skill_name(kept.get("name", "")))
            overlap = _jaccard_tokens(_body_tokens(cand),
                                      _body_tokens(kept))
            if same_name or overlap >= 0.85:
                removed.append({"skill_id": cand["id"],
                                "reason": "Near-duplicate of "
                                + kept["id"] + "; kept the stronger one."})
                dup = True
                break
        if not dup:
            deduped.append(cand)
    # Dynamic selection (the fixed top-3 is gone): the count
    # follows the task — one slot per distinct key term, at
    # least 2 when the cap allows — capped by the caller's k,
    # which always wins (a cap of 1 selects exactly one).
    # The combined context cost must fit the injection budget
    # (the first skill is always kept, even if it alone is large).
    if k <= 0:
        effective_k = 0
    elif k < 2:
        effective_k = k
    else:
        effective_k = min(k, max(2, len(terms)))
    selected: list[dict] = []
    total_chars = 0
    for cand in deduped:
        if len(selected) >= effective_k:
            break
        if selected and total_chars + cand["estimated_chars"] \
                > INJECT_BUDGET_CHARS:
            removed.append({"skill_id": cand["id"],
                            "reason": "No room left in the context budget."})
            continue
        selected.append(cand)
        total_chars += cand["estimated_chars"]
    result = {"skills": selected, "removed": removed,
              "cost_estimate": {"total_chars": total_chars,
                                "budget_chars": INJECT_BUDGET_CHARS,
                                "skills": len(selected)},
              "effective_k": effective_k, "cache_hit": False}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[cache_key] = {**result,
                         "skills": [dict(s) for s in selected]}
    _record_retrieval(tenant_id, selected, text)
    return result


def _record_retrieval(tenant_id: str, skills: list[dict],
                      task: str) -> None:
    tel = _telemetry_for(tenant_id)
    for skill in skills:
        sid = skill["id"]
        tel["retrieved"][sid] = tel["retrieved"].get(sid, 0) + 1
    tel["events"].append({"kind": "retrieval",
                          "skill_ids": [s["id"] for s in skills],
                          "task": task[:200]})


def mark_skill_used(tenant_id: str, skill_id: str,
                    mission_id: str | None = None) -> None:
    """Skill-use telemetry (R78): record that a retrieved skill
    was actually used. Retrieval without this call is not use."""
    tel = _telemetry_for(tenant_id)
    tel["used"][skill_id] = tel["used"].get(skill_id, 0) + 1
    tel["events"].append({"kind": "use", "skill_id": skill_id,
                          "mission_id": mission_id})


def was_skill_used(tenant_id: str, skill_id: str) -> bool:
    return _telemetry_for(tenant_id)["used"].get(skill_id, 0) > 0


def telemetry_for(tenant_id: str) -> dict:
    tel = _telemetry_for(tenant_id)
    return {"retrieved": dict(tel["retrieved"]),
            "used": dict(tel["used"]),
            "events": [dict(e) for e in tel["events"]]}


def clear_telemetry(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _TELEMETRY.clear()
        _CACHE.clear()
    else:
        _TELEMETRY.pop(tenant_id, None)


def retrieve_more(store, tenant_id: str, text: str, settings, *,
                  already_loaded: list[str], library=None,
                  **kwargs) -> dict:
    """Re-retrieval on a genuine execution gap: run the pipeline
    for the gap text, excluding skills already loaded, so
    execution can fetch a further skill without re-injecting
    what it already has."""
    result = retrieve_dynamic(store, tenant_id, text, settings,
                              library=library, **kwargs)
    loaded = set(already_loaded)
    result["skills"] = [s for s in result["skills"]
                        if s["id"] not in loaded]
    result["cost_estimate"]["total_chars"] = sum(
        s["estimated_chars"] for s in result["skills"])
    result["cost_estimate"]["skills"] = len(result["skills"])
    return result
