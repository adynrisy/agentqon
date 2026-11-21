"""v22.1 (S2/S3): the skills API over HTTP, plus mission-planning
injection end-to-end.

The catalog root is swapped for a fixture tree via the
OMNIMIND_SKILLS_DIR override (resolved per call in app/skills.py),
so ranking/gating stay deterministic. Missions run on the suite's
stub LLM (tests/conftest.py). Per-skill lessons are read back from
the app's own store — the TestClient serves in-process.
"""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app import evolve
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
def fixture_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("skills-api-fixture")
    _write_skill(
        root, "pytest-unit-testing--a1", "pytest-unit-testing",
        "Write unit tests with pytest for Python code: fixtures, "
        "assertions, coverage.",
        "# Pytest unit testing\nUse pytest fixtures and plain assert "
        "statements to test python functions. Run pytest -q and read "
        "the coverage report.")
    _write_skill(
        root, "seo-copywriting--b1", "seo-copywriting",
        "Marketing copywriting and SEO content: keywords, headlines.",
        "# SEO copywriting\nResearch keywords and write headlines "
        "for search marketing.", repo="acme/growth-skills")
    _write_skill(
        root, "sourdough-baking--c1", "sourdough-baking",
        "Bake sourdough bread: starter care, fermentation, crust.",
        "# Sourdough\nFeed the starter, bulk ferment, shape and "
        "bake for a crackling crust.", repo="acme/home-skills")
    return root


@pytest.fixture(autouse=True)
def _skills_env(fixture_root, monkeypatch):
    monkeypatch.setenv("OMNIMIND_SKILLS_DIR", str(fixture_root))


def _tenant() -> str:
    return f"org_skills_{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------ API shapes
def test_facets_reports_catalog_shape():
    r = client.get("/v1/skills/facets", headers=hdr(_tenant()))
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3 and j["unique_names"] == 3
    assert {x["repo"]: x["count"] for x in j["repos"]} == {
        "acme/dev-skills": 1, "acme/growth-skills": 1, "acme/home-skills": 1}


def test_browse_search_and_repo_filter():
    t = _tenant()
    j = client.get("/v1/skills", headers=hdr(t)).json()
    assert j["total"] == 3 and len(j["skills"]) == 3
    assert all(s["pinned"] is False for s in j["skills"])
    assert set(j["skills"][0]) >= {"id", "name", "display_name",
                                   "description", "repo", "license",
                                   "body_bytes", "pinned"}
    j = client.get("/v1/skills", params={"q": "python unit tests"},
                   headers=hdr(t)).json()
    assert j["skills"][0]["id"] == "pytest-unit-testing--a1"
    assert j["skills"][0]["score"] > 0
    j = client.get("/v1/skills", params={"repo": "acme/home-skills"},
                   headers=hdr(t)).json()
    assert j["total"] == 1
    assert j["skills"][0]["id"] == "sourdough-baking--c1"
    j = client.get("/v1/skills", params={"limit": 2, "offset": 2},
                   headers=hdr(t)).json()
    assert j["total"] == 3 and len(j["skills"]) == 1


def test_get_one_skill_full_shape_and_404():
    t = _tenant()
    r = client.get("/v1/skills/seo-copywriting--b1", headers=hdr(t))
    assert r.status_code == 200
    s = r.json()["skill"]
    assert s["name"] == "seo-copywriting" and s["pinned"] is False
    assert s["repo"] == "acme/growth-skills" and s["license"] == "MIT"
    assert s["url"] == "https://github.com/acme/growth-skills"
    assert s["path"] == "skills/seo-copywriting/SKILL.md"
    assert s["truncated"] is False
    assert s["body"].startswith("# SEO copywriting")
    r = client.get("/v1/skills/nope--missing", headers=hdr(t))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_pin_roundtrip_and_tenant_isolation():
    t, other = _tenant(), _tenant()
    r = client.post("/v1/skills/seo-copywriting--b1/pin",
                    json={"pinned": True}, headers=hdr(t))
    assert r.status_code == 200
    assert r.json() == {"skill_id": "seo-copywriting--b1", "pinned": True}
    mine = client.get("/v1/skills/seo-copywriting--b1",
                      headers=hdr(t)).json()["skill"]
    assert mine["pinned"] is True
    theirs = client.get("/v1/skills/seo-copywriting--b1",
                        headers=hdr(other)).json()["skill"]
    assert theirs["pinned"] is False
    r = client.post("/v1/skills/seo-copywriting--b1/pin",
                    json={"pinned": False}, headers=hdr(t))
    assert r.json()["pinned"] is False
    assert client.get("/v1/skills/seo-copywriting--b1",
                      headers=hdr(t)).json()["skill"]["pinned"] is False
    # unknown ids and malformed bodies are refused honestly
    assert client.post("/v1/skills/nope--missing/pin",
                       json={"pinned": True},
                       headers=hdr(t)).status_code == 404
    assert client.post("/v1/skills/seo-copywriting--b1/pin",
                       json={"pinned": {"not": "a bool"}},
                       headers=hdr(t)).status_code == 400


# ------------------------------------------------------------ mission S3
def _mission_body(objective: str) -> dict:
    return {"title": objective[:60], "contract": {
        "objective": objective,
        "deliverables": ["Decision brief"],
        "acceptance": ["Material claims cite sources"],
        "constraints": {"max_cost_usd": 10},
        "tools": {"allow": ["web.read", "artifact.write",
                            "artifact.read"], "deny": []},
        "approval_policy": {"external_side_effects": "required"},
        "data_classification": "internal"}}


def _run_mission(tenant: str, objective: str) -> str:
    created = client.post("/v1/missions", json=_mission_body(objective),
                          headers=hdr(tenant, f"sk-{uuid.uuid4().hex[:10]}"))
    assert created.status_code == 201, created.text
    mid = created.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 200, run.text
    return mid


def test_mission_planning_injects_the_matching_skill():
    tenant = _tenant()
    mid = _run_mission(tenant, "Write unit tests for a python module "
                               "and measure coverage")
    events = client.get(f"/v1/missions/{mid}/events",
                        headers=hdr(tenant)).json()["events"]
    injected = [e for e in events if e["event_type"] == "SkillInjected"]
    assert len(injected) == 1
    assert injected[0]["payload"]["skills"] == ["pytest-unit-testing--a1"]
    detail = client.get(f"/v1/missions/{mid}",
                        headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "completed"
    lessons = [l for l in evolve.list_lessons(main_mod.store, tenant)
               if l.get("subject_type") == "skill"]
    assert len(lessons) == 1
    assert lessons[0]["subject"] == "pytest-unit-testing--a1"
    assert lessons[0]["outcome"] == "completed"
    assert lessons[0]["category"] == "none"
    assert lessons[0]["source_id"] == mid


def test_mission_with_unrelated_objective_injects_nothing():
    tenant = _tenant()
    mid = _run_mission(tenant, "Translate a legal contract into French")
    events = client.get(f"/v1/missions/{mid}/events",
                        headers=hdr(tenant)).json()["events"]
    assert [e for e in events
            if e["event_type"] == "SkillInjected"] == []
    assert [l for l in evolve.list_lessons(main_mod.store, tenant)
            if l.get("subject_type") == "skill"] == []
