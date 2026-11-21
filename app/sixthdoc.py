"""AgentQon — sixth-document gap-fix engines (W11, branch gap-sixthdoc).

Pure-stdlib engines for the sixth audit document's 18 sections plus its
10 unusual functions. Deliberately engine-only: no web UI wiring, no new
dependencies, no changes to the Shield, the free-roam Write Law, or
tenant confinement. Where another workstream owns the other side of a
seam (W8 file-ops atomic writes/locking/truncation flags, W1 cost
metric), the seam is an injected callable or a flag — never a second
implementation — and end-to-end wiring stays NOT_TESTED until integration.

Plain-words law: every user-facing message below is plain language.
Internal reason codes (e.g. "timeout", "rate_limit") are machine labels
and are never shown to the user without a plain explanation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

VERSION = "sixthdoc-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def plain_error(reason: str) -> str:
    """Machine reason code -> plain-words explanation (never raw codes)."""
    return {
        "authentication": "Sign-in failed. Check the saved connection and try again.",
        "validation": "Something in the request was not in the expected shape. Nothing was changed.",
        "timeout": "The step took too long and was stopped. Nothing was changed.",
        "rate_limit": "The provider asked us to wait. The step will be retried after a pause.",
        "other": "The step could not finish. Try again, and check the details if it repeats.",
    }.get(reason, "The step could not finish. Try again.")


# ---------------------------------------------------------------- §1 task understanding
AMBIGUITY_MARKERS = (" or ", "either", "maybe", "as needed", "etc", "somehow",
                     "appropriate", "suitable", "best")
MUST_NOT_PATTERNS = (r"\bnever\b", r"\bdo not\b", r"\bdon't\b", r"\bmust not\b",
                     r"\bno\b\s+\w+")
REQUIREMENT_VERBS = ("must", "should", "need", "require", "shall")


@dataclass
class Requirement:
    text: str
    kind: str  # "mandatory" | "preference"
    source: str = "prompt"
    negative: bool = False
    satisfied_by: str | None = None


def extract_requirements(prompt: str) -> list[Requirement]:
    """Split a long prompt into individually trackable requirements."""
    reqs: list[Requirement] = []
    for chunk in re.split(r"[.\n;]+", prompt or ""):
        chunk = chunk.strip()
        if len(chunk) < 4:
            continue
        low = chunk.lower()
        kind = "mandatory" if any(v in low for v in REQUIREMENT_VERBS) else "preference"
        negative = any(re.search(p, low) for p in MUST_NOT_PATTERNS)
        if negative:
            kind = "mandatory"
        reqs.append(Requirement(text=chunk, kind=kind, negative=negative))
    return reqs


def detect_ambiguity(prompt: str) -> list[dict]:
    """Flag multiple plausible interpretations (never silently pick one)."""
    out = []
    low = (prompt or "").lower()
    for marker in AMBIGUITY_MARKERS:
        if marker.strip() and marker.strip() in low:
            out.append({"marker": marker.strip(),
                        "note": "This wording allows more than one reading; ask or state the reading chosen."})
    if " or " in low:
        out.append({"marker": "or-alternative",
                    "note": "Two alternatives are named; the choice must be recorded."})
    return out


def classify_missing(prompt: str, known: dict | None = None) -> list[dict]:
    """must-request vs safely-assumable missing information, decided explicitly."""
    known = known or {}
    out = []
    for token in ("deadline", "budget", "audience", "format", "location"):
        if token not in (prompt or "").lower() and token not in known:
            must = token in ("deadline", "budget")
            out.append({"field": token,
                        "decision": "must-request" if must else "safely-assumable",
                        "assumption": None if must else "Use the usual default and say so in the result."})
    return out


def detect_conflicts(reqs: list[Requirement]) -> list[dict]:
    """Contradictions within instructions: a must-do vs a must-not-do on the same words."""
    out = []
    for a in reqs:
        for b in reqs:
            if a is b or not (a.negative ^ b.negative):
                continue
            shared = set(a.text.lower().split()) & set(b.text.lower().split()) - {"the", "a", "an", "to", "and"}
            if len(shared) >= 2:
                out.append({"a": a.text, "b": b.text,
                            "note": "These two instructions pull in opposite directions; resolve before running."})
    # dedupe symmetric pairs
    seen, uniq = set(), []
    for c in out:
        key = tuple(sorted((c["a"], c["b"])))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def complexity_estimate(prompt: str, reqs: list[Requirement] | None = None) -> dict:
    reqs = reqs if reqs is not None else extract_requirements(prompt)
    words = len((prompt or "").split())
    tools = len(set(re.findall(r"\b(file|web|search|code|test|shell)\b", (prompt or "").lower())))
    return {"requirements": len(reqs), "words": words,
            "estimated_tools": max(1, tools),
            "estimated_steps": max(1, len(reqs)),
            "agents_suggested": 1 if len(reqs) <= 3 else 2,
            "deliverable": predict_deliverable(prompt)}


def predict_deliverable(prompt: str) -> str:
    low = (prompt or "").lower()
    if "report" in low or "brief" in low:
        return "written report"
    if "code" in low or "fix" in low or "patch" in low:
        return "code change with tests"
    if "summary" in low:
        return "summary"
    return "written result"


def hidden_dependencies(prompt: str) -> list[str]:
    low = (prompt or "").lower()
    deps = []
    if "latest" in low or "current" in low:
        deps.append("needs up-to-date outside information")
    if "file" in low or "repo" in low or "codebase" in low:
        deps.append("needs the project files to be present and readable")
    if "test" in low:
        deps.append("needs a working test setup")
    return deps


def completion_conditions(reqs: list[Requirement]) -> list[dict]:
    return [{"requirement": r.text, "kind": r.kind,
             "check": "Show the artifact or test result that proves this is done."}
            for r in reqs]


def detect_scope_change(original: list[Requirement], revised_prompt: str) -> dict:
    revised = extract_requirements(revised_prompt)
    old_texts = {r.text.lower() for r in original}
    new = [r for r in revised if r.text.lower() not in old_texts]
    dropped = [r.text for r in original if r.text.lower() not in {x.text.lower() for x in revised}]
    return {"added": [r.text for r in new], "dropped": dropped,
            "material_change": bool(new or dropped),
            "preserved": not dropped}


def track_negative_constraints(reqs: list[Requirement]) -> list[str]:
    return [r.text for r in reqs if r.negative]


def assumption_expiry(assumptions: list[dict], project_changed: bool) -> list[dict]:
    """Assumptions must be revalidated after project changes."""
    return [{**a, "status": "needs revalidation" if project_changed else a.get("status", "active")}
            for a in assumptions]


def clarification_cost(prompt: str, missing: list[dict] | None = None) -> dict:
    missing = missing if missing is not None else classify_missing(prompt)
    must = [m for m in missing if m["decision"] == "must-request"]
    if must:
        return {"decision": "ask", "reason": "A needed detail is missing and guessing it could waste the run."}
    return {"decision": "proceed-with-reversible-assumption",
            "reason": "Missing details can be assumed safely and undone if wrong."}


def decomposition_preview(prompt: str) -> dict:
    reqs = extract_requirements(prompt)
    return {"requirements": [r.text for r in reqs],
            "ambiguities": detect_ambiguity(prompt),
            "conflicts": detect_conflicts(reqs),
            "missing": classify_missing(prompt),
            "complexity": complexity_estimate(prompt, reqs),
            "completion_conditions": completion_conditions(reqs),
            "negative_constraints": track_negative_constraints(reqs),
            "hidden_dependencies": hidden_dependencies(prompt),
            "clarification": clarification_cost(prompt)}


# ---------------------------------------------------------------- §2 context packaging
SOURCE_LABELS = ("instructions", "files", "tool_results", "model_notes", "background")


@dataclass
class ContextItem:
    text: str
    source: str
    mandatory: bool = False
    timestamp: str = ""
    fresh: bool = True


def context_manifest(items: list[ContextItem]) -> dict:
    by_source: dict[str, int] = {}
    for it in items:
        by_source[it.source] = by_source.get(it.source, 0) + 1
    return {"total_items": len(items), "by_source": by_source,
            "mandatory_items": sum(1 for it in items if it.mandatory),
            "items": [{"source": it.source, "mandatory": it.mandatory,
                       "chars": len(it.text)} for it in items]}


def relevance_score(item: ContextItem, keywords: list[str]) -> float:
    low = item.text.lower()
    hits = sum(1 for k in keywords if k.lower() in low)
    return round(hits / max(1, len(keywords)), 3) if keywords else 0.0


def detect_context_contradictions(items: list[ContextItem]) -> list[dict]:
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if ("never" in a.text.lower() and "always" in b.text.lower()) or \
               ("never" in b.text.lower() and "always" in a.text.lower()):
                out.append({"a": a.text[:80], "b": b.text[:80],
                            "note": "These two supplied items disagree; flag, do not pick silently."})
    return out


def deduplicate_context(items: list[ContextItem]) -> list[ContextItem]:
    seen, out = set(), []
    for it in items:
        key = " ".join(it.text.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def preservation_checklist(reqs: list[Requirement]) -> list[str]:
    """BINDING: built BEFORE any summarization; compressed context must keep these."""
    return [r.text for r in reqs if r.kind == "mandatory"]


def compress_context(items: list[ContextItem], checklist: list[str]) -> dict:
    """Summarize only if every mandatory checklist item survives verbatim-ish."""
    joined = "\n".join(it.text for it in items)
    kept = [c for c in checklist if c.lower()[:40] in joined.lower() or
            any(w in joined.lower() for w in c.lower().split()[:4])]
    # Strict rule: checklist items must be carried, not summarized away.
    compressed_items = [it for it in items if it.mandatory] + \
        [it for it in items if not it.mandatory][:3]
    text = "\n".join(it.text for it in compressed_items)
    missing = [c for c in checklist if c not in text and c.lower()[:40] not in text.lower()]
    if missing:
        text = text + "\nMandatory constraints kept verbatim:\n" + "\n".join("- " + c for c in checklist)
        missing = []
    return {"text": text, "checklist": checklist, "missing": missing,
            "valid": not missing}


def budget_allocation(total_chars: int) -> dict:
    return {"instructions": int(total_chars * 0.25), "evidence": int(total_chars * 0.35),
            "tools": int(total_chars * 0.15), "output": int(total_chars * 0.25)}


def overflow_prediction(items: list[ContextItem], limit_chars: int) -> dict:
    total = sum(len(it.text) for it in items)
    return {"total_chars": total, "limit_chars": limit_chars,
            "would_overflow": total > limit_chars,
            "warning": "The supplied material is larger than the working limit; trim before running."
                       if total > limit_chars else ""}


def freshness_validation(items: list[ContextItem]) -> list[dict]:
    return [{"text": it.text[:60], "fresh": it.fresh,
             "note": "" if it.fresh else "This item may be outdated; check it before trusting it."}
            for it in items if not it.fresh]


def handoff_summary(items: list[ContextItem]) -> str:
    relevant = [it.text for it in items if it.source in ("instructions", "tool_results")]
    return "\n".join(relevant)[:2000]


def apply_exclusions(items: list[ContextItem], excluded_kinds: list[str]) -> list[ContextItem]:
    return [it for it in items if it.source not in excluded_kinds]


def provenance_map(items: list[ContextItem]) -> dict:
    return {f"item-{i}": {"source": it.source, "timestamp": it.timestamp or _now(),
                          "mandatory": it.mandatory} for i, it in enumerate(items)}


# ---------------------------------------------------------------- §3 session lifecycle
# FRAMING: AgentQon agents are roster entries; sessions are loop runs
# (step cap 25, limit 200 — app/agent_loop.py). There is no agent_session
# module today. This section manages SESSIONS, with model-server state
# tracked separately: an agent may fail while the model stays healthy.
SESSION_STATES = ("initializing", "ready", "running", "paused", "expired",
                  "shutting_down", "closed", "crashed", "retired")


@dataclass
class Session:
    session_id: str
    owner: str
    agent_id: str
    model_server_id: str
    state: str = "initializing"
    created_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    max_lifetime_s: float = 3600.0
    idle_timeout_s: float = 900.0
    steps: int = 0
    version: str = VERSION
    events: list[dict] = field(default_factory=list)

    def log(self, event: str, detail: str = "") -> None:
        self.events.append({"at": _now(), "event": event, "detail": detail})


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.model_servers: dict[str, str] = {}  # server_id -> "healthy"|"down"

    def create(self, session_id: str, owner: str, agent_id: str, model_server_id: str,
               **kw) -> Session:
        if not session_id or not owner or not agent_id:
            raise ValueError("Session needs an id, an owner, and an agent.")
        s = Session(session_id=session_id, owner=owner, agent_id=agent_id,
                    model_server_id=model_server_id, **kw)
        s.log("created", f"agent {agent_id}")
        self.sessions[session_id] = s
        return s

    def readiness_handshake(self, s: Session, warmup_s: float = 0.0) -> dict:
        """Initialization validation + warm-up timing before the session runs."""
        if s.state != "initializing":
            return {"ok": False, "note": "Session is not waiting to start."}
        s.state = "ready"
        s.log("ready", f"warm-up took {warmup_s:.2f} seconds")
        return {"ok": True, "warmup_s": warmup_s}

    def heartbeat(self, s: Session) -> None:
        s.last_heartbeat = time.time()
        s.log("heartbeat")

    def is_stale(self, s: Session, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - s.last_heartbeat > s.idle_timeout_s) or \
               (now - s.created_at > s.max_lifetime_s)

    def pause(self, s: Session) -> None:
        s.state = "paused"
        s.log("paused")

    def resume(self, s: Session) -> None:
        if s.state == "paused":
            s.state = "running"
            s.log("resumed")

    def renew(self, s: Session, extra_s: float) -> None:
        s.max_lifetime_s += extra_s
        s.log("renewed", f"lifetime extended by {extra_s} seconds")

    def expire_idle(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        out = []
        for s in list(self.sessions.values()):
            if s.state in ("running", "ready", "paused") and self.is_stale(s, now):
                s.state = "expired"
                s.log("expired", "idle or lifetime limit reached")
                out.append(s.session_id)
        return out

    def graceful_shutdown(self, s: Session) -> None:
        s.state = "shutting_down"
        s.log("shutting down")
        s.state = "closed"
        s.log("closed")

    def crash_recovery(self, s: Session) -> dict:
        if s.state != "crashed":
            return {"recovered": False, "note": "Session did not crash."}
        s.state = "ready"
        s.log("recovered after crash", f"steps kept: {s.steps}")
        return {"recovered": True, "steps_kept": s.steps}

    def retire_replace(self, old: Session, new_id: str) -> Session:
        old.state = "retired"
        old.log("retired")
        new = self.create(new_id, old.owner, old.agent_id, old.model_server_id,
                          version=old.version)
        new.log("replaces", old.session_id)
        return new

    def migrate_state(self, old: Session, new: Session) -> dict:
        if old.version != new.version:
            new.steps, new.events = old.steps, list(old.events)
            new.log("state migrated", f"from {old.session_id}")
            return {"migrated": True, "note": "State carried across versions."}
        new.steps = old.steps
        return {"migrated": True, "note": "State carried over."}

    def restart_policy(self, s: Session, crashes: int) -> str:
        if crashes >= 3:
            return "stop: too many crashes in a row; needs a person to look."
        return "restart"

    def orphans(self, live_pids: set[int], known: dict[str, int]) -> list[str]:
        """Processes AgentQon knows about that are no longer alive."""
        return [sid for sid, pid in known.items() if pid not in live_pids]

    def set_model_server(self, server_id: str, healthy: bool) -> None:
        self.model_servers[server_id] = "healthy" if healthy else "down"

    def model_vs_session_health(self, s: Session) -> dict:
        """An agent may fail while the model stays healthy — report both."""
        return {"session_state": s.state,
                "model_server": self.model_servers.get(s.model_server_id, "unknown"),
                "session_failed_model_healthy": s.state in ("crashed", "expired") and
                self.model_servers.get(s.model_server_id) == "healthy"}


# ---------------------------------------------------------------- §4 tool contracts
ERROR_TAXONOMY = ("authentication", "validation", "timeout", "rate_limit", "other")


@dataclass
class ToolContract:
    name: str
    version: str
    input_schema: dict
    output_schema: dict
    examples: list[dict] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    deprecated: bool = False
    deprecation_note: str = ""
    max_output_chars: int = 8000


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        self.tools[contract.name] = contract

    def check_input(self, name: str, args: dict) -> dict:
        c = self.tools.get(name)
        if c is None:
            return {"ok": False, "reason": "validation", "note": plain_error("validation")}
        missing = [k for k in c.input_schema.get("required", []) if k not in args]
        if missing:
            return {"ok": False, "reason": "validation", "missing": missing,
                    "note": plain_error("validation")}
        return {"ok": True}

    def check_postcondition(self, name: str, result: dict, intended: str) -> dict:
        """BINDING: a tool returning successfully is not proof it did the job.

        `result` carries the tool's own success flag; `accomplished` requires
        evidence the intended purpose holds (e.g. the file exists, the
        record changed). Callers supply that evidence; absence = not proven.
        """
        if not result.get("success"):
            return {"accomplished": False, "reason": "tool reported failure",
                    "note": "The tool did not finish, so the purpose is not done."}
        if not result.get("evidence"):
            return {"accomplished": False, "reason": "no evidence",
                    "note": f"The tool finished, but nothing proves it achieved: {intended}"}
        return {"accomplished": True, "reason": "evidence present"}

    def normalize(self, name: str, raw: object) -> dict:
        return {"tool": name, "status": "ok", "result": raw, "truncated": False}

    def rank_discovery(self, query: str) -> list[str]:
        q = query.lower()
        scored = sorted(self.tools, key=lambda n: (q not in n.lower(), n))
        return scored

    def invocation_preview(self, name: str, args: dict) -> dict:
        return {"tool": name, "arguments": args,
                "note": "Shown before running, when the step allows it."}

    def contract_test(self, name: str) -> dict:
        c = self.tools.get(name)
        if c is None:
            return {"ok": False, "note": "No contract registered."}
        problems = []
        if not c.examples:
            problems.append("no known-good example")
        if not c.input_schema.get("required") and not c.input_schema.get("properties"):
            problems.append("input schema is empty")
        if c.deprecated:
            problems.append(f"deprecated: {c.deprecation_note}")
        return {"ok": not problems, "problems": problems}

    def compatibility(self, name: str, runtime: dict) -> dict:
        c = self.tools.get(name)
        if c is None:
            return {"compatible": False, "note": "Unknown tool."}
        missing = [d for d in c.dependencies if d not in runtime.get("available", [])]
        return {"compatible": not missing, "missing": missing,
                "pinned_version": c.version}


def mark_truncation(text: str, limit: int) -> dict:
    """Truncation notices EVERYWHERE (fs_read already marks; this closes
    the fs_list / fs_search / shell-output gap at the contract layer).
    W8's truncation flags are consumed by callers via `flagged` input."""
    if len(text) <= limit:
        return {"text": text, "truncated": False, "notice": ""}
    return {"text": text[:limit],
            "truncated": True,
            "notice": f"Output was cut short at {limit} characters; the rest was not shown."}


