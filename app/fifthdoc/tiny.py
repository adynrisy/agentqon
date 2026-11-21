"""§21 Tiny features — small utilities, each with one honest behaviour.

Confirmation memory NEVER overrides security policies: a remembered
"don't ask again" only skips low-risk confirmations; anything the policy
requires still asks.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
            "LPT1", "LPT2", "LPT3"}
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    cleaned = INVALID_CHARS.sub("_", name).strip().strip(".")
    stem = cleaned.split(".")[0].upper()
    if stem in RESERVED:
        cleaned = "_" + cleaned
    return cleaned or "unnamed"


def convert_path(path: str, target: str, project_root: str = "") -> str:
    """target: windows | unix | project_relative."""
    if target == "windows":
        return path.replace("/", "\\")
    if target == "unix":
        return path.replace("\\", "/")
    if target == "project_relative" and project_root:
        root = project_root.replace("\\", "/")
        norm = path.replace("\\", "/")
        if norm.startswith(root):
            return norm[len(root):].lstrip("/")
    return path


def copy_as_shell_command(argv: list[str], shell: str = "posix") -> str:
    if shell == "powershell":
        parts = []
        for arg in argv:
            parts.append(arg if re.fullmatch(r"[A-Za-z0-9_./:-]+", arg)
                         else "'" + arg.replace("'", "''") + "'")
        return " ".join(parts)
    if shell == "cmd":
        parts = []
        for arg in argv:
            parts.append(arg if re.fullmatch(r"[A-Za-z0-9_./:-]+", arg)
                         else '"' + arg.replace('"', '\\"') + '"')
        return " ".join(parts)
    import shlex
    return " ".join(shlex.quote(a) for a in argv)


def bulk_rename(names: list[str], template: str) -> list[str]:
    """Template uses {n} (1-based index) and {name} (sanitised stem)."""
    out = []
    for i, name in enumerate(names, start=1):
        stem = name.rsplit(".", 1)[0] if "." in name else name
        out.append(template.format(n=i, name=sanitize_filename(stem)))
    return out


def collision_preview(directory_files: set[str], new_names: list[str]) -> dict:
    collisions = [n for n in new_names if n in directory_files]
    return {"collisions": collisions,
            "would_overwrite": bool(collisions),
            "note": "Review collisions before renaming — nothing was changed."}


def duplicate_mission_check(new_title: str, existing_titles: list[str]) -> dict:
    norm = re.sub(r"\s+", " ", new_title.strip().lower())
    matches = [t for t in existing_titles
               if re.sub(r"\s+", " ", t.strip().lower()) == norm]
    return {"duplicate": bool(matches), "matches": matches}


def new_correlation_id(prefix: str = "op") -> str:
    return f"{prefix}-{int(time.time() * 1000):x}-{id(object()) & 0xffff:x}"


class DurationHistory:
    def __init__(self):
        self.samples: dict[str, list[float]] = {}

    def record(self, operation: str, seconds: float) -> None:
        self.samples.setdefault(operation, []).append(seconds)

    def average(self, operation: str) -> float | None:
        values = self.samples.get(operation)
        return sum(values) / len(values) if values else None


class ConfirmationMemory:
    """Remembers low-risk confirmations only. Policy-gated actions always ask."""

    def __init__(self, policy_gated: set[str] | None = None):
        self.remembered: set[str] = set()
        self.policy_gated = policy_gated or {"external.send", "file.delete",
                                            "payment.send", "publish"}

    def remember(self, action: str) -> bool:
        if action in self.policy_gated:
            return False
        self.remembered.add(action)
        return True

    def should_ask(self, action: str) -> bool:
        if action in self.policy_gated:
            return True
        return action not in self.remembered


@dataclass
class StaleLock:
    lock_id: str
    holder: str
    heartbeat_at: float
    stale_after_seconds: float = 120.0

    def is_stale(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self.heartbeat_at > self.stale_after_seconds


def navigate_long_output(text: str) -> dict:
    headings = [line for line in text.splitlines()
                if line.startswith("#") or line.startswith("## ")]
    code_blocks = text.count("```") // 2
    errors = [line for line in text.splitlines()
              if "error" in line.lower() or "traceback" in line.lower()]
    sections = [line.strip() for line in text.splitlines()
                if line.strip().endswith(":") and len(line.strip()) < 80]
    return {"headings": headings, "code_blocks": code_blocks,
            "error_lines": errors[:20], "sections": sections}


def missing_result_warning(requested: list[str], produced: list[str]) -> list[str]:
    return [r for r in requested if r not in produced]


def cleanup_temp_artifacts(artifacts: list[dict], policy_days: int = 7,
                           now: float | None = None) -> dict:
    now = time.time() if now is None else now
    expired = [a["name"] for a in artifacts
               if now - a.get("created_at", now) > policy_days * 86400]
    kept = [a["name"] for a in artifacts if a["name"] not in expired]
    return {"removed": expired, "kept": kept,
            "policy": f"Temporary artifacts older than {policy_days} days."}


def recent_destinations_push(history: list[str], dest: str, limit: int = 5) -> list[str]:
    history = [d for d in history if d != dest]
    history.insert(0, dest)
    return history[:limit]
