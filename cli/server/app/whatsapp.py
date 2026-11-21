"""AgentQon — WhatsApp front door (v17, Meta Cloud API).

The WhatsApp twin of the Telegram front door (v16, app/telegram.py):
same linking model, same flows, same honesty rules — rebuilt on the
Meta WhatsApp Business Cloud API's very different mechanics. A
WhatsApp number links to ONE AgentQon account (tenant + user + role
snapshot) through the same style of one-time code minted in the web
UI; from then on the number can:

  - send a mission as a plain message — created through the normal
    contract path (same classifier, same classification gate;
    live_web is never silently enabled) and enqueued as an ordinary
    mission_run job;
  - ask "status" for the latest mission;
  - RECEIVE the result when the mission finishes — subject to
    WhatsApp's window rules below;
  - run "arena <topic>" duels and vote with reply buttons — votes go
    through app/arena.py's own cast_vote, never a reimplementation;
  - list "schedules".

PLATFORM RULES THIS MODULE IS BUILT AROUND (Meta's, not ours):

  - THE 24-HOUR WINDOW. Free-form messages (text, buttons,
    documents) may only be sent within 24 hours of the user's last
    inbound message. The adapter tracks last_inbound_at per link and
    the outbox tick decides per push:
      window open  -> the full result, with [🧾 Proof Pack]
                      [⚔️ New Duel] reply buttons;
      window shut  -> if AGENTQON_WHATSAPP_TEMPLATE_NAME names an
                      approved utility template, ONE template alert
                      ({{1}} = the mission title) invites the user to
                      reply; their reply reopens the window and the
                      held full result is delivered immediately
                      (deliver_pending_for_link, called from the
                      inbound path — and from the tick, if the window
                      has since opened). With no template configured
                      the push is simply HELD (status "held") until
                      the user's next message. Nothing is ever
                      dropped for being out of window.
  - Reply buttons: max 3 per message, titles ≤20 chars (the client
    enforces both). Buttons on an already-sent message cannot be
    removed, so a spent vote button stays visible; tapping it again
    earns a polite "already decided".
  - Documents go two-step: upload media, then send by media id —
    and only inside the window (a proof-pack button tap IS an
    inbound message, so the window is open at tap time by
    construction).
  - Webhook authenticity is Meta's signature: POST deliveries must
    carry X-Hub-Signature-256 = HMAC-SHA256(raw body, app secret).
    When AGENTQON_WHATSAPP_APP_SECRET is set the endpoint refuses
    anything unsigned or badly signed.

    ⚠️ NO APP SECRET = NO DELIVERIES (Shield H4, v20.2). The app
    secret is mandatory whenever the channel is enabled: with
    AGENTQON_WHATSAPP_APP_SECRET unset, the webhook endpoint refuses
    every POST (fail closed — the same posture as the Telegram
    door). Earlier versions accepted unauthenticated deliveries in
    that configuration; that trade-off is gone.

The channel is configuration-gated: with AGENTQON_WHATSAPP_TOKEN or
AGENTQON_WHATSAPP_PHONE_NUMBER_ID unset, every function here no-ops
or reports "not configured" and the rest of the platform is
untouched. All outbound calls go through app/whatsapp_client.py
(the only module that knows the Graph API). All bot output is PLAIN
TEXT — persona output is untrusted. Webhook deliveries are
deduplicated by Meta's message id (wamid): Meta redelivers on any
hiccup, and a redelivery must never create a second mission. Every
object a button can name is re-resolved inside the caller's tenant —
a payload naming another tenant's duel or mission fails politely.
Linking has no /start deep link on WhatsApp: the web panel shows a
wa.me link whose prefilled text is "LINK <code>", and the adapter
accepts exactly that first message from an unlinked number.

v18 — MULTI-TENANT NUMBERS (Embedded Signup, app/whatsapp_onboard.py).
A tenant can connect its OWN WhatsApp business number; deliveries
for it arrive at this same webhook under that number's
metadata.phone_number_id. handle_payload routes per event: the
env-configured number (or a payload with no metadata — v17's shape)
behaves exactly as above; a number owned by a stored tenant
connection routes to that tenant, is answered with that tenant's own
client (its number + token, app/whatsapp_client.resolve_client), and
only honours links belonging to that tenant; a number nobody owns
is ignored (counted in the summary) — Meta may deliver for numbers
an app once touched, and silence is the correct answer. Outbound
result pushes resolve per outbox record's tenant the same way.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from . import arena as arena_mod
from .auth import ROLE_RANK
from .config import load_settings
from .domain import StateConflict, event_envelope, now_iso
from .proofpack import build_proof_pack, proof_pack_markdown
from .tasktypes import classify_task
from .telegram import (build_duel_callback, build_proof_callback,
                       build_vote_callback, chunk_text, parse_callback,
                       telegram_contract, _cadence_text,
                       _mission_result_text)
from .whatsapp_client import WhatsAppClient, resolve_client, tenant_client

LINK_CODE_TTL_MINUTES = 10  # same code lifetime as the Telegram door
MESSAGE_LIMIT = 4096        # WhatsApp's hard cap on a text body
WINDOW_HOURS = 24           # Meta's customer-service window
_TERMINAL = ("completed", "failed", "cancelled")

HELP_TEXT = (
    "AgentQon commands:\n"
    "• Send any message — it becomes a mission, and I reply here with "
    "the result when it finishes.\n"
    "• status — the status of your latest mission.\n"
    "• arena <topic> — two agents duel blind on your topic; you pick "
    "the winner.\n"
    "• schedules — your recurring missions.\n"
    "• help — this list."
)

LINK_PROMPT = (
    "👋 This number isn't linked to an AgentQon account yet.\n\n"
    "To link it:\n"
    "1. Open AgentQon in your browser → ⚙ Advanced → WhatsApp panel.\n"
    "2. Tap “Generate link code”.\n"
    "3. Send me a message that says: LINK <code>\n\n"
    "The code works once and expires in 10 minutes."
)

_LINK_RE = re.compile(r"^link\s+(\S+)$", re.IGNORECASE)


# ------------------------------------------------------------- config
def channel_config():
    """Live settings for the channel (load_settings parses the
    environment on every call, so tests can repoint the channel at a
    stub Graph API without a process restart)."""
    return load_settings()


def channel_enabled(cfg=None) -> bool:
    cfg = cfg or channel_config()
    return bool(cfg.whatsapp_token and cfg.whatsapp_phone_number_id)


def get_client(cfg=None) -> Optional[WhatsAppClient]:
    cfg = cfg or channel_config()
    if not channel_enabled(cfg):
        return None
    return WhatsAppClient(cfg.whatsapp_token, cfg.whatsapp_phone_number_id,
                          cfg.whatsapp_api_base, cfg.whatsapp_api_version)


def _default_vault(cfg):
    """The process vault for per-tenant credential resolution, built
    from settings when the caller (API app, worker) didn't pass its
    own. None when the vault cannot be built — tenant connections
    then simply don't resolve, and env behaviour is unchanged."""
    try:
        from .vault import Vault
        return Vault.from_settings(cfg, cfg.data_dir)
    except Exception:
        return None


