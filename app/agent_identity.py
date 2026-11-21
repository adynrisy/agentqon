"""AgentQon — agent identity (expansion B).

Per-agent identity facts that ride wherever an agent is listed:
version + configuration diff, last activity, per-agent budget (via
the EXISTING store hooks — this module builds no second budget
system), memory scope, disable control, health, ownership.

Budget (B3): reserve_for_agent() calls Store.reserve_budget() and
settle_for_agent() calls Store.settle_reservation() — the same
race-safe reservation the mission engine uses (§114). The agent
record only remembers the reservation ids; it never tracks money
itself. If the store has no such hooks (a minimal test double),
reserve returns None and the caller treats the budget as
unavailable, never as unlimited.

Memory scope (B4): an agent sees a memory when the memory's scope
is in the agent's allowed scopes. Scope spellings: "user" (shared
with the user), "agent:<agent_id>" (one agent's own memories),
"session:<session_id>". An agent with no scopes listed sees nothing
— fail-closed, never fail-open.

Disable (B5): a disabled agent is not assignable and cannot run;
assignable() and the scheduler's assignment check both enforce it.

Health (B6): derived from the agent's recorded stats (the existing
agent_stats rows): recent failures lower health; no recent activity
says so plainly instead of inventing a status.

Ownership (B7): built-in | user-created | installed. listing_entry()
carries it everywhere the agent is listed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .domain import now_iso

OWNERSHIPS = ("built-in", "user-created", "installed")


@dataclass
class AgentRecord:
    agent_id: str
    version: str = "1"
    config: dict = field(default_factory=dict)
    last_activity: str = ""
    disabled: bool = False
    ownership: str = "built-in"
    memory_scopes: list = field(default_factory=list)
    reservation_ids: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.ownership not in OWNERSHIPS:
            raise ValueError(f"unknown ownership {self.ownership!r}")
        self.last_activity = self.last_activity or ""

    def touch(self, at: str = "") -> None:
        """Record activity (B2): the last-activity timestamp."""
        self.last_activity = at or now_iso()

    def assignable(self) -> tuple[bool, str]:
        """Disable control (B5): a disabled agent cannot be assigned
        or run. Returns (ok, plain-words reason)."""
        if self.disabled:
            return False, (f"agent {self.agent_id} is disabled, so it "
                           f"cannot be assigned or run")
        return True, ""

    def listing_entry(self, health: dict | None = None) -> dict:
        """The row shown wherever the agent is listed (B7): version,
        ownership, health, last activity, disabled state — together."""
        return {
            "agent_id": self.agent_id,
            "version": self.version,
            "ownership": self.ownership,
            "disabled": self.disabled,
            "last_activity": self.last_activity or "no activity recorded",
            "health": (health or {}).get("label", "no recent activity recorded"),
            "memory_scopes": list(self.memory_scopes),
        }


def config_diff(old_config: dict, new_config: dict) -> dict:
    """Configuration diff between two agent versions (B1): what
    changed, field by field, viewable as plain words."""
    changes = []
    for key in sorted(set(old_config) | set(new_config)):
        if key not in old_config:
            changes.append({"field": key, "change": "added",
                            "old": None, "new": new_config[key],
                            "summary": f"{key} added"})
        elif key not in new_config:
            changes.append({"field": key, "change": "removed",
                            "old": old_config[key], "new": None,
                            "summary": f"{key} removed"})
        elif old_config[key] != new_config[key]:
            changes.append({"field": key, "change": "changed",
                            "old": old_config[key], "new": new_config[key],
                            "summary": f"{key} changed"})
    return {"changes": changes,
            "summary": (f"{len(changes)} setting(s) changed"
                       if changes else "no settings changed")}


def reserve_for_agent(store: Any, tenant_id: str, mission_id: str,
                      agent: AgentRecord, amount: float,
                      cap: float) -> str | None:
    """Per-agent budget reservation through the EXISTING engine hook
    (B3). Returns the reservation id, or None when the reservation
    could not be made (cap reached) or the store has no hook."""
    reserve = getattr(store, "reserve_budget", None)
    if reserve is None:
        return None
    rid = reserve(tenant_id, mission_id, amount, cap)
    if rid:
        agent.reservation_ids.append(rid)
    return rid


def settle_for_agent(store: Any, agent: AgentRecord, reservation_id: str,
                     outcome: str) -> None:
    """Settle an agent reservation through the existing hook (B3).
    outcome: 'reconciled' (work finished) or 'released' (never ran)."""
    settle = getattr(store, "settle_reservation", None)
    if settle is None:
        return
    settle(reservation_id, outcome)
    if reservation_id in agent.reservation_ids:
        agent.reservation_ids.remove(reservation_id)


def memory_scope_filter(memories: list[dict],
                        agent: AgentRecord) -> list[dict]:
    """Per-agent memory scope (B4): only memories whose scope the
    agent is allowed to see. No scopes listed = sees nothing."""
    allowed = set(agent.memory_scopes)
    if not allowed:
        return []
    out = []
    for mem in memories:
        scope = mem.get("scope", "")
        scope_ref = mem.get("scope_ref", "")
        full = f"{scope}:{scope_ref}" if scope_ref else scope
        if scope in allowed or full in allowed:
            out.append(mem)
    return out


def health_from_stats(stats: list[dict]) -> dict:
    """Agent health indicator (B6) from recorded agent stats, in
    plain words. A stat row carries task_type + whatever outcome
    fields the recorder stored; rows with an explicit 'failed' or
    'success' outcome are counted, others are ignored (never
    guessed)."""
    failures = sum(1 for s in stats if s.get("outcome") == "failed"
                   or s.get("status") == "failed")
    successes = sum(1 for s in stats if s.get("outcome") == "success"
                    or s.get("status") == "success")
    total = failures + successes
    if total == 0:
        return {"label": "no recent activity recorded",
                "detail": "no finished work on record to judge health from",
                "failures": 0, "successes": 0}
    if failures == 0:
        label = "healthy — recent work finished without failures"
    elif failures <= total // 3:
        label = "mostly healthy — a few recent failures"
    else:
        label = "unsteady — recent work often fails"
    return {"label": label,
            "detail": f"{successes} finished well, {failures} failed, "
                      f"out of {total} recent runs on record",
            "failures": failures, "successes": successes}
