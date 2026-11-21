"""WhatsApp front door (v17) unit tests: webhook authenticity
(HMAC signature, GET handshake), the 24-hour window computation,
payload parsing (text / button replies / statuses ignored), wamid
dedupe, the LINK-code lifecycle, and the outbox's template-vs-freeform
vs-held decisions driven by the window."""
import hashlib
import hmac
import tempfile
from datetime import datetime, timedelta, timezone

import asyncio

from app import whatsapp as wa
from app.config import Settings
from app.store import Store

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
SECRET = "app-secret-for-tests"
WA_ID = "15557654321"


def _store(tmp):
    return Store(f"{tmp}/t.db", f"{tmp}/artifacts")


def _cfg(**over):
    base = {"whatsapp_token": "tok", "whatsapp_phone_number_id": "12345",
            "whatsapp_app_secret": SECRET, "whatsapp_verify_token": "vtok"}
    base.update(over)
    return Settings(**base)


class FakeClient:
    """Captures outbound calls the way the stub server does, in-process."""

    def __init__(self):
        self.texts = []      # (to, body)
        self.buttons = []    # (to, body, [(id, title)])
        self.templates = []  # (to, name, lang, params)
        self.documents = []  # (to, media_id, filename)
        self.uploads = []    # (filename, bytes)

    def send_text(self, to, body):
        self.texts.append((to, body))
        return {"messages": [{"id": "wamid.fake"}]}

    def send_buttons(self, to, body, buttons):
        self.buttons.append((to, body, list(buttons)))
        return {"messages": [{"id": "wamid.fake"}]}

    def send_template(self, to, name, language, body_params=None):
        self.templates.append((to, name, language, list(body_params or [])))
        return {"messages": [{"id": "wamid.fake"}]}

    def upload_media(self, filename, data, mime="text/markdown"):
        self.uploads.append((filename, data))
        return "media_fake_1"

    def send_document(self, to, media_id, filename, caption=None):
        self.documents.append((to, media_id, filename))
        return {"messages": [{"id": "wamid.fake"}]}


# ------------------------------------------------------------ signature
def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body,
                                hashlib.sha256).hexdigest()


def test_signature_accepts_valid_hmac():
    body = b'{"entry":[]}'
    assert wa.verify_signature(SECRET, body, _sign(SECRET, body)) is True


def test_signature_rejects_tampering_and_bad_forms():
    body = b'{"entry":[]}'
    assert wa.verify_signature(SECRET, body, _sign(SECRET, body + b" ")) is False
    assert wa.verify_signature(SECRET, body, _sign("other", body)) is False
    assert wa.verify_signature(SECRET, body, None) is False
    assert wa.verify_signature(SECRET, body, "") is False
    # no sha256= scheme prefix -> not Meta's form
    bare = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert wa.verify_signature(SECRET, body, bare) is False
    # no secret configured -> never "verified" (endpoint decides policy)
    assert wa.verify_signature("", body, _sign(SECRET, body)) is False


# ------------------------------------------------------------ handshake
def test_handshake_returns_challenge_only_for_the_right_token():
    params = {"hub.mode": "subscribe", "hub.verify_token": "vtok",
              "hub.challenge": "987654"}
    assert wa.verify_handshake(params, "vtok") == "987654"
    assert wa.verify_handshake({**params, "hub.verify_token": "no"}, "vtok") is None
    assert wa.verify_handshake({**params, "hub.mode": "unsubscribe"}, "vtok") is None
    assert wa.verify_handshake(params, "") is None


# ------------------------------------------------------------ window
def test_window_open_within_24h_of_last_inbound():
    link = {"last_inbound_at": wa._iso(T0)}
    assert wa.window_open(link, now=T0) is True
    assert wa.window_open(link, now=T0 + timedelta(hours=23, minutes=59)) is True


def test_window_closes_at_24h():
    link = {"last_inbound_at": wa._iso(T0)}
    assert wa.window_open(link, now=T0 + timedelta(hours=24)) is False
    assert wa.window_open(link, now=T0 + timedelta(days=7)) is False


def test_window_shut_without_inbound_or_link():
    assert wa.window_open({}, now=T0) is False
    assert wa.window_open({"last_inbound_at": None}, now=T0) is False
    assert wa.window_open(None, now=T0) is False


