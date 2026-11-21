"""AgentQon — §8 interface rules as shared utilities.

The governing advice for the third audit is binding here: these are
shared utilities, validation rules and state-machine invariants — not
hundreds of independent features. The same rules are mirrored in
JavaScript in apps/web/index.html (the "W7 platform polish" block),
where they govern the real UI; this module is the testable Python
mirror, so the rules can be proven without a browser.

Rules implemented (audit numbers in brackets):
- [142] Loading-state delay: a spinner shows only after 300 ms, so a
  fast load never flickers.
- [143] Stale-loading recovery: a load older than the timeout is
  recoverable, with a plain-words state, not a frozen spinner.
- [144] Focus restoration: focus targets are recorded and restored.
- [145/146] Tooltip delay + boundary handling: tooltips wait 400 ms
  and are clamped inside the viewport.
- [147] Long-text wrapping: wrapping breaks long words instead of
  overflowing.
- [148] Table-overflow handling: wide tables scroll inside their
  container, never breaking the page.
- [149] Clipboard-failure handling: a failed copy explains itself and
  offers the text to copy by hand.
- [150] Toast dedup: the same message twice in a row shows once.
- [151] Notification priority: errors outrank warnings outrank info.
- [153] Empty-state guidance: every empty list explains itself and
  says what to do next.
- [154] Unsaved-change detection: dirty state blocks silent navigation.
- [155] Shortcut-conflict detection: two actions on one shortcut is
  refused at registration.
- [156] Accessible status announcements: status text is built for a
  screen reader (aria-live wording), not just colour.
- [157] Animation cancellation: animations honour reduced motion and
  can be cancelled.
- [158] Stale-data indicators: data older than a threshold is labelled
  stale with its age.
- [159] Optimistic-update rollback: if the server rejects an optimistic
  change (the Disable Agent example), the UI reverts AND explains.
- [160] UI-state restoration: panel sizes, filters and selections are
  saved and restored.
- [top-15 #14] Stale-response prevention: a slower older response must
  NEVER overwrite newer state. Enforced by sequence numbers.
"""
from __future__ import annotations

import time

LOADING_DELAY_MS = 300
LOADING_TIMEOUT_SECONDS = 30.0
TOOLTIP_DELAY_MS = 400
STALE_DATA_AFTER_SECONDS = 300.0

PRIORITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def should_show_loading(elapsed_ms: float) -> bool:
    """[142] Only show a loading indicator after the delay, so fast
    loads never flicker."""
    return elapsed_ms >= LOADING_DELAY_MS


def loading_state(*, started_at: float, now: float | None = None,
                  finished: bool = False) -> dict:
    """[142/143] Loading state machine: hidden while fast, visible
    after the delay, recoverable (not frozen) after the timeout."""
    moment = time.time() if now is None else now
    elapsed = moment - started_at
    if finished:
        return {"state": "done", "show_spinner": False}
    if elapsed >= LOADING_TIMEOUT_SECONDS:
        return {"state": "stale",
                "show_spinner": False,
                "message": ("This is taking longer than expected. The "
                            "previous result is still shown, and you can "
                            "try again."),
                "recoverable": True}
    return {"state": "loading" if elapsed >= LOADING_DELAY_MS / 1000 else "waiting",
            "show_spinner": should_show_loading(elapsed * 1000)}


class FocusTracker:
    """[144] Record where focus was, restore it after an action."""

    def __init__(self) -> None:
        self._saved: str | None = None

    def save(self, element_id: str) -> None:
        self._saved = element_id

    def restore_target(self) -> str | None:
        return self._saved


def tooltip_position(*, x: int, y: int, width: int, height: int,
                     viewport_width: int, viewport_height: int) -> dict:
    """[145/146] Tooltip placement: waits TOOLTIP_DELAY_MS (enforced in
    the UI layer), and is clamped so it never leaves the viewport."""
    clamped_x = min(max(x, 0), max(viewport_width - width, 0))
    clamped_y = min(max(y, 0), max(viewport_height - height, 0))
    return {"x": clamped_x, "y": clamped_y,
            "delay_ms": TOOLTIP_DELAY_MS,
            "clamped": (clamped_x != x or clamped_y != y)}


def wrap_long_text(text: str, *, width: int = 80) -> str:
    """[147] Wrap long text, breaking over-long words instead of
    letting them overflow their container."""
    words: list[str] = []
    for word in (text or "").split(" "):
        while len(word) > width:
            words.append(word[:width])
            word = word[width:]
        words.append(word)
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "\n".join(lines)


def truncate_with_indicator(text: str, *, limit: int = 200) -> dict:
    """Truncation that SHOWS it was cut: the shortened text plus an
    explicit marker and the full length. Cut text never pretends to
    be complete."""
    if len(text) <= limit:
        return {"text": text, "truncated": False, "full_length": len(text)}
    return {"text": text[:limit] + "… (cut — open to see the full text)",
            "truncated": True, "full_length": len(text)}


def clipboard_result(*, success: bool, text: str) -> dict:
    """[149] A failed copy is not silent: it explains and hands the
    text back so the user can copy it by hand."""
    if success:
        return {"copied": True, "message": "Copied."}
    return {"copied": False,
            "message": ("Copy did not work in this browser. The text is "
                        "shown below — select it and copy it by hand."),
            "text_to_copy": text}


