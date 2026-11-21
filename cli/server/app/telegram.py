"""AgentQon — Telegram front door (v16).

Run the platform from chat. A Telegram chat links to ONE AgentQon
account (tenant + user + role snapshot) through a one-time code
generated in the web UI; from then on the chat can:

  - send a mission as a plain message (or /mission <text>) — created
    through the normal contract path (same classifier, same
    classification gate; live_web is never silently enabled) and
    enqueued as an ordinary mission_run job;
  - ask /status for the latest mission;
  - RECEIVE the result when the mission finishes: the outbox tick
    (worker drain + main loop, mirroring app/schedules.py) watches
    telegram-origin missions and pushes a summary with
    [🧾 Proof Pack] [⚔️ New Duel] inline buttons;
  - run /arena duels and vote with inline buttons — votes go through
    app/arena.py's own cast_vote, never a reimplementation;
  - list /schedules.

The channel is configuration-gated: with AGENTQON_TELEGRAM_BOT_TOKEN
unset, every function here no-ops or reports "not configured" and the
rest of the platform is untouched. All outbound calls go through
app/telegram_client.py (the only module that knows the Bot API), and
all bot output is PLAIN TEXT — persona output is untrusted and must
never be able to inject markup into a user's chat.

Webhook safety: updates are deduplicated by update_id (Telegram
retries deliveries; a retry must never create a second mission), the
shared secret is checked by the endpoint before an update is routed,
and every object a callback can name is re-resolved inside the
caller's tenant — a button payload naming another tenant's duel or
mission fails politely instead of leaking or acting.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import arena as arena_mod
from .auth import ROLE_RANK
from .config import load_settings
from .domain import (StateConflict, event_envelope,
                     MISSION_BUDGET_CEILING_USD, new_id, now_iso)
from .proofpack import build_proof_pack, proof_pack_markdown
from .tasktypes import classify_task
from .telegram_client import TelegramClient

LINK_CODE_TTL_MINUTES = 10
MESSAGE_LIMIT = 4096  # Telegram's hard cap on message text
_CHUNK_LIMIT = 4000   # headroom for the part markers
_TERMINAL = ("completed", "failed", "cancelled")
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

HELP_TEXT = (
    "AgentQon commands:\n"
    "• Send any message — it becomes a mission, and I reply here with "
    "the result when it finishes.\n"
    "• /mission <text> — same thing, explicitly.\n"
    "• /status — the status of your latest mission.\n"
    "• /arena <topic> — two agents duel blind on your topic; you pick "
    "the winner.\n"
    "• /schedules — your recurring missions.\n"
    "• /help — this list."
)

LINK_PROMPT = (
    "👋 This chat isn't linked to an AgentQon account yet.\n\n"
    "To link it:\n"
    "1. Open AgentQon in your browser → ⚙ Advanced → Telegram panel.\n"
    "2. Tap “Generate link code”.\n"
    "3. Send me: /start <code>\n\n"
    "The code works once and expires in 10 minutes."
)


# ------------------------------------------------------------- config
def channel_config():
    """Live settings for the channel. Read fresh from the environment
    (load_settings parses os.environ on every call) so a stub API base
    can be pointed at the channel in tests without a process restart."""
    return load_settings()


def channel_enabled(cfg=None) -> bool:
    cfg = cfg or channel_config()
    return bool(cfg.telegram_bot_token)


def get_client(cfg=None) -> Optional[TelegramClient]:
    cfg = cfg or channel_config()
    if not cfg.telegram_bot_token:
        return None
    return TelegramClient(cfg.telegram_bot_token, cfg.telegram_api_base)


# ------------------------------------------------------------- text hygiene
def chunk_text(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split text for Telegram's 4096-char message cap, preferring
    paragraph boundaries; a single over-long paragraph is hard-split.
    Multi-part results get a trailing "(part i/n)" marker per part."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(para) > limit:
            if current:
                parts.append(current)
                current = ""
            for i in range(0, len(para), limit):
                parts.append(para[i:i + limit])
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            parts.append(current)
            current = para
    if current:
        parts.append(current)
    if len(parts) == 1:
        return parts
    return [f"{p}\n\n(part {i + 1}/{len(parts)})"
            for i, p in enumerate(parts)]


def parse_command(text: str) -> tuple[Optional[str], str]:
    """'/mission@AgentQonBot do the thing' -> ('mission', 'do the thing');
    plain text -> (None, text)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, text
    head, _, rest = text.partition(" ")
    command = head[1:].split("@", 1)[0].lower()
    return command or None, rest.strip()


