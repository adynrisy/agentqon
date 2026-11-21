#!/usr/bin/env python3
"""Apply the AgentQon rename to the shipped data tree.

Reads app/data/id_map.json + app/data/display_names.json (from
tools/build_display_names.py) and restructures app/data so IDs,
paths, and visible names agree:

1. Skills: each folder renamed to its new ID; meta.json's
   `name` and `display_name` fields rewritten to the new title.
   Every other meta field (repo, path, url, license) is left
   byte-identical - attribution is law.
2. Agent library: each `Owner__repo` source dir renamed to
   `collection-NN` (old sorted order, so iteration order is
   unchanged) and given a `_source.json` carrying the original
   {owner, repo} provenance for the importer. Path segments
   below the source level that contain a blocklisted or
   source token are renamed with those tokens stripped.
3. Agency agents: the frontmatter `name:` line of each imported
   file is rewritten to the new display name. Paths stay (they
   are already clean); all other content is untouched.

Usage: python tools/apply_tree_rename.py [--dry-run]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import naming  # noqa: E402

DATA = ROOT / "app" / "data"
DRY = "--dry-run" in sys.argv


def tokens_of(segment: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", segment.lower()) if t]


def raw_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean_segment(segment: str, drop: set[str]) -> str:
    """Strip blocklisted/source tokens from a path segment,
    preserving separators and a leading dot. "" when nothing
    clean remains."""
    dot = segment.startswith(".")
    parts = re.split(r"([._\- ]+)", segment.lstrip("."))
    out: list[str] = []
    for piece in parts:
        if not piece:
            continue
        if re.fullmatch(r"[._\- ]+", piece):
            out.append(piece)
            continue
        toks = tokens_of(piece)
        if toks and all(t in drop for t in toks):
            continue
        out.append(piece)
    cleaned = "".join(out)
    cleaned = re.sub(r"([._\- ])\1+", r"\1", cleaned).strip("._- ")
    if dot:
        cleaned = "." + cleaned if cleaned else ""
    return cleaned


def main() -> int:
    id_map = json.loads((DATA / "id_map.json").read_text())
    display = json.loads((DATA / "display_names.json").read_text())
    skill_map: dict[str, str] = id_map["skills"]
    agent_disp: dict[str, str] = display["agents"]
    stats = {"skill_folders": 0, "skill_metas": 0, "sources": 0,
             "segments": 0, "agency_names": 0}

    # ---- 1. skills -------------------------------------------------
    skills_dir = DATA / "skills"
    seen_new = set()
    skill_titles: dict[str, str] = display["skills"]  # keyed by NEW id
    new_ids = set(skill_map.values())
    for folder in sorted(skills_dir.iterdir()):
        new_id = skill_map.get(folder.name)
        if new_id is None and folder.name in new_ids:
            new_id = folder.name  # already renamed by an earlier run
        if new_id is None:
            print("SKIP unknown skill folder:", folder.name)
            continue
        title = skill_titles.get(new_id)
        meta_path = folder / "meta.json"
        raw = meta_path.read_text(encoding="utf-8")
        meta = json.loads(raw)
        title_slug = naming.role_slug(title or meta["name"]) or new_id
        new_raw = re.sub(r'"display_name":\s*"(?:[^"\\]|\\.)*"',
                         '"display_name": ' + json.dumps(title or "",
                                                          ensure_ascii=False),
                         raw, count=1)
        new_raw = re.sub(r'"name":\s*"(?:[^"\\]|\\.)*"',
                         '"name": ' + json.dumps(title_slug,
                                                 ensure_ascii=False),
                         new_raw, count=1)
        if new_raw != raw:
            if not DRY:
                meta_path.write_text(new_raw, encoding="utf-8")
            stats["skill_metas"] += 1
        seen_new.add(new_id)
        if new_id != folder.name:
            target = skills_dir / new_id
            assert not target.exists(), f"collision: {target}"
            if not DRY:
                folder.rename(target)
            stats["skill_folders"] += 1
    print("skills:", stats)

    # ---- 2. library source dirs ------------------------------------
    lib_dir = DATA / "subagents-library"
    sources = sorted(p for p in lib_dir.iterdir() if p.is_dir())
    rename_plan: list[tuple[Path, Path]] = []
    for i, src in enumerate(sources, 1):
        new_name = f"collection-{i:02d}"
        m = re.match(r"^(.*?)__(.*)$", src.name)
        owner = repo = ""
        if m:
            from app.library import _split_source
            owner, repo = _split_source(src.name)
        target = lib_dir / new_name
        if src.name != new_name:
            rename_plan.append((src, target))
        if not DRY:
            prov = src / "_source.json"
            if owner:
                prov.write_text(json.dumps(
                    {"owner": owner, "repo": repo}, indent=1) + "\n",
                    encoding="utf-8")
        stats["sources"] += 1
        # Segments below source level: brand / harvest tokens
        # are path dirt, and so is a segment that IS the source
        # name wholesale. Subject words inside a repo name
        # ("seo" in "claude-seo") are not dirt - stripping them
        # from every file would mangle the corpus.
        drop = naming.BRAND_TOKENS | {"awesome"}
        whole = {raw_slug(owner), raw_slug(repo)}
        deep = sorted((p for p in src.rglob("*")),
                      key=lambda p: -len(p.parts))
        for p in deep:
            seg_toks = tokens_of(p.name)
            flagged = any(t in drop for t in seg_toks) or \
                raw_slug(p.name.rsplit(".", 1)[0]) in whole
            if flagged:
                if p.is_file() and "." in p.name:
                    stem, _, ext = p.name.rpartition(".")
                    cleaned_stem = clean_segment(stem, drop)
                    cleaned = (cleaned_stem + "." + ext
                               if cleaned_stem else "")
                else:
                    cleaned = clean_segment(p.name, drop)
                if cleaned == p.name:
                    cleaned = ""  # dirty only as a whole-source echo
                if not cleaned:
                    # The whole segment was upstream tokens
                    # (".claude" dirs, "CLAUDE.md" files).
                    if p.is_dir():
                        cleaned = "data"
                    else:
                        stem, dot, ext = p.name.partition(".")
                        cleaned = "brief" + (dot + ext if dot else "")
                if cleaned and cleaned != p.name:
                    newp = p.with_name(cleaned)
                    n = 2
                    base = cleaned
                    while newp.exists():
                        stem, dot, ext = base.partition(".")
                        cand = f"{stem}-x{n}" + (dot + ext if dot else "")
                        newp = p.with_name(cand)
                        n += 1
                    if not DRY:
                        p.rename(newp)
                    stats["segments"] += 1
    for src, target in rename_plan:
        assert not target.exists(), f"collision: {target}"
        if not DRY:
            src.rename(target)
    print("library:", stats)

    # ---- 3. agency frontmatter names --------------------------------
    agency_dir = DATA / "agency-agents"
    # Locate each file through the importer's own import (the
    # manifest's persona_ref is the file's relative path).
    from app.agency import iter_roster
    refs: dict[str, Path] = {}
    for division, file in iter_roster(agency_dir):
        refs[f"agency.{division}.{file.stem}"] = file
    agent_map: dict[str, str] = id_map["agents"]
    for old_id, new_id in agent_map.items():
        if not old_id.startswith("agency."):
            continue
        name = agent_disp.get(new_id)
        path = refs.get(old_id)
        if not path or not name:
            print("AGENCY MISS:", old_id)
            continue
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            print("AGENCY NO-FM:", path)
            continue
        new_raw, n = re.subn(r"(?m)^name:.*$",
                             "name: " + name, raw, count=1)
        if n and new_raw != raw:
            if not DRY:
                path.write_text(new_raw, encoding="utf-8")
            stats["agency_names"] += 1
    print("final:", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
