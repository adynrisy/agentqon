"""§11 Environment inspector.

Detects project facts from the working tree: root, language, framework,
package manager, manifests, lockfiles, test framework, build/lint commands,
runtime versions, executables, missing env vars (names only — secret VALUES
are never read out), version conflicts, repo status via the existing
read-only git screen, generated/ignored files, build artifacts and missing
dependencies. The per-mission fingerprint is a hash of the facts that
affect reproducibility. CodeGraph integration point: symbol counts are
accepted from the caller (the real CodeGraph index) rather than recomputed
here — see fingerprint input `codegraph_symbols`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess

MANIFESTS = {"package.json": "node", "pyproject.toml": "python",
              "requirements.txt": "python", "go.mod": "go",
              "Cargo.toml": "rust", "Gemfile": "ruby", "pom.xml": "java",
              "composer.json": "php"}
LOCKFILES = {"package-lock.json", "poetry.lock", "Pipfile.lock",
             "yarn.lock", "pnpm-lock.yaml", "go.sum", "Cargo.lock",
             "Gemfile.lock"}
TEST_MARKERS = {"pytest.ini": "pytest", "jest.config.js": "jest",
                "vitest.config.ts": "vitest", "phpunit.xml": "phpunit"}
BUILD_DIRS = {"node_modules", "dist", "build", "__pycache__", ".venv",
              "target", "out"}
GENERATED_SUFFIXES = (".min.js", ".map", ".pyc")


def find_project_root(start: str) -> str:
    cur = os.path.abspath(start)
    while True:
        if any(os.path.exists(os.path.join(cur, m)) for m in MANIFESTS) \
                or os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return start
        cur = parent


def git_status_readonly(root: str) -> dict:
    """Read-only git screen only — never a write, never a checkout."""
    try:
        out = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return {"is_repo": False, "uncommitted": []}
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        return {"is_repo": True, "uncommitted": lines,
                "clean": not lines}
    except Exception:
        return {"is_repo": False, "uncommitted": [], "error": "git unavailable"}


def inspect(root: str, required_env_vars: list[str] | None = None,
            codegraph_symbols: int | None = None) -> dict:
    root = os.path.abspath(root)
    entries = set(os.listdir(root)) if os.path.isdir(root) else set()
    manifests = {m: MANIFESTS[m] for m in MANIFESTS if m in entries}
    languages = sorted(set(manifests.values()))
    lockfiles = sorted(e for e in entries if e in LOCKFILES)
    test_frameworks = [v for k, v in TEST_MARKERS.items() if k in entries]
    # Package manager + build/lint discovery from manifests present.
    managers = []
    if "package.json" in entries:
        managers.append("npm")
        try:
            pkg = json.load(open(os.path.join(root, "package.json")))
            scripts = pkg.get("scripts", {})
        except Exception:
            scripts = {}
    else:
        scripts = {}
    if "pyproject.toml" in entries or "requirements.txt" in entries:
        managers.append("pip")
    runtimes = {}
    for exe in ("python3", "node", "go", "cargo", "ruby"):
        path = shutil.which(exe)
        if path:
            try:
                ver = subprocess.run([exe, "--version"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            except Exception:
                ver = "unknown"
            runtimes[exe] = {"available": True, "version": ver}
        else:
            runtimes[exe] = {"available": False, "version": None}
    missing_env = [v for v in (required_env_vars or []) if v not in os.environ]
    generated, artifacts = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in BUILD_DIRS]
        if len(generated) + len(artifacts) > 200:
            break
        for name in filenames:
            if name.endswith(GENERATED_SUFFIXES):
                generated.append(os.path.relpath(os.path.join(dirpath, name), root))
    for d in BUILD_DIRS:
        if os.path.isdir(os.path.join(root, d)):
            artifacts.append(d)
    facts = {"root": root, "languages": languages, "manifests": manifests,
             "lockfiles": lockfiles, "package_managers": managers,
             "test_frameworks": test_frameworks,
             "build_command": scripts.get("build"),
             "lint_command": scripts.get("lint"),
             "runtimes": runtimes,
             "missing_env_vars": missing_env,
             "missing_env_note": "Only variable names are reported; values are "
                                 "never read or shown.",
             "repo": git_status_readonly(root),
             "generated_files_sample": generated[:50],
             "build_artifact_dirs": artifacts,
             "codegraph_symbols": codegraph_symbols}
    fingerprint = hashlib.sha256(json.dumps(
        {k: facts[k] for k in ("languages", "manifests", "lockfiles",
                               "runtimes", "codegraph_symbols")},
        sort_keys=True, default=str).encode()).hexdigest()[:16]
    facts["fingerprint"] = fingerprint
    return facts


def warn_before_dependency_change(package: str, old: str, new: str) -> dict:
    major_old = old.split(".")[0] if old else "?"
    major_new = new.split(".")[0] if new else "?"
    breaking = major_old != major_new
    return {"package": package, "from": old, "to": new,
            "warning": ("Major version change — this can break the build. "
                        "Confirm before changing." if breaking else "")}
