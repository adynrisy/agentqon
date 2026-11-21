"""v38 UI contracts — society visibility (2026-10-24): the panel,
the chat-thread strip and the Simulations page's runs list show a
running world's LIVE progress (the round now open, how many of its
calls have answered) from the persisted progress the engine writes
per call — and the sizer's line refuses to print a precise time
when the harness has no measured pace for the seated model
(est_rough), saying in plain words that the time is unknown.

Same standard as v35: contract pins on the shipped source, plus a
jsdom behavioral run (stubbed API) when node + jsdom are present.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "apps" / "web" / "index.html"
LABEL = "SIMULATED ESTIMATE — never a prediction"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(html: str, name: str) -> str:
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", html)
    assert m, f"function {name} not found"
    i = html.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(html):
        ch = html[j]
        if ch in "'\"`":
            j += 1
            while j < len(html) and html[j] != ch:
                j += 2 if html[j] == "\\" else 1
        elif ch == "/" and html[j + 1:j + 2] == "/":
            j = html.index("\n", j)
        elif ch == "/" and html[j + 1:j + 2] == "*":
            j = html.index("*/", j) + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")


# ------------------------------------------------------- contracts
def test_state_line_reads_live_progress_with_a_fallback():
    body = _body(_html(), "societyStateLine")
    assert "view.progress" in body, \
        "the running line is fed by the persisted live progress"
    assert "' answers received · still working'" in body
    assert "p.calls_done" in body and "p.calls_expected" in body
    assert "sm.rounds_run" in body, \
        "records without progress (pre-fix) still render a line"


def test_sizer_line_refuses_fake_precision_when_rough():
    body = _body(_html(), "societySizerLine")
    assert "sizer.est_rough" in body
    assert "the time is unknown" in body
    assert "depends on your model" in body
    # The measured branch keeps its exact long-standing format.
    assert "' About ' + sizer.est_calls + ' answers expected · ' + when" in body
    assert "on the connected model." in body


def test_runs_list_and_thread_strip_show_progress():
    runs = _body(_html(), "loadSocietyRuns")
    assert "r.progress" in runs and "answers received" in runs
    assert "r.state === 'running'" in runs, \
        "progress is a live-run surface, not a terminal decoration"
    strip = _body(_html(), "attachThreadSociety")
    assert "view.progress" in strip and "answers received" in strip


def test_progress_surfaces_carry_no_banned_strings():
    html = _html()
    for name in ("societyStateLine", "societySizerLine",
                 "loadSocietyRuns", "attachThreadSociety"):
        body = _body(html, name)
        for banned in ("undefined", "TypeError", "Failed to fetch"):
            assert banned not in body, f"{banned} in {name}"


# --------------------------------------------------------- fixtures
FIXTURES = r"""
const LABEL = 'SIMULATED ESTIMATE — never a prediction';
const MISSION = { mission: { id: 'm1', title: 'Launch decision', status: 'completed', spent: 0,
    contract: { objective: 'Compare launch strategies for a new SMB invoicing product' } },
  tasks: [], candidates: [], evaluations: [], critiques: [], events: [], route_decisions: [] };
const AGENTS = [
  { id: 'a1', kind: 'lead', name: 'Ada Lead', category: 'strategy', lead: true, stance_final: 'support', activity: 1 },
  { id: 'a2', kind: 'crowd', name: 'Ben Crowd', category: 'marketing', archetype: 'skeptical', table_id: 't1', lead: false, stance_final: 'oppose', activity: 1 } ];
const EVENTS = [
  { round: 1, agent_id: 'a1', kind: 'lead', table_id: null, stance: 'support', intensity: 0.9, action: 'share', line: 'Ada backs it early.', target_agent_id: null },
  { round: 1, agent_id: 'a2', kind: 'crowd', table_id: 't1', stance: 'oppose', intensity: 0.7, action: 'challenge', line: 'Ben doubts the price.', target_agent_id: null } ];
