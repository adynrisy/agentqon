"""AgentQon — self-evolution (v22, R2).

The harness learns from its own outcomes WITHOUT ever modifying
itself:

- Every agent session and mission that ends (completed / failed /
  cancelled) writes a STRUCTURED LESSON into the memory layer (the
  same `memories` table the curated fact layer uses — lessons are
  records with kind="lesson", kept alongside facts, never superseded:
  each outcome is an event, not a belief to overwrite).
- Failures are classified into a small stable taxonomy so patterns
  are countable: provider_rate_limit, provider_error, tool_error,
  approval_denied, output_format, planning, timeout, other.
- The lessons MEASURABLY influence later picks: agent-mode model
  selection deprioritizes a model/connection with repeated recent
  failures in favour of a clean alternative (see pick_with_lessons).
- GET /v1/evolve/report renders recent failure patterns, the lessons,
  and SAFE TUNINGS — applied ones are runtime parameters only
  (provider pacing learned from 429s, model deprioritization);
  anything beyond that is listed as PROPOSED and requires a human
  release. The report states this in its own payload.

Hard line, also stated in the report: this system NEVER modifies its
own code, safeguards, or permission rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .domain import new_id, now_iso
from .memory import redact_text

FAILURE_CATEGORIES = (
    "provider_rate_limit", "provider_error", "tool_error",
    "approval_denied", "output_format", "planning", "timeout", "other",
)

# A model with this many failure lessons inside the window is
# deprioritized for agent-mode picks; each failure adds its count as
# a penalty, so "repeatedly failing" outweighs "failed once".
LESSON_WINDOW_DAYS = 7
DEPRIORITIZE_MIN_FAILURES = 2

SELF_MODIFICATION_STATEMENT = (
    "This system never modifies its own code, safeguards, or "
    "permission rules. 'Applied' tunings are runtime parameters "
    "learned from outcomes (provider pacing, model "
    "deprioritization). Everything else is listed as proposed only "
    "and requires a human-reviewed release.")


# ------------------------------------------------------------- classify
_GATEWAY_CATEGORY_MAP = {
    "rate_limited": "provider_rate_limit",
    "timeout": "timeout",
    "auth_failed": "provider_error",
    "provider_unavailable": "provider_error",
    "bad_request": "provider_error",
    "unsupported": "provider_error",
    "blocked": "other",
    "unknown": "provider_error",
}


def classify_gateway_error(exc) -> str:
    """Map a GatewayError (anything with a .category) onto the
    lesson taxonomy."""
    category = getattr(exc, "category", None)
    if category:
        return _GATEWAY_CATEGORY_MAP.get(category, "provider_error")
    return classify_failure_text(str(exc))


def classify_failure_text(text: str) -> str:
    """Classify a free-form failure reason (worker summaries, runtime
    errors) into the lesson taxonomy. Order matters: rate limiting
    is a provider_error subclass worth its own bucket, and a denial
    is not a provider problem."""
    t = (text or "").lower()
    if "429" in t or "rate_limited" in t or "rate limit" in t \
            or "rate-limited" in t:
        return "provider_rate_limit"
    if "denied" in t or "approval_denied" in t:
        return "approval_denied"
    if "timed out" in t or "timeout" in t or "deadline exceeded" in t:
        return "timeout"
    if "valid json" in t or "no deliverable" in t or "output failed" in t \
            or "output_format" in t or "did not return" in t \
            or "no usable action" in t:
        return "output_format"
    if "tool_error" in t or "tool call failed" in t or "tool execution" in t:
        return "tool_error"
    if "model_not_connected" in t or "no eligible model" in t \
            or "no model is connected" in t or "gateway returned http" in t \
            or "gateway unreachable" in t or "gateway timed out" in t \
            or "byok connection call failed" in t or "auth_failed" in t \
            or "connection call failed" in t or "provider" in t:
        return "provider_error"
    if "plan" in t or "step cap" in t or "loop" in t:
        return "planning"
    return "other"


# -------------------------------------------------------------- lessons
def record_lesson(store, tenant_id: str, *, subject_type: str, subject: str,
                  outcome: str, category: str, cause: str,
                  evidence_id: str, source_kind: str,
                  source_id: str) -> dict:
    """Write one structured outcome lesson to the memory layer.

    subject_type: "model" | "agent" | "tool" | "mission" | "session"
    outcome:      "completed" | "failed" | "cancelled"
    category:     one of FAILURE_CATEGORIES, or "none" for a clean
                  completion.
    The record lives in the memories table next to curated facts;
    `fact` carries the human one-liner (redacted), the structured
    fields ride alongside for the report and the ledger influence."""
    if category != "none" and category not in FAILURE_CATEGORIES:
        category = "other"
    cause = redact_text(str(cause).strip())[:300] or "(no cause recorded)"
    subject = str(subject)[:160]
    fact = (f"[{source_kind} {outcome}] {subject_type} {subject}: "
            f"{category} — {cause}")
    record = {
        "memory_id": new_id("mem"), "kind": "lesson",
        # Lessons share the memory layer's table but live under their
        # own scope: they are outcome evidence, not curated beliefs,
        # and must never surface in the user/agent/session fact views.
        "scope": "lesson", "scope_ref": source_id,
        "topic": f"lesson:{category}",
        "fact": fact, "source": "evolve",
        "mission_id": source_id if source_kind == "mission" else None,
        "actor": "", "status": "active", "supersedes": None,
        "subject_type": subject_type, "subject": subject,
        "outcome": outcome, "category": category, "cause": cause,
        "evidence_id": evidence_id, "source_kind": source_kind,
        "source_id": source_id,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    store.put_memory(tenant_id, record)
    return record


def list_lessons(store, tenant_id: str, limit: int | None = None) -> list[dict]:
    """All lesson records for the tenant, newest first."""
    out = [m for m in store.list_memories(tenant_id)
           if m.get("kind") == "lesson"]
    out.sort(key=lambda m: m["created_at"], reverse=True)
    return out[:limit] if limit else out


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def model_failure_counts(store, tenant_id: str,
                         days: int = LESSON_WINDOW_DAYS) -> dict[str, int]:
    """Recent failure counts per model id — the ledger the picker
    consults. Only failed outcomes with a model subject count;
    completions never penalize."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts: dict[str, int] = {}
    for lesson in list_lessons(store, tenant_id):
        if lesson.get("outcome") != "failed":
            continue
        if lesson.get("subject_type") != "model":
            continue
        ts = _parse_ts(lesson.get("created_at", ""))
        if ts is not None and ts < cutoff:
            continue
        subject = lesson.get("subject") or ""
        if subject:
            counts[subject] = counts.get(subject, 0) + 1
    return counts


