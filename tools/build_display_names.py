"""Build app/data/display_names.json + app/data/id_map.json.

The rename build step (v22.2, Zohaib's "no tag of reused" order):
walks the shipped rosters with the SAME parsers the runtime
importers use, assigns every harvested agent and skill an
original AgentQon display name and a clean deterministic ID
(app/naming.py holds the naming rules), and records the
old -> new ID map that the boot migration (app/id_migration.py)
uses to rewrite stored user data after an upgrade.

Self-checking: the old-ID sets it derives from the parse walk
must equal what a real registry import produces; any divergence
aborts the build. Deterministic: running it twice on the same
tree produces byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import naming  # noqa: E402
from app.agency import iter_roster, parse_agent_file, roster_dir  # noqa: E402
from app.library import _slugify, iter_library, library_dir  # noqa: E402
from app.library import _split_source  # noqa: E402
from app.skills import skills_root  # noqa: E402

BUILTIN_DISPLAY = {
    "agent.research.alpha": "Alpha — Evidence Researcher",
    "agent.research.beta": "Beta — Market Researcher",
    "agent.critic.core": "Core Critic",
}


# ------------------------------------------------------------ agents
def library_corpus() -> list[dict]:
    """One record per kept library agent (old-import keep rule:
    first copy of each body), plus every duplicate copy for the
    new keep rule. Old IDs are re-derived with the importer's
    exact formula and collision loop."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for source, seed, parsed, _consumed in iter_library(library_dir()):
        if parsed is None:
            continue
        body = parsed["body"].strip()
        digest = hashlib.sha256(body.encode()).hexdigest()
        owner, repo = _split_source(source)
        cand = {"name": parsed["name"],
                "description": parsed["description"], "owner": owner,
                "repo": repo, "source": source, "seed": seed,
                "body_sha": digest}
        if digest not in groups:
            groups[digest] = []
            order.append(digest)
        groups[digest].append(cand)
    seen: set[str] = set()
    records = []
    for digest in order:
        copies = groups[digest]
        first = copies[0]
        source_slug = f"{_slugify(first['owner'])}-{_slugify(first['repo'])}"
        base = f"lib.{source_slug}.{_slugify(first['seed'])}"
        old_id, n = base, 2
        while old_id in seen:
            old_id = f"{base}-{n}"
            n += 1
        seen.add(old_id)
        kept = min(copies, key=lambda c: (c["name"], c["description"],
                                          c["owner"], c["repo"],
                                          c["source"], c["seed"]))
        records.append({**kept, "body_sha": digest, "old_id": old_id,
                        "copies": len(copies),
                        "kept_changed": kept is not first})
    return records


def agency_corpus() -> list[dict]:
    records = []
    for division, f in iter_roster(roster_dir()):
        parsed = parse_agent_file(f)
        if parsed is None:
            continue
        meta, body = parsed["meta"], parsed["body"]
        records.append({
            "division": division, "stem": f.stem,
            "name": meta.get("name", ""),
            "description": meta.get("description") or meta.get("name", ""),
            "body_sha": hashlib.sha256(body.encode()).hexdigest(),
            "old_id": f"agency.{division}.{f.stem}"})
    return records


# ------------------------------------------------------------ skills
def _repo_slug_tokens(repo: str) -> set[str]:
    return naming.source_tokens(*repo.split("/", 1)) if "/" in repo \
        else naming.source_tokens(repo)


def discovered_handles(metas: list[dict]) -> dict[str, set[str]]:
    """Per-repo slug tokens that behave like author handles: they
    LEAD several of one repo's skill slugs (series prefixes like
    an author's name), appear in no other repo's slugs, and are
    not common English (a token used across many descriptions is
    a subject word, e.g. 'detail', never a handle)."""
    token_repos: dict[str, set[str]] = defaultdict(set)
    lead_counts: dict[str, Counter] = defaultdict(Counter)
    desc_counts: Counter = Counter()
    for m in metas:
        toks = [t.lower() for t in naming._raw_tokens(m["name"])]
        for t in set(toks):
            token_repos[t].add(m["repo"])
        core = [t for t in toks if not t.isdigit()]
        for t in set(core[:2]):
            lead_counts[m["repo"]][t] += 1
        for t in set(naming._raw_tokens(
                (m.get("description") or "").lower())):
            desc_counts[t] += 1
    handles: dict[str, set[str]] = defaultdict(set)
    generic = naming.ROLE_WORDS | set(naming.ACRONYMS) | {
        "pro", "plus", "max", "mini", "free", "open", "source"}
    for repo, counts in lead_counts.items():
        for tok, n in counts.items():
            if n >= 2 and len(token_repos[tok]) == 1 \
                    and tok not in generic and len(tok) >= 3 \
                    and desc_counts[tok] < 10:
                handles[repo].add(tok)
    return handles


def slug_vocabulary(metas: list[dict]) -> set[str]:
    """Tokens used by at least three distinct skill slugs: the
    corpus's own subject vocabulary, the only words a title may
    borrow from a description."""
    counts: Counter = Counter()
    for m in metas:
        for t in set(t.lower()
                      for t in naming._raw_tokens(m["name"])):
            counts[t] += 1
    return {t for t, n in counts.items() if n >= 3}