# ------------------------------------------------- webhook authenticity
def verify_signature(app_secret: str, raw_body: bytes,
                     header_value: Optional[str]) -> bool:
    """X-Hub-Signature-256 must be 'sha256=' + hex HMAC-SHA256 of the
    RAW request body under the app secret. Anything else (missing,
    wrong scheme, wrong digest) is not from Meta."""
    if not app_secret or not header_value:
        return False
    scheme, _, digest = header_value.partition("=")
    if scheme != "sha256" or not digest:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.strip())


def verify_handshake(params: dict, verify_token: str) -> Optional[str]:
    """The GET subscription handshake: hub.mode=subscribe and a
    matching hub.verify_token earns the hub.challenge back verbatim;
    anything else earns None (the endpoint answers 403)."""
    if not verify_token:
        return None
    if params.get("hub.mode") == "subscribe" \
            and hmac.compare_digest(params.get("hub.verify_token", ""),
                                    verify_token):
        return params.get("hub.challenge", "")
    return None


# ------------------------------------------------------------- window
def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def window_open(link: Optional[dict], now: Optional[datetime] = None) -> bool:
    """True while free-form messages may be sent to this link: the
    user's last inbound message is less than WINDOW_HOURS old. A link
    with no recorded inbound is treated as window-shut (its linking
    message is an inbound, so a live link always has one)."""
    if not link:
        return False
    last = _parse_iso(link.get("last_inbound_at"))
    if last is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - last) < timedelta(hours=WINDOW_HOURS)


# ------------------------------------------------------ payload parsing
def parse_webhook_payload(payload: dict) -> list[dict]:
    """Normalise a Cloud API webhook body into message events.
    Delivery receipts and status updates (value.statuses) are not
    messages and are ignored, as are message types the front door
    cannot act on (images, audio, stickers…). Each event:
    {wamid, wa_id, name, kind: 'text'|'button', text, button_id,
    timestamp} — button events carry the tapped reply button's id in
    button_id (the adapter's callback vocabulary, shared with the
    Telegram door) and its title in text."""
    events: list[dict] = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                        for c in value.get("contacts") or []}
            for msg in value.get("messages") or []:
                wa_id = msg.get("from")
                base = {"wamid": msg.get("id"), "wa_id": wa_id,
                        "name": contacts.get(wa_id),
                        "timestamp": msg.get("timestamp"),
                        # v18 routing key: the BUSINESS number this
                        # delivery is for. Absent in v17-shaped
                        # payloads -> the env/default route.
                        "phone_number_id": metadata.get("phone_number_id")}
                mtype = msg.get("type")
                if mtype == "text":
                    events.append({**base, "kind": "text",
                                   "text": (msg.get("text") or {}).get("body", ""),
                                   "button_id": None})
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    if reply.get("id"):
                        events.append({**base, "kind": "button",
                                       "text": reply.get("title", ""),
                                       "button_id": reply.get("id")})
    return events


