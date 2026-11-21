"""AgentQon — mission artifacts (W3 Verification & Recovery).

Owns the artifact rules end to end: predictable collision-free names,
stable IDs, content-based type detection, integrity hashes, versions
(not silent overwrites), diffs, duplicate detection, download
verification, content-type validation, streaming large files, backup /
recovery, final-output selection, and retention policies.

Design notes (standing orders):
- Stdlib only. No new runtime dependencies.
- A mission record never carries artifact CONTENT for large files — it
  carries a reference (id, hash, size, path). Small saves keep the same
  reference shape so callers never branch on size.
- The record is the source of truth for final selection: exactly one
  artifact per kind is marked final, drafts are labelled drafts, and the
  current final is computed from the mission's own event stream, so the
  UI and the record cannot disagree — both read the same events.
- Simple-words law: every refusal and finding is a plain sentence, never
  raw JSON, never a bare status code, never jargon.
- The Shield is untouched: this module never writes into the harness's
  own install tree; callers pass the artifacts directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Any, Optional

from .domain import new_id, now_iso

# ------------------------------------------------------------------ naming

MAX_NAME_LEN = 120
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_KNOWN_EXTS = {"json", "md", "txt", "html", "pdf", "png", "jpg", "jpeg",
               "zip", "csv", "toml", "yaml", "yml"}


def safe_filename(name: str) -> str:
    """A predictable, filesystem-safe name: path separators, control
    characters and shell-special characters become '-', leading dots
    (hidden files / parent hops) are stripped, and the result is never
    empty. This is the single naming rule for mission outputs."""
    text = unicodedata.normalize("NFKD", str(name)).strip().lower()
    text = _UNSAFE_RE.sub("-", text).strip(".-")
    text = re.sub(r"-{2,}", "-", text)
    return (text or "artifact")[:MAX_NAME_LEN]


def artifact_filename(kind: str, title: str, content_hash: str,
                      existing: set[str] | None = None,
                      extension: str | None = None) -> str:
    """Predictable and collision-free: ``<kind>-<slug>-<hash8>[.<ext>]``.
    The same content under the same title always yields the same name
    (duplicate recognition); a different content hash can never collide
    with it; and if a caller already holds the name for other content,
    a numeric suffix is added — never a silent overwrite."""
    base = f"{safe_filename(kind)}-{safe_filename(title)}-{content_hash[:8]}"
    ext = ""
    if extension:
        ext = "." + safe_filename(extension).lower().strip(".")
    elif "." in safe_filename(title):
        pass
    name = base + ext
    taken = set(existing or ())
    if name not in taken:
        return name
    n = 2
    while f"{base}-{n}{ext}" in taken:
        n += 1
    return f"{base}-{n}{ext}"


# ------------------------------------------------------------ type detection

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]

_DECLARED_ALLOWS = {
    "json": {"json"},
    "text": {"text", "json", "md"},
    "markdown": {"md", "text"},
    "png": {"png"},
    "jpeg": {"jpeg"},
    "pdf": {"pdf"},
    "zip": {"zip"},
    "csv": {"csv", "text"},
}


def detect_content_type(data: bytes) -> str:
    """Content type from CONTENT, not extension. Returns one of:
    json, text, md, csv, png, jpeg, pdf, zip, gzip, gif, binary."""
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    stripped = text.strip()
    if stripped:
        try:
            json.loads(stripped)
            return "json"
        except (ValueError, TypeError):
            pass
    if "\x00" in text:
        return "binary"
    if stripped.startswith("# ") or "\n## " in text:
        return "md"
    if "\n" in stripped and "," in stripped.split("\n", 1)[0]:
        first = stripped.split("\n", 1)[0]
        if first.count(",") >= 1 and all(
                "," in ln for ln in stripped.split("\n")[1:3] if ln.strip()):
            return "csv"
    return "text"


def validate_content_type(data: bytes, declared: str) -> tuple[bool, str]:
    """Refuse mismatched content; never guess. (True, message) or
    (False, plain-words refusal). Unknown declared types are refused
    rather than waved through."""
    detected = detect_content_type(data)
    allowed = _DECLARED_ALLOWS.get(declared.lower())
    if allowed is None:
        return False, (f"This file was declared as {declared!r}, which is "
                       "not a type this system accepts, so it was refused.")
    if detected not in allowed:
        return False, (f"This file was declared as {declared}, but its "
                       f"contents look like {detected}, so it was refused "
                       "instead of being saved under the wrong type.")
    return True, f"Contents match the declared type ({declared})."


# ------------------------------------------------------------- hashing / diff

def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file_streaming(path: Path, chunk: int = 1024 * 1024) -> str:
    """Hash a file a chunk at a time — large artifacts never load fully
    into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def copy_file_streaming(src: Path, dst: Path, chunk: int = 1024 * 1024) -> str:
    """Copy a file a chunk at a time; returns its hash."""
    h = hashlib.sha256()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            block = fin.read(chunk)
            if not block:
                break
            h.update(block)
            fout.write(block)
    return h.hexdigest()


