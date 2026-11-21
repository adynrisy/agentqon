"""AgentQon — identity (Phase 4).

Two credential types behind one resolution path:

* **Accounts** — email + password (PBKDF2-HMAC-SHA256, per-user salt,
  constant-time verify) issuing opaque session tokens. Only the SHA-256
  hash of a token is stored, sessions expire and are revocable.
* **OIDC** — Bearer JWTs (RS256) verified against the issuer's JWKS:
  signature, issuer, audience and expiry are all checked locally; the
  token's email claim maps to a user (just-in-time provisioning).

Tenant access is never implied by identity alone: a membership row with
a role (owner > admin > member > viewer) must exist for the requested
tenant, otherwise the tenant is invisible (404 semantics).

Dev mode (AGENTQON_AUTH_MODE=dev) keeps the Phase 1-3 header identity
for local development and the test suite; it must never be used for a
public deployment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

ROLES = ("viewer", "member", "admin", "owner")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}

_PBKDF2_ITERATIONS = 210_000


# ---------------- passwords & sessions ----------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_b64, hash_b64 = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(base64.b64encode(dk).decode(), hash_b64)
    except Exception:
        return False


def new_session_token() -> str:
    return "omk_" + secrets.token_urlsafe(32)


# Brain keys (v21.2): scoped integration credentials for
# POST /v1/brain ONLY. Distinct prefix so every other credential path
# (sessions are `omk_…`, OIDC are JWTs) rejects them by construction;
# stored exactly like sessions — SHA-256 hash only, never the key.
BRAIN_KEY_PREFIX = "aqb_"


def new_brain_key() -> str:
    return BRAIN_KEY_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry(ttl_hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()


def session_valid(session: dict) -> bool:
    try:
        return datetime.fromisoformat(session["expires_at"]) > datetime.now(timezone.utc)
    except Exception:
        return False


# ---------------- identity ----------------
@dataclass
class Identity:
    user_id: str
    email: str
    tenant_id: str
    role: str
    via: str  # dev-header | session | oidc

    def require(self, min_role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[min_role]


# ---------------- OIDC (RS256 / JWKS) ----------------
def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


class OIDCVerifier:
    """Verifies OIDC ID tokens against the issuer's JWKS endpoint."""

    def __init__(self, issuer: str, audience: str):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._jwks: dict = {}
        self._jwks_fetched = 0.0

    def _keys(self) -> dict:
        if time.time() - self._jwks_fetched < 600 and self._jwks:
            return self._jwks
        url = f"{self.issuer}/.well-known/openid-configuration"
        with httpx.Client(timeout=10, trust_env=False) as client:
            config = client.get(url).json()
            jwks = client.get(config["jwks_uri"]).json()
        self._jwks = {k["kid"]: k for k in jwks.get("keys", [])}
        self._jwks_fetched = time.time()
        return self._jwks

    def verify(self, token: str) -> Optional[dict]:
        """Returns the claims dict, or None for ANY verification failure."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            header = json.loads(_b64url_decode(header_b64))
            claims = json.loads(_b64url_decode(payload_b64))
            if header.get("alg") != "RS256":
                return None
            jwk = self._keys().get(header.get("kid", ""))
            if not jwk:
                self._jwks_fetched = 0.0  # key rotation: refetch once
                jwk = self._keys().get(header.get("kid", ""))
                if not jwk:
                    return None
            n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
            e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
            public_key = rsa.RSAPublicNumbers(e, n).public_key()
            signing_input = f"{header_b64}.{payload_b64}".encode()
            public_key.verify(_b64url_decode(sig_b64), signing_input,
                              padding.PKCS1v15(), hashes.SHA256())
            if claims.get("iss", "").rstrip("/") != self.issuer:
                return None
            aud = claims.get("aud")
            if isinstance(aud, list):
                if self.audience not in aud:
                    return None
            elif aud != self.audience:
                return None
            if int(claims.get("exp", 0)) < int(time.time()):
                return None
            if not claims.get("sub"):
                return None
            return claims
        except Exception:
            return None