def skill_title(meta: dict, extra_drop: set[str],
                vocab: set[str]) -> str:
    toks = naming.clean_tokens(naming._name_head(meta["name"]),
                               extra_drop)
    if len(toks) < 2:
        stop = {"for", "to", "and", "with", "your", "you", "the",
                "of", "in", "on", "by", "from", "using", "use",
                "how", "what", "when", "an", "as", "at", "or"}
        have = {t.lower() for t in toks}
        for tok in naming._raw_tokens(meta.get("description", "")):
            low = tok.lower()
            if low in have or low in extra_drop or low.isdigit() \
                    or low in stop or low in naming.BRAND_TOKENS \
                    or low in naming.STRUCTURAL_TOKENS \
                    or low in naming.JUNK_TOKENS:
                continue
            if any(low[:3] == h[:3] for h in have if len(h) >= 3):
                continue  # no near-duplicate stems (Coding/Code)
            if low not in vocab and low not in naming.ACRONYMS:
                continue
            toks.append(tok)
            have.add(low)
            if len(toks) >= 3:
                break
    if not toks:
        toks = ["Specialist"]
    return naming.title_case_tokens(toks)


def build_skills() -> list[dict]:
    root = skills_root()
    metas = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        try:
            meta = json.loads((folder / "meta.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or not meta.get("name"):
            continue
        meta = dict(meta)
        meta["old_id"] = folder.name
        metas.append(meta)
    handles = discovered_handles(metas)
    vocab = slug_vocabulary(metas)
    records = []
    meta_by_old = {}
    for m in metas:
        drop = _repo_slug_tokens(m.get("repo", "")) \
            | handles.get(m.get("repo", ""), set())
        title = skill_title(m, drop, vocab)
        meta_by_old[m["old_id"]] = m
        records.append({"old_id": m["old_id"], "name": m["name"],
                        "repo": m.get("repo", ""), "display": title,
                        "path": m.get("path", ""),
                        "description": m.get("description", ""),
                        "handles_dropped": sorted(
                            handles.get(m.get("repo", ""), set()))})

    def qualifiers(rec: dict) -> list[str]:
        out: list[str] = []
        base_slug = naming.role_slug(rec["display"])
        parts = [p for p in rec["path"].split("/")[:-1]
                 if p and p not in ("skills", "SKILL.md")]
        for part in reversed(parts[-2:]):
            toks = naming.clean_tokens(part)
            if toks:
                cand = naming.title_case_tokens(toks)
                cslug = naming.role_slug(cand)
                if cslug != base_slug and cslug not in base_slug:
                    out.append(cand)
        have = {t.lower() for t in naming._raw_tokens(rec["display"])}
        for tok in naming.clean_tokens(
                (rec["description"] or "").split(".")[0]):
            if tok.lower() not in have \
                    and tok.lower() not in naming.QUALIFIER_STOP \
                    and not tok.lower().endswith("ing"):
                cand = naming.title_case_tokens([tok])
                if naming.role_slug(cand) != base_slug:
                    out.append(cand)
                have.add(tok.lower())
            if len(out) >= 4:
                break
        return out

    touched = naming.disambiguate_displays(records, qualifiers)
    for rec in records:
        rec["title"] = rec["display"]
        rec["title_slug"] = naming.role_slug(rec["title"])
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        groups[rec["title_slug"]].append(rec)
    taken = set(groups)
    for slug in sorted(groups):
        group = sorted(groups[slug], key=lambda r: r["old_id"])
        for i, rec in enumerate(group):
            if i == 0:
                rec["new_id"] = slug
                continue
            n = i + 1
            while f"{slug}-{n}" in taken:
                n += 1
            rec["new_id"] = f"{slug}-{n}"
            taken.add(rec["new_id"])
    print(f"skill displays disambiguated: {touched}")
    return records


# ------------------------------------------------------------ main
def verify_old_ids(agent_old_ids: set[str]) -> None:
    """The parse-walk old IDs must equal a real registry import's,
    or the map would strand user data. Aborts on divergence."""
    from app.agency import import_roster
    from app.library import import_library
    from app.registry import seed_registry
    from app.store import Store
    with tempfile.TemporaryDirectory() as td:
        store = Store(str(Path(td) / "verify.db"))
        tenant = "org_verify"
        seed_registry(store, tenant)
        with store.bulk():
            import_roster(store, tenant)
        with store.bulk():
            import_library(store, tenant)
        real = {a["agent_id"] for a in store.list_agents(tenant)}
    mine = set(agent_old_ids) | set(BUILTIN_DISPLAY)
    if real != mine:
        raise SystemExit(
            f"OLD-ID DIVERGENCE: walk={len(mine)} import={len(real)} "
            f"only-walk={sorted(mine - real)[:5]} "
            f"only-import={sorted(real - mine)[:5]}")
    print(f"old-id self-check OK: {len(real)} agents")


def main() -> None:
    lib_records = library_corpus()
    ag_records = agency_corpus()
    for rec in lib_records:
        rec["kind"] = "lib"
        parts = rec["seed"].split("/")
        hint = parts[-2] if len(parts) >= 2 else ""
        rec["display"] = naming.agent_display_name(
            rec["name"], rec["description"], rec["body_sha"],
            naming.source_tokens(rec["owner"], rec["repo"]), hint)
    for rec in ag_records:
        rec["kind"] = "agency"
        rec["display"] = naming.agent_display_name(
            rec["name"], rec["description"], rec["body_sha"],
            hint=rec["division"])
    all_agents = lib_records + ag_records
    touched = naming.disambiguate_displays(
        all_agents, naming.agent_qualifiers)
    print(f"agent displays disambiguated: {touched}")
    naming.assign_agent_ids(all_agents)
    assert len({r["display"] for r in all_agents}) == len(all_agents), \
        "agent display names must be unique"
    assert len({r["new_id"] for r in all_agents}) == len(all_agents), \
        "agent ids must be unique"
    verify_old_ids({r["old_id"] for r in all_agents})

    skill_records = build_skills()
    assert len({r["display"] for r in skill_records}) \
        == len(skill_records), "skill titles must be unique"
    assert len({r["new_id"] for r in skill_records}) \
        == len(skill_records), "skill ids must be unique"

    display = {"agents": {}, "skills": {}}
    id_map = {"version": 1, "agents": {}, "skills": {}}
    for rec in all_agents:
        display["agents"][rec["new_id"]] = rec["display"]
        id_map["agents"][rec["old_id"]] = rec["new_id"]
    for old, disp in BUILTIN_DISPLAY.items():
        display["agents"][old] = disp
    for rec in skill_records:
        display["skills"][rec["new_id"]] = rec["title"]
        id_map["skills"][rec["old_id"]] = rec["new_id"]

    # Library identity is content: paths were renamed, bodies
    # were not. The runtime importer resolves each kept record's
    # new ID from its body hash via this index.
    body_index = {r["body_sha"]: r["new_id"] for r in lib_records}
    assert len(body_index) == len(lib_records), \
        "kept library bodies must be unique"

    data_dir = ROOT / "app" / "data"
    for fname, payload in (("display_names.json", display),
                           ("id_map.json", id_map),
                           ("id_by_body.json", body_index)):
        text = json.dumps(payload, ensure_ascii=False, indent=1,
                          sort_keys=True) + "\n"
        (data_dir / fname).write_text(text, encoding="utf-8")
        print(f"wrote app/data/{fname} ({len(text)} bytes)")

    # ---- audit report
    persona = [r for r in all_agents if " — " in r["display"]]
    bare_specialist = [r for r in all_agents
                       if r["display"].split(" — ", 1)[-1]
                       == "Specialist"]
    changed_kept = [r for r in lib_records if r["kept_changed"]]
    agent_collisions = len(all_agents) - len(
        {r["new_id"] for r in all_agents})
    suffixed = 0
    for rec in all_agents:
        prefix = ("lib." if rec["kind"] == "lib"
                  else f"agency.{rec['division']}.")
        if rec["new_id"] != prefix + rec["slug"]:
            suffixed += 1
    print(f"agents: {len(all_agents)} "
          f"(lib {len(lib_records)}, agency {len(ag_records)}), "
          f"persona-named {len(persona)}, "
          f"bare-specialist fallback {len(bare_specialist)}, "
          f"duplicate-body keep-rule changes {len(changed_kept)}, "
          f"ids needing a numeric suffix {suffixed}")
    print(f"skills: {len(skill_records)}, "
          f"unique titles {len({r['title'] for r in skill_records})}, "
          f"ids {len({r['new_id'] for r in skill_records})}")
    import random
    rng = random.Random(20261023)
    print("sample agent renames (random):")
    for rec in rng.sample(all_agents, 8):
        print(f"  {rec['old_id']}  ->  {rec['new_id']}  "
              f"({rec['display']})")
    print("sample persona displays:")
    for rec in rng.sample(persona, min(6, len(persona))):
        print(f"  {rec['old_id']}  ->  ({rec['display']})")
    print("sample bare-specialist fallbacks:")
    for rec in bare_specialist[:6]:
        print(f"  {rec['old_id']}  name={rec['name']!r}")
    print("sample skill renames (random):")
    for rec in rng.sample(skill_records, 8):
        print(f"  {rec['old_id']}  ->  {rec['new_id']}  "
              f"({rec['title']})")
    json.dump({"agents": [{k: r[k] for k in
                           ("old_id", "new_id", "display", "kind",
                            "body_sha") if k in r} for r in all_agents],
               "skills": [{k: r[k] for k in
                           ("old_id", "new_id", "title", "repo",
                            "title_slug") if k in r}
                          for r in skill_records]},
              open("/tmp/rename_build_report.json", "w"), indent=1)


if __name__ == "__main__":
    main()
