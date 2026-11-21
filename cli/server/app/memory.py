"""AgentQon — long-term memory (v21, Track 3).

Mem0's scoped-fact pattern, implemented natively on the platform's
SQLite store (Mem0 itself is Apache-2.0; the pattern — scoped facts
with curation — is adopted, no code is vendored):

- Facts live in three scopes: `user` (what the workspace has
  learned), `agent` (per-agent track record, scope_ref =
  agent_version_id) and `session` (per-mission record, scope_ref =
  mission_id). Every fact is tenant-isolated.
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

SCOPES = ("user", "agent", "session")

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
             actor: str = "") -> dict:
    """Curated write: dedupe identical facts, supersede same-key
    facts with different text. Returns the stored record."""
    if scope not in SCOPES:
        raise ValueError(f"unknown memory scope: {scope}")
    fact = redact_text(str(fact).strip())
    if not fact:
        raise ValueError("fact is empty after redaction")
    topic = str(topic).strip()[:120]
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
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def list_facts(store, tenant_id: str, scope: str | None = None,
               scope_ref: str | None = None,
               include_superseded: bool = False) -> list[dict]:
    out = []
    for m in store.list_memories(tenant_id):
        # v22: outcome lessons (app/evolve.py) share this table but
        # are evidence records, not facts — they never surface here.
        if m.get("kind") == "lesson":
            continue
        if scope is not None and m["scope"] != scope:
            continue
        if scope_ref is not None and m["scope_ref"] != scope_ref:
            continue
        if not include_superseded and m["status"] != "active":
            continue
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
                   limit: int = 6) -> str:
    """The provenance-labelled retrieval block injected into drafting
    prompts. User-scope facts first (workspace knowledge), then the
    most relevant agent track records. Empty string when there is
    nothing to inject."""
    lines: list[str] = []
    for m in list_facts(store, tenant_id, scope="user"):
        lines.append(
            f"- [memory:user · {m['topic']} · from mission "
            f"{m.get('mission_id') or 'manual'} · {m['created_at'][:10]}] "
            f"{m['fact']}")
    agent_facts = list_facts(store, tenant_id, scope="agent")
    if task_type:
        agent_facts.sort(key=lambda m: 0 if m["topic"].endswith(task_type)
                         else 1)
    for m in agent_facts:
        lines.append(
            f"- [memory:agent {m['scope_ref']} · {m['topic']} · "
            f"{m['created_at'][:10]}] {m['fact']}")
    return "\n".join(lines[:limit])


def can_write(role: str, user_id: str, scope: str, scope_ref: str) -> bool:
    """Write rule: owners/admins manage every scope; members write
    only their own user-scope facts; agent/session scopes are
    system-curated (extraction) or admin-managed."""
    if role in ("owner", "admin"):
        return True
    if role == "member":
        return scope == "user" and scope_ref in ("", user_id)
    return False
