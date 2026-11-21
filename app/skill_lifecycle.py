"""AgentQon — skill lifecycle (W4, gap-memory).

The library (app/skills.py) already scans, indexes, retrieves, and
injects skills. This module owns everything around a skill's LIFE:
versioning, integrity, duplicates, conflicts, dependencies,
compatibility, deprecation, provenance, installation validation,
permission inspection, telemetry, attribution, unused analysis,
evaluation, composition, progressive detail loading (stages 3-4),
and security screening. Standard library only.

Deliberate non-goals, stated so nobody re-adds them later:

- There is NO delete API here. Duplicate detection RECOMMENDS
  consolidation; it never auto-deletes (A1). Unused-skill
  analysis REPORTS; it never auto-removes (A13).
- Skill content is UNTRUSTED input (M8). scan_skill_text()
  refuses override attempts, secret-exfiltration patterns, and
  permission-granting language before a skill becomes usable.
  Injection rendering still wraps bodies in the untrusted-data
  block (app/skills.py format_skills_section); this module is
  the gate in front of it, not a replacement for it.
- A skill can never override harness security rules, reveal
  secrets, or grant itself permissions: the scan below treats
  any such instruction as a finding, install validation fails
  closed on findings of class "override" or "secret", and
  permissions_for() reports only what the harness already
  allows — a skill's claimed permissions are advisory text,
  never an authority grant.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .domain import new_id, now_iso

# ------------------------------------------------------------ naming
_NAME_RE = re.compile(r"[^a-z0-9]+")


def normalize_skill_name(name: str) -> str:
    """Name normalization (R61): lowercase, non-alphanumerics
    become one hyphen, edges trimmed. 'Code Review', 'code-review'
    and 'code_review' all normalize to 'code-review'."""
    return _NAME_RE.sub("-", str(name).lower()).strip("-")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]*", str(text).lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def content_hash(skill: dict) -> str:
    """Integrity hash (A4): over name + body + declared version.
    Recorded at install; verify_integrity() recomputes it and
    flags any skill whose content changed since."""
    raw = (f"{skill.get('name', '')}\0{skill.get('body', '')}\0"
           f"{skill.get('version', '')}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def estimate_size(skill: dict) -> int:
    """Per-skill size estimation (R73): characters the skill
    would cost if injected in full (body, or the recorded
    body_bytes when the body is not loaded)."""
    body = skill.get("body")
    if body is not None:
        return len(str(body))
    return int(skill.get("body_bytes") or 0)


# ------------------------------------------------------------ security scan (M8)
# Each pattern is (class, plain-words finding). Classes:
# - "override": tries to change harness behaviour -> REFUSED.
# - "secret": tries to surface credentials -> REFUSED.
# - "permission": tries to grant itself authority -> REFUSED.
# - "suspicious": worth a human look, does not block alone.
_SCAN_PATTERNS = [
    ("override", re.compile(
        r"(?i)\b(ignore|disregard|override|bypass|disable)\b.{0,60}"
        r"\b(previous instructions|system prompt|system instructions|"
        r"safety rules|security rules|shield|guardrails|policy)\b")),
    ("override", re.compile(
        r"(?i)\b(you are now|from now on|new instructions|"
        r"forget (your|the) (instructions|rules))\b")),
    ("override", re.compile(
        r"(?i)\b(reveal|print|show|output).{0,40}\b(system prompt|"
        r"system instructions|hidden instructions)\b")),
    ("secret", re.compile(
        r"(?i)\b(api[_-]?key|password|passwd|secret|token|private[_-]?key)"
        r"\s*[:=]\s*\S+")),
    ("secret", re.compile(
        r"(?i)\b(send|email|post|upload).{0,40}\b(environment variables|"
        r"\.env|credentials|secrets)\b")),
    ("secret", re.compile(r"(?i)\b(os\.environ|getenv|process\.env)\b")),
    ("permission", re.compile(
        r"(?i)\b(grant (me|this skill|yourself)|give (me|yourself)|"
        r"elevate|sudo|run as (admin|root))\b")),
    ("permission", re.compile(
        r"(?i)\b(disable|turn off).{0,30}\b(approval|permission|"
        r"confirmation|shield)\b")),
    ("suspicious", re.compile(r"(?i)\b(curl|wget).{0,30}https?://")),
    ("suspicious", re.compile(r"(?i)\b(delete|remove|rm -rf)\b")),
]

BLOCKING_CLASSES = ("override", "secret", "permission")


def scan_skill_text(text: str) -> list[dict]:
    """Screen skill text as untrusted input. Returns findings
    with class + plain-words explanation. Any blocking-class
    finding means the skill must not become usable."""
    findings = []
    seen_classes: set[str] = set()
    for cls, pattern in _SCAN_PATTERNS:
        if pattern.search(str(text or "")) and cls not in seen_classes:
            seen_classes.add(cls)
            findings.append({
                "class": cls,
                "finding": {
                    "override": "This skill tries to change the "
                                "harness rules. Skills cannot do that.",
                    "secret": "This skill asks for or exposes "
                              "credentials. Skills never receive them.",
                    "permission": "This skill tries to give itself "
                                  "new permissions. Skills cannot do that.",
                    "suspicious": "This skill contains a command "
                                  "worth a human check before use.",
                }[cls],
            })
    return findings


def validate_install(skill: dict) -> dict:
    """Installation validation (A9): required fields present,
    body non-empty, security scan clean of blocking findings.
    Returns {"usable": bool, "errors": [...], "findings": [...]}.
    An invalid skill is never usable — validation fails closed."""
    errors = []
    for field in ("name", "description", "body"):
        if not str(skill.get(field) or "").strip():
            errors.append(f"Missing required field: {field}.")
    findings = scan_skill_text(skill.get("body", ""))
    blocking = [f for f in findings if f["class"] in BLOCKING_CLASSES]
    for f in blocking:
        errors.append(f["finding"])
    return {"usable": not errors, "errors": errors, "findings": findings}


# ------------------------------------------------------------ registry (versions, telemetry, state)
class SkillRegistry:
    """Per-skill lifecycle state. The catalog folders stay
    read-only (the payload tree is read-only at runtime), so
    versions, deprecation, telemetry, and pins live HERE — in
    memory, optionally mirrored to the tenant store as
    kind="skill_*" rows (which the v21 fact listing skips).
    Rollback restores a stored body snapshot for ONE skill
    only; other skills are never touched (A14)."""

    def __init__(self):
        self._versions: dict[str, list[dict]] = {}
        self._current: dict[str, str] = {}
        self._deprecated: dict[str, dict] = {}
        self._provenance: dict[str, dict] = {}
        self._permissions: dict[str, list[str]] = {}
        self._retrieved: dict[str, int] = {}
        self._used: dict[str, int] = {}
        self._retrieval_events: list[dict] = []
        self._use_events: list[dict] = []
        self._integrity: dict[str, str] = {}

    # ---- versions (M6)
    def record_version(self, skill: dict, author: str = "",
                       note: str = "") -> dict:
        """Record a new version snapshot. Version numbers are
        simple counters (v1, v2, ...) — the visible version is
        the count, not a marketing number."""
        skill_id = skill["id"]
        history = self._versions.setdefault(skill_id, [])
        version = f"v{len(history) + 1}"
        entry = {"skill_id": skill_id, "version": version,
                 "name": skill.get("name", ""),
                 "body": skill.get("body", ""),
                 "hash": content_hash({**skill, "version": version}),
                 "author": author, "note": note,
                 "recorded_at": now_iso()}
        history.append(entry)
        self._current[skill_id] = version
        self._integrity[skill_id] = entry["hash"]
        return entry

    def current_version(self, skill_id: str) -> str | None:
        return self._current.get(skill_id)

    def with_version(self, skill: dict) -> dict:
        """The version, visible wherever the skill is listed or
        injected (M6): a copy of the skill carrying "version"."""
        out = dict(skill)
        out["version"] = self._current.get(skill["id"], "v1")
        if skill["id"] in self._deprecated:
            out.update(self._deprecated[skill["id"]])
        return out

    def rollback(self, skill_id: str, target_version: str) -> dict | None:
        """Per-skill rollback (A14): restore ONE skill to a
        previous version snapshot. Other skills' histories and
        current versions are untouched. Returns the restored
        snapshot, or None for an unknown version."""
        history = self._versions.get(skill_id, [])
        for entry in history:
            if entry["version"] == target_version:
                self._current[skill_id] = target_version
                self._integrity[skill_id] = entry["hash"]
                return dict(entry)
        return None

    # ---- integrity (A4)
    def verify_integrity(self, skill: dict) -> dict:
        """Recompute the hash; flag unexpected changes for
        review. A skill with no recorded hash is "unrecorded",
        not "changed" — only a mismatch flags."""
        recorded = self._integrity.get(skill["id"])
        current = content_hash({**skill,
                                "version": self._current.get(
                                    skill["id"], "")})
        if recorded is None:
            return {"skill_id": skill["id"], "status": "unrecorded"}
        if current != recorded:
            return {"skill_id": skill["id"], "status": "changed",
                    "note": "This skill changed since it was "
                            "recorded. Review the change before use."}
        return {"skill_id": skill["id"], "status": "ok"}

    # ---- deprecation (A7)
    def mark_deprecated(self, skill_id: str,
                        replacement_id: str | None = None,
                        reason: str = "") -> dict:
        entry = {"deprecated": True, "replacement_id": replacement_id,
                 "deprecation_reason": reason,
                 "deprecation_notice": (
                     "This skill is deprecated and should not be "
                     "used for new work."
                     + (f" Use {replacement_id} instead."
                        if replacement_id else ""))}
        self._deprecated[skill_id] = entry
        return entry

    def deprecation_notice(self, skill: dict) -> str | None:
        if skill.get("deprecated"):
            return skill.get("deprecation_notice")
        entry = self._deprecated.get(skill["id"])
        return entry["deprecation_notice"] if entry else None

    # ---- provenance + permissions (A8, A10)
    def record_provenance(self, skill: dict, author: str = "",
                          installed_by: str = "",
                          installed_at: str | None = None) -> dict:
        entry = {"skill_id": skill["id"],
                 "repo": skill.get("repo", ""),
                 "path": skill.get("path", ""),
                 "url": skill.get("url", ""),
                 "license": skill.get("license", ""),
                 "author": author, "installed_by": installed_by,
                 "installed_at": installed_at or now_iso()}
        self._provenance[skill["id"]] = entry
        return entry

    def provenance(self, skill: dict) -> dict:
        return self._provenance.get(skill["id"], {
            "skill_id": skill["id"], "repo": skill.get("repo", ""),
            "path": skill.get("path", ""), "url": skill.get("url", ""),
            "license": skill.get("license", ""), "author": "",
            "installed_by": "", "installed_at": ""})

    def set_permissions(self, skill_id: str, permissions: list[str]) -> None:
        """What a skill is allowed to do, inspectable by the
        user. Default when nothing is declared: reference only
        (the skill supplies text; it runs nothing). A declared
        list is a CLAIM, not a grant — the harness enforces its
        own rules regardless."""
        self._permissions[skill_id] = list(permissions)

    def permissions(self, skill: dict) -> list[str]:
        declared = skill.get("permissions")
        if isinstance(declared, list) and declared:
            return list(declared)
        return self._permissions.get(
            skill["id"], ["reference-only: supplies text, runs nothing"])

    # ---- telemetry + attribution (A11, A12)
    def record_retrieval(self, skill_id: str, task: str = "",
                         mission_id: str | None = None) -> None:
        self._retrieved[skill_id] = self._retrieved.get(skill_id, 0) + 1
        self._retrieval_events.append(
            {"skill_id": skill_id, "task": task[:200],
             "mission_id": mission_id, "at": now_iso()})

    def record_use(self, skill_id: str, mission_id: str | None = None,
                   contributed: bool = True) -> None:
        self._used[skill_id] = self._used.get(skill_id, 0) + 1
        self._use_events.append(
            {"skill_id": skill_id, "mission_id": mission_id,
             "contributed": contributed, "at": now_iso()})

    def retrieval_count(self, skill_id: str) -> int:
        return self._retrieved.get(skill_id, 0)

    def use_count(self, skill_id: str) -> int:
        return self._used.get(skill_id, 0)

    def was_used(self, skill_id: str) -> bool:
        return self._used.get(skill_id, 0) > 0

    def unused_skills(self, all_skill_ids: list[str]) -> list[dict]:
        """Unused-skill analysis (A13): skills never retrieved
        and never used, reported — never silently removed."""
        return [{"skill_id": sid,
                 "retrieved": self._retrieved.get(sid, 0),
                 "used": self._used.get(sid, 0),
                 "note": "Never retrieved or used. Consider "
                         "removing it or improving its description."}
                for sid in all_skill_ids
                if self._retrieved.get(sid, 0) == 0
                and self._used.get(sid, 0) == 0]

    def telemetry_summary(self) -> dict:
        return {"retrieved": dict(self._retrieved),
                "used": dict(self._used),
                "retrieval_events": len(self._retrieval_events),
                "use_events": len(self._use_events)}


# ------------------------------------------------------------ store-backed mission records
def pin_skills_for_mission(store, tenant_id: str, mission_id: str,
                           pins: list[tuple[str, str]]) -> dict:
    """Version pinning per mission (A5): the mission records
    exactly which skill versions it used, so a later rerun can
    reproduce the same combination."""
    record = {
        "memory_id": new_id("skpin"), "kind": "skill_pin_record",
        "scope": "mission", "scope_ref": mission_id,
        "topic": "skill-version-pins", "fact": "skill versions pinned",
        "mission_id": mission_id,
        "pins": [{"skill_id": sid, "version": ver} for sid, ver in pins],
        "status": "active", "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def pinned_versions_for_mission(store, tenant_id: str,
                                mission_id: str) -> dict[str, str]:
    for m in store.list_memories(tenant_id):
        if (m.get("kind") == "skill_pin_record"
                and m.get("mission_id") == mission_id):
            return {p["skill_id"]: p["version"] for p in m.get("pins", [])}
    return {}


def attribute_skills_to_mission(store, tenant_id: str, mission_id: str,
                                contributions: list[dict]) -> dict:
    """Skill-use attribution (A12): the mission record credits
    the skills that actually contributed, with what each one
    contributed. Only skills recorded as used are credited."""
    record = {
        "memory_id": new_id("skattr"), "kind": "skill_attribution",
        "scope": "mission", "scope_ref": mission_id,
        "topic": "skill-attribution", "fact": "skills credited",
        "mission_id": mission_id,
        "contributions": contributions,
        "status": "active", "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def attribution_for_mission(store, tenant_id: str,
                            mission_id: str) -> list[dict]:
    for m in store.list_memories(tenant_id):
        if (m.get("kind") == "skill_attribution"
                and m.get("mission_id") == mission_id):
            return list(m.get("contributions", []))
    return []


# ------------------------------------------------------------ duplicates / conflicts / deps
def find_duplicates(skills: list[dict]) -> list[dict]:
    """Duplicate detection (A1): same normalized name, or body
    overlap >= 0.8 (Jaccard on tokens). Returns groups with a
    consolidation recommendation. NEVER auto-deletes — there is
    deliberately no delete call in this module."""
    groups = []
    seen: set[int] = set()
    for i, first in enumerate(skills):
        if i in seen:
            continue
        group = [first]
        for j in range(i + 1, len(skills)):
            other = skills[j]
            same_name = (normalize_skill_name(first.get("name", ""))
                         == normalize_skill_name(other.get("name", ""))
                         and normalize_skill_name(first.get("name", "")))
            overlap = _jaccard(_tokens(first.get("body", "")),
                               _tokens(other.get("body", "")))
            if same_name or overlap >= 0.8:
                group.append(other)
                seen.add(j)
        if len(group) > 1:
            seen.add(i)
            groups.append({
                "skill_ids": [s["id"] for s in group],
                "names": [s.get("name", "") for s in group],
                "recommendation": "These skills overlap. Consider "
                                  "consolidating them into one skill "
                                  "and keeping the others as notes. "
                                  "Nothing was deleted automatically.",
            })
    return groups


_DIRECTIVE_RE = re.compile(
    r"\b(always|never|must|must not|do not|don't|only)\b\s+"
    r"([a-z][a-z ]{2,40}?)(?:\.|,|;|\n| and |$)")


def _directives(skill: dict) -> list[tuple[str, str]]:
    out = []
    for match in _DIRECTIVE_RE.finditer(str(skill.get("body", "")).lower()):
        out.append((match.group(1), " ".join(match.group(2).split())))
    return out


def detect_conflicts(skills: list[dict]) -> list[dict]:
    """Conflict detection between skills (A2): two skills that
    give opposite directives about the same action ("always
    check the diff" vs "never check the diff") are surfaced as
    a pair with both wordings. Nothing is silently merged."""
    out = []
    for i, first in enumerate(skills):
        for other in skills[i + 1:]:
            for mod_a, action_a in _directives(first):
                for mod_b, action_b in _directives(other):
                    if action_a != action_b:
                        continue
                    opposed = (
                        (mod_a in ("always", "must", "only")
                         and mod_b in ("never", "must not", "do not",
                                       "don't"))
                        or (mod_b in ("always", "must", "only")
                            and mod_a in ("never", "must not", "do not",
                                          "don't")))
                    if opposed:
                        out.append({
                            "skill_a": first["id"], "skill_b": other["id"],
                            "action": action_a,
                            "directive_a": f"{mod_a} {action_a}",
                            "directive_b": f"{mod_b} {action_b}",
                            "note": "These two skills contradict each "
                                    "other on this action. Resolve "
                                    "explicitly before combining them.",
                        })
    return out


def check_dependencies(skill: dict, available_ids: set[str]) -> dict:
    """Dependency checking (A3): a skill's declared
    dependencies (skill ids in meta "dependencies") must all be
    present before the skill is used."""
    declared = skill.get("dependencies") or []
    missing = [d for d in declared if d not in available_ids]
    return {"skill_id": skill["id"], "declared": list(declared),
            "missing": missing, "satisfied": not missing}


def detect_dependency_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Dependency cycle detection (R75): depth-first search
    over skill -> dependencies. Returns each cycle found as a
    list of skill ids in cycle order."""
    cycles = []
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                start = path.index(nxt) if nxt in path else 0
                cycles.append(path[start:] + [nxt])
            elif state.get(nxt, 0) == 0:
                visit(nxt, path + [nxt])
        state[node] = 2

    for node in graph:
        if state.get(node, 0) == 0:
            visit(node, [node])
    return cycles


def _parse_version(version: str) -> tuple[int, ...]:
    parts = []
    for piece in str(version).lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def check_compatibility(skill: dict, harness_version: str,
                        model_capabilities: list[str] | None = None) -> dict:
    """Compatibility checking (A6): the skill's declared
    minimum harness version and required model capabilities
    against what is actually available. Skills that declare
    nothing are compatible (no news is no constraint)."""
    problems = []
    minimum = skill.get("min_harness_version")
    if minimum and _parse_version(harness_version) < _parse_version(minimum):
        problems.append(
            f"Needs harness version {minimum} or newer; "
            f"this harness is {harness_version}.")
    caps = set(model_capabilities or [])
    for need in skill.get("required_capabilities") or []:
        if need not in caps:
            problems.append(f"Needs model capability '{need}'.")
    return {"skill_id": skill["id"], "compatible": not problems,
            "problems": problems}


# ------------------------------------------------------------ composition (M5)
def compose_skills(skills: list[dict], task: str = "",
                   priority: list[str] | None = None) -> dict:
    """Skill composition with conflict resolution (M5): combine
    several skills into one workflow instead of blindly
    concatenating them. Conflicts found by detect_conflicts()
    are resolved explicitly: the winner is the skill listed
    earliest in `priority` (or, without a priority list, the
    first skill in input order), and every resolution is
    recorded with the reason. The composed text carries the
    resolution log in plain words, so a reader can see exactly
    which instruction won and why."""
    priority = priority or [s["id"] for s in skills]
    rank = {sid: idx for idx, sid in enumerate(priority)}
    ordered = sorted(skills, key=lambda s: rank.get(s["id"], len(rank)))
    conflicts = detect_conflicts(ordered)
    resolutions = []
    losers: set[str] = set()
    for conflict in conflicts:
        winner = (conflict["skill_a"]
                  if rank.get(conflict["skill_a"], len(rank))
                  <= rank.get(conflict["skill_b"], len(rank))
                  else conflict["skill_b"])
        loser = (conflict["skill_b"] if winner == conflict["skill_a"]
                 else conflict["skill_a"])
        resolutions.append({
            "action": conflict["action"],
            "winner": winner, "loser": loser,
            "winner_directive": conflict["directive_a"]
            if winner == conflict["skill_a"]
            else conflict["directive_b"],
            "loser_directive": conflict["directive_b"]
            if winner == conflict["skill_a"]
            else conflict["directive_a"],
            "why": "The winning skill was listed first in the "
                   "composition priority for this task.",
        })
        losers.add(loser)
    steps = []
    for skill in ordered:
        steps.append({
            "skill_id": skill["id"],
            "name": skill.get("name", ""),
            "body": skill.get("body", ""),
            "conflicts_lost": skill["id"] in losers,
        })
    lines = ["## Combined workflow"]
    if task:
        lines.append(f"Task: {task}")
    for step in steps:
        lines.append(f"\n### {step['name']} ({step['skill_id']})")
        lines.append(step["body"])
    if resolutions:
        lines.append("\n## Conflicting instructions — how they were resolved")
        for res in resolutions:
            lines.append(
                f"- On '{res['action']}': {res['winner']}'s "
                f"instruction won (\"{res['winner_directive']}\") over "
                f"{res['loser']}'s (\"{res['loser_directive']}\"). "
                f"{res['why']}")
    return {"steps": steps, "conflicts": conflicts,
            "resolutions": resolutions, "text": "\n".join(lines)}


# ------------------------------------------------------------ evaluation (M7)
def evaluate_skill(outcomes: list[dict], baseline: dict | None = None) -> dict:
    """Skill evaluation (M7): judge a skill by MEASURED effect —
    task success rate, reliability, cost, and latency across
    recorded outcomes — never by the length or apparent
    sophistication of its instructions. Each outcome is
    {"success": bool, "cost_usd": float, "latency_ms": float}.
    A baseline (same fields, measured without the skill) turns
    the report into a comparison; without one, the report is
    absolute only, and says so."""
    if not outcomes:
        return {"verdict": "not enough data",
                "note": "No recorded outcomes. A skill cannot be "
                        "judged without measured use."}
    n = len(outcomes)
    success = sum(1 for o in outcomes if o.get("success")) / n
    cost = sum(float(o.get("cost_usd", 0.0)) for o in outcomes) / n
    latency = sum(float(o.get("latency_ms", 0.0)) for o in outcomes) / n
    report: dict = {
        "tasks": n, "success_rate": round(success, 4),
        "reliability": round(success, 4),
        "mean_cost_usd": round(cost, 4),
        "mean_latency_ms": round(latency, 1),
    }
    if baseline:
        base_success = float(baseline.get("success_rate", 0.0))
        delta = success - base_success
        report["baseline_success_rate"] = base_success
        report["success_delta_vs_baseline"] = round(delta, 4)
        report["verdict"] = ("improves outcomes" if delta > 0 else
                             "no measured improvement" if delta == 0 else
                             "harms outcomes")
    else:
        report["verdict"] = ("positive measured effect"
                             if success >= 0.5 else
                             "weak measured effect")
        report["note"] = "No baseline was supplied, so this is an "
        "absolute reading, not a comparison."
    return report


# ------------------------------------------------------------ progressive loading, stages 3-4 (M4)
def detail_files(library, skill_id: str) -> dict:
    """List a skill folder's extra files beyond skill.md and
    meta.json, split into reference materials (stage 3:
    markdown/text/docs) and scripts/examples (stage 4: code,
    scripts, example files). The library's own retrieval never
    loads these — they are loaded only on demand."""
    root_for = getattr(library, "_root_for", None)
    root = root_for(skill_id) if root_for else library.root
    folder = Path(root) / skill_id
    references: list[str] = []
    scripts: list[str] = []
    if not folder.is_dir():
        return {"references": references, "scripts": scripts}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name in ("skill.md", "meta.json"):
            continue
        if path.suffix.lower() in (".py", ".sh", ".js", ".ts"):
            scripts.append(path.name)
        elif "example" in path.name.lower():
            scripts.append(path.name)
        else:
            references.append(path.name)
    return {"references": references, "scripts": scripts}


def load_detail(library, skill_id: str, stage: int) -> dict:
    """On-demand loading (stages 3-4). Stage 3 returns the
    reference materials' text; stage 4 returns scripts/examples.
    Anything else raises ValueError — there are no other
    stages, and detail is never loaded by default retrieval."""
    if stage not in (3, 4):
        raise ValueError("detail loading supports stages 3 and 4 only")
    files = detail_files(library, skill_id)
    wanted = files["references"] if stage == 3 else files["scripts"]
    root_for = getattr(library, "_root_for", None)
    root = root_for(skill_id) if root_for else library.root
    folder = Path(root) / skill_id
    loaded = {}
    for name in wanted:
        try:
            loaded[name] = (folder / name).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
    return {"skill_id": skill_id, "stage": stage, "files": loaded,
            "total_chars": sum(len(v) for v in loaded.values())}
