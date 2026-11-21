"""AgentQon — the Extended Library Pack (v22.2).

The npm package ships 4,840 skills. A further 213
security-research skills — the cluster removed in v22.1.3 so the
package could pass npm's approval gate — are offered as an
OPTIONAL download that npm never carries (spec:
EXTENDED_PACK_SPEC.md, approved 2026-10-23):

- Ask-first: `agentqon setup` offers the download once
  (default no); `agentqon skills extended` installs or reports
  status; `agentqon skills extended --remove` deletes it.
- It lands ONLY in the user's data home:
  <data home>/extended-library/ — never inside the npm package
  directory. The server's data dir is <data home>/data, so the
  pack sits beside it (see extended_dir()).
- Trust: the CLI downloads the pack beside a manifest signed with
  a DEDICATED Ed25519 pack key; the tarball hash is pinned below.
  A pack that fails signature or hash verification is refused —
  there is no silent accept and no fallback. This module pins the
  same public key + hash for the record and for any future
  in-server check; the CLI (cli/lib/extended.js) enforces them at
  install time.
- Counting stays honest: the library reports the boxed count and
  the extended count separately, in both states (app/skills.py
  facets()).

Downloads are CLI-side on purpose: the server never reaches out
for the pack, so a server with no network behaves exactly as
before.
"""
from __future__ import annotations

import os
from pathlib import Path

# The ONE place the pack's home on the web is named. The three
# assets (tarball, manifest, signature) sit side by side in the
# same release; if the host ever moves, change this base and
# rebuild — every consumer derives its URLs from here. The CLI
# mirror lives in cli/lib/extended.js (EXTENDED_PACK_BASE_URL).
EXTENDED_PACK_BASE_URL = (
    "https://github.com/adynrisy/agentqon/releases/download/"
    "extended-library-v1")
EXTENDED_PACK_TARBALL_NAME = "extended-library-v1.tar.gz"
EXTENDED_PACK_URL = f"{EXTENDED_PACK_BASE_URL}/{EXTENDED_PACK_TARBALL_NAME}"
EXTENDED_PACK_MANIFEST_URL = f"{EXTENDED_PACK_BASE_URL}/manifest.json"
EXTENDED_PACK_SIGNATURE_URL = f"{EXTENDED_PACK_BASE_URL}/manifest.sig"

# What the pack is, in numbers a user can check: 213 skills on top
# of the 4,840 in the box.
EXTENDED_PACK_NAME = "extended-library"
EXTENDED_PACK_VERSION = 1
EXTENDED_PACK_SKILL_COUNT = 213
BOXED_SKILL_TOTAL = 4_840

# Trust pins: the dedicated pack-signing public key (the private
# half never ships — it lives with the release keys, outside this
# tree) and the SHA-256 of the v1 tarball itself.
EXTENDED_PACK_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbN9LGhsHNUaII6P6BxIbWoaNKV+34G92Ai8hKe25FH8=
-----END PUBLIC KEY-----
"""
EXTENDED_PACK_TARBALL_SHA256 = (
    "b8b26b163a8e28c998e58e2994f23652badcaf8b6486598fbd652be98e96c678")


def extended_dir(settings) -> Path:
    """Where the pack lands for a given server settings object.

    OMNIMIND_EXTENDED_SKILLS_DIR wins (the OMNIMIND_SKILLS_DIR env
    precedent); otherwise the pack sits in the data HOME — the
    parent of the server's data dir — as `extended-library/`. In a
    packaged install that is ~/.agentqon/extended-library/, which
    is exactly where the CLI (configDir()/extended-library)
    installs it; the two derivations meet at the same path.
    """
    env = os.environ.get("OMNIMIND_EXTENDED_SKILLS_DIR")
    if env:
        return Path(env)
    return Path(settings.data_dir).resolve().parent / "extended-library"


def extended_skills_root(settings) -> Path:
    """The catalog-shaped root inside the installed pack (the
    folder SkillLibrary scans as its extra root)."""
    return extended_dir(settings) / "skills"


def extended_installed(settings) -> bool:
    """Installed = the pack dir holds its catalog + manifest. A
    bare or half-copied directory does not count."""
    root = extended_skills_root(settings)
    return root.is_dir() and (extended_dir(settings) / "manifest.json").is_file()