# ------------------------------------------------------------ parsing
def _payload(messages, statuses=None):
    value = {"messaging_product": "whatsapp",
             "contacts": [{"wa_id": WA_ID, "profile": {"name": "Zohaib"}}],
             "messages": messages}
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [
                {"field": "messages", "value": value}]}]}


def test_parse_text_message():
    events = wa.parse_webhook_payload(_payload([
        {"from": WA_ID, "id": "wamid.1", "timestamp": "1767225600",
         "type": "text", "text": {"body": "research the market"}}]))
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "text" and e["text"] == "research the market"
    assert e["wa_id"] == WA_ID and e["wamid"] == "wamid.1"
    assert e["name"] == "Zohaib"


def test_parse_button_reply():
    events = wa.parse_webhook_payload(_payload([
        {"from": WA_ID, "id": "wamid.2", "timestamp": "1767225600",
         "type": "interactive",
         "interactive": {"type": "button_reply",
                         "button_reply": {"id": "pp:mis_123",
                                          "title": "🧾 Proof Pack"}}}]))
    assert len(events) == 1
    assert events[0]["kind"] == "button"
    assert events[0]["button_id"] == "pp:mis_123"


def test_parse_ignores_statuses_and_unsupported_types():
    events = wa.parse_webhook_payload(_payload(
        [{"from": WA_ID, "id": "wamid.3", "timestamp": "1",
          "type": "image", "image": {"id": "img1"}}],
        statuses=[{"id": "wamid.0", "status": "delivered"}]))
    assert events == []
    assert wa.parse_webhook_payload({}) == []
    assert wa.parse_webhook_payload({"entry": []}) == []


def test_parse_link_text():
    assert wa.parse_link_text("LINK abc123") == "abc123"
    assert wa.parse_link_text("  link abc123  ") == "abc123"
    assert wa.parse_link_text("Link AbC") == "AbC"
    assert wa.parse_link_text("LINK") is None
    assert wa.parse_link_text("please LINK abc") is None
    assert wa.parse_link_text("LINK two words") is None
    assert wa.parse_link_text("hello") is None


# ------------------------------------------------------------ link codes
def test_wamid_dedupe_at_store_level():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        assert store.mark_whatsapp_update("wamid.X") is True
        assert store.mark_whatsapp_update("wamid.X") is False
        assert store.mark_whatsapp_update("wamid.Y") is True


def test_link_code_redeem_binds_number_and_opens_window():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        rec = wa.new_link_code(store, "org_t", "user_1", "member",
                               cfg=_cfg(), now=T0)
        assert rec["wa_link"] is None  # no business number configured
        link = wa._redeem_code(store, rec["code"], WA_ID, "Zohaib", now=T0)
        assert link["tenant_id"] == "org_t" and link["user_id"] == "user_1"
        assert link["role"] == "member"
        assert link["last_inbound_at"] == wa._iso(T0)  # window starts open
        assert store.get_whatsapp_link(WA_ID)["user_id"] == "user_1"
        # single-use
        assert wa._redeem_code(store, rec["code"], "15550000000",
                               "Other", now=T0) is None


def test_link_code_expires_and_new_code_replaces_old():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        rec = wa.new_link_code(store, "org_t", "user_1", "owner",
                               cfg=_cfg(), now=T0)
        later = T0 + timedelta(minutes=wa.LINK_CODE_TTL_MINUTES + 1)
        assert wa._redeem_code(store, rec["code"], WA_ID, "Z",
                               now=later) is None
        assert store.get_whatsapp_code(rec["code"]) is None
        first = wa.new_link_code(store, "org_t", "user_1", "owner",
                                 cfg=_cfg(), now=T0)
        second = wa.new_link_code(store, "org_t", "user_1", "owner",
                                  cfg=_cfg(), now=T0)
        assert wa._redeem_code(store, first["code"], WA_ID, "Z", now=T0) is None
        assert wa._redeem_code(store, second["code"], WA_ID, "Z",
                               now=T0) is not None


