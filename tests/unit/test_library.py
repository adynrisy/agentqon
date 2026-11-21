"""Subagents library: per-format parsers, OpenClaw grouping, dedupe,
slug collisions, lifecycle discipline (mirrors test_agency.py)."""
from __future__ import annotations

import pytest

from app.library import (import_library, library_dir, parse_claude_md,
                         parse_codex_toml, parse_kiro_json)
from app.store import Store


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


CLAUDE_MD = """---
name: api-designer
description: Designs APIs and contracts.
tools: Read, Write
model: sonnet
---

You are a senior API designer. Design contracts first.
"""

CODEX_TOML = """name = "api-designer"
description = "Designs API contracts."
developer_instructions = \"\"\"
You are a senior API designer. Design contracts first.
\"\"\"
"""

OPENCODE_MD = """---
description: Deploys services safely to production.
mode: subagent
---

You are a deployment specialist. Ship carefully.
"""

SOUL = "# Chef Persona\n\nYou cook with fire and patience.\n"
IDENTITY = "Name: Chef Remy\nRole: head chef\n"
AGENTS = "Always taste before serving.\n"
META = '{"name": "Chef Remy", "one_liner": "A chef who tastes everything."}'


@pytest.fixture()
def lib_root(tmp_path):
    root = tmp_path / "library"
    _write(root, "acme__claude-pack/agents/api-designer.md", CLAUDE_MD)
    _write(root, "acme__claude-pack/agents/SKILL.md",
           "---\nname: not-an-agent\ndescription: a skill\n---\n\nSkill body.\n")
    _write(root, "acme__codex-pack/agents/api-designer.toml", CODEX_TOML)
    _write(root, "acme__opencode-pack/agents/deployer.md", OPENCODE_MD)
    _write(root, "acme__openclaw-pack/personas/chef/SOUL.md", SOUL)
    _write(root, "acme__openclaw-pack/personas/chef/IDENTITY.md", IDENTITY)
    _write(root, "acme__openclaw-pack/personas/chef/AGENTS.md", AGENTS)
    _write(root, "acme__openclaw-pack/personas/chef/metadata.json", META)
    _write(root, "acme__openclaw-pack/personas/solo/SOUL.md",
           "A lone wolf persona.\n")
    _write(root, "acme__personas/thinker.md",
           "# The Thinker\n\nThinks deeply about hard problems.\n")
    _write(root, "acme__kiro-pack/agents/architect.json",
           '{"name": "architect", "description": "Designs systems.",'
           ' "prompt": "You are an architect."}')
    _write(root, "acme__kiro-pack/agents/marketplace.json",
           '{"plugins": []}')
    return root


def test_parse_codex_toml(tmp_path):
    p = _write(tmp_path, "a.toml", CODEX_TOML)
    parsed = parse_codex_toml(p)
    assert parsed["name"] == "api-designer"
    assert parsed["description"] == "Designs API contracts."
    assert "senior API designer" in parsed["body"]


def test_parse_opencode_no_name_uses_filename(tmp_path):
    p = _write(tmp_path, "deployer.md", OPENCODE_MD)
    parsed = parse_claude_md(p)
    assert parsed["name"] == "deployer"  # filename, OpenCode has no name
    assert "Deploys services" in parsed["description"]


def test_parse_kiro_json_and_metadata_rejection(tmp_path):
    p = _write(tmp_path, "a.json",
               '{"name": "n", "prompt": "You are X."}')
    assert parse_kiro_json(p)["body"] == "You are X."
    q = _write(tmp_path, "m.json", '{"plugins": []}')
    assert parse_kiro_json(q) is None


