"""Eleventh audit document: retrieval/memory, safe evolution, rigorous
testing, scheduling/resources/execution, data governance, and mission /
product capabilities (36 items; 2 conditional on vector retrieval, which
does not exist — retrieval is SQLite FTS5 keyword search with BM25).

Pure standard-library functions and small in-memory classes. No new
runtime dependencies, no network, no key material handled anywhere (the
key-rotation monitor records ages and statuses only, never keys), and no
user-facing text uses raw JSON, bare HTTP codes, or jargon: every refusal
or warning is a plain sentence.

Foundations mapping (the document's own strategic point): items ride on
the eight reusable systems — Action, Artifact, Event, Permission,
Recovery, Verification, Usage Accounting, Project Context — and the
per-item mapping lives in GAP_W15_ELEVENTH_REPORT.md.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- Group 1
_INSTRUCTION_PATTERNS = [
    r"ignore (all )?(previous|earlier|prior) instructions",
    r"disregard (all )?(previous|earlier|prior) instructions",
    r"you are now (a|an|the)",
    r"new instructions?:",
    r"override (your|the) (system|rules|instructions)",
    r"do not (tell|reveal|mention).*user",
    r"send .* to (an? )?(external|outside)",
]


def scan_document_for_poisoning(text: str, *, author: str,
                                prior_submissions: list | None = None,
                                prior_hash: str | None = None) -> dict:
    """(1) Knowledge-Base Poisoning Detector. Flags embedded instructions
    aimed at agents, coordinated duplicate submissions, and abrupt changes
    (a document whose content hash changed while its identity did not)."""
    flags: list[str] = []
    lowered = text.lower()
    for pat in _INSTRUCTION_PATTERNS:
        if re.search(pat, lowered):
            flags.append("This document contains instructions aimed at an agent.")
            break
    digest = hashlib.sha256(text.encode()).hexdigest()
    if prior_hash is not None and prior_hash != digest:
        flags.append("This document changed abruptly without an explained reason.")
    duplicates = 0
    if prior_submissions:
        for prior in prior_submissions:
            if prior.get("hash") == digest and prior.get("author") != author:
                duplicates += 1
    if duplicates >= 2:
        flags.append("The same document was submitted by several different authors.")
    return {"hash": digest, "suspicious": bool(flags), "flags": flags,
            "duplicate_submissions": duplicates}


def plan_retrieval(query: str, *, vector_available: bool = False) -> dict:
    """(4) Hybrid Retrieval Planner — conditional. Retrieval today is
    keyword search only, so vector search is never selected. When vector
    retrieval exists, this is the cutover point: the planner gains a
    vector branch and retrieval-quality comparisons before cutover.
    Item 2 (Embedding-Model Migration Manager) is likewise NOT_APPLICABLE
    until a vector index exists; fabricating one to justify it would be
    dishonest, so neither is built beyond this honest guard."""
    if vector_available:
        return {"query": query, "route": "combined",
                "note": "Vector search is available; keyword and vector results are combined."}
    return {"query": query, "route": "keyword",
            "note": "Vector search is not available yet, so keyword search is used."}


def check_freshness(source_updated_at: float, indexed_at: float,
                    slo_seconds: float) -> dict:
    """(3) Retrieval Freshness SLO. Generalizes the provider-catalog
    freshness label and CodeGraph staleness tracking to any indexed
    document: the searchable copy is behind its source when the lag
    exceeds the target."""
    lag = max(0.0, indexed_at - source_updated_at) if indexed_at >= source_updated_at else 0.0
    # Lag is measured from source change to index time; a copy indexed
    # before the latest source change is behind by the full gap.
    if indexed_at < source_updated_at:
        lag = source_updated_at - indexed_at
        behind = True
    else:
        behind = False
    breached = lag > slo_seconds
    return {"lag_seconds": lag, "target_seconds": slo_seconds,
            "behind_source": behind, "breach": breached,
            "status": ("The searchable copy is behind its source."
                       if breached else "The searchable copy is up to date.")}


class DependencyMap:
    """(5) Derived-Knowledge Dependency Map. Records which summaries,
    cached answers, and reports derive from each source, so a change or
    deletion lists exactly what needs reprocessing."""

    def __init__(self) -> None:
        self._derived: dict[str, list[dict]] = {}

    def register(self, source: str, derived: str, kind: str) -> None:
        self._derived.setdefault(source, [])
        if all(d["name"] != derived for d in self._derived[source]):
            self._derived[source].append({"name": derived, "kind": kind})

    def affected_by_change(self, source: str) -> list[dict]:
        return list(self._derived.get(source, []))

    def affected_by_deletion(self, source: str) -> dict:
        affected = self.affected_by_change(source)
        return {"source": source, "needs_reprocessing": affected,
                "note": ("These derived items must be rebuilt or removed "
                         "because their source was deleted.")}


SCOPES = ("session", "project", "shared", "global")
# (6) Memory Scope Enforcement. Allowed moves, in plain words. Project
# scope is the gap this closes: session and project memory stay inside
# their tenant; shared memory needs an explicit approval; global
# preferences are read-only copies, never a path for project data out.
_ALLOWED_MOVES = {
    ("session", "project"), ("project", "session"),
    ("project", "shared"), ("shared", "project"),
    ("global", "session"), ("global", "project"),
}


def memory_scope_move_allowed(from_scope: str, to_scope: str, *,
                              approved: bool = False,
                              sensitive: bool = False) -> dict:
    if from_scope not in SCOPES or to_scope not in SCOPES:
        return {"allowed": False, "reason": "That memory scope is not known."}
    if sensitive:
        return {"allowed": False,
                "reason": "Sensitive material cannot move between memory scopes."}
    if (from_scope, to_scope) not in _ALLOWED_MOVES:
        return {"allowed": False,
                "reason": "Material cannot move from that scope to that scope."}
    if to_scope == "shared" and not approved:
        return {"allowed": False,
                "reason": "Moving material to shared memory needs approval first."}
    return {"allowed": True, "reason": "The move stays inside the allowed scopes."}


# ---------------------------------------------------------------- Group 2
class ProposalQueue:
    """(7) Agent Improvement Proposal Queue. Generalizes the evolve
    lessons system: a proposal is NEVER applied directly. It passes
    testing and a human review first; applying without either is refused."""

    def __init__(self) -> None:
        self._proposals: dict[str, dict] = {}
        self._seq = 0

    def submit(self, agent: str, kind: str, change: str, reason: str) -> dict:
        self._seq += 1
        pid = f"prop-{self._seq}"
        rec = {"id": pid, "agent": agent, "kind": kind, "change": change,
               "reason": reason, "status": "waiting for testing and review",
               "tests_passed": False, "reviewer": None}
        self._proposals[pid] = rec
        return rec

    def record_tests(self, pid: str, passed: bool) -> dict:
        rec = self._proposals[pid]
        rec["tests_passed"] = passed
        return rec

    def review(self, pid: str, reviewer: str, approve: bool) -> dict:
        rec = self._proposals[pid]
        rec["reviewer"] = reviewer
        rec["status"] = "approved" if approve else "rejected"
        return rec

    def apply(self, pid: str) -> dict:
        rec = self._proposals[pid]
        if rec["status"] != "approved":
            return {"applied": False,
                    "reason": "This change has not been approved yet."}
        if not rec["tests_passed"]:
            return {"applied": False,
                    "reason": "This change has not passed testing yet."}
        rec["status"] = "applied"
        return {"applied": True, "proposal": rec}


PROTECTED_SELF_MOD_TARGETS = ("runtime", "own permissions",
                              "evaluation criteria", "safeguards")


def check_self_modification(actor: str, target: str, action: str) -> dict:
    """(8) Self-Modification Boundary. An agent may never silently change
    the runtime, its own permissions, its evaluation criteria, or the
    safeguards that govern it. Generalizes the Shield, the Write Law,
    and the evolve contract's never-self-modify rule."""
    if target in PROTECTED_SELF_MOD_TARGETS:
        return {"allowed": False,
                "reason": (f"An agent cannot change {target} by itself. "
                           "That change needs a person to approve it.")}
    return {"allowed": True, "reason": "This change stays outside the protected boundary."}


