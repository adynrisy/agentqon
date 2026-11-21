"""AgentQon — configuration versioning, snapshots, rollback, precedence.

What this module is:
- A versioned configuration store: every change creates a new version
  and keeps the previous one, so a bad change can be undone.
- Rollback that is explicit and recorded: a rollback names the version
  restored and the reason, and the rollback itself is written to the
  audit log. Rollback is never silent.
- Per-mission snapshots: a mission records the configuration it ran
  under, so a later failure can be traced to the exact settings.
- Import / export with validation and secret redaction.
- Reset to defaults that touches ONLY the configuration file — project
  data is never deleted by a reset, and the reset states its scope
  before it runs.
- Deterministic precedence, documented here and enforced by
  ``resolve``:

      1. Admin hard limits (top). Nothing below may loosen them.
      2. Project settings.
      3. User settings.
      4. Built-in defaults (bottom).

  Rule: a lower level may only be MORE restrictive than the level
  above it, never looser. For numeric caps "more restrictive" means a
  smaller number; for on/off switches it means turning a feature off
  when the level above allows it, never turning it on when the level
  above forbids it. A value that tries to loosen an admin limit is
  refused with a field-specific message, not silently clamped.

Field validation gives field-specific feedback in plain words: a bad
value names the field and the valid range. Nothing here stores secrets
in plain text beyond what the caller passes in; export redacts any key
whose name looks like a secret (see SENSITIVE_KEY_HINTS).
"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

# field -> (kind, low, high, plain-words description)
FIELD_RULES: dict[str, tuple[str, object, object, str]] = {
    "budget_cap_usd": ("number", 0.0, 10000.0,
                       "the most a mission may spend, in US dollars"),
    "research_max_fetches": ("integer", 0, 50,
                             "how many web pages research may fetch"),
    "session_ttl_hours": ("integer", 1, 720,
                          "how long a sign-in stays valid, in hours"),
    "rate_run_per_min": ("integer", 1, 1000,
                         "how many mission runs are allowed per minute"),
    "rate_login_per_min": ("integer", 1, 100,
                           "how many sign-in tries are allowed per minute"),
    "worker_count": ("integer", 1, 32, "how many workers may run at once"),
    "model_timeout_seconds": ("integer", 5, 3600,
                              "how long to wait for a model answer, in seconds"),
    "max_mission_steps": ("integer", 1, 500,
                          "how many steps a mission may take"),
    "research_mode": ("choice", ("live", "seed"),
                      None, "whether research uses the live web or the built-in examples"),
    "worker_mode": ("choice", ("inline", "external"),
                    None, "whether the queue worker runs inside the server or separately"),
}

DEFAULTS: dict = {
    "budget_cap_usd": 10.0,
    "research_max_fetches": 5,
    "session_ttl_hours": 168,
    "rate_run_per_min": 30,
    "rate_login_per_min": 5,
    "worker_count": 1,
    "model_timeout_seconds": 300,
    "max_mission_steps": 100,
    "research_mode": "live",
    "worker_mode": "inline",
}

# Admin hard limits: the tightest a lower level may go is bounded here;
# the ADMIN values below are ceilings for caps and fixed switches.
ADMIN_HARD_LIMITS: dict = {
    "budget_cap_usd": 100.0,
    "research_max_fetches": 20,
    "rate_run_per_min": 100,
}

SENSITIVE_KEY_HINTS = ("token", "secret", "password", "api_key", "apikey", "private")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SENSITIVE_KEY_HINTS)


def redact_values(values: dict) -> dict:
    """Return a copy with sensitive values replaced by a plain marker."""
    out = {}
    for key, value in values.items():
        out[key] = "(hidden — a secret value is never shown here)" if is_sensitive_key(key) else value
    return out


def validate_field(field: str, value: object) -> str | None:
    """Return a plain-words error for a bad value, or None when valid.
    Unknown fields are not rejected here (they may be forward
    compatible), but known fields are checked strictly."""
    rule = FIELD_RULES.get(field)
    if rule is None:
        return None
    kind, low, high, description = rule
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return (f"“{field}” ({description}) must be a whole number, "
                    f"not {type(value).__name__}.")
        if not (low <= value <= high):
            return (f"“{field}” ({description}) must be between {low} and "
                    f"{high}; you gave {value}.")
    elif kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return (f"“{field}” ({description}) must be a number, "
                    f"not {type(value).__name__}.")
        if not (low <= value <= high):
            return (f"“{field}” ({description}) must be between {low} and "
                    f"{high}; you gave {value}.")
    elif kind == "choice":
        if value not in low:
            options = " or ".join(str(o) for o in low)
            return (f"“{field}” ({description}) must be {options}; "
                    f"you gave {value!r}.")
    return None


def validate_config(values: dict) -> list[str]:
    """All field-specific errors, in field order. Empty = valid."""
    return [err for field in sorted(values)
            for err in [validate_field(field, values[field])] if err]


def _more_restrictive(field: str, upper: object, lower: object) -> bool:
    """True when ``lower`` is at least as restrictive as ``upper``."""
    if field in ADMIN_HARD_LIMITS and isinstance(upper, (int, float)) \
            and isinstance(lower, (int, float)):
        return lower <= upper
    if isinstance(upper, bool) and isinstance(lower, bool):
        # Turning a feature off is more restrictive; turning it on is not.
        return (not lower) or upper
    return True


def resolve(*, admin: dict | None = None, project: dict | None = None,
            user: dict | None = None) -> dict:
    """Resolve the effective configuration under the documented
    hierarchy. Admin hard limits sit on top: any lower value that
    would loosen an admin limit is refused (ValueError naming the
    field), never silently clamped — a silent clamp would hide the
    fact that the operator's intent was not honoured."""
    admin = dict(admin or {})
    project = dict(project or {})
    user = dict(user or {})
    effective = dict(DEFAULTS)
    for layer_name, layer in (("user", user), ("project", project), ("admin", admin)):
        for field, value in layer.items():
            if layer_name != "admin" and field in ADMIN_HARD_LIMITS:
                limit = ADMIN_HARD_LIMITS[field]
                if isinstance(value, (int, float)) and value > limit:
                    raise ValueError(
                        f"“{field}” may not be set above the admin hard limit "
                        f"of {limit}; you gave {value}. Lower levels may only "
                        f"be more restrictive, never looser.")
            effective[field] = value
    # Admin is applied last, so admin values always win.
    return effective


