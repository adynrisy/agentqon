"""W4 §4 (gap-memory) — the skill retrieval pipeline.

Covers: name normalization, exact-match prioritization,
synonym expansion (recall only), category filtering, dependency
availability, duplicate-candidate suppression, stable ordering,
retrieval confidence (estimate, never a probability), the
minimum-relevance threshold, size estimation, dependency cycle
removal, caching, use telemetry, dynamic selection (count from
the task, never fixed), and gap re-retrieval.
"""
import json
from pathlib import Path

import pytest

from app import skill_retrieval as sr
from app.config import Settings
from app.skill_lifecycle import SkillRegistry
from app.skills import SkillLibrary, retrieve_for_task
from app.store import Store

TENANT = "org_retrieval_test"


def _write_skill(root: Path, folder: str, name: str, description: str,
                 body: str, repo: str = "acme/dev-skills",
                 license_: str = "MIT", **meta):
    d = root / folder
    d.mkdir(parents=True)
    (d / "skill.md").write_text(
        f"<!--\nSource: {repo}\nLicense: {license_}\n-->\n\n{body}",
        encoding="utf-8")
    meta_out = {"name": name, "display_name": name,
                "description": description, "repo": repo,
                "license": license_, "body_bytes": len(body),
                "truncated": False}
    meta_out.update(meta)
    (d / "meta.json").write_text(json.dumps(meta_out), encoding="utf-8")
    return folder


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
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
        "drift, and risky shortcuts.", category="engineering")
    _write_skill(
        root, "code-review--d2", "code-review",
        "A second, stricter code review checklist.",
        "# Code review checklist\nCorrectness first, then style, "
        "then risk sign-off.", repo="acme/quality-skills",
        category="marketing")
    _write_skill(
        root, "bug-repair--e1", "bug-repair",
        "Repair broken Python code: reproduce, isolate, repair.",
        "# Bug repair\nReproduce the bug, isolate the failing line, "
        "repair it, and verify with a test.")
    return SkillLibrary(root, tmp_path / "idx")


def _settings(tmp_path):
    return Settings(data_dir=tmp_path)


def _store(tmp_path):
    return Store(str(tmp_path / "s.db"))


def test_name_normalization_and_exact_match_flag(lib, tmp_path):
    assert sr.normalize_skill_name("Code_Review!") == "code-review"
    # Importing the lifecycle helper under its own name works too.
    from app.skill_lifecycle import normalize_skill_name
    assert normalize_skill_name("Code Review") == "code-review"
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "seo copywriting",
                                 _settings(tmp_path), library=lib)
    assert result["skills"]
    assert result["skills"][0]["id"] == "seo-copywriting--b1"
    assert result["skills"][0]["exact_match"] is True


def test_synonym_expansion_is_recall_only(lib, tmp_path):
    assert "repair" in sr._expand_terms(["fix"])
    assert "debugging" in sr._expand_terms(["debug"])
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "fix python code",
                                 _settings(tmp_path), library=lib)
    assert "bug-repair--e1" in [s["id"] for s in result["skills"]]
    # The floors still run on the original terms: an unrelated
    # task is never filled with unrelated skills.
    assert sr.retrieve_dynamic(store, TENANT, "zxqwv ukjdf",
                               _settings(tmp_path),
                               library=lib)["skills"] == []
    assert retrieve_for_task(store, TENANT, "translate a legal "
                             "contract into french",
                             _settings(tmp_path), library=lib) == []


def test_category_filter_and_dependency_availability(lib, tmp_path):
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "code review changes",
                                 _settings(tmp_path), library=lib,
                                 category="engineering")
    ids = [s["id"] for s in result["skills"]]
    assert "code-review--d1" in ids
    assert "code-review--d2" not in ids
    assert any(r["skill_id"] == "code-review--d2"
               for r in result["removed"])
    # A skill with a missing dependency is removed, with a reason.
    from app.skill_lifecycle import check_dependencies
    needy = {"id": "needy--n1", "dependencies": ["missing--x"]}
    check = check_dependencies(needy, {"code-review--d1"})
    assert check["missing"] == ["missing--x"]


def test_duplicate_suppression_and_stable_order(lib, tmp_path):
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "code review checklist",
                                 _settings(tmp_path), library=lib)
    ids = [s["id"] for s in result["skills"]]
    # The two code-review skills share a normalized name: only
    # the stronger one is selected; the other is named in removed.
    assert len([i for i in ids if i.startswith("code-review")]) == 1
    assert any("Near-duplicate" in r["reason"]
               for r in result["removed"])
    sr.clear_telemetry()
    first = sr.retrieve_dynamic(store, TENANT, "code review checklist",
                                _settings(tmp_path), library=lib)
    second = sr.retrieve_dynamic(store, TENANT, "code review checklist",
                                 _settings(tmp_path), library=lib)
    assert [s["id"] for s in first["skills"]] == \
        [s["id"] for s in second["skills"]]
    assert second["cache_hit"] is True


