"""AgentQon — native code graph (v21, Track 5).

A lightweight per-workspace knowledge graph over a codebase, built
natively (GitNexus is PolyForm Noncommercial and cannot ship in this
product — see THIRD_PARTY_NOTICES.md; users may still plug the real
tool in via generic MCP). Three build phases, mirroring the classic
pipeline:

  1. scan  — walk the workspace (skipping VCS/vendor/cache dirs),
             collecting Python + TypeScript/JavaScript sources;
  2. parse — Python via the stdlib `ast` (exact definitions and
             imports); TS/JS via structural regexes (definitions and
             imports are reliable; bodies are span-approximated);
  3. resolve — import resolution maps module specifiers to files;
             CALLS edges are then derived HEURISTICALLY (name-based:
             a call to a name defined in an imported file, or in the
             same file). Heuristic edges are labelled as such in
             every result — they are navigation aids, not proof.

Storage: one SQLite database per workspace under the data dir,
cached across missions; staleness is tracked against the workspace
fingerprint (git HEAD when available, else a file mtime/size hash)
and reported honestly (`stale: true`) until rebuilt.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".next", ".mypy_cache", ".pytest_cache",
             "coverage", ".tox", "target"}
PY_EXTS = {".py"}
JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
RESOLUTION = "heuristic-v1 (name-based call resolution; imports resolved structurally)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
  path TEXT NOT NULL, line INTEGER DEFAULT 0, extra TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS edges (
  src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  PRIMARY KEY (src, dst, kind));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
"""


def workspace_key(root: str | Path) -> str:
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def graph_db_path(data_dir: str | Path, root: str | Path) -> Path:
    d = Path(data_dir) / "codegraph"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{workspace_key(root)}.db"


