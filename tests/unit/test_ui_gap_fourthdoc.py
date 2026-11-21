"""W9 UI contracts — the workbench surface in apps/web/index.html.

Contract tests pin the shipped source: command palette (Ctrl+K), the
in-app notification center, the Workbench card inside Advanced (no new
sidebar entry — the navigation rule stands), light/system themes,
high-contrast and reduced-motion options, draft autosave surface, and
the deletion-honesty wording. No new sidebar button is added: the
sidebar keeps exactly Chat, Missions, Agents, Simulations, Skills,
Advanced.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_palette_present_with_shortcut_and_role_note():
    html = _html()
    assert 'id="wbPalette"' in html
    assert 'id="wbPaletteInput"' in html
    assert "Ctrl+K" in html
    assert "Only actions you are allowed to run are listed" in html
    assert "wbOpenPalette" in html or "wbPaletteBtn" in html


def test_notification_center_in_app_only():
    html = _html()
    assert 'id="wbNotifPanel"' in html
    assert 'id="wbNotifList"' in html
    assert "kept in this app only" in html
    assert "Nothing is pushed to any chat app" in html


def test_workbench_card_in_advanced_not_sidebar():
    html = _html()
    assert 'id="cardWorkbench"' in html
    sidebar = html[html.index('<aside id="sidebar"'):html.index("</aside>")]
    assert "cardWorkbench" not in sidebar
    assert "wbPaletteBtn" not in sidebar
    for label in ("Chat", "Missions", "Agents", "Simulations", "Skills", "Advanced"):
        assert label in sidebar


def test_display_options_light_system_contrast_motion():
    html = _html()
    assert 'data-wb-theme="light"' in html
    assert "Follow the device" in html
    assert 'id="wbTheme"' in html and 'id="wbTextSize"' in html
    assert 'id="wbContrast"' in html and 'id="wbMotion"' in html
    assert 'data-wb-motion="reduced"' in html


def test_draft_surface_and_approvals_wording():
    html = _html()
    assert 'id="wbDraftText"' in html
    assert "drafts survive a refresh or a crash" in html
    assert "approve once, approve for this mission, reject, or inspect" in html
    assert "never stops a mission" in html


def test_deletion_honesty_and_cost_wording():
    html = _html()
    assert "Exports you already downloaded" in html
    assert "records held by providers" in html
    # The two cost sentences below were the "Costs" section's sub, removed
    # with the whole Costs section by the 2026-10-26 spend-display removal
    # (bb16f2c4): no spendings are shown anywhere, so they are stale, not
    # honesty wording to restore. Deletion honesty above is what remains.


def test_workbench_block_introduces_no_forbidden_brands():
    html = _html()
    block = html[html.index('id="cardWorkbench"'):]
    block = block[:block.index('id="cardAdmin"')]
    assert "ToolMere" not in block
    assert "OmniMind" not in block


def test_no_toolmere_anywhere_in_ui():
    assert "ToolMere" not in _html()


def test_concept_separation_stated():
    html = _html()
    assert "It is not the same as a conversation" in html
    assert "a mission (one job)" in html
    assert "an artifact (one output file)" in html
