"""AgentQon — multi-agent communications (expansion D, §5).

Inter-agent messages, handoffs, and coordination invariants.
Stdlib-only.

D1 envelope: every message carries sender, recipient, mission,
task, type, timestamp, and a correlation ID. validate_envelope()
refuses a message missing any of them — an unaddressed message is
never delivered on a guess.
D2 structured handoffs: states requested | completed | evidence |
incomplete | next, each with required fields; check_handoff_complete()
lists what is missing in plain words. Free-form-only handoffs are
refused (a handoff with no state is not a handoff).
D3 dedup: DeliveryLog.process_once() — the same message id
delivered twice is processed once.
D4 stale detection: TaskStateRegistry tracks each task's state
version; a message carrying an older version is flagged stale and
must not be acted on.
D5 correlation: a task's whole chain shares one correlation id;
ChainLog groups messages by it for tracing.
D6 timeout policy: AgentWatchdog declares an agent timed-out when
its heartbeat is older than the policy timeout — never waited on
forever. The declaration is a record, not a guess about why.
D7 ownership: TaskOwnerRegistry holds exactly one owner per task;
claim/release/transfer are the only ways ownership moves, and
validate_owner() checks it before any state write.
D8 size limits: envelopes over MAX_MESSAGE_CHARS are refused with
plain words, never truncated silently (truncating a handoff would
corrupt its meaning).
D9 evidence references (hardened, top-15 #9): agents share
REFERENCES to verified research — an EvidenceRef carries ids and a
pointer, never the document body. Unverified research cannot be
referenced (make_evidence_ref refuses it).
D10 contradiction metadata: flag_contradiction() attaches the
conflict explicitly to both claims.

§5 coordination, same module (shared invariants, not features):
heartbeat (81) + inactivity detection (88) via AgentWatchdog;
task leases (82) via TaskLease — expiry, inspection, resume or
reassign WITHOUT repeating irreversible actions (the lease carries
completed_action_keys the next holder must skip); assignment ack
(83), duplicate-assignment detection (84), handoff ack (93) via
AssignmentRegistry; handoff completeness (85) via
check_handoff_complete(); output length limits (86); shared-result
freshness (87) via is_fresh(); concurrency limits (89) via
ConcurrencyGate; ownership validation (90) via validate_owner();
replacement policy (91) via replace_agent(); result dedup (92) via
ResultDedup; stop propagation (94) lives in the scheduler and is
re-exported here for one import surface; dependency-block reporting
(95) likewise; contradiction flagging (96) via flag_contradiction();
idle cleanup (99) via cleanup_idle(); lifecycle auditing (100) via
AgentAuditLog; partial-result acceptance (97) is kept in
multiagent.accept_partial_result and re-exported here.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .domain import new_id, now_iso

MAX_MESSAGE_CHARS = 64_000
MAX_AGENT_OUTPUT_CHARS = 200_000

ENVELOPE_FIELDS = ("sender", "recipient", "mission_id", "task_id",
                   "type", "timestamp", "correlation_id", "message_id",
                   "payload")

HANDOFF_STATES = ("requested", "completed", "evidence", "incomplete", "next")
HANDOFF_REQUIRED: dict[str, tuple] = {
    "requested": ("task_id", "ask"),
    "completed": ("task_id", "outcome"),
    "evidence": ("task_id", "evidence_refs"),
    "incomplete": ("task_id", "missing", "reason"),
    "next": ("task_id", "next_steps"),
}


# ---------------------------------------------------------------- D1 envelope

def make_envelope(sender: str, recipient: str, mission_id: str,
                  task_id: str, type: str, payload: dict,
                  correlation_id: str = "",
                  task_state_version: int = 0) -> dict:
    """Build a complete inter-agent message envelope (D1, D5)."""
    return {
        "message_id": new_id("amsg"),
        "sender": sender, "recipient": recipient,
        "mission_id": mission_id, "task_id": task_id,
        "type": type, "payload": payload,
        "timestamp": now_iso(),
        "correlation_id": correlation_id or new_id("corr"),
        "task_state_version": task_state_version,
    }


def validate_envelope(envelope: dict) -> list[str]:
    """Missing envelope fields are plain-words errors (D1)."""
    errors = []
    for f in ENVELOPE_FIELDS:
        if f not in envelope or envelope[f] in ("", None):
            errors.append(f"message is missing {f} — it cannot be delivered")
    return errors


def check_size(envelope: dict) -> tuple[bool, str]:
    """Message size limits (D8): over-limit messages are refused,
    never silently cut."""
    size = len(json.dumps(envelope, default=str))
    if size > MAX_MESSAGE_CHARS:
        return False, (f"message is too large ({size:,} characters; the "
                       f"limit is {MAX_MESSAGE_CHARS:,}) — shorten it "
                       f"and send it again")
    return True, ""


# ---------------------------------------------------------------- D2/D3/D4/D5

def make_handoff(state: str, task_id: str, **fields) -> dict:
    """A structured handoff (D2). State is mandatory; free-form-only
    handoffs (no state) are refused at construction."""
    if state not in HANDOFF_STATES:
        raise ValueError(
            f"handoff state must be one of {', '.join(HANDOFF_STATES)} — "
            f"a handoff without a state is not accepted")
    handoff = {"handoff_id": new_id("hoff"), "state": state,
               "task_id": task_id, "at": now_iso()}
    handoff.update(fields)
    return handoff


def check_handoff_complete(handoff: dict) -> tuple[bool, list[str]]:
    """Handoff completeness checking (§5-85): every required field
    for the handoff's state must be present and non-empty."""
    state = handoff.get("state", "")
    required = HANDOFF_REQUIRED.get(state)
    if required is None:
        return False, [f"handoff state {state!r} is not a known state"]
    missing = [f for f in required
               if not handoff.get(f)]
    if missing:
        return False, [f"handoff is missing {f} for a {state} handoff"
                       for f in missing]
    return True, []