def parse_link_text(text: str) -> Optional[str]:
    """'LINK <code>' (any case, trimmed) -> the code; else None."""
    m = _LINK_RE.match((text or "").strip())
    return m.group(1) if m else None


# ------------------------------------------------------------- linking
def new_link_code(store, tenant_id: str, user_id: str, role: str,
                  cfg=None, now: Optional[datetime] = None) -> dict:
    """Mint a one-time link code for (tenant, user) — same semantics
    as the Telegram door: minting replaces any still-unused codes for
    the same user, so the newest code is the only live one. The
    wa_link is the tap-to-link wa.me URL (business number + prefilled
    'LINK <code>' text) when the business number is configured."""
    cfg = cfg or channel_config()
    now = now or datetime.now(timezone.utc)
    store.delete_whatsapp_codes_for(tenant_id, user_id)
    code = secrets.token_urlsafe(9)
    record = {"code": code, "tenant_id": tenant_id, "user_id": user_id,
              "role": role, "created_at": _iso(now),
              "expires_at": _iso(now + timedelta(minutes=LINK_CODE_TTL_MINUTES))}
    store.put_whatsapp_code(record)
    number = cfg.whatsapp_business_number
    return {**record,
            "business_number": number or None,
            "wa_link": (f"https://wa.me/{number}?text={quote('LINK ' + code)}"
                        if number else None)}


def link_status(store, tenant_id: str, user_id: str, cfg=None) -> dict:
    cfg = cfg or channel_config()
    link = store.get_whatsapp_link_for_user(tenant_id, user_id)
    return {"configured": channel_enabled(cfg),
            "business_number": cfg.whatsapp_business_number or None,
            "linked": link is not None,
            "wa_id": link["wa_id"] if link else None,
            "whatsapp_name": (link or {}).get("whatsapp_name"),
            "linked_at": (link or {}).get("linked_at")}


def unlink_account(store, tenant_id: str, user_id: str) -> bool:
    link = store.get_whatsapp_link_for_user(tenant_id, user_id)
    if link is None:
        return False
    store.delete_whatsapp_codes_for(tenant_id, user_id)
    return store.delete_whatsapp_link(link["wa_id"])


def _redeem_code(store, code: str, wa_id: str,
                 profile_name: Optional[str],
                 now: Optional[datetime] = None) -> Optional[dict]:
    """Validate + consume a link code and bind the number. None = the
    code is unknown, expired, or already used. The linking message is
    itself an inbound, so the fresh link's window starts open."""
    now = now or datetime.now(timezone.utc)
    record = store.get_whatsapp_code(code)
    if record is None:
        return None
    expires = _parse_iso(record["expires_at"])
    if expires is not None and expires <= now:
        store.delete_whatsapp_code(code)
        return None
    store.delete_whatsapp_code(code)  # single-use: consumed either way
    link = {"wa_id": str(wa_id), "whatsapp_name": profile_name,
            "tenant_id": record["tenant_id"], "user_id": record["user_id"],
            "role": record["role"], "linked_at": _iso(now),
            "last_inbound_at": _iso(now), "last_mission_id": None}
    store.put_whatsapp_link(link)
    store.audit(record["tenant_id"], record["user_id"], "whatsapp.linked",
                "whatsapp_link", str(wa_id), {})
    return link


# ------------------------------------------------------------- sending
def _send_long(client: WhatsAppClient, wa_id, text: str) -> list[dict]:
    """Send text chunked to WhatsApp's 4096 cap (paragraph-boundary
    splits with part markers — app/telegram.py's shared chunker)."""
    return [client.send_text(wa_id, part) for part in chunk_text(text)]


def _can_act(link: dict, min_role: str = "member") -> bool:
    return ROLE_RANK.get(link.get("role", ""), -1) >= ROLE_RANK[min_role]


