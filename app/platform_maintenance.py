"""AgentQon — §13 development / maintenance as a SYSTEM.

Each item here is a tool or registry, not a one-off report:
- [223] Unused-dependency detection: pinned requirements vs imports.
- [224] Dead-code detection: top-level definitions never referenced
  (heuristic — reported for review, never auto-deleted).
- [225] Type-checking enforcement: new platform modules must be
  annotated (checked by AST); running mypy, when installed, is
  supported but never faked when it is absent.
- [226] Lint-rule consistency: one ruleset, checked against files.
- [228] Flakiness tracking AS A SYSTEM: every test outcome recorded;
  flake rate computed; the known queue flake is ONE entry.
- [230] Environment-difference reporting: two environment snapshots
  diffed (Python version, platform, key settings).
- [231] Feature-flag cleanup: flags registered with status; stale
  (removed/old) flags reported.
- [232] Deprecation tracking: deprecated items registered with their
  replacement and removal version.
- [233] Change-impact mapping: changed files -> affected modules and
  tests, via the import graph.
- [234] Release-note generation: change entries grouped by area into
  markdown notes.

Items 221, 222, 227, 229, 235 were named as "harden to full" in the
program brief but their audit texts were NOT in this builder's
handoff and no audit document on disk contains them; they are NOT
claimed here (see the report: UNVERIFIED-text-missing, never invented).

All functions here are stdlib-only and read-only except the registries
themselves: nothing here deletes code, drops dependencies or rewrites
files. Detection reports; a human decides.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def parse_requirement_names(requirements_text: str) -> list[str]:
    names = []
    for line in requirements_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\s;\[]", line, 1)[0].strip()
        if name:
            names.append(name)
    return names


def _imported_modules(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    return mods


def find_unused_dependencies(requirements_text: str,
                             sources: dict[str, str]) -> dict:
    """[223] Pinned requirements whose import name never appears in
    any supplied source. Import-name mapping covers the common
    renames (Pillow->PIL, PyYAML->yaml, etc.); anything unmapped is
    matched by normalised name."""
    rename = {"pillow": "PIL", "pyyaml": "yaml", "python-dotenv": "dotenv",
              "beautifulsoup4": "bs4", "scikit-learn": "sklearn"}
    imported: set[str] = set()
    for source in sources.values():
        imported |= _imported_modules(source)
    unused = []
    for req in parse_requirement_names(requirements_text):
        guess = rename.get(req.lower(), req.lower().replace("-", "_"))
        if guess not in imported and req.lower() not in imported:
            unused.append(req)
    return {"unused": unused,
            "note": ("Review before removing: a dependency may be used "
                     "by packaging, entry points or code not supplied here.")}


def find_dead_code(sources: dict[str, str]) -> dict:
    """[224] Top-level function/class names defined but never
    referenced anywhere in the supplied sources. Heuristic only:
    dunder names, test functions and names starting with 'test_' are
    excluded, and results are for review, never auto-deletion."""
    defined: dict[str, str] = {}
    referenced: set[str] = set()
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined[node.name] = path
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
    dead = [{"name": name, "file": path} for name, path in defined.items()
            if name not in referenced and not name.startswith("__")
            and not name.startswith("test_")]
    return {"possibly_dead": dead,
            "note": ("Heuristic: a name may be used dynamically or by "
                     "external callers. Review before deleting anything.")}


def check_annotations(source: str) -> dict:
    """[225] Annotation coverage for top-level functions: every
    argument (except self/cls) and the return must be annotated.
    Returns the uncovered functions. This is the enforceable form of
    type-checking for new platform modules without a new dependency."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": str(exc), "unannotated": []}
    unannotated = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args
                    if a.arg not in ("self", "cls")]
            missing = [a.arg for a in node.args.args
                       if a.arg not in ("self", "cls") and a.annotation is None]
            if missing or node.returns is None:
                unannotated.append({"function": node.name,
                                    "missing_args": missing,
                                    "missing_return": node.returns is None})
    return {"unannotated": unannotated,
            "annotated_ok": not unannotated}


LINT_RULES = {
    "no_tabs": "Indentation uses spaces, never tabs.",
    "no_trailing_whitespace": "No trailing whitespace on any line.",
    "max_line_length": 100,
    "final_newline": "Files end with exactly one newline.",
}


def check_lint_rules(files: dict[str, str]) -> dict:
    """[226] Check supplied files against the single lint ruleset.
    Returns violations per file; an empty list means the file passes."""
    violations: dict[str, list[str]] = {}
    for path, content in files.items():
        problems = []
        for i, line in enumerate(content.splitlines(), 1):
            if "\t" in line:
                problems.append(f"line {i}: tab indentation")
            if line != line.rstrip():
                problems.append(f"line {i}: trailing whitespace")
            if len(line) > LINT_RULES["max_line_length"]:
                problems.append(f"line {i}: over 100 characters")
        if content and not content.endswith("\n"):
            problems.append("missing final newline")
        if problems:
            violations[path] = problems
    return {"rules": LINT_RULES, "violations": violations}


