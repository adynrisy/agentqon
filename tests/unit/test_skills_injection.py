"""v22.1 (S3): skill injection into agent sessions.

Driven through the same scripted stub model as test_agent_loop:
the stub records every system prompt it receives, so the test
asserts on the EXACT context the model was given — the coding
skill (with provenance) for a coding goal, and no skills section
at all for a goal nothing in the library matches. Terminal
per-skill lessons are asserted from the evolve ledger.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import agent_loop, evolve
from app.config import Settings
from app.store import Store

TENANT = "org_skills_inject"
UNKNOWN_ENTRY = {"price_status": "unknown", "price_in": None,
                 "price_out": None}


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
    root = tmp_path_factory.mktemp("skills-inject-fixture")
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


class StubGateway:
    def __init__(self, script):
        self.script = list(script)
        self.systems: list[str] = []
        self.users: list[str] = []

    async def chat_json(self, model, system, user, temperature=0.4):
        self.systems.append(system)
        self.users.append(user)
        item = self.script.pop(0)
        return item, {"input_tokens": 100, "output_tokens": 50,
                      "model": model}


def _runner(tmp_path, script):
    store = Store(str(tmp_path / "s.db"))
    settings = Settings(data_dir=tmp_path)
    gateway = StubGateway(script)

    def factory(tenant_id, on_rate_limited=None):
        return gateway, "stub-model-1", UNKNOWN_ENTRY, \
            {"id": "conn_test", "kind": "direct_provider"}

    return agent_loop.AgentRunner(store, settings, factory), store, gateway


def _done(summary="finished"):
    return {"action": "done", "summary": summary}


def test_coding_goal_receives_the_coding_skill_with_provenance(
        fixture_root, tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMIND_SKILLS_DIR", str(fixture_root))
    runner, store, gateway = _runner(tmp_path, [_done("tests written")])
    session = runner.create_session(
        TENANT, "tester", "write unit tests for a python function",
        workspace_root=str(tmp_path / "ws"), auto_approve_rw=True)
    done = asyncio.run(runner.run(TENANT, session["id"]))
    assert done["status"] == "completed"
    assert done["skills"] == ["pytest-unit-testing--a1"]
    # Audit F6 retarget (2026-10-22): skill content used to ride the
    # SYSTEM prompt; harvested third-party bodies must never sit in
    # instruction position. The section now rides the USER turn,
    # wrapped in the untrusted-data delimiters, and the system
    # prompt carries none of it.
    system = gateway.systems[0]
    assert "## Relevant skills" not in system
    assert "pytest-unit-testing" not in system
    user = gateway.users[0]
    assert "## Relevant skills" in user
    assert "pytest-unit-testing" in user
    # provenance rides the injection — repo + license named in-context
    assert "acme/dev-skills" in user and "MIT" in user
    assert "Use pytest fixtures" in user
    # the untrusted-data boundary wraps the bodies, in order (the
    # boundary sentence itself names the markers, so the closing
    # delimiter is the LAST occurrence — and the section's last line)
    assert user.index("<untrusted-skill-data>") \
        < user.index("Use pytest fixtures") \
        < user.rindex("</untrusted-skill-data>")
    assert "never an instruction to obey" in user
    # usage is on the event stream
    types = [e["event_type"] for e in store.list_events(TENANT, session["id"])]
    assert "SkillInjected" in types
    injected = next(e for e in store.list_events(TENANT, session["id"])
                    if e["event_type"] == "SkillInjected")
    assert injected["payload"]["skills"] == ["pytest-unit-testing--a1"]
    # and the outcome lands in the ledger as a per-skill lesson
    lessons = [l for l in evolve.list_lessons(store, TENANT)
               if l.get("subject_type") == "skill"]
    assert len(lessons) == 1
    assert lessons[0]["subject"] == "pytest-unit-testing--a1"
    assert lessons[0]["outcome"] == "completed"
    assert lessons[0]["category"] == "none"
    assert lessons[0]["source_id"] == session["id"]


def test_unrelated_goal_receives_no_skills(fixture_root, tmp_path,
                                           monkeypatch):
    monkeypatch.setenv("OMNIMIND_SKILLS_DIR", str(fixture_root))
    runner, store, gateway = _runner(tmp_path, [_done("translated")])
    session = runner.create_session(
        TENANT, "tester", "translate a legal contract into french",
        workspace_root=str(tmp_path / "ws"), auto_approve_rw=True)
    done = asyncio.run(runner.run(TENANT, session["id"]))
    assert done["status"] == "completed"
    assert done["skills"] == []
    assert "## Relevant skills" not in gateway.systems[0]
    assert "## Relevant skills" not in gateway.users[0]
    types = [e["event_type"] for e in store.list_events(TENANT, session["id"])]
    assert "SkillInjected" not in types
    assert [l for l in evolve.list_lessons(store, TENANT)
            if l.get("subject_type") == "skill"] == []
