"""§19 Personalization — preferences with provenance.

Precedence (lowest to highest): system default < workspace < project <
user override. effective() reports WHERE each value came from, so the user
can see why a preference affected an execution. English-only: there is no
language preference here, by standing decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULTS = {
    "response_detail": "normal",       # brief | normal | detailed
    "output_format": "markdown",
    "project_directory": "",
    "code_style": "default",
    "default_agent_roles": [],
    "model_per_task": {},
    "confirm_by_risk": {"low": False, "medium": True, "high": True},
    "mission_timeout_seconds": 3600,
    "notification_channels": ["in_app"],
    "export_format": "markdown",
    "editor": "",
    "favourite_skills": [],
    "workspace": "",
}

LAYERS = ("system", "workspace", "project", "user")


class Preferences:
    def __init__(self):
        self.layers: dict[str, dict] = {layer: {} for layer in LAYERS}
        self.layers["system"] = dict(DEFAULTS)

    def set(self, layer: str, key: str, value) -> dict:
        if layer not in LAYERS or layer == "system":
            return {"ok": False, "reason": "System defaults are read-only; "
                    "set a workspace, project or user override."}
        if key not in DEFAULTS:
            return {"ok": False, "reason": f"Unknown preference: {key}"}
        self.layers[layer][key] = value
        return {"ok": True, "layer": layer, "key": key}

    def effective(self, key: str | None = None) -> dict:
        keys = [key] if key else list(DEFAULTS)
        out = {}
        for k in keys:
            winner, value = "system", self.layers["system"][k]
            for layer in ("workspace", "project", "user"):
                if k in self.layers[layer]:
                    winner, value = layer, self.layers[layer][k]
            out[k] = {"value": value, "from": winner}
        return out

    def explain(self, key: str) -> str:
        entry = self.effective(key)[key]
        if entry["from"] == "system":
            return (f"'{key}' uses the system default ({entry['value']!r}) — "
                    "no override was set.")
        return (f"'{key}' is {entry['value']!r} because of your "
                f"{entry['from']} override, which beats the system default.")

    def export(self) -> dict:
        return {"workspace": dict(self.layers["workspace"]),
                "project": dict(self.layers["project"]),
                "user": dict(self.layers["user"])}

    def import_(self, data: dict) -> dict:
        applied, rejected = [], []
        for layer in ("workspace", "project", "user"):
            for key, value in (data.get(layer) or {}).items():
                result = self.set(layer, key, value)
                (applied if result["ok"] else rejected).append(f"{layer}.{key}")
        return {"applied": applied, "rejected": rejected}

    def reset(self, layer: str | None = None) -> None:
        if layer is None:
            for name in ("workspace", "project", "user"):
                self.layers[name] = {}
        elif layer in self.layers:
            self.layers[layer] = {}
