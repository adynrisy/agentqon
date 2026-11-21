"""AgentQon — WhatsApp Cloud API client (v17).

The ONE place outbound WhatsApp calls happen (Meta WhatsApp Business
Cloud API, over the Graph API). Every other module talks to this
client; nothing else in the codebase knows the Graph API exists. The
base URL and API version come from AGENTQON_WHATSAPP_API_BASE /
AGENTQON_WHATSAPP_API_VERSION (app/config.py), so tests and the live
proof point the whole channel at a local stub server by changing the
environment.

Deliberately small: the methods the front door uses — send a text
message, send an interactive reply-button message, send a template
message, upload media, send a document by media id, and (optionally)
mark an inbound message read. Plain text only: no formatting tricks,
so agent/persona output (untrusted text) is delivered exactly as
written.

Platform limits enforced here so callers cannot violate them by
accident: at most 3 reply buttons per interactive message, button
titles truncated to Meta's 20 characters, interactive body text
capped at 1024 characters (callers chunk longer content into plain
text messages first and use the button message only as the call to
action).

v18 adds the version-level half of the Graph API — WhatsAppGraph:
the OAuth code exchange behind Embedded Signup, token inspection
(debug_token), WABA webhook subscription (subscribed_apps) and
phone-number reads — plus the per-tenant resolver used by every
send path: a tenant with a stored WhatsApp connection (minted by
app/whatsapp_onboard.py) sends from ITS OWN number with ITS OWN
token; everyone else falls back to the v17 env-configured client.
"""
from __future__ import annotations

from typing import Optional

import httpx

MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_INTERACTIVE_BODY = 1024


class WhatsAppError(RuntimeError):
    """The Graph API answered with an error, or the call itself failed."""


class WhatsAppClient:
    def __init__(self, token: str, phone_number_id: str, api_base: str,
                 api_version: str = "v21.0", timeout: float = 15.0):
        if not token:
            raise ValueError("an access token is required")
        if not phone_number_id:
            raise ValueError("a phone number id is required")
        self._base = f"{api_base.rstrip('/')}/{api_version}/{phone_number_id}"
        self._media_base = f"{api_base.rstrip('/')}/{api_version}"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    def _post_json(self, url: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                r = client.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"POST {url}: transport error: {exc}") from exc
        return self._unwrap(r, url)

    def _unwrap(self, r, what: str) -> dict:
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code != 200 or (isinstance(body, dict) and body.get("error")):
            err = (body.get("error") or {}) if isinstance(body, dict) else {}
            desc = err.get("message") or f"HTTP {r.status_code}"
            raise WhatsAppError(f"{what}: {desc}")
        return body if isinstance(body, dict) else {}

    # ------------------------------------------------------------- sends
    def send_text(self, to: str, body: str) -> dict:
        """A free-form text message. Only deliverable inside the
        24-hour customer-service window — the adapter (app/whatsapp.py)
        owns that decision; this client just sends."""
        return self._post_json(f"{self._base}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": str(to), "type": "text",
            "text": {"body": body, "preview_url": False}})

    def send_buttons(self, to: str, body: str,
                     buttons: list) -> dict:
        """An interactive reply-button message. `buttons` is a list of
        (reply_id, title) pairs; Meta allows at most 3, titles ≤20
        chars. The user's tap comes back as an interactive reply whose
        id is the reply_id — the adapter's callback vocabulary."""
        replies = [{"type": "reply",
                    "reply": {"id": str(rid),
                              "title": str(title)[:MAX_BUTTON_TITLE]}}
                   for rid, title in list(buttons)[:MAX_BUTTONS]]
        return self._post_json(f"{self._base}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": str(to), "type": "interactive",
            "interactive": {"type": "button",
                            "body": {"text": body[:MAX_INTERACTIVE_BODY]},
                            "action": {"buttons": replies}}})

    def send_template(self, to: str, template_name: str, language: str,
                      body_params: Optional[list] = None) -> dict:
        """A pre-approved template message — the ONLY message kind
        Meta allows outside the 24-hour window. The front door uses
        one utility template whose body carries {{1}} = the mission
        title, as an invitation to reply (a reply reopens the window,
        and the held full result is then delivered)."""
        template: dict = {"name": template_name,
                          "language": {"code": language}}
        if body_params:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)}
                               for p in body_params]}]
        return self._post_json(f"{self._base}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": str(to), "type": "template", "template": template})

    def upload_media(self, filename: str, data: bytes,
                     mime: str = "text/markdown") -> str:
        """Upload a media file (multipart) and return its media id —
        the two-step pattern Meta requires for documents."""
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                r = client.post(
                    f"{self._base}/media",
                    headers=self._headers,
                    data={"messaging_product": "whatsapp", "type": mime},
                    files={"file": (filename, data, mime)})
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"media upload: transport error: {exc}") from exc
        body = self._unwrap(r, "media upload")
        media_id = body.get("id")
        if not media_id:
            raise WhatsAppError("media upload: no media id in the response")
        return str(media_id)

    def send_document(self, to: str, media_id: str, filename: str,
                      caption: Optional[str] = None) -> dict:
        payload: dict = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": str(to), "type": "document",
            "document": {"id": str(media_id), "filename": filename}}
        if caption:
            payload["document"]["caption"] = caption[:1024]
        return self._post_json(f"{self._base}/messages", payload)

    def mark_read(self, message_id: str) -> dict:
        """Optional blue-tick acknowledgement for an inbound message."""
        return self._post_json(f"{self._base}/messages", {
            "messaging_product": "whatsapp", "status": "read",
            "message_id": str(message_id)})


