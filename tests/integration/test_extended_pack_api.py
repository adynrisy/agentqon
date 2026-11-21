"""v22.2: the Extended Library Pack over HTTP.

The boxed catalog root and the extended pack dir are both swapped
for fixtures (OMNIMIND_SKILLS_DIR / OMNIMIND_EXTENDED_SKILLS_DIR),
so the two states are deterministic:

- pack absent: facets report the boxed total alone, the extended
  totals read zero / not installed, and a cut skill (hunt-rce)
  does not resolve;
- pack present: facets report both numbers, search resolves the
  extended skill with pack="extended", and the detail view serves
  its body with the same provenance mark.
"""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from tests.conftest import hdr

client = TestClient(main_mod.app)


def _write_skill(root: Path, folder: str, name: str, description: str,
                 body: str, repo: str = "acme/dev-skills") -> None:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(
        f"<!--\nSource: {repo}\n-->\n\n{body}", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "name": name, "display_name": name, "description": description,
        "repo": repo, "path": f"skills/{name}/SKILL.md",
        "url": f"https://github.com/{repo}", "license": "MIT",
        "body_bytes": len(body), "truncated": False}), encoding="utf-8")


@pytest.fixture(scope="module")
def boxed_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("extended-api-boxed")
    _write_skill(
        root, "pytest-unit-testing--a1", "pytest-unit-testing",
        "Write unit tests with pytest for Python code.",
        "# Pytest unit testing\nUse pytest fixtures and plain "
        "assert statements.")
    _write_skill(
        root, "sourdough-baking--c1", "sourdough-baking",
        "Bake sourdough bread: starter care, fermentation.",
        "# Sourdough\nFeed the starter, bulk ferment and bake.",
        repo="acme/home-skills")
    return root


@pytest.fixture(scope="module")
def pack_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("extended-api-pack")
    _write_skill(
        root / "skills", "hunt-rce", "hunt-rce",
        "Hunting skill for rce vulnerabilities.",
        "# Hunt RCE\nMethodology for hunting remote code execution "
        "(rce) flaws in web targets.",
        repo="sickn33/agentic-awesome-skills")
    (root / "manifest.json").write_text(json.dumps(
        {"pack": "extended-library", "version": 1, "count": 1}),
        encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _boxed_env(boxed_root, monkeypatch):
    monkeypatch.setenv("OMNIMIND_SKILLS_DIR", str(boxed_root))


def _tenant() -> str:
    return f"org_ext_{uuid.uuid4().hex[:8]}"


def test_facets_without_pack_report_boxed_only(boxed_root, tmp_path,
                                               monkeypatch):
    monkeypatch.setenv("OMNIMIND_EXTENDED_SKILLS_DIR",
                       str(tmp_path / "no-pack-here"))
    j = client.get("/v1/skills/facets", headers=hdr(_tenant())).json()
    assert j["total"] == 2
    assert j["boxed_total"] == 2
    assert j["extended_total"] == 0
    assert j["extended_installed"] is False
    # 2 boxed here; the real install reads 4,840 · 5,053 by the
    # same formula (boxed + the pinned 213).
    assert j["total_with_extended"] == 2 + 213


def test_extended_skill_absent_without_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMIND_EXTENDED_SKILLS_DIR",
                       str(tmp_path / "no-pack-here"))
    t = _tenant()
    r = client.get("/v1/skills/hunt-rce", headers=hdr(t))
    assert r.status_code == 404
    j = client.get("/v1/skills", params={"q": "rce"},
                   headers=hdr(t)).json()
    assert all(s["id"] != "hunt-rce" for s in j["skills"])


def test_facets_and_search_with_pack(pack_dir, monkeypatch):
    monkeypatch.setenv("OMNIMIND_EXTENDED_SKILLS_DIR", str(pack_dir))
    t = _tenant()
    j = client.get("/v1/skills/facets", headers=hdr(t)).json()
    assert j["total"] == 3
    assert j["boxed_total"] == 2
    assert j["extended_total"] == 1
    assert j["extended_installed"] is True

    j = client.get("/v1/skills", params={"q": "rce"},
                   headers=hdr(t)).json()
    hit = [s for s in j["skills"] if s["id"] == "hunt-rce"]
    assert len(hit) == 1 and hit[0]["pack"] == "extended"

    r = client.get("/v1/skills/hunt-rce", headers=hdr(t))
    assert r.status_code == 200
    s = r.json()["skill"]
    assert s["pack"] == "extended"
    assert s["repo"] == "sickn33/agentic-awesome-skills"
    assert "remote code" in s["body"]

    # boxed skills keep their own mark in the merged library
    j = client.get("/v1/skills", headers=hdr(t)).json()
    boxed = [s for s in j["skills"] if s["id"] != "hunt-rce"]
    assert boxed and all(s["pack"] == "boxed" for s in boxed)