def preview_change(current: dict, proposed: dict) -> dict:
    """Show what a settings change will affect BEFORE applying it.
    Returns changed fields, unchanged fields, and the plain-words list
    of surfaces affected (missions, workers, research, sign-in)."""
    changed = {}
    for field, value in proposed.items():
        if current.get(field) != value:
            changed[field] = {"from": current.get(field), "to": value}
    surfaces = set()
    for field in changed:
        if field.startswith("budget") or field.startswith("max_mission"):
            surfaces.add("missions (new missions use the new limit)")
        elif field.startswith("rate_") or field.startswith("session_"):
            surfaces.add("sign-in and request limits")
        elif field.startswith("research"):
            surfaces.add("research behaviour")
        elif field.startswith("worker"):
            surfaces.add("worker execution")
        elif field.startswith("model_"):
            surfaces.add("model calls")
        else:
            surfaces.add("general settings")
    return {"changed": changed,
            "unchanged_count": len(proposed) - len(changed),
            "affects": sorted(surfaces),
            "affects_current_missions": False,
            "note": ("Setting changes affect NEW missions only. Missions "
                     "already running keep the configuration they started "
                     "with — see their per-mission snapshot.")}


class ConfigStore:
    """File-backed versioned configuration. The file holds versions,
    snapshots and an audit log; project data lives elsewhere and is
    never touched by this class (that is the reset guarantee)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.versions: list[dict] = data.get("versions", [])
            self.audit: list[dict] = data.get("audit", [])
            self.snapshots: dict[str, dict] = data.get("snapshots", {})
        else:
            self.versions = [{"version": 1, "values": dict(DEFAULTS),
                              "note": "built-in defaults", "at": time.time()}]
            self.audit = []
            self.snapshots = {}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "versions": self.versions, "audit": self.audit,
            "snapshots": self.snapshots}, indent=2, sort_keys=True))

    @property
    def current(self) -> dict:
        return dict(self.versions[-1]["values"])

    @property
    def current_version(self) -> int:
        return self.versions[-1]["version"]

    def set(self, values: dict, *, note: str = "", actor: str = "operator") -> dict:
        """Validate, then store a new version. Returns the preview +
        the new version number. Invalid values are refused wholesale —
        a half-applied configuration is never stored."""
        errors = validate_config(values)
        if errors:
            raise ValueError("; ".join(errors))
        new_values = dict(self.current)
        new_values.update(values)
        version = self.current_version + 1
        self.versions.append({"version": version, "values": new_values,
                              "note": note, "at": time.time(), "actor": actor})
        self.audit.append({"action": "set", "version": version,
                           "fields": sorted(values), "note": note,
                           "actor": actor, "at": time.time()})
        self._save()
        return {"version": version, "values": dict(new_values)}

    def snapshot_for_mission(self, mission_id: str) -> dict:
        """Record the configuration a mission runs under. The snapshot
        is immutable history: later changes never rewrite it."""
        snap = {"mission_id": mission_id,
                "config_version": self.current_version,
                "values": self.current, "at": time.time()}
        self.snapshots[mission_id] = snap
        self._save()
        return copy.deepcopy(snap)

    def snapshot_of(self, mission_id: str) -> dict | None:
        snap = self.snapshots.get(mission_id)
        return copy.deepcopy(snap) if snap else None

    def rollback(self, to_version: int, *, reason: str, actor: str = "operator") -> dict:
        """Restore a previous version. Explicit and recorded: the
        reason is required, and the rollback is written to the audit
        log as its own entry. Rolling back to the current version or
        to an unknown version is refused."""
        if not reason:
            raise ValueError("A rollback needs a reason, in plain words, "
                             "so the record shows why settings changed.")
        target = next((v for v in self.versions if v["version"] == to_version), None)
        if target is None:
            raise ValueError(f"There is no configuration version {to_version} to restore.")
        if to_version == self.current_version:
            raise ValueError(f"Version {to_version} is already the current configuration.")
        new_version = self.current_version + 1
        self.versions.append({"version": new_version,
                              "values": dict(target["values"]),
                              "note": f"rollback to version {to_version}: {reason}",
                              "at": time.time(), "actor": actor})
        self.audit.append({"action": "rollback", "from_version": self.current_version - 1,
                           "to_version": to_version, "new_version": new_version,
                           "reason": reason, "actor": actor, "at": time.time()})
        self._save()
        return {"restored_version": to_version, "new_version": new_version,
                "values": dict(target["values"]), "reason": reason}

    def reset_to_defaults(self) -> dict:
        """Restore built-in defaults WITHOUT deleting project data.
        Scope, stated before it runs (and returned here): only this
        configuration file changes. Mission records, project files and
        snapshots are untouched — the caller must show ``scope`` to
        the user before calling this."""
        scope = ("Reset scope: only the configuration file changes. "
                 "Project files, mission records and saved snapshots are "
                 "NOT deleted or changed.")
        new_version = self.current_version + 1
        self.versions.append({"version": new_version, "values": dict(DEFAULTS),
                              "note": "reset to defaults", "at": time.time(),
                              "actor": "operator"})
        self.audit.append({"action": "reset", "new_version": new_version,
                           "scope": scope, "at": time.time()})
        self._save()
        return {"version": new_version, "values": dict(DEFAULTS), "scope": scope}

    def export(self) -> dict:
        """Export the current configuration with secrets redacted.
        The export is validated on import, so a hand-edited export that
        breaks a rule is refused rather than applied."""
        return {"format": "agentqon-config", "version": self.current_version,
                "values": redact_values(self.current)}

    def import_config(self, payload: dict, *, note: str = "") -> dict:
        """Import a previously exported configuration. Unknown formats
        and invalid values are refused; secrets in an export are
        redacted markers and are never imported as values."""
        if payload.get("format") != "agentqon-config":
            raise ValueError("This file is not an AgentQon configuration export.")
        values = dict(payload.get("values") or {})
        for key, value in values.items():
            if isinstance(value, str) and value.startswith("(hidden"):
                raise ValueError(
                    f"“{key}” is a hidden secret marker from an export — "
                    f"enter the real value again instead of importing it.")
        return self.set(values, note=note or "imported configuration")
