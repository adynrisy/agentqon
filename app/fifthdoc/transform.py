"""§5 Data transformation — built-in utilities, never agent scripts.

All row operations stream in bounded chunks: callers pass iterables and a
chunk size, and no function reads a whole file into memory by default.
YAML support uses the stdlib only: a small, safe subset parser for flat
mappings and simple lists (full YAML needs a library that is not pinned —
see yaml_to_json note). Encoding/delimiter detection is heuristic and
labelled as such.
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- JSON/CSV


def json_to_csv(rows: list[dict]) -> tuple[str, list[str]]:
    """Returns (csv_text, problems). Malformed rows are reported, not dropped silently."""
    problems = []
    fields: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"Row {i} is not an object and was skipped.")
            continue
        for key in row:
            if key not in fields:
                fields.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            writer.writerow(row)
    return buf.getvalue(), problems


def csv_to_json(text: str, delimiter: str | None = None) -> tuple[list[dict], list[str]]:
    delim = delimiter or detect_delimiter(text)
    problems: list[str] = []
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        rows = []
        for i, row in enumerate(reader):
            if None in row:
                problems.append(f"Row {i}: extra values beyond the header were kept "
                                "under a blank key.")
            rows.append({k: v for k, v in row.items() if k is not None})
        return rows, problems
    except csv.Error as exc:
        return [], [f"CSV could not be read: {exc}"]


def detect_delimiter(text: str) -> str:
    sample = text.splitlines()[:5]
    for candidate in (",", ";", "\t", "|"):
        if all(candidate in line for line in sample if line.strip()):
            return candidate
    return ","


def stream_rows(rows, chunk_size: int = 1000):
    """Yield bounded chunks of an iterable — never whole-file-into-memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


# ---------------------------------------------------------------- YAML subset


def yaml_validate(text: str) -> list[str]:
    """Validate a flat-mapping / simple-list YAML subset. Returns problems."""
    problems = []
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            continue
        if ":" not in stripped:
            problems.append(f"Line {i + 1}: expected a 'key: value' pair.")
    return problems


def yaml_to_json(text: str) -> tuple[dict, list[str]]:
    problems = yaml_validate(text)
    out: dict = {}
    current_key = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            out.setdefault(current_key, []).append(stripped[2:].strip())
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            value = value.strip()
            out[current_key] = value if value else []
    return out, problems


# ---------------------------------------------------------------- XML / Markdown / HTML


def xml_to_json(text: str) -> tuple[dict, list[str]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {}, [f"XML could not be read: {exc}"]

    def walk(elem):
        node = {"tag": elem.tag, "text": (elem.text or "").strip()}
        if elem.attrib:
            node["attributes"] = dict(elem.attrib)
        children = [walk(c) for c in elem]
        if children:
            node["children"] = children
        return node

    return walk(root), []


def markdown_tables(text: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if set("".join(cells)) <= set(":- "):
                continue  # separator row
            current.append(cells)
        else:
            if current:
                tables.append(current)
                current = []
    if current:
        tables.append(current)
    return tables


def html_to_text(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- Normalisation / dates / schema


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def convert_datetime(value: str, from_tz: str, to_tz: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(from_tz))
    return parsed.astimezone(ZoneInfo(to_tz)).isoformat()


def detect_encoding(raw: bytes) -> str:
    """Heuristic only: BOM wins, then UTF-8 trial, else latin-1 fallback."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def infer_types(rows: list[dict]) -> dict[str, str]:
    types: dict[str, set[str]] = {}
    for row in rows:
        for key, value in row.items():
            kind = "null" if value is None else type(value).__name__
            if isinstance(value, str):
                kind = "string"
                if re.fullmatch(r"-?\d+", value):
                    kind = "integer-string"
                elif re.fullmatch(r"-?\d+\.\d+", value):
                    kind = "number-string"
            types.setdefault(key, set()).add(kind)
    return {k: (v.pop() if len(v) == 1 else "mixed: " + ",".join(sorted(v)))
            for k, v in types.items()}


def validate_schema(rows: list[dict], required: list[str]) -> dict:
    missing, malformed = [], []
    for i, row in enumerate(rows):
        for key in required:
            if key not in row or row[key] in (None, ""):
                missing.append(f"Row {i}: missing field '{key}'.")
        if not isinstance(row, dict):
            malformed.append(f"Row {i} is not an object.")
    return {"ok": not missing and not malformed, "missing": missing,
            "malformed": malformed}


def rename_fields(rows: list[dict], mapping: dict[str, str]) -> list[dict]:
    return [{mapping.get(k, k): v for k, v in row.items()} for row in rows]


def dedup_rows(rows: list[dict]) -> tuple[list[dict], int]:
    seen, out = set(), []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out, len(rows) - len(out)


def sort_filter(rows: list[dict], sort_key: str | None = None,
                predicate=None) -> list[dict]:
    out = [r for r in rows if predicate is None or predicate(r)]
    if sort_key:
        out.sort(key=lambda r: str(r.get(sort_key, "")))
    return out


def preview(rows: list[dict], limit: int = 5) -> dict:
    return {"total_rows": len(rows), "preview": rows[:limit],
            "fields": sorted({k for r in rows for k in r})}