def workspace_fingerprint(root: str | Path) -> str:
    """git HEAD when the workspace is a repo, else a hash of the
    source files' (path, size, mtime) — either way, a change in the
    code changes the fingerprint."""
    root = Path(root)
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return "git:" + out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    h = hashlib.sha256()
    for p in _scan_files(root):
        try:
            st = p.stat()
            h.update(f"{p.relative_to(root)}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            continue
    return "files:" + h.hexdigest()[:24]


def _scan_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in PY_EXTS | JS_EXTS:
            out.append(p)
    return out


# ------------------------------------------------------------ parsing
def _parse_python(path: Path, root: Path) -> dict:
    rel = str(path.relative_to(root))
    result = {"file": rel, "symbols": [], "imports": [], "calls": []}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return result
    module = rel[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module[:-len(".__init__")]

    def visit_body(node, owner_qual: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{owner_qual}.{child.name}" if owner_qual else child.name
                result["symbols"].append(
                    {"name": child.name, "qualname": qual, "kind": "function",
                     "line": child.lineno, "parent": owner_qual})
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        fn = sub.func
                        if isinstance(fn, ast.Name):
                            result["calls"].append(
                                {"caller": qual, "callee": fn.id, "via": None})
                        elif isinstance(fn, ast.Attribute) \
                                and isinstance(fn.value, ast.Name):
                            result["calls"].append(
                                {"caller": qual, "callee": fn.attr,
                                 "via": fn.value.id})
                visit_body(child, qual)
            elif isinstance(child, ast.ClassDef):
                qual = f"{owner_qual}.{child.name}" if owner_qual else child.name
                result["symbols"].append(
                    {"name": child.name, "qualname": qual, "kind": "class",
                     "line": child.lineno, "parent": owner_qual})
                visit_body(child, qual)

    visit_body(tree, None)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                result["imports"].append({"module": a.name, "names": [],
                                          "alias": a.asname, "level": 0})
        elif isinstance(node, ast.ImportFrom):
            result["imports"].append(
                {"module": node.module or "",
                 "names": [a.name for a in node.names],
                 "alias": None, "level": node.level or 0})
    result["module"] = module
    return result


_JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'"]*?\sfrom\s+)?|require\()\s*['"]([^'"]+)['"]""")
_JS_SYMBOLS = [
    ("function", re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
    ("class", re.compile(r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
]


def _parse_js(path: Path, root: Path) -> dict:
    rel = str(path.relative_to(root))
    text = path.read_text(encoding="utf-8", errors="replace")
    result = {"file": rel, "symbols": [], "imports": [], "calls": []}
    for m in _JS_IMPORT.finditer(text):
        result["imports"].append({"module": m.group(1), "names": [],
                                  "alias": None, "level": 0, "raw": True})
    spans = []
    for kind, rx in _JS_SYMBOLS:
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            result["symbols"].append(
                {"name": m.group(1), "qualname": m.group(1), "kind": kind,
                 "line": line, "parent": None})
            spans.append((m.start(), m.group(1)))
    spans.sort()
    # heuristic call attribution: a call name( inside the span from
    # one symbol's start to the next symbol's start belongs to it.
    known = {s["name"] for s in result["symbols"]}
    for i, (start, name) in enumerate(spans):
        end = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        body = text[start:end]
        for cm in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\(", body):
            callee = cm.group(1)
            if callee != name:
                result["calls"].append({"caller": name, "callee": callee,
                                         "via": None})
    result["_known"] = known
    return result


# --------------------------------------------------------------- build
def build_graph(root: str | Path, db_path: str | Path) -> dict:
    root = Path(root).resolve()
    files = _scan_files(root)
    parsed = []
    for p in files:
        parsed.append(_parse_python(p, root) if p.suffix in PY_EXTS
                      else _parse_js(p, root))
    # module index for resolution
    py_modules: dict[str, str] = {}
    for pr in parsed:
        if "module" in pr:
            py_modules[pr["module"]] = pr["file"]
    file_set = {pr["file"] for pr in parsed}

    conn = sqlite3.connect(str(db_path))
    conn.executescript("DROP TABLE IF EXISTS nodes; DROP TABLE IF EXISTS edges;"
                       " DROP TABLE IF EXISTS meta;")
    conn.executescript(_SCHEMA)

    def node(nid, kind, name, path, line=0, extra=None):
        conn.execute("INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?,?)",
                     (nid, kind, name, path, line, json.dumps(extra or {})))

    def edge(src, dst, kind):
        conn.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?)",
                     (src, dst, kind))

    folders = set()
    for pr in parsed:
        rel = pr["file"]
        parts = rel.split("/")[:-1]
        for i in range(len(parts)):
            folders.add("/".join(parts[:i + 1]))
    for f in sorted(folders):
        node(f"folder:{f}", "Folder", f.split("/")[-1], f)
        parent = "/".join(f.split("/")[:-1])
        if parent:
            edge(f"folder:{parent}", f"folder:{f}", "CONTAINS")
    for pr in parsed:
        rel = pr["file"]
        node(f"file:{rel}", "File", rel.split("/")[-1], rel)
        parent = "/".join(rel.split("/")[:-1])
        if parent:
            edge(f"folder:{parent}", f"file:{rel}", "CONTAINS")
        for sym in pr["symbols"]:
            sid = f"sym:{rel}:{sym['qualname']}"
            node(sid, "Symbol", sym["name"], rel, sym["line"],
                 {"kind": sym["kind"], "qualname": sym["qualname"]})
            if sym.get("parent"):
                edge(f"sym:{rel}:{sym['parent']}", sid, "CONTAINS")
            else:
                edge(f"file:{rel}", sid, "DEFINES")

    externals = 0
    for pr in parsed:
        rel = pr["file"]
        src_file = f"file:{rel}"
        imported_files: dict[str, str] = {}   # alias/module -> file
        imported_names: dict[str, str] = {}   # local name -> file
        for imp in pr["imports"]:
            target = _resolve_import(pr, imp, py_modules, file_set, root)
            if target is None:
                externals += 1
                continue
            edge(src_file, f"file:{target}", "IMPORTS")
            imported_files[imp["module"]] = target
            if imp.get("alias"):
                imported_files[imp["alias"]] = target
            for name in imp.get("names", []):
                imported_names[name] = target
        # CALLS: heuristic name resolution (same file, then imports)
        own = {s["qualname"]: s for s in pr["symbols"]}
        by_name: dict[str, list[str]] = {}
        for pr2 in parsed:
            for s in pr2["symbols"]:
                by_name.setdefault(s["name"], []).append(
                    f"sym:{pr2['file']}:{s['qualname']}")
        for call in pr["calls"]:
            caller_id = f"sym:{rel}:{call['caller']}"
            callee_file = None
            if call.get("via") and call["via"] in imported_files:
                callee_file = imported_files[call["via"]]
            elif call["callee"] in imported_names:
                callee_file = imported_names[call["callee"]]
            candidates = []
            if callee_file:
                candidates = [c for c in by_name.get(call["callee"], [])
                              if c.startswith(f"sym:{callee_file}:")]
            if not candidates and imported_files:
                targets = set(imported_files.values())
                candidates = [c for c in by_name.get(call["callee"], [])
                              if c.split(":")[1] in targets]
            if not candidates:
                candidates = [c for c in by_name.get(call["callee"], [])
                              if c.startswith(f"sym:{rel}:")]
            for cid in candidates[:1]:
                if cid != caller_id:
                    edge(caller_id, cid, "CALLS")
    stats = {
        "files": len(parsed),
        "symbols": sum(len(p["symbols"]) for p in parsed),
        "external_imports": externals,
        "resolution": RESOLUTION,
    }
    conn.execute("INSERT INTO meta VALUES ('fingerprint',?)",
                 (workspace_fingerprint(root),))
    conn.execute("INSERT INTO meta VALUES ('root',?)", (str(root),))
    conn.execute("INSERT INTO meta VALUES ('stats',?)", (json.dumps(stats),))
    conn.commit()
    counts = {k: conn.execute(f"SELECT COUNT(*) FROM {k}").fetchone()[0]
              for k in ("nodes", "edges")}
    conn.close()
    return {**stats, **counts}


def _resolve_import(pr: dict, imp: dict, py_modules: dict[str, str],
                    file_set: set[str], root: Path) -> str | None:
    module = imp["module"]
    if pr["file"].endswith(JS_EXTS_TUPLE):
        if not module.startswith("."):
            return None  # bare specifier = external package
        base = (Path(pr["file"]).parent / module)
        base_str = str(base).replace("\\", "/")
        for cand in (base_str + ext for ext in
                     (".ts", ".tsx", ".js", ".jsx", ".mjs", "")):
            if cand in file_set:
                return cand
        for cand in (f"{base_str}/index{ext}" for ext in
                     (".ts", ".tsx", ".js", ".jsx")):
            if cand in file_set:
                return cand
        return None
    # python: relative imports resolve against the importing
    # file's package (its directory parts), one level per dot.
    if imp.get("level", 0) > 0:
        dir_parts = pr["file"].split("/")[:-1]
        keep = len(dir_parts) - (imp["level"] - 1)
        base = dir_parts[:keep] if keep > 0 else []
        module = ".".join(base + ([module] if module else []))
    if module in py_modules:
        return py_modules[module]
    parent = module.rsplit(".", 1)[0] if "." in module else ""
    if parent and parent in py_modules:  # from X import name (name in X)
        return py_modules[parent]
    return None


JS_EXTS_TUPLE = tuple(JS_EXTS)


# -------------------------------------------------------------- queries
class CodeGraph:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(db_path)) if self.db_path.exists() else None

    def _q(self, sql, params=()):
        if self.conn is None:
            return []
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _meta(self, key):
        if self.conn is None:
            return None
        row = self.conn.execute("SELECT value FROM meta WHERE key=?",
                                (key,)).fetchone()
        return row[0] if row else None

    def status(self, root: str | Path) -> dict:
        if self.conn is None:
            return {"built": False, "stale": True}
        fp = self._meta("fingerprint")
        return {"built": True, "stale": fp != workspace_fingerprint(root),
                "fingerprint": fp,
                "stats": json.loads(self._meta("stats") or "{}")}

    def _find_symbols(self, name: str) -> list[dict]:
        return self._q("SELECT * FROM nodes WHERE kind='Symbol' AND name=?",
                       (name,))

    def symbol_context(self, name: str) -> dict:
        matches = self._find_symbols(name)
        if not matches:
            return {"status": "not_found", "symbol": name}
        out = []
        for sym in matches:
            sid = sym["id"]
            callers = self._q(
                "SELECT n.* FROM edges e JOIN nodes n ON n.id=e.src "
                "WHERE e.dst=? AND e.kind='CALLS'", (sid,))
            callees = self._q(
                "SELECT n.* FROM edges e JOIN nodes n ON n.id=e.dst "
                "WHERE e.src=? AND e.kind='CALLS'", (sid,))
            out.append({
                "symbol": sym["name"], "id": sid, "path": sym["path"],
                "line": sym["line"],
                "defined_in": sym["path"],
                "callers": [{"name": c["name"], "path": c["path"]}
                            for c in callers],
                "calls": [{"name": c["name"], "path": c["path"]}
                          for c in callees],
                "resolution": RESOLUTION})
        return {"status": "ok", "matches": out}

    def impact(self, name: str, max_depth: int = 5) -> dict:
        matches = self._find_symbols(name)
        if not matches:
            return {"status": "not_found", "symbol": name}
        impacted: dict[str, dict] = {}
        frontier = [m["id"] for m in matches]
        seen = set(frontier)
        for _depth in range(max_depth):
            nxt = []
            for sid in frontier:
                for row in self._q(
                        "SELECT src FROM edges WHERE dst=? AND kind='CALLS'",
                        (sid,)):
                    if row["src"] not in seen:
                        seen.add(row["src"])
                        nxt.append(row["src"])
                        node = self._q("SELECT * FROM nodes WHERE id=?",
                                       (row["src"],))
                        if node:
                            impacted[row["src"]] = {
                                "name": node[0]["name"],
                                "path": node[0]["path"]}
            frontier = nxt
            if not frontier:
                break
        files = {m["path"] for m in matches}
        importers = self._q(
            "SELECT n.path FROM edges e JOIN nodes n ON n.id=e.src "
            "WHERE e.dst=? AND e.kind='IMPORTS'",
            (f"file:{matches[0]['path']}",)) if matches else []
        return {"status": "ok", "symbol": name,
                "defined_in": sorted(files),
                "impacted_symbols": sorted(impacted.values(),
                                           key=lambda x: x["name"]),
                "impacted_files": sorted({v["path"] for v in impacted.values()}
                                         | {i["path"] for i in importers}),
                "resolution": RESOLUTION}

    def search(self, query: str, limit: int = 25) -> dict:
        like = f"%{query.lower()}%"
        rows = self._q(
            "SELECT * FROM nodes WHERE lower(name) LIKE ? "
            "ORDER BY kind, name LIMIT ?", (like, limit))
        return {"status": "ok", "query": query,
                "results": [{"kind": r["kind"], "name": r["name"],
                             "path": r["path"], "line": r["line"]}
                            for r in rows],
                "count": len(rows)}


def ensure_graph(data_dir: str | Path, root: str | Path) -> tuple[CodeGraph, dict]:
    """Build (or rebuild when stale) and return (graph, status)."""
    db = graph_db_path(data_dir, root)
    graph = CodeGraph(db)
    st = graph.status(root)
    if not st["built"] or st["stale"]:
        build_graph(root, db)
        graph = CodeGraph(db)
        st = graph.status(root)
    return graph, st