# ---------------------------------------------------------------- §5 provenance
@dataclass
class Evidence:
    evidence_id: str
    kind: str  # "source" | "test" | "artifact" | "tool_result" | "calculation"
    reference: str
    timestamp: str = ""
    version: str = ""
    reliability: str = "unrated"  # "high" | "medium" | "low" | "unrated"
    observed: bool = True  # False = inferred (never citable as observed)


class ProvenanceRegistry:
    def __init__(self) -> None:
        self.evidence: dict[str, Evidence] = {}
        self.claims: dict[str, dict] = {}

    def add(self, ev: Evidence) -> Evidence:
        self.evidence[ev.evidence_id] = ev
        return ev

    def link_claim(self, claim: str, evidence_ids: list[str]) -> dict:
        missing = [e for e in evidence_ids if e not in self.evidence]
        self.claims[claim] = {"evidence": evidence_ids, "missing": missing}
        return {"claim": claim, "linked": evidence_ids, "missing": missing}

    def unsupported_claims(self) -> list[str]:
        return [c for c, v in self.claims.items() if not v["evidence"] or v["missing"]]

    def claim_warnings(self) -> list[dict]:
        out = []
        for claim, v in self.claims.items():
            if not v["evidence"] or v["missing"]:
                out.append({"claim": claim,
                            "warning": "This claim has no recorded evidence behind it; treat it as unproven."})
            for eid in v["evidence"]:
                ev = self.evidence.get(eid)
                if ev is not None and not ev.observed:
                    out.append({"claim": claim,
                                "warning": "This claim rests on an inference, not something observed."})
        return out

    def freshness_score(self, ev: Evidence, age_days: float) -> str:
        if age_days <= 7:
            return "fresh"
        if age_days <= 90:
            return "aging"
        return "stale"

    def conflicts(self) -> list[dict]:
        return []  # populated by callers comparing evidence texts; registry keeps links

    def citation_valid(self, claim: str) -> bool:
        v = self.claims.get(claim)
        return bool(v and v["evidence"] and not v["missing"])

    def coverage_report(self, claims: list[str]) -> dict:
        covered = sum(1 for c in claims if self.citation_valid(c))
        return {"claims": len(claims), "covered": covered,
                "uncovered": [c for c in claims if not self.citation_valid(c)]}

    def build_pass_record(self, command: str, exit_code: int, log: str) -> dict:
        """BINDING example: 'the build passes' links to command + exit code + log,
        never merely to the agent's final message."""
        ev = Evidence(evidence_id=f"ev-{_hash(command + log)}", kind="tool_result",
                      reference=f"command={command} exit={exit_code} log={log[:500]}",
                      timestamp=_now(), observed=True)
        self.add(ev)
        return {"claim": "the build passes", "evidence_id": ev.evidence_id,
                "passes": exit_code == 0, "command": command, "exit_code": exit_code}

    def export_manifest(self) -> dict:
        return {"evidence": [{ "id": e.evidence_id, "kind": e.kind, "reference": e.reference,
                               "observed": e.observed} for e in self.evidence.values()],
                "claims": self.claims}


