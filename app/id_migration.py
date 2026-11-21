"""Stored-data migration for the AgentQon rename.

Agent and skill ids were replaced with clean AgentQon ids
(app/data/id_map.json). Everything a user already stored -
skill pins, mission records, agent sessions, evolve lessons,
society casts, settings - can still reference the OLD ids.
On first boot after the upgrade this migration rewrites those
references to the new ids, in place:

- exact id columns (agents, personas, tasks, runs, pins,
  stats, audit entity ids);
- ids embedded in JSON payloads (exact values, "id@version"
  and "id|task" composites, and delimited occurrences inside
  longer strings such as session transcripts);
- society cast member names that were derived from an old id
  are replaced with the member's new display name.

Guarantees: idempotent (a marker in platform_flags + the fact
that rewritten data no longer contains old ids make a second
run a no-op), never deletes a record, and it reports how many
references it rewrote per store in plain words. Stale skill
search indexes (derived data, rebuilt automatically) are
removed from the data directory.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import display_names

MARKER_KEY = "id_rename_v1"

# (table, pk columns, id column, which map / transform)
_ID_COLUMNS = [
    ("agents", ("tenant_id", "agent_version_id"), "agent_version_id", "agent"),
    ("personas", ("tenant_id", "id"), "id", "agent"),
    ("tasks", ("id",), "assigned_agent", "agent"),
    ("runs", ("id",), "agent_version_id", "agent"),
    ("skill_pins", ("tenant_id", "id"), "id", "skill"),
    ("agent_stats", ("tenant_id", "id"), "id", "stats"),
    ("audit_log", ("id",), "entity_id", "any"),
]

# (table, pk columns, json/text columns to walk)
_JSON_COLUMNS = [
    ("agents", ("tenant_id", "agent_version_id"), ("manifest",)),
    ("missions", ("id",), ("contract",)),
    ("candidates", ("id",), ("data",)),
    ("evaluations", ("id",), ("data",)),
    ("critiques", ("id",), ("data",)),
    ("societies", ("id",), ("data",)),
    ("worlds", ("tenant_id", "id"), ("data",)),
    ("sources", ("tenant_id", "id"), ("data",)),
    ("memories", ("tenant_id", "id"), ("data",)),
    ("agent_sessions", ("tenant_id", "id"), ("data",)),
    ("skill_pins", ("tenant_id", "id"), ("data",)),
    ("agent_stats", ("tenant_id", "id"), ("data",)),
    ("duels", ("tenant_id", "id"), ("data",)),
    ("telegram_duels", ("tenant_id", "id"), ("data",)),
    ("whatsapp_duels", ("tenant_id", "id"), ("data",)),
    ("schedules", ("tenant_id", "id"), ("data",)),
    ("telegram_outbox", ("tenant_id", "id"), ("data",)),
    ("whatsapp_outbox", ("tenant_id", "id"), ("data",)),
    ("events", ("id",), ("data",)),
    ("jobs", ("id",), ("payload",)),
    ("artifacts", ("id",), ("metadata",)),
    ("usage_records", ("id",), ("detail",)),
    ("approvals", ("id",), ("preview",)),
    ("tool_invocations", ("id",), ("data",)),
    ("audit_log", ("id",), ("detail",)),
]

_REGEX: re.Pattern | None = None


def _substring_regex(combined: dict[str, str]) -> re.Pattern:
    global _REGEX
    if _REGEX is None:
        alt = "|".join(re.escape(k)
                       for k in sorted(combined, key=len, reverse=True))
        _REGEX = re.compile(
            r"(?<![A-Za-z0-9_.\-])(" + alt + r")(?![A-Za-z0-9_\-])")
    return _REGEX


def _derived_name(old_agent_id: str) -> str:
    """The pre-rename society derivation (title-cased id tail)."""
    tail = old_agent_id.split(".")[-1]
    return tail.replace("-", " ").replace("_", " ").title()


class _Rewriter:
    def __init__(self, combined: dict[str, str]):
        self.map = combined
        self.regex = _substring_regex(combined)
        self.count = 0
        self.cast_names = 0

    def string(self, s: str) -> str:
        if s in self.map:
            self.count += 1
            return self.map[s]
        # Composites: "id@version", "id|task_type", "id, id".
        for sep in ("@", "|"):
            if sep in s:
                head, _, rest = s.partition(sep)
                if head in self.map:
                    self.count += 1
                    return self.map[head] + sep + rest
        if "lib." in s or "agency." in s or "--" in s:
            def sub(m: re.Match) -> str:
                self.count += 1
                return self.map[m.group(1)]
            return self.regex.sub(sub, s)
        return s

    def obj(self, o):
        if isinstance(o, str):
            return self.string(o)
        if isinstance(o, list):
            return [self.obj(v) for v in o]
        if isinstance(o, dict):
            out = {k: self.obj(v) for k, v in o.items()}
            # Society cast members carried a name derived from
            # the OLD agent id; give them the new display name.
            aid = o.get("agent_id")
            if isinstance(aid, str) and aid in self.map \
                    and isinstance(o.get("name"), str) \
                    and o["name"] == _derived_name(aid):
                new = display_names.agent_display(self.map[aid])
                if new:
                    out["name"] = new
                    self.cast_names += 1
            return out
        return o

    def text(self, raw: str) -> tuple[str, bool]:
        """Rewrite a stored JSON/text payload; (new, changed)."""
        before = self.count + self.cast_names
        try:
            parsed = json.loads(raw)
        except Exception:
            new = self.string(raw)
            return new, (self.count + self.cast_names) != before
        new = self.obj(parsed)
        if (self.count + self.cast_names) == before:
            return raw, False
        return json.dumps(new, ensure_ascii=False), True


def migrate_store_ids(store, *, log=print) -> dict:
    """Rewrite old agent/skill ids throughout a Store.

    Returns per-store rewrite counts. Safe to call repeatedly:
    the second call finds nothing to change and returns zeros.
    """
    amap = display_names.agent_id_map()
    smap = display_names.skill_id_map()
    combined: dict[str, str] = dict(amap)
    combined.update(smap)
    if not combined:
        return {}
    digest = hashlib.sha256(json.dumps(
        combined, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    marker = store.get_flag(MARKER_KEY)
    if marker:
        try:
            if json.loads(marker).get("map") == digest:
                return {"already_applied": True}
        except Exception:
            pass
    rw = _Rewriter(combined)
    counts: dict[str, int] = {}
    with store.bulk():
        return _run(store, rw, counts, digest, log)


def _run(store, rw: _Rewriter, counts: dict, digest: str, log) -> dict:
    amap = display_names.agent_id_map()
    smap = display_names.skill_id_map()
    combined = rw.map

    def bump(store_name: str, n: int) -> None:
        if n:
            counts[store_name] = counts.get(store_name, 0) + n

    # ---- exact id columns -------------------------------------
    for table, pks, col, kind in _ID_COLUMNS:
        rows = store._all(f"SELECT {', '.join(pks)}, {col} FROM {table}")
        for row in rows:
            val = row.get(col)
            if not isinstance(val, str) or not val:
                continue
            new = None
            lookup = combined if kind in ("any", "stats") else \
                (amap if kind == "agent" else smap)
            if val in lookup:
                new = lookup[val]
            else:
                # Composites: "id@version" (runs), "id|task" (stats).
                for sep in ("@", "|"):
                    head, s, rest = val.partition(sep)
                    if s and head in lookup:
                        new = lookup[head] + sep + rest
                        break
            if new and new != val:
                where = " AND ".join(f"{p}=?" for p in pks)
                store._exec(
                    f"UPDATE {table} SET {col}=? WHERE {where}",
                    (new, *[row[p] for p in pks]))
                bump(table, 1)

    # ---- JSON / text payloads ----------------------------------
    for table, pks, cols in _JSON_COLUMNS:
        rows = store._all(
            f"SELECT {', '.join(pks)}, {', '.join(cols)} FROM {table}")
        for row in rows:
            changed = False
            new_vals: dict[str, str] = {}
            for colname in cols:
                raw = row.get(colname)
                if not isinstance(raw, str) or not raw:
                    continue
                before = rw.count + rw.cast_names
                new_raw, did = rw.text(raw)
                if did:
                    new_vals[colname] = new_raw
                    changed = True
                    bump(f"{table}", rw.count + rw.cast_names - before)
            if changed:
                sets = ", ".join(f"{c}=?" for c in new_vals)
                where = " AND ".join(f"{p}=?" for p in pks)
                store._exec(
                    f"UPDATE {table} SET {sets} WHERE {where}",
                    (*new_vals.values(), *[row[p] for p in pks]))

    # ---- stale derived search indexes ---------------------------
    removed = 0
    try:
        from .config import load_settings
        data_dir = Path(load_settings().data_dir)
        for stale in data_dir.glob("skills-index-*.db"):
            stale.unlink()
            removed += 1
    except Exception:
        pass
    if removed:
        counts["stale_search_indexes_removed"] = removed
    if rw.cast_names:
        counts["society_cast_names"] = rw.cast_names

    store.set_flag(MARKER_KEY, json.dumps(
        {"map": digest, "counts": counts}, sort_keys=True))
    if counts:
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        log(f"AgentQon rename migration: updated stored references "
            f"to the new agent and skill names ({parts}).")
    return counts
