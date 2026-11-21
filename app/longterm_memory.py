"""AgentQon — consented cross-session long-term memory (W4, gap-memory).

Built on the existing tenant-scoped memories table (app/store.py) and
the v21 curation rules (app/memory.py). No new tables, no new
dependencies, standard library only. Every record written here
carries ``kind="fact"`` (or a ``ltm_*`` kind for system rows), so
the v21 fact listing — which skips non-fact kinds — never leaks
system rows into user-facing memory, and tenant isolation comes
from the store itself: one tenant's rows are never read for another.

The five-way reliability system (every entry is exactly one of
these, visible wherever the memory is used):

- ``explicitly_requested`` — the user stated it in plain words.
- ``model_inferred`` — the model guessed it. NEVER presented with
  the authority of a tool-confirmed entry, and NEVER auto-trusted:
  model-inferred facts are stored as ``status="proposed"`` until
  the user approves them (approve_fact).
- ``file_contained`` — read from a file the user pointed at.
- ``tool_confirmed`` — verified by a tool or execution result.
- ``true_at_a_time`` — true when recorded; it may have changed.

Confidence is a stored note, never a measured probability: every
rendering that shows a confidence number carries that sentence in
plain words (see CONFIDENCE_NOTE). validate_confidence() rejects
anything outside 0..1.

Consent gate: retain() refuses (ConsentRequired) unless the user
has granted long-term-memory consent (grant_consent) or the call
passes approved=True for that one write. Nothing is retained
without an approval path. Model-inferred entries additionally
require approval even when general consent exists — consent to
remember is not consent to trust a guess.

Scopes, enforced at read time by read_for(): ``user`` (one user),
``project`` (one project), ``mission`` (one mission),
``organization`` (the whole tenant). A mission-scoped record is
never returned for a different mission, and project-scoped records
never cross projects. Legacy v21 scopes (``agent``, ``session``)
keep working through the v21 functions; this module's read path
only serves the four scopes above plus tenant-wide legacy user
facts whose scope_ref is empty.

Expiry: per-entry ``expires_at`` wins; otherwise the per-class
default TTL applies (CLASS_TTL_DAYS). process_expiry() marks
overdue active records ``expired``; read paths exclude expired
records even before a sweep runs.

Contradiction: a new fact that contradicts a stored one (same
normalized key, conflicting polarity cues) does NOT silently
overwrite it. The old record becomes ``contested`` and the new
record names it in ``contradicts``; list_conflicts() surfaces the
pair for a human decision.

Deletion propagation (proved by test, not asserted): delete_fact()
with confirm=True removes the row, marks every derived record
(``derived_from`` naming it, or naming it in
``source_memory_ids``) ``invalidated``, and clears every
in-process retrieval cache entry for the tenant. Deleting an
original therefore cannot leave a live summary, cached answer, or
derived memory behind.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone

from .domain import new_id, now_iso
from .memory import redact_text

# ------------------------------------------------------------ vocabulary
RELIABILITY_CLASSES = (
    "explicitly_requested",
    "model_inferred",
    "file_contained",
    "tool_confirmed",
    "true_at_a_time",
)

RELIABILITY_LABELS = {
    # Plain words, and deliberately unequal: a guess must never
    # read with the same authority as a verified result.
    "explicitly_requested": "you told us this directly",
    "model_inferred": "a model guessed this — not verified",
    "file_contained": "read from a file",
    "tool_confirmed": "verified by a tool result",
    "true_at_a_time": "true when recorded, may have changed",
}

# Reliability weight used only inside retrieval ranking (never
# shown as a probability, never shown at all to the user).
_RELIABILITY_WEIGHT = {
    "explicitly_requested": 0.9,
    "model_inferred": 0.3,
    "file_contained": 0.7,
    "tool_confirmed": 1.0,
    "true_at_a_time": 0.5,
}

PROVENANCE_ORIGINS = (
    "user_provided",
    "file_extracted",
    "source_retrieved",
    "model_inferred",
    "execution_confirmed",
)

# Origin implied when the caller only gives a v21-style source tag.
_SOURCE_TO_ORIGIN = {
    "manual": "user_provided",
    "mission": "execution_confirmed",
    "file": "file_extracted",
    "source": "source_retrieved",
    "model": "model_inferred",
}

# Reliability implied when the caller only gives a source tag.
_SOURCE_TO_RELIABILITY = {
    "manual": "explicitly_requested",
    "mission": "tool_confirmed",
    "file": "file_contained",
    "source": "true_at_a_time",
    "model": "model_inferred",
}

SCOPES = ("user", "project", "mission", "organization")

# Per-class expiry in days (None = no automatic expiry). An
# explicit per-entry expires_at always wins over the class rule.
CLASS_TTL_DAYS = {
    "explicitly_requested": None,
    "model_inferred": 30,
    "file_contained": 180,
    "tool_confirmed": 365,
    "true_at_a_time": 90,
}

# Retention: an active record older than this (days) with zero
# recorded uses is reported for pruning by enforce_retention().
RETENTION_DAYS = {
    "explicitly_requested": 730,
    "model_inferred": 90,
    "file_contained": 365,
    "tool_confirmed": 730,
    "true_at_a_time": 180,
}

CONFIDENCE_NOTE = ("This confidence number is a rough note, not a "
                   "measured probability.")

# Read/write roles per memory class. Owners and admins manage
# everything; members read their own user-scope records and
# tenant-wide organization records, and write only their own
# user-scope records; viewers read organization records only.
_READ_ROLES = {
    "user": ("owner", "admin", "member"),
    "project": ("owner", "admin", "member"),
    "mission": ("owner", "admin", "member"),
    "organization": ("owner", "admin", "member", "viewer"),
}
_WRITE_ROLES = {
    "user": ("owner", "admin", "member"),
    "project": ("owner", "admin"),
    "mission": ("owner", "admin"),
    "organization": ("owner", "admin"),
}

_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

# Small, explicit polarity cues for contradiction candidates.
# This is candidate DETECTION (surfaced for a human), never an
# automatic verdict: the pair is reported, not resolved.
_NEGATIONS = {"not", "never", "no", "none", "without", "no longer",
              "doesnt", "doesn't", "dont", "don't", "isnt", "isn't",
              "cannot", "cant", "can't", "instead", "rather"}
_ANTONYMS = {
    "prefers": "dislikes", "likes": "dislikes", "uses": "avoids",
    "always": "never", "requires": "forbids", "allows": "forbids",
    "enabled": "disabled", "on": "off", "open": "closed",
}

# In-process retrieval cache: tenant -> (fingerprint, doc
# vectors, idf). Fingerprint = hash of (memory_id, updated_at)
# over the tenant's rows, so any write, expiry sweep, or
# deletion invalidates it (stale-index detection, S113).
# delete_fact() also clears it.
_INDEX_CACHE: dict[str, tuple[str, dict, dict]] = {}
# Access telemetry: content-free events (S117) — ids and roles
# only, never fact text.
_ACCESS_LOG: list[dict] = []


class ConsentRequired(Exception):
    """Raised when a retain is attempted without consent or a
    per-call approval. The caller must get the user's approval
    first; the fact is not stored."""


class DeletionNotConfirmed(Exception):
    """Raised when delete_fact() is called without confirm=True."""


# ------------------------------------------------------------ helpers
def normalize_key(text: str) -> str:
    """Key normalization (S101): lowercase, punctuation dropped,
    whitespace collapsed. Two keys that differ only in case,
    punctuation, or spacing normalize to the same key."""
    return " ".join(_PUNCT_RE.sub(" ", str(text).lower()).split())


def _norm_fact(text: str) -> str:
    return " ".join(str(text).lower().split())


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_confidence(value) -> float | None:
    """Confidence-field validation (S105): None or a number in
    0..1. Anything else is refused, never clamped silently."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError("confidence must be a number between 0 and 1")
    if math.isnan(num) or num < 0.0 or num > 1.0:
        raise ValueError("confidence must be a number between 0 and 1")
    return num


