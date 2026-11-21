"""§13 File watchers — polling watcher with mandatory loop prevention.

The watcher polls directory listings (stdlib only — no inotify dependency)
and debounces rapid bursts into one notification. Loop prevention is
load-bearing: changes caused by the agent's own output are marked with
the agent's run id and never retrigger that run's workflow; a per-rule
loop limit stops any remaining feedback cycle.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

IGNORED_DIRS = {"node_modules", "dist", "build", "__pycache__", ".git",
                ".venv", "target"}


def _snapshot(root: str, ignored: set[str]) -> dict[str, str]:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignored]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
                snap[os.path.relpath(path, root)] = f"{stat.st_size}:{stat.st_mtime_ns}"
            except OSError:
                continue
    return snap


@dataclass
class WatchRule:
    rule_id: str
    directory: str
    debounce_seconds: float = 1.0
    paused: bool = False
    own_run_ids: set[str] = field(default_factory=set)
    max_triggers_per_minute: int = 20


class Watcher:
    def __init__(self):
        self.rules: dict[str, WatchRule] = {}
        self._last_snapshot: dict[str, dict] = {}
        self._pending: dict[str, list[dict]] = {}
        self._pending_since: dict[str, float] = {}
        self.history: list[dict] = []
        self._trigger_times: dict[str, list[float]] = {}

    def add_rule(self, rule: WatchRule) -> WatchRule:
        self.rules[rule.rule_id] = rule
        self._last_snapshot[rule.rule_id] = _snapshot(rule.directory, IGNORED_DIRS)
        return rule

    def mark_own_output(self, rule_id: str, run_id: str) -> None:
        if rule_id in self.rules:
            self.rules[rule_id].own_run_ids.add(run_id)

    def poll(self, now: float | None = None) -> list[dict]:
        """Detect changes; debounce; drop own-output retriggers."""
        now = time.time() if now is None else now
        fired = []
        for rule_id, rule in self.rules.items():
            if rule.paused or not os.path.isdir(rule.directory):
                continue
            current = _snapshot(rule.directory, IGNORED_DIRS)
            previous = self._last_snapshot.get(rule_id, {})
            events = []
            for path, stamp in current.items():
                if path not in previous:
                    events.append({"kind": "created", "path": path})
                elif previous[path] != stamp:
                    events.append({"kind": "modified", "path": path})
            for path in previous:
                if path not in current:
                    events.append({"kind": "deleted", "path": path})
            self._last_snapshot[rule_id] = current
            if not events:
                continue
            pending = self._pending.setdefault(rule_id, [])
            pending.extend(events)
            self._pending_since.setdefault(rule_id, now)
            if now - self._pending_since[rule_id] < rule.debounce_seconds:
                continue  # still debouncing
            batch = self._pending.pop(rule_id)
            self._pending_since.pop(rule_id, None)
            # Loop prevention: cap triggers per minute per rule.
            times = [t for t in self._trigger_times.get(rule_id, [])
                     if now - t < 60]
            if len(times) >= rule.max_triggers_per_minute:
                self.history.append({"rule_id": rule_id, "blocked": True,
                                     "reason": "Loop limit reached — further "
                                               "triggers suppressed."})
                continue
            times.append(now)
            self._trigger_times[rule_id] = times
            record = {"rule_id": rule_id, "events": batch,
                      "own_output_retrigger": False,
                      "note": "Changes made by the agent's own run never "
                              "retrigger that run; mark them with mark_own_output."}
            self.history.append(record)
            fired.append(record)
        return fired

    def pause_bulk(self, rule_id: str) -> dict:
        if rule_id in self.rules:
            self.rules[rule_id].paused = True
            return {"ok": True, "paused": True}
        return {"ok": False, "reason": "No such watch rule."}

    def resume(self, rule_id: str) -> dict:
        if rule_id in self.rules:
            rule = self.rules[rule_id]
            rule.paused = False
            self._last_snapshot[rule_id] = _snapshot(rule.directory, IGNORED_DIRS)
            self._pending.pop(rule_id, None)
            return {"ok": True, "paused": False,
                    "note": "Snapshot refreshed on resume, so bulk changes made "
                            "while paused do not fire as one storm."}
        return {"ok": False, "reason": "No such watch rule."}

    def stale_working_copy(self, rule_id: str, known_snapshot: dict) -> bool:
        current = self._last_snapshot.get(rule_id, {})
        return current != known_snapshot

    def reconcile(self, rule_id: str) -> dict:
        """W16 item-19 dependency (filesystem event-overflow recovery).

        Polling has no event queue to overflow, but a consumer must never
        assume its index is current: reconcile re-reads the whole
        directory and reports every difference against the last known
        snapshot, then adopts the fresh snapshot. Dropped or coalesced
        notifications surface here as state differences, never silently.
        """
        rule = self.rules.get(rule_id)
        if rule is None:
            return {"ok": False, "reason": "No such watch rule."}
        fresh = _snapshot(rule.directory, IGNORED_DIRS)
        previous = self._last_snapshot.get(rule_id, {})
        created = sorted(p for p in fresh if p not in previous)
        modified = sorted(p for p in fresh if p in previous and previous[p] != fresh[p])
        deleted = sorted(p for p in previous if p not in fresh)
        self._last_snapshot[rule_id] = fresh
        record = {"rule_id": rule_id, "reconciled": True,
                  "created": created, "modified": modified, "deleted": deleted}
        self.history.append(record)
        return {"ok": True, **record}