class DeliveryLog:
    """Message dedup (D3): the same message id is processed once,
    however many times it is delivered."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def process_once(self, message_id: str) -> bool:
        """True when this delivery should be processed; False when
        it is a duplicate and must be skipped."""
        with self._lock:
            if message_id in self._seen:
                return False
            self._seen.add(message_id)
            return True


class TaskStateRegistry:
    """Task-state versions + stale detection (D4)."""

    def __init__(self) -> None:
        self._versions: dict[str, int] = {}
        self._states: dict[str, str] = {}

    def set_state(self, task_id: str, state: str) -> int:
        version = self._versions.get(task_id, 0) + 1
        self._versions[task_id] = version
        self._states[task_id] = state
        return version

    def current_version(self, task_id: str) -> int:
        return self._versions.get(task_id, 0)

    def is_stale(self, envelope: dict) -> tuple[bool, str]:
        """A message for a superseded task state is flagged, not
        acted on (D4)."""
        current = self.current_version(envelope.get("task_id", ""))
        carried = envelope.get("task_state_version", 0)
        if carried < current:
            return True, (f"message is for an older state of task "
                          f"{envelope.get('task_id')} (carried version "
                          f"{carried}, current version {current}) — "
                          f"it must not be acted on")
        return False, ""


class ChainLog:
    """Correlation IDs end-to-end (D5): messages grouped by the
    correlation id they share, so a task's whole chain is traceable."""

    def __init__(self) -> None:
        self._chains: dict[str, list[dict]] = {}

    def append(self, envelope: dict) -> None:
        self._chains.setdefault(envelope["correlation_id"], []).append(envelope)

    def chain_for(self, correlation_id: str) -> list[dict]:
        return list(self._chains.get(correlation_id, []))


# ---------------------------------------------------------------- D6/D7/D9/D10 + §5

class AgentWatchdog:
    """Heartbeat (81), timeout policy (D6), inactivity detection
    (88), idle cleanup (99)."""

    def __init__(self, timeout_seconds: float = 120.0,
                 idle_seconds: float = 600.0):
        self.timeout_seconds = timeout_seconds
        self.idle_seconds = idle_seconds
        self._last_seen: dict[str, float] = {}
        self._registered: dict[str, float] = {}
        self._timed_out: dict[str, str] = {}

    def register(self, agent_id: str, now: float | None = None) -> None:
        stamp = now if now is not None else time.monotonic()
        self._registered[agent_id] = stamp
        self._last_seen[agent_id] = stamp

    def heartbeat(self, agent_id: str, now: float | None = None) -> None:
        self._last_seen[agent_id] = now if now is not None else time.monotonic()

    def timed_out_agents(self, now: float | None = None) -> list[dict]:
        """Agents declared timed-out BY POLICY (D6): silent longer
        than the timeout. Declaration is recorded, not waited past."""
        at = now if now is not None else time.monotonic()
        out = []
        for agent_id, last in self._last_seen.items():
            if at - last > self.timeout_seconds and agent_id not in self._timed_out:
                self._timed_out[agent_id] = now_iso()
                out.append({"agent_id": agent_id,
                            "silent_for_seconds": round(at - last, 1),
                            "status": "timed out by policy",
                            "detail": ("the agent stopped responding and "
                                       "the timeout policy declares it "
                                       "timed out — it is not waited on "
                                       "any longer")})
        return out

    def inactive_agents(self, now: float | None = None) -> list[str]:
        at = now if now is not None else time.monotonic()
        return [a for a, last in self._last_seen.items()
                if at - last > self.idle_seconds]

    def cleanup_idle(self, now: float | None = None) -> list[str]:
        """Idle-agent cleanup (99): idle registrations are removed
        and the removal list is returned for auditing."""
        idle = self.inactive_agents(now)
        for agent_id in idle:
            self._last_seen.pop(agent_id, None)
            self._registered.pop(agent_id, None)
        return idle


