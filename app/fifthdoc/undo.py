"""§1 Smart undo — change timeline, selective rollback, snapshots.

A Timeline records ordered entries for files created, settings changed,
commands executed and patches applied. A snapshot is a copy of file
contents held in memory (bounded by a configurable byte limit; oldest
snapshots are dropped first and the drop is recorded). Selective rollback
restores one entry without touching later unrelated entries. External
actions (emails, remote changes) are marked irreversible and are NEVER
promised reversible — the manifest and every preview say so in plain words.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

EXTERNAL_KINDS = {"email.send", "remote.change", "payment.send", "publish"}
REVERSIBLE_KINDS = {"file.create", "file.edit", "file.delete", "setting.change",
                    "patch.apply", "command.run"}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class TimelineEntry:
    entry_id: str
    kind: str
    target: str
    before: str | None  # content before, or None if the file did not exist
    after: str | None   # content after, or None if deleted
    created_at: float = field(default_factory=time.time)
    reversible: bool = True
    note: str = ""


@dataclass
class Checkpoint:
    name: str
    entry_ids: list[str]
    created_at: float = field(default_factory=time.time)


class Timeline:
    """Ordered, bounded change timeline with selective rollback."""

    def __init__(self, storage_limit_bytes: int = 1_000_000):
        self.storage_limit_bytes = storage_limit_bytes
        self.entries: list[TimelineEntry] = []
        self.checkpoints: dict[str, Checkpoint] = {}
        self._stored_bytes = 0
        self.dropped_snapshots: list[str] = []

    def _size(self, e: TimelineEntry) -> int:
        return sum(len(x or "") for x in (e.before, e.after))

    def record(self, entry: TimelineEntry) -> TimelineEntry:
        if entry.kind in EXTERNAL_KINDS:
            entry.reversible = False
            entry.note = ("This action reached outside this machine and cannot "
                          "be undone from here.")
        self.entries.append(entry)
        self._stored_bytes += self._size(entry)
        while self._stored_bytes > self.storage_limit_bytes and self.entries:
            old = self.entries.pop(0)
            self._stored_bytes -= self._size(old)
            self.dropped_snapshots.append(old.entry_id)
        return entry

    def snapshot(self, entry_id: str, content: str) -> dict:
        """Pre-action snapshot metadata (content hash, not a second copy)."""
        return {"entry_id": entry_id, "digest": _digest(content),
                "bytes": len(content)}

    def preview_rollback(self, entry_id: str, current_files: dict) -> dict:
        """What a rollback would do, without doing it."""
        entry = next((e for e in self.entries if e.entry_id == entry_id), None)
        if entry is None:
            return {"ok": False, "reason": "No such change in the timeline."}
        if not entry.reversible:
            return {"ok": False, "reason": "This action cannot be undone from here.",
                    "irreversible": True}
        current = current_files.get(entry.target)
        conflict = (entry.after is not None and current is not None
                    and current != entry.after)
        return {"ok": True, "entry_id": entry_id, "target": entry.target,
                "restore_to": ("deleted" if entry.before is None else "previous content"),
                "newer_user_change_detected": conflict,
                "warning": ("Someone changed this file after the recorded change. "
                            "Restoring would overwrite that newer change."
                            if conflict else "")}

    def rollback(self, entry_id: str, current_files: dict,
                 force: bool = False) -> dict:
        """Selective rollback of ONE entry; later unrelated entries untouched."""
        preview = self.preview_rollback(entry_id, current_files)
        if not preview.get("ok"):
            return preview
        if preview["newer_user_change_detected"] and not force:
            return {"ok": False, "reason": "Refused: a newer change would be lost. "
                    "Review the preview and confirm to overwrite it.",
                    "newer_user_change_detected": True}
        entry = next(e for e in self.entries if e.entry_id == entry_id)
        if entry.before is None:
            current_files.pop(entry.target, None)
        else:
            current_files[entry.target] = entry.before
        return {"ok": True, "entry_id": entry_id, "target": entry.target,
                "restored": True}

    def restore_deleted(self, target: str) -> dict:
        """Restore a deleted artifact from version history, if recorded."""
        for entry in reversed(self.entries):
            if entry.target == target and entry.kind == "file.delete" and entry.before is not None:
                return {"ok": True, "target": target, "content": entry.before,
                        "from_entry": entry.entry_id}
        return {"ok": False, "reason": "No recorded version of this file was found."}

    def restore_selected(self, entry_ids: list[str], current_files: dict) -> dict:
        applied, refused = [], []
        for eid in entry_ids:
            result = self.rollback(eid, current_files)
            (applied if result.get("ok") else refused).append(eid)
        return {"applied": applied, "refused": refused}

    def compare_to_start(self, start_files: dict, current_files: dict) -> dict:
        changed, added, removed = [], [], []
        for path, cur in current_files.items():
            if path not in start_files:
                added.append(path)
            elif start_files[path] != cur:
                changed.append(path)
        for path in start_files:
            if path not in current_files:
                removed.append(path)
        return {"changed": sorted(changed), "added": sorted(added),
                "removed": sorted(removed)}

    def checkpoint(self, name: str) -> Checkpoint:
        cp = Checkpoint(name=name, entry_ids=[e.entry_id for e in self.entries])
        self.checkpoints[name] = cp
        return cp

    def recovery_manifest(self) -> dict:
        restorable = [e.entry_id for e in self.entries if e.reversible]
        unrestorable = [e.entry_id for e in self.entries if not e.reversible]
        return {"restorable_entries": restorable,
                "cannot_be_restored": unrestorable,
                "cannot_be_restored_note": ("Actions that reached outside this "
                    "machine (emails, remote changes, payments) cannot be undone "
                    "from here."),
                "dropped_snapshots": list(self.dropped_snapshots),
                "storage_limit_bytes": self.storage_limit_bytes}
