"""AgentQon — agency-agents roster importer.

Installs the msitarzewski/agency-agents roster (MIT) into the agent
registry: every roster file becomes a CapabilityManifest, ACTIVE on
arrival (the operator's standing order, v13 — the whole roster joins
the suitability pool; quarantine is the bench), and its persona brief
is stored once in the global persona
store. Persona agents run through the LLM research adapter with the
brief as their system prompt; they carry declared quality priors
(unmeasured — the registry says so) below the curated core agents, so
activating them makes them eligible without silently displacing the
core until an operator promotes them and/or quarantines the core.

Capability mapping is a curation decision, recorded here: analytical /
advisory divisions also receive "research.web" (they can serve as
mission researchers once active); every roster agent receives
"persona.task". Builder/reviewer divisions (engineering, security,
testing, design...) are installed and activatable as personas but are
not dressed up as web researchers.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROSTER_VERSION = "1.0.0"
RESEARCH_DIVISIONS = {
    "academic", "finance", "gis", "healthcare", "marketing", "paid-media",
    "product", "project-management", "research", "sales", "specialized",
    "strategy", "support",
}
_SKIP_DIRS = {".git", ".github", "examples", "integrations", "scripts"}


def roster_dir() -> Path:
    env = os.environ.get("OMNIMIND_AGENCY_ROSTER")
    if env and Path(env).is_dir():
        return Path(env)
    bundled = Path(__file__).parent / "data" / "agency-agents"
    if bundled.is_dir():
        return bundled
    return Path.home() / "workspace" / "agency-agents"


def parse_agent_file(path: Path) -> dict | None:
    """Tolerant frontmatter parse (flat key: value subset of YAML)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    front, body = text[3:end], text[end + 4:].lstrip()
    meta: dict[str, str] = {}
    for line in front.splitlines():
        m = re.match(r"^([a-zA-Z_-]+):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if not meta.get("name"):
        return None
    return {"meta": meta, "body": body}


def iter_roster(root: Path):
    for division_dir in sorted(root.iterdir()):
        if not division_dir.is_dir() or division_dir.name in _SKIP_DIRS:
            continue
        for file in sorted(division_dir.rglob("*.md")):
            yield division_dir.name, file


def import_roster(store, tenant_id: str, root: Path | None = None) -> dict:
    root = root or roster_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"agency roster not found at {root}")
    imported = updated = skipped = 0
    seen: set[str] = set()
    existing = {a["agent_id"]: a for a in store.list_agents(tenant_id)}
    for division, file in iter_roster(root):
        parsed = parse_agent_file(file)
        if parsed is None:
            skipped += 1
            continue
        meta, body = parsed["meta"], parsed["body"]
        slug = file.stem
        legacy_id = f"agency.{division}.{slug}"
        # Renamed roster: the path-derived legacy id maps to the
        # agent's clean AgentQon id (fixture trees keep legacy).
        from .display_names import new_agent_id
        agent_id = new_agent_id(legacy_id) or legacy_id
        if agent_id in seen:
            skipped += 1
            continue
        seen.add(agent_id)
        capabilities = ["persona.task"]
        if division in RESEARCH_DIVISIONS:
            capabilities.append("research.web")
        manifest = {
            "agent_id": agent_id, "version": ROSTER_VERSION,
            "owner": "agency-agents (msitarzewski), MIT",
            "description": (meta.get("description") or meta["name"])[:400],
            "capabilities": capabilities,
            "permissions_requested": ["web.read", "artifact.write"]
            if "research.web" in capabilities else ["artifact.read"],
            "strategy": "persona", "lifecycle": "active",
            "quality": 3.8, "reliability": 0.90, "latency_ms": 950,
            "cost_per_run_usd": 0.30,
            "origin": "agency-agents", "persona_ref": agent_id,
        }
        existed = store.get_persona(agent_id) is not None
        if agent_id in existing:
            # Re-import refreshes the brief, never the operator's
            # lifecycle decision.
            manifest["lifecycle"] = existing[agent_id].get("lifecycle", "active")
        store.put_persona(agent_id, body)
        store.upsert_agent(tenant_id, manifest)
        if existed:
            updated += 1
        else:
            imported += 1
    return {"imported": imported, "updated": updated, "skipped": skipped,
            "total": imported + updated, "roster": str(root)}
