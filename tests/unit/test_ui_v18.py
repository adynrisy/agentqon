"""Chat UI wiring for v18 (static check — same pattern as
test_ui_v16/v17; pins the Embedded Signup connect block in the
WhatsApp panel so a UI refactor cannot silently drop it)."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def test_embedded_signup_connect_block_wired():
    html = INDEX.read_text(encoding="utf-8")
    # the connect block sits in the WhatsApp card, above the
    # link-code panel
    assert 'id="waConnectPanel"' in html
    assert html.index('id="waConnectPanel"') < html.index('id="whatsappPanel"')
    assert "loadWhatsAppConnect" in html
    # refreshed together with the WhatsApp panel
    assert "loadWhatsAppConnect();" in html
    # the one-click flow: public config -> FB SDK -> FB.login with
    # the Login-for-Business config id -> WA_EMBEDDED_SIGNUP session
    # info -> server exchange; connected state offers Disconnect
    assert "/v1/whatsapp/onboard/public-config" in html
    assert "/v1/whatsapp/onboard/exchange" in html
    assert "/v1/whatsapp/connection" in html
    assert "https://connect.facebook.net/en_US/sdk.js" in html
    assert "Connect WhatsApp with Facebook" in html
    assert "FB.login" in html and "config_id" in html
    assert "WA_EMBEDDED_SIGNUP" in html
    assert "waDisconnectBtn" in html
    # the not-configured fallback keeps the manual path usable
    assert "use the link code below" in html
