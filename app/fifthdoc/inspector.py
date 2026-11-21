"""§8 Data inspector — ONE unified inspector, not scattered views.

Every event links to its task, its agent and its artifact. That linkage is
what separates this from a debug log: a filter by agent shows that agent's
work across every stage, and a filter by artifact shows everything that
touched it. Secrets are redacted by default.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

STAGES = ("input", "requirements", "plan", "step", "assignment", "tool",
          "artifact", "context", "validation", "resource", "error", "output")
SEVERITIES = ("info", "warning", "error")

_SECRET_RE = re.compile(r"(api[_-]?key|password|token|secret)\s*[:=]\s*\S+",
                        re.IGNORECASE)


def redact_text(text: str) -> str:
    return _SECRET_RE.sub(r"\1=[redacted]", text)


@dataclass
class InspectorEvent:
    event_id: str
    stage: str
    operation: str
    task_id: str
    agent_id: str
    artifact_id: str | None = None
    severity: str = "info"
    detail: str = ""
    resource_usage: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class Inspector:
    def __init__(self):
        self.events: list[InspectorEvent] = []

    def record(self, event: InspectorEvent) -> InspectorEvent:
        if event.stage not in STAGES:
            raise ValueError(f"Unknown stage: {event.stage}")
        if event.severity not in SEVERITIES:
            raise ValueError(f"Unknown severity: {event.severity}")
        event.detail = redact_text(event.detail)
        self.events.append(event)
        return event

    def query(self, stage: str | None = None, agent_id: str | None = None,
              operation: str | None = None, severity: str | None = None,
              since: float | None = None, until: float | None = None) -> list[InspectorEvent]:
        out = []
        for event in self.events:
            if stage and event.stage != stage:
                continue
            if agent_id and event.agent_id != agent_id:
                continue
            if operation and event.operation != operation:
                continue
            if severity and event.severity != severity:
                continue
            if since is not None and event.created_at < since:
                continue
            if until is not None and event.created_at > until:
                continue
            out.append(event)
        return out

    def timeline_for_artifact(self, artifact_id: str) -> list[InspectorEvent]:
        return [e for e in self.events if e.artifact_id == artifact_id]

    def error_history(self) -> list[InspectorEvent]:
        return [e for e in self.events if e.severity == "error"]

    def summary(self) -> dict:
        by_stage: dict[str, int] = {}
        total_cost = 0.0
        for event in self.events:
            by_stage[event.stage] = by_stage.get(event.stage, 0) + 1
            total_cost += float(event.resource_usage.get("cost_usd", 0) or 0)
        return {"events": len(self.events), "by_stage": by_stage,
                "errors": len(self.error_history()),
                "total_recorded_cost_usd": round(total_cost, 4)}