def pick_with_lessons(settings, store, tenant_id: str, entries: list) -> tuple:
    """Choose a catalog entry for agent-mode work, lesson-aware.

    Same preference order as modelstate._pick_catalog_entry (the
    configured default model, then a local-endpoint connection, then
    a chat-advertised entry, then the first) — EXCEPT a model with
    DEPRIORITIZE_MIN_FAILURES or more recent failure lessons is
    demoted below every clean alternative. With no lessons (or no
    clean alternative) the pick is exactly _pick_catalog_entry's."""
    from .modelstate import _pick_catalog_entry
    try:
        counts = model_failure_counts(store, tenant_id) if store else {}
    except Exception:
        counts = {}
    if not counts:
        return _pick_catalog_entry(settings, entries)
    wanted = (settings.llm_model or "").strip()

    def pref_rank(pair) -> int:
        entry, conn = pair
        mid = entry.get("model_id") or ""
        if wanted and (mid == wanted or mid.endswith("/" + wanted)):
            return 0
        if conn.get("kind") == "local_endpoint":
            return 1
        if (entry.get("capabilities") or {}).get("chat") == "advertised":
            return 2
        return 3

    def penalty(pair) -> int:
        n = counts.get(pair[0].get("model_id") or "", 0)
        return n if n >= DEPRIORITIZE_MIN_FAILURES else 0

    ordered = sorted(enumerate(entries),
                     key=lambda t: (penalty(t[1]), pref_rank(t[1]), t[0]))
    return ordered[0][1]