def _effective_role(store, link: dict, cfg=None) -> Optional[str]:
    """Audit F8: the role a link confers RIGHT NOW. The link record
    carries a role SNAPSHOT from link time; in accounts mode the
    tenant's membership table is authoritative and is re-checked
    whenever the chat acts — a demoted member's link confers the
    demoted role, and a removed member's link confers nothing at
    all. In dev/local mode there is no membership authority (the
    single-user product: the link IS the user's own), so the
    snapshot stands."""
    cfg = cfg or channel_config()
    if cfg.auth_mode == "accounts":
        membership = store.get_membership(link["tenant_id"],
                                          link["user_id"])
        return membership["role"] if membership else None
    return link.get("role")


def _can_act_now(store, link: dict, cfg=None,
                 min_role: str = "member") -> bool:
    """_can_act against the LIVE role (F8), not the link snapshot."""
    role = _effective_role(store, link, cfg)
    return role is not None \
        and ROLE_RANK.get(role, -1) >= ROLE_RANK[min_role]


# ------------------------------------------------------------- missions
def create_whatsapp_mission(store, link: dict, objective: str) -> dict:
    """Create + enqueue a mission from a WhatsApp message, exactly as
    the Telegram door does (the chat contract is shared — the
    platform default, classifier-resolved task type, live_web never
    silently enabled): create_mission, a MissionCreated event marked
    origin=whatsapp, an ordinary mission_run job, and an outbox watch
    record so the result finds its way back subject to the window
    rules."""
    tenant_id, user_id = link["tenant_id"], link["user_id"]
    title = objective if len(objective) <= 80 else objective[:77] + "…"
    contract = telegram_contract(objective)
    mission = store.create_mission(
        tenant_id, user_id, title, contract,
        float(contract["constraints"]["max_cost_usd"]))
    store.put_event(tenant_id, event_envelope(
        tenant_id, "mission", mission["id"], "MissionCreated",
        {"title": title, "origin": "whatsapp", "wa_id": link["wa_id"]}))
    job = store.enqueue_job(tenant_id, mission["id"], kind="mission_run")
    store.put_whatsapp_outbox(tenant_id, {
        "outbox_id": f"mission_result:{mission['id']}", "kind": "mission_result",
        "tenant_id": tenant_id, "user_id": user_id, "wa_id": link["wa_id"],
        "mission_id": mission["id"], "status": "watching",
        "created_at": now_iso(), "pushed_at": None, "last_error": None})
    link["last_mission_id"] = mission["id"]
    store.put_whatsapp_link(link)
    return {"mission": mission, "job_id": job["id"]}


def _deliver_result(store, client: WhatsAppClient, link: dict,
                    mission: dict) -> None:
    """Send a terminal mission's full result into the window: the
    summary text (chunked), then — for completed missions — one
    interactive message carrying the [🧾 Proof Pack] [⚔️ New Duel]
    reply buttons. Callers have already established the window is
    open; a send failure raises and the outbox record stays pending
    for the next attempt."""
    tenant_id = link["tenant_id"]
    _send_long(client, link["wa_id"],
               _mission_result_text(store, tenant_id, mission))
    if mission["status"] == "completed":
        client.send_buttons(
            link["wa_id"], "Mission finished — what next?",
            [(build_proof_callback(mission["id"]), "🧾 Proof Pack"),
             (build_duel_callback(mission["id"]), "⚔️ New Duel")])


# ------------------------------------------------------------- arena over chat
def send_duel(store, client: WhatsAppClient, link: dict, duel: dict) -> dict:
    """Deliver a blind duel: an intro, then each side's output as
    plain text followed by a short interactive message whose single
    reply button votes for that side. (Interactive bodies cap at
    1024 chars, so the output itself rides as text; the button
    message is just the call to action.) The bookkeeping mirrors the
    Telegram record so the vote flow resolves identically."""
    view = arena_mod.public_duel(duel)
    tenant_id, wa_id = link["tenant_id"], link["wa_id"]
    _send_long(client, wa_id,
               f"⚔️ Arena duel ({duel['task_type']})\n"
               f"Prompt: {duel['prompt']}\n\n"
               "Two agents answered blind — no names until you vote. "
               "Pick the better answer; the result feeds the agents' "
               "measured record.")
    for side, label in (("a", "A"), ("b", "B")):
        _send_long(client, wa_id,
                   f"—— Side {label} ——\n\n{view[f'side_{side}']['output']}")
        client.send_buttons(
            wa_id, f"Vote for Side {label}?",
            [(build_vote_callback(duel["duel_id"], side), "🏆 This one wins")])
    record = {"duel_id": duel["duel_id"], "tenant_id": tenant_id,
              "wa_id": wa_id, "user_id": link["user_id"],
              "created_at": now_iso()}
    store.put_whatsapp_duel(tenant_id, record)
    return record