def test_wa_link_built_from_business_number():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        cfg = _cfg(whatsapp_business_number="15550001111")
        rec = wa.new_link_code(store, "org_t", "user_1", "owner",
                               cfg=cfg, now=T0)
        assert rec["wa_link"] == (
            f"https://wa.me/15550001111?text=LINK%20{rec['code']}")
        assert rec["business_number"] == "15550001111"


# ------------------------------------------------------------ outbox decisions
def _linked_store_with_completed_mission(tmp, inbound_at):
    """A linked number + a completed whatsapp mission awaiting push;
    the link's window state is set by inbound_at."""
    store = _store(tmp)
    rec = wa.new_link_code(store, "org_t", "user_1", "owner",
                           cfg=_cfg(), now=T0)
    link = wa._redeem_code(store, rec["code"], WA_ID, "Zohaib", now=T0)
    outcome = wa.create_whatsapp_mission(store, link,
                                         "Compare launch strategies")
    mission = outcome["mission"]
    store.set_mission_status("org_t", mission["id"], "completed")
    link = store.get_whatsapp_link(WA_ID)
    link["last_inbound_at"] = wa._iso(inbound_at)
    store.put_whatsapp_link(link)
    return store, link, mission


def test_tick_in_window_pushes_full_result_with_buttons():
    with tempfile.TemporaryDirectory() as tmp:
        store, link, mission = _linked_store_with_completed_mission(tmp, T0)
        fake = FakeClient()
        out = wa.tick_whatsapp_outbox(store, cfg=_cfg(), client=fake,
                                      now=T0 + timedelta(hours=1))
        assert [p["mission_id"] for p in out["pushed"]] == [mission["id"]]
        assert out["alerted"] == [] and out["held"] == [] and out["errors"] == []
        assert fake.texts and "Mission completed" in fake.texts[0][1]
        # exactly one buttons message: Proof Pack + New Duel
        assert len(fake.buttons) == 1
        ids = [rid for rid, _ in fake.buttons[0][2]]
        assert ids == [f"pp:{mission['id']}", f"duel:{mission['id']}"]
        titles = [t for _, t in fake.buttons[0][2]]
        assert all(len(t) <= 20 for t in titles)
        rec = store.get_whatsapp_outbox("org_t",
                                        f"mission_result:{mission['id']}")
        assert rec["status"] == "sent"


def test_tick_out_of_window_with_template_alerts_then_reply_delivers():
    with tempfile.TemporaryDirectory() as tmp:
        store, link, mission = _linked_store_with_completed_mission(tmp, T0)
        fake = FakeClient()
        cfg = _cfg(whatsapp_template_name="mission_result_alert",
                   whatsapp_template_lang="en")
        out = wa.tick_whatsapp_outbox(store, cfg=cfg, client=fake,
                                      now=T0 + timedelta(hours=30))
        assert out["pushed"] == [] and out["errors"] == []
        assert [a["mission_id"] for a in out["alerted"]] == [mission["id"]]
        # the ONLY send is the template, carrying the mission title
        assert fake.texts == [] and fake.buttons == []
        assert len(fake.templates) == 1
        to, name, lang, params = fake.templates[0]
        assert to == WA_ID and name == "mission_result_alert" and lang == "en"
        assert params == [mission["title"]]
        rec = store.get_whatsapp_outbox("org_t",
                                        f"mission_result:{mission['id']}")
        assert rec["status"] == "alerted"
        # a second tick with the window still shut does NOT re-alert
        out2 = wa.tick_whatsapp_outbox(store, cfg=cfg, client=fake,
                                       now=T0 + timedelta(hours=31))
        assert out2["alerted"] == [] and len(fake.templates) == 1
        # the user replies (window reopens): the held full result lands
        link = store.get_whatsapp_link(WA_ID)
        link["last_inbound_at"] = wa._iso(T0 + timedelta(hours=32))
        store.put_whatsapp_link(link)
        delivered = wa.deliver_pending_for_link(
            store, fake, link, now=T0 + timedelta(hours=32))
        assert delivered == [mission["id"]]
        assert fake.texts and "Mission completed" in fake.texts[0][1]
        assert len(fake.buttons) == 1
        rec = store.get_whatsapp_outbox("org_t",
                                        f"mission_result:{mission['id']}")
        assert rec["status"] == "sent"


