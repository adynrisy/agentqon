"""AgentQon — long-term memory (v21, Track 3).

Mem0's scoped-fact pattern, implemented natively on the platform's
SQLite store (Mem0 itself is Apache-2.0; the pattern — scoped facts
with curation — is adopted, no code is vendored):

- Facts live in four scopes: `user` (what the workspace has
  learned), `agent` (per-agent track record, scope_ref =
  agent_version_id), `session` (per-mission record, scope_ref =
  mission_id) and `project` (W9: per-project record, scope_ref =
  project id — the project memory scope the fourth-document build
  adds, so a project's facts no longer hide in user scope). Every
  fact is tenant-isolated.
- Curation: a fact is keyed by (scope, scope_ref, topic). Writing a
  new fact under an existing key SUPERSEDES the old one (kept on
  record, status=superseded, linked via `supersedes`); writing the
  same text again is a dedupe no-op.
- Facts are extracted deterministically from completed missions —
  platform-verified outcomes (the evaluated winner, its agent, the
  mission summary), never unverified model prose.
- Retrieval renders a provenance-labelled context block that the
  orchestrator injects into drafting prompts as BACKGROUND — it is
  never part of the citable evidence corpus.
- No raw secrets: every fact passes redact_text() before storage
  (secret-shaped substrings are replaced, building on the vault's
  looks_like_secret heuristics).
"""
from __future__ import annotations

import re

from .domain import new_id, now_iso
from .vault import looks_like_secret

# W4 (gap-memory) adds the long-term scopes user/project/mission/
# organization alongside the v21 scopes. The v21 behaviour for
# user/agent/session is unchanged; the new scopes are enforced
# at read time by app/longterm_memory.py read_for().
SCOPES = ("user", "agent", "session", "project", "mission",
          "organization")

# Reliability classes shared with app/longterm_memory.py (kept
# here in miniature so this module stays import-cycle free):
# the full five-way system, its labels, and its rules live there.
_RELIABILITY_BY_SOURCE = {
    "manual": "explicitly_requested",
    "mission": "tool_confirmed",
    "file": "file_contained",
    "source": "true_at_a_time",
    "model": "model_inferred",
}
_RELIABILITY_LABELS = {
    "explicitly_requested": "you told us this directly",
    "model_inferred": "a model guessed this — not verified",
    "file_contained": "read from a file",
    "tool_confirmed": "verified by a tool result",
    "true_at_a_time": "true when recorded, may have changed",
}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|apikey|password|passwd|secret|token)"
               r"\s*[:=]\s*\S+"),
]


