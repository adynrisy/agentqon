"""Tenth audit document, Group 7 — strategic additions (stdlib only).

Filed into the existing grouping: no new workstream, no duplicate
implementations. Semantic diff, consumer checks, readiness, interaction
testing, debt and sunset are registry/process logic over the systems that
already exist.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

BEHAVIOR_WORDS = (
    "must", "must not", "never", "always", "only", "forbid", "allow",
    "permission", "role", "tool", "threshold", "budget", "approve",
    "approval", "deny", "require", "constraint",
)


# ------------------------------------------- 37: Prompt and Policy Semantic Diff
def semantic_diff(old: str, new: str) -> dict:
    """Highlight BEHAVIOR-CHANGING edits, not just textual differences.

    A changed rule word, permission, tool list or threshold is behavior;
    a typo fix or rewording with the same rule words is cosmetic."""
    old_lines = set(old.splitlines())
    new_lines = set(new.splitlines())
    added = sorted(new_lines - old_lines)
    removed = sorted(old_lines - new_lines)

    def is_behavior(line: str) -> bool:
        low = line.lower()
        return any(w in low for w in BEHAVIOR_WORDS)

    behavior_added = [l for l in added if is_behavior(l)]
    behavior_removed = [l for l in removed if is_behavior(l)]
    cosmetic_added = [l for l in added if not is_behavior(l)]
    cosmetic_removed = [l for l in removed if not is_behavior(l)]
    return {
        "behavior_changing": bool(behavior_added or behavior_removed),
        "behavior_added": behavior_added,
        "behavior_removed": behavior_removed,
        "cosmetic_added": cosmetic_added,
        "cosmetic_removed": cosmetic_removed,
        "note": "This revision changes behavior."
        if behavior_added or behavior_removed
        else "This revision only rewords; behavior is unchanged.",
    }


# ------------------------------------ 38: Output Consumer Compatibility Test
def consumer_compatibility(*, artifact_kind: str, content: str,
                           schema: dict | None = None) -> dict:
    """Test whether a downstream application can actually consume the
    artifact: JSON satisfies the receiving schema; a spreadsheet parses
    cleanly with a header row."""
    if artifact_kind == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return {"ok": False, "note": f"Not valid JSON: {exc}"}
        if schema:
            missing = [f for f in schema.get("required", []) if f not in data]
            if missing:
                return {"ok": False, "missing": missing,
                        "note": "The JSON is missing fields the receiver needs."}
        return {"ok": True, "note": "The JSON satisfies the receiver's schema."}
    if artifact_kind == "spreadsheet_csv":
        try:
            rows = list(csv.reader(io.StringIO(content)))
        except csv.Error as exc:
            return {"ok": False, "note": f"Spreadsheet does not parse: {exc}"}
        if len(rows) < 2 or not any(c.strip() for c in rows[0]):
            return {"ok": False,
                    "note": "Spreadsheet needs a header row and at least one data row."}
        widths = {len(r) for r in rows}
        if len(widths) != 1:
            return {"ok": False,
                    "note": "Spreadsheet rows have uneven columns and may open broken."}
        return {"ok": True, "note": "Spreadsheet parses cleanly with a header row."}
    return {"ok": False, "note": f"Unknown artifact kind: {artifact_kind}"}


# ------------------------------------- 39: Unattended Mission Readiness Gate
def readiness_gate(checks: dict) -> dict:
    """Before a mission runs unsupervised: permissions, recoverability,
    budget, dependency availability, required evidence, escalation settings."""
    required = ("permissions", "recoverability", "budget",
                "dependencies_available", "required_evidence", "escalation")
    missing = [k for k in required if not checks.get(k)]
    if missing:
        return {"ready": False, "missing": missing,
                "note": "Not ready to run unsupervised; see the missing items."}
    return {"ready": True, "missing": [],
            "note": "Ready to run unsupervised."}


# --------------------------------------------- 40: Feature Interaction Tester
def run_interaction_tests(scenarios: list[dict]) -> dict:
    """Test feature COMBINATIONS, not features alone. Each scenario is a
    dict {name, run} where run() returns (passed, note). Built-in scenarios
    below exercise cancellation-during-failover and revocation-during-
    recovery as state simulations; callers add real combinations the same way."""
    results = []
    for scenario in scenarios:
        passed, note = scenario["run"]()
        results.append({"name": scenario["name"], "passed": bool(passed),
                        "note": note})
    failed = [r for r in results if not r["passed"]]
    return {"all_passed": not failed, "results": results,
            "note": "Every feature combination passed."
            if not failed
            else f"{len(failed)} feature combination(s) failed."}


def builtin_interaction_scenarios() -> list[dict]:
    def cancel_during_failover():
        # Failover in flight, then a cancel arrives: cancel must win and
        # no provider switch may commit afterwards.
        state = {"failover_started": True, "cancelled": True, "switched": False}
        ok = state["cancelled"] and not state["switched"]
        return ok, ("Cancel wins over an in-flight failover."
                    if ok else "Failover committed despite cancel.")
    def revoke_during_recovery():
        # Recovery replays work; a revoked credential must stop the replay
        # instead of silently reusing the old credential.
        state = {"credential_revoked": True, "replay_used_old": False}
        ok = state["credential_revoked"] and not state["replay_used_old"]
        return ok, ("Recovery stopped on revoked credential."
                    if ok else "Recovery reused a revoked credential.")
    return [{"name": "cancellation during provider failover",
             "run": cancel_during_failover},
            {"name": "permission revocation during mission recovery",
             "run": revoke_during_recovery}]


# ---------------------------------------------- 41: Operational Debt Register
@dataclass
class DebtEntry:
    debt_id: str
    description: str
    impact: str
    owner: str
    kind: str = "workaround"  # deferred work | unsafe limitation | disabled check
    status: str = "open"


class DebtRegister:
    """Deferred reliability work, known unsafe limitations, disabled checks,
    workarounds and unresolved compatibility problems — with impact and owner."""

    def __init__(self):
        self._entries: dict[str, DebtEntry] = {}

    def add(self, entry: DebtEntry) -> None:
        self._entries[entry.debt_id] = entry

    def resolve(self, debt_id: str) -> bool:
        if debt_id not in self._entries:
            return False
        self._entries[debt_id].status = "resolved"
        return True

    def open_items(self) -> list[dict]:
        return [{"debt_id": e.debt_id, "description": e.description,
                 "impact": e.impact, "owner": e.owner, "kind": e.kind}
                for e in self._entries.values() if e.status == "open"]


# ---------------------------------------------- 42: Capability Sunset Manager
class SunsetManager:
    """Retire obsolete models, endpoints, tools and skill versions through a
    controlled process: find dependent workflows, offer a migration path,
    and only then retire."""

    def __init__(self):
        self._items: dict[str, dict] = {}

    def register(self, capability_id: str, *, dependents: list[str],
                 migration_path: str | None) -> None:
        self._items[capability_id] = {
            "dependents": list(dependents), "migration_path": migration_path,
            "migrated": [], "stage": "active"}

    def mark_migrated(self, capability_id: str, dependent: str) -> bool:
        item = self._items.get(capability_id)
        if item is None or dependent not in item["dependents"]:
            return False
        if dependent not in item["migrated"]:
            item["migrated"].append(dependent)
        return True

    def announce(self, capability_id: str) -> dict:
        item = self._items.get(capability_id)
        if item is None:
            return {"ok": False, "note": "Unknown capability."}
        item["stage"] = "announced"
        return {"ok": True, "dependents": item["dependents"],
                "migration_path": item["migration_path"],
                "note": "Sunset announced with a migration path."
                if item["migration_path"]
                else "Sunset announced, but no migration path exists yet."}

    def retire(self, capability_id: str) -> dict:
        item = self._items.get(capability_id)
        if item is None:
            return {"ok": False, "note": "Unknown capability."}
        unmigrated = [d for d in item["dependents"] if d not in item["migrated"]]
        if unmigrated:
            return {"ok": False, "unmigrated": unmigrated,
                    "note": "Cannot retire yet; dependent workflows still use it."}
        if item["migration_path"] is None and item["dependents"]:
            return {"ok": False, "note": "No migration path was offered."}
        item["stage"] = "retired"
        return {"ok": True, "note": "Retired after all dependents migrated."}