# =====================================================================
# v18 — version-level Graph operations (OAuth + WABA admin).
# WhatsAppClient above is bound to ONE phone number (the /messages
# and /media paths live under it); the Embedded Signup exchange works
# at the app/WABA level instead: swap the Facebook login code for a
# token, lengthen it, inspect its scopes, subscribe this app to the
# WABA's webhooks, and read the number's display identity. All of it
# still lives here — the one module that knows the Graph API exists.
# =====================================================================
class WhatsAppGraph:
    def __init__(self, app_id: str, app_secret: str, api_base: str,
                 api_version: str = "v21.0", timeout: float = 15.0):
        if not app_id:
            raise ValueError("a Meta app id is required")
        if not app_secret:
            raise ValueError("a Meta app secret is required")
        self._app_id = app_id
        self._app_secret = app_secret
        self._root = f"{api_base.rstrip('/')}/{api_version}"
        self._timeout = timeout

    # ---------------------------------------------------------- plumbing
    def _request(self, method: str, url: str, params: Optional[dict] = None,
                 token: Optional[str] = None,
                 payload: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                r = client.request(method, url, params=params, json=payload,
                                   headers=headers)
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"{method} {url}: transport error: {exc}") from exc
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code != 200 or (isinstance(body, dict) and body.get("error")):
            err = (body.get("error") or {}) if isinstance(body, dict) else {}
            desc = err.get("message") or f"HTTP {r.status_code}"
            raise WhatsAppError(f"{method} {url}: {desc}")
        return body if isinstance(body, dict) else {}

    def _token_from(self, body: dict, what: str) -> str:
        token = body.get("access_token")
        if not token:
            raise WhatsAppError(f"{what}: no access_token in the response")
        return str(token)

    # ------------------------------------------------------------- OAuth
    def exchange_code(self, code: str) -> str:
        """Embedded Signup step 1: the short-lived user token for the
        login code the browser received from FB.login."""
        body = self._request("GET", f"{self._root}/oauth/access_token",
                             params={"client_id": self._app_id,
                                     "client_secret": self._app_secret,
                                     "code": code})
        return self._token_from(body, "code exchange")

    def exchange_long_lived(self, token: str) -> str:
        """Embedded Signup step 2: swap the short-lived user token
        for a long-lived one (~60 days) — the token worth storing."""
        body = self._request("GET", f"{self._root}/oauth/access_token",
                             params={"grant_type": "fb_exchange_token",
                                     "client_id": self._app_id,
                                     "client_secret": self._app_secret,
                                     "fb_exchange_token": token})
        return self._token_from(body, "long-lived exchange")

    def debug_token(self, token: str) -> dict:
        """Inspect a user token with the APP access token
        (app_id|app_secret): validity, scopes, expiry. Returns the
        response's data object."""
        body = self._request(
            "GET", f"{self._root}/debug_token",
            params={"input_token": token,
                    "access_token": f"{self._app_id}|{self._app_secret}"})
        data = body.get("data")
        if not isinstance(data, dict):
            raise WhatsAppError("debug_token: no data object in the response")
        return data

    # --------------------------------------------------------- WABA admin
    def subscribe_app(self, waba_id: str, token: str) -> dict:
        """Subscribe THIS app to the WABA's webhooks — without it,
        Meta delivers nothing for the tenant's number."""
        return self._request("POST", f"{self._root}/{waba_id}/subscribed_apps",
                             token=token, payload={})

    def unsubscribe_app(self, waba_id: str, token: str) -> dict:
        return self._request("DELETE",
                             f"{self._root}/{waba_id}/subscribed_apps",
                             token=token)

    def phone_number_info(self, phone_number_id: str, token: str) -> dict:
        """The number's public identity: display_phone_number +
        verified_name (what the UI shows as 'connected as')."""
        return self._request(
            "GET", f"{self._root}/{phone_number_id}",
            params={"fields": "display_phone_number,verified_name"},
            token=token)


