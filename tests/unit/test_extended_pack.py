"""v22.2: the Extended Library Pack — server/library half.

The pack is the optional, signed download that returns the 213
security-research skills cut in v22.1.3 (they never ride inside
the npm package). These tests pin the library-side contract:

- the merged library counts honestly in BOTH states (boxed total
  and extended total reported separately);
- an extended skill resolves in search and in get() ONLY while
  the pack is installed, and always carries pack="extended";
- removing the pack returns the library to the boxed count with
  no residue (a fresh library view sees the boxed catalog only).

The CLI half (download, Ed25519 verify, refusal of tampered
packs, the setup offer) is pinned in cli/tests/hardening.test.js;
the API surface is pinned in
tests/integration/test_extended_pack_api.py.
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import extended_pack as ep
from app.skills import SkillLibrary, get_library


def _write_skill(root: Path, folder: str, name: str, description: str,
                 body: str, repo: str = "acme/dev-skills") -> None:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(
        f"<!--\nSource: {repo}\nLicense: MIT\n-->\n\n{body}",
        encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "name": name, "display_name": name, "description": description,
        "repo": repo, "path": f"skills/{name}/SKILL.md",
        "url": f"https://github.com/{repo}", "license": "MIT",
        "body_bytes": len(body), "truncated": False}), encoding="utf-8")


@pytest.fixture()
def boxed_root(tmp_path) -> Path:
    root = tmp_path / "boxed"
    _write_skill(root, "pytest-unit-testing--a1", "pytest-unit-testing",
                 "Write unit tests with pytest.",
                 "# Pytest\nUse fixtures and plain asserts.")
    _write_skill(root, "sourdough-baking--c1", "sourdough-baking",
                 "Bake sourdough bread.",
                 "# Sourdough\nFeed the starter and bake.")
    return root


@pytest.fixture()
def pack_dir(tmp_path) -> Path:
    """An installed pack, in the shape the CLI leaves behind:
    <pack>/skills/<id>/… plus the pack manifest."""
    root = tmp_path / "extended-library"
    _write_skill(root / "skills", "hunt-rce", "hunt-rce",
                 "Hunting skill for rce vulnerabilities.",
                 "# Hunt RCE\nMethodology for hunting remote code "
                 "execution (rce) flaws in web targets.",
                 repo="sickn33/agentic-awesome-skills")
    _write_skill(root / "skills", "sqlmap-database-pentesting",
                 "sqlmap-database-pentesting",
                 "Database pentesting with sqlmap.",
                 "# Sqlmap\nEnumerate and test database inputs.",
                 repo="sickn33/agentic-awesome-skills")
    (root / "manifest.json").write_text(json.dumps(
        {"pack": "extended-library", "version": 1, "count": 2}),
        encoding="utf-8")
    return root


# ------------------------------------------------------- dir + state
def test_extended_dir_resolution(tmp_path, monkeypatch):
    settings = SimpleNamespace(data_dir=tmp_path / "home" / "data")
    monkeypatch.delenv("OMNIMIND_EXTENDED_SKILLS_DIR", raising=False)
    # default: the data HOME (parent of the data dir)
    assert ep.extended_dir(settings) == tmp_path / "home" / "extended-library"
    assert ep.extended_installed(settings) is False
    # env override wins
    custom = tmp_path / "elsewhere"
    monkeypatch.setenv("OMNIMIND_EXTENDED_SKILLS_DIR", str(custom))
    assert ep.extended_dir(settings) == custom
    # installed means: catalog + manifest, not a bare directory
    (custom / "skills").mkdir(parents=True)
    assert ep.extended_installed(settings) is False
    (custom / "manifest.json").write_text("{}", encoding="utf-8")
    assert ep.extended_installed(settings) is True


def test_pack_constants_are_the_true_numbers():
    assert ep.EXTENDED_PACK_SKILL_COUNT == 213
    assert ep.BOXED_SKILL_TOTAL == 4840
    assert ep.EXTENDED_PACK_URL == (
        "https://github.com/adynrisy/agentqon/releases/download/"
        "extended-library-v1/extended-library-v1.tar.gz")
    assert len(ep.EXTENDED_PACK_TARBALL_SHA256) == 64
    int(ep.EXTENDED_PACK_TARBALL_SHA256, 16)  # hex, not decoration
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    from cryptography.hazmat.primitives.serialization import (
        load_pem_public_key)
    key = load_pem_public_key(
        ep.EXTENDED_PACK_PUBLIC_KEY_PEM.encode())
    assert isinstance(key, Ed25519PublicKey)


# ------------------------------------------------------- merge logic
def test_merge_counts_and_provenance(boxed_root, pack_dir, tmp_path):
    lib = SkillLibrary(boxed_root, tmp_path / "idx",
                       extra_root=pack_dir / "skills")
    facets = lib.facets()
    assert facets["total"] == 4
    assert facets["boxed_total"] == 2
    assert facets["extended_total"] == 2
    assert facets["extended_installed"] is True
    # the total a user would have with the REAL pack, per the pin
    assert facets["total_with_extended"] == 2 + 213
    metas = {m["id"]: m for m in lib.browse(limit=50)[0]}
    assert metas["hunt-rce"]["pack"] == "extended"
    assert metas["pytest-unit-testing--a1"]["pack"] == "boxed"


def test_boxed_only_counts_without_pack(boxed_root, tmp_path):
    lib = SkillLibrary(boxed_root, tmp_path / "idx")
    facets = lib.facets()
    assert facets["total"] == 2
    assert facets["boxed_total"] == 2
    assert facets["extended_total"] == 0
    assert facets["extended_installed"] is False
    assert facets["total_with_extended"] == 2 + 213


def test_search_and_get_only_with_pack(boxed_root, pack_dir, tmp_path):
    plain = SkillLibrary(boxed_root, tmp_path / "idx-plain")
    items, _ = plain.search("rce")
    assert all(i["id"] != "hunt-rce" for i in items)
    assert plain.get("hunt-rce") is None

    merged = SkillLibrary(boxed_root, tmp_path / "idx-merged",
                          extra_root=pack_dir / "skills")
    items, total = merged.search("rce")
    hit = [i for i in items if i["id"] == "hunt-rce"]
    assert total >= 1 and len(hit) == 1
    assert hit[0]["pack"] == "extended"
    full = merged.get("hunt-rce")
    assert full is not None and full["pack"] == "extended"
    assert "remote code" in full["body"]


# ------------------------------------------- install/remove via the
# process-wide get_library() seam the server actually uses
def _make_full_pack(base: Path) -> Path:
    """213 minimal skills + manifest — the real pack's shape at
    the real count, so the honest totals are the shipped ones."""
    root = base / "extended-library"
    for i in range(213):
        sid = "hunt-rce" if i == 0 else f"ext-fixture-{i:03d}"
        _write_skill(root / "skills", sid, sid,
                     f"Extended fixture skill {i}.",
                     f"# Fixture {i}\nBody of extended fixture {i}.",
                     repo="sickn33/agentic-awesome-skills")
    (root / "manifest.json").write_text(json.dumps(
        {"pack": "extended-library", "version": 1, "count": 213}),
        encoding="utf-8")
    return root


def test_get_library_counts_flip_with_install_and_remove(
        tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIMIND_EXTENDED_SKILLS_DIR", raising=False)
    monkeypatch.delenv("OMNIMIND_SKILLS_DIR", raising=False)
    settings = SimpleNamespace(data_dir=tmp_path / "home" / "data")
    settings.data_dir.mkdir(parents=True)

    before = get_library(settings).facets()
    assert before["total"] == 4840 and before["boxed_total"] == 4840
    assert before["extended_total"] == 0
    assert before["extended_installed"] is False
    assert before["total_with_extended"] == 5053

    pack = _make_full_pack(tmp_path / "home")
    during = get_library(settings).facets()
    assert during["total"] == 5053 and during["boxed_total"] == 4840
    assert during["extended_total"] == 213
    assert during["extended_installed"] is True
    assert during["total_with_extended"] == 5053

    shutil.rmtree(pack)
    after = get_library(settings).facets()
    assert after["total"] == 4840 and after["extended_total"] == 0
    assert after["extended_installed"] is False