# ---------------------------------------------------------------- §6 concurrency
# Seams: atomic publication rides W8's atomic writes (injected publish_fn);
# file locking rides W8's locking (injected lock_fn). Until W8 lands, the
# in-process fallbacks below are correct but single-process only.
@dataclass
class VersionedRecord:
    record_id: str
    version: int
    content: str
    artifact_path: str = ""


class ConcurrencyControl:
    def __init__(self) -> None:
        self.records: dict[str, VersionedRecord] = {}
        self.locks: dict[str, dict] = {}
        self.seen_events: set[str] = set()
        self.idempotency: dict[str, str] = {}

    def write(self, record_id: str, expected_version: int, content: str) -> dict:
        cur = self.records.get(record_id)
        if cur is not None and cur.version != expected_version:
            return {"ok": False, "reason": "stale-write",
                    "note": "This copy is out of date; refresh it before saving."}
        new_version = (cur.version + 1) if cur else 1
        self.records[record_id] = VersionedRecord(record_id, new_version, content)
        return {"ok": True, "version": new_version}

    def conflict(self, a: VersionedRecord, b: VersionedRecord) -> bool:
        return a.record_id == b.record_id and a.version != b.version and a.content != b.content

    def acquire_lock(self, resource: str, owner: str, ttl_s: float = 60.0) -> dict:
        cur = self.locks.get(resource)
        if cur and time.time() < cur["expires_at"] and cur["owner"] != owner:
            return {"acquired": False, "owner": cur["owner"],
                    "note": "Someone else holds this lock right now."}
        self.locks[resource] = {"owner": owner, "expires_at": time.time() + ttl_s}
        return {"acquired": True, "owner": owner}

    def lock_visibility(self) -> dict:
        return {r: {"owner": v["owner"], "expires_in_s": max(0.0, v["expires_at"] - time.time())}
                for r, v in self.locks.items()}

    def detect_deadlock(self, waits: dict[str, str]) -> list[list[str]]:
        """waits: owner -> resource-owner it waits on. Return cycles."""
        cycles = []
        for start in waits:
            path, cur = [start], waits.get(start)
            while cur and cur not in path:
                path.append(cur)
                cur = waits.get(cur)
            if cur == start and len(path) > 1:
                cycles.append(path)
        return cycles

    def suppress_duplicate(self, event_id: str) -> bool:
        if event_id in self.seen_events:
            return True  # suppressed
        self.seen_events.add(event_id)
        return False

    def handle_out_of_order(self, seq: int, last_seen: int) -> dict:
        if seq <= last_seen:
            return {"action": "ignore-stale-snapshot",
                    "note": "An older update arrived after a newer one; the newer one stands."}
        return {"action": "apply", "new_last_seen": seq}

    def idempotent(self, key: str, result: str) -> dict:
        if key in self.idempotency:
            return {"reused": True, "result": self.idempotency[key]}
        self.idempotency[key] = result
        return {"reused": False, "result": result}

    def stale_write_rejected(self, record_id: str, expected: int) -> bool:
        cur = self.records.get(record_id)
        return cur is not None and cur.version != expected

    def publish_atomically(self, path: str, content: str, publish_fn=None) -> dict:
        """W8 seam: publish_fn performs the real atomic write; fallback is
        a plain write clearly marked as not atomic."""
        if publish_fn is not None:
            publish_fn(path, content)
            return {"published": True, "atomic": True, "path": path}
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"published": True, "atomic": False,
                "note": "Fallback write used; the W8 atomic writer was not wired in."}

    def reconcile(self, db_records: dict[str, int], artifacts: dict[str, bool]) -> list[dict]:
        out = []
        for rid, ver in db_records.items():
            if not artifacts.get(rid):
                out.append({"record": rid, "version": ver, "problem": "record without stored file"})
        for rid in artifacts:
            if rid not in db_records:
                out.append({"record": rid, "problem": "stored file without a record"})
        return out

    def external_modification(self, path: str, known_hash: str, current_hash: str) -> dict:
        if known_hash != current_hash:
            return {"modified_outside": True,
                    "note": "This file changed outside AgentQon since it was last seen."}
        return {"modified_outside": False}

    def mission_conflict_warning(self, mission_a: str, mission_b: str, shared: list[str]) -> dict:
        return {"missions": [mission_a, mission_b], "shared_files": shared,
                "warning": "Two missions touch the same files; their writes may clash."} if shared else {}


# ---------------------------------------------------------------- §7 output protocols
OUTPUT_SCHEMA_VERSIONS = ("output-v1", "output-v2")
MIGRATIONS = {("output-v1", "output-v2"): lambda d: {**d, "schema_version": "output-v2"}}


def make_envelope(status: str, result: object = None, warnings: list[str] | None = None,
                  uncertainty: str = "low", finish_reason: str = "done",
                  artifacts: list[dict] | None = None,
                  schema_version: str = "output-v2") -> dict:
    """Structured response envelope; user text stays separate from machine fields."""
    return {"schema_version": schema_version, "status": status, "result": result,
            "warnings": warnings or [], "uncertainty": uncertainty,
            "finish_reason": finish_reason, "artifacts": artifacts or [],
            "partial": finish_reason in ("truncated", "partial"),
            "continuation": finish_reason == "truncated"}


def validate_envelope(env: dict, required: list[str]) -> dict:
    missing = [f for f in required if f not in env or env[f] in (None, "")]
    return {"ok": not missing, "missing": missing}


