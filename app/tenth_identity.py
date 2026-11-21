"""Tenth audit document, Group 2 — identity and authorization (stdlib only).

Generalizes the approval records and per-file write grants in agent_loop
into a chain with context binding. Item 8's rule: a stored grant never
silently authorizes a CHANGED action — the binding (action, resource,
parameters, version) must match exactly, or fresh approval is required.
The integration seam is agent_loop grant reuse consulting
ContextBinding.check_grant_reuse before reusing a stored grant.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field

MORE_POWERFUL_METHODS = ("admin", "root", "sudo", "elevated", "unrestricted")


def _freeze(params: dict) -> str:
    return json.dumps(params, sort_keys=True, default=str)


# ------------------------------------------------- 6: Just-in-Time Credential Broker
@dataclass
class _Token:
    token_id: str
    scope: str
    operation: str
    resource: str
    expires_at: float
    single_use: bool
    used: bool = False
    revoked: bool = False


class JITCredentialBroker:
    """Issue short-lived, narrowly scoped credentials per tool operation.

    Long-lived secrets are never handed out: every token names one
    operation, one resource, and an expiry measured in seconds.
    """

    def __init__(self, *, now_fn=None):
        self._now = now_fn or time.time
        self._tokens: dict[str, _Token] = {}

    def issue(self, *, scope: str, operation: str, resource: str,
              ttl_seconds: int = 60, single_use: bool = True) -> dict:
        if ttl_seconds <= 0:
            raise ValueError("Credential lifetime must be positive.")
        raw = secrets.token_urlsafe(24)
        token_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        self._tokens[token_id] = _Token(
            token_id=token_id, scope=scope, operation=operation,
            resource=resource, expires_at=self._now() + ttl_seconds,
            single_use=single_use)
        return {"token": raw, "token_id": token_id,
                "expires_in_seconds": ttl_seconds, "scope": scope,
                "operation": operation, "resource": resource}

    def validate(self, raw_token: str, *, operation: str, resource: str) -> dict:
        token_id = hashlib.sha256(raw_token.encode()).hexdigest()[:16]
        tok = self._tokens.get(token_id)
        if tok is None:
            return {"ok": False, "note": "Unknown credential."}
        if tok.revoked:
            return {"ok": False, "note": "This credential was revoked."}
        if self._now() > tok.expires_at:
            return {"ok": False, "note": "This credential has expired."}
        if tok.single_use and tok.used:
            return {"ok": False, "note": "This credential was already used once."}
        if tok.operation != operation or tok.resource != resource:
            return {"ok": False,
                    "note": "This credential does not cover that action or resource."}
        if tok.single_use:
            tok.used = True
        return {"ok": True, "token_id": token_id, "scope": tok.scope,
                "note": "Credential is valid for this one action."}

    def revoke(self, token_id: str) -> bool:
        tok = self._tokens.get(token_id)
        if tok is None:
            return False
        tok.revoked = True
        return True


# --------------------------------------------- 7: Delegated Authorization Chain
@dataclass
class DelegationLink:
    authorizer: str
    delegatee: str
    permissions: frozenset
    scope_note: str = ""


class DelegationChain:
    """authorizer -> delegated permissions -> using agent, verified per action."""

    def __init__(self):
        self._links: list[DelegationLink] = []

    def delegate(self, authorizer: str, delegatee: str, permissions) -> None:
        self._links.append(DelegationLink(authorizer, delegatee, frozenset(permissions)))

    def verify(self, *, actor: str, action: str) -> dict:
        """Every link in the chain must permit the action; a delegatee can
        never hold more than the authorizer granted."""
        chain = []
        current = actor
        seen = set()
        while True:
            link = next((l for l in self._links if l.delegatee == current), None)
            if link is None:
                break
            if link.delegatee in seen:
                return {"ok": False, "chain": chain,
                        "note": "The delegation chain loops, so it cannot be trusted."}
            seen.add(link.delegatee)
            chain.append({"authorizer": link.authorizer, "delegatee": link.delegatee,
                          "permissions": sorted(link.permissions)})
            if action not in link.permissions:
                return {"ok": False, "chain": chain,
                        "note": f"{link.authorizer} never granted {action}."}
            current = link.authorizer
        if not chain:
            return {"ok": False, "chain": chain,
                    "note": "No delegation chain covers this agent."}
        return {"ok": True, "chain": chain,
                "note": "Every link in the chain permits this action."}


# -------------------------------------------- 8: Authorization Context Binding
@dataclass(frozen=True)
class ApprovalBinding:
    action: str
    resource: str
    parameters: str  # frozen JSON (see _freeze)
    version: str

    @classmethod
    def build(cls, action: str, resource: str, parameters: dict, version: str):
        return cls(action, resource, _freeze(parameters), version)


def check_binding(binding: ApprovalBinding, *, action: str, resource: str,
                  parameters: dict, version: str) -> dict:
    """Exact match required. Any change -> fresh approval, never silent reuse."""
    changed = []
    if binding.action != action:
        changed.append("action")
    if binding.resource != resource:
        changed.append("resource")
    if binding.parameters != _freeze(parameters):
        changed.append("parameters")
    if binding.version != version:
        changed.append("version")
    if changed:
        return {"ok": False, "changed": changed,
                "note": "The action changed since approval, so fresh approval is required."}
    return {"ok": True, "changed": [],
            "note": "The action matches the approval exactly."}


def check_grant_reuse(stored_binding: ApprovalBinding, **request) -> dict:
    """Seam for agent_loop grant reuse: a stored per-file grant is only a
    shortcut when the binding still matches; otherwise it authorizes nothing."""
    return check_binding(stored_binding, **request)


# ------------------------------------------ 9: Privilege Escalation Tripwire
class EscalationTripwire:
    """Detect attempts to reach broader permissions, a new resource class,
    or a more powerful way of running something."""

    def __init__(self):
        self._baseline: dict[str, dict] = {}  # actor -> {resources, classes, methods}
        self._trips: list[dict] = []

    def set_baseline(self, actor: str, *, resources, resource_classes, methods) -> None:
        self._baseline[actor] = {
            "resources": set(resources), "classes": set(resource_classes),
            "methods": set(methods)}

    def check(self, actor: str, *, resource: str, resource_class: str,
              method: str) -> dict:
        base = self._baseline.get(actor)
        if base is None:
            return {"trip": False, "note": "No baseline recorded for this agent yet."}
        reasons = []
        if resource not in base["resources"]:
            reasons.append(f"new resource: {resource}")
        if resource_class not in base["classes"]:
            reasons.append(f"new resource class: {resource_class}")
        if method in MORE_POWERFUL_METHODS and method not in base["methods"]:
            # A more powerful method subsumes "new method": one reason, not two.
            reasons.append(f"more powerful execution method: {method}")
        elif method not in base["methods"]:
            reasons.append(f"new execution method: {method}")
        if reasons:
            trip = {"actor": actor, "reasons": reasons}
            self._trips.append(trip)
            return {"trip": True, "reasons": reasons,
                    "note": "This request reaches beyond the agent's usual permissions."}
        return {"trip": False, "reasons": [],
                "note": "This request stays within the agent's usual permissions."}

    @property
    def trips(self) -> list[dict]:
        return list(self._trips)


# -------------------------------------- 10: Credential Revocation Propagation
class RevocationRegistry:
    """Track which sessions/workers still use a credential; on revocation,
    list them all so each can be ended or renewed under policy."""

    def __init__(self):
        self._holders: dict[str, set[str]] = {}  # credential_id -> holder ids
        self._revoked: set[str] = set()

    def register_use(self, credential_id: str, holder_id: str) -> None:
        self._holders.setdefault(credential_id, set()).add(holder_id)

    def release(self, credential_id: str, holder_id: str) -> None:
        holders = self._holders.get(credential_id)
        if holders:
            holders.discard(holder_id)

    def revoke(self, credential_id: str, *, policy: str = "terminate") -> dict:
        """policy: 'terminate' ends holders now; 'renew' marks them for renewal."""
        affected = sorted(self._holders.get(credential_id, set()))
        self._revoked.add(credential_id)
        self._holders[credential_id] = set()
        return {"credential_id": credential_id, "policy": policy,
                "affected_holders": affected,
                "note": f"{len(affected)} session(s) affected; policy is {policy}."}

    def is_revoked(self, credential_id: str) -> bool:
        return credential_id in self._revoked


# --------------------------------------------- 11: Separation-of-Duties Rules
class SeparationOfDuties:
    """Sensitive operations need a reviewer who is not the proposer.

    In a single-user deployment the owner approves everything; the rule
    says so plainly and passes through instead of faking a second person.
    It enforces only when more than one distinct identity is configured.
    """

    def __init__(self, *, identities: list[str]):
        self._identities = list(identities)

    def check(self, *, operation: str, proposer: str, reviewer: str | None,
              sensitive: bool) -> dict:
        if not sensitive:
            return {"ok": True, "note": "This operation is not sensitive."}
        if len(set(self._identities)) < 2:
            return {"ok": True, "single_user": True,
                    "note": "Single-user setup: the owner approves everything, "
                            "so no separate reviewer exists here."}
        if reviewer is None:
            return {"ok": False,
                    "note": "A sensitive operation needs a reviewer before it can run."}
        if reviewer == proposer:
            return {"ok": False,
                    "note": "The reviewer must be someone other than the proposer."}
        return {"ok": True, "note": "Proposer and reviewer are different people."}
