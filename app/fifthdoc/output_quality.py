"""§12 Output quality — PER-FORMAT validation rules.

Never one generic "looks good". Each format gets its own checker; a
deliverable manifest lists exactly which requested outputs are present
and verified (ties to §21's final deliverable checklist).
"""
from __future__ import annotations

import json
import re
import struct

RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}


def check_markdown(text: str) -> dict:
    problems = []
    headings, prev_level = [], 0
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"^(#{1,6})\s", line)
        if match:
            level = len(match.group(1))
            headings.append(line.strip())
            if prev_level and level > prev_level + 1:
                problems.append(f"Line {i + 1}: heading jumps from level "
                                f"{prev_level} to {level}.")
            prev_level = level
        if line.startswith("|") and line.count("|") < 3:
            problems.append(f"Line {i + 1}: table row looks malformed.")
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
    broken = [l for l in links if l.startswith(("http://", "https://"))
              and " " in l]
    return {"ok": not problems, "problems": problems,
            "headings": headings, "outline": headings,
            "links_checked": len(links), "broken_links": broken}


def check_html(text: str) -> dict:
    problems = []
    imgs = re.findall(r"<img[^>]*>", text, re.IGNORECASE)
    for tag in imgs:
        if "alt=" not in tag.lower():
            problems.append("Image without alt text found.")
    links = re.findall(r'href="([^"]+)"', text)
    empty = [l for l in links if not l.strip() or l.strip() == "#"]
    if empty:
        problems.append(f"{len(empty)} empty link(s).")
    return {"ok": not problems, "problems": problems, "links": len(links)}


def check_json_text(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {"ok": False, "problems": [f"Not valid JSON: {exc}"]}
    return {"ok": True, "problems": [], "top_level_type": type(data).__name__}


def check_pdf_header(raw: bytes) -> dict:
    if not raw.startswith(b"%PDF-"):
        return {"ok": False, "problems": ["File does not start with a PDF header."]}
    count = raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")
    metadata = {}
    for key in (b"/Title", b"/Author"):
        match = re.search(re.escape(key) + rb"\s*\(([^)]*)\)", raw)
        if match:
            metadata[key.decode().lstrip("/")] = match.group(1).decode("utf-8", "replace")
    return {"ok": True, "problems": [], "page_count_estimate": max(count, 0),
            "metadata": metadata}


def check_svg(text: str) -> dict:
    problems = []
    if "<svg" not in text:
        problems.append("No <svg> root element.")
    if "width=" not in text or "height=" not in text:
        problems.append("SVG is missing width or height.")
    return {"ok": not problems, "problems": problems}


def check_image_dimensions(raw: bytes) -> dict:
    """PNG/JPEG header dimensions only — no decoder dependency."""
    if raw.startswith(b"\x89PNG"):
        width, height = struct.unpack(">II", raw[16:24])
        return {"ok": True, "format": "png", "width": width, "height": height}
    if raw.startswith(b"\xff\xd8"):
        return {"ok": True, "format": "jpeg", "width": None, "height": None,
                "note": "JPEG dimensions need a full scan; format confirmed."}
    return {"ok": False, "problems": ["Unknown image format."]}


def check_code_format(text: str, language: str = "python") -> dict:
    problems = []
    if language == "python":
        for i, line in enumerate(text.splitlines()):
            if "\t" in line:
                problems.append(f"Line {i + 1}: tab indentation.")
            if len(line) > 200:
                problems.append(f"Line {i + 1}: very long line.")
    return {"ok": not problems, "problems": problems}


def check_filename(name: str) -> dict:
    problems = []
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        problems.append("Filename contains characters that are invalid on some systems.")
    if name.split(".")[0].upper() in RESERVED_NAMES:
        problems.append("Filename uses a reserved system name.")
    if name != name.strip():
        problems.append("Filename has leading or trailing spaces.")
    return {"ok": not problems, "problems": problems}


def check_spreadsheet(sheets: dict[str, list[dict]]) -> dict:
    problems, names = [], list(sheets)
    for sheet, rows in sheets.items():
        for i, row in enumerate(rows):
            for key, value in row.items():
                if isinstance(value, str) and value.startswith("#") \
                        and value in ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?"):
                    problems.append(f"Sheet {sheet}, row {i}: formula error {value}.")
    return {"ok": not problems, "problems": problems, "sheet_names": names}


def check_slides(slides: list[dict]) -> dict:
    problems = []
    for i, slide in enumerate(slides):
        if not slide.get("title"):
            problems.append(f"Slide {i + 1} has no title.")
    return {"ok": not problems, "problems": problems, "slide_count": len(slides)}


def deliverable_manifest(requested: list[str], present: dict[str, bool],
                         verified: dict[str, bool]) -> dict:
    rows = []
    for name in requested:
        rows.append({"output": name, "present": bool(present.get(name)),
                     "verified": bool(verified.get(name))})
    complete = all(r["present"] and r["verified"] for r in rows)
    return {"complete": complete, "items": rows,
            "missing": [r["output"] for r in rows if not r["present"]],
            "unverified": [r["output"] for r in rows
                           if r["present"] and not r["verified"]]}


def licence_reminder(has_attribution: bool) -> str:
    if has_attribution:
        return ""
    return ("Reminder: if any output reuses someone else's work, add the "
            "licence and attribution before delivery.")
