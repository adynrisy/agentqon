"""§2 Task inbox — one unified inbox for all 12 item types.

RULE (load-bearing): dismissing an inbox item NEVER cancels the underlying
mission. Dismiss only removes the item from the inbox; the mission keeps
whatever state it had. Cancellation is a separate, explicit mission action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time

TYPES = (
    "approval", "failed_mission", "awaiting_input", "incomplete_deliverable",
    "review_request", "stalled_background", "scheduled_awaiting_execution",
    "unverified_result", "conflicting_recommendations", "provider_auth_problem",
    "ready_for_final_approval", "manual_intervention",
)

ACTIONS_BY_STATE = {
    "open": ("Open", "Snooze", "Dismiss"),
    "awaiting_decision": ("Approve", "Reject", "Snooze", "Dismiss"),
    "failed": ("Retry", "Open", "Dismiss"),
    "paused": ("Resume", "Dismiss"),
    "assigned": ("Open", "Assign", "Snooze", "Dismiss"),
}

PRIORITIES = ("low", "normal", "high", "urgent")


@dataclass
class InboxItem:
    item_id: str
    kind: str
    title: str
    mission_id: str | None = None
    state: str = "open"
    priority: str = "normal"
    due_at: float | None = None
    assignee: str | None = None
    snoozed_until: float | None = None
    dismissed: bool = False
    note: str = ""
    created_at: float = field(default_factory=time.time)


class Inbox:
    def __init__(self):
        self.items: dict[str, InboxItem] = {}

    def add(self, item: InboxItem) -> InboxItem:
        if item.kind not in TYPES:
            raise ValueError(f"Unknown inbox type: {item.kind}")
        if item.priority not in PRIORITIES:
            raise ValueError(f"Unknown priority: {item.priority}")
        self.items[item.item_id] = item
        return item

    def outstanding_count(self) -> int:
        now = time.time()
        return sum(1 for i in self.items.values()
                   if not i.dismissed and not (i.snoozed_until and i.snoozed_until > now))

    def list(self, kind: str | None = None, priority: str | None = None,
             state: str | None = None, include_dismissed: bool = False) -> list[InboxItem]:
        out = []
        for i in sorted(self.items.values(), key=lambda x: x.created_at):
            if not include_dismissed and i.dismissed:
                continue
            if kind and i.kind != kind:
                continue
            if priority and i.priority != priority:
                continue
            if state and i.state != state:
                continue
            out.append(i)
        return out

    def actions_for(self, item: InboxItem) -> tuple[str, ...]:
        return ACTIONS_BY_STATE.get(item.state, ("Open", "Dismiss"))

    def apply(self, item_id: str, action: str, **kw) -> dict:
        """Apply a per-item action. Dismiss never touches the mission."""
        item = self.items.get(item_id)
        if item is None:
            return {"ok": False, "reason": "No such inbox item."}
        if item.dismissed:
            return {"ok": False, "reason": "This inbox item was dismissed."}
        allowed = self.actions_for(item)
        if action not in allowed:
            return {"ok": False, "reason": f"Action not available for this item. "
                    f"Available: {', '.join(allowed)}"}
        if action == "Dismiss":
            item.dismissed = True
            return {"ok": True, "action": "Dismiss",
                    "mission_cancelled": False,
                    "note": "Only the inbox item was removed. The mission itself "
                            "was not changed or cancelled."}
        if action == "Snooze":
            item.snoozed_until = time.time() + float(kw.get("seconds", 3600))
        elif action == "Approve":
            item.state = "approved"
        elif action == "Reject":
            item.state = "rejected"
            item.note = kw.get("feedback", "")
        elif action == "Retry":
            item.state = "open"
        elif action == "Resume":
            item.state = "open"
        elif action == "Assign":
            item.assignee = kw.get("assignee")
        return {"ok": True, "action": action, "state": item.state,
                "mission_cancelled": False}

    def bulk(self, item_ids: list[str], action: str, **kw) -> dict:
        results = {iid: self.apply(iid, action, **kw) for iid in item_ids}
        return {"results": results,
                "succeeded": sum(1 for r in results.values() if r.get("ok"))}
