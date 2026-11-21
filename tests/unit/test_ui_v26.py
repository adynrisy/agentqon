"""v22 UI redesign — chat-first layout, first-run connect card, agent mode.

R3 of the v22 contract: left sidebar (brand, New chat, model-status
summary, history, Connections entry, Advanced toggle at the bottom) +
main column (top bar with the model picker + view title, stream,
composer pinned at the bottom). The Advanced dashboard keeps every
panel; every existing id/name/handler moved with its element.

First run (R3/R6 web half): when /v1/model-state says no model, the
chat view opens on ONE connect card — auto-detect creates+validates
local connections through the existing API (Ollama 11434, LM Studio
1234, OmniRouter 20128, llama.cpp 8080) and an API-key path uses the
existing two-flavor create. On success the card swaps to the
composer with a plain "You're connected" state.

Agent mode: a Chat/Mission/Agent switch on the composer, aligned
to the REAL backend API (verified from source):
- create: POST /v1/agent-sessions {goal, workspace_root?} -> 201
  {"session": {...}} (app/main.py AgentSessionBody line 460, create
  endpoint line 474, _session_view line 453; extra fields forbidden)
- run: POST /v1/agent-sessions/{id}/run with NO body (line 514)
- events: GET /v1/agent-sessions/{id}/events?after=<event_id> ->
  {"events": [...]} (line 504); envelopes {event_id, event_type,
  payload} from app/domain.py event_envelope line 370, listed via
  app/store.py list_events line 1047 (after matches event_id)
- session record fields built in app/agent_loop.py line 185
  (id, goal, workspace_root, status, pending_action, ...)
- ApprovalNeeded event payload {call_id, tool, args, reason}
  (app/agent_loop.py lines 484-487); the same call is parked on
  session.pending_action
- approve/deny: ONE endpoint POST /v1/agent-sessions/{id}/approve
  with {"decision": "approve"|"deny"} (app/main.py
  ApprovalDecisionBody line 469, endpoint line 538)
- cancel: POST /v1/agent-sessions/{id}/cancel, no body (line 564)
Until a server offers that API the calls 404 and the UI degrades
honestly, in plain language.

Behavioral tests drive the real page in jsdom (stubbed API) when
node + jsdom are available and skip otherwise — same standard as
test_ui_v24/v25.
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


# ------------------------------------------------------------ layout
def test_layout_landmarks_and_sidebar_order():
    html = _html()
    # viewTitle was REMOVED by the Muse restyle (spec §3: no view-title label).
    for landmark in ('id="sidebar"', 'id="mainCol"', 'id="topbar"',
                     'id="composer"', 'id="thread"',
                     'id="sideModel"', 'id="connectCard"', 'id="agentRow"'):
        assert landmark in html, landmark
    # sidebar order (Muse restyle): brand, New chat, model summary, nav
    # (Chat · Missions · Agents · Skills · Simulations · Advanced),
    # Recent history. (The sidebar footer workspace code was removed by
    # the plain pass, P-34: the same code is editable at the workspace
    # input, so the order ends at Recent.) Advanced is the 6th nav entry now
    # (railAdvBtn inside the nav list), not a toggle below the history.
    # (The nav's Connections entry was REMOVED 2026-10-23 — Zohaib's
    # refined map: connections live only in Advanced; v36 pins the map.)
    order = [html.index('class="brand"'), html.index('id="newChatBtn"'),
             html.index('id="sideModel"'), html.index('id="navChat"'),
             html.index('id="railAdvBtn"'), html.index('id="recentRow"')]
    assert order == sorted(order), "sidebar sections out of order"
    # Advanced sits inside the nav list, in spec order after Simulations
    nav = [html.index('id="navChat"'), html.index('id="navMissions"'),
           html.index('id="navAgents"'), html.index('id="navSkills"'),
           html.index('id="navSims"'), html.index('id="railAdvBtn"')]
    assert nav == sorted(nav), \
        "nav order is not Chat · Missions · Agents · Skills · Simulations · Advanced"
    assert html.index('<nav') < html.index('id="railAdvBtn"') \
        < html.index('</nav>'), "Advanced is a nav entry, not a toggle below the nav"
    # the model picker lives in the top bar — exactly once in the file
    assert html.count('id="chatModel"') == 1
    assert html.index('id="topbar"') < html.index('id="chatModel"') \
        < html.index('id="composer"'), "model picker must sit in the top bar"


def test_mode_switch_and_new_named_buttons():
    html = _html()
    for name, fid in (("mode-chat", "modeChatBtn"), ("mode-mission", "modeMissionBtn"),
                      ("mode-agent", "modeAgentBtn"), ("agent-start", "agentStartBtn"),
                      ("connect-autodetect", "ccDetectBtn"),
                      ("connect-api-key", "ccConnectBtn")):
        assert f'name="{name}"' in html, name
        assert f'id="{fid}"' in html, fid
    # inline approval buttons render from the agent event stream
    render = _body(html, "renderAgentEvent")
    assert 'name="agent-approve"' in render and 'name="agent-deny"' in render
    assert "agentApproval(" in render, "approval buttons are wired to the approval call"
    assert 'details class="toolcard"' in render, "tool calls render as collapsible cards"
    # mode switching exists and the agent row is agent-mode only
    mode = _body(html, "setMode")
    assert "uiMode = m" in mode and "agentRow" in mode
    send = _body(html, "sendChat")
    assert send.index("uiMode === 'agent'") < send.index("chatRoute(text)"), \
        "agent mode is decided before the chat/mission classifier"
    assert "uiMode === 'mission'" in send, "mission mode can force the panel path"


def test_responsive_rules_cover_the_new_surfaces():
    html = _html()
    media = html[html.index("@media (max-width:840px)"):]
    assert "#chatModelSearch{width:140px;max-width:140px}" in media, "the top-bar picker search shrinks at phone width"
    assert "#agentRow" in media, "the agent row wraps at phone width"
    assert "#sidebar.open" in media or "#sidebar" in media, "sidebar drawer pattern intact"


# ------------------------------------------------------- connect card
def test_connect_card_markup_and_endpoints():
    html = _html()
    for fid in ("ccBaseUrl", "ccApiKey", "ccLabel", "ccStatus",
                "ccDetectStatus", "ccDetectResults"):
        assert f'id="{fid}"' in html, fid
    detect = _body(html, "detectLocalModels")
    for url in ("http://127.0.0.1:11434/v1", "http://127.0.0.1:1234/v1",
                "http://localhost:20128/v1", "http://127.0.0.1:8080/v1"):
        assert url in html, url
    assert "kind:'local_endpoint'" in detect or 'kind: "local_endpoint"' in detect \
        or "kind:'local_endpoint'" in detect.replace(" ", ""), \
        "auto-detect creates LOCAL connections"
    assert "'/validate'" in detect, "auto-detect validates each created connection"
    key = _body(html, "connectWithApiKey")
    assert "direct_provider" in key, "the API-key path uses the BYOK flavor"
    state = _body(html, "loadModelState")
    assert "updateConnectCard(connected)" in state, \
        "the card follows the tenant-aware model state"
    assert "sideModel" in state, "the sidebar summary is fed by the same state"
    assert "You're connected" in html, "the success swap speaks plainly"


# --------------------------------------------------------- agent mode
def test_agent_session_api_shape_and_honest_degrade():
    html = _html()
    # REAL shapes only: no trace of the guessed API anywhere.
    assert "/v1/agent/sessions" not in html, "guessed slash path must be gone"
    assert "approval_id" not in html, "the real API has no such field"
    start = _body(html, "startAgentSession")
    # create: hyphenated path, workspace_root key, session.id extraction
    # (app/main.py AgentSessionBody line 460 / create line 474)
    assert "agentFetch('/v1/agent-sessions'" in start
    assert "createBody.workspace_root = workspace" in start
    assert "created.data.session" in start and "session.id" not in start.split("created.data.session")[0][-200:]
    assert "status === 404" in start, "a 404 is detected explicitly, not lumped into errors"
    assert "AGENT_NOT_AVAILABLE" in html and "not available on this server yet" in html
    assert "404 for /v1/agent-sessions" in html, "the degrade text names the real path"
    # run: NO body (app/main.py line 514)
    assert "agentFetch('/v1/agent-sessions/' + rawSid + '/run', { method:'POST' })" in start
    # events: after cursor from the last event_id (app/store.py line 1047)
    assert "'/events'" in start and "?after=" in start and "st.cursor" in start
    assert "seenIds" in start, "each event renders exactly once"
    assert "document.hidden" in start, "agent polling pauses when the tab is hidden"
    assert "liveAgents" in html, "agent sessions track themselves per id (no composer lock)"
    # approvals: ONE endpoint, decision body (app/main.py line 469/538)
    appr = _body(html, "agentApproval")
    assert "'/approve'" in appr and "decision" in appr
    assert "approve ? 'approve' : 'deny'" in appr, "both answers go to the single approve endpoint"
    assert "agent-sessions/' + rawSid + '/approve'" in appr
    assert "/deny" not in appr, "there is no separate deny endpoint"
    # a parked pending_action also renders an approval card
    handle = _body(html, "handleAgentSession")
    assert "pending_action" in handle and "ApprovalNeeded" in handle
    render = _body(html, "renderAgentEvent")
    assert "ApprovalNeeded" in render and "call_id" in render, \
        "approval cards key off the real payload's call_id (agent_loop.py lines 484-487)"
    assert "AgentCompleted" in render and "AgentStep" in render and "ToolCall" in render
    # cancel: its own endpoint, no body (app/main.py line 564)
    cancel = _body(html, "agentCancel")
    assert "'/cancel'" in cancel and "method:'POST'" in cancel
    assert 'name="agent-cancel"' in start, "a live session offers a Cancel button"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 16, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
function makeRoutes(scenario) {
  const state = { connected: scenario !== 'firstrun', connSeq: 0, connUrls: {}, agentApproved: false };
  const routes = (path, opts) => {
    const method = (opts && opts.method) || 'GET';
    if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
    if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
    if (path === '/v1/worlds') return { s: 200, j: { worlds: [] } };
    if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
    if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
    if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
    if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
    if (path === '/v1/model-state') return { s: 200, j: state.connected ? { connected: true, model: 'stub-model', source: 'byok' } : { connected: false } };
    if (path === '/v1/models') return { s: 200, j: { connections: [], models: [] } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
    if (path === '/v1/missions' && method === 'POST') return { s: 200, j: { mission_id: 'm-new' } };
    if (path === '/v1/missions/m-new/run') return { s: 200, j: {} };
    if (path === '/v1/missions/m-new') return { s: 200, j: { mission: { id: 'm-new', title: 't', status: 'completed', spent: 0, contract: { objective: 'o', task_type: 'qa' } }, candidates: [], evaluations: [], critiques: [], tasks: [], events: [], route_decisions: [] } };
    if (path === '/v1/missions/m-new/events') return { s: 200, j: { events: [] } };
    if (path === '/v1/chat' && method === 'POST') { const b = JSON.parse(opts.body); return { s: 200, j: { reply: 'Stub reply to: ' + b.message, model: 'stub-model', source: 'byok' } }; }
    if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
    if (path === '/v1/connections' && method === 'POST') { const b = JSON.parse(opts.body); const id = 'c' + (++state.connSeq); state.connUrls[id] = b.base_url; return { s: 200, j: { id, masked_key: 'no key' } }; }
    const val = path.match(/^\/v1\/connections\/(c\d+)\/validate$/);
    if (val && method === 'POST') { const url = state.connUrls[val[1]] || ''; if (url.includes('11434')) { state.connected = true; return { s: 200, j: { state: 'active', catalog_sync: { models: 3 } } }; } return { s: 200, j: { state: 'error' } }; }
    if (/^\/v1\/connections\/c\d+$/.test(path) && method === 'DELETE') return { s: 200, j: {} };
    if (scenario === 'agent404' && path.startsWith('/v1/agent-')) return { s: 404, j: { error: { message: 'not found' } } };
    // REAL backend shapes (see module docstring for source lines):
    // create -> 201 {session:{...record fields, app/agent_loop.py line 185}}
    if (path === '/v1/agent-sessions' && method === 'POST') { const b = JSON.parse(opts.body); return { s: 201, j: { session: { id: 's1', goal: b.goal, workspace_root: b.workspace_root || '/tmp/repo', status: 'created', auto_approve_rw: false, max_steps: 25, steps_used: 0, spent_usd: 0, pending_action: null, model: null, connection_id: null, source: null, summary: '', failure: null, created_at: '2026-10-21T00:00:00Z', updated_at: '2026-10-21T00:00:00Z' } } }; }
    // run -> {session} parked on the approval (pending_action = {**tool_call, reason})
    if (path === '/v1/agent-sessions/s1/run' && method === 'POST') return { s: 200, j: { session: { id: 's1', status: 'awaiting_approval', pending_action: { call_id: 'call2', tool: 'run_shell', args: { command: 'python -m pytest -q' }, step: 2, reason: 'Running a shell command needs approval' } } } };
    // events -> {events:[envelopes]} honoring ?after=<event_id> (app/store.py line 1047);
    // post-approval events are only stored once the approve call lands.
    const evMatch = path.match(/^\/v1\/agent-sessions\/s1\/events(?:\?after=([^&]+))?$/);
    if (evMatch && method === 'GET') { const env = (event_id, event_type, payload) => ({ event_id, tenant_id: 'org_demo', aggregate_type: 'agent_session', aggregate_id: 's1', event_type, schema_version: '1.0', occurred_at: '2026-10-21T00:00:00Z', correlation_id: 'corr-' + event_id, payload });
      let evts = [ env('evt1', 'AgentSessionCreated', { goal: 'Make the test suite pass', workspace_root: '/tmp/repo', max_steps: 25, auto_approve_rw: false }),
        env('evt2', 'AgentStep', { step: 1, thought: 'Plan: read the file, then patch it' }),
        env('evt3', 'ToolCall', { step: 1, call_id: 'call1', tool: 'read_file', args: { path: 'src/app.py' }, needs_approval: false }),
        env('evt4', 'ToolResult', { call_id: 'call1', tool: 'read_file', ok: true, summary: 'path=src/app.py' }),
        env('evt5', 'ApprovalNeeded', { call_id: 'call2', tool: 'run_shell', args: { command: 'python -m pytest -q' }, reason: 'Running a shell command needs approval' }) ];
      if (state.agentApproved) evts = evts.concat([ env('evt6', 'ApprovalResolved', { call_id: 'call2', decision: 'approve' }),
        env('evt7', 'ToolResult', { call_id: 'call2', tool: 'run_shell', ok: true, summary: 'returncode=0' }),
        env('evt8', 'AgentCompleted', { summary: 'All tests pass now.', steps_used: 3 }) ]);
      if (evMatch[1]) { const idx = evts.findIndex(e => e.event_id === decodeURIComponent(evMatch[1])); if (idx >= 0) evts = evts.slice(idx + 1); }
      return { s: 200, j: { events: evts } }; }
    // approve/deny: ONE endpoint, {decision} body; resumes the loop -> completed session
    if (path === '/v1/agent-sessions/s1/approve' && method === 'POST') { state.agentApproved = true; return { s: 200, j: { session: { id: 's1', status: 'completed', pending_action: null, summary: 'All tests pass now.' } } }; }
    if (path === '/v1/agent-sessions/s1/cancel' && method === 'POST') return { s: 200, j: { session: { id: 's1', status: 'cancelled', pending_action: null } } };
    return { s: 200, j: {} };
  };
  routes.state = state;
  return routes;
}
function makeWindow(routes) {
  const calls = [];
  const dom = new JSDOM(HTML, { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.Element.prototype.scrollIntoView = function () {};
      window.scrollTo = () => {}; window.prompt = () => 'x'; window.confirm = () => true;
      window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
      window.fetch = async (path, opts = {}) => { calls.push({ method: opts.method || 'GET', path, body: opts.body || null });
        const r = routes(path, opts); return { ok: r.s < 300, status: r.s, json: async () => r.j, blob: async () => new window.Blob(['x']) }; };
    } });
  return { window: dom.window, calls };
}
const cnt = (calls, m, p) => calls.filter(c => c.method === m && c.path === p).length;
(async () => {
  const out = {};
  const s = process.argv[2];
  const routes = makeRoutes(s); const { window, calls } = makeWindow(routes); const d = window.document;
  await flush();
  if (s === 'firstrun') {
    out.card_visible = d.getElementById('connectCard').style.display !== 'none';
    out.welcome_hidden = d.getElementById('welcome').style.display === 'none';
    out.side_says_nomodel = d.getElementById('sideModel').textContent.includes('No model connected');
    await window.detectLocalModels(); await flush();
    out.create_posts = calls.filter(c => c.method === 'POST' && c.path === '/v1/connections').length;
    out.tried_ollama = calls.some(c => c.path === '/v1/connections' && (c.body || '').includes('11434'));
    out.tried_all = ['1234', '20128', '8080'].every(p => calls.some(c => (c.body || '').includes(p)));
    out.validate_posts = calls.filter(c => c.method === 'POST' && /\/validate$/.test(c.path)).length;
    out.results = d.getElementById('ccDetectResults').textContent;
    out.card_hidden_after = d.getElementById('connectCard').style.display === 'none';
    out.thread = d.getElementById('thread').innerHTML;
  } else if (s === 'agent404') {
    d.getElementById('modeAgentBtn').click();
    out.agent_row_shown = d.getElementById('agentRow').style.display === 'flex';
    d.getElementById('chatInput').value = 'Fix the failing tests in this repo';
    await window.sendChat(); await flush();
    out.create_posts = cnt(calls, 'POST', '/v1/agent-sessions');
    out.mission_creates = cnt(calls, 'POST', '/v1/missions');
    out.thread = d.getElementById('thread').innerHTML;
  } else if (s === 'agentflow') {
    d.getElementById('modeAgentBtn').click();
    d.getElementById('agentWorkspace').value = '/tmp/repo';
    d.getElementById('chatInput').value = 'Make the test suite pass';
    await window.sendChat(); await flush(20);
    const create = calls.find(c => c.method === 'POST' && c.path === '/v1/agent-sessions');
    out.create_body = create ? JSON.parse(create.body) : null;
    const run = calls.find(c => c.method === 'POST' && c.path === '/v1/agent-sessions/s1/run');
    out.run_posts = cnt(calls, 'POST', '/v1/agent-sessions/s1/run');
    out.run_body_is_null = run ? run.body === null : false;
    out.events_paths = calls.filter(c => c.path.startsWith('/v1/agent-sessions/s1/events')).map(c => c.path);
    out.mission_creates = cnt(calls, 'POST', '/v1/missions');
    out.chat_calls = cnt(calls, 'POST', '/v1/chat');
    out.steps = d.getElementById('asteps-s1') ? d.getElementById('asteps-s1').textContent : '';
    out.tool_card = !!d.querySelector('#abody-s1 details.toolcard');
    out.tool_card_open_text = d.querySelector('#abody-s1 details.toolcard') ? d.querySelector('#abody-s1 details.toolcard').textContent : '';
    out.approval_card = !!d.getElementById('appr-call2');
    out.thread = d.getElementById('thread').innerHTML;
    const btn = d.querySelector('#appr-call2 button[name="agent-approve"]');
    out.approve_btn_named = !!btn;
    if (btn) { btn.click(); await flush(6); }
    const appr = calls.find(c => c.path === '/v1/agent-sessions/s1/approve');
    out.approve_post = appr ? JSON.parse(appr.body) : null;
    out.approve_posts = cnt(calls, 'POST', '/v1/agent-sessions/s1/approve');
    out.events_after_approve = calls.filter(c => c.path.startsWith('/v1/agent-sessions/s1/events?after=')).length;
    out.events_used_after = calls.some(c => c.path.startsWith('/v1/agent-sessions/s1/events?after=evt'));
    out.approval_marked = d.getElementById('appr-call2').className.includes('done');
    out.thread_after = d.getElementById('thread').innerHTML;
  } else if (s === 'modes') {
    // mission mode forces the panel even for a greeting
    d.getElementById('modeMissionBtn').click();
    d.getElementById('chatInput').value = 'Hello!';
    await window.sendChat(); await flush();
    out.mission_in_mission_mode = cnt(calls, 'POST', '/v1/missions');
    out.chat_in_mission_mode = cnt(calls, 'POST', '/v1/chat');
    // chat mode routes the same greeting to conversation again
    d.getElementById('modeChatBtn').click();
    d.getElementById('chatInput').value = 'Hello!';
    await window.sendChat(); await flush();
    out.chat_in_chat_mode = cnt(calls, 'POST', '/v1/chat');
    out.mission_in_chat_mode = cnt(calls, 'POST', '/v1/missions');
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


def _run_harness(scenario: str) -> dict:
    import pytest
    node, env = _jsdom_env()
    if not node:
        pytest.skip("node/jsdom not available in this environment")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(HARNESS)
        script = f.name
    try:
        proc = subprocess.run([node, script, scenario], capture_output=True,
                              text=True, env=env, timeout=180)
        assert proc.returncode == 0, \
            f"harness {scenario} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_first_run_card_and_autodetect():
    out = _run_harness("firstrun")
    assert out["card_visible"] is True, "no model -> the connect card opens the chat view"
    assert out["welcome_hidden"] is True, "one card only — the welcome block stands down"
    assert out["side_says_nomodel"] is True
    assert out["tried_ollama"] and out["tried_all"], \
        "auto-detect probes Ollama, LM Studio, OmniRouter and llama.cpp"
    assert out["create_posts"] >= 4 and out["validate_posts"] >= 4
    assert "Ollama — connected" in out["results"]
    assert "nothing answered" in out["results"], "failures are reported in plain language"
    assert out["card_hidden_after"] is True, "the card swaps away once a model connects"
    assert "You're connected" in out["thread"], "the composer state says so, plainly"


def test_agent_mode_degrades_honestly_on_404():
    out = _run_harness("agent404")
    assert out["agent_row_shown"] is True
    assert out["create_posts"] == 1
    assert out["mission_creates"] == 0, "agent mode never falls back to a mission silently"
    assert "not available on this server yet" in out["thread"]
    assert "Chat and Mission modes work as normal" in out["thread"]


def test_agent_flow_renders_steps_tools_and_inline_approval():
    out = _run_harness("agentflow")
    # create body mirrors AgentSessionBody (app/main.py line 460):
    # goal + workspace_root only — no guessed "workspace" key.
    assert out["create_body"] == {"goal": "Make the test suite pass", "workspace_root": "/tmp/repo"}
    assert out["run_posts"] == 1
    assert out["run_body_is_null"] is True, "run is POSTed with NO body (app/main.py line 514)"
    assert out["events_used_after"] is True, "events polling advances the ?after= cursor"
    assert out["mission_creates"] == 0 and out["chat_calls"] == 0
    assert "Plan: read the file, then patch it" in out["steps"], "AgentStep thought renders"
    assert out["tool_card"] is True and "read_file" in out["tool_card_open_text"]
    assert 'name="agent-cancel"' in out["thread"], "a live session offers Cancel"
    assert out["approval_card"] is True, "the ApprovalNeeded event renders an approval card (keyed by call_id)"
    assert out["approve_btn_named"] is True
    # Approve POSTs {"decision":"approve"} to the ONE approve endpoint
    # (app/main.py ApprovalDecisionBody line 469 / endpoint line 538)
    assert out["approve_posts"] == 1
    assert out["approve_post"] == {"decision": "approve"}
    assert out["approval_marked"] is True
    assert out["events_after_approve"] >= 1, "after approving, polling resumes so the resumed loop renders"
    assert "Agent session finished." in out["thread_after"]
    assert "All tests pass now." in out["thread_after"], "AgentCompleted summary renders"


def test_mode_switch_forces_and_releases_the_panel():
    out = _run_harness("modes")
    assert out["mission_in_mission_mode"] == 1 and out["chat_in_mission_mode"] == 0, \
        "Mission mode sends even a greeting to the panel"
    assert out["chat_in_chat_mode"] == 1 and out["mission_in_chat_mode"] == 1, \
        "Chat mode routes the same greeting back to conversation (no new mission)"