def build_vote_callback(duel_id: str, side: str) -> str:
    return f"vote:{duel_id}:{side}"


def build_proof_callback(mission_id: str) -> str:
    return f"pp:{mission_id}"


def build_duel_callback(mission_id: str) -> str:
    return f"duel:{mission_id}"


def parse_callback(data: str) -> tuple[str, list[str]]:
    parts = (data or "").split(":")
    return parts[0], parts[1:]


# ------------------------------------------------------------- linking
def _parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_link_code(store, tenant_id: str, user_id: str, role: str,
                  cfg=None, now: Optional[datetime] = None) -> dict:
    """Mint a one-time link code for (tenant, user). Minting replaces
    any still-unused codes for the same user — the newest code is the
    only live one."""
    cfg = cfg or channel_config()
    now = now or datetime.now(timezone.utc)
    store.delete_telegram_codes_for(tenant_id, user_id)
    code = secrets.token_urlsafe(9)
    record = {"code": code, "tenant_id": tenant_id, "user_id": user_id,
              "role": role, "created_at": _iso(now),
              "expires_at": _iso(now + timedelta(minutes=LINK_CODE_TTL_MINUTES))}
    store.put_telegram_code(record)
    username = cfg.telegram_bot_username
    return {**record,
            "bot_username": username or None,
            "deep_link": (f"https://t.me/{username}?start={code}"
                          if username else None)}


def link_status(store, tenant_id: str, user_id: str, cfg=None) -> dict:
    cfg = cfg or channel_config()
    link = store.get_telegram_link_for_user(tenant_id, user_id)
    return {"configured": channel_enabled(cfg),
            "bot_username": cfg.telegram_bot_username or None,
            "linked": link is not None,
            "chat_id": link["chat_id"] if link else None,
            "telegram_username": (link or {}).get("telegram_username"),
            "linked_at": (link or {}).get("linked_at")}


def unlink_account(store, tenant_id: str, user_id: str) -> bool:
    link = store.get_telegram_link_for_user(tenant_id, user_id)
    if link is None:
        return False
    store.delete_telegram_codes_for(tenant_id, user_id)
    return store.delete_telegram_link(link["chat_id"])


def _redeem_code(store, code: str, chat: dict, from_user: dict,
                 now: Optional[datetime] = None) -> Optional[dict]:
    """Validate + consume a link code and bind the chat. None = the
    code is unknown, expired, or already used."""
    now = now or datetime.now(timezone.utc)
    record = store.get_telegram_code(code)
    if record is None:
        return None
    if _parse_iso(record["expires_at"]) <= now:
        store.delete_telegram_code(code)
        return None
    store.delete_telegram_code(code)  # single-use: consumed either way
    link = {"chat_id": chat["id"],
            "telegram_user_id": from_user.get("id"),
            "telegram_username": (from_user.get("username")
                                  or chat.get("username")),
            "tenant_id": record["tenant_id"], "user_id": record["user_id"],
            "role": record["role"], "linked_at": _iso(now),
            "last_mission_id": None}
    store.put_telegram_link(link)
    store.audit(record["tenant_id"], record["user_id"], "telegram.linked",
                "telegram_link", str(chat["id"]), {})
    return link


# ------------------------------------------------------------- sending
def _send_long(client: TelegramClient, chat_id, text: str,
               reply_markup: Optional[dict] = None) -> list[int]:
    """Send text chunked to Telegram's cap; the inline keyboard (if
    any) rides the LAST chunk. Returns the message ids in order."""
    parts = chunk_text(text)
    ids = []
    for i, part in enumerate(parts):
        markup = reply_markup if i == len(parts) - 1 else None
        result = client.send_message(chat_id, part, reply_markup=markup)
        if result.get("message_id") is not None:
            ids.append(result["message_id"])
    return ids


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
def telegram_contract(objective: str) -> dict:
    """The mission contract a chat message creates — field for field
    the platform default the web creates for the same words: the
    deterministic classifier resolves the task type at creation, the
    classification is the platform default (internal), and live_web
    stays None so the classification gate decides. A chat message
    never silently enables live web."""
    return {
        "objective": objective,
        "deliverables": ["Decision brief"],
        "acceptance": ["Material claims cite sources"],
        "constraints": {"max_cost_usd": MISSION_BUDGET_CEILING_USD},
        "tools": {"allow": ["web.read", "artifact.write", "artifact.read"],
                  "deny": [], "live_web": None},
        "approval_policy": {"external_side_effects": "required"},
        "data_classification": "internal",
        "task_type": classify_task(objective),
    }