const RUNNING = { society_id: 'socrun', state: 'running', mission_id: 'm1', model: 'stub-model', label: LABEL,
  sizer: { size: 100, tier: 100, rounds_cap: 5, leads: 12, table_size: 10, est_calls: 105,
    est_seconds: 3600, pace_source: 'history', est_rough: false,
    reason: 'Seated 100 agents: the standard room for a mission of this shape.', requires_confirmation: false },
  progress: { round: 2, rounds_cap: 5, calls_done: 7, calls_expected: 12, responded: 63, updated_at: 0 },
  summary: { size: 100, responded: 63, non_responded: 37,
    stance_split: { support: 50, oppose: 50, neutral: 0 }, converged_round: null,
    capped: false, rounds_run: 1, calls_made: 27,
    progress: { round: 2, rounds_cap: 5, calls_done: 7, calls_expected: 12, responded: 63, updated_at: 0 } },
  world_output: { label: LABEL, model: 'stub-model', size: 100,
    sizer_reason: 'Seated 100 agents: the standard room for a mission of this shape.',
    rounds_run: 1, converged_round: null, capped: false,
    stance_split_by_round: [ { round: 1, support: 50, oppose: 50, neutral: 0, responded: 2 } ],
    stance_split_final: { support: 50, oppose: 50, neutral: 0 },
    top_lines: { support: [], oppose: [], neutral: [] },
    responded_count: 2, non_responded_count: 98, digest_final: 'Round 1: the room is moving.' },
  graph: { nodes: [{ id: 'mission', kind: 'mission', label: 'Launch at $20' }].concat(AGENTS),
    edges: [ { source: 'a1', target: 'mission', round: 1, kind: 'share' } ] },
  events: EVENTS, events_count: 2 };
const SIZED_ROUGH = { society_id: 'socrough', state: 'sized', mission_id: 'm1', model: 'stub-model', label: LABEL,
  sizer: { size: 100, tier: 100, rounds_cap: 5, leads: 12, table_size: 10, est_calls: 105,
    est_seconds: 158, pace_source: 'guess', est_rough: true,
    reason: 'Seated 100 agents: the standard room for a mission of this shape.', requires_confirmation: false },
  progress: null,
  summary: { size: 100, responded: 0, non_responded: 100,
    stance_split: { support: 0, oppose: 0, neutral: 0 }, converged_round: null,
    capped: false, rounds_run: 0, calls_made: 0, progress: null },
  world_output: null,
  graph: { nodes: [{ id: 'mission', kind: 'mission', label: 'Launch at $20' }], edges: [] },
  events_count: 0 };