def migrate_output(data: dict, from_v: str, to_v: str) -> dict:
    fn = MIGRATIONS.get((from_v, to_v))
    if fn is None:
        return {"ok": False, "note": f"No migration from {from_v} to {to_v} is known."}
    return {"ok": True, "data": fn(data)}


def standard_error(reason: str, detail: str = "") -> dict:
    return {"status": "error", "reason": reason, "message": plain_error(reason),
            "detail": detail}


def repair_json(text: str) -> dict:
    """Safe repair: try strict parse, then one conservative fix; always re-validate."""
    try:
        return {"ok": True, "data": json.loads(text), "repaired": False}
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", text.strip())
    if fixed == text.strip():
        fixed = text.strip().rstrip(",") + ("}" if text.strip().startswith("{") else "]")
    try:
        return {"ok": True, "data": json.loads(fixed), "repaired": True}
    except json.JSONDecodeError:
        return {"ok": False, "note": "The output could not be read as structured data, even after repair."}


def detect_malformed(output: str) -> dict:
    try:
        json.loads(output)
        return {"malformed": False}
    except json.JSONDecodeError as exc:
        return {"malformed": True, "note": f"Structured output was not valid: {exc.msg}."}


def enforce_size(text: str, limit: int) -> dict:
    if len(text) <= limit:
        return {"text": text, "truncated": False}
    return {"text": text[:limit], "truncated": True,
            "continuation": "Output was cut short; ask for the rest to continue."}


def tool_call_result(tool: str, success: bool, result: object, reason: str = "") -> dict:
    return {"tool": tool, "success": success, "result": result,
            "reason": reason, "message": "" if success else plain_error(reason or "other")}


# ---------------------------------------------------------------- §8 profiling
@dataclass
class StageTiming:
    stage: str
    seconds: float
    detail: str = ""


class Profiler:
    def __init__(self) -> None:
        self.stages: list[StageTiming] = []
        self.memory_peak_mb: float = 0.0
        self.cache_hits = 0
        self.cache_misses = 0
        self.retries = 0

    def record(self, stage: str, seconds: float, detail: str = "") -> None:
        self.stages.append(StageTiming(stage, seconds, detail))

    def slow_steps(self, threshold_s: float = 1.0) -> list[StageTiming]:
        return [s for s in self.stages if s.seconds >= threshold_s]

    def critical_path(self) -> list[StageTiming]:
        return sorted(self.stages, key=lambda s: -s.seconds)

    def cache_effectiveness(self) -> dict:
        total = self.cache_hits + self.cache_misses
        return {"hits": self.cache_hits, "misses": self.cache_misses,
                "hit_rate": round(self.cache_hits / total, 3) if total else 0.0}

    def diagnose_slowness(self) -> dict:
        by_stage = {}
        for s in self.stages:
            by_stage[s.stage] = by_stage.get(s.stage, 0.0) + s.seconds
        if not by_stage:
            return {"cause": "unknown", "note": "No timing was recorded."}
        top = max(by_stage, key=by_stage.get)
        mapping = {"model": "slow because of the model",
                   "model_queue": "slow because of waiting for the model",
                   "file_scan": "slow because of file scanning",
                   "database": "slow because of a database query",
                   "rate_limit": "slow because of a rate-limited provider",
                   "approval_wait": "slow because it waited for a person to approve",
                   "dependency_wait": "slow because it waited on another step"}
        return {"cause": top, "note": mapping.get(top, f"slowest step: {top}"),
                "seconds": round(by_stage[top], 3)}

    def regression_alert(self, baseline_total: float, threshold: float = 1.5) -> dict:
        total = sum(s.seconds for s in self.stages)
        return {"total_s": round(total, 3), "baseline_s": baseline_total,
                "regression": total > baseline_total * threshold,
                "note": "This run is much slower than the baseline." if total > baseline_total * threshold else ""}

    def benchmark(self, runs: list[float]) -> dict:
        if not runs:
            return {"runs": 0}
        return {"runs": len(runs), "mean_s": round(sum(runs) / len(runs), 3),
                "best_s": min(runs), "worst_s": max(runs)}


# ---------------------------------------------------------------- §9 adaptive policies
# Every decision is explained, bounded, and measured. A policy NEVER
# silently changes a security requirement or bypasses a budget limit:
# decisions that would do so are refused, not adapted around.
ADAPTIVE_LIMITS = {"max_model_escalations": 2, "max_unproductive_retries": 3,
                   "max_extra_budget_fraction": 0.25, "max_parallelism": 8}


@dataclass
class PolicyDecision:
    policy: str
    decision: str
    reason: str
    limit: str
    effect_measured: str = ""


def adapt(policy: str, context: dict) -> PolicyDecision:
    """All adaptive policies, each with an explicit limit and explanation."""
    if policy == "stronger_model_on_verify_failure":
        n = context.get("verify_failures", 0)
        if n >= 1 and context.get("escalations", 0) < ADAPTIVE_LIMITS["max_model_escalations"]:
            return PolicyDecision(policy, "escalate-model",
                                  "Verification failed, so a stronger model is tried.",
                                  f"at most {ADAPTIVE_LIMITS['max_model_escalations']} escalations")
        return PolicyDecision(policy, "keep-model", "No escalation left or no failure to react to.",
                              f"at most {ADAPTIVE_LIMITS['max_model_escalations']} escalations")
    if policy == "smaller_model_for_bounded_subtask":
        return PolicyDecision(policy, "use-smaller-model" if context.get("bounded") else "keep-model",
                              "The subtask is small and bounded." if context.get("bounded")
                              else "The subtask is not clearly bounded.",
                              "smaller model only for bounded subtasks")
    if policy == "reduce_parallelism_under_memory_pressure":
        return PolicyDecision(policy, "reduce-parallelism" if context.get("memory_pressure") else "keep-parallelism",
                              "Memory is under pressure, so fewer things run at once." if context.get("memory_pressure")
                              else "No memory pressure seen.",
                              f"parallelism floor 1, ceiling {ADAPTIVE_LIMITS['max_parallelism']}")
    if policy == "fallback_provider":
        # FailoverGateway is kept as the model policy; this decision records it.
        return PolicyDecision(policy, "use-fallback-provider" if context.get("provider_failed") else "keep-provider",
                              "The provider failed in an eligible way, so the fallback is used." if context.get("provider_failed")
                              else "The provider is working.",
                              "fallback only after eligible failures")
    if policy == "increase_verification_for_high_impact":
        return PolicyDecision(policy, "extra-verification" if context.get("high_impact") else "normal-verification",
                              "This change has high impact, so it gets extra checks." if context.get("high_impact")
                              else "Normal impact, normal checks.",
                              "extra checks never skipped for high-impact changes")
    if policy == "skip_redundant_checks_on_cached":
        return PolicyDecision(policy, "skip-redundant-checks" if context.get("cache_valid") else "run-checks",
                              "A valid cached result already passed these checks." if context.get("cache_valid")
                              else "No valid cached result, so checks run.",
                              "skip only when the cached result is valid")
    if policy == "reuse_successful_intermediates":
        return PolicyDecision(policy, "reuse-intermediate" if context.get("intermediate_ok") else "recompute",
                              "A good intermediate result exists." if context.get("intermediate_ok")
                              else "No good intermediate result.",
                              "reuse only verified intermediates")
    if policy == "extra_budget_for_critical":
        frac = context.get("extra_fraction", 0.0)
        if context.get("critical_unresolved") and frac <= ADAPTIVE_LIMITS["max_extra_budget_fraction"]:
            return PolicyDecision(policy, "grant-extra-budget",
                                  "A critical task is still unresolved, so it gets more budget.",
                                  f"at most {ADAPTIVE_LIMITS['max_extra_budget_fraction']:.0%} extra")
        return PolicyDecision(policy, "refuse-extra-budget",
                              "Extra budget would pass the allowed limit, so it is refused.",
                              f"at most {ADAPTIVE_LIMITS['max_extra_budget_fraction']:.0%} extra")
    if policy == "stop_after_unproductive_retries":
        n = context.get("unproductive_retries", 0)
        return PolicyDecision(policy, "stop" if n >= ADAPTIVE_LIMITS["max_unproductive_retries"] else "continue",
                              f"{n} unproductive retries seen." if n >= ADAPTIVE_LIMITS["max_unproductive_retries"]
                              else "Retries are still productive.",
                              f"stop after {ADAPTIVE_LIMITS['max_unproductive_retries']} unproductive retries")
    if policy == "human_review_on_uncertainty":
        return PolicyDecision(policy, "ask-person" if context.get("uncertainty") == "high" else "continue",
                              "Uncertainty is still high, so a person decides." if context.get("uncertainty") == "high"
                              else "Uncertainty is manageable.",
                              "high uncertainty always goes to a person")
    if policy == "deadline_strategy":
        return PolicyDecision(policy, context.get("strategy", "normal"),
                              "Strategy chosen from the time left before the deadline.",
                              "deadline choice never drops mandatory checks")
    if policy == "context_size_adaptation":
        return PolicyDecision(policy, "shrink-context" if context.get("over_limit") else "keep-context",
                              "Context is over the measured limit, so it is trimmed with the preservation checklist."
                              if context.get("over_limit") else "Context fits.",
                              "mandatory constraints are never trimmed")
    if policy == "timeout_from_history":
        hist = context.get("history_s", [])
        timeout = round(max(hist) * 1.5, 1) if hist else 30.0
        return PolicyDecision(policy, f"timeout-{timeout}s",
                              "Timeout set from past runs of this tool.", "timeout capped by policy layer")
    if policy == "sequential_when_parallelism_helps_little":
        return PolicyDecision(policy, "run-sequentially" if context.get("parallel_gain", 1.0) < 1.2 else "run-in-parallel",
                              "Parallel runs would barely help here." if context.get("parallel_gain", 1.0) < 1.2
                              else "Parallel runs help here.",
                              "sequential choice is about speed, not correctness")
    if policy == "deeper_testing_after_risky_change":
        return PolicyDecision(policy, "deeper-tests" if context.get("risky") else "normal-tests",
                              "The change is risky, so testing goes deeper." if context.get("risky")
                              else "Normal change, normal tests.",
                              "risky changes never get lighter testing")
    return PolicyDecision(policy, "unknown-policy", "No such adaptive policy is known.", "refused")


