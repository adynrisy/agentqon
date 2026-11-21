"""WhatsApp front door (v17): the full journey against a stub Graph
API server (OMNIMIND_WHATSAPP_API_BASE points at it — no real Meta).

handshake + signature gates → unlinked prompt → LINK <code> binds →
plain text creates a mission → worker drain completes it → in-window
push with reply buttons → proof-pack button returns labels + a
document (media upload first) → arena duel → button vote moves the
arena ledger → out-of-window completion sends a template alert, and
the full result lands when the user replies → without a template the
push is held until the next inbound → disabled channel 503s.
"""
import hashlib
import hmac
import itertools
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.main import orchestrator as app_orchestrator
from app.main import store as app_store
from app.whatsapp import _iso
from app.worker import drain
from tests.conftest import hdr
from tests.helpers_stub_whatsapp import start_stub

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
WA = "15557654321"
_wamids = itertools.count(1)


@pytest.fixture(scope="module")
def wa_stub():
    server, stub, base = start_stub()
    yield stub, base
    server.shutdown()


@pytest.fixture()
def wa_env(monkeypatch, wa_stub):
    stub, base = wa_stub
    monkeypatch.setenv("OMNIMIND_WHATSAPP_TOKEN", "EAATESTTOKEN")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("OMNIMIND_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_API_BASE", base)
    monkeypatch.setenv("OMNIMIND_WHATSAPP_BUSINESS_NUMBER", "15550001111")
    monkeypatch.delenv("OMNIMIND_WHATSAPP_TEMPLATE_NAME", raising=False)
    stub.calls.clear()
    return stub


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body,
                                hashlib.sha256).hexdigest()


def _post(client, payload, secret=APP_SECRET, sign=True):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(raw, secret)
    return client.post("/v1/channels/whatsapp/webhook",
                       content=raw, headers=headers)


def _text(wa_id, text, name="Zohaib"):
    n = next(_wamids)
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages",
                        "value": {
                            "contacts": [{"wa_id": wa_id,
                                          "profile": {"name": name}}],
                            "messages": [{
                                "from": wa_id, "id": f"wamid.t{n}",
                                "timestamp": "1767225600", "type": "text",
                                "text": {"body": text}}]}}]}]}


def _button(wa_id, button_id, title="🏆 This one wins"):
    n = next(_wamids)
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages",
                        "value": {
                            "contacts": [{"wa_id": wa_id,
                                          "profile": {"name": "Zohaib"}}],
                            "messages": [{
                                "from": wa_id, "id": f"wamid.t{n}",
                                "timestamp": "1767225600",
                                "type": "interactive",
                                "interactive": {
                                    "type": "button_reply",
                                    "button_reply": {"id": button_id,
                                                     "title": title}}}]}}]}]}


def _close_window(wa_id, hours=48):
    link = app_store.get_whatsapp_link(wa_id)
    link["last_inbound_at"] = _iso(
        datetime.now(timezone.utc) - timedelta(hours=hours))
    app_store.put_whatsapp_link(link)


def _buttons_for(stub, prefix):
    return [b for b in stub.buttons()
            if str(b["id"]).startswith(prefix)]