def diff_text(old: bytes, new: bytes, name: str = "artifact") -> dict:
    """What changed between two versions: a plain-words summary plus a
    unified diff for text. Binary content gets a summary only."""
    try:
        old_lines = old.decode("utf-8").splitlines()
        new_lines = new.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        changed = old != new
        return {"summary": ("The file changed." if changed
                            else "The file did not change."),
                "lines_added": 0, "lines_removed": 0, "diff": "",
                "changed": changed}
    # difflib only terminates lines that already end with one, and
    # splitlines() strips them — so re-terminate every yielded line.
    diff = "".join(
        ln if ln.endswith("\n") else ln + "\n"
        for ln in unified_diff(old_lines, new_lines, fromfile=f"old/{name}",
                               tofile=f"new/{name}", lineterm="\n"))
    added = sum(1 for ln in diff.splitlines()
                if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines()
                  if ln.startswith("-") and not ln.startswith("---"))
    if not added and not removed:
        summary = "Nothing changed between these versions."
    else:
        summary = (f"{added} line(s) added and {removed} line(s) removed "
                   f"between these versions.")
    return {"summary": summary, "lines_added": added,
            "lines_removed": removed, "diff": diff,
            "changed": bool(added or removed)}


# --------------------------------------------------------------- retention

# How long each artifact class is kept, in days. None = kept until a
# person explicitly removes it. Stated here so the policy is visible,
# not buried in a job somewhere.
RETENTION_DAYS: dict[str, Optional[int]] = {
    "final": None,          # the deliverable: kept until explicitly removed
    "draft": 90,            # drafts: three months, then listed for removal
    "intermediate": 30,     # working material: one month
    "log": 30,              # run logs: one month
    "checkpoint": 90,       # recovery snapshots: three months
}


def retention_due(record: dict, now: datetime | None = None) -> bool:
    """True when a record's class retention period has passed."""
    days = RETENTION_DAYS.get(record.get("class", "intermediate"))
    if days is None:
        return False
    created = record.get("created_at", "")
    try:
        stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    now = now or datetime.now(timezone.utc)
    return (now - stamp).days >= days


def retention_plan(records: list[dict],
                   now: datetime | None = None) -> dict:
    """Which artifacts are due for removal under the stated policy.
    This only LISTS them — removal itself stays a separate, approved
    action, so a policy can never silently delete a deliverable."""
    due = [r for r in records if retention_due(r, now)]
    return {"due": [r["artifact_id"] for r in due],
            "due_count": len(due),
            "kept_count": len(records) - len(due),
            "policy": {k: (f"{v} days" if v is not None else "kept until "
                           "explicitly removed")
                       for k, v in RETENTION_DAYS.items()}}


# ------------------------------------------------------------ artifact store