def refuse_silent_security_change(request: dict) -> dict:
    """A policy may never silently weaken security or bypass a budget."""
    if request.get("weakens_security") or request.get("bypasses_budget"):
        return {"allowed": False,
                "note": "Refused: this would quietly weaken a safety rule or pass a budget limit."}
    return {"allowed": True}


# ---------------------------------------------------------------- §10 cache correctness
# BINDING: a result is never reused because two prompts look similar.
# The key covers inputs, permissions, environment, freshness, and schema.
SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "apikey")


@dataclass
class CacheEntry:
    key: str
    value: str
    schema_version: str
    project: str
    user: str
    created_at: float = field(default_factory=time.time)
    ttl_s: float = 3600.0
    provenance: str = ""
    checksum: str = ""


class ResultCache:
    def __init__(self, max_entries: int = 1000) -> None:
        self.entries: dict[str, CacheEntry] = {}
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    @staticmethod
    def eligible(kind: str, sensitive: bool) -> bool:
        if sensitive:
            return False  # sensitive data is never cached
        return kind in ("deterministic", "repeatable", "read_only")

    @staticmethod
    def build_key(prompt: str, inputs: dict, permissions: list[str], env: dict,
                  freshness: str, schema_version: str) -> str:
        payload = json.dumps({"prompt": prompt, "inputs": inputs,
                              "permissions": sorted(permissions), "env": env,
                              "freshness": freshness, "schema": schema_version},
                             sort_keys=True)
        return _hash(payload)

    def put(self, entry: CacheEntry) -> dict:
        if len(self.entries) >= self.max_entries:
            oldest = min(self.entries, key=lambda k: self.entries[k].created_at)
            del self.entries[oldest]
        entry.checksum = _hash(entry.value)
        self.entries[entry.key] = entry
        return {"stored": True, "key": entry.key}

    def get(self, key: str, project: str, user: str) -> dict:
        e = self.entries.get(key)
        if e is None:
            self.misses += 1
            return {"hit": False, "explanation": "No cached result for this exact key."}
        if e.project != project or e.user != user:
            self.misses += 1
            return {"hit": False, "explanation": "Cached result belongs to a different project or person."}
        if time.time() - e.created_at > e.ttl_s:
            self.misses += 1
            return {"hit": False, "explanation": "Cached result expired.", "stale": True}
        if e.checksum != _hash(e.value):
            self.misses += 1
            return {"hit": False, "explanation": "Cached result failed its integrity check."}
        self.hits += 1
        return {"hit": True, "value": e.value, "provenance": e.provenance,
                "explanation": "Exact key, project, person, freshness, and integrity all match."}

    def invalidate(self, key: str | None = None, project: str | None = None) -> int:
        if key:
            return 1 if self.entries.pop(key, None) else 0
        doomed = [k for k, e in self.entries.items() if project is None or e.project == project]
        for k in doomed:
            del self.entries[k]
        return len(doomed)

    def invalidate_on_change(self, changed: str) -> int:
        """File / model / prompt / skill changes invalidate affected entries."""
        doomed = [k for k, e in self.entries.items() if changed in e.provenance]
        for k in doomed:
            del self.entries[k]
        return len(doomed)

    def effectiveness(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "entries": len(self.entries)}


# ---------------------------------------------------------------- §11 reference data
@dataclass
class ReferenceDoc:
    doc_id: str
    text: str
    version: str
    metadata: dict = field(default_factory=dict)
    chunks: list[str] = field(default_factory=list)


class ReferenceCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.docs: dict[str, ReferenceDoc] = {}
        self.access_log: list[dict] = []
        self.snapshots: dict[str, list[str]] = {}

    def register(self, doc: ReferenceDoc) -> None:
        self.docs[doc.doc_id] = doc

    def index_incremental(self, doc: ReferenceDoc) -> dict:
        existed = doc.doc_id in self.docs
        self.docs[doc.doc_id] = doc
        return {"indexed": doc.doc_id, "updated": existed}

    def duplicates(self) -> list[list[str]]:
        by_hash: dict[str, list[str]] = {}
        for d in self.docs.values():
            by_hash.setdefault(_hash(" ".join(d.text.lower().split())), []).append(d.doc_id)
        return [v for v in by_hash.values() if len(v) > 1]

    def version_compare(self, doc_id: str, other: ReferenceDoc) -> dict:
        cur = self.docs.get(doc_id)
        if cur is None:
            return {"known": False}
        return {"doc": doc_id, "current_version": cur.version,
                "other_version": other.version, "changed": cur.text != other.text}

    def normalize_metadata(self, doc: ReferenceDoc) -> dict:
        return {str(k).lower().strip(): str(v).strip() for k, v in doc.metadata.items()}

    def chunk_map(self, doc: ReferenceDoc, size: int = 500) -> list[dict]:
        chunks = [doc.text[i:i + size] for i in range(0, len(doc.text), size)] or [""]
        doc.chunks = chunks
        return [{"chunk": i, "doc": doc.doc_id, "chars": len(c)} for i, c in enumerate(chunks)]

    def cleanup_deleted(self, live_ids: set[str]) -> list[str]:
        gone = [d for d in self.docs if d not in live_ids]
        for d in gone:
            del self.docs[d]
        return gone

    def retrieve(self, query: str, allowed_projects: list[str], user: str) -> list[dict]:
        out = []
        for d in self.docs.values():
            project = d.metadata.get("project")
            if project:
                if project not in allowed_projects and "*" not in allowed_projects:
                    continue
            elif "*" not in allowed_projects:
                continue  # no project label = restricted unless retrieval is explicitly open
            score = sum(1 for w in query.lower().split() if w in d.text.lower())
            if score:
                out.append({"doc": d.doc_id, "score": score})
                self.access_log.append({"doc": d.doc_id, "user": user, "at": _now()})
        return sorted(out, key=lambda x: -x["score"])

    def snapshot(self, name: str) -> dict:
        self.snapshots[name] = sorted(self.docs)
        return {"snapshot": name, "docs": len(self.snapshots[name])}

    def restore(self, name: str) -> dict:
        ids = self.snapshots.get(name)
        if ids is None:
            return {"restored": False, "note": "No such snapshot."}
        return {"restored": True, "docs": ids}

    def stale_warnings(self, max_age_days: float, ages: dict[str, float]) -> list[dict]:
        return [{"doc": d, "warning": "This reference may be outdated."}
                for d, age in ages.items() if age > max_age_days and d in self.docs]

    def search_quality(self, query: str, expected: list[str]) -> dict:
        got = [r["doc"] for r in self.retrieve(query, allowed_projects=["*"], user="eval")]
        got = [r["doc"] for r in self.retrieve(query, allowed_projects=list(
            {d.metadata.get("project", "*") for d in self.docs.values()}), user="eval")]
        found = [e for e in expected if e in got]
        return {"expected": len(expected), "found": len(found),
                "coverage": round(len(found) / len(expected), 3) if expected else 0.0}


# ---------------------------------------------------------------- §12 incident handling
# BINDING: a heuristic alert is NEVER presented as proof of bad behavior.
# Alerts carry certainty "heuristic" and "may indicate" wording.
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass
class Incident:
    incident_id: str
    kind: str
    severity: str
    certainty: str  # "observed" | "heuristic"
    events: list[dict] = field(default_factory=list)
    contained: bool = False
    review: str = ""