async def _start_duel(store, orchestrator, client, link: dict,
                      task_type: str, prompt: str) -> None:
    try:
        duel = await arena_mod.create_duel(
            store, orchestrator, link["tenant_id"], link["user_id"],
            task_type, prompt)
    except arena_mod.DuelError as exc:
        _send_long(client, link["wa_id"], f"⚔️ Couldn't start that duel: {exc}")
        return
    except KeyError:
        _send_long(client, link["wa_id"],
                   "⚔️ Couldn't start that duel: an agent wasn't found.")
        return
    send_duel(store, client, link, duel)


# ------------------------------------------------------------- update handling
async def handle_payload(store, orchestrator, payload: dict, cfg=None,
                         client: Optional[WhatsAppClient] = None,
                         now: Optional[datetime] = None, vault=None) -> dict:
    """Route one webhook delivery. Messages are deduped by wamid
    before anything runs; the sender's window timestamp is refreshed
    from every fresh inbound; and after a linked user's message is
    handled, any held/alerted result pushes for that number are
    flushed — the reply that reopens the window receives them.
    Never raises for a well-formed delivery (the endpoint answers 200
    either way, so Meta doesn't redeliver-storm); outcomes are
    reported in the returned summary.

    v18 routing (see the module docstring): each event is routed by
    the business number it arrived on. An explicitly passed client
    (tests, embedders) handles everything, exactly as v17. Otherwise
    the env number — and v17-shaped payloads with no metadata —
    use the env client; a number owned by a tenant's stored
    connection is answered with that tenant's client and scoped to
    that tenant; a number nobody owns is ignored (counted, not
    errored)."""
    cfg = cfg or channel_config()
    explicit = client is not None
    default_client = client or get_client(cfg)
    if default_client is None and not any(
            c.get("kind") == "whatsapp"
            for c in store.list_connections_all()):
        return {"ok": False, "error": "whatsapp not configured"}
    now = now or datetime.now(timezone.utc)
    processed = duplicates = ignored = 0
    errors: list[str] = []
    route_cache: dict = {}

    def _route_for(event):
        """(client, expected_tenant) for one event, or (None, None)
        when the event's business number is not ours to answer."""
        if explicit:
            return client, None
        pnid = event.get("phone_number_id")
        if not pnid or str(pnid) == str(cfg.whatsapp_phone_number_id):
            return default_client, None
        if pnid not in route_cache:
            from .whatsapp_onboard import tenant_for_phone_number_id
            tenant_id = tenant_for_phone_number_id(store, pnid)
            if tenant_id is None:
                route_cache[pnid] = None
            else:
                v = vault if vault is not None else _default_vault(cfg)
                route_cache[pnid] = (
                    tenant_client(store, v, tenant_id, cfg), tenant_id)
        return route_cache[pnid] or (None, None)

    for event in parse_webhook_payload(payload):
        wamid = event.get("wamid")
        if wamid and not store.mark_whatsapp_update(wamid):
            duplicates += 1
            continue
        ev_client, expected_tenant = _route_for(event)
        if ev_client is None:
            ignored += 1  # a business number nobody onboarded — stay silent
            continue
        processed += 1
        try:
            link = await _route_event(store, orchestrator, ev_client, event,
                                      now, expected_tenant=expected_tenant)
            if link is not None:
                deliver_pending_for_link(store, ev_client, link, now=now)
        except Exception as exc:  # surfaced in the summary, never a 500 loop
            errors.append(str(exc)[:300])
    return {"ok": True, "processed": processed, "duplicates": duplicates,
            "ignored": ignored, "errors": errors}


