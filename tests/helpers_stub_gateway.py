"""A tiny local OpenAI-compatible gateway stub for tests.

Serves /v1/models and /v1/chat/completions on 127.0.0.1. When
expected_key is set, requests without `Authorization: Bearer <key>` get
401 — letting tests prove which credential was actually used. Chat
responses are valid OmniMind research/critic JSON so full missions can
run against it.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [{"id": "stub-model-1", "owned_by": "stub"},
          {"id": "stub-model-2", "owned_by": "stub"}]

CANDIDATE = {
    "title": "Stub candidate", "summary": "Produced by the stub gateway model.",
    "claims": [{"text": "Buyers in a single industry community are the most common adoption path",
                "source_ids": ["src_seed_smb_survey_2026"], "confidence_category": "supported"}],
    "assumptions": ["stub"], "risks": ["stub risk"],
    "estimated_cost_usd": 0.5, "time_to_learn": "2 weeks",
}


def start_stub(expected_key: str | None = None, models=None) -> tuple[ThreadingHTTPServer, str]:
    models = MODELS if models is None else models

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _auth_ok(self) -> bool:
            if expected_key is None:
                return True
            return self.headers.get("Authorization") == f"Bearer {expected_key}"

        def _send(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            if self.path.endswith("/models"):
                return self._send(200, {"data": models})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad key"})
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = payload.get("messages", [])
            system = messages[0]["content"] if messages else ""
            user = messages[1]["content"] if len(messages) > 1 else ""
            if "critic" in system.lower():
                m = re.search(r"cand_[a-z0-9]+", user)
                content = {"critiques": [{
                    "candidate_id": m.group(0) if m else "cand_unknown",
                    "target_claim_id": None, "issue": "Stub critique: evidence is thin.",
                    "severity": "medium", "requested_action": "Add stronger sourcing."}]}
            else:
                content = CANDIDATE
            return self._send(200, {
                "id": "chatcmpl-stub", "model": payload.get("model", "stub-model-1"),
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"