def test_tick_out_of_window_without_template_holds_until_next_inbound():
    with tempfile.TemporaryDirectory() as tmp:
        store, link, mission = _linked_store_with_completed_mission(tmp, T0)
        fake = FakeClient()
        out = wa.tick_whatsapp_outbox(store, cfg=_cfg(), client=fake,
                                      now=T0 + timedelta(hours=30))
        assert [h["mission_id"] for h in out["held"]] == [mission["id"]]
        assert fake.texts == [] and fake.templates == [] and fake.buttons == []
        rec = store.get_whatsapp_outbox("org_t",
                                        f"mission_result:{mission['id']}")
        assert rec["status"] == "held"
        # next inbound reopens the window -> flush delivers in full
        link = store.get_whatsapp_link(WA_ID)
        link["last_inbound_at"] = wa._iso(T0 + timedelta(hours=40))
        store.put_whatsapp_link(link)
        delivered = wa.deliver_pending_for_link(
            store, fake, link, now=T0 + timedelta(hours=40))
        assert delivered == [mission["id"]]
        assert fake.texts and len(fake.buttons) == 1


def test_tick_is_noop_when_channel_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        out = wa.tick_whatsapp_outbox(store, cfg=Settings(), client=FakeClient(),
                                      now=T0)
        assert out["skipped"] == "whatsapp not configured"
        assert out["pushed"] == []


# ------------------------------------------------------------ inbound handling
def _text_event(wamid, text, wa_id=WA_ID, name="Zohaib"):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages",
                        "value": {
                            "contacts": [{"wa_id": wa_id,
                                          "profile": {"name": name}}],
                            "messages": [{"from": wa_id, "id": wamid,
                                          "timestamp": "1", "type": "text",
                                          "text": {"body": text}}]}}]}]}


def test_unlinked_number_gets_link_prompt_and_link_via_text():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        fake = FakeClient()
        r = asyncio.run(wa.handle_payload(
            store, None, _text_event("wamid.a", "do research for me"),
            cfg=_cfg(), client=fake, now=T0))
        assert r["ok"] is True and r["processed"] == 1
        assert len(fake.texts) == 1 and "isn't linked" in fake.texts[0][1]
        assert store.list_missions("org_t") == []
        # LINK <code> binds the number and greets
        rec = wa.new_link_code(store, "org_t", "user_1", "member",
                               cfg=_cfg(), now=T0)
        asyncio.run(wa.handle_payload(
            store, None, _text_event("wamid.b", f"LINK {rec['code']}"),
            cfg=_cfg(), client=fake, now=T0))
        link = store.get_whatsapp_link(WA_ID)
        assert link is not None and link["user_id"] == "user_1"
        assert any("Linked" in t for _, t in fake.texts)


def test_duplicate_wamid_is_not_reprocessed():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        fake = FakeClient()
        event = _text_event("wamid.dup", "hello")
        first = asyncio.run(wa.handle_payload(store, None, event,
                                              cfg=_cfg(), client=fake, now=T0))
        second = asyncio.run(wa.handle_payload(store, None, event,
                                               cfg=_cfg(), client=fake, now=T0))
        assert first["processed"] == 1 and second["duplicates"] == 1
        assert len(fake.texts) == 1  # the prompt was sent exactly once


def test_viewer_link_cannot_create_missions():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        fake = FakeClient()
        rec = wa.new_link_code(store, "org_v", "viewer_1", "viewer",
                               cfg=_cfg(), now=T0)
        asyncio.run(wa.handle_payload(
            store, None, _text_event("wamid.v1", f"LINK {rec['code']}"),
            cfg=_cfg(), client=fake, now=T0))
        asyncio.run(wa.handle_payload(
            store, None, _text_event("wamid.v2", "research something"),
            cfg=_cfg(), client=fake, now=T0))
        assert any("viewer" in t for _, t in fake.texts)
        assert store.list_missions("org_v") == []


def test_send_long_chunks_under_the_4096_cap():
    fake = FakeClient()
    wa._send_long(fake, WA_ID, "z" * 9500)
    assert len(fake.texts) == 3
    assert all(len(t) <= 4096 for _, t in fake.texts)
