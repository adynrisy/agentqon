"""§20 Extension toolkit — builds on the W7 plugin framework.

Seam: W7 owns plugin loading, discovery and the runtime host. This module
owns the AUTHORING side — manifest schema, typed interfaces, permission
declaration validation, compatibility declarations, package validation,
signing/integrity checks where supported, docs generation, health checks
and lifecycle hooks. Until W7 is merged, install/remove run against a
local registry record (NOT_TESTED end-to-end through the W7 host).

API boundary rule: extensions NEVER receive unrestricted application
access by default. A manifest that declares no permissions gets none;
a manifest that asks for more than the host allows is rejected.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

ALLOWED_PERMISSIONS = {"artifact.read", "artifact.write", "web.read",
                       "tool.run", "event.subscribe", "config.read"}
HOST_MAX_PERMISSIONS = set(ALLOWED_PERMISSIONS)
MANIFEST_REQUIRED = ("name", "version", "entrypoint", "permissions",
                     "api_version")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Extension:
    name: str
    version: str
    entrypoint: str
    permissions: list[str] = field(default_factory=list)
    api_version: str = "1"
    description: str = ""
    config_schema: dict = field(default_factory=dict)
    signature: str = ""
    deprecated: bool = False
    deprecation_note: str = ""
    installed: bool = False
    health: str = "unknown"


def validate_manifest(data: dict) -> dict:
    problems = []
    for key in MANIFEST_REQUIRED:
        if key not in data:
            problems.append(f"Manifest is missing: {key}")
    if data.get("version") and not VERSION_RE.match(str(data["version"])):
        problems.append("Version must look like 1.2.3.")
    for perm in data.get("permissions", []):
        if perm not in ALLOWED_PERMISSIONS:
            problems.append(f"Permission not allowed by default: {perm}")
        if perm not in HOST_MAX_PERMISSIONS:
            problems.append(f"Permission exceeds what the host allows: {perm}")
    if data.get("entrypoint", "").startswith("/"):
        problems.append("Entrypoint must be relative to the extension package.")
    return {"ok": not problems, "problems": problems}


def check_api_compatibility(manifest_api: str, host_api: str) -> dict:
    major_m, major_h = manifest_api.split(".")[0], host_api.split(".")[0]
    compatible = major_m == major_h
    return {"compatible": compatible,
            "note": ("" if compatible else
                     "Major API version differs — this extension may not run "
                     "on this host version.")}


def sign_package(files: dict[str, str]) -> str:
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return f"sha256:{digest}"


def verify_package(files: dict[str, str], signature: str) -> bool:
    return sign_package(files) == signature


def validate_package(files: dict[str, str]) -> dict:
    problems = []
    if "manifest.json" not in files:
        problems.append("Package has no manifest.json.")
    else:
        try:
            manifest = json.loads(files["manifest.json"])
        except ValueError as exc:
            return {"ok": False, "problems": [f"Manifest is not valid JSON: {exc}"]}
        result = validate_manifest(manifest)
        problems.extend(result["problems"])
    return {"ok": not problems, "problems": problems}


def generate_docs(ext: Extension) -> str:
    lines = [f"# {ext.name} {ext.version}", "",
             ext.description or "No description given.", "",
             "## Permissions requested"]
    lines += [f"- {p}" for p in ext.permissions] or ["- none"]
    if ext.config_schema:
        lines += ["", "## Configuration",
                  "```json", json.dumps(ext.config_schema, indent=2), "```"]
    if ext.deprecated:
        lines += ["", f"> Deprecated: {ext.deprecation_note}"]
    return "\n".join(lines)


class ExtensionRegistry:
    """Local authoring registry; the W7 host owns real loading."""

    def __init__(self):
        self.extensions: dict[str, Extension] = {}

    def install(self, ext: Extension, on_install=None) -> dict:
        result = validate_manifest({"name": ext.name, "version": ext.version,
                                    "entrypoint": ext.entrypoint,
                                    "permissions": ext.permissions,
                                    "api_version": ext.api_version})
        if not result["ok"]:
            return {"ok": False, "problems": result["problems"]}
        ext.installed = True
        self.extensions[ext.name] = ext
        if on_install:
            on_install(ext)
        return {"ok": True, "installed": ext.name,
                "permissions_granted": list(ext.permissions),
                "note": "Only the declared permissions were granted."}

    def remove(self, name: str, on_remove=None) -> dict:
        ext = self.extensions.pop(name, None)
        if ext is None:
            return {"ok": False, "reason": "No such extension."}
        if on_remove:
            on_remove(ext)
        return {"ok": True, "removed": name}

    def health_check(self, name: str) -> dict:
        ext = self.extensions.get(name)
        if ext is None:
            return {"ok": False, "reason": "No such extension."}
        ext.health = "ok" if ext.installed else "not_installed"
        if ext.deprecated:
            ext.health = "deprecated"
        return {"ok": True, "name": name, "health": ext.health,
                "deprecation": ext.deprecation_note if ext.deprecated else ""}

    def mock_tool_provider(self, tool_name: str, canned_result: dict) -> dict:
        """Test fixture: a fake tool provider extensions can test against."""
        return {"tool": tool_name, "mock": True, "result": canned_result}
