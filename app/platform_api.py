"""AgentQon — API utilities: rate limits, webhook verification, deprecation,
secret rotation, graceful degradation.

- Rate limits: fixed-window, per key, with plain-words breach errors
  (never a bare number or code). Documented in docs/platform-behavior.md.
- Webhook verification: HMAC-SHA256 signature verification + dedup.
  This is a TESTED MODULE. It is NOT wired to a webhook endpoint here:
  wiring is NOT_TESTED until a webhook system ships, and no webhook
  system is faked to make the module look complete.
- Deprecation: a deprecated endpoint's response names its replacement.
- Secret rotation: a supported path that keeps the previous credential
  valid for a grace period, then expires it. Rotation is recorded.
- Graceful degradation (also top-15 #15): an optional integration's
  failure must not necessarily fail the mission. The limitation is
  explained in plain words and the mission continues degraded where
  safe. Core failures (model call, mission store) still fail — only
  integrations listed in OPTIONAL_INTEGRATIONS may degrade.
"""
from __future__ import annotations

import hashlib
import hmac
import time

# Optional integrations: their failure degrades a mission, never fails
# it outright. Anything not listed here is core and fails honestly.
OPTIONAL_INTEGRATIONS = {
    "live_research": "Live web research is unavailable, so the mission "
                     "continues with the built-in example material. "
                     "Results may be less current.",
    "telegram_delivery": "Delivery to Telegram failed, so the result stays "
                         "in the app for you to read here.",
    "whatsapp_delivery": "Delivery to WhatsApp failed, so the result stays "
                         "in the app for you to read here.",
    "extended_pack": "The optional extended library is unavailable, so "
                     "the mission continues with the built-in library.",
    "model_catalog_refresh": "The model list could not be refreshed, so the "
                             "mission continues with the last known list.",
}


class RateLimiter:
    """Fixed-window rate limiter. ``allow`` returns (allowed, message,
    retry_after_seconds). Breach messages are plain words."""

    def __init__(self, *, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> dict:
        moment = time.time() if now is None else now
        hits = [t for t in self._hits.get(key, [])
                if moment - t < self.window_seconds]
        if len(hits) >= self.limit:
            retry_after = int(self.window_seconds - (moment - hits[0])) + 1
            return {"allowed": False,
                    "reason": (f"Too many requests in a short time — please "
                               f"wait about {retry_after} seconds and try again."),
                    "retry_after_seconds": retry_after}
        hits.append(moment)
        self._hits[key] = hits
        return {"allowed": True, "reason": "", "retry_after_seconds": 0}


def sign_webhook(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex signature for a webhook body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookVerifier:
    """Verify webhook signatures and dedup deliveries. A delivery is
    accepted only when its signature matches (constant-time compare)
    AND its event id has not been seen before. Replay protection:
    an event id seen once is never processed twice."""

    def __init__(self, secret: str):
        self.secret = secret
        self._seen: set[str] = set()

    def verify(self, *, body: bytes, signature: str, event_id: str) -> dict:
        expected = sign_webhook(self.secret, body)
        if not hmac.compare_digest(expected, signature or ""):
            return {"accepted": False,
                    "reason": "The delivery signature does not match — "
                              "this delivery cannot be trusted."}
        if event_id in self._seen:
            return {"accepted": False,
                    "reason": "This delivery was already processed — "
                              "a repeat delivery is ignored, not run twice."}
        self._seen.add(event_id)
        return {"accepted": True, "reason": "Signature verified."}


def deprecated_response(payload: dict, *, replacement: str,
                        endpoint: str) -> dict:
    """Wrap a deprecated endpoint's response. The response itself says
    it is deprecated and names the replacement — the caller never has
    to guess where to go next."""
    out = dict(payload)
    out["deprecated"] = True
    out["deprecation_notice"] = (
        f"This endpoint ({endpoint}) is deprecated and will be removed in "
        f"a future version. Use {replacement} instead.")
    out["replacement"] = replacement
    return out


class SecretRotation:
    """Rotation path for API credentials held in a dict-like store.
    rotate() stores the new value, keeps the old one valid during a
    grace period, and records the rotation. After the grace period the
    old value stops working. Rotation is explicit: it never happens
    silently, and the previous value is never kept forever."""

    def __init__(self, store: dict, *, grace_seconds: int = 3600):
        self.store = store
        self.grace_seconds = grace_seconds
        self._previous: dict[str, tuple[str, float]] = {}
        self._log: list[dict] = []

    def rotate(self, name: str, new_value: str) -> dict:
        old = self.store.get(name)
        self.store[name] = new_value
        if old is not None:
            self._previous[name] = (old, time.time())
        self._log.append({"action": "rotate", "name": name, "at": time.time(),
                          "previous_kept_until": time.time() + self.grace_seconds})
        return {"rotated": name,
                "note": ("The new credential is live now. The previous one "
                         "still works briefly, then stops — update any "
                         "other copies before then."),
                "previous_valid_for_seconds": self.grace_seconds}

    def valid(self, name: str, value: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self.store.get(name) == value:
            return True
        prev = self._previous.get(name)
        if prev and prev[0] == value and moment - prev[1] <= self.grace_seconds:
            return True
        return False

    def log(self) -> list[dict]:
        return list(self._log)


def degrade(integration: str, error: str) -> dict:
    """Graceful degradation decision for an optional integration's
    failure. Returns a degraded-continuation record with a plain-words
    explanation. Core integrations (anything not optional) raise:
    their failure must fail the mission honestly, never degrade."""
    if integration not in OPTIONAL_INTEGRATIONS:
        raise ValueError(
            f"{integration!r} is not an optional integration — its failure "
            "must fail the mission honestly, not degrade it.")
    return {"continue_degraded": True,
            "integration": integration,
            "limitation": OPTIONAL_INTEGRATIONS[integration],
            "detail": error,
            "mission_failed": False}
