"""AgentQon — independent verification & recovery (W3).

Everything here checks a deliverable WITHOUT trusting the agent that
produced it: structural validation, an optional test-execution gate,
evidence validation, structured quality scoring, a scoped repair
planner, recovery checkpoints, and scoped rollback.

Standing orders:
- Stdlib only. No new runtime dependencies.
- The test gate is OPTIONAL and OFF by default: it runs only when the
  caller passes approved=True, and it routes through the existing shell
  classification (app/agent_tools.classify_shell) — network commands are
  refused outright, and anything the classifier does not recognise as
  read-only still needs that explicit approval. Untrusted code is never
  auto-run.
- A test's EXIT CODE is its verdict. Output text never overrides it.
- A timeout is a failure of the run ("timed out"), never proof about
  the world outside the run.
- Scores are checklist summaries in plain words, never a claim about
  intelligence, and agreement between models is never proof of truth.
- Rollback is approval-gated, scoped to a checkpoint, and recorded;
  it never deletes a file the checkpoint did not record.
- Simple-words law: findings are plain sentences.
"""

from __future__ import annotations

import configparser
import copy
import difflib
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Optional

from .artifacts import detect_content_type, validate_content_type
from .domain import event_envelope, new_id, now_iso

CHECKER_NAME = "agentqon-verification"
CHECKER_VERSION = "w3-v1"

# A coverage number says how much code the tests touched. It does not
# say the code is correct, and it must never be presented as if it did.
COVERAGE_DISCLAIMER = (
    "Coverage counts how much code the tests touched. It does not prove "
    "the code is correct, and a high number is not a quality stamp.")
AGREEMENT_DISCLAIMER = (
    "Agreement between models is not proof of truth. Two models can "
    "agree and still be wrong; only evidence decides a claim.")

