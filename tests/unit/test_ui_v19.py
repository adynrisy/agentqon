"""Chat UI wiring for v21.1 (static check — same pattern as
test_ui_v15.py; pins the boot gate + watchdog so a UI refactor cannot
silently drop them and bring back the blank-page failure)."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def test_boot_gate_markup_present_from_first_paint():
    html = INDEX.read_text(encoding="utf-8")
    # the gate is static markup, ahead of the app shell in the body —
    # it paints before any script runs
    assert 'id="bootGate"' in html
    assert html.index('id="bootGate"') < html.index('id="shell"')
    # loading state + recovery state both live inside the gate
    assert "Loading your workspace" in html
    assert 'id="bootErrMsg"' in html
    assert 'id="bootRetry"' in html
    # recovery names the exact diagnose command
    assert "agentqon doctor" in html


def test_boot_watchdog_armed_and_disarmed():
    html = INDEX.read_text(encoding="utf-8")
    # watchdog is armed in <head>, before the app script can fail
    assert "__agentqonBootTimer" in html
    assert "__agentqonBootFail" in html
    assert html.index("__agentqonBootTimer") < html.index("loadModelState();")
    # the app disarms it only at the very end of a successful boot
    assert "__agentqonBooted = true" in html
    assert "clearTimeout(window.__agentqonBootTimer)" in html
    assert html.index("__agentqonBooted = true") > html.index("setView('chat'); renderRecent();")