def create_telegram_mission(store, link: dict, objective: str) -> dict:
    """Create + enqueue a mission from a chat, exactly as a schedule
    firing does: create_mission, a MissionCreated event marked
    origin=telegram, an ordinary mission_run job — and an outbox watch
    record so the result is pushed back to this chat on completion."""
    tenant_id, user_id = link["tenant_id"], link["user_id"]
    title = objective if len(objective) <= 80 else objective[:77] + "…"
    contract = telegram_contract(objective)
    mission = store.create_mission(
        tenant_id, user_id, title, contract,
        float(contract["constraints"]["max_cost_usd"]))
    store.put_event(tenant_id, event_envelope(
        tenant_id, "mission", mission["id"], "MissionCreated",
        {"title": title, "origin": "telegram", "chat_id": link["chat_id"]}))
    job = store.enqueue_job(tenant_id, mission["id"], kind="mission_run")
    store.put_telegram_outbox(tenant_id, {
        "outbox_id": f"mission_result:{mission['id']}", "kind": "mission_result",
        "tenant_id": tenant_id, "user_id": user_id, "chat_id": link["chat_id"],
        "mission_id": mission["id"], "status": "watching",
        "created_at": now_iso(), "pushed_at": None, "last_error": None})
    link["last_mission_id"] = mission["id"]
    store.put_telegram_link(link)
    return {"mission": mission, "job_id": job["id"]}


def _mission_result_text(store, tenant_id: str, mission: dict) -> str:
    """The pushed summary: what the mission concluded, in the
    candidate's own recorded words — summary + deliverable of the
    selected candidate (or the first candidate if none is selected)."""
    icon = {"completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(
        mission["status"], "ℹ️")
    lines = [f"{icon} Mission {mission['status']} — {mission['title']}"]
    if mission["status"] == "completed":
        candidates = store.list_candidates(tenant_id, mission["id"])
        selected = next((c for c in candidates
                         if c["candidate_id"] == mission.get("selected_candidate_id")),
                        candidates[0] if candidates else None)
        if selected:
            lines.append("")
            lines.append(f"Selected: {selected.get('title', '')}")
            body = (selected.get("deliverable")
                    or selected.get("summary") or "").strip()
            if body:
                lines.append("")
                lines.append(body)
        lines.append("")
        lines.append(f"Run cost ${float(mission.get('spent', 0)):.2f} · "
                     "full record in the web UI.")
    elif mission["status"] == "failed":
        lines.append("The run failed — open the mission in the web UI "
                     "for the event log.")
    return "\n".join(lines)


# ------------------------------------------------------------- arena over chat
def send_duel(store, client: TelegramClient, link: dict, duel: dict) -> dict:
    """Deliver a blind duel to a chat: an intro, then Side A and Side B
    as separate messages, each carrying its own [🏆 This one wins]
    button. The message bookkeeping (which message holds which side's
    button) is stored so the vote reveal can clear both keyboards."""
    view = arena_mod.public_duel(duel)
    tenant_id, chat_id = link["tenant_id"], link["chat_id"]
    _send_long(client, chat_id,
               f"⚔️ Arena duel ({duel['task_type']})\n"
               f"Prompt: {duel['prompt']}\n\n"
               "Two agents answered blind — no names until you vote. "
               "Pick the better answer; the result feeds the agents' "
               "measured record.")
    button_ids: dict[str, Optional[int]] = {}
    for side, label in (("a", "A"), ("b", "B")):
        markup = {"inline_keyboard": [[{
            "text": "🏆 This one wins",
            "callback_data": build_vote_callback(duel["duel_id"], side)}]]}
        ids = _send_long(client, chat_id,
                         f"—— Side {label} ——\n\n{view[f'side_{side}']['output']}",
                         reply_markup=markup)
        button_ids[side] = ids[-1] if ids else None
    record = {"duel_id": duel["duel_id"], "tenant_id": tenant_id,
              "chat_id": chat_id, "user_id": link["user_id"],
              "button_messages": button_ids, "created_at": now_iso()}
    store.put_telegram_duel(tenant_id, record)
    return record


