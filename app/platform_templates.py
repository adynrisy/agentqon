"""AgentQon — workflow templates.

A template saves a successful mission PATTERN so common tasks execute
consistently: the plan shape (ordered steps), the agent roles involved,
and the verification steps that decide whether the result is good
enough. A template deliberately does NOT store a previous run's
outputs — instantiating a template starts fresh work; it never replays
an old answer as if it were new.

Validation is strict at save time: a template with no steps, no roles
or no verification steps is refused, because a pattern that cannot
say how it will be checked is not a pattern worth reusing.
"""
from __future__ import annotations

import time


class TemplateError(ValueError):
    pass


def validate_template(data: dict) -> list[str]:
    errors = []
    if not data.get("name"):
        errors.append("A template needs a name, so it can be found again.")
    steps = data.get("plan_shape") or []
    if not steps:
        errors.append("A template needs at least one step in its plan shape.")
    if any(not isinstance(s, str) or not s.strip() for s in steps):
        errors.append("Every plan step must be a non-empty description.")
    if not (data.get("agent_roles") or []):
        errors.append("A template needs at least one agent role.")
    if not (data.get("verification_steps") or []):
        errors.append("A template needs at least one verification step — "
                      "a pattern without a check is not reusable.")
    if "outputs" in data or "previous_result" in data:
        errors.append("A template must not store a previous run's outputs — "
                      "it saves the pattern, not the answer.")
    return errors


class TemplateStore:
    def __init__(self) -> None:
        self._templates: dict[str, dict] = {}

    def save(self, template_id: str, data: dict, *,
             source_mission_id: str = "") -> dict:
        errors = validate_template(data)
        if errors:
            raise TemplateError("; ".join(errors))
        record = {
            "template_id": template_id,
            "name": data["name"],
            "plan_shape": list(data["plan_shape"]),
            "agent_roles": list(data["agent_roles"]),
            "verification_steps": list(data["verification_steps"]),
            "notes": data.get("notes", ""),
            "source_mission_id": source_mission_id,
            "created_at": time.time(),
            "uses": 0,
        }
        self._templates[template_id] = record
        return dict(record)

    def get(self, template_id: str) -> dict | None:
        record = self._templates.get(template_id)
        return dict(record) if record else None

    def list(self) -> list[dict]:
        return [dict(t) for t in self._templates.values()]

    def instantiate(self, template_id: str, *, title: str) -> dict:
        """Start fresh work from a template. The draft carries the
        plan shape, roles and checks — and explicitly NO prior outputs,
        so the new mission cannot be mistaken for a replay."""
        record = self._templates.get(template_id)
        if record is None:
            raise TemplateError(f"There is no template called {template_id!r}.")
        record["uses"] += 1
        return {
            "title": title,
            "template_id": template_id,
            "plan_shape": list(record["plan_shape"]),
            "agent_roles": list(record["agent_roles"]),
            "verification_steps": list(record["verification_steps"]),
            "prior_outputs": None,
            "note": ("Started from a saved pattern. This is fresh work — "
                     "no previous answer is reused."),
        }
