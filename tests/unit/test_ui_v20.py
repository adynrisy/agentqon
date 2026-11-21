"""Chat UI wiring for v21.2 (static check — same pattern as
test_ui_v15.py; pins the custom-agent registry UI and the named-button
sweep so a UI refactor cannot silently drop either)."""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_custom_agent_registry_ui_wired():
    html = _html()
    # the Add Agent entry point lives in the registry card, beside the
    # roster import, and opens the authoring form
    assert 'id="customAgentAddBtn"' in html
    assert 'name="custom-agent-add"' in html
    assert 'id="customAgentForm"' in html
    assert html.index('id="customAgentAddBtn"') < html.index('id="customAgentForm"')
    # the form fields: name, role, description, expertise, instructions
    for fid in ("ca-name", "ca-title", "ca-desc", "ca-instructions",
                "ca-error", "ca-status", "ca-save", "ca-cancel"):
        assert f'id="{fid}"' in html, fid
    assert 'class="ca-type"' in html
    for t in ("strategy", "research", "writing", "code", "plan", "qa", "general"):
        assert f'value="{t}"' in html, t
    # the JS talks to the custom-agents API and refreshes the registry
    assert "openCustomAgentForm" in html
    assert "saveCustomAgent" in html
    assert "editCustomAgent" in html
    assert "deleteCustomAgent" in html
    assert "/v1/agents/custom" in html
    # custom rows are visibly marked and carry Edit/Delete actions;
    # bundled roster rows get neither
    assert "customActions" in html
    assert '<span class="chip">custom</span>' in html
    assert 'name="custom-agent-edit"' in html
    assert 'name="custom-agent-delete"' in html
    assert "a.origin !== 'custom'" in html


def test_every_button_has_a_semantic_name():
    html = _html()
    tags = re.findall(r"<button\b[^>]*>", html)
    assert len(tags) >= 60, f"suspiciously few buttons found: {len(tags)}"
    # A button is semantically named if it carries a name attribute, an
    # aria-label (the icon-only convention, cf. the aria-label test
    # below), or — for picker option buttons — role="option" with its
    # data-value. The restyle's NEW controls fall in the latter two
    # classes: the scroll-to-bottom button (aria-label) and the
    # model-picker options (role + data-value + option text). A button
    # with none of the three is still anonymous and still fails.
    def _named(t):
        return (re.search(r'\bname="[^"]+"', t)
                or re.search(r'\baria-label="[^"]+"', t)
                or (re.search(r'\brole="option"', t)
                    and re.search(r'\bdata-value="', t)))
    unnamed = [t for t in tags if not _named(t)]
    assert not unnamed, f"buttons without a semantic name: {unnamed}"


def test_icon_only_buttons_have_aria_labels():
    html = _html()
    # the two icon-only controls (menu, send) are referenceable by
    # accessible name, not just by their title tooltip
    menu = re.search(r'<button[^>]*id="menuBtn"[^>]*>', html)
    send = re.search(r'<button[^>]*id="chatSend"[^>]*>', html)
    assert menu and 'aria-label="Menu"' in menu.group(0)
    assert send and 'aria-label="Send"' in send.group(0)
    # the send button is the task's reference example
    assert 'name="mission-send"' in send.group(0)