def test_full_whatsapp_journey(client, tenant, wa_env, monkeypatch):
    stub = wa_env

    # 1. the GET handshake: right token earns the challenge, wrong 403
    r = client.get("/v1/channels/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "challenge-42"})
    assert r.status_code == 200 and r.text == "challenge-42"
    r = client.get("/v1/channels/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong",
        "hub.challenge": "challenge-42"})
    assert r.status_code == 403

    # 2. POST is refused without a valid X-Hub-Signature-256
    payload = _text(WA, "hello")
    assert _post(client, payload, sign=False).status_code == 403
    assert _post(client, payload, secret="wrong-secret").status_code == 403
    assert stub.calls == []  # nothing reached Meta's side either

    # 3. an unlinked number only ever gets the link prompt
    r = _post(client, payload)
    assert r.status_code == 200 and r.json()["processed"] == 1
    bodies = stub.all_text_bodies(WA)
    assert len(bodies) == 1 and "isn't linked" in bodies[0]

    # 4. link via one-time code minted at the web API
    code_resp = client.post("/v1/channels/whatsapp/link-code",
                            headers=hdr(tenant))
    assert code_resp.status_code == 201, code_resp.text
    body = code_resp.json()
    assert body["wa_link"] == \
        f"https://wa.me/15550001111?text=LINK%20{body['code']}"
    link_update = _text(WA, f"LINK {body['code']}")
    r = _post(client, link_update)
    assert r.json()["ok"] is True
    assert any("Linked" in b for b in stub.all_text_bodies(WA))
    status = client.get("/v1/channels/whatsapp/status",
                        headers=hdr(tenant)).json()
    assert status["linked"] is True and status["whatsapp_name"] == "Zohaib"

    # 5. Meta redelivers the same wamid: deduped, no second greeting
    r = _post(client, link_update)
    assert r.json()["duplicates"] == 1
    assert len([b for b in stub.all_text_bodies(WA) if "Linked" in b]) == 1

    # 6. plain text becomes a mission through the normal contract path
    objective = "Compare launch strategies for a new SMB invoicing product"
    _post(client, _text(WA, objective))
    missions = client.get("/v1/missions",
                          headers=hdr(tenant)).json()["missions"]
    mine = next(m for m in missions if m["title"].startswith("Compare launch"))
    assert mine["contract"]["task_type"] == "strategy"
    events = client.get(f"/v1/missions/{mine['id']}/events",
                        headers=hdr(tenant)).json()["events"]
    created = next(e for e in events if e["event_type"] == "MissionCreated")
    assert created["payload"]["origin"] == "whatsapp"

    # 7. the worker completes it; the window is open, so the full
    #    result is pushed with Proof Pack / New Duel reply buttons
    drain(app_store, app_orchestrator, "wa-test-worker")
    detail = client.get(f"/v1/missions/{mine['id']}",
                        headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "completed"
    pp = _buttons_for(stub, "pp:")
    duel_btns = _buttons_for(stub, "duel:")
    assert [b["id"] for b in pp] == [f"pp:{mine['id']}"]
    assert [b["id"] for b in duel_btns] == [f"duel:{mine['id']}"]
    assert all(len(b["title"]) <= 20 for b in pp + duel_btns)
    pushes = [b for b in stub.all_text_bodies(WA) if "Mission completed" in b]
    assert len(pushes) == 1

    # 8. proof-pack button: label summary + media upload + document
    _post(client, _button(WA, pp[0]["id"], "🧾 Proof Pack"))
    uploads = stub.find("media")
    assert len(uploads) == 1
    assert uploads[0]["filename"] == f"proof-pack-{mine['id']}.md"
    assert uploads[0]["bytes"] > 500
    docs = [c for c in stub.of_type("document")]
    assert len(docs) == 1
    assert docs[0]["document"]["id"].startswith("media_stub_")
    assert docs[0]["document"]["filename"] == f"proof-pack-{mine['id']}.md"
    assert any("Proof Pack" in b and "verified" in b
               for b in stub.all_text_bodies(WA))

    # 9. arena: a blind duel arrives with one vote button per side
    client.post("/v1/agents/import-agency", headers=hdr(tenant))
    _post(client, _text(WA, "arena Write a tagline for a coffee brand"))
    votes = _buttons_for(stub, "vote:")
    assert len(votes) == 2
    duel_id = votes[0]["id"].split(":")[1]
    assert votes[1]["id"].split(":")[1] == duel_id

    # 10. voting moves the arena ledger and reveals the agents
    _post(client, _button(WA, votes[0]["id"]))
    stats = client.get("/v1/agents/stats",
                       headers=hdr(tenant)).json()["stats"]
    assert any(x.get("arena_wins", 0) == 1 for x in stats)
    assert any(x.get("arena_losses", 0) == 1 for x in stats)
    assert any("Duel decided" in b for b in stub.all_text_bodies(WA))
    # a second tap on the spent button is a polite no-op
    _post(client, _button(WA, votes[1]["id"]))
    assert any("already decided" in b for b in stub.all_text_bodies(WA))
    stats = client.get("/v1/agents/stats",
                       headers=hdr(tenant)).json()["stats"]
    assert sum(x.get("arena_wins", 0) for x in stats) == 1

    # 11. a button naming ANOTHER tenant's duel fails politely
    other = f"org_other_{uuid.uuid4().hex[:6]}"
    client.post("/v1/agents/import-agency", headers=hdr(other))
    foreign = client.post("/v1/arena/duels", headers=hdr(other), json={
        "task_type": "qa", "prompt": "What is coffee?"}).json()
    _post(client, _button(WA, f"vote:{foreign['duel_id']}:a"))
    assert any("isn't yours" in b for b in stub.all_text_bodies(WA))
    still = client.get(f"/v1/arena/duels/{foreign['duel_id']}",
                       headers=hdr(other)).json()
    assert still["status"] == "awaiting_vote"

    # 12. OUT OF WINDOW + template configured: the completion sends
    #     ONE template alert; the full result waits for a reply
    monkeypatch.setenv("OMNIMIND_WHATSAPP_TEMPLATE_NAME",
                       "mission_result_alert")
    objective2 = "Compare pricing strategies for invoicing tools"
    _post(client, _text(WA, objective2))
    missions = client.get("/v1/missions",
                          headers=hdr(tenant)).json()["missions"]
    m2 = next(m for m in missions if m["title"].startswith("Compare pricing"))
    _close_window(WA)  # the user goes silent for two days
    drain(app_store, app_orchestrator, "wa-test-worker")
    detail = client.get(f"/v1/missions/{m2['id']}",
                        headers=hdr(tenant)).json()
    assert detail["mission"]["status"] == "completed"
    templates = stub.of_type("template")
    assert len(templates) == 1
    assert templates[0]["template"]["name"] == "mission_result_alert"
    params = templates[0]["template"]["components"][0]["parameters"]
    assert params == [{"type": "text", "text": m2["title"]}]
    rec = app_store.get_whatsapp_outbox(tenant, f"mission_result:{m2['id']}")
    assert rec["status"] == "alerted"
    pushes2 = [b for b in stub.all_text_bodies(WA)
               if "Mission completed" in b and "pricing" in b.lower()]
    assert pushes2 == []  # the full result has NOT been sent yet
    # the user replies -> window reopens -> held result is delivered
    _post(client, _text(WA, "status"))
    rec = app_store.get_whatsapp_outbox(tenant, f"mission_result:{m2['id']}")
    assert rec["status"] == "sent"
    pp2 = [b for b in _buttons_for(stub, "pp:")
           if b["id"] == f"pp:{m2['id']}"]
    assert len(pp2) == 1

    # 13. OUT OF WINDOW + NO template: held silently until next inbound
    monkeypatch.delenv("OMNIMIND_WHATSAPP_TEMPLATE_NAME")
    objective3 = "Compare onboarding flows for invoicing tools"
    _post(client, _text(WA, objective3))
    missions = client.get("/v1/missions",
                          headers=hdr(tenant)).json()["missions"]
    m3 = next(m for m in missions if m["title"].startswith("Compare onboarding"))
    _close_window(WA)
    drain(app_store, app_orchestrator, "wa-test-worker")
    assert len(stub.of_type("template")) == 1  # still just the one above
    rec = app_store.get_whatsapp_outbox(tenant, f"mission_result:{m3['id']}")
    assert rec["status"] == "held"
    _post(client, _text(WA, "help"))
    rec = app_store.get_whatsapp_outbox(tenant, f"mission_result:{m3['id']}")
    assert rec["status"] == "sent"
    pp3 = [b for b in _buttons_for(stub, "pp:")
           if b["id"] == f"pp:{m3['id']}"]
    assert len(pp3) == 1

    # 14. unlink: the number goes back to link prompts
    r = client.delete("/v1/channels/whatsapp/link", headers=hdr(tenant))
    assert r.json()["unlinked"] is True
    status = client.get("/v1/channels/whatsapp/status",
                        headers=hdr(tenant)).json()
    assert status["linked"] is False
    _post(client, _text(WA, "hello again"))
    assert "isn't linked" in stub.all_text_bodies(WA)[-1]


def test_disabled_channel_503s(client, tenant, wa_env, monkeypatch):
    monkeypatch.delenv("OMNIMIND_WHATSAPP_TOKEN")
    r = client.post("/v1/channels/whatsapp/link-code", headers=hdr(tenant))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "WHATSAPP_NOT_CONFIGURED"
    assert client.get("/v1/channels/whatsapp/status",
                      headers=hdr(tenant)).status_code == 503
    assert client.delete("/v1/channels/whatsapp/link",
                         headers=hdr(tenant)).status_code == 503
    assert client.get("/v1/channels/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "x"}).status_code == 503
    assert _post(client, _text(WA, "hi")).status_code == 503


def test_link_code_is_single_use_over_http(client, tenant, wa_env):
    wa_a, wa_b = WA, "15550009999"
    code = client.post("/v1/channels/whatsapp/link-code",
                       headers=hdr(tenant)).json()["code"]
    _post(client, _text(wa_a, f"LINK {code}"))
    _post(client, _text(wa_b, f"LINK {code}"))
    bodies_b = wa_env.all_text_bodies(wa_b)
    assert bodies_b and "invalid, expired, or already used" in bodies_b[-1]
    _post(client, _text(wa_b, "do work"))
    assert "isn't linked" in wa_env.all_text_bodies(wa_b)[-1]