def _fact_hash(scope: str, scope_ref: str, topic: str, fact: str) -> str:
    raw = f"{scope}\0{scope_ref}\0{normalize_key(topic)}\0{_norm_fact(fact)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_expired(record: dict, at: datetime | None = None) -> bool:
    exp = record.get("expires_at")
    if not exp:
        return False
    parsed = _parse_ts(exp)
    return parsed is not None and parsed <= (at or _now())


def _is_fact_row(record: dict) -> bool:
    """System rows (consent, telemetry, attribution) share the
    memories table but are never user-facing facts."""
    return record.get("kind") in (None, "fact")


def _all_facts(store, tenant_id: str) -> list[dict]:
    return [m for m in store.list_memories(tenant_id) if _is_fact_row(m)]


def _log_access(tenant_id: str, memory_id: str, action: str,
                role: str) -> None:
    """Access telemetry (S117): ids, action, role, time — never
    the fact text, never the topic content."""
    _ACCESS_LOG.append({"tenant_id": tenant_id, "memory_id": memory_id,
                        "action": action, "role": role,
                        "at": now_iso()})
    if len(_ACCESS_LOG) > 5000:
        del _ACCESS_LOG[:1000]


def access_log() -> list[dict]:
    return [dict(e) for e in _ACCESS_LOG]