# =====================================================================
# v18 — per-tenant resolution. A tenant's WhatsApp connection (kind
# "whatsapp" in the BYOK store, secret in the vault) is authoritative
# for that tenant: sends use its phone_number_id + token. Resolution
# never raises — a missing/shredded/undecryptable credential simply
# means "no tenant client", and callers fall back or skip.
# =====================================================================
def find_whatsapp_connection(store, tenant_id: str) -> Optional[dict]:
    """The tenant's live WhatsApp connection row, or None."""
    for conn in store.list_connections(tenant_id):
        if conn.get("kind") == "whatsapp" and conn.get("state") == "active":
            return conn
    return None


def connection_metadata(conn: Optional[dict]) -> dict:
    """The WhatsApp metadata block minted at signup:
    {waba_id, phone_number_id, display_phone_number, verified_name}."""
    if not conn:
        return {}
    return dict((conn.get("data_policy") or {}).get("whatsapp") or {})


def tenant_client(store, vault, tenant_id: str, cfg) -> Optional[WhatsAppClient]:
    """A client bound to the TENANT's own number + token, or None
    when the tenant has no usable WhatsApp connection. `cfg` is the
    channel Settings (api base/version come from the environment —
    the same Graph deployment serves every tenant)."""
    if vault is None:
        return None
    conn = find_whatsapp_connection(store, tenant_id)
    if conn is None:
        return None
    meta = connection_metadata(conn)
    phone_number_id = meta.get("phone_number_id")
    if not phone_number_id:
        return None
    from .connections import get_secret_for_call
    try:
        token = get_secret_for_call(store, vault, tenant_id, conn["id"])
    except Exception:
        return None
    if not token:
        return None
    return WhatsAppClient(token, str(phone_number_id),
                          cfg.whatsapp_api_base, cfg.whatsapp_api_version)


def resolve_client(store, vault, tenant_id: str, cfg) -> Optional[WhatsAppClient]:
    """The send client for a tenant: its own connection when it has
    one, otherwise the v17 env-configured client, otherwise None."""
    client = tenant_client(store, vault, tenant_id, cfg)
    if client is not None:
        return client
    if cfg.whatsapp_token and cfg.whatsapp_phone_number_id:
        return WhatsAppClient(cfg.whatsapp_token,
                              cfg.whatsapp_phone_number_id,
                              cfg.whatsapp_api_base, cfg.whatsapp_api_version)
    return None
