"""AgentQon — domain contracts and state machines.

Implements the textbook's frozen contracts (Ch.3, 4, 6, 9, 11, 14, 30, 31):
mission contract, task contract, capability manifest, claim ledger,
evaluation record, event envelope, error envelope, and the mission /
agent-lifecycle state machines. Unknown fields are rejected on
security-sensitive contracts (extra='forbid').
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- state machines

class MissionStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Legal mission transitions (Ch.17/30/31). awaiting_approval -> completed is
# deliberately absent: only a verified approval + tool result can complete
# a mission, never an agent statement.
MISSION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"planned", "cancelled"},
    "planned": {"running", "cancelled"},
    "running": {"awaiting_approval", "completed", "failed", "cancelled", "paused"},
    "awaiting_approval": {"running", "failed", "cancelled"},
    "paused": {"running", "cancelled", "failed"},
    "completed": set(),
    # A failed mission may be reset to draft ONLY by an operator action —
    # replaying its dead-letter job (Ch.19: controlled replay). It can never
    # jump straight back to running.
    "failed": {"draft"},
    "cancelled": set(),
}


class StateConflict(Exception):
    """Raised when a state transition is not legal."""


def assert_transition(current: str, target: str, machine: dict[str, set[str]], label: str) -> None:
    if target not in machine.get(current, set()):
        raise StateConflict(f"{label} cannot transition from {current} to {target}")


class AgentLifecycle(str, Enum):
    DRAFT = "draft"
    TESTING = "testing"
    APPROVED = "approved"
    ACTIVE = "active"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


AGENT_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"testing"},
    "testing": {"approved", "draft"},
    "approved": {"active"},
    "active": {"degraded", "quarantined", "deprecated"},
    "degraded": {"active", "quarantined", "deprecated"},
    "quarantined": {"testing", "deprecated", "retired"},
    "deprecated": {"retired"},
    "retired": set(),
}


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


# ---------------------------------------------------------------- mission contract (Ch.4)

KNOWN_TOOLS = {
    "web.read", "artifact.write", "artifact.read", "code.execute",
    "email.send", "message.send", "purchase.create", "file.delete",
    "deploy.production",
    # v21 reach pack (read-only; per-channel enablement in the gateway)
    "reach.read", "reach.rss", "reach.github", "reach.youtube",
    "reach.reddit", "reach.exa",
    # v21 native code graph (read-only, per-workspace)
    "code.symbol_context", "code.impact", "code.search",
}
HIGH_RISK_TOOLS = {"email.send", "message.send", "purchase.create", "file.delete", "deploy.production"}


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: list[str] = Field(default_factory=lambda: ["web.read", "artifact.write"])
    deny: list[str] = Field(default_factory=list)
    # Phase 5: explicit live-web override. None = classification decides
    # (public/internal research live; confidential/restricted stay on the
    # local corpus because search queries would egress mission content).
    live_web: Optional[bool] = None

    @field_validator("allow", "deny")
    @classmethod
    def _known_tools(cls, v: list[str]) -> list[str]:
        unknown = [t for t in v if t not in KNOWN_TOOLS]
        if unknown:
            raise ValueError(f"Unsupported tool names rejected: {unknown}")
        return v


# v21.3: budgets are no longer user-facing. Missions carry no budget a
# user can set or hit; this single internal ceiling is the only cap in
# the system, and it exists purely as runaway-loop protection. Spend is
# still recorded in the usage ledger (accounting, not a control).
MISSION_BUDGET_CEILING_USD = 1000.0


class MissionConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline: Optional[str] = None
    # Retained for contract compatibility; not user-facing and no
    # longer an enforcement input — enforcement uses the internal
    # ceiling above.
    max_cost_usd: float = Field(default=MISSION_BUDGET_CEILING_USD, gt=0, le=1000)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_side_effects: str = Field(default="required", pattern="^(required|not_required)$")


class MissionContract(BaseModel):
    """The validated mission contract stored with every mission (Ch.4)."""
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1, max_length=12000)
    deliverables: list[str] = Field(min_length=1, max_length=20)
    constraints: MissionConstraints = Field(default_factory=MissionConstraints)
    tools: ToolPolicy = Field(default_factory=ToolPolicy)
    acceptance: list[str] = Field(min_length=1, max_length=30)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    data_classification: str = Field(default="internal", pattern="^(public|internal|confidential|restricted)$")
    # V3 routing inputs (Master Build §112): optional BYOK connection,
    # routing mode and pinned model. Absent = platform default connection.
    connection_id: str | None = None
    # v12: general-purpose missions. None = the platform classifies the
    # objective at creation (see app/tasktypes.py); an explicit value
    # always wins. "strategy" is the original comparison pipeline.
    task_type: str | None = Field(
        default=None,
        pattern="^(strategy|research|writing|code|plan|qa|general)$")
    # v21: mission modes — a named way of working (role pack + phase
    # pipeline + deliverable shape) layered on the contract; see
    # app/modes.py. None = the platform suggests one at creation for
    # unambiguous objectives, else the standard pipeline runs.
    mode: str | None = Field(
        default=None,
        pattern="^(software_company|trading_firm|build_chain|society)$")
    # v21: per-mode configuration (build_chain phase list today).
    mode_config: dict | None = None
    routing_mode: str | None = Field(
        default=None,
        pattern="^(lowest_cost_acceptable|balanced|quality_within_budget|fastest_eligible|pinned_model|task_aware_auto)$")
    pinned_model_id: str | None = None


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    contract: MissionContract
    project_id: str | None = None


class MemoryWrite(BaseModel):
    """v21: one scoped long-term-memory fact (app/memory.py)."""
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(pattern="^(user|agent|session)$")
    scope_ref: str = Field(default="", max_length=200)
    topic: str = Field(min_length=1, max_length=120)
    fact: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- task contract (Ch.30)

class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(default=2, ge=1, le=5)
    retryable: list[str] = Field(default_factory=lambda: ["TIMEOUT", "RATE_LIMITED"])


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    amount: float = Field(ge=0)


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    mission_id: str
    description: str
    kind: str  # research | critique | synthesis
    dependencies: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    output_schema_version: str = "candidate-v1"
    acceptance_tests: list[str] = Field(default_factory=list)
    permission_scope: list[str] = Field(default_factory=list)
    budget_limit: Budget = Field(default_factory=lambda: Budget(amount=2.0))
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    status: str = "queued"


# ---------------------------------------------------------------- capability manifest (Ch.6)

class DataHandling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention: str = "none"
    training_use: str = "no"  # 'unknown' blocks sensitive inputs until reviewed


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrency: int = 3
    timeout_seconds: int = 90


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    version: str
    owner: str = "internal-curation"
    description: str
    capabilities: list[str]
    modalities: dict[str, list[str]] = Field(default_factory=lambda: {"input": ["text"], "output": ["text", "json"]})
    data_handling: DataHandling = Field(default_factory=DataHandling)
    permissions_requested: list[str] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    lifecycle: str = "active"
    strategy: str = ""  # for research agents: the distinct approach they take
    origin: str = ""        # provenance, e.g. "agency-agents@<commit>" (MIT)
    persona_ref: str = ""   # key into the persona store for persona agents
    quality: float = Field(default=4.0, ge=0, le=5)       # measured, seed values for the slice
    reliability: float = Field(default=0.95, ge=0, le=1)
    latency_ms: int = 900
    cost_per_run_usd: float = 0.35


# ---------------------------------------------------------------- claims & evidence (Ch.11)

class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    title: str
    kind: str = "secondary"  # primary | secondary | opinion | unverified
    published_at: str
    retrieved_at: str
    excerpt: str
    # Phase 5 provenance for live-retrieved sources (seed records omit).
    url: Optional[str] = None
    publisher: Optional[str] = None
    content_hash: Optional[str] = None
    retrieval_method: Optional[str] = None  # e.g. "live-web:duckduckgo"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(default_factory=lambda: new_id("clm"))
    text: str
    claim_type: str = "factual"
    source_ids: list[str] = Field(default_factory=list)
    confidence_category: str = "supported"  # supported | contested | unsupported
    verification_status: str = "unchecked"  # unchecked | checked


# ---------------------------------------------------------------- candidates & evaluation (Ch.10, 14)

class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(default_factory=lambda: new_id("cand"))
    mission_id: str
    title: str
    strategy: str
    summary: str
    agent_version_id: str
    claims: list[Claim] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = 0.0
    time_to_learn: str = ""
    # v12: for non-strategy task types, the actual work product (the
    # draft / answer / code / plan) in Markdown. None for strategy
    # candidates, whose product is the strategy itself.
    deliverable: str | None = None
    status: str = "proposed"  # proposed | selected
    version: int = 1          # V3: revision rounds create new versions
    revises: str | None = None  # candidate_id this version revises


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(ge=0, le=5)
    max: float = 5
    notes: str = ""


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str = Field(default_factory=lambda: new_id("eval"))
    candidate_id: str
    rubric_version: str = "research-v1"
    dimensions: dict[str, DimensionScore] = Field(default_factory=dict)
    deterministic_tests: list[dict[str, Any]] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    cost_usd: float = 0.0
    elapsed_ms: int = 0


class Critique(BaseModel):
    """Structured critique (Ch.10): free-text rants are not accepted."""
    model_config = ConfigDict(extra="forbid")
    critique_id: str = Field(default_factory=lambda: new_id("crt"))
    target_candidate_id: str
    target_claim: str = Field(min_length=1)
    issue: str = Field(min_length=1)
    evidence_or_test: str = Field(min_length=1)
    severity: str = Field(pattern="^(low|medium|high)$")
    suggested_correction: str = Field(min_length=1)
    confidence: str = Field(default="medium", pattern="^(low|medium|high)$")
    critic_agent_version_id: str = ""


# ---------------------------------------------------------------- envelopes (Ch.18, 31)

def error_envelope(code: str, message: str, correlation_id: str, retryable: bool = False,
                   detail: str | None = None) -> dict:
    error = {"code": code, "message": message,
             "correlation_id": correlation_id, "retryable": retryable}
    if detail is not None:
        # v21.4.2: operator-visible detail, attached only when the
        # deployment mode allows it (see main.internal_error).
        error["detail"] = detail
    return {"error": error}


def event_envelope(tenant_id: str, aggregate_type: str, aggregate_id: str,
                   event_type: str, payload: dict) -> dict:
    return {
        "event_id": new_id("evt"),
        "tenant_id": tenant_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "schema_version": "1.0",
        "occurred_at": now_iso(),
        "correlation_id": new_id("corr"),
        "payload": payload,
    }


def content_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:60] or "mission"
