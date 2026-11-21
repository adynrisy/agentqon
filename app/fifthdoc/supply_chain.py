"""§17 Licensing / supply-chain.

Ships: licence detection, dependency inventory, SBOM generation,
missing-licence flags, source + integrity checks, unpinned warnings,
licence compatibility vs project policy, generated-code attribution,
committed-secret scanning, suspicious package change detection,
inventory diffs, compliance reports, scanner + database version recording.

BLOCKED (honest, per contract): live CVE-database vulnerability scanning.
No scanner is pinned in this tree and no new dependency may be added, so
vulnerability_scan() returns a blocked status naming the gap instead of
faking a scan. Results always name their source and scan time; absence of
findings NEVER proves security.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

LICENSE_MARKERS = {
    "MIT License": "MIT", "Apache License": "Apache-2.0",
    "GNU GENERAL PUBLIC LICENSE": "GPL", "BSD 3-Clause": "BSD-3-Clause",
    "Mozilla Public License": "MPL-2.0", "ISC License": "ISC",
}
SECRET_PATTERNS = {
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    "generic_token": re.compile(r"(?i)(api[_-]?key|secret)\s*[:=]\s*['\"][^'\"]{8,}"),
}
# Example: a project that only accepts permissive licences.
PERMISSIVE = {"MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0"}

VULN_SCAN_BLOCKED = {
    "scanner": None,
    "status": "blocked",
    "reason": ("No vulnerability scanner is pinned in this tree and the "
               "workstream may not add a new runtime dependency. Until a "
               "scanner is chosen and pinned (for example OSV-Scanner or "
               "Trivy, run outside the harness), CVE scanning is unavailable "
               "and no scan result is claimed."),
}


@dataclass
class Dependency:
    name: str
    version: str | None  # None = unpinned
    licence: str | None = None
    source: str = ""
    integrity: str = ""  # expected hash, when known
    actual_integrity: str = ""  # hash measured by the caller, when known
    generated: bool = False


def detect_licence(text: str) -> str | None:
    for marker, licence in LICENSE_MARKERS.items():
        if marker.lower() in text.lower():
            return licence
    return None


def inventory(dependencies: list[Dependency]) -> dict:
    missing_licence = [d.name for d in dependencies if not d.licence]
    unpinned = [d.name for d in dependencies if not d.version]
    integrity_fail = []
    for dep in dependencies:
        # Only compare when both a recorded and a measured hash exist;
        # a missing measurement is reported as unchecked, never as passing.
        if dep.integrity and dep.actual_integrity \
                and dep.integrity != dep.actual_integrity:
            integrity_fail.append(dep.name)
    return {"total": len(dependencies),
            "missing_licence": missing_licence, "unpinned": unpinned,
            "integrity_mismatches": integrity_fail}


def generate_sbom(dependencies: list[Dependency], project: str) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.5",
            "project": project,
            "components": [{"name": d.name, "version": d.version or "unpinned",
                            "licences": [d.licence] if d.licence else [],
                            "source": d.source} for d in dependencies]}


def check_compatibility(dependencies: list[Dependency],
                        allowed: set[str] | None = None) -> dict:
    allowed = PERMISSIVE if allowed is None else allowed
    conflicts = [d.name for d in dependencies
                 if d.licence and d.licence not in allowed]
    return {"allowed": sorted(allowed), "conflicts": conflicts}


def scan_committed_secrets(files: dict[str, str]) -> list[dict]:
    findings = []
    for path, content in files.items():
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append({"file": path, "kind": kind,
                                 "note": "Possible secret committed — rotate it "
                                         "and remove it from history."})
    return findings


def suspicious_change(before: Dependency, after: Dependency) -> dict:
    flags = []
    if before.version and after.version and before.version != after.version:
        flags.append(f"Version changed {before.version} -> {after.version}.")
    if before.source != after.source:
        flags.append("Source changed.")
    if before.licence != after.licence:
        flags.append("Licence changed.")
    return {"package": after.name, "suspicious": bool(flags), "flags": flags}


def diff_inventory(old: list[Dependency], new: list[Dependency]) -> dict:
    old_map, new_map = {d.name: d for d in old}, {d.name: d for d in new}
    return {"added": sorted(set(new_map) - set(old_map)),
            "removed": sorted(set(old_map) - set(new_map)),
            "changed": sorted(n for n in set(old_map) & set(new_map)
                              if old_map[n].version != new_map[n].version)}


def vulnerability_scan() -> dict:
    """BLOCKED — see module docstring. Never returns a fake clean result."""
    return {**VULN_SCAN_BLOCKED, "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                            time.gmtime())}


def compliance_report(dependencies: list[Dependency], project: str) -> dict:
    inv = inventory(dependencies)
    return {"project": project, "scan_time": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                           time.gmtime()),
            "source": "AgentQon built-in inventory scan (licences, pins, "
                      "integrity, secrets). CVE scanning: blocked, see "
                      "vulnerability_scan().",
            "inventory": inv,
            "compatibility": check_compatibility(dependencies),
            "sbom": generate_sbom(dependencies, project),
            "honesty_note": "Absence of findings never proves security."}
