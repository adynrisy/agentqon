"""v21 Track 5 — native code graph: a small known codebase must yield
the expected symbols, edges and impact; graphs cache per workspace
and report staleness honestly; the gateway tools sit behind the
usual controls."""
import tempfile
from pathlib import Path

from app.codegraph import CodeGraph, build_graph, ensure_graph
from app.gateway import ToolGateway
from app.store import Store

FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/util.py": '''"""Utilities."""


def ext():
    return "ext"


def unused():
    return None
''',
    "pkg/core.py": '''"""Core logic."""
from .util import ext


def helper():
    return ext()


def main():
    value = helper()
    return value
''',
    "pkg/shapes.py": '''class Shape:
    def area(self):
        return 0


class Circle(Shape):
    def area(self):
        return 3
''',
    "web/lib.ts": '''export function thing(): number {
  return 1;
}
''',
    "web/app.ts": '''import { thing } from "./lib";

function run(): number {
  return thing();
}
''',
}


def make_repo(base: Path) -> Path:
    repo = base / "repo"
    for rel, content in FIXTURE.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return repo


def test_build_finds_symbols_and_edges(tmp_path):
    repo = make_repo(tmp_path)
    db = tmp_path / "g.db"
    stats = build_graph(repo, db)
    assert stats["files"] == 6
    assert stats["symbols"] >= 8  # helper, main, ext, unused, Shape,
    # Circle, 2x area, thing, run
    g = CodeGraph(db)
    found = g.search("help")
    assert found["count"] >= 1
    assert any(r["name"] == "helper" and r["path"] == "pkg/core.py"
               for r in found["results"])


def test_symbol_context_and_impact_python(tmp_path):
    repo = make_repo(tmp_path)
    g, st = ensure_graph(tmp_path / "data", repo)
    assert st["built"] is True and st["stale"] is False
    ctx = g.symbol_context("helper")
    assert ctx["status"] == "ok"
    match = ctx["matches"][0]
    assert match["defined_in"] == "pkg/core.py"
    assert any(c["name"] == "main" for c in match["callers"])
    assert any(c["name"] == "ext" for c in match["calls"])  # via import
    imp = g.impact("ext")
    assert imp["status"] == "ok"
    impacted_names = {s["name"] for s in imp["impacted_symbols"]}
    assert {"helper", "main"} <= impacted_names  # transitive callers
    assert "pkg/core.py" in imp["impacted_files"]


def test_typescript_symbols_and_calls(tmp_path):
    repo = make_repo(tmp_path)
    g, _st = ensure_graph(tmp_path / "data", repo)
    ctx = g.symbol_context("thing")
    assert ctx["status"] == "ok"
    match = next(m for m in ctx["matches"] if m["path"] == "web/lib.ts")
    assert any(c["name"] == "run" for c in match["callers"])


def test_staleness_detected_and_rebuilt(tmp_path):
    repo = make_repo(tmp_path)
    g, st = ensure_graph(tmp_path / "data", repo)
    assert st["stale"] is False
    (repo / "pkg" / "util.py").write_text(
        'def ext():\n    return "v2"\n\n\ndef brand_new():\n    return 1\n')
    g2 = CodeGraph(g.db_path)
    assert g2.status(repo)["stale"] is True
    g3, st3 = ensure_graph(tmp_path / "data", repo)
    assert st3["stale"] is False
    assert g3.symbol_context("brand_new")["status"] == "ok"


def test_gateway_code_tools_behind_controls(tmp_path):
    repo = make_repo(tmp_path)
    store = Store(str(tmp_path / "t.db"), str(tmp_path / "artifacts"))
    gw = ToolGateway(store)

    def mission(allow):
        return {"id": "mis_code", "spent": 0.0, "budget_limit": 10.0,
                "contract": {"tools": {"allow": allow, "deny": []},
                             "approval_policy": {"external_side_effects": "required"},
                             "data_classification": "internal"}}

    denied = gw.authorize_and_run("org_t", mission(["web.read"]),
                                  "code.search",
                                  {"workspace": str(repo), "query": "help"})
    assert denied.allowed is False and denied.reason == "NOT_IN_ALLOW_LIST"
    ok = gw.authorize_and_run("org_t", mission(["code.search"]),
                              "code.search",
                              {"workspace": str(repo), "query": "help"})
    assert ok.allowed is True and ok.result["status"] == "ok"
    assert ok.result["graph_built"] is True
    ctx = gw.authorize_and_run("org_t", mission(["code.symbol_context"]),
                               "code.symbol_context",
                               {"workspace": str(repo), "symbol": "helper"})
    assert ctx.result["matches"][0]["defined_in"] == "pkg/core.py"
    bad = gw.authorize_and_run("org_t", mission(["code.impact"]),
                               "code.impact",
                               {"workspace": "/nonexistent-nowhere",
                                "symbol": "helper"})
    assert bad.result["status"] == "invalid_args"
