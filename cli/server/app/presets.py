"""Provider presets (2026-10-23, fix-list items 5+6).

Zohaib's ask: "fixed presets for every llm api provider ... to
minimize api connection failures." The data file
(app/provider_presets.json, generated from the OmniRoute provider
registry) carries two lists:

- "presets": the entries that honestly work as OpenAI-style
  API-key endpoints — the BYOK form's picker. Picking one fills
  the base URL (still editable); "Custom" remains for anything
  else.
- "excluded": every other registry entry, each with a plain note
  saying why it cannot be a preset (OAuth sign-in, stdio program,
  web chat page, a different API shape). They are data, never
  picker entries — a preset that cannot work is worse than none.

AgentRouter rides the picker as a special preset: its registry
entry speaks the Anthropic Messages shape, but AgentRouter also
serves an OpenAI-style endpoint at https://agentrouter.org/v1 —
gated on a Claude Code identity. The preset therefore carries
the identity headers (item 5's per-connection headers), applied
automatically when picked.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).with_name("provider_presets.json")


def _load() -> dict:
    try:
        return json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception:
        return {"presets": [], "excluded": []}


def picker_presets() -> list[dict]:
    """What the BYOK picker may offer: id, name, base_url, auth,
    and (AgentRouter only) the headers the preset applies."""
    return _load().get("presets", [])


def excluded_presets() -> list[dict]:
    return _load().get("excluded", [])


def preset_by_id(preset_id: str) -> dict | None:
    for p in picker_presets():
        if p["id"] == preset_id:
            return p
    return None
