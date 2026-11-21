"""Chat UI wiring for v17 (static check — same pattern as
test_ui_v16.py; pins the WhatsApp panel in Advanced so a UI refactor
cannot silently drop it)."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def test_whatsapp_panel_wired_in_advanced():
    html = INDEX.read_text(encoding="utf-8")
    assert "<h2>WhatsApp</h2>" in html
    assert 'id="waLinkBtn"' in html and 'id="waUnlinkBtn"' in html
    assert "loadWhatsApp" in html
    assert "/v1/channels/whatsapp/link-code" in html
    assert "/v1/channels/whatsapp/status" in html
    assert "/v1/channels/whatsapp/link" in html
    # refreshed with the rest of the dashboard panels
    assert "loadWhatsApp();" in html
