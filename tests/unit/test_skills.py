"""v22.1 (S2/S3): the skill library — index, ranked search, the
relevance gate, per-tenant pins, retrieval ordering, and the
injection section format.

Runs against a small fixture tree (the shipped catalog's shape:
folder/skill.md + folder/meta.json) so ranking and gating are
deterministic. The gate's calibration against the real 4,840-skill
library (5,053 before the v22.1.3 exclusion) was measured at build
time (see app/skills.py).
"""
import json
from pathlib import Path

import pytest

from app import skills as skills_mod
from app.skills import (SkillLibrary, format_skills_section,
                        retrieve_for_task)
from app.store import Store

TENANT = "org_skills_test"
OTHER = "org_skills_other"


def _write_skill(root: Path, folder: str, name: str, description: str,
                 body: str, repo: str = "acme/dev-skills",
                 license_: str = "MIT") -> str:
    d = root / folder
    d.mkdir(parents=True)
    (d / "skill.md").write_text(
        f"<!--\nSource: {repo}\nPath: skills/{name}/SKILL.md\n"
        f"License: {license_}\n-->\n\n{body}", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "name": name, "display_name": name, "description": description,
        "repo": repo, "path": f"skills/{name}/SKILL.md",
        "url": f"https://github.com/{repo}", "license": license_,
        "body_bytes": len(body), "truncated": False}), encoding="utf-8")
    return folder


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("skills-fixture")
    _write_skill(
        root, "pytest-unit-testing--a1", "pytest-unit-testing",
        "Write unit tests with pytest for Python code: fixtures, "
        "assertions, coverage.",
        "# Pytest unit testing\nUse pytest fixtures and plain assert "
        "statements to test python functions. Run pytest -q and read "
        "the coverage report.")
    _write_skill(
        root, "python-debugging--a2", "python-debugging",
        "Debug Python programs: tracebacks, pdb, logging, breakpoints.",
        "# Python debugging\nRead the traceback, set breakpoints with "
        "pdb, add logging, and isolate the failing line.")
    _write_skill(
        root, "seo-copywriting--b1", "seo-copywriting",
        "Marketing copywriting and SEO content: keywords, headlines, "
        "meta descriptions.",
        "# SEO copywriting\nResearch keywords, write headlines, and "
        "optimise meta descriptions for search marketing.",
        repo="acme/growth-skills", license_="Apache-2.0")
    _write_skill(
        root, "sourdough-baking--c1", "sourdough-baking",
        "Bake sourdough bread: starter care, fermentation, crust.",
        "# Sourdough\nFeed the starter, bulk ferment, shape and bake "
        "for a crackling crust.", repo="acme/home-skills")
    _write_skill(
        root, "code-review--d1", "code-review",
        "Review code changes for correctness, style, and risk.",
        "# Code review\nCheck the diff line by line for bugs, style "
        "drift, and risky shortcuts.", repo="acme/dev-skills")
    _write_skill(
        root, "code-review--d2", "code-review",
        "A second, stricter code review checklist.",
        "# Code review checklist\nCorrectness first, then style, "
        "then risk sign-off.", repo="acme/quality-skills")
    return root


@pytest.fixture()
def lib(fixture_root, tmp_path) -> SkillLibrary:
    return SkillLibrary(fixture_root, tmp_path / "idx")


# ------------------------------------------------------------ library
def test_index_builds_and_facets_count(lib):
    assert lib.available()
    assert lib.total() == 6
    facets = lib.facets()
    assert facets["total"] == 6
    # the two code-review skills share one meta name
    assert facets["unique_names"] == 5
    counts = {r["repo"]: r["count"] for r in facets["repos"]}
    assert counts == {"acme/dev-skills": 3, "acme/growth-skills": 1,
                      "acme/home-skills": 1, "acme/quality-skills": 1}
    index = lib.ensure_index()
    assert index.exists() and index.parent == lib.index_dir


def test_get_returns_body_and_provenance(lib):
    skill = lib.get("pytest-unit-testing--a1")
    assert skill["name"] == "pytest-unit-testing"
    assert skill["repo"] == "acme/dev-skills"
    assert skill["license"] == "MIT"
    assert skill["url"] == "https://github.com/acme/dev-skills"
    assert skill["path"] == "skills/pytest-unit-testing/SKILL.md"
    # the harvested provenance comment is stripped; the body proper remains
    assert skill["body"].startswith("# Pytest unit testing")
    assert "Source:" not in skill["body"]
    assert lib.get("nope--missing") is None
    assert lib.get_meta("nope--missing") is None


def test_browse_paginates_and_filters_by_repo(lib):
    items, total = lib.browse(limit=2, offset=0)
    assert total == 6 and len(items) == 2
    items2, _ = lib.browse(limit=2, offset=2)
    assert {i["id"] for i in items}.isdisjoint({i["id"] for i in items2})
    dev, dev_total = lib.browse(repo="acme/dev-skills")
    assert dev_total == 3
    assert all(i["repo"] == "acme/dev-skills" for i in dev)


# ------------------------------------------------------------ search
def test_search_ranks_the_obviously_relevant_skill_first(lib):
    results, total = lib.search("python unit tests", limit=10)
    assert total >= 2
    assert results[0]["id"] == "pytest-unit-testing--a1"
    assert results[0]["score"] > 0
    seo, _ = lib.search("seo marketing headlines", limit=10)
    assert seo[0]["id"] == "seo-copywriting--b1"
    repo_only, _ = lib.search("python", repo="acme/growth-skills")
    assert all(r["repo"] == "acme/growth-skills" for r in repo_only)