class TaskOwnerRegistry:
    """Task-state ownership (D7, §5-90): exactly one owner per task
    at a time. Ownership moves only via claim/release/transfer."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    def claim(self, task_id: str, agent_id: str) -> tuple[bool, str]:
        owner = self._owners.get(task_id)
        if owner is None or owner == agent_id:
            self._owners[task_id] = agent_id
            return True, ""
        return False, (f"task {task_id} is already owned by {owner} — "
                       f"only one owner at a time")

    def release(self, task_id: str, agent_id: str) -> bool:
        if self._owners.get(task_id) == agent_id:
            del self._owners[task_id]
            return True
        return False

    def transfer(self, task_id: str, from_agent: str,
                 to_agent: str) -> tuple[bool, str]:
        if self._owners.get(task_id) != from_agent:
            return False, (f"task {task_id} is not owned by {from_agent}, "
                           f"so it cannot be handed over by them")
        self._owners[task_id] = to_agent
        return True, ""

    def owner(self, task_id: str) -> str:
        return self._owners.get(task_id, "")

    def validate_owner(self, task_id: str, agent_id: str) -> bool:
        """Ownership validation (§5-90) before a state write."""
        return self._owners.get(task_id) == agent_id


@dataclass
class TaskLease:
    """A task assignment lease (§5-82). When it expires, the
    assignment is inspected and resumed or reassigned — WITHOUT
    automatically repeating irreversible external actions: the
    lease carries completed_action_keys, and the next holder MUST
    skip them (see resume_or_reassign())."""
    task_id: str
    agent_id: str
    expires_at: float
    completed_action_keys: list = field(default_factory=list)
    progress_notes: list = field(default_factory=list)
    state: str = "active"  # active | expired | resumed | reassigned

    def expired(self, now: float | None = None) -> bool:
        at = now if now is not None else time.monotonic()
        return self.state == "active" and at > self.expires_at


def resume_or_reassign(lease: TaskLease, new_agent_id: str | None = None,
                       inspection: str = "") -> dict:
    """Lease expiry handling (§5-82): inspect, then resume (same
    agent) or reassign (new agent). Either way the returned plan
    EXCLUDES completed irreversible actions — they are never
    repeated automatically."""
    lease.state = "expired"
    if new_agent_id and new_agent_id != lease.agent_id:
        lease.state = "reassigned"
        holder = new_agent_id
    else:
        lease.state = "resumed"
        holder = lease.agent_id
    return {
        "task_id": lease.task_id, "holder": holder, "state": lease.state,
        "inspection": inspection or "lease expired; assignment inspected",
        "skip_action_keys": list(lease.completed_action_keys),
        "rule": ("actions already completed are skipped by the next "
                 "holder — irreversible steps are never repeated "
                 "automatically"),
    }


class AssignmentRegistry:
    """Assignment acknowledgment (83), duplicate-assignment detection
    (84), handoff acknowledgment (93), replacement policy (91)."""

    def __init__(self) -> None:
        self._assignments: dict[str, dict] = {}
        self._handoffs_acked: set[str] = set()

    def assign(self, task_id: str, agent_id: str) -> tuple[bool, str]:
        """Assign a task. A second live assignment of the same task
        to a different agent is duplicate assignment (84) and is
        refused — the first assignment stands until it is released
        or replaced."""
        current = self._assignments.get(task_id)
        if current and current["agent_id"] != agent_id \
                and current["state"] == "assigned":
            return False, (f"task {task_id} is already assigned to "
                           f"{current['agent_id']} — a second live "
                           f"assignment is refused")
        self._assignments[task_id] = {"agent_id": agent_id,
                                      "state": "assigned",
                                      "acknowledged": False}
        return True, ""

    def acknowledge(self, task_id: str, agent_id: str) -> bool:
        """Assignment acknowledgment (83)."""
        current = self._assignments.get(task_id)
        if current and current["agent_id"] == agent_id:
            current["acknowledged"] = True
            return True
        return False

    def acknowledge_handoff(self, handoff_id: str) -> bool:
        """Handoff acknowledgment (93)."""
        if handoff_id in self._handoffs_acked:
            return False
        self._handoffs_acked.add(handoff_id)
        return True

    def replace_agent(self, task_id: str, old_agent_id: str,
                      new_agent_id: str, reason: str) -> dict:
        """Agent replacement policy (91): the old assignment ends
        (timed-out, inactive, or failed), the new agent takes over
        the same task state — it does NOT restart the task from
        scratch, and irreversible actions stay done."""
        current = self._assignments.get(task_id)
        if not current or current["agent_id"] != old_agent_id:
            return {"replaced": False,
                    "reason": f"task {task_id} is not assigned to "
                              f"{old_agent_id}, so there is nothing to replace"}
        self._assignments[task_id] = {"agent_id": new_agent_id,
                                      "state": "assigned",
                                      "acknowledged": False,
                                      "replaces": old_agent_id}
        return {"replaced": True, "task_id": task_id,
                "old_agent": old_agent_id, "new_agent": new_agent_id,
                "reason": reason,
                "rule": "the new agent continues the task state; "
                        "completed irreversible actions are not repeated"}


class ResultDedup:
    """Agent-result dedup (§5-92): the same task result delivered
    twice (same task id + same result hash) is recorded once."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    @staticmethod
    def _key(task_id: str, result: Any) -> str:
        raw = json.dumps(result, sort_keys=True, default=str).encode()
        return f"{task_id}:{hashlib.sha256(raw).hexdigest()}"

    def is_duplicate(self, task_id: str, result: Any) -> bool:
        key = self._key(task_id, result)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class ConcurrencyGate:
    """Concurrency limits (§5-89) as a counting gate."""

    def __init__(self, limit: int):
        self.limit = max(1, limit)
        self._in_use = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._in_use >= self.limit:
                return False
            self._in_use += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._in_use = max(0, self._in_use - 1)


