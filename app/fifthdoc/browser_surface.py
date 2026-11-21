"""§6 Browser utilities — a NEW, controlled, permissioned surface.

AgentQon today has web_read only; this is not that, and it is not any
assistant's own browser tooling. It is a session model: the harness records
tabs, navigation, actions and diagnostics, and enforces permission +
redaction. Live driving against a real browser binary is NOT_TESTED here
(no browser binary is pinned in this environment); the session state
machine, permission gate, redaction and detection rules are fully tested.

Hard rules:
- CAPTCHA / access-denial DETECTION only. Never bypass access controls,
  never evade anti-abuse systems.
- Credentials and sensitive form values are redacted from every log and
  diagnostic before storage.
- A form submission needs an explicit confirmation record.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

SENSITIVE_KEYS = ("password", "passwd", "token", "secret", "api_key", "apikey",
                  "card", "ssn", "otp", "code")
CAPTCHA_MARKERS = ("captcha", "recaptcha", "hcaptcha", "verify you are human",
                   "access denied", "403 forbidden", "bot detection",
                   "unusual traffic")
SESSION_ACTIONS_REQUIRING_PERMISSION = ("navigate", "submit_form", "download",
                                        "screenshot")


def redact_value(key: str, value: str) -> str:
    lowered = key.lower()
    if any(s in lowered for s in SENSITIVE_KEYS):
        return "[redacted]"
    return value


def redact_record(record: dict) -> dict:
    return {k: redact_value(k, str(v)) for k, v in record.items()}


def detect_access_barrier(page_text: str, status: int | None = None) -> dict:
    lowered = page_text.lower()
    hits = [m for m in CAPTCHA_MARKERS if m in lowered]
    blocked = bool(hits) or status in (403, 429)
    return {"blocked": blocked, "markers": hits, "status": status,
            "action": ("Detected an access check or denial. Stopping here — "
                       "this surface never bypasses it." if blocked else "ok")}


@dataclass
class Tab:
    tab_id: str
    url: str = ""
    title: str = ""
    status: str = "idle"  # idle | loading | loaded | failed
    console_errors: list[str] = field(default_factory=list)
    network_failures: list[str] = field(default_factory=list)


class BrowserSession:
    def __init__(self, session_id: str, permission_granted: bool = False):
        self.session_id = session_id
        self.permission_granted = permission_granted
        self.tabs: dict[str, Tab] = {}
        self.active_tab: str | None = None
        self.actions: list[dict] = []
        self.downloads: list[dict] = []
        self.closed = False
        self.created_at = time.time()

    def _log(self, action: str, details: dict) -> dict:
        entry = {"action": action, "at": time.time(),
                 **redact_record(details)}
        self.actions.append(entry)
        return entry

    def _need_permission(self, action: str) -> dict | None:
        if action in SESSION_ACTIONS_REQUIRING_PERMISSION and not self.permission_granted:
            return {"ok": False, "reason": "Browser action needs permission first."}
        return None

    def status(self) -> dict:
        return {"session_id": self.session_id, "closed": self.closed,
                "tabs": len(self.tabs), "active_tab": self.active_tab,
                "actions_recorded": len(self.actions)}

    def open_tab(self, tab_id: str, url: str = "") -> Tab:
        tab = Tab(tab_id=tab_id, url=url)
        self.tabs[tab_id] = tab
        self.active_tab = tab_id
        self._log("open_tab", {"tab_id": tab_id, "url": url})
        return tab

    def select_tab(self, tab_id: str) -> dict:
        if tab_id not in self.tabs:
            return {"ok": False, "reason": "No such tab in this session."}
        self.active_tab = tab_id
        self._log("select_tab", {"tab_id": tab_id})
        return {"ok": True, "active_tab": tab_id}

    def navigate(self, url: str, timeout_seconds: float = 30.0) -> dict:
        blocked = self._need_permission("navigate")
        if blocked:
            return blocked
        if self.active_tab is None:
            return {"ok": False, "reason": "No tab selected."}
        tab = self.tabs[self.active_tab]
        tab.url, tab.status = url, "loading"
        self._log("navigate", {"tab_id": tab.tab_id, "url": url,
                               "timeout_seconds": str(timeout_seconds)})
        return {"ok": True, "tab_id": tab.tab_id, "url": url,
                "navigation_timeout_seconds": timeout_seconds}

    def page_loaded(self, tab_id: str, title: str, page_text: str = "") -> dict:
        tab = self.tabs.get(tab_id)
        if tab is None:
            return {"ok": False, "reason": "No such tab."}
        tab.title, tab.status = title, "loaded"
        barrier = detect_access_barrier(page_text)
        self._log("page_loaded", {"tab_id": tab_id, "title": title})
        return {"ok": True, "tab_id": tab_id, "title": title, "url": tab.url,
                "access_barrier": barrier}

    def wait_for(self, condition: str, timeout_seconds: float = 30.0) -> dict:
        self._log("wait", {"condition": condition})
        return {"ok": True, "condition": condition,
                "timeout_seconds": timeout_seconds,
                "note": "Wait recorded; a live driver would poll until the "
                        "condition holds or the timeout ends."}

    def screenshot(self) -> dict:
        blocked = self._need_permission("screenshot")
        if blocked:
            return blocked
        self._log("screenshot", {"tab_id": str(self.active_tab)})
        return {"ok": True, "tab_id": self.active_tab,
                "note": "Screenshot capture needs a live browser driver; "
                        "this record proves the action was requested."}

    def dom_snapshot(self, elements: list[dict]) -> dict:
        redacted = []
        for element in elements:
            redacted.append(redact_record(element))
            # A field NAMED password (or token/secret) is sensitive even
            # when its dict key is the generic "value".
            field_name = str(element.get("name", "")).lower()
            if any(s in field_name for s in SENSITIVE_KEYS):
                redacted[-1]["value"] = "[redacted]"
        self._log("dom_snapshot", {"tab_id": str(self.active_tab)})
        return {"ok": True, "elements": redacted}

    def inspect_locator(self, locator: str, found: bool, count: int = 0) -> dict:
        self._log("inspect_locator", {"locator": locator})
        return {"ok": True, "locator": locator, "found": found, "count": count}

    def submit_form(self, fields: dict, confirmed: bool = False) -> dict:
        blocked = self._need_permission("submit_form")
        if blocked:
            return blocked
        if not confirmed:
            return {"ok": False, "reason": "Form submission needs explicit confirmation."}
        entry = self._log("submit_form", fields)
        return {"ok": True, "recorded": entry}

    def note_download(self, filename: str, url: str) -> dict:
        blocked = self._need_permission("download")
        if blocked:
            return blocked
        self.downloads.append({"filename": filename, "url": url})
        self._log("download_detected", {"filename": filename, "url": url})
        return {"ok": True, "downloads": len(self.downloads)}

    def note_popup(self, url: str) -> dict:
        self._log("popup_or_new_tab", {"url": url})
        return {"ok": True, "note": "Popup/new tab recorded; it opens only as a "
                "new tab inside this session."}

    def auth_state(self, cookie_expiry: float | None, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if cookie_expiry is None:
            return {"signed_in": False, "warning": "No session cookie recorded."}
        remaining = cookie_expiry - now
        return {"signed_in": remaining > 0,
                "warning": ("Session cookie expires soon — sign in again before "
                            "it runs out." if 0 < remaining < 3600 else ""),
                "seconds_remaining": max(0, int(remaining))}

    def page_changed(self, tab_id: str, old_url: str, new_url: str) -> dict:
        changed = old_url != new_url
        if changed:
            self._log("page_changed", {"tab_id": tab_id, "old_url": old_url,
                                       "new_url": new_url})
        return {"changed": changed, "old_url": old_url, "new_url": new_url}

    def console_error(self, tab_id: str, message: str) -> None:
        if tab_id in self.tabs:
            self.tabs[tab_id].console_errors.append(message)

    def network_failure(self, tab_id: str, summary: str) -> None:
        if tab_id in self.tabs:
            self.tabs[tab_id].network_failures.append(summary)

    def cleanup(self) -> dict:
        self.closed = True
        self.tabs.clear()
        return {"ok": True, "closed": True, "actions_recorded": len(self.actions),
                "note": "Session closed; tabs cleared. Action records are kept "
                        "for review, with secrets redacted."}