async def _route_event(store, orchestrator, client, event: dict,
                       now: datetime,
                       expected_tenant: Optional[str] = None) -> Optional[dict]:
    """Handle one normalised message event. Returns the sender's link
    (post-handling) when the sender is linked, else None — the caller
    flushes pending pushes for linked senders.

    expected_tenant (v18): the event arrived on that tenant's own
    business number, so only that tenant's links are honoured — a
    number linked under another tenant is treated as unlinked here,
    and a LINK code minted by another tenant is not redeemed."""
    wa_id = event.get("wa_id")
    if not wa_id:
        return None

    def _own(link):
        if link is None:
            return None
        if expected_tenant is not None \
                and link.get("tenant_id") != expected_tenant:
            return None
        return link

    if event["kind"] == "button":
        link = _own(store.get_whatsapp_link(wa_id))
        if link is None:
            _send_long(client, wa_id, LINK_PROMPT)
            return None
        link["last_inbound_at"] = _iso(now)
        store.put_whatsapp_link(link)
        await _handle_button(store, orchestrator, client, link,
                             event.get("button_id") or "")
        return link

    text = (event.get("text") or "").strip()
    link = _own(store.get_whatsapp_link(wa_id))

    # Linking: "LINK <code>" is honoured from any number, linked or
    # not (a fresh code rebinds the number, newest wins — the same
    # replace semantics as the Telegram door). On a tenant's own
    # business number (v18), only that tenant's codes bind.
    code = parse_link_text(text)
    if code is not None:
        if expected_tenant is not None:
            record = store.get_whatsapp_code(code)
            if record is not None \
                    and record.get("tenant_id") != expected_tenant:
                _send_long(client, wa_id,
                           "That link code is invalid, expired, or "
                           "already used. Generate a fresh one in "
                           "AgentQon (⚙ Advanced → WhatsApp) and send "
                           "LINK <code> again.")
                return link
        new_link = _redeem_code(store, code, wa_id, event.get("name"), now=now)
        if new_link is None:
            _send_long(client, wa_id,
                       "That link code is invalid, expired, or already "
                       "used. Generate a fresh one in AgentQon "
                       "(⚙ Advanced → WhatsApp) and send LINK <code> "
                       "again.")
            return link
        _send_long(client, wa_id,
                   "✅ Linked to your AgentQon workspace.\n\n" + HELP_TEXT)
        return new_link

    if link is None:
        _send_long(client, wa_id, LINK_PROMPT)
        return None

    link["last_inbound_at"] = _iso(now)
    store.put_whatsapp_link(link)
    if not text:
        return link  # nothing the front door can act on

    word = text.lstrip("/").strip()
    lowered = word.lower()
    if lowered == "help":
        _send_long(client, wa_id, HELP_TEXT)
    elif lowered == "status":
        _reply_status(store, client, link)
    elif lowered == "schedules":
        _reply_schedules(store, client, link)
    elif lowered == "arena" or lowered.startswith("arena "):
        topic = word[len("arena"):].strip()
        if not _can_act_now(store, link):
            _send_long(client, wa_id,
                       "Your AgentQon role is viewer — viewers can read "
                       "results but can't start duels.")
        elif not topic:
            _send_long(client, wa_id,
                       "Send: arena <your topic or question> — and two "
                       "agents will duel on it, blind.")
        else:
            await _start_duel(store, orchestrator, client, link,
                              classify_task(topic), topic)
    elif lowered == "mission" or lowered.startswith("mission "):
        objective = word[len("mission"):].strip()
        if not objective:
            _send_long(client, wa_id,
                       "Send: mission <what you want done> — or just "
                       "type it as a normal message.")
        else:
            _reply_create_mission(store, client, link, objective)
    else:
        _reply_create_mission(store, client, link, text)
    return link


def _reply_create_mission(store, client, link: dict, objective: str) -> None:
    wa_id = link["wa_id"]
    if not _can_act_now(store, link):
        _send_long(client, wa_id,
                   "Your AgentQon role is viewer — viewers can read "
                   "results but can't run missions. Ask a workspace "
                   "admin to raise your role.")
        return
    outcome = create_whatsapp_mission(store, link, objective)
    mission = outcome["mission"]
    _send_long(client, wa_id,
               f"🚀 Mission {mission['id'][:10]}… created and queued.\n"
               f"“{mission['title']}”\n"
               "I'll send the result here when it finishes. Send "
               "'status' to check on it meanwhile.\n"
               "Note: WhatsApp only lets me message freely within 24 "
               "hours of your last message — if the result lands "
               "later, I may send a short alert first and the full "
               "result the moment you reply.")


def _reply_status(store, client, link: dict) -> None:
    wa_id = link["wa_id"]
    mid = link.get("last_mission_id")
    mission = store.get_mission(link["tenant_id"], mid) if mid else None
    if mission is None:
        _send_long(client, wa_id,
                   "No missions yet from this number. Send me something "
                   "to work on.")
        return
    icon = {"completed": "✅", "failed": "❌", "cancelled": "🚫",
            "running": "⏳", "planned": "⏳"}.get(mission["status"], "ℹ️")
    _send_long(client, wa_id,
               f"{icon} Latest mission: “{mission['title']}”\n"
               f"Status: {mission['status']} · id {mission['id'][:10]}…")


def _reply_schedules(store, client, link: dict) -> None:
    mine = [s for s in store.list_schedules(link["tenant_id"])
            if s.get("created_by") == link["user_id"]]
    if not mine:
        _send_long(client, link["wa_id"],
                   "You have no schedules. Create recurring missions "
                   "in the web UI (⚙ Advanced → Schedules).")
        return
    lines = ["⏰ Your schedules:"]
    for s in mine:
        state = "on" if s.get("enabled") else "off"
        lines.append(f"• {s['title']} — {_cadence_text(s['cadence'])} · "
                     f"{state} · next {s.get('next_run_at') or '—'} · "
                     f"{s.get('run_count', 0)} run(s)")
    _send_long(client, link["wa_id"], "\n".join(lines))