def run_canary(version: str, missions: list, results: list) -> dict:
    """(9) Canary Mission Runner. A new agent, prompt, or skill version
    runs on a controlled set of representative missions first. Rollout
    only when the set passes; otherwise the version is held back."""
    if not missions:
        return {"version": version, "decision": "held",
                "reason": "No representative missions were supplied."}
    passed = sum(1 for r in results if r.get("passed"))
    rate = passed / len(missions) if missions else 0.0
    decision = "roll out" if rate >= 0.8 and len(results) >= len(missions) else "held"
    return {"version": version, "missions": len(missions), "passed": passed,
            "pass_rate": rate, "decision": decision,
            "reason": ("The version passed the controlled missions."
                       if decision == "roll out"
                       else "The version did not pass enough controlled missions.")}


def analyze_cohorts(records: list) -> dict:
    """(10) Agent Performance Cohort Analysis. Generalizes the arena
    performance ledger: group runs by category, difficulty, project type,
    and resource conditions, and report success and cost per group."""
    groups: dict[str, dict] = {}
    for rec in records:
        key = "|".join(str(rec.get(k, "unknown")) for k in
                       ("category", "difficulty", "project_type", "resources"))
        g = groups.setdefault(key, {"runs": 0, "successes": 0, "total_cost": 0.0})
        g["runs"] += 1
        g["successes"] += 1 if rec.get("success") else 0
        g["total_cost"] += float(rec.get("cost", 0.0))
    out = {}
    for key, g in groups.items():
        out[key] = {**g, "success_rate": g["successes"] / g["runs"] if g["runs"] else 0.0}
    return out


