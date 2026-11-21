"""AgentQon — disaster recovery: exportable state backup + restore.

What "the harness state that matters" means here, stated plainly so a
restore can be judged:
- configuration versions + snapshots + audit log (platform_config)
- workflow templates (platform_templates)
- model evaluation history (platform_history)
- plugin registry entries (platform_plugins)
- experiment summaries (platform_benchmarks)

What is NOT in a backup: provider secrets (never exported — the user
reconnects providers after a restore, and the restore report says so),
live mission queue rows (a restore never resurrects a half-run mission
as if it were still running), and other tenants' data (a backup is
scoped to one tenant).

Integrity: every bundle carries a checksum over its sections. A bundle
with a bad checksum, an unknown format or a newer format version than
this code understands is refused — a corrupt restore is worse than no
restore, because it looks like recovery while it is not.
"""
from __future__ import annotations

import hashlib
import json
import time

FORMAT = "agentqon-state-backup"
FORMAT_VERSION = 1
BACKUP_SECTIONS = ("config", "templates", "model_history", "plugins", "experiments")


def _checksum(sections: dict) -> str:
    blob = json.dumps(sections, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def export_state(*, tenant_id: str, sections: dict) -> dict:
    """Build a backup bundle. Unknown sections are refused (a backup
    that silently drops a section is a lie about what was saved)."""
    unknown = sorted(set(sections) - set(BACKUP_SECTIONS))
    if unknown:
        raise ValueError(f"Unknown backup sections refused: {', '.join(unknown)}.")
    picked = {name: sections[name] for name in BACKUP_SECTIONS if name in sections}
    return {"format": FORMAT, "format_version": FORMAT_VERSION,
            "tenant_id": tenant_id, "exported_at": time.time(),
            "sections": picked, "checksum": _checksum(picked),
            "excluded": ("Provider secrets are never exported — reconnect "
                         "providers after a restore. Live queue rows are "
                         "not restored — a half-run mission is not resumed "
                         "as if it were still running.")}


def validate_bundle(bundle: dict) -> list[str]:
    errors = []
    if bundle.get("format") != FORMAT:
        errors.append("This file is not an AgentQon state backup.")
    if bundle.get("format_version", 0) > FORMAT_VERSION:
        errors.append("This backup was made by a newer version and cannot "
                      "be restored by this version.")
    sections = bundle.get("sections")
    if not isinstance(sections, dict):
        errors.append("The backup has no sections to restore.")
        return errors
    unknown = sorted(set(sections) - set(BACKUP_SECTIONS))
    if unknown:
        errors.append(f"Unknown backup sections refused: {', '.join(unknown)}.")
    if bundle.get("checksum") != _checksum(sections):
        errors.append("The backup checksum does not match — the file may be "
                      "damaged or altered. Restore refused.")
    return errors


def restore_preview(bundle: dict) -> dict:
    """Show what a restore WOULD do, before doing it. A restore is
    explicit: the caller shows this preview, then calls restore()."""
    errors = validate_bundle(bundle)
    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True,
            "tenant_id": bundle.get("tenant_id"),
            "sections": sorted(bundle["sections"]),
            "exported_at": bundle.get("exported_at"),
            "note": ("Restoring replaces the current configuration, "
                     "templates, model history, plugins and experiments "
                     "for this tenant with the backup's copies. Provider "
                     "secrets are not restored — reconnect them afterwards.")}


def restore_state(bundle: dict, target: dict) -> dict:
    """Apply a validated bundle into ``target`` (a plain dict acting
    as the tenant state holder). Returns a report of what was restored.
    Refuses invalid bundles outright; never partially restores."""
    errors = validate_bundle(bundle)
    if errors:
        raise ValueError("; ".join(errors))
    restored = []
    for name in BACKUP_SECTIONS:
        if name in bundle["sections"]:
            target[name] = bundle["sections"][name]
            restored.append(name)
    return {"restored": restored, "tenant_id": bundle.get("tenant_id"),
            "note": ("Restore complete. Reconnect providers: secrets were "
                     "never part of the backup.")}