def clear_access_log() -> None:
    _ACCESS_LOG.clear()


# ------------------------------------------------------------ consent
def grant_consent(store, tenant_id: str, user_id: str,
                  classes: list[str] | None = None) -> dict:
    """Record the user's approval to retain long-term memory.
    Consent to remember is NOT consent to trust a model guess
    (model-inferred facts still need per-fact approval)."""
    record = {
        "memory_id": new_id("consent"), "kind": "ltm_consent",
        "scope": "user", "scope_ref": user_id, "topic": "ltm-consent",
        "fact": "long-term memory consent granted",
        "classes": list(classes) if classes else list(RELIABILITY_CLASSES),
        "user_id": user_id, "status": "active",
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def revoke_consent(store, tenant_id: str, user_id: str) -> bool:
    """Revoke consent. Revocation stops FUTURE retains; it does
    not silently delete already-stored facts (deletion is a
    separate, confirmed act — see delete_fact)."""
    revoked = False
    for m in store.list_memories(tenant_id):
        if (m.get("kind") == "ltm_consent"
                and m.get("user_id") == user_id
                and m.get("status") == "active"):
            m["status"] = "revoked"
            m["updated_at"] = now_iso()
            store.put_memory(tenant_id, m)
            revoked = True
    return revoked


def has_consent(store, tenant_id: str, user_id: str) -> bool:
    return any(
        m.get("kind") == "ltm_consent" and m.get("user_id") == user_id
        and m.get("status") == "active"
        for m in store.list_memories(tenant_id))


# ------------------------------------------------------------ access control
def can_read(role: str, user_id: str, record: dict) -> bool:
    """Who may read a memory class (B10). Members read only
    their own user-scope records (plus tenant-wide legacy user
    facts with an empty scope_ref, which are workspace knowledge)
    and organization records; mission/project records are
    readable by tenant members only through read_for()'s exact
    scope match, which this check complements."""
    if role not in _READ_ROLES.get(record.get("scope", ""), ()):
        return False
    if role == "member" and record.get("scope") == "user":
        ref = record.get("scope_ref", "")
        return ref in ("", user_id)
    return True


def can_write(role: str, user_id: str, scope: str, scope_ref: str) -> bool:
    if role not in _WRITE_ROLES.get(scope, ()):
        return False
    if role == "member" and scope == "user":
        return scope_ref in ("", user_id)
    return True


# ------------------------------------------------------------ retain / propose / approve
def retain(store, tenant_id: str, user_id: str, scope: str,
           scope_ref: str, topic: str, fact: str, *,
           reliability: str | None = None,
           provenance: dict | None = None,
           source: str = "manual",
           confidence=None,
           expires_at: str | None = None,
           approved: bool = False,
           access: dict | None = None,
           derived_from: list[str] | None = None,
           mission_id: str | None = None,
           _allow_supersede: bool = True) -> dict:
    """Store one long-term memory. Consent-gated; dedupes on the
    normalized (scope, scope_ref, topic, fact); supersedes a
    same-key record with different text (kept, version bumped).

    Model-inferred facts are NEVER trusted by this path: passing
    reliability="model_inferred" stores the fact as "proposed"
    (see propose_fact); only approve_fact() promotes it."""
    if scope not in SCOPES:
        raise ValueError(f"unknown long-term memory scope: {scope}")
    if not has_consent(store, tenant_id, user_id) and not approved:
        raise ConsentRequired(
            "Long-term memory needs your approval first: nothing "
            "is retained without it.")
    reliability = reliability or _SOURCE_TO_RELIABILITY.get(
        source, "true_at_a_time")
    if reliability not in RELIABILITY_CLASSES:
        raise ValueError(f"unknown reliability class: {reliability}")
    confidence = validate_confidence(confidence)
    fact = redact_text(str(fact).strip())
    if not fact:
        raise ValueError("fact is empty after redaction")
    topic = normalize_key(topic)[:120]
    if expires_at is None:
        ttl = CLASS_TTL_DAYS.get(reliability)
        if ttl is not None:
            expires_at = (_now() + timedelta(days=ttl)).isoformat(
                ).replace("+00:00", "Z")
    prov = dict(provenance) if provenance else {}
    prov.setdefault("origin", _SOURCE_TO_ORIGIN.get(source,
                                                    "user_provided"))
    if prov["origin"] not in PROVENANCE_ORIGINS:
        raise ValueError(f"unknown provenance origin: {prov['origin']}")
    prov.setdefault("detail", source)
    # Sensitive-data filtering (B11): scrub secrets from the
    # provenance detail too, not just the fact text.
    prov["detail"] = redact_text(str(prov["detail"])[:500])
    prov.setdefault("recorded_at", now_iso())
    # Proposed model guesses dedupe against other proposals;
    # active records dedupe against active records.
    _live = ("active", "proposed") if reliability == "model_inferred" \
        else ("active",)
    existing = [m for m in _all_facts(store, tenant_id)
                if m["scope"] == scope and m["scope_ref"] == scope_ref
                and normalize_key(m.get("topic", "")) == topic
                and m["status"] in _live]
    for m in existing:
        if _norm_fact(m["fact"]) == _norm_fact(fact):
            return m  # dedupe: the same memory stored twice is one
    supersedes = None
    version = 1
    # Contradiction keeps the old record live (it becomes
    # "contested" instead); ordinary updates supersede it.
    if _allow_supersede:
        for m in existing:
            m["status"] = "superseded"
            m["updated_at"] = now_iso()
            store.put_memory(tenant_id, m)
            supersedes = m["memory_id"]
            version = int(m.get("version", 1)) + 1
    record = {
        "memory_id": new_id("mem"), "kind": "fact",
        "scope": scope, "scope_ref": scope_ref, "topic": topic,
        "fact": fact, "source": source, "mission_id": mission_id,
        "actor": user_id,
        "status": "active" if reliability != "model_inferred"
                  else "proposed",
        "reliability": reliability,
        "reliability_label": RELIABILITY_LABELS[reliability],
        "provenance": prov,
        "confidence": confidence,
        "confidence_note": CONFIDENCE_NOTE if confidence is not None
                           else None,
        "access": access or {"read_roles": list(_READ_ROLES[scope]),
                             "write_roles": list(_WRITE_ROLES[scope])},
        "expires_at": expires_at,
        "supersedes": supersedes,
        "version": version,
        "fact_hash": _fact_hash(scope, scope_ref, topic, fact),
        "derived_from": list(derived_from) if derived_from else [],
        "use_count": 0, "helpful_count": 0, "last_used_at": None,
        "last_verified_at": now_iso(),
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    _INDEX_CACHE.pop(tenant_id, None)
    return record


def propose_fact(store, tenant_id: str, user_id: str, scope: str,
                 scope_ref: str, topic: str, fact: str, **kwargs) -> dict:
    """A model-inferred candidate: stored as "proposed", never
    returned by active retrieval, until a user approves it. Failed
    or speculative model claims must never auto-become trusted
    memory — this is the only path that stores them."""
    kwargs["reliability"] = "model_inferred"
    return retain(store, tenant_id, user_id, scope, scope_ref, topic,
                  fact, **kwargs)


def approve_fact(store, tenant_id: str, memory_id: str) -> dict | None:
    """Promote a proposed fact to active. Its reliability stays
    "model_inferred" and its label stays the weak one — approval
    makes it usable, it does not upgrade it to verified."""
    record = store.get_memory(tenant_id, memory_id)
    if record is None or record.get("status") != "proposed":
        return None
    record["status"] = "active"
    record["approved_at"] = now_iso()
    record["updated_at"] = now_iso()
    store.put_memory(tenant_id, record)
    _INDEX_CACHE.pop(tenant_id, None)
    return record


def verify_entry(store, tenant_id: str, memory_id: str) -> dict | None:
    """Refresh last_verified_at (B4). Verification is a human or
    tool re-check, recorded with a timestamp — not a silent bump."""
    record = store.get_memory(tenant_id, memory_id)
    if record is None:
        return None
    record["last_verified_at"] = now_iso()
    record["updated_at"] = now_iso()
    store.put_memory(tenant_id, record)
    return record


def version_history(store, tenant_id: str, memory_id: str) -> list[dict]:
    """Version tracking (S103): walk the supersedes chain from
    the newest record back to the first version."""
    out = []
    current = store.get_memory(tenant_id, memory_id)
    seen = set()
    while current is not None and current["memory_id"] not in seen:
        seen.add(current["memory_id"])
        out.append(current)
        nxt = current.get("supersedes")
        current = store.get_memory(tenant_id, nxt) if nxt else None
    return out


# ------------------------------------------------------------ expiry
def process_expiry(store, tenant_id: str,
                   at: datetime | None = None) -> list[dict]:
    """Expiration processing (S106): mark every overdue active
    record expired and return them. Per-entry expires_at wins;
    class TTLs were already applied at retain time."""
    at = at or _now()
    out = []
    for m in _all_facts(store, tenant_id):
        if m["status"] == "active" and _is_expired(m, at):
            m["status"] = "expired"
            m["updated_at"] = now_iso()
            store.put_memory(tenant_id, m)
            out.append(m)
    if out:
        _INDEX_CACHE.pop(tenant_id, None)
    return out


# ------------------------------------------------------------ contradiction
def _polarity_cues(text: str) -> set[str]:
    tokens = set(_TERM_RE.findall(text.lower()))
    cues = set()
    for tok in tokens:
        if tok in _NEGATIONS:
            cues.add("neg:" + tok)
        if tok in _ANTONYMS:
            cues.add("ant:" + _ANTONYMS[tok])
        if tok in set(_ANTONYMS.values()):
            cues.add("ant-value:" + tok)
    return cues


def find_contradiction_candidates(store, tenant_id: str, scope: str,
                                  scope_ref: str, topic: str,
                                  fact: str) -> list[dict]:
    """Contradiction candidate detection (S107): same normalized
    key + overlapping terms + a polarity flip (a negation or an
    antonym pair). Returns candidates, never a verdict — the
    caller surfaces them and a human decides."""
    key = normalize_key(topic)
    new_tokens = set(_TERM_RE.findall(fact.lower()))
    new_cues = _polarity_cues(fact)
    out = []
    for m in _all_facts(store, tenant_id):
        if m["status"] != "active":
            continue
        if m["scope"] != scope or m["scope_ref"] != scope_ref:
            continue
        if normalize_key(m.get("topic", "")) != key:
            continue
        if _norm_fact(m["fact"]) == _norm_fact(fact):
            continue
        old_tokens = set(_TERM_RE.findall(m["fact"].lower()))
        overlap = len(new_tokens & old_tokens)
        if overlap == 0:
            continue
        old_cues = _polarity_cues(m["fact"])
        flipped = bool((new_cues & {"ant-value:" + v for v in
                                    _ANTONYMS.values()})
                       or (old_cues ^ new_cues))
        # A polarity flip on a shared topic, or a direct antonym
        # swap (prefers X vs dislikes X), is a candidate.
        if flipped or (new_cues and old_cues
                       and not (new_cues & old_cues)):
            out.append({"existing": m,
                        "reason": "same topic with conflicting "
                                  "wording — check which is current",
                        "shared_terms": sorted(new_tokens & old_tokens)})
    return out


def contradict(store, tenant_id: str, user_id: str, scope: str,
               scope_ref: str, topic: str, fact: str,
               **kwargs) -> dict:
    """Store a fact that contradicts stored ones: the old records
    become "contested" (kept, visible, never silently overwritten)
    and the new record names them. Use retain() for ordinary
    updates; use this path when new evidence disagrees."""
    candidates = find_contradiction_candidates(
        store, tenant_id, scope, scope_ref, topic, fact)
    record = retain(store, tenant_id, user_id, scope, scope_ref,
                    topic, fact, _allow_supersede=False, **kwargs)
    if candidates:
        contested = []
        for cand in candidates:
            old = cand["existing"]
            if old["memory_id"] == record["memory_id"]:
                continue
            old = store.get_memory(tenant_id, old["memory_id"]) or old
            if old.get("status") == "active":
                old["status"] = "contested"
                old["updated_at"] = now_iso()
                store.put_memory(tenant_id, old)
            contested.append(old["memory_id"])
        record["contradicts"] = contested
        store.put_memory(tenant_id, record)
    return record


def list_conflicts(store, tenant_id: str) -> list[dict]:
    """Every contested record plus the record that contested it,
    so conflicts surface instead of hiding in the store."""
    facts = _all_facts(store, tenant_id)
    by_id = {m["memory_id"]: m for m in facts}
    out = []
    for m in facts:
        if m["status"] == "contested":
            challengers = [c for c in facts
                           if m["memory_id"] in c.get("contradicts", [])]
            out.append({"contested": m, "challengers": challengers,
                        "note": "These two records disagree. Keep "
                                "the one that is still true and "
                                "delete or correct the other."})
    return out


# ------------------------------------------------------------ scoped read + retrieval
def read_for(store, tenant_id: str, scope: str, scope_ref: str,
             role: str = "owner", user_id: str = "") -> list[dict]:
    """Scope enforcement at read time (B7): exact scope +
    scope_ref match only. A mission-scoped record never leaks
    into another mission's context; a project record never
    crosses projects. Tenant isolation is the store's own
    tenant_id boundary on top of this."""
    if scope not in SCOPES:
        raise ValueError(f"unknown long-term memory scope: {scope}")
    out = []
    for m in _all_facts(store, tenant_id):
        if m["status"] != "active" or _is_expired(m):
            continue
        if m["scope"] != scope:
            continue
        # Legacy tenant-wide user facts (empty scope_ref) are
        # workspace knowledge, readable by tenant members.
        if scope == "user" and m["scope_ref"] == "":
            if not can_read(role, user_id, m):
                continue
            out.append(m)
            continue
        if m["scope_ref"] != scope_ref:
            continue
        if not can_read(role, user_id, m):
            continue
        out.append(m)
        _log_access(tenant_id, m["memory_id"], "read", role)
    out.sort(key=lambda m: m["created_at"])
    return out


def _tfidf_index(records: list[dict]) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    docs = {}
    df: dict[str, int] = {}
    for rec in records:
        tokens = _TERM_RE.findall(
            f"{rec.get('topic', '')} {rec['fact']}".lower())
        counts: dict[str, int] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0) + 1
        for tok in counts:
            df[tok] = df.get(tok, 0) + 1
        docs[rec["memory_id"]] = counts
    n = max(1, len(records))
    idf = {tok: math.log((n + 1) / (c + 1)) + 1.0
           for tok, c in df.items()}
    vecs = {}
    for mid, counts in docs.items():
        total = sum(counts.values()) or 1
        vec = {tok: (c / total) * idf[tok] for tok, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vecs[mid] = {tok: v / norm for tok, v in vec.items()}
    return vecs, idf


def _cosine(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    return sum(v * vec_b.get(tok, 0.0) for tok, v in vec_a.items())


def _index_fingerprint(records: list[dict]) -> str:
    raw = "\n".join(f"{m['memory_id']}|{m.get('updated_at', '')}"
                    for m in sorted(records, key=lambda m: m["memory_id"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def retrieve(store, tenant_id: str, query: str, scope: str,
             scope_ref: str, *, role: str = "owner", user_id: str = "",
             limit: int = 6, per_topic_cap: int = 2) -> list[dict]:
    """Retrieval beyond FTS (M2): TF-IDF/cosine over the
    scope-allowed records, then a reranking pass (reliability
    weight, exact-phrase bonus, topic bonus). Scores are
    min-max normalized to 0..1 across the result set (S111), and
    no single topic may take more than per_topic_cap slots
    (diversity, S112). Each result carries a plain-words
    explanation of why it was retrieved (B8) and its reliability
    label (B13). The in-process index is rebuilt whenever its
    fingerprint changes (stale-index detection, S113)."""
    records = read_for(store, tenant_id, scope, scope_ref, role, user_id)
    if not records or not query.strip():
        return []
    # Stale-index detection (S113): the cache key is the
    # fingerprint of the exact record set; any write, expiry, or
    # deletion changes it and forces a rebuild.
    fingerprint = _index_fingerprint(records)
    cached = _INDEX_CACHE.get(tenant_id)
    if cached is not None and cached[0] == fingerprint:
        cached_vecs, idf = cached[1], cached[2]
    else:
        cached_vecs, idf = _tfidf_index(records)
        _INDEX_CACHE[tenant_id] = (fingerprint, cached_vecs, idf)
    query_tokens = _TERM_RE.findall(query.lower())
    if not query_tokens:
        return []
    q_counts: dict[str, int] = {}
    for tok in query_tokens:
        q_counts[tok] = q_counts.get(tok, 0) + 1
    q_total = sum(q_counts.values())
    q_vec = {tok: (c / q_total) * idf.get(tok, 1.0)
             for tok, c in q_counts.items()}
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    q_vec = {tok: v / q_norm for tok, v in q_vec.items()}
    scored = []
    for rec in records:
        doc_vec = cached_vecs.get(rec["memory_id"], {})
        cosine = _cosine(q_vec, doc_vec)
        if cosine <= 0:
            continue
        reliability = rec.get("reliability", "true_at_a_time")
        weight = _RELIABILITY_WEIGHT.get(reliability, 0.5)
        phrase_bonus = 0.1 if query.lower().strip() in rec["fact"].lower() else 0.0
        topic_bonus = 0.1 if any(tok in rec.get("topic", "").lower()
                                 for tok in query_tokens) else 0.0
        score = 0.6 * cosine + 0.25 * weight + phrase_bonus + topic_bonus
        matched = sorted({tok for tok in query_tokens
                          if tok in doc_vec})
        scored.append((score, rec, matched))
    if not scored:
        return []
    top = max(s for s, _, _ in scored)
    bottom = min(s for s, _, _ in scored)
    span = top - bottom
    # Equal scores (including a single result) normalize to 1.0:
    # they are all equally the top, not zero.
    # Stable order: normalized score desc, then memory_id asc.
    scored.sort(key=lambda item: (-item[0], item[1]["memory_id"]))
    out = []
    per_topic: dict[str, int] = {}
    for score, rec, matched in scored:
        topic_key = rec.get("topic", "")
        if per_topic.get(topic_key, 0) >= per_topic_cap:
            continue
        per_topic[topic_key] = per_topic.get(topic_key, 0) + 1
        item = dict(rec)
        item["retrieval_score"] = 1.0 if span == 0 else round(
            (score - bottom) / span, 4)
        item["matched_terms"] = matched
        item["explanation"] = (
            f"Matched the words {', '.join(matched) or 'in your question'}; "
            f"recorded as {RELIABILITY_LABELS.get(rec.get('reliability', ''), '')}; "
            f"from {rec.get('provenance', {}).get('origin', 'an earlier record')}.")
        out.append(item)
        _log_access(tenant_id, rec["memory_id"], "retrieved", role)
        if len(out) >= limit:
            break
    return out


def render_memory_block(results: list[dict]) -> str:
    """Plain-words rendering. The reliability label is part of
    every line, so a model guess never reads with the authority
    of a verified result; a confidence number, when shown, is
    always followed by the not-a-probability note."""
    if not results:
        return ""
    lines = ["## Remembered notes (from long-term memory)"]
    for rec in results:
        label = rec.get("reliability_label") or RELIABILITY_LABELS.get(
            rec.get("reliability", ""), "")
        conf = rec.get("confidence")
        conf_txt = ""
        if conf is not None:
            conf_txt = f" · confidence {conf} ({CONFIDENCE_NOTE})"
        lines.append(
            f"- [{label} · {rec.get('scope', '')} · "
            f"{rec.get('topic', '')} · "
            f"recorded {str(rec.get('created_at', ''))[:10]}"
            f"{conf_txt}] {rec['fact']}")
    return "\n".join(lines)


# ------------------------------------------------------------ deletion propagation
def delete_fact(store, tenant_id: str, memory_id: str,
                *, confirm: bool = False) -> dict:
    """Deletion propagation (B9, S115, S116 — TOP-15 #13).
    confirm=True is mandatory (deletion confirmation); without
    it nothing is touched. With it: the row is deleted; every
    derived record (derived_from naming it, or naming it in
    source_memory_ids) is marked "invalidated" so summaries,
    cached answers, and derived memories built from it stop
    being served; and the tenant's retrieval cache is cleared.
    Returns a report of exactly what happened, so the caller
    can prove propagation instead of trusting it."""
    if not confirm:
        raise DeletionNotConfirmed(
            "Deleting a memory needs explicit confirmation: pass "
            "confirm=True. Nothing was deleted.")
    record = store.get_memory(tenant_id, memory_id)
    if record is None:
        return {"deleted": False, "memory_id": memory_id,
                "invalidated": [], "cache_cleared": False}
    store.delete_memory(tenant_id, memory_id)
    invalidated = []
    for m in store.list_memories(tenant_id):
        derived = list(m.get("derived_from") or [])
        sources = list(m.get("source_memory_ids") or [])
        if memory_id in derived or memory_id in sources:
            if m.get("status") in ("active", "proposed"):
                m["status"] = "invalidated"
                m["invalidated_because"] = memory_id
                m["updated_at"] = now_iso()
                store.put_memory(tenant_id, m)
                invalidated.append(m["memory_id"])
    _INDEX_CACHE.pop(tenant_id, None)
    _log_access(tenant_id, memory_id, "deleted", "owner")
    return {"deleted": True, "memory_id": memory_id,
            "invalidated": invalidated, "cache_cleared": True}


# ------------------------------------------------------------ usefulness
def record_use(store, tenant_id: str, memory_id: str,
               helped: bool) -> dict | None:
    """Usefulness tracking (B12): every retrieval that is
    actually used (or not) is counted on the record. A record
    that never helps is reported by usefulness_report(), never
    silently deleted by this function."""
    record = store.get_memory(tenant_id, memory_id)
    if record is None:
        return None
    record["use_count"] = int(record.get("use_count", 0)) + 1
    if helped:
        record["helpful_count"] = int(record.get("helpful_count", 0)) + 1
    record["last_used_at"] = now_iso()
    store.put_memory(tenant_id, record)
    return record


def usefulness_report(store, tenant_id: str,
                      unused_days: int = 30) -> dict:
    """Memories that never helped, and memories never used at
    all past unused_days, reported for pruning. Reporting only:
    nothing here deletes anything."""
    cutoff = _now() - timedelta(days=unused_days)
    never_used = []
    never_helpful = []
    for m in _all_facts(store, tenant_id):
        if m["status"] != "active":
            continue
        created = _parse_ts(m.get("created_at", "")) or _now()
        if int(m.get("use_count", 0)) == 0 and created <= cutoff:
            never_used.append(m)
        elif (int(m.get("use_count", 0)) > 0
              and int(m.get("helpful_count", 0)) == 0):
            never_helpful.append(m)
    return {"never_used": never_used, "never_helpful": never_helpful,
            "note": "Review these for pruning; nothing was deleted."}


# ------------------------------------------------------------ integrity / retention / size / backup
def verify_integrity(store, tenant_id: str) -> dict:
    """Storage-integrity checking (S114): recompute each
    record's content hash and flag any record whose stored hash
    no longer matches (tampered or corrupted rows)."""
    ok, flagged = [], []
    for m in _all_facts(store, tenant_id):
        expected = m.get("fact_hash")
        if expected is None:
            continue  # legacy v21 rows predate hashes; not flagged
        actual = _fact_hash(m["scope"], m["scope_ref"],
                            m.get("topic", ""), m["fact"])
        (ok if actual == expected else flagged).append(m["memory_id"])
    return {"checked": len(ok) + len(flagged), "ok": ok,
            "flagged": flagged}


def enforce_retention(store, tenant_id: str) -> list[dict]:
    """Retention enforcement (S119): active records older than
    their class retention limit with zero uses are marked
    "retention_pruned" and reported. Pruning is a status change
    on the record (kept for audit), not a silent row delete."""
    now = _now()
    pruned = []
    for m in _all_facts(store, tenant_id):
        if m["status"] != "active" or int(m.get("use_count", 0)) > 0:
            continue
        limit = RETENTION_DAYS.get(m.get("reliability",
                                         "true_at_a_time"), 180)
        created = _parse_ts(m.get("created_at", "")) or now
        if now - created > timedelta(days=limit):
            m["status"] = "retention_pruned"
            m["updated_at"] = now_iso()
            store.put_memory(tenant_id, m)
            pruned.append(m)
    if pruned:
        _INDEX_CACHE.pop(tenant_id, None)
    return pruned


def size_accounting(store, tenant_id: str) -> dict:
    """Size accounting (S118): bytes of stored fact text, by
    scope, plus a total. Counts characters of fact + topic,
    which is what the context budget actually pays for."""
    by_scope: dict[str, int] = {}
    total = 0
    for m in _all_facts(store, tenant_id):
        if m["status"] != "active":
            continue
        size = len(m["fact"]) + len(m.get("topic", ""))
        by_scope[m["scope"]] = by_scope.get(m["scope"], 0) + size
        total += size
    return {"by_scope": by_scope, "total_chars": total}


def verify_backup(store, tenant_id: str,
                  backup_rows: list[dict]) -> dict:
    """Backup verification (S120): compare a supplied export
    (rows with memory_id + fact_hash) against the live store.
    This verifies an export the operator supplies; it does not
    claim a backup system exists. Missing, changed, and extra
    rows are all reported."""
    live = {m["memory_id"]: m for m in _all_facts(store, tenant_id)}
    backup = {r["memory_id"]: r for r in backup_rows}
    missing = sorted(set(live) - set(backup))
    extra = sorted(set(backup) - set(live))
    changed = sorted(
        mid for mid in set(live) & set(backup)
        if live[mid].get("fact_hash") is not None
        and backup[mid].get("fact_hash") is not None
        and live[mid]["fact_hash"] != backup[mid]["fact_hash"])
    return {"live": len(live), "backup": len(backup),
            "missing_from_backup": missing, "extra_in_backup": extra,
            "changed": changed,
            "ok": not missing and not extra and not changed}