def evaluate_capability(name: str, benchmark_pass_rate: float,
                        recent_failures: int, *, promote_at: float = 0.8,
                        retire_after: int = 5) -> dict:
    """(11) Capability Promotion and Retirement Rules. Promote after
    defined benchmarks; flag for retirement after repeated failures."""
    if benchmark_pass_rate >= promote_at:
        return {"name": name, "decision": "promote",
                "reason": "The capability met the benchmark for promotion."}
    if recent_failures >= retire_after:
        return {"name": name, "decision": "flag for retirement",
                "reason": "The capability failed repeatedly and should be retired."}
    return {"name": name, "decision": "keep",
            "reason": "The capability neither met promotion nor retirement rules."}


class ConsentGate:
    """(12) Learning-Data Consent Gate. Mission outputs, corrections,
    and feedback may be reused for evaluation or improvement only when
    consent covers that purpose; sensitive project data is excluded."""

    def __init__(self) -> None:
        self._grants: dict[str, set] = {}
        self._sensitive: set = set()

    def mark_sensitive(self, record_id: str) -> None:
        self._sensitive.add(record_id)

    def grant(self, record_id: str, purposes: list) -> None:
        self._grants.setdefault(record_id, set()).update(purposes)

    def withdraw(self, record_id: str) -> None:
        self._grants.pop(record_id, None)

    def can_reuse(self, record_id: str, purpose: str) -> dict:
        if record_id in self._sensitive:
            return {"allowed": False,
                    "reason": "This record is sensitive and is excluded from reuse."}
        if purpose in self._grants.get(record_id, set()):
            return {"allowed": True, "reason": "Consent covers this use."}
        return {"allowed": False,
                "reason": "Consent does not cover this use."}


# ---------------------------------------------------------------- Group 3
def check_metamorphic(fn, base_input, variants: list) -> dict:
    """(13) Metamorphic Testing Engine. Harmless changes to the input
    (formatting, spacing, ordering that should not matter) must not
    change the result."""
    base = fn(base_input)
    failures = []
    for variant in variants:
        try:
            got = fn(variant)
        except Exception as exc:  # noqa: BLE001 - variant failure is data
            failures.append({"variant": variant, "error": str(exc)})
            continue
        if got != base:
            failures.append({"variant": variant, "expected": base, "got": got})
    return {"base_result": base, "variants_checked": len(variants),
            "passed": not failures, "failures": failures}