# ------------------------------------------------------------- button replies
async def _handle_button(store, orchestrator, client, link: dict,
                         button_id: str) -> None:
    kind, rest = parse_callback(button_id)
    if kind == "vote" and len(rest) == 2:
        _handle_vote(store, client, link, rest[0], rest[1])
    elif kind == "pp" and len(rest) == 1:
        _handle_proof_pack(store, client, link, rest[0])
    elif kind == "duel" and len(rest) == 1:
        await _handle_new_duel(store, orchestrator, client, link, rest[0])
    else:
        _send_long(client, link["wa_id"], "Unknown action.")


def _owns_outbox(store, link: dict, mission_id: str) -> bool:
    """A pushed button is only honoured for the number it was pushed
    to: the outbox watch record ties mission -> tenant + wa_id."""
    record = store.get_whatsapp_outbox(
        link["tenant_id"], f"mission_result:{mission_id}")
    return (record is not None
            and str(record.get("wa_id")) == str(link["wa_id"]))


def _handle_vote(store, client, link: dict, duel_id: str, side: str) -> None:
    tenant_id = link["tenant_id"]
    duel = store.get_duel(tenant_id, duel_id)
    if duel is None:
        _send_long(client, link["wa_id"],
                   "That duel isn't yours, or it no longer exists.")
        return
    if duel["status"] != "awaiting_vote":
        _send_long(client, link["wa_id"], "This duel was already decided.")
        return
    try:
        duel = arena_mod.cast_vote(store, tenant_id, duel_id, side)
    except StateConflict:
        _send_long(client, link["wa_id"], "This duel was already decided.")
        return
    view = arena_mod.public_duel(duel)
    winner_side = view["winner_side"]
    _send_long(client, link["wa_id"],
               "🏆 Duel decided!\n"
               f"Side A was {view['side_a'].get('agent_id')}\n"
               f"Side B was {view['side_b'].get('agent_id')}\n"
               f"Winner: Side {winner_side.upper()} — "
               f"{view['winner_agent_id']}.\n"
               "Recorded in the arena ledger.")


def _handle_proof_pack(store, client, link: dict, mission_id: str) -> None:
    tenant_id, wa_id = link["tenant_id"], link["wa_id"]
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None or not _owns_outbox(store, link, mission_id):
        _send_long(client, wa_id,
                   "That mission isn't yours, or it no longer exists.")
        return
    if mission["status"] != "completed":
        _send_long(client, wa_id,
                   "Proof packs are issued once a mission completes.")
        return
    pack = build_proof_pack(store, tenant_id, mission_id)
    markdown = proof_pack_markdown(pack)
    store.put_artifact(tenant_id, mission_id, "proof_pack", pack,
                       {"produced_by": "proof-pack",
                        "pack_version": pack["pack_version"]})
    store.put_artifact(tenant_id, mission_id, "proof_pack_md", markdown,
                       {"produced_by": "proof-pack", "format": "markdown"})
    summary = pack["confidence_summary"]
    _send_long(client, wa_id,
               f"🧾 Proof Pack — {pack['title']}\n"
               f"Overall confidence: {summary['overall']} "
               f"({summary['overall_reason']})\n"
               f"Claims: {summary['claims_total']} — "
               f"✅ verified {summary['verified']} · "
               f"🟡 estimated {summary['estimated']} · "
               f"⚪ assumed {summary['assumed']}")
    media_id = client.upload_media(f"proof-pack-{mission_id}.md",
                                   markdown.encode("utf-8"))
    client.send_document(wa_id, media_id, f"proof-pack-{mission_id}.md",
                         caption=f"🧾 Proof Pack — {pack['title']}"[:1024])


async def _handle_new_duel(store, orchestrator, client, link: dict,
                           mission_id: str) -> None:
    tenant_id = link["tenant_id"]
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None or not _owns_outbox(store, link, mission_id):
        _send_long(client, link["wa_id"],
                   "That mission isn't yours, or it no longer exists.")
        return
    if not _can_act_now(store, link):
        _send_long(client, link["wa_id"],
                   "Your role (viewer) can't start duels.")
        return
    contract = mission.get("contract") or {}
    _send_long(client, link["wa_id"], "Starting a duel ⚔️")
    await _start_duel(store, orchestrator, client, link,
                      contract.get("task_type") or "strategy",
                      contract.get("objective") or mission["title"])


