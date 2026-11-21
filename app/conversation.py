"""AgentQon — conversation management (expansion A).

Pure message-state rules, stdlib-only, no UI framework dependency:
the web UI consumes these rules (status labels, copy content, scroll
decisions, draft storage) and the same rules are unit-tested here.

A1 stable IDs: a message id is assigned once (msg_<random>) and never
changes — re-renders look messages up by id, they never renumber.
A2 statuses: draft | waiting | generating | completed | failed |
cancelled | interrupted, each with a plain-words label for display.
A3 partial recovery: a failed generation keeps its partial text
marked partial; retry() creates a NEW clean message linked to the
old one and never appends onto the partial.
A4 edit semantics: edit() requires an explicit mode — "replace"
(same message, original kept in edit history) or "branch" (a new
message; the original untouched). No mode, no edit: ambiguity is
refused, never guessed.
A5 regeneration idempotency: ActionLedger records external actions
(email, purchase, patch) by key. A regeneration that would repeat
an executed action is refused by the ledger — the action runs once.
A6 copy: copy_content() returns the message text only — never
metadata, logs, or labels.
A7 streaming markdown: sanitize_streaming_markdown() closes a
half-written code fence for rendering and flags the text incomplete,
so a stream never shows raw markup soup. Unclosed emphasis/link
markup is left as plain text, not rendered as formatting.
A8 code-block actions: actions_for_block() offers copy/download
always; apply-patch only for patch/diff blocks; open-file only when
a safe relative path is attached; run-test only for test files.
A9 scroll: should_scroll() is True only when the user is already at
the bottom — reading earlier content is never interrupted.
A10 drafts: DraftStore persists unsent drafts (JSON file when a path
is given, memory otherwise) so a draft survives navigation and
refresh; the web UI mirrors this in localStorage.
A11 duplicate sends: SendGuard.claim() admits a send key once —
a double-tap/double-submit sends once.
A12 cancellation: cancel_description() states in plain words what
stopped: generation, a tool run, or an external operation still
completing.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .domain import new_id, now_iso

STATUSES = ("draft", "waiting", "generating", "completed", "failed",
            "cancelled", "interrupted")

STATUS_LABELS = {
    "draft": "Draft — not sent yet",
    "waiting": "Waiting to start",
    "generating": "Writing a reply…",
    "completed": "Done",
    "failed": "Could not finish — try again",
    "cancelled": "Stopped by you",
    "interrupted": "Interrupted — the reply stopped part-way",
}


def status_label(status: str) -> str:
    """Plain-words label for a message status (A2). Unknown statuses
    are never shown raw — they fall back to a plain-words unknown."""
    return STATUS_LABELS.get(status, "Status unknown")


@dataclass
class Message:
    message_id: str
    conversation_id: str
    role: str  # "user" | "assistant" | "system"
    content: str = ""
    status: str = "draft"
    partial: bool = False
    parent_id: str = ""
    retried_from: str = ""
    edited: bool = False
    edit_history: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown message status {self.status!r}")
        self.created_at = self.created_at or now_iso()
        self.updated_at = self.updated_at or self.created_at

    def view(self) -> dict:
        return {
            "message_id": self.message_id, "conversation_id": self.conversation_id,
            "role": self.role, "content": self.content, "status": self.status,
            "status_label": status_label(self.status), "partial": self.partial,
            "parent_id": self.parent_id, "retried_from": self.retried_from,
            "edited": self.edited,
        }


class MessageThread:
    """An ordered thread of stably-identified messages (A1)."""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self._messages: dict[str, Message] = {}
        self._order: list[str] = []

    def add(self, role: str, content: str = "",
            status: str = "draft") -> Message:
        msg = Message(message_id=new_id("msg"),
                      conversation_id=self.conversation_id, role=role,
                      content=content, status=status)
        self._messages[msg.message_id] = msg
        self._order.append(msg.message_id)
        return msg

    def get(self, message_id: str) -> Message | None:
        return self._messages.get(message_id)

    def render(self) -> list[dict]:
        """Re-render by stable id (A1): the same messages, the same
        ids, in the same order — a re-render never renumbers."""
        return [self._messages[mid].view() for mid in self._order]

    def set_status(self, message_id: str, status: str) -> Message:
        msg = self._messages[message_id]
        if status not in STATUSES:
            raise ValueError(f"unknown message status {status!r}")
        msg.status = status
        msg.updated_at = now_iso()
        return msg

    def mark_partial(self, message_id: str, partial_text: str) -> Message:
        """A failed generation's partial text stays, marked partial
        (A3). The message is failed AND partial — both facts visible."""
        msg = self._messages[message_id]
        msg.content = partial_text
        msg.partial = True
        msg.status = "failed"
        msg.updated_at = now_iso()
        return msg

    def retry(self, message_id: str) -> Message:
        """Retry starts CLEAN (A3): a new message in 'waiting' with
        empty content, linked to the failed one. It NEVER appends
        onto the partial text as if it were one answer."""
        old = self._messages[message_id]
        new = Message(message_id=new_id("msg"),
                      conversation_id=self.conversation_id,
                      role=old.role, content="", status="waiting",
                      retried_from=old.message_id)
        self._messages[new.message_id] = new
        self._order.append(new.message_id)
        return new

    def edit(self, message_id: str, new_content: str,
             mode: str) -> Message:
        """Edit-and-resubmit (A4). mode must be 'replace' or 'branch'
        — anything else (including missing) is refused, never guessed.
        Replace: same id, original kept in edit_history. Branch: a
        new message; the original is untouched."""
        if mode not in ("replace", "branch"):
            raise ValueError(
                "editing a message must state clearly whether it replaces "
                "the original or creates a new branch — pass mode='replace' "
                "or mode='branch'")
        msg = self._messages[message_id]
        if mode == "replace":
            msg.edit_history.append({"content": msg.content,
                                     "at": msg.updated_at})
            msg.content = new_content
            msg.edited = True
            msg.status = "draft"
            msg.updated_at = now_iso()
            return msg
        branched = Message(message_id=new_id("msg"),
                           conversation_id=self.conversation_id,
                           role=msg.role, content=new_content,
                           status="draft", parent_id=msg.message_id)
        self._messages[branched.message_id] = branched
        self._order.append(branched.message_id)
        return branched


# ---------------------------------------------------------------- A5 regeneration

class ActionLedger:
    """Idempotency on the action side (A5). External actions —
    sending an email, placing a purchase, applying a patch — are
    recorded by key when they execute. A regeneration that presents
    the same key is refused: the action already happened and must
    not happen again."""

    def __init__(self) -> None:
        self._done: dict[str, dict] = {}

    def record(self, key: str, kind: str) -> dict:
        """Record an executed action. A second record for the same
        key reports already_done and does NOT count as a new action."""
        if key in self._done:
            return {"key": key, "already_done": True,
                    "kind": self._done[key]["kind"]}
        self._done[key] = {"kind": kind, "at": now_iso()}
        return {"key": key, "already_done": False, "kind": kind}

    def already_done(self, key: str) -> bool:
        return key in self._done

    def regen_may_repeat(self, key: str) -> tuple[bool, str]:
        """May a regeneration repeat this action? Never, when the
        ledger says it already ran."""
        if key in self._done:
            return False, (f"this action already ran "
                           f"({self._done[key]['kind']}) — regenerating "
                           f"the reply must not run it again")
        return True, ""


# ---------------------------------------------------------------- A6/A7/A8 helpers

def copy_content(message: Message | dict) -> str:
    """Copy behavior (A6): the message CONTENT only. Metadata, logs,
    and labels are never part of a copy."""
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return message.content


def sanitize_streaming_markdown(text: str) -> tuple[str, bool, list[str]]:
    """Render incomplete markdown sanely while streaming (A7).
    Returns (renderable_text, incomplete, notes). Rules:
      * An unclosed code fence (odd count of ``` lines) is closed
        for rendering, so the rest of the text is not swallowed
        into a code block and never shows as raw markup soup.
      * Unclosed emphasis markers are left as literal text (the
        renderer must not invent formatting the author did not
        finish).
      * An unclosed link [text](url is left as plain text.
    """
    notes: list[str] = []
    incomplete = False
    lines = text.split("\n")
    fence_lines = [i for i, line in enumerate(lines)
                   if line.strip().startswith("```")]
    if len(fence_lines) % 2 == 1:
        incomplete = True
        notes.append("code block not finished yet — showing it as a "
                     "code block until the closing marker arrives")
        lines.append("```")
    out = "\n".join(lines)
    # Unclosed link: "[text](partial" -> plain text, no link markup.
    if re.search(r"\[[^\]]*\]\([^)]*$", out):
        incomplete = True
        notes.append("link not finished yet — showing it as plain text")
        out = re.sub(r"\[([^\]]*)\]\([^)]*$", r"\1", out)
    return out, incomplete, notes


_SAFE_PATH_RE = re.compile(r"^(?!/)(?!\.\./)[A-Za-z0-9_./-]+$")


def actions_for_block(block: dict) -> list[str]:
    """Code-block actions (A8): offered ONLY where safely supported.
    copy/download: always. apply-patch: patch/diff blocks only.
    open-file: only when a safe relative path is attached (no
    absolute paths, no parent-directory escapes). run-test: only
    for test files (name contains 'test' and a runnable language)."""
    actions = ["copy", "download"]
    language = str(block.get("language", "")).lower()
    kind = str(block.get("kind", "")).lower()
    if language in ("diff", "patch") or kind == "patch":
        actions.append("apply-patch")
    path = str(block.get("path", ""))
    if path and _SAFE_PATH_RE.match(path):
        actions.append("open-file")
    if path and "test" in Path(path).name.lower() \
            and language in ("python", "javascript", "typescript"):
        actions.append("run-test")
    return actions


def should_scroll(user_at_bottom: bool, new_content: bool = True) -> bool:
    """Scroll behavior (A9): follow new content ONLY when the user
    is already at the bottom. Reading earlier content is never
    interrupted by a jump."""
    return bool(user_at_bottom and new_content)


# ---------------------------------------------------------------- A10/A11/A12

class DraftStore:
    """Draft preservation (A10). Drafts live in memory and, when a
    path is given, in a JSON file — so an unsent draft survives
    navigation (new view, same store) and refresh (file re-read)."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._drafts: dict[str, dict] = {}
        self._lock = threading.Lock()
        if self._path and self._path.exists():
            try:
                self._drafts = json.loads(self._path.read_text())
            except (OSError, ValueError):
                self._drafts = {}

    def save(self, conversation_id: str, text: str) -> None:
        with self._lock:
            self._drafts[conversation_id] = {"text": text,
                                             "saved_at": now_iso()}
            self._persist_locked()

    def get(self, conversation_id: str) -> str:
        return self._drafts.get(conversation_id, {}).get("text", "")

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._drafts.pop(conversation_id, None)
            self._persist_locked()

    def _persist_locked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._drafts))
        except OSError:
            pass  # a failed persist never loses the in-memory draft


class SendGuard:
    """Duplicate-submission protection for chat send (A11): the
    first claim of a send key wins; a double-tap/double-submit with
    the same key sends once."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def claim(self, send_key: str) -> bool:
        with self._lock:
            if send_key in self._seen:
                return False
            self._seen.add(send_key)
            return True


def cancel_description(what_stopped: str) -> str:
    """Cancellation clarity (A12): plain words for what stopped."""
    mapping = {
        "generation": ("Stopped while writing the reply. Nothing was "
                       "sent, and the partial reply is kept as a draft."),
        "tool_run": ("Stopped while a tool was running. The tool's "
                     "work may have partly finished — check before "
                     "running it again."),
        "external_pending": ("Stopped here, but an outside operation "
                             "is still completing on the other side. "
                             "It may still finish even though this "
                             "reply stopped."),
    }
    return mapping.get(what_stopped,
                       "Stopped. If an outside operation was running, "
                       "it may still be completing.")