def compare_runs(run_a: dict, run_b: dict) -> dict:
    """(14) Differential Execution Lab. Compare two runs of equivalent
    tasks across providers, runtime versions, or configurations."""
    diffs = {}
    for key in ("artifacts", "errors", "duration_seconds", "cost"):
        if run_a.get(key) != run_b.get(key):
            diffs[key] = {"first": run_a.get(key), "second": run_b.get(key)}
    return {"same_behavior": not diffs, "differences": diffs}


def minimize_tests(cases: list, coverage_fn) -> list:
    """(15) Test-Suite Minimizer. Greedy removal: drop a case only when
    the remaining set still covers everything the full set covered."""
    full = coverage_fn(cases)
    kept = list(cases)
    for case in list(cases):
        trial = [c for c in kept if c is not case]
        if coverage_fn(trial) == full:
            kept = trial
    return kept


def reduce_reproducer(items: list, still_fails) -> list:
    """(16) Minimal Failure Reproducer. Shrinks a failing input list to
    the smallest subset that still reproduces the failure."""
    current = list(items)
    changed = True
    while changed:
        changed = False
        for i in range(len(current)):
            trial = current[:i] + current[i + 1:]
            if trial and still_fails(trial):
                current = trial
                changed = True
                break
    return current


NEGATIVE_CASES = [
    {"id": "write-outside-allowed-directory",
     "description": "Write a file outside the allowed directory."},
    {"id": "use-unapproved-external-service",
     "description": "Call an external service that was not approved."},
    {"id": "read-other-tenant-data",
     "description": "Read data belonging to another tenant."},
    {"id": "modify-harness-files",
     "description": "Change the harness's own files."},
]


def run_negative_capability_suite(attempt) -> dict:
    """(17) Negative-Capability Test Suite, user-facing form of the
    harness's internal security suites: each prohibited action is
    attempted through the supplied adapter, and the suite passes only
    when every attempt is refused."""
    results = []
    for case in NEGATIVE_CASES:
        try:
            allowed = attempt(case["id"])
        except Exception:  # noqa: BLE001 - refusal by exception counts
            allowed = False
        results.append({"case": case["id"], "refused": not allowed})
    return {"passed": all(r["refused"] for r in results), "results": results}


def check_behavioral_invariants(events: list) -> dict:
    """(18) Behavioral Invariant Library. Reusable assertions, in plain
    words: a cancelled mission must not start new outside writes, and a
    failed check cannot produce a verified result."""
    violations = []
    cancelled_at = None
    for ev in events:
        if ev.get("type") == "mission_cancelled":
            cancelled_at = ev.get("seq", 0)
        if cancelled_at is not None and ev.get("type") == "external_write" \
                and ev.get("seq", 0) > cancelled_at:
            violations.append("A cancelled mission started a new outside write.")
        if ev.get("type") == "verified" and ev.get("validator_passed") is False:
            violations.append("A failed check produced a verified result.")
    return {"passed": not violations, "violations": violations}


# ---------------------------------------------------------------- Group 4
class SharedBudgetPool:
    """(19) Atomic Budget Reservation, shared-pool layer. The store's
    reserve_budget already guards a single mission's cap atomically
    (conditional UPDATE); this pool extends the same guarantee across
    concurrent missions sharing one allowance, so parallel tasks cannot
    independently spend the same remaining money. Thread-safe."""

    def __init__(self, total_cap: float) -> None:
        self._cap = total_cap
        self._reserved = 0.0
        self._lock = threading.Lock()
        self._holds: dict[str, float] = {}

    def reserve(self, mission_id: str, amount: float) -> dict:
        if amount <= 0:
            return {"reserved": False, "reason": "The amount must be positive."}
        with self._lock:
            if self._reserved + amount > self._cap:
                return {"reserved": False,
                        "reason": "That reservation would exceed the shared allowance."}
            self._reserved += amount
            self._holds[mission_id] = self._holds.get(mission_id, 0.0) + amount
        return {"reserved": True, "remaining": self._cap - self._reserved}

    def release(self, mission_id: str, amount: float) -> None:
        with self._lock:
            held = self._holds.get(mission_id, 0.0)
            freed = min(held, amount)
            self._holds[mission_id] = held - freed
            self._reserved -= freed

    @property
    def reserved(self) -> float:
        with self._lock:
            return self._reserved


