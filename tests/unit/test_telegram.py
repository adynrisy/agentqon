"""Telegram front door (v16) unit tests: text hygiene (chunking,
command/callback parsing, plain-text output), the link-code lifecycle
(expiry, single-use, replace-on-relink), webhook update dedupe, the
role gate, and the mission contract a chat message creates."""
import tempfile
from datetime import datetime, timedelta, timezone

import asyncio

import pytest

from app import telegram as tg
from app.store import Store

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def _store(tmp):
    return Store(f"{tmp}/t.db", f"{tmp}/artifacts")


class FakeClient:
    """Captures outbound calls the way the stub server does, in-process."""

    def __init__(self):
        self.sent = []       # (chat_id, text, reply_markup)
        self.documents = []  # (chat_id, filename, bytes)
        self.answers = []    # (callback_query_id, text)
        self._mid = 0

    def send_message(self, chat_id, text, reply_markup=None):
        self._mid += 1
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": self._mid}

    def send_document(self, chat_id, filename, data, caption=None):
        self.documents.append((chat_id, filename, data))
        return {"message_id": 999}

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return {}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))
        return {}


# ------------------------------------------------------------ chunking
def test_chunk_short_text_unchanged():
    assert tg.chunk_text("hello world") == ["hello world"]
    assert tg.chunk_text("") == [""]


def test_chunk_splits_on_paragraph_boundaries_with_markers():
    text = "\n\n".join(f"paragraph {i} " + "x" * 60 for i in range(6))
    parts = tg.chunk_text(text, limit=200)
    assert len(parts) >= 3
    for i, part in enumerate(parts):
        assert len(part) <= 200 + len("\n\n(part 9/9)")
        assert part.endswith(f"(part {i + 1}/{len(parts)})")
        # boundaries are respected: no paragraph is torn in two
        assert "paragraph" in part


def test_chunk_hard_splits_overlong_paragraph():
    parts = tg.chunk_text("y" * 9500)
    assert len(parts) == 3  # 4000 + 4000 + 1500
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(p.split("\n\n(part")[0] for p in parts) == "y" * 9500


# ------------------------------------------------------------ parsing
def test_parse_command():
    assert tg.parse_command("/mission do the thing") == ("mission", "do the thing")
    assert tg.parse_command("/mission@OmniMindBot do it") == ("mission", "do it")
    assert tg.parse_command("/START abc123") == ("start", "abc123")
    assert tg.parse_command("just a normal message") == (None, "just a normal message")
    assert tg.parse_command("/help") == ("help", "")


def test_callback_payload_roundtrip():
    assert tg.parse_callback(tg.build_vote_callback("duel_abc", "a")) == \
        ("vote", ["duel_abc", "a"])
    assert tg.parse_callback(tg.build_proof_callback("mis_xyz")) == \
        ("pp", ["mis_xyz"])
    assert tg.parse_callback(tg.build_duel_callback("mis_xyz")) == \
        ("duel", ["mis_xyz"])
    assert tg.parse_callback("") == ("", [])


# ------------------------------------------------------------ link codes
def test_link_code_redeem_single_use_and_role_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        rec = tg.new_link_code(store, "org_t", "user_1", "member", now=T0)
        chat = {"id": 555, "username": "chatname"}
        link = tg._redeem_code(store, rec["code"], chat,
                               {"id": 42, "username": "zoe"}, now=T0)
        assert link["tenant_id"] == "org_t" and link["user_id"] == "user_1"
        assert link["role"] == "member"
        assert link["telegram_username"] == "zoe"
        assert store.get_telegram_link(555)["user_id"] == "user_1"
        # single-use: the same code cannot bind a second chat
        assert tg._redeem_code(store, rec["code"], {"id": 556},
                               {"id": 43}, now=T0) is None
        assert store.get_telegram_link(556) is None


def test_link_code_expires():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        rec = tg.new_link_code(store, "org_t", "user_1", "owner", now=T0)
        later = T0 + timedelta(minutes=tg.LINK_CODE_TTL_MINUTES + 1)
        assert tg._redeem_code(store, rec["code"], {"id": 1}, {"id": 2},
                               now=later) is None
        # expired codes are cleaned up, not left redeemable
        assert store.get_telegram_code(rec["code"]) is None


