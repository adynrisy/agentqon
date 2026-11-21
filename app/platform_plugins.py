"""AgentQon — plugin framework: permissioned, versioned extensions.

An extension installs WITHOUT modifying core code. It declares exactly
what it needs, and the framework enforces three rules:

1. PERMISSIONED — an extension may only use the tools and permissions
   it declared at install time. An undeclared use is refused.
2. VERSIONED — every extension carries a version; installing a new
   version replaces the old record, and the old version stays on the
   record as history (upgrades never silently rewrite what ran before).
3. ISOLATED — an extension can never override a core security rule,
   read another tenant's data, touch the credential vault, or change
   configuration limits. Those permissions are DENIED at install time
   even if declared — asking for them refuses the whole install, so a
   malicious manifest cannot smuggle one dangerous permission past a
   pile of harmless ones.

Extensions run in the host process but behind this permission check;
true process isolation is NOT claimed here (see the module note in the
docs: an extension is code the operator chose to trust, permissioned
so its reach is bounded and auditable).
"""
from __future__ import annotations

import re
import time

KNOWN_PERMISSIONS = {
    "read_missions", "create_missions", "read_usage", "use_tools",
    "read_library", "run_benchmarks", "read_model_history",
}

# Permissions that can never be granted, whatever a manifest declares.
CORE_DENIED_PERMISSIONS = {
    "modify_core", "override_security", "read_other_tenants",
    "read_vault", "write_config_limits", "modify_harness_files",
}

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class PluginError(ValueError):
    pass


def validate_manifest(manifest: dict) -> list[str]:
    errors = []
    if not manifest.get("name"):
        errors.append("An extension needs a name.")
    version = manifest.get("version", "")
    if not _VERSION_RE.match(str(version)):
        errors.append(f"Version {version!r} is not a three-part version "
                      "(for example 1.2.3).")
    declared = manifest.get("permissions") or []
    if not isinstance(declared, list) or any(not isinstance(p, str) for p in declared):
        errors.append("Permissions must be a list of permission names.")
        return errors
    denied = sorted(set(declared) & CORE_DENIED_PERMISSIONS)
    if denied:
        errors.append("These permissions can never be granted because they "
                      f"would break core security rules: {', '.join(denied)}.")
    unknown = sorted(set(declared) - KNOWN_PERMISSIONS - CORE_DENIED_PERMISSIONS)
    if unknown:
        errors.append(f"Unknown permissions refused: {', '.join(unknown)}.")
    tools = manifest.get("tools") or []
    if any(not isinstance(t, str) for t in tools):
        errors.append("Declared tools must be a list of tool names.")
    return errors


class PluginRegistry:
    def __init__(self) -> None:
        self._installed: dict[str, dict] = {}
        self._history: list[dict] = []

    def install(self, manifest: dict, *, tenant_id: str) -> dict:
        errors = validate_manifest(manifest)
        if errors:
            raise PluginError("; ".join(errors))
        name = manifest["name"]
        previous = self._installed.get(name)
        record = {"name": name, "version": manifest["version"],
                  "permissions": sorted(manifest.get("permissions") or []),
                  "tools": sorted(manifest.get("tools") or []),
                  "tenant_id": tenant_id,
                  "installed_at": time.time(),
                  "previous_version": previous["version"] if previous else None}
        self._installed[name] = record
        self._history.append({"action": "install", **record})
        return dict(record)

    def uninstall(self, name: str, *, tenant_id: str) -> bool:
        record = self._installed.get(name)
        if record is None or record["tenant_id"] != tenant_id:
            return False
        del self._installed[name]
        self._history.append({"action": "uninstall", "name": name,
                              "tenant_id": tenant_id, "at": time.time()})
        return True

    def list(self, *, tenant_id: str) -> list[dict]:
        return [dict(r) for r in self._installed.values()
                if r["tenant_id"] == tenant_id]

    def check(self, *, name: str, tenant_id: str, permission: str,
              tool: str | None = None) -> dict:
        """The enforcement point: every extension action goes through
        here. Returns allowed/denied with a plain-words reason. A
        denied check is never an exception — the caller shows the
        reason to the user and continues without that extension."""
        record = self._installed.get(name)
        if record is None:
            return {"allowed": False,
                    "reason": f"Extension {name!r} is not installed."}
        if record["tenant_id"] != tenant_id:
            return {"allowed": False,
                    "reason": ("This extension belongs to another tenant — "
                               "extensions never reach other tenants' data.")}
        if permission in CORE_DENIED_PERMISSIONS:
            return {"allowed": False,
                    "reason": ("That permission would override a core "
                               "security rule and can never be granted.")}
        if permission not in record["permissions"]:
            return {"allowed": False,
                    "reason": (f"Extension {name!r} did not declare the "
                               f"permission {permission!r} when it was installed.")}
        if tool is not None and tool not in record["tools"]:
            return {"allowed": False,
                    "reason": (f"Extension {name!r} did not declare the tool "
                               f"{tool!r} when it was installed.")}
        return {"allowed": True, "reason": "Declared permission."}
