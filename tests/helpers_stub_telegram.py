"""A tiny local Telegram Bot API stub for tests.

Records every Bot API call the app makes (method, JSON fields, and —
for sendDocument — the multipart filename + byte count) and answers
with plausible Bot API envelopes. Point the channel at it with
OMNIMIND_TELEGRAM_API_BASE=http://127.0.0.1:<port> (the token in the
path is ignored, like the real API's /bot<token>/<method> shape).
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class TelegramStub:
    def __init__(self):
        self.calls: list[dict] = []
        self._next_message_id = 1000
        self._lock = threading.Lock()

    def record(self, method: str, payload: dict) -> dict:
        with self._lock:
            self._next_message_id += 1
            mid = self._next_message_id
            self.calls.append({"method": method, **payload})
        return {"message_id": mid, "chat": {"id": payload.get("chat_id")},
                "date": 0}

    def find(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method]

    def messages_to(self, chat_id) -> list[dict]:
        return [c for c in self.find("sendMessage")
                if str(c.get("chat_id")) == str(chat_id)]


def start_stub() -> tuple[ThreadingHTTPServer, TelegramStub, str]:
    stub = TelegramStub()

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
            m = re.match(r"^/bot[^/]+/(\w+)$", self.path)
            if not m:
                return self._send(404, {"ok": False, "description": "no method"})
            method = m.group(1)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                payload = {"chat_id": None, "multipart": True}
                cm = re.search(rb'name="chat_id"\r\n\r\n([^\r]+)', raw)
                if cm:
                    payload["chat_id"] = cm.group(1).decode()
                fm = re.search(rb'filename="([^"]+)"', raw)
                payload["filename"] = fm.group(1).decode() if fm else None
                cap = re.search(rb'name="caption"\r\n\r\n([^\r]+)', raw)
                payload["caption"] = cap.group(1).decode() if cap else None
                payload["bytes"] = len(raw)
                result = stub.record(method, payload)
                return self._send(200, {"ok": True, "result": result})
            payload = json.loads(raw or b"{}")
            if method == "getMe":
                return self._send(200, {"ok": True, "result": {
                    "id": 999, "is_bot": True, "username": "omnimind_stub_bot"}})
            result = stub.record(method, payload)
            return self._send(200, {"ok": True, "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, stub, f"http://127.0.0.1:{server.server_address[1]}"