async def _start_duel(store, orchestrator, client, link: dict,
                      task_type: str, prompt: str) -> None:
    try:
        duel = await arena_mod.create_duel(
            store, orchestrator, link["tenant_id"], link["user_id"],
            task_type, prompt)
    except arena_mod.DuelError as exc:
        _send_long(client, link["chat_id"], f"⚔️ Couldn't start that duel: {exc}")
        return
    except KeyError:
        _send_long(client, link["chat_id"],
                   "⚔️ Couldn't start that duel: an agent wasn't found.")
        return
    send_duel(store, client, link, duel)


# ------------------------------------------------------------- update handling
async def handle_update(store, orchestrator, update: dict, cfg=None,
                        client: Optional[TelegramClient] = None) -> dict:
    """Route one webhook update. Deduped by update_id before anything
    runs; never raises for a well-formed update (the endpoint answers
    200 either way, so Telegram doesn't retry-storm) — outcomes are
    reported in the returned summary."""
    cfg = cfg or channel_config()
    client = client or get_client(cfg)
    if client is None:
        return {"ok": False, "error": "telegram not configured"}
    update_id = update.get("update_id")
    if update_id is not None and not store.mark_telegram_update(update_id):
        return {"ok": True, "duplicate": True}
    try:
        if "callback_query" in update:
            await _handle_callback(store, orchestrator, client,
                                   update["callback_query"])
        elif "message" in update:
            await _handle_message(store, orchestrator, client,
                                  update["message"])
        return {"ok": True}
    except Exception as exc:  # surfaced in the summary, never a 500 loop
        return {"ok": False, "error": str(exc)[:300]}


async def _handle_message(store, orchestrator, client, msg: dict) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return  # stickers/photos/etc.: nothing the front door can act on
    command, args = parse_command(text)
    link = store.get_telegram_link(chat_id)

    if command == "start":
        if args:
            new_link = _redeem_code(store, args, chat, msg.get("from") or {})
            if new_link is None:
                _send_long(client, chat_id,
                           "That link code is invalid, expired, or already "
                           "used. Generate a fresh one in AgentQon "
                           "(⚙ Advanced → Telegram) and send /start <code> "
                           "again.")
            else:
                _send_long(client, chat_id,
                           "✅ Linked to your AgentQon workspace.\n\n"
                           + HELP_TEXT)
            return
        if link is None:
            _send_long(client, chat_id, LINK_PROMPT)
        else:
            _send_long(client, chat_id,
                       "You're already linked to AgentQon. /help lists "
                       "what I can do.")
        return

    if link is None:
        _send_long(client, chat_id, LINK_PROMPT)
        return

    if command == "help":
        _send_long(client, chat_id, HELP_TEXT)
    elif command == "status":
        _reply_status(store, client, link)
    elif command == "schedules":
        _reply_schedules(store, client, link)
    elif command == "arena":
        if not _can_act_now(store, link):
            _send_long(client, chat_id,
                       "Your AgentQon role is viewer — viewers can read "
                       "results but can't start duels.")
        elif not args:
            _send_long(client, chat_id,
                       "Send /arena <your topic or question> and two "
                       "agents will duel on it, blind.")
        else:
            await _start_duel(store, orchestrator, client, link,
                              classify_task(args), args)
    elif command == "mission":
        if not args:
            _send_long(client, chat_id,
                       "Send /mission <what you want done> — or just "
                       "type it as a normal message.")
        else:
            _reply_create_mission(store, client, link, args)
    elif command is None:
        _reply_create_mission(store, client, link, text)
    else:
        _send_long(client, chat_id,
                   f"I don't know /{command}. /help lists what I can do.")


def _reply_create_mission(store, client, link: dict, objective: str) -> None:
    chat_id = link["chat_id"]
    if not _can_act_now(store, link):
        _send_long(client, chat_id,
                   "Your AgentQon role is viewer — viewers can read "
                   "results but can't run missions. Ask a workspace "
                   "admin to raise your role.")
        return
    outcome = create_telegram_mission(store, link, objective)
    mission = outcome["mission"]
    _send_long(client, chat_id,
               f"🚀 Mission {mission['id'][:10]}… created and queued.\n"
               f"“{mission['title']}”\n"
               "I'll send the result here when it finishes. "
               "/status checks on it meanwhile.")


