"""AgentQon — trust-boundary security (gap-seventhdoc, group B).

Every control here operates at the EXECUTION BOUNDARY: these functions
are meant to be called by the gateway / tool layer before an action
runs, never merely written into a model's instructions. Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

UNTRUSTED_SOURCES = ("webpage", "uploaded_file", "email", "tool_result",
                     "external_agent")
# Destinations a tainted value may NOT reach without human review.
REVIEW_REQUIRED_DESTINATIONS = ("shell_command", "api_call", "decision")
SECRET_PATTERNS = [
    ("api_key", re.compile(r"sk-[A-Za-z0-9_\-]{8,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", re.I)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assignment", re.compile(r"(?i)(password|passwd|secret)\s*=\s*\S+")),
    ("token_assignment", re.compile(r"(?i)(api[_-]?key|access[_-]?token)\s*=\s*\S+")),
]


def taint(value: str, source: str) -> dict:
    """Mark content with its origin. Only the listed sources count as
    untrusted; content the user typed directly is trusted."""
    if source not in UNTRUSTED_SOURCES:
        raise ValueError(f"Unknown taint source: {source}")
    return {"value": value, "taint": source, "reviewed": False}


def trusted(value: str) -> dict:
    return {"value": value, "taint": None, "reviewed": False}


def combine_taint(*items: dict) -> Optional[str]:
    """Taint flows: any untrusted input taints the combined output."""
    for item in items:
        if item.get("taint"):
            return item["taint"]
    return None


def check_flow(item: dict, destination: str, reviewed: bool = False) -> dict:
    """Decide whether a (possibly tainted) value may reach a destination.
    Tainted -> shell / API / decision without review is BLOCKED."""
    t = item.get("taint")
    if t and destination in REVIEW_REQUIRED_DESTINATIONS and not reviewed:
        return {"allowed": False, "reason":
                f"Blocked: content from an untrusted {t} would reach "
                f"{destination.replace('_', ' ')} without review.",
                "taint": t, "destination": destination}
    return {"allowed": True, "reason": "Flow is allowed.", "taint": t,
            "destination": destination}


def _redact_snippet(text: str, start: int, end: int) -> str:
    shown = text[max(0, start - 12):start] + "[secret hidden]" + text[end:end + 12]
    return shown


def tripwire_scan(text: str) -> list[dict]:
    """Pre-send tripwire: find credential / token / private-config
    patterns WITHOUT echoing the secret itself (snippets are redacted)."""
    hits = []
    for kind, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            hits.append({"kind": kind,
                         "snippet": _redact_snippet(text, m.start(), m.end()),
                         "reason": f"A {kind.replace('_', ' ')} pattern was "
                         "found where it must not be sent."})
    return hits


def tripwire_check(payload: Any) -> dict:
    """Scan an external request, log line, artifact, or another agent's
    context BEFORE it is sent. Any hit blocks the send."""
    texts: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(payload)
    hits = []
    for t in texts:
        hits.extend(tripwire_scan(t))
    return {"blocked": bool(hits), "hits": hits,
            "reason": ("Blocked: sensitive material would have been sent "
                       "outside." if hits else "No sensitive material found.")}


# --- Data-egress policy engine (B8) --------------------------------------
CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2,
                       "restricted": 3}


def egress_decide(policy: dict, destination_domain: str, service: str,
                  data_classification: str) -> dict:
    """policy: {allowed_domains, allowed_services, deny_by_default}.
    Sensitive projects (confidential/restricted) are deny-by-default:
    only explicitly allowed destinations pass. This generalizes the live
    gateway rule (confidential missions may not use high-risk tools)."""
    rank = CLASSIFICATION_RANK.get(data_classification, 1)
    allowed_domains = set(policy.get("allowed_domains", []))
    allowed_services = set(policy.get("allowed_services", []))
    sensitive = rank >= 2
    deny_default = policy.get("deny_by_default", sensitive)
    if destination_domain in allowed_domains and service in allowed_services:
        return {"allowed": True, "reason": "Destination and service are on "
                "the mission's allowed list."}
    if deny_default:
        return {"allowed": False, "reason":
                "Blocked: this is a sensitive project and the destination "
                "is not on its allowed list (deny by default)."}
    if destination_domain in allowed_domains or service in allowed_services:
        return {"allowed": True, "reason": "Destination is permitted for "
                "this classification."}
    return {"allowed": False, "reason": "Destination is not permitted."}


# --- Cross-project memory firewall (B9) -----------------------------------
class MemoryFirewall:
    """Project-scoped isolation WITHIN a tenant, on top of W4's landed
    scope enforcement (mission/project/user scopes). This class does NOT
    re-implement scope enforcement: it is the explicit-share layer —
    a cross-project read is denied unless an auditable grant exists."""

    def __init__(self):
        self.grants: list[dict] = []
        self.audit: list[dict] = []

    def grant_share(self, tenant_id: str, from_project: str, to_project: str,
                    approver: str, scope_note: str = "") -> dict:
        grant = {"tenant_id": tenant_id, "from_project": from_project,
                 "to_project": to_project, "approver": approver,
                 "scope_note": scope_note}
        self.grants.append(grant)
        self.audit.append({"action": "grant", **grant})
        return grant

    def can_read(self, tenant_id: str, reader_project: str,
                 owner_project: str) -> dict:
        if reader_project == owner_project:
            return {"allowed": True, "reason": "Same project."}
        for g in self.grants:
            if (g["tenant_id"] == tenant_id
                    and g["from_project"] == owner_project
                    and g["to_project"] == reader_project):
                self.audit.append({"action": "allowed_share", "tenant_id": tenant_id,
                                   "from_project": owner_project,
                                   "to_project": reader_project})
                return {"allowed": True, "reason": "An approved share grant "
                        "covers this read.", "grant": g}
        self.audit.append({"action": "denied", "tenant_id": tenant_id,
                           "from_project": owner_project,
                           "to_project": reader_project})
        return {"allowed": False, "reason":
                "Blocked: notes from another project need an approved "
                "share grant first."}


# --- Agent identity and attestation (B10) ---------------------------------
def attest_action(agent_implementation: str, model_config: dict,
                  skill_versions: dict, permission_profile: list[str]) -> dict:
    """Per-action attestation record. Skill versions are caller-supplied
    from W4's M6 versioning — this module records them, it does not
    re-implement versioning."""
    fingerprint_src = "|".join([
        agent_implementation,
        str(sorted(model_config.items())),
        str(sorted(skill_versions.items())),
        ",".join(sorted(permission_profile))])
    return {"agent_implementation": agent_implementation,
            "model_config": dict(model_config),
            "skill_versions": dict(skill_versions),
            "permission_profile": sorted(permission_profile),
            "fingerprint": hashlib.sha256(fingerprint_src.encode()).hexdigest()[:16]}


def detect_identity_change(records: list[dict]) -> dict:
    """An unexpected execution-identity change mid-mission: the
    fingerprint changes after the first action."""
    if len(records) < 2:
        return {"changed": False, "reason": "Too few actions to compare."}
    first = records[0]["fingerprint"]
    for i, rec in enumerate(records[1:], start=1):
        if rec["fingerprint"] != first:
            return {"changed": True, "at_action": i,
                    "reason": f"Execution identity changed at action {i}: "
                    "agent, model, skills, or permissions differ from the "
                    "mission's first action."}
    return {"changed": False, "reason": "Execution identity stayed the same."}
