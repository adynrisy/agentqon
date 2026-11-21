"""v35 UI contracts — society simulation ("Run the world",
2026-10-22, SOCIETY_SPEC.md).

The feature is OPTIONAL, always: a society is created only from
the composer's "Also run the world" switch (Mission mode, default
OFF) or the mission detail's "Run the world" button; creation
spends ZERO model calls (the backend only sizes + casts); a run
starts only from an explicit Run action, and rooms over 100
agents only after an explicit confirmation (the 422 fence is
handled in plain words). The world output is presented as a
hand-rolled 2D network graph — no libraries, no 3D — with node
colours by stance AT THE SELECTED ROUND (derived from the event
record), a round scrubber + Play, click-a-node lines, a legend
and a stance-split bar. The honesty label rides every state.

Contract tests pin the shipped source; behavioral tests drive
the real page in jsdom (stubbed API) when node + jsdom are
available and skip otherwise — same standard as v24–v34.
Behavioral waits are condition-based (waitFor), never sleeps.
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
def test_composer_switch_is_opt_in_and_off_by_default():
    html = _html()
    tag = re.search(r'<input[^>]*id="societySwitch"[^>]*>', html)
    assert tag, "the society switch exists in the composer"
    assert 'type="checkbox"' in tag.group(0)
    assert 'name="mission-society-switch"' in tag.group(0)
    assert "checked" not in tag.group(0), \
        "opt-in law: the switch ships OFF — running the world is chosen"
    assert "Also run the world" in html
    mode = _body(html, "setMode")
    assert "$('societyRow').style.display = m === 'mission' ? 'flex' : 'none'" in mode, \
        "the switch lives in Mission mode only (missions, never chats)"


def test_send_captures_the_switch_for_that_mission_only():
    body = _body(_html(), "sendChat")
    assert "societyWanted.add(m.mission_id)" in body
    assert "$('societySwitch').checked" in body
    assert "uiMode === 'mission'" in body, \
        "a chat-routed mission never inherits the switch (it is hidden there)"


def test_panel_lives_in_mission_detail_with_named_controls():
    html = _html()
    i_card = html.index('id="missionCard"')
    i_block = html.index('id="societyBlock"')
    i_next = html.index('id="compareCard"')
    assert i_card < i_block < i_next, \
        "the society panel is a section of the mission detail card"
    for name in ["society-run-world", "society-run", "society-confirm-run",
                 "society-cancel", "society-run-again", "society-retry",
                 "society-graph-play", "society-round-scrub",
                 "mission-see-world", "mission-run-world-link"]:
        assert f'name="{name}"' in html, f"named control missing: {name}"
    assert 'id="societyGraphCanvas"' in html


def test_honesty_label_and_model_ride_the_panel():
    html = _html()
    assert LABEL in html, "the honesty label is in the shipped source"
    panel = _body(html, "renderSocietyPanel")
    assert "stamp warn" in panel and "view.label || SOCIETY_LABEL" in panel
    assert "Powered by " in panel, "the model that powered the room is named"
    assert "societySizerLine(view.sizer)" in panel


def test_sizer_line_format_is_plain_words():
    body = _body(_html(), "societySizerLine")
    assert "'The room holds ' + sizer.size + ' agents — '" in body
    assert "' About ' + sizer.est_calls + ' answers expected" in body
    assert "sizer.reason" in body, "the sizer's own reason is carried verbatim"


def test_creation_spends_nothing_and_run_respects_the_fence():
    body = _body(_html(), "createSocietyForMission")
    assert "JSON.stringify({ mission_id: mid })" in body
    assert "opts.autoRun" in body and "!view.sizer.requires_confirmation" in body, \
        "auto-run (the switch's opt-in) never crosses the >100 fence"
    run = _body(_html(), "societyRunFromPanel")
    assert "CONFIRMATION_REQUIRED" in run and "422" in run, \
        "a 422 from the fence is handled, in plain words"
    assert "friendlyError(e)" in run


def test_graph_is_hand_rolled_2d_with_a_testable_model():
    html = _html()
    assert "function societyLayout" in html, "the force layout is ours"
    assert "getContext('2d')" in html, "canvas 2D — explicitly not 3D"
    assert "WebGL" not in html and "webgl" not in html
    assert "<script src" not in html, "no external libraries are loaded"
    model = _body(html, "societyGraphModel")
    assert "window.__societyGraph = model" in model, \
        "the laid-out node/edge model is exposed for headless tests"
    assert "societyStanceAtRound(events, n.id, round)" in model, \
        "node colours are stance AT THE SELECTED ROUND, not the final"
    fetch = _body(html, "societyFetchView")
    assert "?events=1" in fetch, "the panel reads the ordered event record"
    notes = _body(html, "societyNotes")
    assert "did not respond" in notes, "non-response is counted out loud"
    assert "may be incomplete" in notes, \
        "a truncated event record (server cap) is disclosed, never hidden"


def test_graph_wears_the_exhibit_frame_and_matched_legend():
    """Styled pass (2026-10-23): the canvas sits in a framed
    exhibit with a corner tag, and the side area reads in mono
    exhibit labels with a legend that matches the node rendering
    (glow dots, hollow for silent, a lead sample)."""
    html = _html()
    assert "The room" in html, "the framed canvas carries its plain room tag"
    panel = _body(html, "renderSocietyPanel")
    assert "socgraph-frame" in panel and "socgraph-tag" in panel
    assert "soc-side-label" in panel, "the side area reads in exhibit labels"
    assert "Lead agent" in panel, "the legend names the lead sample it draws"
    split = _body(html, "societyRenderSplit")
    assert "seg-sup" in split and "seg-opp" in split and "seg-neu" in split, \
        "the split bar keeps three persistent segments (widths animate via CSS)"


def test_animations_are_guarded_by_motion_and_capability():
    """The animation layer is presentation only: it must refuse
    to start under prefers-reduced-motion, without
    requestAnimationFrame, or without a real 2d context (jsdom) —
    and every animation must land on the exact final frame, with
    the exposed model carrying the final colours throughout."""
    html = _html()
    reduced = _body(html, "societyReducedMotion")
    assert "prefers-reduced-motion: reduce" in reduced, \
        "the OS reduced-motion setting disables the animation layer"
    start = _body(html, "societyStartAnim")
    assert "societyReducedMotion()" in start, "no animation starts under reduced motion"
    assert "requestAnimationFrame" in start, "rAF is feature-detected, never assumed"
    assert "societyCtxAvailable()" in start, \
        "headless (no 2d context) skips animation — the final frame is the surface"
    draw = _body(html, "drawSocietyGraph")
    assert "societyPanel.anim = null" in draw, \
        "an animation always lands on the exact final frame"
    model = _body(html, "societyGraphModel")
    assert "societyStanceAtRound(events, n.id, round)" in model, \
        "the exposed model still carries final colours, animation or not"


def test_thread_card_links_to_the_panel():
    thread = _body(_html(), "renderThread")
    assert "threadSociety-" in thread and "attachThreadSociety(m.id)" in thread
    go = _body(_html(), "goSocietyPanel")
    assert "openMission(mid)" in go and "societyBlock" in go


def test_three_script_blocks_parse():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not available in this environment")
    script = (
        "const fs=require('fs'), vm=require('vm');"
        "const html=fs.readFileSync(process.env.AQ_HTML,'utf-8');"
        "const blocks=[...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)]"
        ".map(m=>m[1]);"
        "if (blocks.length !== 3) { console.error('blocks: '+blocks.length); process.exit(2); }"
        "blocks.forEach(b => new vm.Script(b));"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True,
                          env=dict(os.environ, AQ_HTML=str(INDEX)), timeout=60)
    assert proc.returncode == 0, f"script blocks must parse under vm.Script: {proc.stderr[-500:]}"


# --------------------------------------------------------- fixtures
FIXTURES = r"""
const LABEL = 'SIMULATED ESTIMATE — never a prediction';
const MISSION = { mission: { id: 'm1', title: 'Launch decision', status: 'completed', spent: 0,
    contract: { objective: 'Compare launch strategies for a new SMB invoicing product' } },
  tasks: [], candidates: [], evaluations: [], critiques: [], events: [], route_decisions: [] };
