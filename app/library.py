"""AgentQon — subagents-library roster importer (v14).

Installs the collected subagents mega-library (~/workspace/
subagents-library — 9,198 agent definition files harvested from 65
public collections) into the agent registry as a SECOND roster next
to agency-agents: every parsed persona becomes a CapabilityManifest,
ACTIVE on arrival (the operator's standing order, v13 — the whole
roster joins the suitability pool; quarantine is the bench), with its
persona brief stored once in the global persona store.

Unlike agency.py's single-format roster, the library is heterogeneous,
so this module carries one tolerant parser per source format:

- Claude Code .md      — YAML frontmatter (name/description/tools/
                         model); body is the brief.
- Codex .toml          — name/description/developer_instructions.
- OpenCode .md         — frontmatter has description+mode but NO name;
                         the name comes from the filename.
- OpenClaw persona dir — SOUL.md is the persona; IDENTITY.md /
                         AGENTS.md / USER.md / agent.md in the SAME
                         directory are companions of that one persona
                         (appended under clear headings), and a
                         metadata.json there only lends its
                         name/one-liner. A persona directory counts
                         ONCE, not 3–4 times.
- Frontmatter-less .md — persona markdown (noumenon style): name from
                         the first heading, else the filename.
- Kiro .json           — name/description/prompt.

Files named SKILL.md are skills, not subagents, and are never
imported (the library extraction excluded them for the same reason).

Dedupe: byte-identical persona bodies (sha256) are imported once —
the same brief re-shipped by ten collections is one agent; the first
source in sorted order wins and keeps the provenance.

Declared priors: quality 3.5 / reliability 0.85 are UNMEASURED
declared priors, deliberately set below the agency roster's 3.8/0.90:
nobody has run these personas through AgentQon's evaluator yet, and
the registry must not pretend otherwise. Measured performance (the
v13 agent_stats ledger) replaces the priors as runs accumulate.

Capability mapping is a curation decision, recorded here (mirroring
agency.py's division rule): every library agent receives
"persona.task"; an agent ALSO receives "research.web" when its name
or description marks it as advisory/research-flavoured — matching any
of: research, analyst, analysis, market, seo, data, finance,
academic, strategy, journalist, writer. Everything else is installed
as a persona only, not dressed up as a web researcher.

Delivery story: the library is ~72 MB of harvested third-party files,
so it is deliberately NOT vendored into app/data (the agency roster
is). library_dir() resolves: AGENTQON_SUBAGENT_LIBRARY env var →
bundled app/data/subagents-library if a distributor ships one →
~/workspace/subagents-library/files on the operator's machine. A
deployment that wants the roster points the env var at its copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

LIBRARY_VERSION = "1.0.0"
RESEARCH_KEYWORDS = (
    "research", "analyst", "analysis", "market", "seo", "data",
    "finance", "academic", "strategy", "journalist", "writer",
)
_SOUL_NAMES = {"soul.md"}
_COMPANION_STEMS = {"identity", "agents", "user", "agent"}
_SKIP_STEMS = {"readme", "index", "contributing", "changelog", "license"}
_TEXT_SUFFIXES = {".md", ".toml", ".json"}


def library_dir() -> Path:
    env = os.environ.get("OMNIMIND_SUBAGENT_LIBRARY")
    if env and Path(env).is_dir():
        return Path(env)
    bundled = Path(__file__).parent / "data" / "subagents-library"
    if bundled.is_dir():
        return bundled
    return Path.home() / "workspace" / "subagents-library" / "files"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "agent"


def _split_source(dirname: str) -> tuple[str, str]:
    """'VoltAgent__awesome-codex-subagents' -> ('VoltAgent', '...')."""
    owner, _, repo = dirname.partition("__")
    return owner or dirname, repo or dirname


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Tolerant flat frontmatter parse (agency.py's subset of YAML,
    plus block scalars — some collections write description: | )."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    front, body = text[3:end], text[end + 4:].lstrip()
    lines = front.splitlines()
    meta: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^([a-zA-Z_-]+):\s*(.*)$", lines[i])
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value in ("|", ">", "|-", ">-", "|+", ">+"):
                block = []
                i += 1
                while i < len(lines) and (lines[i].startswith((" ", "\t"))
                                          or not lines[i].strip()):
                    block.append(lines[i].strip())
                    i += 1
                meta[key] = " ".join(x for x in block if x).strip()
                continue
            meta[key] = value.strip('"').strip("'")
        i += 1
    return meta, body


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip().strip("#").strip()
        if line:
            break
    return None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().strip("*_`> ")
        if line and not line.startswith("#"):
            return line
    return ""


# ------------------------------------------------------------- parsers
# Each returns {"name", "description", "body"} or None.

def parse_claude_md(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = _parse_frontmatter(text)
    if fm is not None:
        meta, body = fm
        name = meta.get("name") or path.stem
        description = meta.get("description") or name
        if not body.strip():
            return None
        return {"name": name, "description": description, "body": body}
    # Frontmatter-less persona markdown: heading or filename is the name.
    if not text.strip():
        return None
    name = _first_heading(text) or path.stem
    description = _first_line(text) or name
    return {"name": name, "description": description, "body": text}


def parse_codex_toml(path: Path) -> dict | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    name = data.get("name") or path.stem
    body = (data.get("developer_instructions") or data.get("instructions")
            or data.get("prompt") or data.get("system_prompt"))
    if not body:
        return None
    return {"name": str(name),
            "description": str(data.get("description") or name),
            "body": str(body)}


def parse_kiro_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    body = (data.get("prompt") or data.get("instructions")
            or data.get("system_prompt") or data.get("developer_instructions"))
    name = data.get("name")
    if not body or not name:
        return None  # metadata/marketplace JSON is not an agent
    return {"name": str(name),
            "description": str(data.get("description") or name),
            "body": str(body)}


_PARSERS = {".md": parse_claude_md, ".toml": parse_codex_toml,
            ".json": parse_kiro_json}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _persona_from_dir(dirpath: Path, files: list[Path]) -> dict | None:
    """One OpenClaw persona from its directory: SOUL.md is the brief,
    companions are appended under headings, metadata.json only lends
    name/description."""
    soul = next(f for f in files if f.name.lower() in _SOUL_NAMES)
    body = _read(soul)
    if not body.strip():
        return None
    name = dirpath.name
    description = ""
    meta_file = next((f for f in files if f.name.lower() == "metadata.json"),
                     None)
    if meta_file is not None:
        try:
            meta = json.loads(_read(meta_file))
            if isinstance(meta, dict):
                name = str(meta.get("name") or name)
                description = str(meta.get("one_liner")
                                  or meta.get("description") or "")
        except (json.JSONDecodeError, ValueError):
            pass
    for companion in sorted(files, key=lambda f: f.name.lower()):
        if companion is soul or companion is meta_file:
            continue
        if companion.stem.lower() in _COMPANION_STEMS:
            text = _read(companion)
            if text.strip():
                body += (f"\n\n## {companion.stem.upper()}\n\n"
                         f"{text.strip()}")
    return {"name": name,
            "description": description or _first_line(body) or name,
            "body": body}


def iter_library(root: Path):
    """Yield (source_dirname, slug_seed, parsed, files_consumed).

    Walks each <owner>__<repo> source; OpenClaw persona directories
    (any directory holding a SOUL.md) collapse into ONE record.
    files_consumed lets the caller reconcile against the file census.
    """
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(f for f in source_dir.rglob("*")
                       if f.is_file() and f.suffix.lower() in _TEXT_SUFFIXES)
        by_dir: dict[Path, list[Path]] = {}
        for f in files:
            by_dir.setdefault(f.parent, []).append(f)
        persona_dirs = {d for d, fs in by_dir.items()
                        if any(f.name.lower() in _SOUL_NAMES for f in fs)}
        consumed: set[Path] = set()
        for dirpath in sorted(persona_dirs):
            group = by_dir[dirpath]
            consumed.update(group)
            parsed = _persona_from_dir(dirpath, group)
            rel = dirpath.relative_to(source_dir).as_posix()
            yield source_dir.name, rel, parsed, len(group)
        for f in files:
            if f in consumed:
                continue
            rel = f.relative_to(source_dir).with_suffix("").as_posix()
            if f.name.lower() == "skill.md" or f.stem.lower() in _SKIP_STEMS:
                yield source_dir.name, rel, None, 1
                continue
            if f.stem.lower() in _COMPANION_STEMS and f.suffix.lower() == ".md":
                # A companion brief with no SOUL.md anchor in its
                # directory is a fragment, not a persona.
                yield source_dir.name, rel, None, 1
                continue
            yield (source_dir.name, rel,
                   _PARSERS[f.suffix.lower()](f), 1)


def _source_of(root: Path, source: str) -> tuple[str, str]:
    """Provenance for a library source dir: the shipped tree
    renamed its dirs to collection-NN, so the original
    owner/repo travels in _source.json; older fixture trees
    still encode it in the dirname."""
    prov = root / source / "_source.json"
    if prov.is_file():
        try:
            data = json.loads(prov.read_text(encoding="utf-8"))
            if data.get("owner"):
                return str(data["owner"]), str(data.get("repo") or "")
        except Exception:
            pass
    return _split_source(source)


def import_library(store, tenant_id: str, root: Path | None = None) -> dict:
    root = root or library_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"subagents library not found at {root}")
    imported = updated = skipped = duplicates = files_seen = folded = 0
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    sources: dict[str, tuple[str, str]] = {}
    existing = {a["agent_id"]: a for a in store.list_agents(tenant_id)}
    from .display_names import agent_id_for_body
    for source, slug_seed, parsed, consumed in iter_library(root):
        files_seen += consumed
        if parsed is None:
            skipped += consumed
            continue
        body = parsed["body"].strip()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            duplicates += 1
            skipped += consumed - 1
            continue
        seen_hashes.add(digest)
        if source not in sources:
            sources[source] = _source_of(root, source)
        owner, repo = sources[source]
        # AgentQon identity is content: the new id resolves from
        # the body hash (the harvested paths were renamed).
        agent_id = agent_id_for_body(digest)
        if agent_id is None or agent_id in seen_ids:
            # Unshipped entry (fixture trees): derive a clean id
            # from the entry's own name, never from its path.
            from .naming import agent_display_name, role_slug, \
                source_tokens
            disp = agent_display_name(
                parsed["name"], parsed["description"], digest,
                source_tokens(owner, repo))
            base_id = f"lib.{role_slug(disp) or 'specialist'}"
            agent_id, n = base_id, 2
            while agent_id in seen_ids:
                agent_id = f"{base_id}-{n}"
                n += 1
        seen_ids.add(agent_id)
        haystack = f"{parsed['name']} {parsed['description']}".lower()
        capabilities = ["persona.task"]
        if any(k in haystack for k in RESEARCH_KEYWORDS):
            capabilities.append("research.web")
        manifest = {
            "agent_id": agent_id, "version": LIBRARY_VERSION,
            "owner": f"{owner}/{repo} (via subagents-library)",
            "description": parsed["description"][:400],
            "capabilities": capabilities,
            "permissions_requested": ["web.read", "artifact.write"]
            if "research.web" in capabilities else ["artifact.read"],
            "strategy": "persona", "lifecycle": "active",
            "quality": 3.5, "reliability": 0.85, "latency_ms": 950,
            "cost_per_run_usd": 0.30,
            "origin": "subagents-library", "persona_ref": agent_id,
        }
        existed = store.get_persona(agent_id) is not None
        if agent_id in existing:
            # Re-import refreshes the brief, never the operator's
            # lifecycle decision (same rule as the agency importer).
            manifest["lifecycle"] = existing[agent_id].get("lifecycle", "active")
        store.put_persona(agent_id, body)
        store.upsert_agent(tenant_id, manifest)
        folded += consumed - 1  # OpenClaw companions folded into one persona
        if existed:
            updated += 1
        else:
            imported += 1
    return {"imported": imported, "updated": updated, "skipped": skipped,
            "duplicates_dropped": duplicates, "companions_folded": folded,
            "files_seen": files_seen,
            "total": imported + updated, "library": str(root)}