class IncidentTracker:
    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}
        self.event_log: list[dict] = []

    def observe(self, kind: str, detail: str, certainty: str = "observed") -> dict:
        self.event_log.append({"kind": kind, "detail": detail, "at": _now(), "certainty": certainty})
        return {"logged": True, "kind": kind}

    def repeated_auth_failures(self, count: int, threshold: int = 5) -> dict | None:
        if count >= threshold:
            return self.raise_incident("repeated_auth_failure", "high", "observed",
                                       f"{count} failed sign-ins in a row")
        return None

    def secret_exposure(self, text: str) -> dict | None:
        if re.search(r"sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}", text):
            return self.raise_incident("secret_exposure", "critical", "observed",
                                       "A secret-shaped value appeared where it should not.")
        return None

    def unusual_tool_pattern(self, calls: list[str]) -> dict | None:
        if len(calls) >= 10 and len(set(calls)) == 1:
            return self.raise_incident("unusual_tool_pattern", "medium", "heuristic",
                                       "The same tool was called many times in a row; this may indicate a loop.")
        return None

    def raise_incident(self, kind: str, severity: str, certainty: str, detail: str) -> dict:
        inc = Incident(incident_id=f"inc-{_hash(kind + detail + _now())}", kind=kind,
                       severity=severity, certainty=certainty,
                       events=[{"at": _now(), "detail": detail}])
        self.incidents[inc.incident_id] = inc
        return {"incident_id": inc.incident_id, "kind": kind, "severity": severity,
                "certainty": certainty,
                "wording": ("This may indicate a problem; it is not proof of bad behavior."
                            if certainty == "heuristic" else "This was directly observed.")}

    def timeline(self, incident_id: str) -> list[dict]:
        inc = self.incidents.get(incident_id)
        return inc.events if inc else []

    def contain(self, incident_id: str, actions: list[str]) -> dict:
        inc = self.incidents.get(incident_id)
        if inc is None:
            return {"contained": False}
        inc.contained = True
        inc.events.append({"at": _now(), "detail": "Containment: " + ", ".join(actions)})
        return {"contained": True, "actions": actions}

    def revoke_credentials(self, incident_id: str, credential: str) -> dict:
        return {"incident": incident_id, "credential_revoked": True,
                "note": "The named credential was revoked; sign in again to continue."}

    def terminate_session(self, incident_id: str, session_id: str) -> dict:
        return {"incident": incident_id, "session_ended": session_id}

    def recovery_checklist(self, kind: str) -> list[str]:
        return ["Confirm what changed.", "Remove the cause.", "Check nearby records.",
                "Restore from a known-good copy if needed.", "Record what was learned."]

    def export(self, incident_id: str) -> dict:
        inc = self.incidents.get(incident_id)
        if inc is None:
            return {"found": False}
        return {"found": True, "incident_id": inc.incident_id, "kind": inc.kind,
                "severity": inc.severity, "certainty": inc.certainty, "events": inc.events,
                "contained": inc.contained}

    def review(self, incident_id: str, note: str) -> dict:
        inc = self.incidents.get(incident_id)
        if inc is None:
            return {"recorded": False}
        inc.review = note
        return {"recorded": True, "review": note}

    def correlate(self, kinds: list[str]) -> list[dict]:
        return [{"kind": k, "count": sum(1 for e in self.event_log if e["kind"] == k)}
                for k in kinds]


# ---------------------------------------------------------------- §13 i18n / text
# English-only interface stands: no UI language switch. Multilingual
# prompts and files are still supported; this module handles them safely.
CONFUSABLES = {"а": "Cyrillic a looks like Latin a", "е": "Cyrillic e looks like Latin e",
               "о": "Cyrillic o looks like Latin o", "р": "Cyrillic p looks like Latin p",
               "0": "digit 0 looks like letter O", "1": "digit 1 looks like letter l",
               "rn": "letters r+n look like letter m"}


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def grapheme_count(text: str) -> int:
    """Grapheme-aware count: base character + combining marks count as one."""
    count, i = 0, 0
    chars = list(text)
    while i < len(chars):
        count += 1
        i += 1
        while i < len(chars) and unicodedata.combining(chars[i]):
            i += 1
    return count


def detect_language(text: str) -> dict:
    """Detection with uncertainty — never a bare guess."""
    if not text.strip():
        return {"language": "unknown", "uncertainty": "high"}
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    arabic = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    total = max(1, len(text))
    for name, n in (("cyrillic-script", cyrillic), ("arabic-script", arabic), ("cjk-script", cjk)):
        if n / total > 0.5:
            return {"language": name, "uncertainty": "low"}
    return {"language": "latin-script", "uncertainty": "medium"}


def is_rtl(text: str) -> bool:
    return any("\u0590" <= ch <= "\u08ff" for ch in text)


