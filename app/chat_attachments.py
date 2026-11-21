"""Chat composer file attachments (v22.6): the + button in the composer.

A chat message may carry files. The client sends them as base64 inside
the normal /v1/chat JSON body (no separate upload endpoint, no
multipart); the server validates, stores the bytes under the tenant
data dir, and folds readable text into the message the model actually
receives. Nothing here logs file contents — at most names and sizes
reach a log line, and this module logs nothing at all.

Limits (enforced server-side; the client mirrors them so a rejection
is seen before sending, but the server is the authority):
  MAX_FILES = 5 files per message
  MAX_FILE_BYTES = 5 MB per file
  MAX_TOTAL_BYTES = 10 MB per message
  MAX_TEXT_CHARS_PER_FILE = 20,000 chars folded into model context
  MAX_TEXT_TOTAL_CHARS = 50,000 chars folded in total

Allowed types (extension allowlist — the client MIME hint is never
trusted on its own): plain-text formats, common images, and PDF.
Only successfully UTF-8-decoded text is folded into model context.
Images and PDFs are stored, recorded, and NAMED in the message the
model receives — the model path here is text-only (OpenAI-compatible
/chat/completions with string content), so image bytes are never
pretended to be seen. The UI says exactly that.

Safety: filenames are sanitized to a basename with a safe alphabet
(no directories, no leading dots, no traversal); the final path is
resolved and checked to stay inside the tenant attachments dir; all
writes stay under the tenant data dir (AGENTQON_DATA / OMNIMIND_DATA
via resolve_portable_data_dir, else ./data). Stdlib only.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from pathlib import Path

MAX_FILES = 5
MAX_FILE_BYTES = 5_000_000
MAX_TOTAL_BYTES = 10_000_000
MAX_TEXT_CHARS_PER_FILE = 20_000
MAX_TEXT_TOTAL_CHARS = 50_000

TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".json", ".csv", ".log",
    ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yml", ".yaml",
})
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
ALLOWED_EXTS = TEXT_EXTS | IMAGE_EXTS | {".pdf"}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_TENANT = re.compile(r"[^A-Za-z0-9_-]+")


class AttachmentError(ValueError):
    """Plain-words rejection for an attachment batch (HTTP 400)."""


def safe_filename(name: str) -> str:
    """Basename + safe alphabet. Never returns a path, a dot-file, or empty."""
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = _SAFE_NAME.sub("_", base).strip("._")
    if not base:
        base = "file"
    return base[:100]


def safe_tenant(tenant_id: str) -> str:
    """Tenant ids reach the filesystem only through this alphabet."""
    clean = _SAFE_TENANT.sub("_", tenant_id or "tenant").strip("_")
    return (clean or "tenant")[:64]


def _kind_for(ext: str, media_type: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS or (media_type or "").startswith("text/"):
        return "text"
    return "file"


def validate_and_store(data_dir: str | Path, tenant_id: str,
                       message_id: str, items: list[dict]) -> list[dict]:
    """Validate, store, and describe one message's attachments.

    Returns metadata dicts (name, stored_name, media_type, size, kind,
    text_chars) in input order. Raises AttachmentError with a plain
    message on any violation. Writes bytes + meta.json under
    <data_dir>/attachments/<tenant>/<message_id>/ and NOTHING else.
    File contents are never logged, never echoed back in metadata.
    """
    if not isinstance(items, list) or not items:
        raise AttachmentError("No attachments were sent.")
    if len(items) > MAX_FILES:
        raise AttachmentError(
            f"Too many files — at most {MAX_FILES} files per message.")
    total = 0
    decoded: list[tuple[dict, bytes]] = []
    for item in items:
        if not isinstance(item, dict):
            raise AttachmentError("Each attachment must be an object.")
        name = item.get("name")
        media_type = item.get("media_type") or ""
        size = item.get("size")
        content_b64 = item.get("content_b64")
        if not isinstance(name, str) or not name.strip():
            raise AttachmentError("Each attachment needs a file name.")
        if not isinstance(content_b64, str) or not content_b64:
            raise AttachmentError(f"File '{safe_filename(name)}' arrived empty.")
        if not isinstance(size, int) or size < 0:
            raise AttachmentError(f"File '{safe_filename(name)}' has no size.")
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise AttachmentError(
                f"File '{safe_filename(name)}' has a type that cannot be "
                "attached (text files, images, and PDF only).")
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError(
                f"File '{safe_filename(name)}' did not arrive intact.")
        if len(raw) != size:
            raise AttachmentError(
                f"File '{safe_filename(name)}' did not arrive intact.")
        if len(raw) > MAX_FILE_BYTES:
            raise AttachmentError(
                f"File '{safe_filename(name)}' is over the 5 MB per-file limit.")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError(
                "All files together are over the 10 MB per-message limit.")
        decoded.append(({"name": safe_filename(name),
                         "media_type": str(media_type)[:100],
                         "size": len(raw),
                         "kind": _kind_for(ext, str(media_type))}, raw))
    tenant_dir = (Path(data_dir) / "attachments" / safe_tenant(tenant_id)
                  / message_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    base_resolved = tenant_dir.resolve()
    metas: list[dict] = []
    for meta, raw in decoded:
        stored_name = meta["name"]
        dest = (base_resolved / stored_name).resolve()
        if dest.parent != base_resolved:
            raise AttachmentError("A file name escaped its folder.")
        # Duplicate names in one batch: keep both, second gets a suffix.
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            n = 2
            while True:
                cand = (base_resolved / f"{stem}-{n}{suffix}").resolve()
                if not cand.exists():
                    dest = cand
                    break
                n += 1
            stored_name = dest.name
        dest.write_bytes(raw)
        meta = dict(meta, stored_name=stored_name, text_chars=0)
        if meta["kind"] == "text":
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                meta["kind"] = "text-unreadable"
            else:
                meta["_text"] = text
                meta["text_chars"] = len(text)
        metas.append(meta)
    (base_resolved / "meta.json").write_text(
        json.dumps({"message_id": message_id, "tenant_id": tenant_id,
                    "files": [{k: v for k, v in meta.items()
                               if not k.startswith("_")}
                              for meta in metas]},
                   indent=2), encoding="utf-8")
    return metas


def build_context_section(metas: list[dict]) -> str:
    """The text appended to the user message the model receives.

    Text files contribute a delimited section (capped per file and in
    total). Images, PDFs, and unreadable text contribute one honest
    line each: named, never pretended to be read.
    """
    if not metas:
        return ""
    lines = ["", "Attached files:"]
    budget = MAX_TEXT_TOTAL_CHARS
    for meta in metas:
        name = meta["name"]
        if meta["kind"] == "text" and budget > 0:
            text = meta.get("_text", "")
            take = text[:min(len(text), MAX_TEXT_CHARS_PER_FILE, budget)]
            budget -= len(take)
            lines.append(f"--- {name} ---")
            lines.append(take)
            if len(text) > len(take):
                lines.append(f"[{name} continues past the included part.]")
        elif meta["kind"] == "image":
            lines.append(f"- {name} (image, {meta['size']} bytes) — "
                         "attached as a file; the model reads text only.")
        elif meta["kind"] == "pdf":
            lines.append(f"- {name} (PDF, {meta['size']} bytes) — "
                         "attached as a file; its text was not extracted.")
        elif meta["kind"] == "text-unreadable":
            lines.append(f"- {name} — attached as a file; "
                         "it could not be read as text.")
        else:
            lines.append(f"- {name} ({meta['size']} bytes) — attached as a file.")
    return "\n".join(lines)


def new_message_id() -> str:
    return uuid.uuid4().hex
