"""Chat UI wiring for v21.3 (static check — same pattern as
test_ui_v15.py; pins the simplification release: two-flavor
connections, no user-facing budgets, collapsible forms)."""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_connection_kind_select_has_exactly_two_flavors():
    html = _html()
    select = re.search(r'<select id="cKind">(.*?)</select>', html, re.S)
    assert select, "cKind select missing"
    options = re.findall(r'<option value="([^"]+)">([^<]*)</option>', select.group(1))
    # Local first, then the API-key (BYOK) flavor — nothing else.
    assert [v for v, _ in options] == ["local_endpoint", "direct_provider"]
    assert "AI app on this computer (no key)" in options[0][1]  # redesign: plain-words kind label
    assert "My own key" in options[1][1]  # redesign: plain-words kind label (was "API key (BYOK)")


def test_no_user_facing_budget_controls():
    html = _html()
    # The chat budget picker, the form budget input and the
    # budget-vs-spent bar are gone, and the UI sends no budget at all.
    for gone in ('id="chatBudget"', 'id="budget"', 'id="mBudgetTxt"',
                 'id="mBudgetBar"', "max_cost_usd", "Spent / Budget"):
        assert gone not in html, gone


def test_custom_agent_form_starts_collapsed():
    html = _html()
    tag = re.search(r'<div id="customAgentForm"[^>]*>', html)
    assert tag and "display:none" in tag.group(0)
    # ...and the open/cancel/save wiring that collapses it again.
    assert "openCustomAgentForm" in html
    assert "$('customAgentForm').style.display = 'none'" in html


def test_no_mission_creation_form_in_advanced():
    # UPDATED 2026-10-22 (was: test_new_mission_more_options_starts_
    # collapsed, pinning the Advanced "New mission" form and its
    # collapsed More-options section). Zohaib ordered the creation
    # card removed from Advanced — "remove the mission thing, it
    # must be in the chat box which is already there" — so mission
    # creation now lives ONLY in the composer's Mission tab. This
    # pin now guards the removal: the form, its toggle, its fields
    # and its handlers stay gone, and the composer tab stays.
    html = _html()
    for gone in ('id="cardNew"', 'id="missionMoreOptions"',
                 'name="mission-more-options"', 'id="moreOptionsBtn"',
                 'id="presetBtn"', 'id="runBtn"', 'id="runStatus"',
                 'id="objective"', 'id="deliverables"', 'id="acceptance"',
                 'id="classification"', 'id="taskType"', 'id="projectSel"',
                 'id="connSel"', 'id="routingMode"', 'id="pinnedModel"',
                 "loadProjects"):
        assert gone not in html, gone
    assert 'id="modeMissionBtn"' in html and 'name="mode-mission"' in html, \
        "mission creation lives in the composer now"