def forecast_quota(upcoming: list, rate_limit_per_minute: float,
                   concurrency_limit: int) -> dict:
    """(20) Provider Quota Forecasting. Estimates whether upcoming work
    exceeds rate or concurrency limits, so work can be scheduled first
    instead of failing mid-run."""
    needed_per_minute = sum(float(t.get("calls_per_minute", 0)) for t in upcoming)
    peak_concurrent = max((int(t.get("concurrent", 1)) for t in upcoming), default=0)
    over_rate = needed_per_minute > rate_limit_per_minute
    over_concurrency = peak_concurrent > concurrency_limit
    return {"needed_per_minute": needed_per_minute,
            "rate_limit_per_minute": rate_limit_per_minute,
            "peak_concurrent": peak_concurrent,
            "concurrency_limit": concurrency_limit,
            "exceeds": over_rate or over_concurrency,
            "advice": ("Spread the work out over time before starting."
                       if over_rate or over_concurrency
                       else "The upcoming work fits inside the limits.")}


def escalation_decision(local_capable: bool, sensitive: bool,
                        user_allows_cloud: bool, needs_cloud_capability: bool) -> dict:
    """(21) Local-to-Cloud Escalation Gate. A local model is used when
    it can do the job; cloud escalation needs a real capability reason,
    the user's permission, and non-sensitive data."""
    if sensitive and not user_allows_cloud:
        return {"decision": "local",
                "reason": "The data is sensitive and cloud use was not allowed."}
    if local_capable and not needs_cloud_capability:
        return {"decision": "local",
                "reason": "The local model can do this job."}
    if not user_allows_cloud:
        return {"decision": "local",
                "reason": "Cloud use was not allowed, so the local model is used."}
    return {"decision": "cloud",
            "reason": "The job needs a capability only the cloud model has."}


def recalculate_deadline(deadline_seconds: float, remaining_work: float,
                         rate_per_second: float, disruptions: list) -> dict:
    """(22) Deadline Feasibility Recalculation. On failure, slowdown, or
    a new requirement, re-estimate which deliverables are at risk."""
    if rate_per_second <= 0:
        return {"feasible": False, "at_risk": ["all remaining work"],
                "reason": "No progress rate is available to estimate with."}
    needed = remaining_work / rate_per_second
    extra = sum(float(d.get("delay_seconds", 0)) for d in disruptions)
    feasible = needed + extra <= deadline_seconds
    return {"feasible": feasible, "needed_seconds": needed + extra,
            "deadline_seconds": deadline_seconds,
            "at_risk": ([] if feasible else ["deliverables due by this deadline"]),
            "reason": ("The deadline can still be met."
                       if feasible else "The deadline can no longer be met at this pace.")}


class ShutdownCoordinator:
    """(23) Graceful Shutdown Coordinator. Generalizes drain + stop:
    stop accepting new work, finish or checkpoint what is safe, release
    resources, and keep recoverable state."""

    def __init__(self) -> None:
        self.accepting = True
        self.checkpointed: list = []
        self.released = False

    def begin_drain(self) -> dict:
        self.accepting = False
        return {"accepting_new_work": False,
                "note": "No new work is accepted; running work finishes or is checkpointed."}

    def checkpoint(self, item: str) -> None:
        self.checkpointed.append(item)

    def finish(self) -> dict:
        self.released = True
        return {"accepting_new_work": self.accepting,
                "checkpointed": list(self.checkpointed),
                "resources_released": True,
                "recoverable_state": "kept"}