class ArtifactStore:
    """File-backed artifact registry for one artifacts directory.

    Records never embed content: they hold id, kind, version, class
    (final/draft/intermediate/log/checkpoint), filename, hash, size and
    the on-disk path. Versions are per (mission_id, kind); saving new
    content bumps the version and keeps the old version readable by id.
    Saving identical content returns the existing record — a duplicate
    is recognized, never silently duplicated.
    """

    INDEX = "index.json"

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / self.INDEX
        self._records: list[dict] = []
        if self._index_path.exists():
            try:
                self._records = json.loads(self._index_path.read_text())
            except (ValueError, OSError):
                self._records = []

    # -- internals ------------------------------------------------
    def _save_index(self) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2))
        tmp.replace(self._index_path)

    def _next_version(self, mission_id: str | None, kind: str) -> int:
        versions = [r["version"] for r in self._records
                    if r["mission_id"] == mission_id and r["kind"] == kind]
        return (max(versions) + 1) if versions else 1

    def _store_bytes(self, data: bytes, filename: str) -> Path:
        path = self.root / filename
        path.write_bytes(data)
        return path

    def _register(self, *, mission_id, kind, cls, filename, content_hash,
                  size, declared_type, title) -> dict:
        record = {
            "artifact_id": new_id("art"),
            "mission_id": mission_id,
            "kind": kind,
            "class": cls,
            "title": title,
            "filename": filename,
            "content_hash": content_hash,
            "size_bytes": size,
            "declared_type": declared_type,
            "detected_type": None,
            "version": self._next_version(mission_id, kind),
            "created_at": now_iso(),
            "is_final": False,
        }
        self._records.append(record)
        self._save_index()
        return dict(record)

    # -- public API ------------------------------------------------
    def find_duplicate(self, content_hash: str,
                       mission_id: str | None = None) -> Optional[dict]:
        """The existing record for identical content, or None."""
        for rec in self._records:
            if rec["content_hash"] == content_hash and (
                    mission_id is None or rec["mission_id"] == mission_id):
                return dict(rec)
        return None

    def save_bytes(self, data: bytes, *, mission_id: str | None, kind: str,
                   title: str, declared_type: str,
                   cls: str = "intermediate") -> dict:
        """Save bytes after content-type validation. Identical content
        returns the existing record (duplicate recognized)."""
        ok, message = validate_content_type(data, declared_type)
        if not ok:
            raise ValueError(message)
        content_hash = hash_bytes(data)
        dup = self.find_duplicate(content_hash, mission_id)
        if dup is not None:
            dup["duplicate_of_existing"] = True
            return dup
        filename = artifact_filename(
            kind, title, content_hash,
            existing={r["filename"] for r in self._records},
            extension=_ext_for(declared_type, data))
        self._store_bytes(data, filename)
        record = self._register(mission_id=mission_id, kind=kind, cls=cls,
                                filename=filename, content_hash=content_hash,
                                size=len(data), declared_type=declared_type,
                                title=title)
        record["detected_type"] = detect_content_type(data)
        return record

    def save_file(self, src: Path, *, mission_id: str | None, kind: str,
                  title: str, declared_type: str,
                  cls: str = "intermediate") -> dict:
        """Stream a large file in: hash and copy chunk by chunk, so the
        file is never loaded whole into memory and the record never
        carries its content — only hash, size and path."""
        src = Path(src)
        # Header check for binary declared types only: a declared zip
        # must start with the zip signature, and so on. Text-like
        # types cannot be judged from a header, so they are not
        # refused here — full validation belongs to save_bytes.
        magic_ok, magic_message = _header_matches_declared(src, declared_type)
        if not magic_ok:
            raise ValueError(magic_message)
        content_hash = hash_file_streaming(src)
        dup = self.find_duplicate(content_hash, mission_id)
        if dup is not None:
            dup["duplicate_of_existing"] = True
            return dup
        filename = artifact_filename(
            kind, title, content_hash,
            existing={r["filename"] for r in self._records},
            extension=_ext_for(declared_type, None))
        copy_file_streaming(src, self.root / filename)
        record = self._register(mission_id=mission_id, kind=kind, cls=cls,
                                filename=filename, content_hash=content_hash,
                                size=src.stat().st_size,
                                declared_type=declared_type, title=title)
        return record

    def get(self, artifact_id: str) -> Optional[dict]:
        for rec in self._records:
            if rec["artifact_id"] == artifact_id:
                return dict(rec)
        return None

    def get_version(self, mission_id: str | None, kind: str,
                    version: int) -> Optional[dict]:
        for rec in self._records:
            if (rec["mission_id"] == mission_id and rec["kind"] == kind
                    and rec["version"] == version):
                return dict(rec)
        return None

    def list_for_mission(self, mission_id: str | None) -> list[dict]:
        return [dict(r) for r in self._records
                if r["mission_id"] == mission_id]

    def read_bytes(self, artifact_id: str) -> bytes:
        rec = self.get(artifact_id)
        if rec is None:
            raise KeyError(f"No artifact with id {artifact_id}")
        return (self.root / rec["filename"]).read_bytes()

    def verify_integrity(self, artifact_id: str) -> tuple[bool, str]:
        """The stored bytes still match the hash recorded at creation."""
        rec = self.get(artifact_id)
        if rec is None:
            return False, "No artifact with that id was found."
        path = self.root / rec["filename"]
        if not path.exists():
            return False, "The artifact file is missing from storage."
        actual = hash_file_streaming(path)
        if actual != rec["content_hash"]:
            return False, ("The artifact file no longer matches the hash "
                           "recorded when it was created, so it may have "
                           "been changed or damaged.")
        return True, "The artifact file matches its recorded hash."

    def verify_download(self, downloaded: bytes, artifact_id: str) -> tuple[bool, str]:
        """Downloaded bytes match the recorded hash."""
        rec = self.get(artifact_id)
        if rec is None:
            return False, "No artifact with that id was found."
        if hash_bytes(downloaded) != rec["content_hash"]:
            return False, ("The downloaded file does not match the "
                           "recorded hash, so the download may be "
                           "incomplete or damaged.")
        return True, "The downloaded file matches the recorded hash."

    def mark_final(self, artifact_id: str) -> dict:
        """Mark exactly one artifact per kind as the final deliverable.
        Marking a second final unmarks the first; drafts stay labelled
        drafts. Returns the newly final record."""
        rec = self.get(artifact_id)
        if rec is None:
            raise KeyError(f"No artifact with id {artifact_id}")
        for r in self._records:
            if (r["mission_id"] == rec["mission_id"]
                    and r["kind"] == rec["kind"]):
                r["is_final"] = (r["artifact_id"] == artifact_id)
                if r["is_final"]:
                    r["class"] = "final"
        self._save_index()
        return self.get(artifact_id)

    def current_final(self, mission_id: str | None,
                      kind: str) -> Optional[dict]:
        """The one artifact marked final for this mission + kind, or
        None when no final has been chosen yet."""
        for rec in self._records:
            if (rec["mission_id"] == mission_id and rec["kind"] == kind
                    and rec.get("is_final")):
                return dict(rec)
        return None

    def diff_versions(self, mission_id: str | None, kind: str,
                      old_version: int, new_version: int) -> dict:
        old = self.get_version(mission_id, kind, old_version)
        new = self.get_version(mission_id, kind, new_version)
        if old is None or new is None:
            raise KeyError("One of the requested versions was not found.")
        result = diff_text(self.read_bytes(old["artifact_id"]),
                           self.read_bytes(new["artifact_id"]),
                           name=old["filename"])
        result["from_version"] = old_version
        result["to_version"] = new_version
        return result

    def backup(self, artifact_id: str, backup_dir: Path | str) -> dict:
        """Copy an artifact to a backup folder and verify the copy
        against the recorded hash before reporting success."""
        rec = self.get(artifact_id)
        if rec is None:
            raise KeyError(f"No artifact with id {artifact_id}")
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = backup_dir / rec["filename"]
        copied_hash = copy_file_streaming(self.root / rec["filename"], dest)
        if copied_hash != rec["content_hash"]:
            return {"ok": False,
                    "message": "The backup copy does not match the "
                               "recorded hash, so the backup was not trusted."}
        return {"ok": True, "backup_path": str(dest),
                "message": "Backup copy verified against the recorded hash."}

    def restore(self, backup_path: Path | str, artifact_id: str) -> dict:
        """Restore an artifact from a backup copy, verifying the hash
        first. A mismatched backup is refused, never written over the
        stored file."""
        rec = self.get(artifact_id)
        if rec is None:
            raise KeyError(f"No artifact with id {artifact_id}")
        actual = hash_file_streaming(Path(backup_path))
        if actual != rec["content_hash"]:
            return {"ok": False,
                    "message": "The backup does not match the recorded "
                               "hash, so it was not restored."}
        copy_file_streaming(Path(backup_path), self.root / rec["filename"])
        return {"ok": True,
                "message": "The artifact was restored from backup."}


