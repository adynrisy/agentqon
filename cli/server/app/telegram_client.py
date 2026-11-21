"""AgentQon — Telegram Bot API client (v16).

The ONE place outbound Telegram calls happen. Every other module
talks to this client; nothing else in the codebase knows the Bot API
exists. The base URL comes from AGENTQON_TELEGRAM_API_BASE
(app/config.py), so tests and the live proof point the whole channel
at a local stub server by changing one environment variable.

Deliberately small: the four methods the front door uses
(sendMessage, sendDocument, editMessageText, editMessageReplyMarkup,
answerCallbackQuery). Plain text only — no parse_mode anywhere, so
agent/persona output (untrusted text) can never inject markup into a
user's chat.
"""
from __future__ import annotations

from typing import Optional

import httpx


class TelegramError(RuntimeError):
    """The Bot API answered ok=false, or the call itself failed."""


class TelegramClient:
    def __init__(self, token: str, api_base: str, timeout: float = 15.0):
        if not token:
            raise ValueError("a bot token is required")
        self._base = f"{api_base.rstrip('/')}/bot{token}"
        self._timeout = timeout

    def _call(self, method: str, payload: Optional[dict] = None,
              files: Optional[dict] = None) -> dict:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                if files:
                    r = client.post(f"{self._base}/{method}",
                                    data=payload or {}, files=files)
                else:
                    r = client.post(f"{self._base}/{method}",
                                    json=payload or {})
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: transport error: {exc}") from exc
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code != 200 or not body.get("ok", False):
            desc = body.get("description") or f"HTTP {r.status_code}"
            raise TelegramError(f"{method}: {desc}")
        return body.get("result") or {}

    # ------------------------------------------------------------- sends
    def send_message(self, chat_id, text: str,
                     reply_markup: Optional[dict] = None) -> dict:
        payload: dict = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def send_document(self, chat_id, filename: str, data: bytes,
                      caption: Optional[str] = None) -> dict:
        payload: dict = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption[:1024]
        return self._call("sendDocument", payload,
                          files={"document": (filename, data,
                                              "application/octet-stream")})

    def edit_message_text(self, chat_id, message_id: int, text: str,
                          reply_markup: Optional[dict] = None) -> dict:
        payload: dict = {"chat_id": chat_id, "message_id": message_id,
                         "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", payload)

    def edit_message_reply_markup(self, chat_id, message_id: int,
                                  reply_markup: Optional[dict] = None) -> dict:
        """Attach or clear a message's inline keyboard (None clears)."""
        payload: dict = {"chat_id": chat_id, "message_id": message_id,
                         "reply_markup": reply_markup or {"inline_keyboard": []}}
        return self._call("editMessageReplyMarkup", payload)

    def answer_callback_query(self, callback_query_id: str,
                              text: Optional[str] = None) -> dict:
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        return self._call("answerCallbackQuery", payload)

    def get_me(self) -> dict:
        return self._call("getMe")