def _reply_status(store, client, link: dict) -> None:
    chat_id = link["chat_id"]
    mid = link.get("last_mission_id")
    mission = store.get_mission(link["tenant_id"], mid) if mid else None
    if mission is None:
        _send_long(client, chat_id,
                   "No missions yet from this chat. Send me something "
                   "to work on.")
        return
    icon = {"completed": "✅", "failed": "❌", "cancelled": "🚫",
            "running": "⏳", "planned": "⏳"}.get(mission["status"], "ℹ️")
    _send_long(client, chat_id,
               f"{icon} Latest mission: “{mission['title']}”\n"
               f"Status: {mission['status']} · id {mission['id'][:10]}…")


def _cadence_text(cadence: dict) -> str:
    kind = cadence.get("kind")
    if kind == "interval_hours":
        return f"every {cadence.get('every_hours')}h"
    if kind == "daily":
        return f"daily at {cadence.get('time_of_day')} UTC"
    if kind == "weekly":
        day = _WEEKDAYS[int(cadence.get("weekday", 0))]
        return f"weekly on {day} at {cadence.get('time_of_day')} UTC"
    return kind or "?"


def _reply_schedules(store, client, link: dict) -> None:
    mine = [s for s in store.list_schedules(link["tenant_id"])
            if s.get("created_by") == link["user_id"]]
    if not mine:
        _send_long(client, link["chat_id"],
                   "You have no schedules. Create recurring missions "
                   "in the web UI (⚙ Advanced → Schedules).")
        return
    lines = ["⏰ Your schedules:"]
    for s in mine:
        state = "on" if s.get("enabled") else "off"
        lines.append(f"• {s['title']} — {_cadence_text(s['cadence'])} · "
                     f"{state} · next {s.get('next_run_at') or '—'} · "
                     f"{s.get('run_count', 0)} run(s)")
    _send_long(client, link["chat_id"], "\n".join(lines))


async def _handle_callback(store, orchestrator, client, cq: dict) -> None:
    data = cq.get("data") or ""
    cq_id = cq.get("id", "")
    message = cq.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    link = store.get_telegram_link(chat_id) if chat_id is not None else None
    if link is None:
        client.answer_callback_query(cq_id, "This chat isn't linked to "
                                            "an AgentQon account.")
        return
    kind, rest = parse_callback(data)
    if kind == "vote" and len(rest) == 2:
        _handle_vote(store, client, link, cq_id, rest[0], rest[1])
    elif kind == "pp" and len(rest) == 1:
        _handle_proof_pack(store, client, link, cq_id, rest[0])
    elif kind == "duel" and len(rest) == 1:
        await _handle_new_duel(store, orchestrator, client, link, cq_id,
                               rest[0])
    else:
        client.answer_callback_query(cq_id, "Unknown action.")


def _owns_outbox(store, link: dict, mission_id: str) -> bool:
    """A pushed button is only honoured for the chat it was pushed to:
    the outbox watch record ties mission -> tenant + chat."""
    record = store.get_telegram_outbox(
        link["tenant_id"], f"mission_result:{mission_id}")
    return (record is not None
            and str(record.get("chat_id")) == str(link["chat_id"]))


def _handle_vote(store, client, link: dict, cq_id: str, duel_id: str,
                 side: str) -> None:
    tenant_id = link["tenant_id"]
    duel = store.get_duel(tenant_id, duel_id)
    if duel is None:
        client.answer_callback_query(
            cq_id, "That duel isn't yours, or it no longer exists.")
        return
    if duel["status"] != "awaiting_vote":
        client.answer_callback_query(cq_id, "This duel was already decided.")
        return
    try:
        duel = arena_mod.cast_vote(store, tenant_id, duel_id, side)
    except StateConflict:
        client.answer_callback_query(cq_id, "This duel was already decided.")
        return
    # Clear both vote keyboards (the buttons are spent), then reveal.
    record = store.get_telegram_duel(tenant_id, duel_id)
    if record:
        for msg_id in (record.get("button_messages") or {}).values():
            if msg_id is not None:
                try:
                    client.edit_message_reply_markup(link["chat_id"], msg_id)
                except Exception:
                    pass  # an un-editable old message must not lose the vote
    view = arena_mod.public_duel(duel)
    winner_side = view["winner_side"]
    _send_long(client, link["chat_id"],
               "🏆 Duel decided!\n"
               f"Side A was {view['side_a'].get('agent_id')}\n"
               f"Side B was {view['side_b'].get('agent_id')}\n"
               f"Winner: Side {winner_side.upper()} — "
               f"{view['winner_agent_id']}.\n"
               "Recorded in the arena ledger.")
    client.answer_callback_query(cq_id, "Vote recorded 🏆")


