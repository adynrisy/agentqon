"""Credential vault (Master Build V3, Part XXII).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC) from the
`cryptography` library — no custom crypto. Ciphertext lives in a
dedicated table (`credential_secrets`), separate from connection
metadata, and is only decrypted server-side at the moment of a gateway
call. Plaintext never reaches logs, events, API responses or artifacts.

Key management:
  - AGENTQON_VAULT_KEY (a Fernet key) is the production path; version is
    AGENTQON_VAULT_KEY_VERSION (default "v1"). Older keys can be supplied
    as AGENTQON_VAULT_KEY_V1, _V2, ... so rotation keeps old ciphertext
    readable until connections are rotated/shredded.
  - Development fallback: if no key is configured, a key file is created
    under the data directory (0600) — clearly a DEV convenience, not a
    production KMS. PROJECT_STATUS documents this.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Versioned rotation keys are OMNIMIND_VAULT_KEY_V<n> with a NUMERIC
# suffix. (v21.4: the old prefix scan also matched
# OMNIMIND_VAULT_KEY_VERSION itself and derived a bogus version
# "version" out of it — whose value, "v1"/"v2", is not a Fernet key,
# so the documented rotation setup failed vault construction.)
_VERSIONED_KEY_RE = re.compile(r"^OMNIMIND_VAULT_KEY_V(\d+)$")


class VaultError(Exception):
    pass


def mask_secret(secret: str) -> str:
    """Safe display form: bullets + last 4 chars (only if long enough)."""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "••••"
    return "••••" + secret[-4:]


class Vault:
    def __init__(self, keys: dict[str, bytes], active_version: str):
        if active_version not in keys:
            raise VaultError(f"active vault key version {active_version} not provided")
        self._fernets = {v: Fernet(k) for v, k in keys.items()}
        self.active_version = active_version

    # -- core ----------------------------------------------------------------
    def encrypt(self, secret: str) -> tuple[str, str]:
        token = self._fernets[self.active_version].encrypt(secret.encode()).decode()
        return self.active_version, token

    def decrypt(self, key_version: str, ciphertext: str) -> str:
        f = self._fernets.get(key_version)
        if f is None:
            raise VaultError(f"vault key version {key_version} unavailable (rotated away?)")
        try:
            return f.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise VaultError("ciphertext failed integrity check") from exc

    # -- construction ----------------------------------------------------------
    @classmethod
    def from_env(cls, data_dir: str | Path) -> "Vault":
        keys: dict[str, bytes] = {}
        active = os.environ.get("OMNIMIND_VAULT_KEY_VERSION", "v1")
        main = os.environ.get("OMNIMIND_VAULT_KEY")
        if main:
            keys[active] = main.encode()
        for name, value in os.environ.items():
            m = _VERSIONED_KEY_RE.match(name)
            if m and value:
                keys.setdefault(f"v{int(m.group(1))}", value.encode())
        if not keys:
            # DEV fallback: persistent key file outside source control.
            key_file = Path(data_dir) / "vault.key"
            key_file.parent.mkdir(parents=True, exist_ok=True)
            if key_file.exists():
                keys["v1"] = key_file.read_bytes().strip()
                # M2: a pre-existing key file may carry loose modes
                # from an older install — tighten it on every boot.
                try:
                    key_file.chmod(0o600)
                except OSError:
                    pass
            else:
                raw = Fernet.generate_key()
                key_file.write_bytes(raw)
                try:
                    key_file.chmod(0o600)
                except OSError:
                    pass
                keys["v1"] = raw
            active = "v1"
        # sanity: every key must be a valid Fernet key
        for version, key in keys.items():
            try:
                Fernet(key)
            except Exception as exc:
                raise VaultError(f"vault key {version} is not a valid Fernet key") from exc
        return cls(keys, active)


    @classmethod
    def from_settings(cls, settings, data_dir: str | Path) -> "Vault":
        """Phase 4 key providers.

        AGENTQON_VAULT_KEY_FILE points at a file holding one Fernet key
        per line; line order defines versions (v1, v2, ...), the LAST
        line is the active key. This is the seam a KMS or secrets manager
        mounts into (it materializes keys to a file); without it, the
        env-based provider (from_env) is used.
        """
        key_file = getattr(settings, "vault_key_file", "") if settings else ""
        if key_file:
            lines = [ln.strip() for ln in Path(key_file).read_text().splitlines()
                     if ln.strip()]
            if not lines:
                raise VaultError("vault key file is empty")
            keys = {f"v{i + 1}": ln.encode() for i, ln in enumerate(lines)}
            for version, key in keys.items():
                try:
                    Fernet(key)
                except Exception as exc:
                    raise VaultError(f"vault key {version} in file is invalid") from exc
            return cls(keys, f"v{len(lines)}")
        return cls.from_env(data_dir)


def rewrap_all_secrets(store, vault: Vault) -> dict:
    """Re-encrypt every stored secret onto the vault's ACTIVE key version.

    This is the rotation completion step: after a new key version is
    deployed, old ciphertext stays readable (versioned keys) until this
    runs; afterwards the old key can be retired. Tombstoned (shredded)
    rows are skipped — there is nothing left to protect.
    """
    rewrapped, skipped = 0, 0
    for row in store.list_secret_rows():
        if row["key_version"] == vault.active_version:
            continue
        try:
            plaintext = vault.decrypt(row["key_version"], row["ciphertext"])
        except VaultError:
            skipped += 1  # tombstone or unreadable — never resurrect it
            continue
        version, token = vault.encrypt(plaintext)
        store.update_secret_row(row["tenant_id"], row["connection_id"], token, version)
        rewrapped += 1
    return {"rewrapped": rewrapped, "skipped": skipped,
            "active_version": vault.active_version}


def looks_like_secret(text: str) -> bool:
    """Cheap redaction helper for log/event payloads."""
    lowered = text.lower()
    return any(tok in lowered for tok in ("sk-", "api_key", "apikey", "bearer ", "password"))