def test_import_all_formats_and_grouping(lib_root, tmp_path):
    store = Store(str(tmp_path / "lib.db"))
    result = import_library(store, "org_lib", root=lib_root)
    agents = store.list_agents("org_lib")
    lib = [a for a in agents if a["agent_id"].startswith("lib.")]
    ids = {a["agent_id"] for a in lib}
    # claude md + codex toml share the same brief body: the toml is a
    # byte-identical duplicate after parsing and is dropped.
    assert result["duplicates_dropped"] == 1
    # Rename contract (2026-10-23): ids are derived from each
    # entry's own content and no longer encode the source pack;
    # provenance is asserted through the manifest `owner` field.
    owners = {a["owner"] for a in lib}
    assert any(o.startswith("acme/claude-pack") for o in owners)
    assert not any(o.startswith("acme/codex-pack") for o in owners)
    # OpenCode agent lands under its filename-derived name
    assert "lib.deployer" in ids
    # OpenClaw: chef dir (4 files) is ONE persona; solo is one more.
    claw = [a for a in lib if a["owner"].startswith("acme/openclaw-pack")]
    assert len(claw) == 2
    assert result["companions_folded"] == 3  # IDENTITY+AGENTS+metadata
    chef_id = next(a["agent_id"] for a in claw
                   if a["description"] == "A chef who tastes everything.")
    body = store.get_persona(chef_id)
    assert "You cook with fire" in body
    assert "## IDENTITY" in body and "## AGENTS" in body
    chef = next(a for a in lib if a["agent_id"] == chef_id)
    assert chef["description"] == "A chef who tastes everything."
    # frontmatter-less persona + kiro json imported
    assert any(o.startswith("acme/personas") for o in owners)
    assert any(o.startswith("acme/kiro-pack") for o in owners)
    # SKILL.md and marketplace.json skipped; every file reconciles
    total_files = sum(1 for _ in lib_root.rglob("*") if _.is_file())
    assert result["files_seen"] == total_files
    assert result["total"] == len(lib)
    assert all(a["lifecycle"] == "active" for a in lib)


def test_priors_and_capability_curation(lib_root, tmp_path):
    store = Store(str(tmp_path / "lib2.db"))
    import_library(store, "org_lib2", root=lib_root)
    agents = store.list_agents("org_lib2")
    api = next(a for a in agents
               if a["owner"].startswith("acme/claude-pack"))
    assert api["quality"] == 3.5 and api["reliability"] == 0.85
    assert api["origin"] == "subagents-library"
    assert api["owner"] == "acme/claude-pack (via subagents-library)"
    assert api["strategy"] == "persona"
    # 'api-designer' + 'Designs APIs' hits no advisory keyword
    assert api["capabilities"] == ["persona.task"]
    # 'architect' description 'Designs systems.' — no keyword either;
    # the thinker: name/heading hits nothing. Use chef: 'tastes' no.
    # Positive case: rename-free check via the solo persona is not one;
    # assert at least the mechanism with a dedicated fixture below.


def test_research_keyword_grants_web(tmp_path):
    root = tmp_path / "libkw"
    _write(root, "acme__kw/agents/seo-analyst.md",
           "---\nname: seo-analyst\ndescription: SEO research and audits."
           "\n---\n\nYou audit sites.\n")
    store = Store(str(tmp_path / "kw.db"))
    import_library(store, "org_kw", root=root)
    agent = store.list_agents("org_kw")[0]
    assert "research.web" in agent["capabilities"]
    assert agent["permissions_requested"] == ["web.read", "artifact.write"]


def test_slug_collision_suffixes(tmp_path):
    # Rename contract: ids derive from the entry's own name, so a
    # collision is two entries carrying the same name; the second
    # one takes the deterministic -2 suffix.
    root = tmp_path / "libcol"
    _write(root, "acme__col/agents/first.md",
           "---\nname: API Designer\ndescription: First.\n---\n\n"
           "First body, unique.\n")
    _write(root, "acme__col/agents/second.md",
           "---\nname: API Designer\ndescription: Second.\n---\n\n"
           "Second body, also unique.\n")
    store = Store(str(tmp_path / "col.db"))
    result = import_library(store, "org_col", root=root)
    ids = sorted(a["agent_id"] for a in store.list_agents("org_col"))
    assert result["total"] == 2
    assert ids == ["lib.api-designer", "lib.api-designer-2"]


def test_reimport_preserves_lifecycle(lib_root, tmp_path):
    store = Store(str(tmp_path / "lib3.db"))
    first = import_library(store, "org_lib3", root=lib_root)
    target = next(a for a in store.list_agents("org_lib3")
                  if a["owner"].startswith("acme/claude-pack"))
    store.set_agent_lifecycle("org_lib3", target["agent_version_id"],
                              "quarantine")
    second = import_library(store, "org_lib3", root=lib_root)
    assert second["imported"] == 0
    assert second["updated"] == first["total"]
    after = next(a for a in store.list_agents("org_lib3")
                 if a["agent_id"] == target["agent_id"])
    assert after["lifecycle"] == "quarantine"


def test_library_dir_env_override(lib_root, monkeypatch):
    monkeypatch.setenv("OMNIMIND_SUBAGENT_LIBRARY", str(lib_root))
    assert library_dir() == lib_root


def test_missing_library_raises(tmp_path):
    store = Store(str(tmp_path / "lib4.db"))
    with pytest.raises(FileNotFoundError):
        import_library(store, "org_x", root=tmp_path / "nope")