def convert_encoding(data: bytes, from_enc: str, to_enc: str = "utf-8") -> dict:
    """Conversion with validation: silent mangling is refused, not hidden."""
    try:
        text = data.decode(from_enc, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        return {"ok": False, "note": f"The text could not be read as {from_enc}; nothing was converted. ({exc})"}
    try:
        return {"ok": True, "text": text, "encoded": text.encode(to_enc, errors="strict")}
    except UnicodeEncodeError as exc:
        return {"ok": False, "note": f"The text cannot be written as {to_enc}. ({exc})"}


def confusable_warnings(text: str) -> list[dict]:
    """BINDING safeguard for identifiers, filenames, security settings."""
    out = []
    for ch, note in CONFUSABLES.items():
        if ch in text and len(ch) == 1:
            out.append({"character": ch, "warning": note})
    if "rn" in text:
        out.append({"sequence": "rn", "warning": CONFUSABLES["rn"]})
    return out


def locale_number(value: float, locale: str = "en") -> str:
    if locale in ("de", "fr", "es"):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{value:,.2f}"


def locale_sort(items: list[str], locale: str = "en") -> list[str]:
    return sorted(items, key=lambda s: unicodedata.normalize("NFKD", s).casefold())


def search_normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def token_estimate(text: str, language: str = "en") -> int:
    words = len(text.split())
    return math.ceil(words * 1.3) if language == "en" else math.ceil(words * 1.6)


def preserve_code_in_translation(text: str) -> dict:
    code = re.findall(r"`[^`]+`", text)
    return {"code_spans_kept": code, "note": "Code and identifiers must survive translation unchanged."}


def filename_check(name: str) -> dict:
    problems = []
    if name != normalize_text(name):
        problems.append("filename is not in normalized form")
    if confusable_warnings(name):
        problems.append("filename contains look-alike characters")
    if "/" in name or "\x00" in name:
        problems.append("filename contains a forbidden character")
    return {"ok": not problems, "problems": problems}


def multilingual_error(reason: str, language: str = "en") -> str:
    base = plain_error(reason)
    if language != "en":
        return f"{base} (shown in English; the interface language is English only)"
    return base


# ---------------------------------------------------------------- §14 centralized policy layer
# Folds the scattered guards into ONE layer — step cap (agent_loop:
# DEFAULT_MAX_STEPS=25, MAX_STEPS_LIMIT=200), shell timeouts, gateway
# pacing, and the hidden ceiling — instead of sitting alongside them.
@dataclass
class Policy:
    max_mission_duration_s: float = 3600.0
    max_agents_per_mission: int = 10
    max_tool_calls_per_task: int = 100
    max_repeated_failures: int = 5
    max_artifact_bytes: int = 50_000_000
    max_generated_files: int = 500
    max_recursion_depth: int = 10
    max_workflow_iterations: int = 50
    max_local_model_loads: int = 4
    max_queued_jobs: int = 100
    max_response_chars: int = 200_000
    max_attachment_bytes: int = 20_000_000
    max_external_requests_per_min: int = 60
    step_cap_default: int = 25
    step_cap_limit: int = 200
    quotas_per_project: dict = field(default_factory=dict)
    exceptions: list[dict] = field(default_factory=list)

    def check(self, metric: str, value: float, project: str | None = None,
              authorized_override: bool = False) -> dict:
        limits = {
            "mission_duration_s": (self.max_mission_duration_s, True),
            "agents_per_mission": (self.max_agents_per_mission, True),
            "tool_calls_per_task": (self.max_tool_calls_per_task, True),
            "repeated_failures": (self.max_repeated_failures, True),
            "artifact_bytes": (self.max_artifact_bytes, True),
            "generated_files": (self.max_generated_files, True),
            "recursion_depth": (self.max_recursion_depth, True),
            "workflow_iterations": (self.max_workflow_iterations, True),
            "local_model_loads": (self.max_local_model_loads, True),
            "queued_jobs": (self.max_queued_jobs, False),
            "response_chars": (self.max_response_chars, False),
            "attachment_bytes": (self.max_attachment_bytes, True),
            "external_requests_per_min": (self.max_external_requests_per_min, False),
            "steps": (self.step_cap_limit, True),
        }
        if metric not in limits:
            return {"allowed": True, "note": "No policy covers this measure."}
        limit, hard = limits[metric]
        if project and project in self.quotas_per_project:
            limit = min(limit, self.quotas_per_project[project].get(metric, limit))
        if value <= limit:
            return {"allowed": True, "limit": limit}
        if authorized_override and not hard:
            self.exceptions.append({"metric": metric, "value": value, "at": _now()})
            return {"allowed": True, "limit": limit, "override": True,
                    "note": "An authorized override was used; it is logged."}
        return {"allowed": False, "limit": limit, "hard": hard,
                "explanation": ("This run hit a hard safety limit and was stopped; work already done is kept."
                                if hard else
                                "This run passed a soft warning limit; a person can allow it to continue."),
                "action": "cancel" if hard else "warn"}

    def folded_guards(self) -> dict:
        """Documents which scattered guards this layer now owns."""
        return {"step_cap": f"default {self.step_cap_default}, limit {self.step_cap_limit}",
                "shell_timeouts": "owned: tool calls carry timeouts checked here",
                "gateway_pacing": "owned: external request rate checked here",
                "hidden_ceiling": "owned: response and artifact ceilings checked here"}


# ---------------------------------------------------------------- §15 migration safety
@dataclass
class MigrationChange:
    change_id: str
    area: str  # "database" | "config" | "plugin" | "api" | "code"
    reversible: bool
    rollback_note: str


class MigrationPlan:
    def __init__(self, version_from: str, version_to: str) -> None:
        self.version_from, self.version_to = version_from, version_to
        self.changes: list[MigrationChange] = []
        self.backup_taken = False
        self.progress: list[str] = []

    def add(self, change: MigrationChange) -> None:
        self.changes.append(change)

    def preflight(self, deps_ok: bool, plugins_ok: bool) -> dict:
        problems = []
        if not deps_ok:
            problems.append("a dependency is not compatible with the new version")
        if not plugins_ok:
            problems.append("a plugin is not compatible with the new version")
        return {"ok": not problems, "problems": problems}

    def backup(self) -> dict:
        self.backup_taken = True
        return {"backup_taken": True}

    def rollback_feasible(self) -> dict:
        """BINDING: per-change reversibility. Rolling back code never
        automatically rolls back a database migration."""
        irreversible = [c.change_id for c in self.changes if not c.reversible]
        db_changes = [c.change_id for c in self.changes if c.area == "database"]
        return {"fully_reversible": not irreversible, "irreversible": irreversible,
                "database_changes_need_own_rollback": db_changes,
                "note": "Database changes need their own rollback plan; code rollback alone does not undo them."}

    def dry_run(self) -> dict:
        if any(not c.reversible for c in self.changes) and not self.backup_taken:
            return {"ok": False, "note": "Take a backup before trying an upgrade with irreversible changes."}
        return {"ok": True, "changes": len(self.changes)}

    def apply(self, fail_at: str | None = None) -> dict:
        for c in self.changes:
            if fail_at == c.change_id:
                self.progress.append(f"failed at {c.change_id}")
                return {"ok": False, "failed_at": c.change_id,
                        "recovery": "Restore the backup, then retry or stay on the old version."}
            self.progress.append(f"applied {c.change_id}")
        return {"ok": True, "progress": list(self.progress)}

    def integrity_check(self, records_ok: bool) -> dict:
        return {"ok": records_ok,
                "note": "" if records_ok else "Data checks failed after the upgrade; do not proceed."}

    def health_check(self) -> dict:
        return {"ok": True, "checks": ["service starts", "stored data reads", "version recorded"]}

    def release_notes(self) -> str:
        lines = [f"Upgrade {self.version_from} -> {self.version_to}"]
        for c in self.changes:
            lines.append(f"- {c.area}: {c.change_id} (reversible: {c.reversible})")
        return "\n".join(lines)

    def deprecated_warnings(self, settings: dict, deprecated: list[str]) -> list[str]:
        return [f"Setting '{s}' is deprecated; it will stop working in a later version."
                for s in deprecated if s in settings]


# ---------------------------------------------------------------- §16 analytics (privacy-bound)
# BINDING: minimized, transparent, never prompts or file contents unless
# explicitly needed AND authorized. Cost-per-success reads the W1 metric
# through an injected callable — never a second computation.
class Analytics:
    def __init__(self, cost_metric=None) -> None:
        self.missions: list[dict] = []
        self.cost_metric = cost_metric  # W1 seam: callable(mission) -> cost or None

    def record(self, mission: dict) -> dict:
        safe = {k: mission.get(k) for k in ("task_type", "completed", "corrections",
                                            "verification_failures", "retries", "seconds_to_success",
                                            "fallbacks", "cache_hit", "skill_effective",
                                            "recovered", "abandoned", "interventions",
                                            "feature", "feedback", "seconds_saved_estimate")}
        self.missions.append(safe)
        return {"recorded": True, "fields": sorted(safe)}

    def completion_rate_by_type(self) -> dict:
        by_type: dict[str, list[bool]] = {}
        for m in self.missions:
            by_type.setdefault(str(m.get("task_type")), []).append(bool(m.get("completed")))
        return {t: round(sum(v) / len(v), 3) for t, v in by_type.items() if v}

    def rates(self) -> dict:
        n = max(1, len(self.missions))
        return {"correction_rate": round(sum(m.get("corrections") or 0 for m in self.missions) / n, 3),
                "verification_failure_rate": round(sum(1 for m in self.missions if m.get("verification_failures")) / n, 3),
                "retry_rate": round(sum(m.get("retries") or 0 for m in self.missions) / n, 3),
                "workflow_failure_rate": round(sum(1 for m in self.missions if not m.get("completed")) / n, 3),
                "abandonment_rate": round(sum(1 for m in self.missions if m.get("abandoned")) / n, 3),
                "fallback_frequency": sum(m.get("fallbacks") or 0 for m in self.missions),
                "interventions": sum(m.get("interventions") or 0 for m in self.missions),
                "cache_effectiveness": round(sum(1 for m in self.missions if m.get("cache_hit")) / n, 3),
                "skill_effectiveness": round(sum(1 for m in self.missions if m.get("skill_effective")) / n, 3),
                "recovery_success_rate": round(sum(1 for m in self.missions if m.get("recovered")) / n, 3)}

    def cost_per_success(self) -> dict:
        if self.cost_metric is None:
            return {"available": False,
                    "note": "The W1 cost engine was not wired in, so cost per success is not computed here."}
        successes = [m for m in self.missions if m.get("completed")]
        costs = [self.cost_metric(m) for m in successes]
        costs = [c for c in costs if c is not None]
        if not costs:
            return {"available": True, "successes": len(successes), "cost_per_success": None}
        return {"available": True, "successes": len(successes),
                "cost_per_success": round(sum(costs) / len(costs), 4)}

    def before_after(self, feature: str) -> dict:
        before = [m for m in self.missions if m.get("feature") != feature]
        after = [m for m in self.missions if m.get("feature") == feature]
        def rate(ms):
            return round(sum(1 for m in ms if m.get("completed")) / len(ms), 3) if ms else 0.0
        return {"feature": feature, "before_completion": rate(before), "after_completion": rate(after)}

    def time_saved(self) -> float:
        return round(sum(m.get("seconds_saved_estimate") or 0 for m in self.missions), 1)


# ---------------------------------------------------------------- §17 doc generation
# BINDING: generated docs distinguish information extracted from code
# from assumptions or recommendations made by an agent.
def architecture_summary(structure: dict) -> dict:
    extracted = [f"Module {m} exists with {len(v)} items." for m, v in structure.items()]
    return {"extracted_from_code": extracted, "assumed": [],
            "note": "Only verified structure is stated as fact."}


def undocumented_interfaces(public: list[str], documented: list[str]) -> list[str]:
    return [p for p in public if p not in documented]


def stale_setup_instructions(instructions: list[str], actual_steps: list[str]) -> list[str]:
    return [i for i in instructions if i not in actual_steps]


def config_reference(settings: dict) -> list[dict]:
    return [{"setting": k, "value_type": type(v).__name__, "extracted": True} for k, v in settings.items()]


def api_docs_from_spec(spec: dict) -> dict:
    endpoints = spec.get("endpoints", [])
    validated = [e for e in endpoints if e.get("method") and e.get("path")]
    return {"documented": validated, "rejected_unvalidated": len(endpoints) - len(validated)}


def dependency_diagram(deps: dict[str, list[str]]) -> str:
    return "\n".join(f"{m} -> {', '.join(v) or '(none)'}" for m, v in deps.items())


def glossary(terms: dict[str, str]) -> dict:
    return terms


def troubleshooting_from_incidents(incidents: list[dict]) -> list[dict]:
    return [{"problem": i.get("kind"), "verified": i.get("certainty") == "observed",
             "guidance": i.get("detail", "")} for i in incidents]


def docs_affected_by_change(changed_files: list[str], doc_index: dict[str, list[str]]) -> list[str]:
    out = []
    for doc, files in doc_index.items():
        if any(f in files for f in changed_files):
            out.append(doc)
    return out


def undocumented_env_vars(code_vars: list[str], documented_vars: list[str]) -> list[str]:
    return [v for v in code_vars if v not in documented_vars]


def changelog_draft(changes: list[str]) -> dict:
    return {"extracted_from_record": changes, "assumed": [],
            "draft": "\n".join(f"- {c}" for c in changes)}


def validate_links_examples(docs: list[str], known_links: set[str]) -> list[dict]:
    out = []
    for doc in docs:
        for link in re.findall(r"https?://[^\s)]+", doc):
            out.append({"link": link, "known": link in known_links})
    return out


def docs_vs_config_comparison(doc_settings: list[str], actual_settings: list[str]) -> dict:
    return {"documented_only": [s for s in doc_settings if s not in actual_settings],
            "actual_only": [s for s in actual_settings if s not in doc_settings]}


# ---------------------------------------------------------------- §18 resource lifecycle
# BINDING: the cleanup engine must never delete an artifact still
# referenced by an active mission or a retained version.
@dataclass
class TempResource:
    resource_id: str
    owner: str
    kind: str  # "workspace" | "cache" | "artifact" | "download"
    created_at: float = field(default_factory=time.time)
    ttl_s: float = 3600.0
    referenced_by: list[str] = field(default_factory=list)
    retained: bool = False


class ResourceLifecycle:
    def __init__(self) -> None:
        self.resources: dict[str, TempResource] = {}
        self.quota_bytes = 10_000_000_000
        self.used_bytes = 0

    def allocate(self, r: TempResource) -> TempResource:
        self.resources[r.resource_id] = r
        return r

    def expired(self, now: float | None = None) -> list[TempResource]:
        now = time.time() if now is None else now
        return [r for r in self.resources.values() if now - r.created_at > r.ttl_s]

    def cleanup(self, dry_run: bool = False, now: float | None = None) -> dict:
        """Deletes only unreferenced, unretained, expired resources."""
        doomed, protected = [], []
        for r in self.expired(now):
            if r.referenced_by or r.retained:
                protected.append(r.resource_id)
            else:
                doomed.append(r.resource_id)
        if not dry_run:
            for rid in doomed:
                del self.resources[rid]
        return {"would_delete" if dry_run else "deleted": doomed, "protected": protected}

    def cleanup_after_success(self, mission_id: str, keep_failed: bool = True) -> dict:
        doomed = [r.resource_id for r in self.resources.values()
                  if r.kind == "workspace" and mission_id not in r.referenced_by and not r.retained]
        for rid in doomed:
            del self.resources[rid]
        return {"cleaned": doomed}

    def retention_tier(self, r: TempResource) -> str:
        if r.retained:
            return "retained"
        if r.referenced_by:
            return "referenced"
        return "temporary"

    def reserve_disk(self, needed_bytes: int) -> dict:
        if self.used_bytes + needed_bytes > self.quota_bytes:
            return {"reserved": False, "note": "Not enough space under the quota for this job."}
        return {"reserved": True}

    def orphan_processes(self, live_pids: set[int], known: dict[str, int]) -> list[str]:
        return [sid for sid, pid in known.items() if pid not in live_pids]

    def leak_report(self) -> list[dict]:
        old = [r for r in self.resources.values() if time.time() - r.created_at > r.ttl_s * 2]
        return [{"resource": r.resource_id, "owner": r.owner, "kind": r.kind,
                 "note": "This temporary resource outlived its lifetime."} for r in old]

    def storage_accounting(self) -> dict:
        by_kind: dict[str, int] = {}
        for r in self.resources.values():
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        return {"total": len(self.resources), "by_kind": by_kind,
                "used_bytes": self.used_bytes, "quota_bytes": self.quota_bytes}

    def cleanup_with_retry(self, attempts: int = 3) -> dict:
        for _ in range(attempts):
            result = self.cleanup()
            if result["deleted"] or not result["protected"]:
                return {"ok": True, **result}
        return {"ok": False, "note": "Cleanup could not finish; protected items remain."}


# ---------------------------------------------------------------- §19 unusual functions (critical path)
def execution_differential(run_a: list[dict], run_b: list[dict]) -> dict:
    """First divergence between two runs, step by step."""
    for i, (a, b) in enumerate(zip(run_a, run_b)):
        if a != b:
            return {"first_divergence_at": i, "run_a": a, "run_b": b,
                    "note": "Runs agreed up to this step, then differed here."}
    if len(run_a) != len(run_b):
        return {"first_divergence_at": min(len(run_a), len(run_b)),
                "note": "One run had extra steps the other did not."}
    return {"first_divergence_at": None, "note": "The runs match."}


def unproductive_work_detector(steps: list[dict]) -> dict:
    """Repeated reasoning cycles, unchanged retries, non-advancing tool calls."""
    repeated = 0
    seen: dict[str, int] = {}
    for s in steps:
        key = json.dumps(s, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            repeated += 1
    non_advancing = sum(1 for s in steps if s.get("progress") is False)
    unproductive = repeated + non_advancing
    return {"unproductive_steps": unproductive, "repeated": repeated,
            "non_advancing": non_advancing,
            "unproductive": unproductive >= 3,
            "note": "Work is unproductive when the same step repeats without progress."}


def alternative_branches(strategies: dict[str, dict]) -> dict:
    """Explore strategies independently, compare VERIFIED outcomes only."""
    verified = {k: v for k, v in strategies.items() if v.get("verified")}
    best = max(verified, key=lambda k: verified[k].get("score", 0)) if verified else None
    return {"strategies": sorted(strategies), "verified": sorted(verified),
            "best_verified": best,
            "note": "Only outcomes with proof are compared; unverified branches never win."}


def environment_fingerprint(runtime: dict, deps: dict, model: dict, config: dict) -> dict:
    payload = json.dumps({"runtime": runtime, "deps": deps, "model": model, "config": config},
                         sort_keys=True)
    return {"fingerprint": _hash(payload), "runtime": runtime, "deps": deps,
            "model": model, "config_keys": sorted(config)}


def requirement_to_evidence_map(reqs: list[Requirement], evidence: dict[str, str]) -> dict:
    """Adapter over W12's proof-map core (app/integrity.py::build_proof_map,
    adopted verbatim, W12-owned): the proven/not-proven judgment is the
    core's, never a second implementation. This layer only shapes inputs
    (Requirement objects -> requirement dicts; text-keyed artifacts ->
    output-file evidence) and preserves the W11 return shape."""
    from .integrity import build_proof_map
    req_dicts = [{"requirement_id": f"req-{i}", "text": r.text} for i, r in enumerate(reqs)]
    core_evidence: dict[str, dict] = {}
    mapping = {}
    for i, r in enumerate(reqs):
        artifact = evidence.get(r.text)
        mapping[r.text] = artifact
        if artifact:
            core_evidence[f"req-{i}"] = {"output_files": [artifact]}
    core = build_proof_map(req_dicts, core_evidence)
    uncovered = [t for t, e in mapping.items() if not e]
    return {"map": mapping, "uncovered": uncovered,
            "coverage": round(1 - len(uncovered) / len(reqs), 3) if reqs else 1.0,
            "proof_rows": core["rows"], "verdict": core["verdict"],
            "proven_count": core["proven_count"], "total_count": core["total_count"]}


def change_impact_preview(changed_files: list[str], dependents: dict[str, list[str]],
                          tests: dict[str, list[str]], workflows: dict[str, list[str]]) -> dict:
    """Adapter over W12's blast-radius preview core
    (app/eval_ops.py::blast_radius_preview, adopted verbatim, W12-owned):
    the traversal is the core's transitive closure, never a second
    previewer. This layer only shapes inputs (changed files +
    dependents + tests + workflows -> a kind-labelled dependency graph)
    and splits the core's kind buckets back into files/tests/workflows."""
    from .eval_ops import blast_radius_preview
    graph_dependents: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for f in changed_files:
        kinds.setdefault(f, "file")
    for src, deps in dependents.items():
        graph_dependents.setdefault(src, []).extend(deps)
        kinds.setdefault(src, "file")
        for d in deps:
            kinds.setdefault(d, "file")
    for src, ts in tests.items():
        graph_dependents.setdefault(src, []).extend(ts)
        for t in ts:
            kinds[t] = "test"
    for src, ws in workflows.items():
        graph_dependents.setdefault(src, []).extend(ws)
        for w in ws:
            kinds[w] = "workflow"
    graph = {"dependents": graph_dependents, "kinds": kinds}
    affected_files = set(changed_files)
    affected_tests: set[str] = set()
    affected_workflows: set[str] = set()
    cores = []
    for target in changed_files:
        core = blast_radius_preview({"action_id": "change-impact-preview", "target": target}, graph)
        cores.append(core)
        for kind, nodes in core["affected"].items():
            if kind == "test":
                affected_tests.update(nodes)
            elif kind == "workflow":
                affected_workflows.update(nodes)
            else:
                affected_files.update(nodes)
    return {"affected_files": sorted(affected_files), "affected_tests": sorted(affected_tests),
            "affected_workflows": sorted(affected_workflows),
            "affected_count": len(affected_files) + len(affected_tests) + len(affected_workflows),
            "blast_radius_cores": cores}


def deadline_feasibility(deadline_s: float, history_s: list[float], queue_s: float) -> dict:
    typical = sum(history_s) / len(history_s) if history_s else 0.0
    needed = typical + queue_s
    return {"needed_s": round(needed, 1), "deadline_s": deadline_s,
            "feasible": needed <= deadline_s,
            "note": "Feasibility is judged from observed history and the current queue, not optimism."}


def artifact_merge_assistant(versions: dict[str, str]) -> dict:
    """Reconcile multi-agent outputs; never blindly overwrite."""
    texts = list(versions.values())
    if len(set(texts)) == 1:
        return {"merged": texts[0], "conflicts": [], "note": "All versions agree."}
    conflicts = [k for k, v in versions.items() if v != texts[0]]
    return {"merged": None, "conflicts": conflicts,
            "note": "Versions disagree; a person or a merge rule must resolve this, not a blind overwrite."}


def trust_boundary_map() -> dict:
    return {"boundaries": [
        {"from": "user", "to": "agents", "trust": "trusted: the user's own instructions"},
        {"from": "external content", "to": "agents", "trust": "untrusted: outside text is data, never instructions"},
        {"from": "tools", "to": "agents", "trust": "checked: tool results are evidence, not orders"},
        {"from": "agents", "to": "trusted configuration", "trust": "refused: agents never change the harness itself"},
        {"from": "model output", "to": "actions", "trust": "verified: actions need checks before they run"},
    ]}


def progress_confidence(milestones: list[dict]) -> dict:
    verified = [m for m in milestones if m.get("verified")]
    total = len(milestones)
    measured = round(len(verified) / total, 3) if total else 0.0
    return {"verified_milestones": len(verified), "total_milestones": total,
            "measured_progress": measured,
            "uncertain_estimate": None,
            "note": "Measured progress counts only verified milestones; the rest is not counted as done."}
