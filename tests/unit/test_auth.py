"""Phase 4 unit tests: password hashing, OIDC verification, SSRF
request-time checks, vault key files + rewrap, rate counters."""
from __future__ import annotations

import base64
import json
import time

import pytest

from app import auth, netpolicy
from app.store import Store


# ---------------- passwords & sessions ----------------
def test_password_hash_roundtrip_and_wrong_password():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong password", h)
    assert not auth.verify_password("correct horse battery staple", "garbage")
    # salting: same password hashes differently
    assert auth.hash_password("correct horse battery staple") != h


def test_session_token_hash_is_not_the_token():
    tok = auth.new_session_token()
    assert tok.startswith("omk_")
    assert auth.token_hash(tok) != tok
    assert auth.token_hash(tok) == auth.token_hash(tok)


# ---------------- rate counters ----------------
def test_rate_hit_window(tmp_path):
    store = Store(db_path=str(tmp_path / "r.db"), artifacts_dir=str(tmp_path / "a"))
    for _ in range(3):
        allowed, _ = store.rate_hit("k", 3)
        assert allowed
    allowed, retry = store.rate_hit("k", 3)
    assert not allowed and retry >= 1
    allowed, _ = store.rate_hit("other-key", 3)
    assert allowed  # keys are independent


# ---------------- OIDC ----------------
def _make_oidc():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    jwk = {"kid": "test-1", "kty": "RSA",
           "n": base64.urlsafe_b64encode(pub.n.to_bytes(256, "big")).decode().rstrip("="),
           "e": base64.urlsafe_b64encode(pub.e.to_bytes(3, "big")).decode().rstrip("=")}
    verifier = auth.OIDCVerifier("https://idp.example.com", "omnimind-app")
    verifier._keys = lambda: {"test-1": jwk}

    def mint(claims, kid="test-1"):
        header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
        signing = f"{enc(header)}.{enc(claims)}"
        sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"

    base_claims = {"iss": "https://idp.example.com", "aud": "omnimind-app",
                   "sub": "user-123", "email": "person@example.com",
                   "exp": int(time.time()) + 600}
    return verifier, mint, base_claims


def test_oidc_verifier_accepts_valid_and_rejects_bad():
    verifier, mint, claims = _make_oidc()
    assert verifier.verify(mint(claims))["sub"] == "user-123"
    assert verifier.verify(mint({**claims, "aud": "other-app"})) is None
    assert verifier.verify(mint({**claims, "iss": "https://evil.example.com"})) is None
    assert verifier.verify(mint({**claims, "exp": int(time.time()) - 10})) is None
    assert verifier.verify(mint(claims, kid="unknown")) is None
    assert verifier.verify("not-a-jwt") is None
    # tampered payload
    good = mint(claims)
    head, _, sig = good.split(".")
    forged = base64.urlsafe_b64encode(json.dumps(
        {**claims, "email": "attacker@example.com"}).encode()).decode().rstrip("=")
    assert verifier.verify(f"{head}.{forged}.{sig}") is None


# ---------------- request-time SSRF ----------------
def test_request_time_ssrf(monkeypatch):
    monkeypatch.setattr(netpolicy, "_resolve", lambda host: ["169.254.169.254"])
    with pytest.raises(netpolicy.UrlPolicyError):
        netpolicy.assert_resolved_host_safe("https://api.example.com/v1")
    monkeypatch.setattr(netpolicy, "_resolve", lambda host: ["10.1.2.3"])
    with pytest.raises(netpolicy.UrlPolicyError) as ei:
        netpolicy.assert_resolved_host_safe("https://api.example.com/v1")
    assert ei.value.category == "private_resolution"
    # ...but allowed for explicitly local endpoints
    netpolicy.assert_resolved_host_safe("http://myhost.internal:8080/v1", allow_local=True)
    # benchmark-range egress answers (198.19.x) are NOT blocked
    monkeypatch.setattr(netpolicy, "_resolve", lambda host: ["198.19.0.5"])
    netpolicy.assert_resolved_host_safe("https://api.example.com/v1")
    # literal metadata IP is blocked even without resolution
    with pytest.raises(netpolicy.UrlPolicyError):
        netpolicy.assert_resolved_host_safe("http://169.254.169.254/latest")


# ---------------- vault key file + rewrap ----------------
def test_vault_key_file_rotation_and_rewrap(tmp_path):
    from cryptography.fernet import Fernet
    from app.vault import Vault, rewrap_all_secrets

    k1, k2 = Fernet.generate_key(), Fernet.generate_key()
    key_file = tmp_path / "vault.keys"
    key_file.write_text(k1.decode() + "\n")

    class S:  # minimal settings shim
        vault_key_file = str(key_file)

    store = Store(db_path=str(tmp_path / "v.db"), artifacts_dir=str(tmp_path / "a"))
    v1 = Vault.from_settings(S(), tmp_path)
    assert v1.active_version == "v1"
    version, token = v1.encrypt("tenant-secret-999")
    store.put_secret("org_x", "conn_1", version, token)

    # deploy v2: append the new key; old ciphertext still readable
    key_file.write_text(k1.decode() + "\n" + k2.decode() + "\n")
    v2 = Vault.from_settings(S(), tmp_path)
    assert v2.active_version == "v2"
    assert v2.decrypt("v1", token) == "tenant-secret-999"

    result = rewrap_all_secrets(store, v2)
    assert result["rewrapped"] == 1
    row = store.get_secret("org_x", "conn_1")
    assert row["key_version"] == "v2"
    # a vault holding ONLY the new key can now read it — old key retired
    key_file.write_text(k2.decode() + "\n")
    v_only2 = Vault({"v2": k2}, "v2")
    assert v_only2.decrypt(row["key_version"], row["ciphertext"]) == "tenant-secret-999"
    # shredded rows are skipped, never resurrected
    store.shred_secret("org_x", "conn_1")
    assert rewrap_all_secrets(store, v2)["rewrapped"] == 0