# ------------------------------------------------------------- outbox / window
def deliver_pending_for_link(store, client: WhatsAppClient, link: dict,
                             now: Optional[datetime] = None) -> list[str]:
    """Deliver this link's held/alerted full results now that the
    user has messaged (window open). Called from the inbound path
    after every linked message; the tick also retries. Returns the
    mission ids delivered."""
    now = now or datetime.now(timezone.utc)
    if not window_open(link, now):
        return []
    delivered = []
    for record in store.list_whatsapp_outbox():
        if record.get("status") not in ("alerted", "held"):
            continue
        if str(record.get("wa_id")) != str(link["wa_id"]):
            continue
        tenant_id = record["tenant_id"]
        mission = store.get_mission(tenant_id, record["mission_id"])
        if mission is None or mission["status"] not in _TERMINAL:
            continue
        try:
            _deliver_result(store, client, link, mission)
        except Exception:
            continue  # stays pending; the tick retries later
        record["status"] = "sent"
        record["pushed_at"] = now_iso()
        record["last_error"] = None
        store.put_whatsapp_outbox(tenant_id, record)
        delivered.append(mission["id"])
    return delivered


def tick_whatsapp_outbox(store, cfg=None, client: Optional[WhatsAppClient] = None,
                         now: Optional[datetime] = None, vault=None) -> dict:
    """Push results for whatsapp-origin missions that reached a
    terminal state — window-aware (see the module docstring).
    Mirrors the Telegram tick: called from the worker drain + main
    loop, a safe no-op when the channel is disabled, and one bad
    record never blocks the others. Statuses: watching (awaiting a
    decision) -> sent (full result delivered) | alerted (template
    sent out-of-window; full result pending the user's reply) |
    held (no template configured; pending the user's next message) |
    dropped (mission or link gone). A record is only marked sent
    after Meta accepted the full delivery, so failures retry.

    v18: unless a client is passed in, sends resolve PER RECORD
    TENANT — a tenant with its own connected business number gets
    its results pushed from that number with its token; everyone
    else uses the env client (app/whatsapp_client.resolve_client).
    A record whose tenant resolves to no client stays pending."""
    cfg = cfg or channel_config()
    if not channel_enabled(cfg):
        return {"pushed": [], "alerted": [], "held": [], "errors": [],
                "skipped": "whatsapp not configured"}
    now = now or datetime.now(timezone.utc)
    explicit = client is not None
    resolved: dict = {}
    _vault = vault

    def _client_for(tenant_id):
        nonlocal _vault
        if explicit:
            return client
        if tenant_id not in resolved:
            if _vault is None:
                _vault = _default_vault(cfg)
            resolved[tenant_id] = resolve_client(store, _vault,
                                                 tenant_id, cfg)
        return resolved[tenant_id]

    pushed, alerted, held, errors = [], [], [], []
    records = store.list_whatsapp_outbox()
    for record in records:
        if record.get("status") not in ("watching", "alerted", "held"):
            continue
        tenant_id = record["tenant_id"]
        try:
            mission = store.get_mission(tenant_id, record["mission_id"])
            if mission is None:
                record["status"] = "dropped"
                record["pushed_at"] = now_iso()
                store.put_whatsapp_outbox(tenant_id, record)
                continue
            if mission["status"] not in _TERMINAL:
                continue
            link = store.get_whatsapp_link(record["wa_id"])
            if link is None:
                record["status"] = "dropped"
                record["pushed_at"] = now_iso()
                store.put_whatsapp_outbox(tenant_id, record)
                continue
            rec_client = _client_for(tenant_id)
            if rec_client is None:
                continue  # no usable client for this tenant — stays pending
            if window_open(link, now):
                _deliver_result(store, rec_client, link, mission)
                record["status"] = "sent"
                record["pushed_at"] = now_iso()
                record["last_error"] = None
                store.put_whatsapp_outbox(tenant_id, record)
                pushed.append({"mission_id": mission["id"],
                               "tenant_id": tenant_id})
            elif record["status"] == "watching":
                # First decision point with the window shut.
                if cfg.whatsapp_template_name:
                    rec_client.send_template(
                        link["wa_id"], cfg.whatsapp_template_name,
                        cfg.whatsapp_template_lang,
                        body_params=[mission["title"]])
                    record["status"] = "alerted"
                    record["last_error"] = None
                    store.put_whatsapp_outbox(tenant_id, record)
                    alerted.append({"mission_id": mission["id"],
                                    "tenant_id": tenant_id})
                else:
                    record["status"] = "held"
                    store.put_whatsapp_outbox(tenant_id, record)
                    held.append({"mission_id": mission["id"],
                                 "tenant_id": tenant_id})
            # alerted/held with the window still shut: nothing to do —
            # the user's next inbound (or a later tick) delivers.
        except Exception as exc:  # one bad push must not stop the tick
            record["last_error"] = str(exc)[:300]
            store.put_whatsapp_outbox(tenant_id, record)
            errors.append({"mission_id": record.get("mission_id"),
                           "error": str(exc)[:300]})
    return {"pushed": pushed, "alerted": alerted, "held": held,
            "errors": errors}