"""

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
__FIXTURES__
function baseRoutes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
  if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
  if (path === '/v1/worlds' && method === 'GET') return { s: 200, j: { worlds: [] } };
  if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
  if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
  if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
  if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
  if (path === '/v1/model-state') return { s: 200, j: { connected: true, model: 'stub-model' } };
  if (path === '/v1/models' && method === 'GET') return { s: 200, j: { connections: [], models: [] } };
  if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
  if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
  if (path === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
  return { s: 200, j: {} };
}
function routes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  const clean = path.split('?')[0];
  if (clean === '/v1/missions/m1' && method === 'GET') return { s: 200, j: MISSION };
  if (clean === '/v1/missions/m1/events') return { s: 200, j: { events: [] } };
  if (clean === '/v1/societies/socrun' && method === 'GET') return { s: 200, j: RUNNING };
  if (clean === '/v1/societies/socrough' && method === 'GET') return { s: 200, j: SIZED_ROUGH };
  if (clean === '/v1/societies' && method === 'GET') return { s: 200, j: { societies: [
    { society_id: 'socrun', state: 'running', mission_id: 'm1', mission_title: 'Launch decision',
      objective: 'Compare launch strategies', model: 'stub-model', label: LABEL, size: 100,
      summary: null, progress: RUNNING.progress, created_at: 1 } ] } };
  return baseRoutes(path, opts);
}
function makeWindow() {
  const calls = [];
  const dom = new JSDOM(HTML, { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.Element.prototype.scrollIntoView = function () {};
      window.scrollTo = () => {}; window.prompt = () => 'x'; window.confirm = () => true;
      window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
      window.HTMLAnchorElement.prototype.click = function () { window.__dl = { href: this.href, download: this.download }; };
      window.fetch = async (path, opts = {}) => { calls.push({ method: opts.method || 'GET', path, body: opts.body || null });
        const r = routes(path, opts);
        if (r.throw) throw new TypeError('Failed to fetch');
        return { ok: r.s < 300, status: r.s, json: async () => r.j, blob: async () => new window.Blob(['x']) }; };
    } });
  return { window: dom.window, calls };
}
const waitFor = async (fn, timeout = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < timeout) { if (fn()) return true; await new Promise(r => setTimeout(r, 50)); } return fn(); };
(async () => {
  const out = {};
  const { window, calls } = makeWindow(); const d = window.document;
  const $id = id => d.getElementById(id);
  await waitFor(() => typeof window.openMission === 'function');
  await window.openMission('m1');
  window.societyRemember('m1', 'socrun');
  await window.societyLoadIntoPanel('m1', 'socrun');
  await waitFor(() => $id('societyStateLine') && $id('societyStateLine').textContent.includes('answers received'));
  out.state_line = $id('societyStateLine').textContent;
  out.sizer_line = $id('societySizer').textContent;
  out.label_shown = $id('societyBody').textContent.includes('SIMULATED ESTIMATE — never a prediction');
  out.model_shown = $id('societyBody').textContent.includes('Powered by stub-model');
  await window.loadSocietyRuns();
  out.runs_row = $id('societyRuns').textContent.replace(/\s+/g, ' ').trim();
  window.societyStopPolling();
  window.societyRemember('m1', 'socrough');
  await window.societyLoadIntoPanel('m1', 'socrough');
  await waitFor(() => $id('societySizer') && $id('societySizer').textContent.includes('time is unknown'));
  out.rough_sizer_line = $id('societySizer').textContent;
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _jsdom_env():
    node = shutil.which("node")
    if not node:
        return None, None
    candidates = [
        Path.home() / "workspace" / "toolmere" / "node_modules",
        ROOT / "node_modules",
        Path.home() / "workspace" / "node_modules",
    ]
    for cand in candidates:
        if (cand / "jsdom").is_dir():
            env = dict(os.environ, NODE_PATH=str(cand), AQ_HTML=str(INDEX))
            return node, env
    return None, None


def _run_harness() -> dict:
    import pytest
    node, env = _jsdom_env()
    if not node:
        pytest.skip("node/jsdom not available in this environment")
    harness = HARNESS.replace("__FIXTURES__", FIXTURES)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness)
        script = f.name
    try:
        proc = subprocess.run([node, script, "boot"], capture_output=True,
                              text=True, env=env, timeout=240)
        assert proc.returncode == 0, \
            f"harness failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


# --------------------------------------------------------- behavioral
def test_running_panel_shows_live_progress_and_rough_sizer_is_plain():
    out = _run_harness()
    line = out["state_line"]
    assert "round 2 of 5" in line, line
    assert "7 of 12 answers received" in line, line
    assert "still working" in line, line
    # A measured pace keeps its honest figure.
    assert "about 60 minutes" in out["sizer_line"], out["sizer_line"]
    assert out["label_shown"] is True and out["model_shown"] is True
    # The Simulations page's runs list carries the same live line.
    assert "7 of 12 answers received" in out["runs_row"], out["runs_row"]
    assert "still working" in out["runs_row"], out["runs_row"]
    # No measured pace: no precise figure anywhere in the line.
    rough = out["rough_sizer_line"]
    assert "the time is unknown" in rough, rough
    assert "105 model calls" in rough, rough
    assert "158" not in rough and "about 3 min" not in rough, rough
