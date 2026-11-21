"""Tenth audit document, Group 5 — rights, privacy and governance
(stdlib only). Generalizes agent/skill attribution into a ledger for any
incorporated material, and enforces residency, consent expiry and release
gates where the infrastructure actually permits enforcement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


# ------------------------------------- 25: Content Rights and Attribution Ledger
@dataclass
class RightsEntry:
    entry_id: str
    source: str
    kind: str  # "code" | "media" | "document" | "third_party"
    license: str
    incorporated_into: str
    attribution_text: str = ""


class RightsLedger:
    """Track source and licensing for incorporated material, and produce
    attribution records where they apply."""

    def __init__(self):
        self._entries: dict[str, RightsEntry] = {}

    def record(self, entry: RightsEntry) -> None:
        self._entries[entry.entry_id] = entry

    def attribution_record(self) -> list[dict]:
        out = []
        for entry in self._entries.values():
            out.append({
                "source": entry.source, "kind": entry.kind,
                "license": entry.license,
                "incorporated_into": entry.incorporated_into,
                "attribution": entry.attribution_text
                or f"Includes material from {entry.source} under {entry.license}.",
            })
        return out

    def unlicensed(self) -> list[str]:
        return [e.entry_id for e in self._entries.values()
                if e.license.strip().lower() in ("", "unknown", "none")]


# ---------------------------------------------- 26: Data Residency Enforcement
class ResidencyEnforcer:
    """Restrict project data to approved storage regions and processing
    locations where enforcement is possible. In local-first mode, local
    storage is always allowed and remote locations must be approved."""

    def __init__(self, *, approved_regions: list[str], local_region: str = "local"):
        self._approved = set(approved_regions)
        self._local = local_region

    def check(self, *, storage_region: str, processing_location: str) -> dict:
        problems = []
        for label, place in (("storage", storage_region),
                             ("processing", processing_location)):
            if place != self._local and place not in self._approved:
                problems.append(f"{label} in unapproved location: {place}")
        if problems:
            return {"ok": False, "problems": problems,
                    "note": "This data would land somewhere not approved for it."}
        return {"ok": True, "problems": [],
                "note": "Storage and processing locations are approved."}


# ------------------------------------------- 27: Consent and Authorization Expiry
class ConsentRegistry:
    """Time-limited permissions and consents expire on their own, including
    permission to use particular data for a particular purpose."""

    def __init__(self, *, now_fn=None):
        self._now = now_fn or time.time
        self._consents: dict[str, dict] = {}

    def grant(self, consent_id: str, *, subject: str, data: str, purpose: str,
              expires_at: float) -> None:
        self._consents[consent_id] = {"subject": subject, "data": data,
                                      "purpose": purpose, "expires_at": expires_at,
                                      "revoked": False}

    def revoke(self, consent_id: str) -> bool:
        if consent_id not in self._consents:
            return False
        self._consents[consent_id]["revoked"] = True
        return True

    def check(self, consent_id: str, *, data: str, purpose: str) -> dict:
        rec = self._consents.get(consent_id)
        if rec is None:
            return {"ok": False, "note": "No such consent exists."}
        if rec["revoked"]:
            return {"ok": False, "note": "This consent was withdrawn."}
        if self._now() > rec["expires_at"]:
            return {"ok": False, "note": "This consent has expired."}
        if rec["data"] != data or rec["purpose"] != purpose:
            return {"ok": False,
                    "note": "This consent does not cover that data or purpose."}
        return {"ok": True, "note": "Consent is current for this data and purpose."}

    def sweep_expired(self) -> list[str]:
        now = self._now()
        expired = [cid for cid, r in self._consents.items()
                   if not r["revoked"] and now > r["expires_at"]]
        for cid in expired:
            self._consents[cid]["revoked"] = True
        return expired


# ----------------------------------------- 28: Sensitive Output Release Gate
class ReleaseGate:
    """Inspect an exported or published deliverable BEFORE release for
    restricted information, prohibited disclosures, and required warnings."""

    def __init__(self, *, restricted_markers=(), required_warnings=()):
        self._markers = list(restricted_markers)
        self._warnings = list(required_warnings)

    def inspect(self, deliverable_text: str) -> dict:
        found = [m for m in self._markers if m in deliverable_text]
        missing_warnings = [w for w in self._warnings if w not in deliverable_text]
        if found or missing_warnings:
            reasons = []
            if found:
                reasons.append(f"restricted information found: {', '.join(found)}")
            if missing_warnings:
                reasons.append("required warnings missing: "
                               + ", ".join(missing_warnings))
            return {"ok": False, "reasons": reasons,
                    "note": "This deliverable must not be released as it stands."}
        return {"ok": True, "reasons": [],
                "note": "No restricted information found and all warnings present."}


# ------------------------------- 29: Cross-Boundary Data Transformation Audit
class BoundaryAudit:
    """Record what filtering, redaction, summarization or conversion happened
    before data crossed a project, agent, or provider boundary."""

    def __init__(self):
        self._records: list[dict] = []

    def record_crossing(self, *, data_id: str, from_boundary: str, to_boundary: str,
                        transforms: list[str]) -> None:
        self._records.append({"data_id": data_id, "from": from_boundary,
                              "to": to_boundary, "transforms": list(transforms)})

    def audit_for(self, data_id: str) -> list[dict]:
        return [r for r in self._records if r["data_id"] == data_id]

    def untransformed_crossings(self) -> list[dict]:
        return [r for r in self._records if not r["transforms"]]


# -------------------------------------- 30: Privacy-Preserving Aggregation
def aggregate_reports(records: list[dict]) -> dict:
    """Build usage/quality reports from minimized, aggregated information.

    Individual prompts and raw user content are dropped, never carried into
    the report: only counts and averages grouped by category survive.
    Analytics in this system is built under this rule.
    """
    groups: dict[str, dict] = {}
    for rec in records:
        key = rec.get("category", "uncategorized")
        bucket = groups.setdefault(key, {"count": 0, "quality_sum": 0.0,
                                         "quality_n": 0})
        bucket["count"] += 1
        if rec.get("quality_score") is not None:
            bucket["quality_sum"] += float(rec["quality_score"])
            bucket["quality_n"] += 1
    out = {}
    for key, bucket in groups.items():
        out[key] = {
            "count": bucket["count"],
            "average_quality": (bucket["quality_sum"] / bucket["quality_n"]
                                if bucket["quality_n"] else None),
        }
    return {"groups": out,
            "note": "Report built from counts and averages only; "
                    "no prompts or raw content were kept."}