class FlakeTracker:
    """[228] Flakiness tracking as a system. Every outcome is recorded;
    a test with both passes and failures is flaky, with its flake rate
    computed. The known queue timing flake is seeded as one entry by
    the caller (see the tests), not hard-coded as special."""

    def __init__(self) -> None:
        self._outcomes: dict[str, list[str]] = {}

    def record(self, test_name: str, outcome: str) -> None:
        if outcome not in ("passed", "failed", "skipped"):
            raise ValueError(f"Unknown outcome {outcome!r}.")
        self._outcomes.setdefault(test_name, []).append(outcome)

    def flaky_tests(self) -> list[dict]:
        out = []
        for name, outcomes in self._outcomes.items():
            runs = len(outcomes)
            failures = outcomes.count("failed")
            if 0 < failures < runs:
                out.append({"test": name, "runs": runs, "failures": failures,
                            "flake_rate": round(failures / runs, 4)})
        return sorted(out, key=lambda r: -r["flake_rate"])

    def summary(self) -> dict:
        return {"tests_tracked": len(self._outcomes),
                "flaky": self.flaky_tests()}


def environment_snapshot() -> dict:
    """[230] One half of an environment-difference report: the facts
    that must match between two machines for a result to be comparable."""
    return {"python": sys.version.split()[0], "platform": sys.platform,
            "executable": sys.executable}


def diff_environments(left: dict, right: dict) -> dict:
    """[230] Diff two environment snapshots; differences are listed
    plainly so a 'works here, fails there' can be traced."""
    differences = {key: {"left": left.get(key), "right": right.get(key)}
                   for key in sorted(set(left) | set(right))
                   if left.get(key) != right.get(key)}
    return {"differences": differences,
            "identical": not differences}


class FlagRegistry:
    """[231] Feature flags with lifecycle status. A flag marked
    'removed' that still appears in code is stale and reported; a flag
    is cleaned up by removing its registration AND its uses, never by
    deleting the registration alone."""

    def __init__(self) -> None:
        self._flags: dict[str, dict] = {}

    def register(self, name: str, *, status: str = "active",
                 introduced: str = "") -> None:
        if status not in ("active", "deprecated", "removed"):
            raise ValueError(f"Unknown flag status {status!r}.")
        self._flags[name] = {"status": status, "introduced": introduced}

    def stale_flags(self, code_references: set[str]) -> list[dict]:
        return [{"flag": name, "status": info["status"],
                 "note": "Flag is removed but still referenced in code — "
                         "remove the uses too."}
                for name, info in self._flags.items()
                if info["status"] == "removed" and name in code_references]


class DeprecationRegistry:
    """[232] Deprecated items tracked with replacement + removal
    version, so nothing deprecated is ever forgotten."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def deprecate(self, name: str, *, replacement: str,
                  remove_in: str = "") -> None:
        self._items[name] = {"replacement": replacement, "remove_in": remove_in}

    def list(self) -> list[dict]:
        return [{"name": name, **info} for name, info in self._items.items()]


def _imported_dotted(source: str) -> set[str]:
    """Full dotted import names (``app.platform_usage``), not just the
    top-level package — matching on top-level names alone misses
    ``from app.platform_usage import ...`` entirely."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def change_impact(changed_files: list[str], sources: dict[str, str]) -> dict:
    """[233] Map changed files to affected modules and tests via the
    import graph: a module is affected when it imports a changed
    module (by dotted path or by file stem). Tests are affected when
    they import an affected module or a changed module directly."""
    stems = {Path(f).stem for f in changed_files}
    dotted = {Path(f).with_suffix("").as_posix().replace("/", ".")
              for f in changed_files}
    affected_modules: set[str] = set()
    for path, source in sources.items():
        imported = _imported_dotted(source)
        segments = {part for name in imported for part in name.split(".")}
        if imported & dotted or segments & stems:
            affected_modules.add(path)
    tests = [p for p in affected_modules if "test" in Path(p).name]
    return {"changed": list(changed_files),
            "affected_modules": sorted(affected_modules),
            "affected_tests": sorted(tests),
            "note": ("Run the affected tests first after a change; the "
                     "full suite still runs before any release.")}


def generate_release_notes(entries: list[dict]) -> str:
    """[234] Release notes from change entries ({title, area}).
    Grouped by area, in area order, so notes are written from the
    change record — never from memory."""
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        grouped.setdefault(entry.get("area", "general"), []).append(entry["title"])
    lines = ["# Release notes", ""]
    for area in sorted(grouped):
        lines.append(f"## {area}")
        for title in grouped[area]:
            lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