class ResourcePool:
    """(24) Resource Reservation Isolation. Caps per mission for memory,
    workers, provider concurrency, and storage, with a reserve kept for
    high-priority missions so one mission cannot starve the others."""

    def __init__(self, totals: dict, high_priority_reserve: dict | None = None) -> None:
        self._totals = dict(totals)
        self._used: dict[str, float] = {k: 0.0 for k in totals}
        self._reserve = dict(high_priority_reserve or {})
        self._lock = threading.Lock()

    def acquire(self, mission_id: str, request: dict, high_priority: bool = False) -> dict:
        with self._lock:
            for resource, amount in request.items():
                available = self._totals.get(resource, 0) - self._used.get(resource, 0)
                if not high_priority:
                    available -= self._reserve.get(resource, 0)
                if amount > available:
                    return {"granted": False,
                            "reason": f"Not enough {resource} is available for this mission."}
            for resource, amount in request.items():
                self._used[resource] = self._used.get(resource, 0) + amount
        return {"granted": True, "mission": mission_id}


# ---------------------------------------------------------------- Group 5
def simulate_retention(records: list, policy: dict) -> dict:
    """(25) Retention-Policy Simulator. Previews what a proposed policy
    would expire BEFORE it is activated. Nothing is deleted here."""
    max_age = float(policy.get("max_age_seconds", 0))
    now = time.time()
    would_expire = [r["id"] for r in records
                    if now - float(r.get("created_at", now)) > max_age]
    return {"would_expire": would_expire, "would_keep": len(records) - len(would_expire),
            "note": "This is a preview only; nothing was deleted."}


def propagate_consent_withdrawal(record_id: str, dependency_map: DependencyMap) -> dict:
    """(26) Consent Withdrawal Propagation. On withdrawal, every derived
    copy — summaries, stored copies, and other managed copies — is
    listed for review or removal. Rides on the Dependency Map (5)."""
    affected = dependency_map.affected_by_deletion(record_id)
    return {"withdrawn_record": record_id,
            "copies_needing_review_or_removal": affected["needs_reprocessing"]}


def check_legal_hold(deletion: dict, holds: list) -> dict:
    """(27) Legal-Hold Conflict Resolver. A deletion or retention rule
    that conflicts with a documented preservation requirement is
    stopped and routed for authorized resolution, never applied."""
    for hold in holds:
        if hold.get("record_id") == deletion.get("record_id"):
            return {"allowed": False, "conflict": True,
                    "hold": hold.get("reason", "A preservation requirement applies."),
                    "route": "This conflict needs an authorized person to resolve it."}
    return {"allowed": True, "conflict": False, "route": None}


