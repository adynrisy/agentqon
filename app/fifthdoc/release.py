"""§18 Release readiness — user-project release workflow.

This is the USER's project release checklist, not AgentQon's own release
pipeline (which already does several of these). Deployment is an EXPLICIT
permission-controlled action: a passing checklist never deploys anything
on its own.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

CHECKLIST_TEMPLATE = [
    "version_set", "changelog_written", "tests_passing",
    "dependency_scan_reviewed", "config_validated",
    "environment_settings_checked", "migration_reviewed",
    "rollback_plan_validated", "approval_granted",
]

OPTIONAL_CHECKS = ["deployment_dry_run", "post_deploy_health_check"]


@dataclass
class Release:
    release_id: str
    version: str
    changes: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    approved: bool = False
    deployed: bool = False
    audit_trail: list[dict] = field(default_factory=list)

    def log(self, event: str, detail: str = "") -> None:
        self.audit_trail.append({"at": time.time(), "event": event,
                                 "detail": detail})


def changelog(release: Release) -> str:
    lines = [f"# Release {release.version}", ""]
    lines += [f"- {change}" for change in release.changes] or ["- No changes recorded."]
    return "\n".join(lines)


def checklist_status(release: Release) -> dict:
    missing = [c for c in CHECKLIST_TEMPLATE if not release.checks.get(c)]
    return {"complete": not missing, "missing": missing,
            "checks": dict(release.checks)}


def verify_artifacts(release: Release) -> dict:
    bad = [a["name"] for a in release.artifacts if not a.get("verified")]
    return {"ok": not bad, "unverified": bad,
            "manifest": [{"name": a["name"], "verified": bool(a.get("verified"))}
                         for a in release.artifacts]}


def approve(release: Release, approver: str = "owner") -> dict:
    status = checklist_status(release)
    if not status["complete"]:
        return {"ok": False, "reason": "Checklist incomplete.",
                "missing": status["missing"]}
    release.approved = True
    release.log("approved", f"by {approver}")
    return {"ok": True, "approved": True}


def dry_run_deploy(release: Release) -> dict:
    status = checklist_status(release)
    return {"ok": status["complete"], "missing": status["missing"],
            "would_deploy_version": release.version,
            "note": "Dry run only — nothing was deployed."}


def deploy(release: Release, permission_granted: bool = False) -> dict:
    if not permission_granted:
        return {"ok": False, "reason": "Deployment needs explicit permission."}
    if not release.approved:
        return {"ok": False, "reason": "Release approval gate has not passed."}
    release.deployed = True
    release.log("deployed", f"version {release.version}")
    return {"ok": True, "deployed": True, "version": release.version}


def post_deploy_health(release: Release, checks: dict[str, bool]) -> dict:
    failed = [k for k, v in checks.items() if not v]
    release.log("post_deploy_health", "failed: " + ",".join(failed) if failed else "all ok")
    return {"healthy": not failed, "failed_checks": failed}


def compare_releases(a: Release, b: Release) -> dict:
    return {"versions": (a.version, b.version),
            "changes_added": sorted(set(b.changes) - set(a.changes)),
            "changes_removed": sorted(set(a.changes) - set(b.changes)),
            "checks_changed": {c: (a.checks.get(c), b.checks.get(c))
                               for c in set(a.checks) | set(b.checks)
                               if a.checks.get(c) != b.checks.get(c)}}


def incident_summary(release: Release, what_failed: str) -> dict:
    return {"release": release.version, "deployed": release.deployed,
            "what_failed": what_failed, "audit_trail": release.audit_trail,
            "rollback_plan_validated": bool(release.checks.get("rollback_plan_validated"))}