# ------------------------------------------------------------------ helpers


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _norm_heading(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", line.strip().lstrip("#").strip().lower()).strip()


def _headings(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.add(_norm_heading(line))
    return out


# ------------------------------------------------------- 1. structural validation

def validate_text_sections(text: str, required_sections: list[str]) -> list[dict]:
    """Every required section must appear as a heading. Each missing
    section is named exactly, in plain words."""
    have = _headings(text)
    checks = []
    for section in required_sections:
        want = _norm_heading(section)
        checks.append(_check(
            f"section:{section}",
            want in have,
            (f"Section '{section}' is present." if want in have else
             f"Section '{section}' is missing from the document.")))
    return checks


def validate_config(content: bytes, kind: str) -> tuple[bool, str]:
    """Configuration-file validity, by format. JSON, TOML and INI are
    checked with the standard library; anything else is refused rather
    than guessed at."""
    raw = content.decode("utf-8", errors="strict") \
        if isinstance(content, bytes) else str(content)
    try:
        if kind == "json":
            json.loads(raw)
        elif kind == "toml":
            tomllib.loads(raw)
        elif kind in ("ini", "cfg"):
            parser = configparser.ConfigParser()
            parser.read_string(raw)
        else:
            return False, (f"Configuration format '{kind}' is not one this "
                           "checker understands, so the file was not "
                           "accepted as valid.")
    except Exception as exc:  # noqa: BLE001 — message is for the user
        return False, (f"The configuration file is not valid {kind}: "
                       f"{exc.__class__.__name__}.")
    return True, f"The configuration file is valid {kind}."


_TYPE_NAMES = {"string": str, "number": (int, float), "boolean": bool,
               "array": list, "object": dict, "null": type(None)}


def validate_json_schema(data: Any, schema: dict,
                         path: str = "document") -> list[dict]:
    """Small schema check (no new dependency): required fields, field
    types, and nested objects. Every mismatch is named exactly."""
    checks: list[dict] = []
    if not isinstance(schema, dict):
        return [_check("schema:shape", False,
                       "The schema itself is not written correctly.")]
    if not isinstance(data, dict):
        return [_check("schema:shape", False,
                       f"The {path} is not an object, so its fields "
                       "could not be checked.")]
    for field in schema.get("required", []):
        checks.append(_check(
            f"schema:required:{field}", field in data,
            (f"Field '{field}' is present." if field in data else
             f"Field '{field}' is missing from the {path}.")))
    props = schema.get("properties", {})
    for field, spec in props.items():
        if field not in data:
            continue
        want = spec.get("type") if isinstance(spec, dict) else None
        py = _TYPE_NAMES.get(want) if want else None
        if py is not None and not isinstance(data[field], py):
            checks.append(_check(
                f"schema:type:{field}", False,
                f"Field '{field}' should be {want} but is not."))
        elif isinstance(spec, dict) and spec.get("type") == "object" \
                and isinstance(data[field], dict):
            checks.extend(validate_json_schema(
                data[field], spec, path=f"{path}.{field}"))
    return checks


def validate_deliverable(files: dict[str, bytes], spec: dict) -> dict:
    """Structural validation for a whole deliverable.

    files: filename -> bytes. spec may carry:
      required_files: [names that must exist]
      required_sections: {filename: [section headings]}
      expected_types: {filename: declared content type}
      config_files: {filename: "json"|"toml"|"ini"}
      schemas: {filename: schema dict for validate_json_schema}
    Returns {"passed", "checks": [...], "missing": [plain sentences]}.
    """
    checks: list[dict] = []
    missing: list[str] = []
    for name in spec.get("required_files", []):
        ok = name in files
        checks.append(_check(f"file:{name}", ok,
                             (f"File '{name}' is present." if ok else
                              f"File '{name}' is missing from the deliverable.")))
        if not ok:
            missing.append(f"File '{name}' is missing from the deliverable.")
    for name, sections in spec.get("required_sections", {}).items():
        if name not in files:
            continue  # already reported as a missing file
        try:
            text = files[name].decode("utf-8")
        except UnicodeDecodeError:
            checks.append(_check(f"sections:{name}", False,
                                 f"File '{name}' is not readable text, so "
                                 "its sections could not be checked."))
            continue
        for check in validate_text_sections(text, sections):
            checks.append(check)
            if not check["passed"]:
                missing.append(check["detail"])
    for name, declared in spec.get("expected_types", {}).items():
        if name not in files:
            continue
        ok, message = validate_content_type(files[name], declared)
        checks.append(_check(f"type:{name}", ok, message))
        if not ok:
            missing.append(message)
    for name, kind in spec.get("config_files", {}).items():
        if name not in files:
            continue
        try:
            ok, message = validate_config(files[name], kind)
        except UnicodeDecodeError:
            ok, message = False, (f"File '{name}' is not readable text, so "
                                  "it could not be checked as configuration.")
        # Name the file in the finding, not just the format.
        if not ok and name not in message:
            message = f"File '{name}': {message[0].lower() + message[1:]}"
        checks.append(_check(f"config:{name}", ok, message))
        if not ok:
            missing.append(message)
    for name, schema in spec.get("schemas", {}).items():
        if name not in files:
            continue
        try:
            data = json.loads(files[name].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            checks.append(_check(f"schema:{name}", False,
                                 f"File '{name}' is not valid JSON, so its "
                                 "fields could not be checked."))
            missing.append(f"File '{name}' is not valid JSON.")
            continue
        for check in validate_json_schema(data, schema):
            checks.append(check)
            if not check["passed"]:
                missing.append(check["detail"])
    return {"passed": all(c["passed"] for c in checks),
            "checks": checks, "missing": missing,
            "checker": CHECKER_NAME, "checker_version": CHECKER_VERSION}


# ------------------------------------------------- 2. test-execution gate (optional)

def _record_env(command: list[str], timeout: float,
                work_copy: bool) -> dict:
    return {"interpreter": sys.version.split()[0],
            "platform": platform.platform(),
            "command": list(command),
            "timeout_seconds": timeout,
            "ran_in_working_copy": bool(work_copy),
            "checker": CHECKER_NAME, "checker_version": CHECKER_VERSION}


def run_project_tests(project_dir: str | Path, command: list[str], *,
                      approved: bool, timeout: float = 120.0,
                      work_copy: bool = True) -> dict:
    """Run a project's tests as an OPTIONAL verification gate.

    - approved=False (the default): nothing runs; the result says so.
    - Network-class commands are refused outright, even when approved.
    - With work_copy=True (default) the project is copied to a fresh
      temporary folder first, so one run's files can never leak into
      another run's result (isolation) or into the real project.
    - The run is bounded by `timeout`: a hung test ends as a timeout
      failure, never a hung mission.
    - The EXIT CODE is the verdict. Output text never overrides it.
    """
    if not approved:
        return {"ran": False, "verdict": "refused",
                "passed": False,
                "detail": "Running the tests was not approved, so no "
                          "tests were run and nothing was changed.",
                "environment": _record_env(command, timeout, work_copy),
                "provenance": {"checker": CHECKER_NAME,
                               "checker_version": CHECKER_VERSION,
                               "configuration": {"timeout_seconds": timeout,
                                                 "work_copy": work_copy}}}
    if not command or not all(isinstance(a, str) and a for a in command):
        return {"ran": False, "verdict": "refused", "passed": False,
                "detail": "No usable test command was given, so no tests "
                          "were run.",
                "environment": _record_env(command or [], timeout, work_copy),
                "provenance": {"checker": CHECKER_NAME,
                               "checker_version": CHECKER_VERSION,
                               "configuration": {"timeout_seconds": timeout,
                                                 "work_copy": work_copy}}}
    from .agent_tools import classify_shell
    kind = classify_shell([str(a) for a in command])
    if kind == "network":
        return {"ran": False, "verdict": "refused", "passed": False,
                "detail": "The test command reaches the network, so it "
                          "was refused even though running tests was "
                          "approved.",
                "environment": _record_env(command, timeout, work_copy),
                "provenance": {"checker": CHECKER_NAME,
                               "checker_version": CHECKER_VERSION,
                               "configuration": {"timeout_seconds": timeout,
                                                 "work_copy": work_copy}}}
    project = Path(project_dir)
    if not project.is_dir():
        return {"ran": False, "verdict": "refused", "passed": False,
                "detail": "The project folder was not found, so no tests "
                          "were run.",
                "environment": _record_env(command, timeout, work_copy),
                "provenance": {"checker": CHECKER_NAME,
                               "checker_version": CHECKER_VERSION,
                               "configuration": {"timeout_seconds": timeout,
                                                 "work_copy": work_copy}}}
    tmp = None
    run_dir = project
    if work_copy:
        tmp = Path(tempfile.mkdtemp(prefix="agentqon-verify-"))
        run_dir = tmp / "project"
        shutil.copytree(project, run_dir,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
    try:
        try:
            proc = subprocess.run(
                [str(a) for a in command], cwd=str(run_dir),
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return {"ran": True, "verdict": "timed_out", "passed": False,
                    "exit_code": None, "timed_out": True,
                    "detail": (f"The tests did not finish within "
                               f"{timeout:g} seconds, so the run was "
                               "stopped and counted as failed. A timeout "
                               "does not say anything about work outside "
                               "the test run."),
                    "stdout": (exc.stdout or "")[-2000:] if isinstance(
                        exc.stdout, str) else "",
                    "stderr": (exc.stderr or "")[-2000:] if isinstance(
                        exc.stderr, str) else "",
                    "environment": _record_env(command, timeout, work_copy),
                    "provenance": {"checker": CHECKER_NAME,
                                   "checker_version": CHECKER_VERSION,
                                   "configuration": {
                                       "timeout_seconds": timeout,
                                       "work_copy": work_copy}}}
        # Exit code decides — even "PASS" text with a failing code fails,
        # and "FAIL" text with a passing code passes.
        passed = proc.returncode == 0
        return {"ran": True, "verdict": "passed" if passed else "failed",
                "passed": passed, "exit_code": proc.returncode,
                "timed_out": False,
                "detail": ("The tests finished and their exit code says "
                           f"they {'passed' if passed else 'failed'}."),
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
                "environment": _record_env(command, timeout, work_copy),
                "provenance": {"checker": CHECKER_NAME,
                               "checker_version": CHECKER_VERSION,
                               "configuration": {"timeout_seconds": timeout,
                                                 "work_copy": work_copy}}}
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------- 3. evidence / citation validation

_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "into",
              "about", "which", "when", "were", "been", "have", "has"}


def _claim_words(statement: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", statement.lower())
            if w not in _STOPWORDS}


def validate_evidence(claims: list[dict], sources: dict) -> dict:
    """Research-output evidence check.

    claims: [{id, subject, statement, source_ids, kind}] where kind is
    "fact" or "assumption". sources: {id: {excerpt, kind}}.
    Returns facts, assumptions, conflicts (surfaced, never hidden) and
    plain-words findings. Relevance here is a word-overlap heuristic —
    it flags a source that does not seem to be about the claim; it is
    not a judgement that the claim is true or false.
    """
    facts, assumptions, findings, conflicts = [], [], [], []
    by_subject: dict[str, list[dict]] = {}
    for claim in claims:
        kind = claim.get("kind", "fact")
        if kind == "assumption":
            assumptions.append(claim)
            continue
        facts.append(claim)
        subject = str(claim.get("subject", "")).strip().lower()
        if subject:
            by_subject.setdefault(subject, []).append(claim)
        ids = claim.get("source_ids") or []
        if not ids:
            findings.append(
                f"Claim '{claim.get('id', '?')}' is stated as a fact but "
                "has no source attached, so it is treated as unproven.")
            continue
        for sid in ids:
            src = sources.get(sid)
            if src is None:
                findings.append(
                    f"Claim '{claim.get('id', '?')}' cites a source "
                    f"({sid}) that was not found on record.")
                continue
            excerpt = str(src.get("excerpt", ""))
            if not excerpt.strip():
                findings.append(
                    f"Claim '{claim.get('id', '?')}' cites a source "
                    f"({sid}) that has no quoted passage on record.")
                continue
            overlap = _claim_words(str(claim.get("statement", ""))) & \
                _claim_words(excerpt)
            if not overlap:
                findings.append(
                    f"Claim '{claim.get('id', '?')}' cites a source "
                    f"({sid}) whose passage shares no key words with the "
                    "claim, so the source may not be about this claim. "
                    "This is a warning, not a proof the claim is wrong.")
    for subject, group in by_subject.items():
        statements = {str(c.get("statement", "")).strip().lower()
                      for c in group}
        if len(statements) > 1:
            conflicts.append({
                "subject": subject,
                "claims": [c.get("id") for c in group],
                "statements": sorted(statements),
                "note": ("These claims are about the same subject but say "
                         "different things. Both are shown; neither was "
                         "hidden and neither was picked as the winner."),
            })
    return {"facts": facts, "assumptions": assumptions,
            "findings": findings, "conflicts": conflicts,
            "passed": not findings and not conflicts,
            "checker": CHECKER_NAME, "checker_version": CHECKER_VERSION}


# ------------------------------------------------ 4. structured quality scoring

QUALITY_CRITERIA = ("requirement_coverage", "correctness", "test_results",
                    "safety", "readability", "evidence_quality")


def score_quality(criteria: dict) -> dict:
    """Multi-criterion score. Each criterion is {"score": 0..1 or None,
    "explanation": plain words}. A criterion without a score is reported
    as not checked, never silently averaged as zero. The overall figure
    is the mean of the criteria that were actually scored, labelled as
    a checklist summary — never as a measure of intelligence."""
    scored, details = [], {}
    for name in QUALITY_CRITERIA:
        entry = criteria.get(name)
        if entry is None:
            details[name] = {"score": None,
                             "explanation": "Not checked for this deliverable."}
            continue
        score = entry.get("score")
        explanation = entry.get("explanation", "")
        if score is not None and not (0.0 <= float(score) <= 1.0):
            raise ValueError(f"Score for {name} must be between 0 and 1.")
        if score is not None:
            scored.append(float(score))
        details[name] = {"score": score,
                         "explanation": explanation or
                         "No explanation was given for this criterion."}
    overall = (sum(scored) / len(scored)) if scored else None
    return {"criteria": details, "overall": overall,
            "overall_note": ("A checklist summary of the criteria that "
                             "were actually checked. It is not an "
                             "objective measure of intelligence, and it "
                             "does not prove the deliverable is correct."),
            "agreement_note": AGREEMENT_DISCLAIMER,
            "checker": CHECKER_NAME, "checker_version": CHECKER_VERSION}


# ------------------------------------------------------- 5. repair planner

def plan_repair(failures: list[dict]) -> dict:
    """A repair plan scoped to the identified problem only. Each step
    names one failing file and the problem in it; no step ever touches
    a file that did not fail. `failures` items: {file, problem, detail}.
    """
    steps, seen = [], set()
    for failure in failures:
        key = (failure.get("file"), failure.get("problem"))
        if key in seen:
            continue
        seen.add(key)
        steps.append({
            "file": failure.get("file"),
            "problem": failure.get("problem"),
            "action": (f"Fix '{failure.get('problem')}' in "
                       f"'{failure.get('file')}'. Do not change any other "
                       "file as part of this step."),
            "detail": failure.get("detail", ""),
        })
    files = sorted({s["file"] for s in steps})
    return {"steps": steps, "files_touched": files,
            "scope_note": ("This plan touches only the files listed. "
                           "Files that did not fail are not to be changed."),
            "checker": CHECKER_NAME, "checker_version": CHECKER_VERSION}


# ------------------------------------------- 6/7. checkpoints and rollback

CHECKPOINT_SIZE_CAP = 1024 * 1024  # 1 MB; larger files: hash only, honest


def snapshot_paths(paths: list[Path], root: Path) -> dict:
    """A before/after snapshot of named files: hash plus content for
    small files (so rollback can restore them), hash only for large
    files (stated, not silently faked). Paths outside `root` are
    refused."""
    root = Path(root).resolve()
    snap: dict[str, dict] = {}
    for path in paths:
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"Path {path} is outside the project folder, "
                             "so it was not snapshotted.")
        rel = str(resolved.relative_to(root))
        if not resolved.exists():
            snap[rel] = {"exists": False, "content_hash": None,
                         "content": None}
            continue
        data = resolved.read_bytes() if resolved.stat().st_size \
            <= CHECKPOINT_SIZE_CAP else None
        snap[rel] = {"exists": True,
                     "content_hash": _hash_file(resolved),
                     "content": data.decode("utf-8", errors="surrogateescape")
                     if data is not None else None,
                     "large_file_content_not_stored": data is None}
    return snap


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def diff_snapshots(before: dict, after: dict) -> dict:
    """Before/after diff for a checkpoint pair: plain-words summary
    plus a unified diff for text files that changed."""
    summary, diffs = [], {}
    for rel in sorted(set(before) | set(after)):
        old = before.get(rel)
        new = after.get(rel)
        if old is None:
            summary.append(f"File '{rel}' was created.")
        elif new is None:
            summary.append(f"File '{rel}' was removed.")
        elif old.get("content_hash") != new.get("content_hash"):
            summary.append(f"File '{rel}' changed.")
            if old.get("content") is not None and new.get("content") is not None:
                # difflib only terminates lines that already end with
                # one; splitlines() strips them, so re-terminate.
                diffs[rel] = "".join(
                    ln if ln.endswith("\n") else ln + "\n"
                    for ln in difflib.unified_diff(
                        old["content"].splitlines(),
                        new["content"].splitlines(),
                        fromfile=f"before/{rel}", tofile=f"after/{rel}",
                        lineterm="\n"))
        # unchanged files are not listed: the diff is the change, not noise
    return {"summary": summary, "diffs": diffs,
            "changed_files": [s for s in summary]}


def checkpoint_event(tenant_id: str, mission_id: str, label: str,
                     before: dict, after: dict) -> dict:
    """A checkpoint recorded on the mission record (event stream), so
    a failed step can be understood and reverted from evidence. File
    contents are NOT embedded for large files — only hashes."""
    diff = diff_snapshots(before, after)
    payload = {"label": label, "summary": diff["summary"],
               "diffs": diff["diffs"],
               "before_hashes": {k: v.get("content_hash")
                                 for k, v in before.items()},
               "after_hashes": {k: v.get("content_hash")
                                for k, v in after.items()},
               "note": "A checkpoint is evidence of what changed. It is "
                       "not, by itself, a rollback."}
    return event_envelope(tenant_id, "mission", mission_id,
                          "VerificationCheckpoint", payload)


def rollback_plan(before: dict, current_files: list[str]) -> dict:
    """What a rollback would do: restore snapshotted files, and list
    files created since the snapshot separately — created files are
    never deleted by default, because an unrelated file must never be
    deleted by a rollback."""
    restore = [rel for rel, snap in before.items() if snap.get("exists")]
    created = [f for f in current_files if f not in before]
    removed = [rel for rel, snap in before.items()
               if snap.get("exists") and rel not in current_files]
    return {"restore": restore, "created_since_snapshot": created,
            "already_removed": removed,
            "note": ("Files created since the snapshot are listed, not "
                     "deleted: deleting them needs them to be named "
                     "explicitly.")}


def execute_rollback(root: Path, before: dict, *, approved: bool,
                     delete_created: list[str] | None = None,
                     protected_roots: list[Path] | None = None) -> dict:
    """Scoped rollback. Refused without approval. Restores only files
    the checkpoint recorded (and only those small enough to carry
    content); deletes ONLY files named in `delete_created`; refuses
    any path outside `root` or inside a protected root. Returns what
    was done, in plain words."""
    if not approved:
        return {"ok": False, "restored": [], "deleted": [],
                "message": "Rollback was not approved, so nothing was "
                           "changed."}
    root = Path(root).resolve()
    protected = [Path(p).resolve() for p in (protected_roots or [])]
    restored, deleted, refused = [], [], []

    def _allowed(rel: str) -> bool:
        resolved = (root / rel).resolve()
        if resolved != root and root not in resolved.parents:
            return False
        return not any(resolved == p or p in resolved.parents
                       for p in protected)

    for rel, snap in before.items():
        if not snap.get("exists") or snap.get("content") is None:
            if snap.get("exists"):
                refused.append(f"{rel} (too large to restore from this "
                               "checkpoint)")
            continue
        if not _allowed(rel):
            refused.append(f"{rel} (outside the allowed folder)")
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(snap["content"], errors="surrogateescape")
        restored.append(rel)
    for rel in (delete_created or []):
        if rel in before:
            refused.append(f"{rel} (it was in the snapshot, so it is "
                           "restored, not deleted)")
            continue
        if not _allowed(rel):
            refused.append(f"{rel} (outside the allowed folder)")
            continue
        target = root / rel
        if target.exists():
            target.unlink()
            deleted.append(rel)
    return {"ok": True, "restored": restored, "deleted": deleted,
            "refused": refused,
            "message": (f"Rollback restored {len(restored)} file(s) and "
                        f"deleted {len(deleted)} named file(s). "
                        f"{len(refused)} item(s) were refused.")}


def rollback_event(tenant_id: str, mission_id: str,
                   result: dict) -> dict:
    return event_envelope(tenant_id, "mission", mission_id,
                          "VerificationRollback", result)


# ------------------------------------------- mechanics: mapping, grouping, etc.

def map_requirements(criteria: list[str], checks: list[dict]) -> dict:
    """Each acceptance criterion must map to at least one check.
    Unmapped criteria are reported, not hidden. A check maps to a
    criterion when its name or detail mentions the criterion text, or
    when the check carries an explicit "criterion" field."""
    mapped, unmapped = {}, []
    for criterion in criteria:
        hits = [c for c in checks
                if c.get("criterion") == criterion
                or criterion.lower() in c.get("name", "").lower()
                or criterion.lower() in c.get("detail", "").lower()]
        if hits:
            mapped[criterion] = [h["name"] for h in hits]
        else:
            unmapped.append(criterion)
    return {"mapped": mapped, "unmapped": unmapped,
            "all_mapped": not unmapped}


def _normalize_failure(text: str) -> str:
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"/[^\s:]+", "<path>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def group_failures(failures: list[dict]) -> list[dict]:
    """Identical failures grouped by root cause for the summary:
    same check name + same normalized first line = one group."""
    groups: dict[tuple[str, str], dict] = {}
    for failure in failures:
        detail = str(failure.get("detail", ""))
        first = detail.split("\n", 1)[0]
        key = (str(failure.get("name", "")), _normalize_failure(first))
        group = groups.setdefault(key, {"name": failure.get("name"),
                                        "cause": first, "count": 0,
                                        "examples": []})
        group["count"] += 1
        if len(group["examples"]) < 3:
            group["examples"].append(detail)
    return sorted(groups.values(), key=lambda g: -g["count"])


def detect_flaky(history: list[dict]) -> dict:
    """A test that passes AND fails on identical input is flaky, not
    trusted. History items: {input_hash, passed}. Different inputs are
    different tests — they do not count."""
    by_input: dict[str, set[bool]] = {}
    for item in history:
        by_input.setdefault(str(item.get("input_hash")), set()).add(
            bool(item.get("passed")))
    flaky = sorted(h for h, verdicts in by_input.items()
                   if True in verdicts and False in verdicts)
    return {"flaky": bool(flaky), "flaky_inputs": flaky,
            "note": ("A flaky result is flagged and not trusted as a "
                     "pass, even when its latest run passed.")
            if flaky else "No test passed and failed on identical input."}


def compare_to_baseline(current: list[dict], baseline: list[dict]) -> dict:
    """Results against the last known-good baseline: new failures,
    fixes, and unchanged results, each listed by check name."""
    cur = {c["name"]: bool(c.get("passed")) for c in current}
    base = {c["name"]: bool(c.get("passed")) for c in baseline}
    new_failures = sorted(n for n, p in cur.items()
                          if not p and base.get(n, True))
    fixed = sorted(n for n, p in cur.items()
                   if p and base.get(n) is False)
    unchanged = sorted(n for n in cur if n in base and cur[n] == base[n])
    return {"new_failures": new_failures, "fixed": fixed,
            "unchanged": unchanged}


def parse_coverage_text(text: str) -> Optional[float]:
    """Extract a coverage total from coverage.py-style output, if one
    is present. The number is reported with the disclaimer, never as
    proof of quality."""
    match = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", text,
                      re.MULTILINE)
    return float(match.group(1)) if match else None


def coverage_report(text: str) -> dict:
    percent = parse_coverage_text(text)
    return {"coverage_percent": percent,
            "note": COVERAGE_DISCLAIMER,
            "reported": percent is not None}


# ------------------------------------------- freshness + persistence

def make_verification_record(checks: list[dict], artifact_hash: str,
                             artifact_version: int | None = None,
                             coverage_text: str | None = None) -> dict:
    """A verification result bound to an artifact version/content hash,
    with provenance. This is what gets persisted on the mission record."""
    record = {"record_id": new_id("verif"),
              "artifact_hash": artifact_hash,
              "artifact_version": artifact_version,
              "checks": checks,
              "passed": all(c.get("passed") for c in checks),
              "created_at": now_iso(),
              "provenance": {"checker": CHECKER_NAME,
                             "checker_version": CHECKER_VERSION},
              "fresh": True}
    if coverage_text is not None:
        record["coverage"] = coverage_report(coverage_text)
    return record


def is_fresh(record: dict, current_hash: str) -> bool:
    """Fresh only while the artifact hash has not changed. After the
    artifact changes, the result is stale — a stale pass is never
    shown as current."""
    return record.get("artifact_hash") == current_hash


def stale_checks(record: dict, current_hash: str) -> list[str]:
    """Checks that must rerun after the artifact changed."""
    if is_fresh(record, current_hash):
        return []
    return [c.get("name", "?") for c in record.get("checks", [])]


def verification_event(tenant_id: str, mission_id: str,
                       record: dict) -> dict:
    """Persist a verification result on the mission record (event
    stream). Output is capped so a noisy test run cannot bloat the
    record; the verdict, checks and provenance are kept whole."""
    payload = copy.deepcopy(record)
    for check in payload.get("checks", []):
        for key in ("stdout", "stderr"):
            if isinstance(check.get(key), str) and len(check[key]) > 2000:
                check[key] = check[key][-2000:]
    return event_envelope(tenant_id, "mission", mission_id,
                          "VerificationResult", payload)