def _header_matches_declared(src: Path, declared_type: str) -> tuple[bool, str]:
    magic_types = {kind: magic for magic, kind in _MAGIC}
    want = declared_type.lower()
    if want not in magic_types:
        return True, ""
    with open(src, "rb") as fh:
        head = fh.read(16)
    if head.startswith(magic_types[want]):
        return True, ""
    return False, (f"This file was declared as {want}, but its first "
                   "bytes do not carry that type's signature, so it was "
                   "refused instead of being saved under the wrong type.")


def _ext_for(declared_type: str, data: bytes | None) -> str | None:
    mapping = {"json": "json", "text": "txt", "markdown": "md",
               "png": "png", "jpeg": "jpg", "pdf": "pdf", "zip": "zip",
               "csv": "csv"}
    if declared_type.lower() in mapping:
        return mapping[declared_type.lower()]
    if data is not None:
        detected = detect_content_type(data)
        return {"json": "json", "md": "md", "text": "txt",
                "png": "png", "jpeg": "jpg", "pdf": "pdf",
                "zip": "zip", "csv": "csv"}.get(detected)
    return None


# ------------------------------------------------------- record-level finals
# The mission event stream is the shared source of truth: the record
# and the UI both read it, so they cannot disagree about which artifact
# is final. These helpers build / read those events; callers persist
# them with the store's ordinary put_event.

def final_selection_event(tenant_id: str, mission_id: str, kind: str,
                          artifact_id: str) -> dict:
    from .domain import event_envelope
    return event_envelope(tenant_id, "mission", mission_id,
                          "ArtifactFinalSelected",
                          {"kind": kind, "artifact_id": artifact_id,
                           "note": "This artifact is the final deliverable "
                                   "for its kind; earlier versions are drafts."})


def final_artifacts_from_events(events: list[dict]) -> dict:
    """Current final per kind from an event list (last selection wins).
    Drafts are simply every version that is not the current final."""
    finals: dict[str, str] = {}
    for event in events:
        if event.get("event_type") != "ArtifactFinalSelected":
            continue
        payload = event.get("payload") or {}
        if payload.get("kind") and payload.get("artifact_id"):
            finals[payload["kind"]] = payload["artifact_id"]
    return finals