function sizedView(size, confirm, estCalls, estSeconds, reason) {
  return { society_id: 'soc' + size, state: 'sized', mission_id: 'm1', model: 'stub-model', label: LABEL,
    sizer: { size, tier: size, rounds_cap: 5, leads: 12, table_size: 10, est_calls: estCalls,
      est_seconds: estSeconds, reason, requires_confirmation: confirm },
    summary: { size, responded: 0, non_responded: 0, stance_split: { support: 0, oppose: 0, neutral: 0 },
      converged_round: null, capped: false, rounds_run: 0, calls_made: 0 },
    world_output: null,
    graph: { nodes: [{ id: 'mission', kind: 'mission', label: 'Launch at $20' }], edges: [] },
    events_count: 0 };
}
const DONE = (() => {
  const agents = [
    { id: 'a1', kind: 'lead', name: 'Ada Lead', category: 'strategy', lead: true, stance_final: 'support', activity: 2 },
    { id: 'a2', kind: 'crowd', name: 'Ben Crowd', category: 'marketing', archetype: 'skeptical', table_id: 't1', lead: false, stance_final: 'neutral', activity: 2 },
    { id: 'a3', kind: 'crowd', name: 'Cid Crowd', category: 'support', archetype: 'supportive', table_id: 't1', lead: false, stance_final: 'support', activity: 1 },
    { id: 'a4', kind: 'crowd', name: 'Dee Quiet', category: 'ops', archetype: 'indifferent', table_id: 't2', lead: false, stance_final: null, activity: 0 } ];
  const events = [
    { round: 1, agent_id: 'a1', kind: 'lead', table_id: null, stance: 'support', intensity: 0.9, action: 'share', line: 'Ada backs it early.', target_agent_id: null },
    { round: 1, agent_id: 'a2', kind: 'crowd', table_id: 't1', stance: 'oppose', intensity: 0.7, action: 'challenge', line: 'Ben doubts the price.', target_agent_id: null },
    { round: 1, agent_id: 'a3', kind: 'crowd', table_id: 't1', stance: 'support', intensity: 0.5, action: 'share', line: 'Cid likes it.', target_agent_id: null },
    { round: 2, agent_id: 'a1', kind: 'lead', table_id: null, stance: 'support', intensity: 0.8, action: 'share', line: 'Ada holds firm.', target_agent_id: 'a2' },
    { round: 2, agent_id: 'a2', kind: 'crowd', table_id: 't1', stance: 'neutral', intensity: 0.4, action: 'ignore', line: 'Ben softens to neutral.', target_agent_id: null } ];
  return { society_id: 'soc1', state: 'done', mission_id: 'm1', model: 'stub-model', label: LABEL,
    sizer: { size: 4, tier: 4, rounds_cap: 5, leads: 1, table_size: 10, est_calls: 25, est_seconds: 120,
      reason: 'Seated 4 agents: the standard room for a mission of this shape.', requires_confirmation: false },
    summary: { size: 4, responded: 3, non_responded: 1, stance_split: { support: 50, oppose: 0, neutral: 50 },
      converged_round: 2, capped: false, rounds_run: 2, calls_made: 9 },
    world_output: { label: LABEL, model: 'stub-model', size: 4,
      sizer_reason: 'Seated 4 agents: the standard room for a mission of this shape.',
      rounds_run: 2, converged_round: 2, capped: false,
      stance_split_by_round: [ { round: 1, support: 66.7, oppose: 33.3, neutral: 0, responded: 3 },
        { round: 2, support: 50, oppose: 0, neutral: 50, responded: 2 } ],
      stance_split_final: { support: 50, oppose: 0, neutral: 50 },
      top_lines: { support: [], oppose: [], neutral: [] },
      responded_count: 3, non_responded_count: 1, digest_final: 'Round 2: the room is settling.' },
    graph: { nodes: [{ id: 'mission', kind: 'mission', label: 'Launch at $20' }].concat(agents),
      edges: [ { source: 'a1', target: 'mission', round: 1, kind: 'share' },
        { source: 'a2', target: 'mission', round: 1, kind: 'challenge' },
        { source: 'a3', target: 'mission', round: 1, kind: 'share' },
        { source: 'a1', target: 'a2', round: 2, kind: 'share' } ] },
    events, events_count: 5 };
})();
function runningView(from) {
  const v = JSON.parse(JSON.stringify(from));
  v.state = 'running'; v.world_output = null; v.events = []; v.events_count = 0;
  v.summary = { size: v.sizer.size, responded: 0, non_responded: 0,
    stance_split: { support: 0, oppose: 0, neutral: 0 }, converged_round: null,
    capped: false, rounds_run: 0, calls_made: 0 };
  return v;
}
"""

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const MODE = process.argv[3] || 'sized';
__FIXTURES__
const state = { runCalls: [], createCalls: 0, societyGets: 0, failSocietyGet: false };
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
  if (MODE === 'sized') {
    const sized = sizedView(250, true, 265, 900, 'Seated 250 agents: a spread question — how far an idea travels is a network behaviour, and a network needs size.');
    if (clean === '/v1/societies' && method === 'POST') { state.createCalls++; return { s: 201, j: sized }; }
    if (clean === '/v1/societies/soc250/run' && method === 'POST') {
      const b = JSON.parse(opts.body || '{}'); state.runCalls.push(b);
      if (!b.confirm) return { s: 422, j: { error: { code: 'CONFIRMATION_REQUIRED', message: 'this room seats 250 agents (about 265 model calls) — confirm the spend to run it' } } };
      return { s: 200, j: runningView(sized) };
    }
    if (clean === '/v1/societies/soc250' && method === 'GET') return { s: 200, j: state.runCalls.length ? runningView(sized) : sized };
  }
  if (MODE === 'switch') {
    const sized = sizedView(100, false, 105, 157, 'Seated 100 agents: the standard room for a mission of this shape.');
    if (clean === '/v1/missions' && method === 'POST') return { s: 201, j: { mission_id: 'm1' } };
    if (clean === '/v1/missions/m1/run' && method === 'POST') return { s: 200, j: {} };
    if (clean === '/v1/societies' && method === 'POST') { state.createCalls++; return { s: 201, j: Object.assign(sized, { society_id: 'soc100' }) }; }
    if (clean === '/v1/societies/soc100/run' && method === 'POST') { state.runCalls.push(JSON.parse(opts.body || '{}')); return { s: 200, j: runningView(sized) }; }
    if (clean === '/v1/societies/soc100' && method === 'GET') return { s: 200, j: Object.assign({}, DONE, { society_id: 'soc100' }) };
  }
  if (MODE === 'graph' || MODE === 'errfetch') {
    if (clean === '/v1/societies/soc1' && method === 'GET') {
      state.societyGets++;
      if (MODE === 'errfetch' && state.failSocietyGet) return { s: 500, j: { error: { code: 'INTERNAL', message: "KeyError: 'tenant_id'" } } };
      return { s: 200, j: DONE };
    }
  }
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
  if (MODE === 'sized') {
    await waitFor(() => typeof window.openMission === 'function');
    out.switch_default = $id('societySwitch').checked;
    out.row_hidden_in_chat = $id('societyRow').style.display;
    window.setMode('mission');
    out.row_shown_in_mission = $id('societyRow').style.display;
    await window.openMission('m1');
    out.block_display = $id('societyBlock').style.display;
    out.prompt_has_run_world = !!d.querySelector('#societyBody button[name="society-run-world"]');
    $id('societyBody').querySelector('button[name="society-run-world"]').click();
    await waitFor(() => !!$id('societySizer'));
    out.sizer_line = $id('societySizer').textContent;
    out.run_calls_before_confirm = state.runCalls.length;
    out.confirm_offered = !!d.querySelector('#societyBody button[name="society-confirm-run"]');
    out.label_shown = $id('societyBody').textContent.includes('SIMULATED ESTIMATE — never a prediction');
    out.model_shown = $id('societyBody').textContent.includes('Powered by stub-model');
    d.querySelector('#societyBody button[name="society-confirm-run"]').click();
    await waitFor(() => state.runCalls.length === 1);
    out.run_body = state.runCalls[0];
    await waitFor(() => $id('societyStateLine') && $id('societyStateLine').textContent.includes('Running'));
    out.state_line = $id('societyStateLine').textContent;
  }
  if (MODE === 'switch') {
    await waitFor(() => typeof window.sendChat === 'function');
    window.setMode('mission');
    $id('societySwitch').checked = true;
    $id('chatInput').value = 'Compare launch strategies for a new SMB invoicing product';
    await window.sendChat();
    await waitFor(() => { const h = $id('threadSociety-m1'); return h && h.textContent.includes('The world has spoken'); }, 45000);
    const host = $id('threadSociety-m1');
    out.strip = host ? host.textContent.replace(/\s+/g, ' ').trim() : '';
    out.see_world_btn = !!d.querySelector('#threadSociety-m1 button[name="mission-see-world"]');
    out.create_calls = state.createCalls;
    out.run_body = state.runCalls[0] || null;
  }
  if (MODE === 'graph') {
    await waitFor(() => typeof window.openMission === 'function');
    await window.openMission('m1');
    window.societyRemember('m1', 'soc1');
    await window.societyLoadIntoPanel('m1', 'soc1');
    await waitFor(() => !!window.__societyGraph);
    const g = () => window.__societyGraph;
    const node = id => g().nodes.find(n => n.id === id);
    out.nodes = g().nodes.length;
    out.initial_round = g().round;
    out.a2_round2 = node('a2').color; out.a1_round2 = node('a1').color; out.a4_color = node('a4').color;
    out.edges_final = g().edges.length;
    out.split_final = $id('societySplitText').textContent;
    out.sizer_line = $id('societySizer').textContent;
    out.notes = $id('societyNotes').textContent.replace(/\s+/g, ' ').trim();
    const scrub = $id('societyRoundScrub');
    scrub.value = '1'; scrub.dispatchEvent(new window.Event('input'));
    out.round_after_scrub = g().round;
    out.a2_round1 = node('a2').color;
    out.edges_round1 = g().edges.length;
    out.split_round1 = $id('societySplitText').textContent;
    out.mission_centered = Math.abs(node('mission').x - g().width / 2) < 2 && Math.abs(node('mission').y - g().height / 2) < 2;
    out.lead_bigger = node('a1').r > node('a2').r;
    out.finite = g().nodes.every(n => isFinite(n.x) && isFinite(n.y));
    // click a2 on the canvas: jsdom rects are 0-sized, so model
    // coordinates are the click coordinates (scale falls back to 1)
    const a2 = node('a2');
    $id('societyGraphCanvas').dispatchEvent(new window.MouseEvent('click', { clientX: a2.x, clientY: a2.y, bubbles: true }));
    await waitFor(() => $id('societyNodeLines').textContent.includes('Ben doubts the price.'));
    out.node_lines = $id('societyNodeLines').textContent.replace(/\s+/g, ' ').trim();
    // Play from round 1 advances to the final round
    scrub.value = '1'; scrub.dispatchEvent(new window.Event('input'));
    $id('societyPlayBtn').click();
    await waitFor(() => g().round === 2, 15000);
    out.round_after_play = g().round;
    out.play_label = $id('societyPlayBtn').textContent;
  }
  if (MODE === 'errfetch') {
    state.failSocietyGet = true;
    await waitFor(() => typeof window.openMission === 'function');
    await window.openMission('m1');
    window.societyRemember('m1', 'soc1');
    await window.societyLoadIntoPanel('m1', 'soc1');
    await waitFor(() => $id('societyError') && $id('societyError').style.display === 'block');
    out.err_shown = $id('societyErrorMsg').textContent;
    out.retry_offered = !!d.querySelector('#societyError button[name="society-retry"]');
    state.failSocietyGet = false;
    d.querySelector('#societyError button[name="society-retry"]').click();
    await waitFor(() => !!window.__societyGraph);
    out.recovered_nodes = window.__societyGraph.nodes.length;
    out.err_hidden_after = $id('societyError') ? $id('societyError').style.display : 'gone';
  }
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


def _run_harness(mode: str) -> dict:
    import pytest
    node, env = _jsdom_env()
    if not node:
        pytest.skip("node/jsdom not available in this environment")
    harness = HARNESS.replace("__FIXTURES__", FIXTURES)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness)
        script = f.name
    try:
        proc = subprocess.run([node, script, "boot", mode], capture_output=True,
                              text=True, env=env, timeout=240)
        assert proc.returncode == 0, \
            f"harness {mode} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


# --------------------------------------------------------- behavioral
def test_sized_state_shows_the_estimate_and_spends_nothing():
    out = _run_harness("sized")
    assert out["switch_default"] is False, "the composer switch ships OFF"
    assert out["row_hidden_in_chat"] in ("", "none")
    assert out["row_shown_in_mission"] == "flex"
    assert out["block_display"] == "block", \
        "the society panel shows on a completed mission's detail"
    assert out["prompt_has_run_world"] is True
    assert out["sizer_line"].startswith("The room holds 250 agents — ")
    assert "About 265 answers expected" in out["sizer_line"]
    assert out["run_calls_before_confirm"] == 0, \
        "creation + sizing spent NOTHING: no run call before confirmation"
    assert out["confirm_offered"] is True, \
        "a >100 room offers only the explicit confirm action"
    assert out["label_shown"] is True and out["model_shown"] is True
    assert out["run_body"] == {"confirm": True}, \
        "the run starts only from the confirm click, with confirm=true"
    assert out["state_line"].startswith("Running — round 1 of 5")


def test_switch_flow_creates_and_runs_a_standard_room():
    out = _run_harness("switch")
    assert out["create_calls"] == 1, "exactly one society was created"
    assert out["run_body"] == {"confirm": False}, \
        "at 100 the user's switch IS the opt-in — the run starts directly"
    assert "The world has spoken: 50% support · 0% oppose · 50% neutral" \
        in out["strip"]
    assert LABEL in out["strip"]
    assert out["see_world_btn"] is True, \
        "the thread card links to the society panel"


def test_graph_renders_the_talk_round_by_round():
    out = _run_harness("graph")
    assert out["nodes"] == 5, "cast of 4 + the mission node"
    assert out["initial_round"] == 2, "the panel opens on the latest round"
    assert out["a1_round2"] == "#4D9FFF", "support is night blue"
    assert out["a2_round2"] == "#A1A1A6", "a2 ended neutral (gray)"
    assert out["a4_color"] is None, "an agent who never spoke has no stance colour"
    assert out["edges_final"] == 4
    assert "50% support" in out["split_final"] or "Support 50%" in out["split_final"]
    assert out["sizer_line"].startswith("The room holds 4 agents — ")
    assert "did not respond" in out["notes"], "the silent agent is counted"
    assert "settled in round 2" in out["notes"], "convergence is said in plain words"
    # scrubbing back to round 1 re-colours from the record
    assert out["round_after_scrub"] == 1
    assert out["a2_round1"] == "#B3261E", "in round 1 a2 opposed (red)"
    assert out["edges_round1"] == 3, "only round-1 reactions are drawn"
    assert "Oppose 33.3%" in out["split_round1"]
    assert out["mission_centered"] is True
    assert out["lead_bigger"] is True, "lead agents are drawn larger"
    assert out["finite"] is True
    # clicking the node reads its recorded lines, verbatim
    assert "Ben doubts the price." in out["node_lines"]
    assert "Ben softens to neutral." in out["node_lines"]
    assert out["round_after_play"] == 2, "Play advances the rounds"


def test_a_failed_society_fetch_is_plain_and_recoverable():
    out = _run_harness("errfetch")
    assert out["err_shown"], "the failure is shown, in words"
    assert "KeyError" not in out["err_shown"], \
        "raw technical detail never reaches the screen (v32 screen)"
    assert "HTTP 500" not in out["err_shown"]
    assert out["retry_offered"] is True
    assert out["recovered_nodes"] == 5, "Try again loads the world"


def test_interrupted_state_renders_honestly():
    """Gate fix (stale-run sweeper): a society whose run was
    interrupted (server stopped mid-run) gets a plain state line,
    its note shown, and the Run-a-new-world action — never a blank
    panel pretending to still run."""
    html = _html()
    line = _body(html, "societyStateLine")
    assert "view.state === 'interrupted'" in line
    assert "Interrupted — the server stopped" in line
    panel = _body(html, "renderSocietyPanel")
    assert "'interrupted'" in panel, "interrupted is a terminal state with an action"
    assert "view.state === 'interrupted'" in panel, "the sweeper's note is displayed"
