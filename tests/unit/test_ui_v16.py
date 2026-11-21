"""Chat UI wiring for v16 (static check — same pattern as
test_ui_v15.py; pins the Telegram panel in Advanced so a UI refactor
cannot silently drop it)."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def test_telegram_panel_wired_in_advanced():
    html = INDEX.read_text(encoding="utf-8")
    assert "<h2>Telegram</h2>" in html
    assert 'id="tgLinkBtn"' in html and 'id="tgUnlinkBtn"' in html
    assert "loadTelegram" in html
    assert "/v1/channels/telegram/link-code" in html
    assert "/v1/channels/telegram/status" in html
    assert "/v1/channels/telegram/link" in html
    # refreshed with the rest of the dashboard panels
    assert "loadTelegram();" in html
