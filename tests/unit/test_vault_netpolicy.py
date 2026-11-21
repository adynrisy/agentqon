"""V3: credential vault + outbound URL (SSRF) policy."""
import pytest
from cryptography.fernet import Fernet

from app.netpolicy import UrlPolicyError, validate_endpoint_url
from app.vault import Vault, VaultError, mask_secret


def make_vault() -> Vault:
    return Vault({"v1": Fernet.generate_key()}, "v1")


def test_vault_roundtrip_and_masking():
    v = make_vault()
    version, token = v.encrypt("sk-super-secret-1234")
    assert version == "v1"
    assert "sk-super-secret" not in token
    assert v.decrypt(version, token) == "sk-super-secret-1234"
    assert mask_secret("sk-super-secret-1234") == "••••1234"
    assert mask_secret("abc") == "••••"
    mid = len(token) // 2  # flip a character inside the token body
    tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1:]
    with pytest.raises(VaultError):
        v.decrypt("v1", tampered)


def test_vault_rotation_keeps_old_versions_readable():
    old, new = Fernet.generate_key(), Fernet.generate_key()
    v_old = Vault({"v1": old}, "v1")
    _, token = v_old.encrypt("key-material")
    v_new = Vault({"v1": old, "v2": new}, "v2")
    assert v_new.decrypt("v1", token) == "key-material"  # old still readable
    version, token2 = v_new.encrypt("fresh")
    assert version == "v2" and v_new.decrypt("v2", token2) == "fresh"


ALL_KINDS = ("local_endpoint", "direct_provider", "omniroute_gateway")


def test_url_policy_blocks_metadata_and_bad_schemes():
    for url, kind in [("http://169.254.169.254/latest/meta-data", "local_endpoint"),
                      ("http://metadata.google.internal/", "local_endpoint"),
                      ("file:///etc/passwd", "direct_provider"),
                      ("https://user:pass@api.example.com/v1", "direct_provider")]:
        with pytest.raises(UrlPolicyError):
            validate_endpoint_url(url, kind=kind)


def test_url_policy_metadata_blocked_for_every_kind():
    # Metadata hosts/addresses and credentials-in-URL stay blocked no
    # matter which connection kind asks (v21.3).
    for kind in ALL_KINDS:
        for url in ("http://169.254.169.254/latest/meta-data",
                    "http://metadata.google.internal/",
                    "https://user:pass@api.example.com/v1"):
            with pytest.raises(UrlPolicyError):
                validate_endpoint_url(url, kind=kind)


def test_url_policy_local_targets_accepted_for_every_kind():
    # v21.3: locality is a property of the URL, not the connection kind —
    # loopback/private targets are acceptable for a local model server
    # AND for an API-key (BYOK) connection alike, with no env escape
    # hatch involved.
    for kind in ALL_KINDS:
        for url in ("http://127.0.0.1:11434/v1", "http://[::1]:11434/v1",
                    "http://192.168.1.50:8080/v1", "http://localhost:1234/v1"):
            ok = validate_endpoint_url(url, kind=kind)
            assert ok["is_local"] is True, (kind, url)


def test_url_policy_public_http_refused_for_every_kind():
    # Public endpoints still require https, whatever the kind. (A
    # genuinely public IP literal: documentation ranges like
    # 203.0.113.0/24 are is_private in ipaddress and classify as local.)
    for kind in ALL_KINDS:
        with pytest.raises(UrlPolicyError) as exc:
            validate_endpoint_url("http://8.8.8.8:8080/v1", kind=kind)
        assert exc.value.category == "scheme"
        ok = validate_endpoint_url("https://api.openai.com/v1", kind=kind)
        assert ok["is_local"] is False


def test_url_policy_ipv6_loopback_parity():
    # ::1 is the same loopback as 127.0.0.1 and must be treated identically:
    # ipaddress flags it reserved as well as loopback, which used to get it
    # refused as "reserved_range" before the loopback rules could apply.
    for kind in ALL_KINDS:
        ok = validate_endpoint_url("http://[::1]:11434/v1", kind=kind)
        assert ok["is_local"] is True
    # Genuinely reserved / unspecified space stays blocked as reserved.
    for url in ["http://[::]/v1", "http://240.0.0.1/v1"]:
        with pytest.raises(UrlPolicyError) as exc:
            validate_endpoint_url(url, kind="local_endpoint")
        assert exc.value.category == "reserved_range", url
