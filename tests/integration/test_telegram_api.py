"""Telegram front door (v16): the full journey against a stub Bot API
server (OMNIMIND_TELEGRAM_API_BASE points at it — no real Telegram).

unlinked prompt → /start <code> links → plain text creates a mission
→ worker drain completes it → outbox pushes the result with buttons →
proof-pack callback returns labels + a document → /arena duel → vote
callback moves the arena ledger → foreign-duel tampering refused →
disabled channel 503s.
"""
import itertools
import random
import uuid

import pytest

from app.main import orchestrator as app_orchestrator
from app.main import store as app_store
from app.worker import drain
from tests.conftest import hdr
from tests.helpers_stub_telegram import start_stub

SECRET = "test-webhook-secret"
_update_ids = itertools.count(9_100_000)


@pytest.fixture(scope="module")
def tg_stub():
    server, stub, base = start_stub()
    yield stub, base
    server.shutdown()


@pytest.fixture()
def tg_env(monkeypatch, tg_stub):
    stub, base = tg_stub
    monkeypatch.setenv("OMNIMIND_TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("OMNIMIND_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("OMNIMIND_TELEGRAM_API_BASE", base)
    monkeypatch.setenv("OMNIMIND_TELEGRAM_BOT_USERNAME", "omnimind_stub_bot")
    stub.calls.clear()
    return stub


def _chat() -> int:
    return random.randint(100_000_000, 899_999_999)


def _wh(client, update, secret=SECRET):
    headers = {}
    if secret is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    return client.post("/v1/channels/telegram/webhook", json=update,
                       headers=headers)


def _msg(chat, text, username="zohaib"):
    return {"update_id": next(_update_ids), "message": {
        "message_id": 1, "chat": {"id": chat, "type": "private"},
        "from": {"id": 777, "username": username}, "text": text}}


def _cb(chat, data, message_id=1001):
    return {"update_id": next(_update_ids), "callback_query": {
        "id": f"cbq{next(_update_ids)}", "from": {"id": 777},
        "message": {"message_id": message_id,
                    "chat": {"id": chat, "type": "private"}},
        "data": data}}


def _markups(stub, prefix):
    """callback_data values (in send order) whose payload starts with prefix."""
    out = []
    for call in stub.find("sendMessage"):
        kb = (call.get("reply_markup") or {}).get("inline_keyboard") or []
        for row in kb:
            for btn in row:
                if btn.get("callback_data", "").startswith(prefix):
                    out.append(btn["callback_data"])
    return out


def _link(client, tg_env, tenant, chat):
    code = client.post("/v1/channels/telegram/link-code",
                       headers=hdr(tenant)).json()
    r = _wh(client, _msg(chat, f"/start {code['code']}"))
    assert r.status_code == 200, r.text
    return code


def test_full_telegram_journey(client, tenant, tg_env):
    stub = tg_env
    chat = _chat()

    # 1. an unlinked chat only ever gets the link prompt
    r = _wh(client, _msg(chat, "research the market for me"))
    assert r.status_code == 200
    msgs = stub.messages_to(chat)
    assert len(msgs) == 1 and "isn't linked" in msgs[0]["text"]

    # 2. the webhook refuses bad/missing secrets
    assert _wh(client, _msg(chat, "hi"), secret="wrong").status_code == 403
    assert _wh(client, _msg(chat, "hi"), secret=None).status_code == 403

    # 3. link via one-time code from the web API
    code = client.post("/v1/channels/telegram/link-code",
                       headers=hdr(tenant))
    assert code.status_code == 201, code.text
    body = code.json()
    assert body["deep_link"] == \
        f"https://t.me/omnimind_stub_bot?start={body['code']}"
    start_update = _msg(chat, f"/start {body['code']}")
    r = _wh(client, start_update)
    assert r.status_code == 200 and r.json()["ok"] is True
    linked_msgs = [m for m in stub.messages_to(chat) if "Linked" in m["text"]]
    assert len(linked_msgs) == 1
    status = client.get("/v1/channels/telegram/status",
                        headers=hdr(tenant)).json()
    assert status["linked"] is True
    assert status["telegram_username"] == "zohaib"

    # 4. Telegram retries the same update: deduped, no second send
    r = _wh(client, start_update)
    assert r.json().get("duplicate") is True
    assert len([m for m in stub.messages_to(chat)
                if "Linked" in m["text"]]) == 1

    # 5. plain text becomes a mission through the normal contract path
    objective = "Compare launch strategies for a new SMB invoicing product"
    _wh(client, _msg(chat, objective))
    ack = [m for m in stub.messages_to(chat) if "Mission" in m["text"]]
    assert ack and "queued" in ack[-1]["text"]
    missions = client.get("/v1/missions",
                          headers=hdr(tenant)).json()["missions"]
    mine = next(m for m in missions if m["title"].startswith("Compare launch"))
    assert mine["contract"]["task_type"] == "strategy"
    assert mine["created_by"] == "tester"
    events = client.get(f"/v1/missions/{mine['id']}/events",
                        headers=hdr(tenant)).json()["events"]
    created = next(e for e in events if e["event_type"] == "MissionCreated")
    assert created["payload"]["origin"] == "telegram"

    # 6. the worker completes it; the outbox pushes the result + buttons
    drain(app_store, app_orchestrator, "tg-test-worker")
    detail = client.get(f"/v1/missions/{mine['id']}",
                        headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "completed"
    pp_buttons = _markups(stub, "pp:")
    duel_buttons = _markups(stub, "duel:")
    assert pp_buttons == [f"pp:{mine['id']}"]
    assert duel_buttons == [f"duel:{mine['id']}"]
    push = [m for m in stub.messages_to(chat)
            if "Mission completed" in m["text"]]
    assert len(push) == 1

    # 7. proof-pack button: label summary message + the Markdown document
    _wh(client, _cb(chat, pp_buttons[0]))
    docs = stub.find("sendDocument")
    assert len(docs) == 1
    assert docs[0]["filename"] == f"proof-pack-{mine['id']}.md"
    assert docs[0]["bytes"] > 500
    pack_msgs = [m for m in stub.messages_to(chat) if "Proof Pack" in m["text"]]
    assert pack_msgs and "verified" in pack_msgs[-1]["text"]

    # /status and /schedules answer from the same link
    _wh(client, _msg(chat, "/status"))
    assert "Latest mission" in stub.messages_to(chat)[-1]["text"]
    _wh(client, _msg(chat, "/schedules"))
    assert "no schedules" in stub.messages_to(chat)[-1]["text"]

    # 8. /arena: a blind duel arrives as Side A / Side B with vote buttons
    client.post("/v1/agents/import-agency", headers=hdr(tenant))
    _wh(client, _msg(chat, "/arena Write a tagline for a coffee brand"))
    votes = _markups(stub, "vote:")
    assert len(votes) == 2
    duel_id = votes[0].split(":")[1]
    assert votes[1].split(":")[1] == duel_id
    sides = [m for m in stub.messages_to(chat) if "Side A" in m["text"]
             or "Side B" in m["text"]]
    assert len(sides) == 2

    # 9. voting moves the arena ledger and reveals the agents
    _wh(client, _cb(chat, votes[0]))
    stats = client.get("/v1/agents/stats",
                       headers=hdr(tenant)).json()["stats"]
    assert any(x.get("arena_wins", 0) == 1 for x in stats)
    assert any(x.get("arena_losses", 0) == 1 for x in stats)
    assert len(stub.find("editMessageReplyMarkup")) == 2  # keyboards cleared
    reveal = [m for m in stub.messages_to(chat) if "Duel decided" in m["text"]]
    assert reveal and "Winner" in reveal[-1]["text"]

    # a second tap on the spent button is a polite no-op
    _wh(client, _cb(chat, votes[1]))
    answers = [c.get("text", "") for c in stub.find("answerCallbackQuery")]
    assert any("already decided" in a for a in answers)
    stats = client.get("/v1/agents/stats",
                       headers=hdr(tenant)).json()["stats"]
    assert sum(x.get("arena_wins", 0) for x in stats) == 1

    # 10. a button naming ANOTHER tenant's duel fails politely
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    client.post("/v1/agents/import-agency", headers=hdr(other))
    foreign = client.post("/v1/arena/duels", headers=hdr(other), json={
        "task_type": "qa", "prompt": "What is coffee?"}).json()
    before = len(stub.find("answerCallbackQuery"))
    _wh(client, _cb(chat, f"vote:{foreign['duel_id']}:a"))
    answers = [c.get("text", "")
               for c in stub.find("answerCallbackQuery")[before:]]
    assert any("isn't yours" in a for a in answers)
    still = client.get(f"/v1/arena/duels/{foreign['duel_id']}",
                       headers=hdr(other)).json()
    assert still["status"] == "awaiting_vote"

    # 11. unlink: the chat goes back to link prompts
    r = client.delete("/v1/channels/telegram/link", headers=hdr(tenant))
    assert r.json()["unlinked"] is True
    status = client.get("/v1/channels/telegram/status",
                        headers=hdr(tenant)).json()
    assert status["linked"] is False
    _wh(client, _msg(chat, "hello again"))
    assert "isn't linked" in stub.messages_to(chat)[-1]["text"]


def test_disabled_channel_503s(client, tenant, tg_env, monkeypatch):
    monkeypatch.delenv("OMNIMIND_TELEGRAM_BOT_TOKEN")
    r = client.post("/v1/channels/telegram/link-code", headers=hdr(tenant))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "TELEGRAM_NOT_CONFIGURED"
    assert client.get("/v1/channels/telegram/status",
                      headers=hdr(tenant)).status_code == 503
    assert client.delete("/v1/channels/telegram/link",
                         headers=hdr(tenant)).status_code == 503
    r = _wh(client, _msg(_chat(), "hi"))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "TELEGRAM_NOT_CONFIGURED"


def test_link_code_is_single_use_over_http(client, tenant, tg_env):
    chat_a, chat_b = _chat(), _chat()
    code = client.post("/v1/channels/telegram/link-code",
                       headers=hdr(tenant)).json()["code"]
    _wh(client, _msg(chat_a, f"/start {code}"))
    _wh(client, _msg(chat_b, f"/start {code}"))
    msgs_b = tg_env.messages_to(chat_b)
    assert msgs_b and "invalid, expired, or already used" in msgs_b[-1]["text"]
    # chat B never got bound: its plain text earns the link prompt
    _wh(client, _msg(chat_b, "do work"))
    assert "isn't linked" in tg_env.messages_to(chat_b)[-1]["text"]