def check_output_length(text: str) -> tuple[bool, str]:
    """Agent-output length limits (§5-86). Over-limit output is
    refused with plain words, never silently cut."""
    if len(text) > MAX_AGENT_OUTPUT_CHARS:
        return False, (f"agent output is too long ({len(text):,} "
                       f"characters; the limit is "
                       f"{MAX_AGENT_OUTPUT_CHARS:,}) — shorten it")
    return True, ""


def is_fresh(record: dict, max_age_seconds: float,
             now_iso_value: str = "") -> bool:
    """Shared-result freshness checking (§5-87): a shared result
    counts as fresh only when its recorded time is within the age
    limit. Records without a time are never treated as fresh."""
    recorded = record.get("recorded_at") or record.get("at") or ""
    if not recorded:
        return False
    try:
        from datetime import datetime
        stamp = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
        now = (datetime.fromisoformat(now_iso_value.replace("Z", "+00:00"))
               if now_iso_value else datetime.now(stamp.tzinfo))
        return (now - stamp).total_seconds() <= max_age_seconds
    except ValueError:
        return False


@dataclass
class EvidenceRef:
    """A REFERENCE to verified research (D9, top-15 #9) — ids and a
    pointer only. The document body is NEVER carried here; agents
    share the reference and fetch the document themselves when they
    need it."""
    ref_id: str
    source_id: str
    pointer: str  # e.g. "artifact:<id>" or "source:<id>#excerpt"
    verified: bool = True
    recorded_at: str = ""

    def __post_init__(self) -> None:
        self.recorded_at = self.recorded_at or now_iso()

    def to_dict(self) -> dict:
        return {"ref_id": self.ref_id, "source_id": self.source_id,
                "pointer": self.pointer, "verified": self.verified,
                "recorded_at": self.recorded_at}


def make_evidence_ref(source_id: str, pointer: str,
                      verified: bool) -> EvidenceRef:
    """Only VERIFIED research may be referenced (D9). Unverified
    research is refused, never referenced as if it were verified."""
    if not verified:
        raise ValueError(
            "only verified research can be shared as an evidence "
            "reference — unverified research must not be passed on")
    return EvidenceRef(ref_id=new_id("evref"), source_id=source_id,
                       pointer=pointer, verified=True)


def flag_contradiction(claim_a: str, claim_b: str, reason: str) -> dict:
    """Contradiction metadata (D10, §5-96): conflicting claims carry
    their contradiction explicitly, as unresolved until evidence
    decides it."""
    return {"claims": [claim_a, claim_b], "reason": reason,
            "status": "unresolved",
            "detail": ("these two claims conflict; the conflict is "
                       "recorded explicitly until evidence resolves it")}


class AgentAuditLog:
    """Lifecycle auditing per agent (§5-100): every lifecycle event
    for an agent, timestamped, in order."""

    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = {}

    def record(self, agent_id: str, event: str, detail: str = "") -> None:
        self._events.setdefault(agent_id, []).append(
            {"event": event, "detail": detail, "at": now_iso()})

    def events_for(self, agent_id: str) -> list[dict]:
        return list(self._events.get(agent_id, []))


# Re-exports for one import surface (§5-94/95/97 live in multiagent).
from .multiagent import Scheduler, accept_partial_result  # noqa: E402