def test_confidence_estimate_and_size_estimation(lib, tmp_path):
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT,
                                 "write unit tests for a python function",
                                 _settings(tmp_path), library=lib)
    assert result["skills"]
    skill = result["skills"][0]
    assert 0.0 <= skill["confidence"] <= 1.0
    assert "not a measured probability" in skill["confidence_note"]
    assert skill["estimated_chars"] == len(skill["body"])
    assert result["cost_estimate"]["total_chars"] == sum(
        s["estimated_chars"] for s in result["skills"])
    assert result["cost_estimate"]["total_chars"] <= \
        result["cost_estimate"]["budget_chars"]


def test_dependency_cycle_removes_both_skills(tmp_path):
    root = tmp_path / "cyc"
    root.mkdir()
    _write_skill(root, "cyc-a--a1", "cyc-a",
                 "Cycle A: needs B. Cycle alpha beta.",
                 "# Cycle A\nNeeds cyc-b. Cycle alpha beta.",
                 dependencies=["cyc-b--b1"])
    _write_skill(root, "cyc-b--b1", "cyc-b",
                 "Cycle B: needs A. Cycle alpha beta.",
                 "# Cycle B\nNeeds cyc-a. Cycle alpha beta.",
                 dependencies=["cyc-a--a1"])
    lib = SkillLibrary(root, tmp_path / "idx")
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "cycle alpha beta",
                                 _settings(tmp_path), library=lib)
    assert result["skills"] == []
    assert any("cycle" in r["reason"].lower()
               for r in result["removed"])


def test_caching_invalidates_on_pin_change(lib, tmp_path):
    store = _store(tmp_path)
    sr.clear_telemetry()
    first = sr.retrieve_dynamic(store, TENANT,
                                "write unit tests for a python function",
                                _settings(tmp_path), library=lib)
    assert first["cache_hit"] is False
    second = sr.retrieve_dynamic(store, TENANT,
                                 "write unit tests for a python function",
                                 _settings(tmp_path), library=lib)
    assert second["cache_hit"] is True
    store.set_skill_pin(TENANT, "python-debugging--a2", True, "tester")
    third = sr.retrieve_dynamic(store, TENANT,
                                "write unit tests for a python function",
                                _settings(tmp_path), library=lib)
    assert third["cache_hit"] is False
    assert third["skills"][0]["id"] == "python-debugging--a2"


def test_use_telemetry_and_gap_reretrieval(lib, tmp_path):
    store = _store(tmp_path)
    sr.clear_telemetry(TENANT)
    result = sr.retrieve_dynamic(store, TENANT,
                                 "write unit tests for a python function",
                                 _settings(tmp_path), library=lib)
    picked = result["skills"][0]["id"]
    assert sr.was_skill_used(TENANT, picked) is False
    sr.mark_skill_used(TENANT, picked, mission_id="m1")
    assert sr.was_skill_used(TENANT, picked) is True
    assert sr.telemetry_for(TENANT)["used"][picked] == 1
    more = sr.retrieve_more(store, TENANT, "python testing debugging",
                            _settings(tmp_path), library=lib,
                            already_loaded=[picked])
    assert picked not in [s["id"] for s in more["skills"]]


def test_dynamic_selection_count_follows_the_task(lib, tmp_path):
    store = _store(tmp_path)
    sr.clear_telemetry()
    narrow = sr.retrieve_dynamic(store, TENANT, "sourdough starter",
                                 _settings(tmp_path), library=lib)
    assert narrow["effective_k"] == 2
    broad = sr.retrieve_dynamic(store, TENANT,
                                "python testing debugging code review",
                                _settings(tmp_path), library=lib)
    assert broad["effective_k"] == 3  # capped by the default cap
    assert broad["effective_k"] > narrow["effective_k"]
    # And the cap itself is honoured: k=1 selects exactly one.
    capped = sr.retrieve_dynamic(store, TENANT,
                                 "python testing debugging",
                                 _settings(tmp_path), library=lib, k=1,
                                 min_coverage=0.3)
    assert len(capped["skills"]) == 1


def test_deprecated_skill_removed_with_replacement_named(lib, tmp_path):
    reg = SkillRegistry()
    reg.mark_deprecated("seo-copywriting--b1",
                        replacement_id="seo-copywriting--b2")
    store = _store(tmp_path)
    result = sr.retrieve_dynamic(store, TENANT, "seo copywriting",
                                 _settings(tmp_path), library=lib,
                                 registry=reg)
    assert result["skills"] == []
    assert any("seo-copywriting--b2" in r["reason"]
               for r in result["removed"])
    kept = sr.retrieve_dynamic(store, TENANT, "seo copywriting",
                               _settings(tmp_path), library=lib,
                               registry=reg, include_deprecated=True)
    assert kept["skills"]