def _handle_proof_pack(store, client, link: dict, cq_id: str,
                       mission_id: str) -> None:
    tenant_id, chat_id = link["tenant_id"], link["chat_id"]
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None or not _owns_outbox(store, link, mission_id):
        client.answer_callback_query(
            cq_id, "That mission isn't yours, or it no longer exists.")
        return
    if mission["status"] != "completed":
        client.answer_callback_query(
            cq_id, "Proof packs are issued once a mission completes.")
        return
    pack = build_proof_pack(store, tenant_id, mission_id)
    markdown = proof_pack_markdown(pack)
    store.put_artifact(tenant_id, mission_id, "proof_pack", pack,
                       {"produced_by": "proof-pack",
                        "pack_version": pack["pack_version"]})
    store.put_artifact(tenant_id, mission_id, "proof_pack_md", markdown,
                       {"produced_by": "proof-pack", "format": "markdown"})
    summary = pack["confidence_summary"]
    _send_long(client, chat_id,
               f"🧾 Proof Pack — {pack['title']}\n"
               f"Overall confidence: {summary['overall']} "
               f"({summary['overall_reason']})\n"
               f"Claims: {summary['claims_total']} — "
               f"✅ verified {summary['verified']} · "
               f"🟡 estimated {summary['estimated']} · "
               f"⚪ assumed {summary['assumed']}")
    client.send_document(chat_id, f"proof-pack-{mission_id}.md",
                         markdown.encode("utf-8"),
                         caption=f"🧾 Proof Pack — {pack['title']}"[:1024])
    client.answer_callback_query(cq_id, "Proof pack sent 🧾")


async def _handle_new_duel(store, orchestrator, client, link: dict,
                           cq_id: str, mission_id: str) -> None:
    tenant_id = link["tenant_id"]
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None or not _owns_outbox(store, link, mission_id):
        client.answer_callback_query(
            cq_id, "That mission isn't yours, or it no longer exists.")
        return
    if not _can_act_now(store, link):
        client.answer_callback_query(
            cq_id, "Your role (viewer) can't start duels.")
        return
    contract = mission.get("contract") or {}
    client.answer_callback_query(cq_id, "Starting a duel ⚔️")
    await _start_duel(store, orchestrator, client, link,
                      contract.get("task_type") or "strategy",
                      contract.get("objective") or mission["title"])


# ------------------------------------------------------------- outbox tick
def tick_telegram_outbox(store, cfg=None, client: Optional[TelegramClient] = None) -> dict:
    """Push results for telegram-origin missions that reached a
    terminal state. Mirrors the schedules tick: called from the worker
    drain + main loop, safe to call when the channel is disabled (a
    no-op), and one bad record never blocks the others. A record is
    marked sent only after Telegram accepted the message, so a failed
    push is retried on the next tick instead of being lost."""
    cfg = cfg or channel_config()
    if not channel_enabled(cfg):
        return {"pushed": [], "errors": [], "skipped": "telegram not configured"}
    client = client or get_client(cfg)
    pushed, errors = [], []
    for record in store.list_telegram_outbox(status="watching"):
        tenant_id = record["tenant_id"]
        try:
            mission = store.get_mission(tenant_id, record["mission_id"])
            if mission is None:
                record["status"] = "dropped"
                record["pushed_at"] = now_iso()
                store.put_telegram_outbox(tenant_id, record)
                continue
            if mission["status"] not in _TERMINAL:
                continue
            markup = None
            if mission["status"] == "completed":
                markup = {"inline_keyboard": [[
                    {"text": "🧾 Proof Pack",
                     "callback_data": build_proof_callback(mission["id"])},
                    {"text": "⚔️ New Duel",
                     "callback_data": build_duel_callback(mission["id"])}]]}
            _send_long(client, record["chat_id"],
                       _mission_result_text(store, tenant_id, mission),
                       reply_markup=markup)
            record["status"] = "sent"
            record["pushed_at"] = now_iso()
            record["last_error"] = None
            store.put_telegram_outbox(tenant_id, record)
            pushed.append({"mission_id": mission["id"],
                           "tenant_id": tenant_id})
        except Exception as exc:  # one bad push must not stop the tick
            record["last_error"] = str(exc)[:300]
            store.put_telegram_outbox(tenant_id, record)
            errors.append({"mission_id": record.get("mission_id"),
                           "error": str(exc)[:300]})
    return {"pushed": pushed, "errors": errors}