def test_new_code_replaces_previous_unused_code():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        first = tg.new_link_code(store, "org_t", "user_1", "owner", now=T0)
        second = tg.new_link_code(store, "org_t", "user_1", "owner", now=T0)
        assert first["code"] != second["code"]
        assert tg._redeem_code(store, first["code"], {"id": 1}, {"id": 2},
                               now=T0) is None
        assert tg._redeem_code(store, second["code"], {"id": 1}, {"id": 2},
                               now=T0) is not None


def test_relink_replaces_chat_binding():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        c1 = tg.new_link_code(store, "org_t", "user_1", "owner", now=T0)
        tg._redeem_code(store, c1["code"], {"id": 900}, {"id": 1}, now=T0)
        # a second account links the same physical chat: newest wins
        c2 = tg.new_link_code(store, "org_t", "user_2", "member", now=T0)
        tg._redeem_code(store, c2["code"], {"id": 900}, {"id": 1}, now=T0)
        assert store.get_telegram_link(900)["user_id"] == "user_2"
        assert store.get_telegram_link_for_user("org_t", "user_1") is None


# ------------------------------------------------------------ dedupe + roles
def test_update_dedupe_at_store_level():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        assert store.mark_telegram_update(123456) is True
        assert store.mark_telegram_update(123456) is False
        assert store.mark_telegram_update(123457) is True


def test_role_gate():
    assert tg._can_act({"role": "owner"}) is True
    assert tg._can_act({"role": "member"}) is True
    assert tg._can_act({"role": "viewer"}) is False
    assert tg._can_act({"role": ""}) is False
    assert tg._can_act({}) is False


# ------------------------------------------------------------ mission contract
def test_telegram_contract_is_the_platform_default():
    contract = tg.telegram_contract("Research the SMB invoicing market")
    assert contract["task_type"] == "research"  # classifier, at creation
    assert contract["data_classification"] == "internal"
    # live_web stays None: the classification gate decides, a chat
    # message never silently enables live web
    assert contract["tools"]["live_web"] is None
    # v21.3: no user-facing budget — the contract carries the internal
    # runaway ceiling, not a chat-settable cap.
    from app.domain import MISSION_BUDGET_CEILING_USD
    assert contract["constraints"]["max_cost_usd"] == MISSION_BUDGET_CEILING_USD


# ------------------------------------------------------------ update handling
def test_unlinked_chat_gets_link_prompt_only():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        client = FakeClient()
        update = {"update_id": 1, "message": {
            "message_id": 1, "chat": {"id": 31337, "type": "private"},
            "from": {"id": 1}, "text": "do some research for me"}}
        result = asyncio.run(tg.handle_update(store, None, update, client=client))
        assert result["ok"] is True
        assert len(client.sent) == 1
        assert "isn't linked" in client.sent[0][1]
        # nothing was created for an unlinked chat
        assert store.list_missions("org_any") == []


def test_duplicate_update_is_not_reprocessed():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        client = FakeClient()
        update = {"update_id": 77, "message": {
            "message_id": 1, "chat": {"id": 1, "type": "private"},
            "from": {"id": 1}, "text": "/start"}}
        first = asyncio.run(tg.handle_update(store, None, update, client=client))
        second = asyncio.run(tg.handle_update(store, None, update, client=client))
        assert first["ok"] is True and second.get("duplicate") is True
        assert len(client.sent) == 1  # the prompt was sent exactly once


def test_viewer_link_cannot_create_missions():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        client = FakeClient()
        rec = tg.new_link_code(store, "org_v", "viewer_1", "viewer")
        start = {"update_id": 5, "message": {
            "message_id": 1, "chat": {"id": 808, "type": "private"},
            "from": {"id": 9}, "text": f"/start {rec['code']}"}}
        asyncio.run(tg.handle_update(store, None, start, client=client))
        ask = {"update_id": 6, "message": {
            "message_id": 2, "chat": {"id": 808, "type": "private"},
            "from": {"id": 9}, "text": "research something for me"}}
        asyncio.run(tg.handle_update(store, None, ask, client=client))
        texts = [t for _, t, _ in client.sent]
        assert any("viewer" in t for t in texts)
        assert store.list_missions("org_v") == []
