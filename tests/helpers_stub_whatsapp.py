"""A tiny local Meta Graph API stub for tests.

Records every WhatsApp Cloud API call the app makes (JSON message
payloads, and — for media uploads — the multipart filename + byte
count) and answers with plausible Graph API envelopes. Point the
channel at it with OMNIMIND_WHATSAPP_API_BASE=http://127.0.0.1:<port>
(the version + phone-number-id path segments are accepted as-is,
like the real /<version>/<phone_number_id>/<method> shape).

v18: the version-level endpoints behind Embedded Signup are stubbed
too — GET oauth/access_token (code swap: SHORT_<code>; long-lived
swap: LONG_<short token>; the code "bad-code" is refused), GET
debug_token (scopes from `debug_scopes`, both WhatsApp permissions
by default), GET /<phone_number_id> (display identity), and
POST/DELETE /<waba_id>/subscribed_apps. Every recorded call carries
the request's Authorization header ("_auth") and path ("_path") so
tests can prove WHICH tenant's token/number a send used.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BOTH_SCOPES = ["whatsapp_business_management", "whatsapp_business_messaging"]


class WhatsAppStub:
    def __init__(self):
        self.calls: list[dict] = []
        self._next = 0
        self._lock = threading.Lock()
        # v18 knobs: tests narrow these to exercise the scope gate.
        self.debug_scopes: list[str] = list(BOTH_SCOPES)
        self.phone_info = {"display_phone_number": "+1 555-0100",
                           "verified_name": "Stub Business"}

    def record(self, method: str, payload: dict) -> dict:
        with self._lock:
            self._next += 1
            n = self._next
            self.calls.append({"method": method, **payload})
        if method == "media":
            return {"id": f"media_stub_{n}"}
        if method in ("subscribed_apps", "subscribed_apps_delete"):
            return {"success": True}
        return {"messaging_product": "whatsapp",
                "contacts": [{"wa_id": payload.get("to")}],
                "messages": [{"id": f"wamid.stub.{n}"}]}

    def oauth(self, query: dict, meta: dict | None = None) -> tuple[int, dict]:
        """GET /<version>/oauth/access_token — code swap + fb_exchange."""
        self.record("oauth/access_token", {"query": query, **(meta or {})})
        if query.get("grant_type") == "fb_exchange_token":
            short = query.get("fb_exchange_token", "")
            return 200, {"access_token": f"LONG_{short}",
                         "token_type": "bearer", "expires_in": 5184000}
        code = query.get("code", "")
        if code == "bad-code":
            return 400, {"error": {"message": "bad code",
                                   "code": 100}}
        return 200, {"access_token": f"SHORT_{code}",
                     "token_type": "bearer", "expires_in": 3600}

    def debug(self, query: dict, meta: dict | None = None) -> tuple[int, dict]:
        self.record("debug_token", {"query": query, **(meta or {})})
        return 200, {"data": {"app_id": "stub-app-id", "type": "USER",
                              "is_valid": True, "expires_at": 0,
                              "scopes": list(self.debug_scopes)}}

    def phone(self, phone_number_id: str,
              meta: dict | None = None) -> tuple[int, dict]:
        self.record("phone_number",
                    {"phone_number_id": phone_number_id, **(meta or {})})
        return 200, {"id": phone_number_id, **self.phone_info}

    def find(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method]

    def messages(self) -> list[dict]:
        return self.find("messages")

    def of_type(self, mtype: str) -> list[dict]:
        return [c for c in self.messages() if c.get("type") == mtype]

    def texts_to(self, wa_id) -> list[dict]:
        return [c for c in self.of_type("text")
                if str(c.get("to")) == str(wa_id)]

    def all_text_bodies(self, wa_id) -> list[str]:
        return [(c.get("text") or {}).get("body", "")
                for c in self.texts_to(wa_id)]

    def buttons(self) -> list[dict]:
        """All reply buttons sent, flattened: {to, id, title}."""
        out = []
        for c in self.of_type("interactive"):
            for b in ((c.get("interactive") or {}).get("action")
                      or {}).get("buttons") or []:
                reply = b.get("reply") or {}
                out.append({"to": c.get("to"), "id": reply.get("id"),
                            "title": reply.get("title")})
        return out


def start_stub() -> tuple[ThreadingHTTPServer, WhatsAppStub, str]:
    stub = WhatsAppStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            parts = urlsplit(self.path)
            m = re.match(r"^/v[\d.]+/[^/]+/(\w+)$", parts.path)
            if not m:
                return self._send(404, {"error": {"message": "no method"}})
            method = m.group(1)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "")
            meta = {"_auth": self.headers.get("Authorization"),
                    "_path": parts.path}
            if "multipart/form-data" in ctype:
                payload = {"multipart": True, **meta}
                fm = re.search(rb'filename="([^"]+)"', raw)
                payload["filename"] = fm.group(1).decode() if fm else None
                payload["bytes"] = len(raw)
                result = stub.record(method, payload)
                return self._send(200, result)
            payload = json.loads(raw or b"{}")
            payload.update(meta)
            result = stub.record(method, payload)
            return self._send(200, result)

        def do_GET(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            seg = [s for s in parts.path.split("/") if s]
            meta = {"_auth": self.headers.get("Authorization"),
                    "_path": parts.path}
            # seg = [version, ...] — the version segment is accepted as-is
            if len(seg) == 3 and seg[1] == "oauth" and seg[2] == "access_token":
                code, body = stub.oauth(query, meta)
                return self._send(code, body)
            if len(seg) == 2 and seg[1] == "debug_token":
                code, body = stub.debug(query, meta)
                return self._send(code, body)
            if len(seg) == 2:
                code, body = stub.phone(seg[1], meta)
                return self._send(code, body)
            return self._send(404, {"error": {"message": "no method"}})

        def do_DELETE(self):
            parts = urlsplit(self.path)
            m = re.match(r"^/v[\d.]+/[^/]+/(\w+)$", parts.path)
            if not m:
                return self._send(404, {"error": {"message": "no method"}})
            payload = {"_auth": self.headers.get("Authorization"),
                       "_path": parts.path}
            result = stub.record(m.group(1) + "_delete", payload)
            return self._send(200, result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, stub, f"http://127.0.0.1:{server.server_address[1]}"