def test_search_gibberish_and_stopwords_match_nothing(lib):
    assert lib.search("zxqwv ukjdf plmrf") == ([], 0)
    assert lib.search("the and of to") == ([], 0)
    assert lib.search("") == ([], 0)


# ------------------------------------------------------------ pins (store)
def test_pins_persist_and_are_tenant_scoped(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    assert store.pinned_skill_ids(TENANT) == set()
    store.set_skill_pin(TENANT, "seo-copywriting--b1", True, "tester")
    store.set_skill_pin(TENANT, "pytest-unit-testing--a1", True, "tester")
    assert store.pinned_skill_ids(TENANT) == {
        "seo-copywriting--b1", "pytest-unit-testing--a1"}
    # another tenant sees none of them
    assert store.pinned_skill_ids(OTHER) == set()
    pins = store.list_skill_pins(TENANT)
    assert {p["skill_id"] for p in pins} == store.pinned_skill_ids(TENANT)
    assert all(p["user_id"] == "tester" and p["pinned_at"] for p in pins)
    store.set_skill_pin(TENANT, "seo-copywriting--b1", False, "tester")
    assert store.pinned_skill_ids(TENANT) == {"pytest-unit-testing--a1"}


# ------------------------------------------------------------ retrieval
def _settings(tmp_path):
    from app.config import Settings
    return Settings(data_dir=tmp_path)


def test_retrieval_picks_the_coding_skill_for_a_coding_task(
        lib, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    found = retrieve_for_task(
        store, TENANT, "write unit tests for a python function",
        _settings(tmp_path), library=lib)
    assert [s["id"] for s in found] == ["pytest-unit-testing--a1"]
    assert found[0]["coverage"] >= 0.5 and not found[0]["pinned"]
    assert found[0]["body"].startswith("# Pytest unit testing")


def test_retrieval_returns_none_for_unrelated_or_gibberish(
        lib, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    # none of the fixture skills share a term with this task
    assert retrieve_for_task(
        store, TENANT, "translate a legal contract into french",
        _settings(tmp_path), library=lib) == []
    assert retrieve_for_task(
        store, TENANT, "zxqwv ukjdf", _settings(tmp_path),
        library=lib) == []


def test_retrieval_gate_is_explicit_and_tunable(lib, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    text = "write unit tests for a python function"
    # the debugging skill matches FTS (python) but covers only 1 of
    # the task's 4 key terms (0.25) -> the default coverage floor
    # (0.5) excludes it
    default = retrieve_for_task(store, TENANT, text, _settings(tmp_path),
                                library=lib)
    assert "python-debugging--a2" not in [s["id"] for s in default]
    # lowering the floor admits it, still ranked after the real match
    lowered = retrieve_for_task(store, TENANT, text, _settings(tmp_path),
                                library=lib, min_coverage=0.2)
    assert [s["id"] for s in lowered] == [
        "pytest-unit-testing--a1", "python-debugging--a2"]
    # an unreachable score floor admits nothing at all
    assert retrieve_for_task(store, TENANT, text, _settings(tmp_path),
                             library=lib, min_score=1e9) == []


def test_pinned_skill_is_offered_first_when_it_matches(lib, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.set_skill_pin(TENANT, "python-debugging--a2", True, "tester")
    found = retrieve_for_task(
        store, TENANT, "write unit tests for a python function",
        _settings(tmp_path), library=lib)
    # the pin matched the query at all (python), so it leads even
    # though its rank and coverage are below the natural winner's
    assert [s["id"] for s in found] == [
        "python-debugging--a2", "pytest-unit-testing--a1"]
    assert found[0]["pinned"] is True and found[1]["pinned"] is False
    # a pin that does not match the query at all is NOT injected
    store.set_skill_pin(TENANT, "sourdough-baking--c1", True, "tester")
    found = retrieve_for_task(
        store, TENANT, "write unit tests for a python function",
        _settings(tmp_path), library=lib)
    assert "sourdough-baking--c1" not in [s["id"] for s in found]


def test_retrieval_respects_top_k(lib, tmp_path):
    store = Store(str(tmp_path / "s.db"))
    found = retrieve_for_task(store, TENANT, "python testing debugging",
                              _settings(tmp_path), library=lib, k=1,
                              min_coverage=0.3)
    assert len(found) == 1


# ------------------------------------------------------------ section format
def test_format_section_is_provenance_first_and_budget_capped():
    big = {"id": "big--x1", "name": "big", "display_name": "Big Skill",
           "repo": "acme/dev-skills", "license": "MIT",
           "body": "alpha guidance. " * 2000}
    other = {"id": "other--x2", "name": "other", "display_name": "Other",
             "repo": "acme/growth-skills", "license": "Apache-2.0",
             "body": "beta guidance. " * 2000}
    section = format_skills_section([big, other], budget=4000)
    assert len(section) <= 4000
    assert section.startswith("## Relevant skills")
    # provenance header precedes the body it describes
    assert section.index("Source: acme/dev-skills") < \
        section.index("alpha guidance")
    assert "truncated" in section or "omitted" in section
    assert format_skills_section([]) == ""