def redact_text(text: str) -> str:
    """Replace secret-shaped substrings with [redacted]. A fact that
    is nothing but a secret is refused entirely."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[redacted]", out)
    if looks_like_secret(out) and "[redacted]" not in out:
        out = "[redacted]"
    return out


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def add_fact(store, tenant_id: str, scope: str, scope_ref: str, topic: str,
             fact: str, source: str = "manual", mission_id: str | None = None,
             actor: str = "", reliability: str | None = None,
             provenance: dict | None = None, confidence=None,
             expires_at: str | None = None) -> dict:
    """Curated write: dedupe identical facts, supersede same-key
    facts with different text. Returns the stored record.

    W4 (gap-memory): every record also carries its reliability
    class (the five-way system — see app/longterm_memory.py),
    its provenance, an optional confidence note (validated to
    0..1, never a measured probability), and an optional
    expiry. Defaults are derived from `source` so existing
    callers are unchanged."""
    if scope not in SCOPES:
        raise ValueError(f"unknown memory scope: {scope}")
    reliability = reliability or _RELIABILITY_BY_SOURCE.get(
        source, "true_at_a_time")
    if reliability not in _RELIABILITY_LABELS:
        raise ValueError(f"unknown reliability class: {reliability}")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise ValueError("confidence must be a number between 0 and 1")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be a number between 0 and 1")
    fact = redact_text(str(fact).strip())
    if not fact:
        raise ValueError("fact is empty after redaction")
    topic = str(topic).strip()[:120]
    prov = dict(provenance) if provenance else {"origin": source,
                                                "detail": source}
    prov["detail"] = redact_text(str(prov.get("detail", source))[:500])
    existing = [m for m in store.list_memories(tenant_id)
                if m["scope"] == scope and m["scope_ref"] == scope_ref
                and m["topic"] == topic and m["status"] == "active"]
    for m in existing:
        if _norm(m["fact"]) == _norm(fact):
            return m  # dedupe: nothing to do
    supersedes = None
    for m in existing:
        m["status"] = "superseded"
        m["updated_at"] = now_iso()
        store.put_memory(tenant_id, m)
        supersedes = m["memory_id"]
    record = {
        "memory_id": new_id("mem"), "scope": scope, "scope_ref": scope_ref,
        "topic": topic, "fact": fact, "source": source,
        "mission_id": mission_id, "actor": actor, "status": "active",
        "supersedes": supersedes,
        "reliability": reliability,
        "reliability_label": _RELIABILITY_LABELS[reliability],
        "provenance": prov,
        "confidence": confidence,
        "expires_at": expires_at,
        "last_verified_at": now_iso(),
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def list_facts(store, tenant_id: str, scope: str | None = None,
               scope_ref: str | None = None,
               include_superseded: bool = False,
               include_expired: bool = False) -> list[dict]:
    out = []
    for m in store.list_memories(tenant_id):
        # v22: outcome lessons (app/evolve.py) share this table but
        # are evidence records, not facts — they never surface here.
        # W4: the same rule generalizes — any system row whose
        # kind is not a user-facing fact (consent, telemetry,
        # skill pins/attribution) never surfaces here either.
        if m.get("kind") not in (None, "fact"):
            continue
        if scope is not None and m["scope"] != scope:
            continue
        if scope_ref is not None and m["scope_ref"] != scope_ref:
            continue
        if not include_superseded and m["status"] != "active":
            continue
        if not include_expired and m.get("expires_at"):
            try:
                from datetime import datetime
                exp = datetime.fromisoformat(
                    str(m["expires_at"]).replace("Z", "+00:00"))
                if exp <= datetime.now(exp.tzinfo):
                    continue
            except ValueError:
                pass
        out.append(m)
    out.sort(key=lambda m: m["created_at"])
    return out


def extract_from_mission(store, tenant_id: str, mission_id: str) -> list[dict]:
    """Deterministic extraction from a completed mission: the
    evaluated winning candidate becomes a user-scope outcome fact,
    an agent-scope track-record fact, and a session-scope summary.
    Returns the facts written (possibly fewer on replays — curation
    dedupes)."""
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None or mission["status"] != "completed":
        return []
    candidates = store.list_candidates(tenant_id, mission_id)
    if not candidates:
        return []
    evaluations = {e["candidate_id"]: e
                   for e in store.list_evaluations(tenant_id, mission_id)}

    def _avg(evaluation: dict | None) -> float:
        if not evaluation:
            return -1.0
        dims = evaluation.get("dimensions", {})
        scores = [d.get("score", 0.0) for d in dims.values()
                  if isinstance(d, dict)]
        return sum(scores) / len(scores) if scores else -1.0

    winner = max(candidates,
                 key=lambda c: _avg(evaluations.get(c["candidate_id"])))
    contract = mission.get("contract", {})
    task_type = contract.get("task_type") or "strategy"
    title = mission.get("title", "")
    facts = []
    facts.append(add_fact(
        store, tenant_id, "user", "", f"outcome:{task_type}",
        f"Mission '{title}': the selected approach was "
        f"'{winner['title']}' (strategy: {winner['strategy']}).",
        source="mission", mission_id=mission_id))
    avg = _avg(evaluations.get(winner["candidate_id"]))
    avg_txt = f"{avg:.2f}" if avg >= 0 else "not scored"
    facts.append(add_fact(
        store, tenant_id, "agent", winner["agent_version_id"],
        f"selected:{task_type}",
        f"Produced the selected candidate '{winner['title']}' for "
        f"objective '{contract.get('objective', '')[:80]}' "
        f"(evaluation average {avg_txt}).",
        source="mission", mission_id=mission_id))
    facts.append(add_fact(
        store, tenant_id, "session", mission_id, "summary",
        f"{title} — {winner['summary']}",
        source="mission", mission_id=mission_id))
    return facts


def memory_context(store, tenant_id: str, task_type: str | None = None,
                   limit: int = 6, project_id: str | None = None) -> str:
    """The provenance-labelled retrieval block injected into drafting
    prompts. User-scope facts first (workspace knowledge), then the
    most relevant agent track records. Empty string when there is
    nothing to inject."""
    lines: list[str] = []
    for m in list_facts(store, tenant_id, scope="user"):
        # W4: the reliability label rides in the bracket, so a
        # model guess never reads with verified authority.
        label = m.get("reliability_label", "")
        suffix = f" · {label}" if label else ""
        lines.append(
            f"- [memory:user · {m['topic']} · from mission "
            f"{m.get('mission_id') or 'manual'} · {m['created_at'][:10]}"
            f"{suffix}] {m['fact']}")
    agent_facts = list_facts(store, tenant_id, scope="agent")
    if task_type:
        agent_facts.sort(key=lambda m: 0 if m["topic"].endswith(task_type)
                         else 1)
    for m in agent_facts:
        label = m.get("reliability_label", "")
        suffix = f" · {label}" if label else ""
        lines.append(
            f"- [memory:agent {m['scope_ref']} · {m['topic']} · "
            f"{m['created_at'][:10]}{suffix}] {m['fact']}")
    if project_id:
        for m in list_facts(store, tenant_id, scope="project",
                            scope_ref=project_id):
            lines.append(
                f"- [memory:project {m['scope_ref']} · {m['topic']} · "
                f"{m['created_at'][:10]}] {m['fact']}")
    return "\n".join(lines[:limit])


def can_write(role: str, user_id: str, scope: str, scope_ref: str) -> bool:
    """Write rule: owners/admins manage every scope; members write
    only their own user-scope facts; agent/session/project scopes are
    system-curated (extraction) or admin-managed."""
    if role in ("owner", "admin"):
        return True
    if role == "member":
        return scope == "user" and scope_ref in ("", user_id)
    return False