class ShareRecord:
    """(28) Recipient-Bound Artifact Sharing. Expiring access, recipient
    restrictions, download controls where supported, and an auditable
    sharing record for sensitive deliverables."""

    def __init__(self, artifact: str, recipient: str, expires_at: float,
                 max_downloads: int = 1) -> None:
        self.artifact = artifact
        self.recipient = recipient
        self.expires_at = expires_at
        self.max_downloads = max_downloads
        self.downloads = 0
        self.access_log: list = []

    def check_access(self, requester: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if requester != self.recipient:
            self.access_log.append({"requester": requester, "allowed": False,
                                    "reason": "This share is bound to a different recipient."})
            return {"allowed": False, "reason": "This share is bound to a different recipient."}
        if now > self.expires_at:
            return {"allowed": False, "reason": "This share has expired."}
        if self.downloads >= self.max_downloads:
            return {"allowed": False, "reason": "This share has reached its download limit."}
        self.downloads += 1
        self.access_log.append({"requester": requester, "allowed": True})
        return {"allowed": True, "downloads_used": self.downloads}


@dataclass
class KeyStatus:
    """(29) Cryptographic Key-Rotation Monitor — metadata only. Ages,
    rotation requirements, dependent services, and migration status are
    tracked WITHOUT ever holding or exposing key material."""
    name: str
    created_at: float
    rotation_interval_seconds: float
    dependent_services: list = field(default_factory=list)
    migrated: bool = False

    def age_seconds(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.created_at

    def status(self, now: float | None = None) -> dict:
        age = self.age_seconds(now)
        due = age >= self.rotation_interval_seconds
        return {"name": self.name, "age_seconds": age,
                "rotation_due": due, "migrated": self.migrated,
                "dependent_services": list(self.dependent_services),
                "note": ("Rotation is due for this key."
                         if due else "Rotation is not due yet.")}


def propagate_attribution(inputs: list) -> list:
    """(30) Attribution Obligation Propagator. Carries attribution and
    license information forward when third-party code, text, or media
    is combined into a new deliverable. Generalizes library attribution."""
    seen: dict[str, dict] = {}
    for item in inputs:
        key = f"{item.get('source')}|{item.get('license', '')}"
        seen.setdefault(key, {"source": item.get("source"),
                              "license": item.get("license"),
                              "attribution": item.get("attribution", "")})
    return list(seen.values())


# ---------------------------------------------------------------- Group 6
def diff_runs(previous: dict, latest: dict) -> dict:
    """(31) Run-to-Run Outcome Diff. Compares the latest mission run
    with its previous run across coverage, artifacts, tests, costs,
    failures, and newly introduced risks."""
    out = {}
    for key in ("requirement_coverage", "artifacts", "test_results",
                "cost", "failures", "risks"):
        if previous.get(key) != latest.get(key):
            out[key] = {"previous": previous.get(key), "latest": latest.get(key)}
    return {"changed": out, "any_change": bool(out)}


class ProgressivePublisher:
    """(32) Progressive Deliverable Publishing. Completed artifacts are
    released incrementally with an explicit version and verification
    status; incomplete outputs are marked as drafts and can never be
    mistaken for a finished package."""

    def __init__(self) -> None:
        self.published: list = []

    def publish(self, artifact: str, version: str, verified: bool) -> dict:
        status = "finished" if verified else "draft"
        rec = {"artifact": artifact, "version": version, "status": status}
        self.published.append(rec)
        return rec


def record_recipe(mission: dict) -> dict:
    """(33) Task Recipe Recorder. Turns a successful mission into a
    reusable, parameterized recipe: prerequisites, inputs, tools,
    validation steps, and expected outputs."""
    if not mission.get("success"):
        return {"recorded": False,
                "reason": "Only a successful mission can become a recipe."}
    params = {}
    for key, value in (mission.get("inputs") or {}).items():
        params[key] = f"{{{key}}}"
    return {"recorded": True,
            "recipe": {"prerequisites": mission.get("prerequisites", []),
                       "inputs": params, "tools": mission.get("tools", []),
                       "validation_steps": mission.get("validation_steps", []),
                       "expected_outputs": mission.get("expected_outputs", [])}}


def preview_approval(action: str, targets: list, dependent_missions: list,
                     files: list) -> dict:
    """(34) Approval Impact Preview. Before approval, shows affected
    resources, dependent missions, relevant files, and likely downstream
    consequences. Generalizes the approvals action+target display and
    blast-radius preview."""
    return {"action": action, "affected_resources": list(targets),
            "dependent_missions": list(dependent_missions),
            "relevant_files": list(files),
            "downstream_consequences": (
                [f"Dependent mission {m} may be affected." for m in dependent_missions]
                or ["No dependent missions are affected."])}


def capability_impact_report(disabled: str, workflows: list) -> dict:
    """(35) Capability Dependency Impact Report. When a provider, model,
    tool, or skill is disabled or updated, lists the exact workflows
    and features that may stop working."""
    affected = [w for w in workflows if disabled in (w.get("depends_on") or [])]
    return {"disabled": disabled, "affected_workflows": affected,
            "unaffected_count": len(workflows) - len(affected)}


class MissionClosure:
    """(36) Mission Closure and Reopening Protocol. A defined closure
    record for completed missions; if new evidence appears later, the
    mission reopens while the original results are preserved and the
    reason the earlier conclusion is no longer sufficient is stated."""

    def __init__(self, mission_id: str, conclusion: str, results: dict) -> None:
        self.mission_id = mission_id
        self.original_conclusion = conclusion
        self.original_results = dict(results)
        self.status = "closed"
        self.reopen_reason: str | None = None

    def reopen(self, new_evidence: str) -> dict:
        self.status = "reopened"
        self.reopen_reason = new_evidence
        return {"mission": self.mission_id, "status": "reopened",
                "original_conclusion_preserved": self.original_conclusion,
                "original_results_preserved": self.original_results,
                "why_reopened": (f"New evidence appeared: {new_evidence}. "
                                 "The earlier conclusion is no longer sufficient on its own.")}