# --------------------------------------------------------------- report
def _applied_tunings(store, tenant_id: str, counts: dict[str, int]) -> list[dict]:
    applied = []
    for model, n in sorted(counts.items()):
        if n >= DEPRIORITIZE_MIN_FAILURES:
            applied.append({
                "kind": "model_deprioritization", "model": model,
                "recent_failures": n,
                "detail": (f"{model} is deprioritized for agent-mode "
                           f"picks after {n} recent failure lessons; a "
                           "clean alternative is preferred while one "
                           "exists.")})
    try:
        from .gateway_client import pacing_snapshot
        for snap in pacing_snapshot():
            if snap.get("gap_s", 0) > 0:
                applied.append({
                    "kind": "provider_pacing",
                    "connection_id": snap["connection_id"],
                    "gap_s": snap["gap_s"],
                    "detail": ("Calls to this connection are paced "
                               f"({snap['gap_s']}s minimum gap), "
                               "learned from its HTTP 429 responses; "
                               "the gap is forgotten after a quiet "
                               "window.")})
    except Exception:
        pass  # pacing state is best-effort observability
    return applied


def _proposed_tunings(patterns: list[dict]) -> list[dict]:
    by_cat: dict[str, int] = {}
    for p in patterns:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + p["count"]
    proposed = []
    if by_cat.get("output_format", 0) >= 2:
        proposed.append({
            "kind": "proposal", "category": "output_format",
            "detail": ("Repeated output-format failures: prefer "
                       "models advertising structured output for "
                       "agent sessions, or tighten the action-schema "
                       "instructions. Proposed only — needs a human "
                       "release.")})
    if by_cat.get("provider_rate_limit", 0) >= 2:
        proposed.append({
            "kind": "proposal", "category": "provider_rate_limit",
            "detail": ("Repeated rate limiting: reserve free-tier "
                       "connections for chat and run long agent "
                       "sessions on higher-quota connections. "
                       "Proposed only — connection choice stays with "
                       "the user.")})
    if by_cat.get("timeout", 0) >= 2:
        proposed.append({
            "kind": "proposal", "category": "timeout",
            "detail": ("Repeated timeouts: consider smaller step "
                       "budgets per session or a longer per-call "
                       "timeout. Proposed only — no setting changes "
                       "itself.")})
    return proposed


def build_report(store, tenant_id: str,
                 days: int = LESSON_WINDOW_DAYS,
                 recent_limit: int = 20) -> dict:
    """The evolve report: recent failure patterns (counts by category
    + subject), the lessons behind them, and the safe tunings —
    applied (runtime parameters) vs proposed (human decision)."""
    lessons = list_lessons(store, tenant_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for lesson in lessons:
        ts = _parse_ts(lesson.get("created_at", ""))
        if ts is not None and ts < cutoff:
            continue
        recent.append(lesson)
    grouped: dict[tuple, int] = {}
    for lesson in recent:
        if lesson.get("outcome") != "failed":
            continue
        key = (lesson.get("category", "other"),
               lesson.get("subject_type", ""), lesson.get("subject", ""))
        grouped[key] = grouped.get(key, 0) + 1
    patterns = [{"category": c, "subject_type": st, "subject": s, "count": n}
                for (c, st, s), n in grouped.items()]
    patterns.sort(key=lambda p: (-p["count"], p["category"], p["subject"]))
    counts = model_failure_counts(store, tenant_id, days=days)
    return {
        "tenant_id": tenant_id,
        "window_days": days,
        "generated_at": now_iso(),
        "failure_patterns": patterns,
        "lessons": [{
            "subject_type": l.get("subject_type"),
            "subject": l.get("subject"),
            "outcome": l.get("outcome"),
            "category": l.get("category"),
            "cause": l.get("cause"),
            "evidence_id": l.get("evidence_id"),
            "source_kind": l.get("source_kind"),
            "source_id": l.get("source_id"),
            "created_at": l.get("created_at"),
        } for l in recent[:recent_limit]],
        "tunings": {
            "applied": _applied_tunings(store, tenant_id, counts),
            "proposed": _proposed_tunings(patterns),
        },
        "safety": {
            "self_modification": "never",
            "statement": SELF_MODIFICATION_STATEMENT,
        },
    }