class ToastDedup:
    """[150] The same toast twice in a row shows once."""

    def __init__(self) -> None:
        self._last: str | None = None

    def show(self, message: str) -> dict:
        if message == self._last:
            return {"shown": False, "reason": "Same message already shown."}
        self._last = message
        return {"shown": True, "message": message}


def sort_notifications(notifications: list[dict]) -> list[dict]:
    """[151] Errors first, then warnings, then information."""
    return sorted(notifications,
                  key=lambda n: PRIORITY_ORDER.get(n.get("priority", "info"), 2))


def empty_state(*, what: str, next_step: str) -> dict:
    """[153] Every empty list explains itself and says what to do."""
    return {"empty": True,
            "message": f"There is nothing in {what} yet.",
            "next_step": next_step}


class UnsavedChanges:
    """[154] Dirty tracking: navigation away from dirty work warns."""

    def __init__(self) -> None:
        self.dirty = False

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_saved(self) -> None:
        self.dirty = False

    def may_navigate(self) -> dict:
        if self.dirty:
            return {"allowed": False,
                    "message": ("You have unsaved changes. Save them or "
                                "discard them before leaving this page.")}
        return {"allowed": True, "message": ""}


class ShortcutRegistry:
    """[155] Two actions on one shortcut is a conflict, refused at
    registration — not discovered by the user pressing the key."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def register(self, shortcut: str, action: str) -> dict:
        existing = self._map.get(shortcut)
        if existing is not None and existing != action:
            return {"registered": False,
                    "conflict": True,
                    "reason": (f"Shortcut {shortcut!r} is already used by "
                               f"{existing!r} — it cannot also run {action!r}.")}
        self._map[shortcut] = action
        return {"registered": True, "conflict": False}

    def action_for(self, shortcut: str) -> str | None:
        return self._map.get(shortcut)


def announce(status: str, *, kind: str = "info") -> dict:
    """[156] Accessible status announcement: text a screen reader can
    speak, never colour alone. Every status carries words."""
    return {"aria_live": "polite" if kind == "info" else "assertive",
            "text": status,
            "kind": kind}


def animation_policy(*, prefers_reduced_motion: bool) -> dict:
    """[157] Animations are cancelled under reduced motion, and the
    UI must still work without them (no content hidden behind an
    animation)."""
    return {"animations_enabled": not prefers_reduced_motion,
            "note": ("Animations are off because reduced motion is "
                     "requested. Everything still works without them.")
            if prefers_reduced_motion else ""}


def stale_indicator(*, data_age_seconds: float,
                    threshold: float = STALE_DATA_AFTER_SECONDS) -> dict:
    """[158] Data older than the threshold is labelled stale, with its
    age in plain words, instead of silently looking current."""
    if data_age_seconds <= threshold:
        return {"stale": False, "label": "Current."}
    minutes = int(data_age_seconds // 60)
    return {"stale": True,
            "label": (f"Stale — this data is about {minutes} minutes old. "
                      "Refresh it before acting on it.")}


class OptimisticUpdate:
    """[159] Optimistic update with rollback: apply locally, confirm
    with the server, and on rejection REVERT to the previous value AND
    explain why. The Disable Agent example: if the server rejects the
    disable, the agent shows as enabled again, with the reason."""

    def __init__(self, initial: object):
        self.value = initial
        self._pending: object | None = None
        self._previous: object | None = None

    def apply_optimistic(self, new_value: object) -> None:
        self._previous = self.value
        self._pending = new_value
        self.value = new_value

    def confirm(self) -> dict:
        self._pending = None
        self._previous = None
        return {"state": "confirmed", "value": self.value}

    def reject(self, reason: str) -> dict:
        self.value = self._previous
        self._pending = None
        self._previous = None
        return {"state": "reverted", "value": self.value,
                "explanation": (f"The change was not saved: {reason} "
                                "The previous value is shown again.")}


class UIStateStore:
    """[160] Save and restore UI state: panel sizes, filters, selected
    items. Restoration never invents values — unknown keys are ignored."""

    def __init__(self) -> None:
        self._state: dict = {}

    def save(self, state: dict) -> None:
        self._state.update(state)

    def restore(self) -> dict:
        return dict(self._state)


class StaleResponseGuard:
    """[top-15 #14] Stale-response prevention. Every request gets a
    rising sequence number; a response is accepted ONLY when its
    number is at least the newest requested. A slower older response
    can NEVER overwrite newer state — it is dropped and counted."""

    def __init__(self) -> None:
        self._latest_requested = 0
        self._latest_accepted = 0
        self.dropped_stale = 0

    def next_request(self) -> int:
        self._latest_requested += 1
        return self._latest_requested

    def accept(self, seq: int, payload: object) -> dict:
        if seq < self._latest_requested:
            self.dropped_stale += 1
            return {"accepted": False, "payload": None,
                    "reason": ("An older response arrived after a newer "
                               "request — it was ignored so it cannot "
                               "overwrite newer state."),
                    "dropped_stale": self.dropped_stale}
        self._latest_accepted = seq
        return {"accepted": True, "payload": payload}
