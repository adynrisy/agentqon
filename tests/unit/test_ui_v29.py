"""v29 UI contracts — the composer's home (REWRITTEN 2026-10-22).

This suite originally pinned the composer dock: #composer moved
out of #chatView into <main>, visible on Chat and on the Panel
(Advanced), per Zohaib's "dont remove the chatbox from the panel
just adjust the both together". Later the same day Zohaib
CORRECTED the placement, verbatim: "dont put the chat box in the
advance section. instead put it on the main screen only in
bottom" — and this suite was rewritten to pin the corrected
placement:

- #composer is back INSIDE #chatView, the bottom element of the
  main (chat) screen, after the thread — held at the bottom by
  the base CSS (the chat view is a flex column; the composer is
  sticky with margin-top:auto). Advanced and Skills contain and
  show no chatbox.
- The dock machinery is gone: no body[data-view] composer rule,
  no composerToChat() send gate (send handlers never switch
  views), no 100vh shell with per-view scrolling, and nothing in
  setView/updateConnectCard touches the composer's display — it
  stands down with the chat view while the first-run landing
  owns the screen, exactly as it did before the dock, and is
  restored the moment a model seats.

Contract tests pin the shipped source; behavioral tests drive
the real page in jsdom (stubbed API) when node + jsdom are
available and skip otherwise — same standard as test_ui_v24–v28.
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


def _section(html: str, section_id: str) -> str:
    start = html.index(f'id="{section_id}"')
    start = html.rindex("<section", 0, start)
    return html[start:html.index("</section>", start)]


# ------------------------------------------------------------ layout
def test_composer_lives_inside_chat_view_at_its_bottom():
    html = _html()
    chat = _section(html, "chatView")
    assert 'id="composer"' in chat, \
        "the chatbox is back inside the main (chat) screen — the owner's correction"
    assert chat.index('id="thread"') < chat.index('id="composer"'), \
        "the composer is the bottom element of the chat screen, after the thread"
    assert html.count('id="composer"') == 1, "there is exactly one chatbox"
    assert 'id="composer"' not in html[html.index('id="dashView"'):], \
        "no chatbox inside Advanced, Skills, or anywhere after them"
    main = html[html.index("<main>"):html.index("</main>")]
    assert main.index('id="chatView"') < main.index('id="composer"') \
        < main.index('id="skillsView"'), \
        "document order: the composer sits within the chat view's span of <main>"


def test_composer_component_is_whole():
    html = _html()
    box = html[html.index('id="composer"'):html.index('id="connectCard"')]
    for name in ('name="mode-chat"', 'name="mode-mission"', 'name="mode-agent"',
                 'name="agent-start"', 'name="mission-send"'):
        assert name in box, name + " — no part of the chatbox was removed"
    for fid in ('id="modeChatBtn"', 'id="modeMissionBtn"', 'id="modeAgentBtn"',
                'id="agentRow"', 'id="agentWorkspace"', 'id="agentStartBtn"',
                'id="chatInput"', 'id="chatSend"'):
        assert fid in box, fid


def test_dock_machinery_is_gone():
    html = _html()
    assert "composerToChat" not in html, \
        "the send gate is gone — sends never switch views"
    assert "$('composer').style.display" not in html, \
        "nothing pokes the composer's display: it stands down with the chat view"
    assert 'body[data-view="skills"] #composer{display:none}' not in html
    assert 'body[data-view="dash"] #composer' not in html, \
        "no view-scoped composer rule survives the revert"
    assert "#shell{height:100vh}" not in html, "no fixed-height shell"
    assert "#chatView{min-height:0;overflow-y:auto}" not in html, \
        "the views no longer scroll individually — the page scrolls"
    assert "$('dashView').scrollTop" not in html \
        and "$('chatView').scrollTop" not in html, \
        "the nav handlers are back to window.scrollTo only"


def test_composer_bottom_of_chat_css():
    html = _html()
    assert "#shell{display:flex;min-height:100dvh}" in html, \
        "the shell grows with the page again"
    assert "#chatView{" in html and "flex-direction:column" in html, \
        "the chat view is a flex column"
    # the composer rule was reformatted by the restyle (multi-line);
    # the sticky geometry itself is unchanged: sticky, bottom 14px,
    # margin-top auto (see the rule's own comment).
    comp = re.search(r"#composer\s*\{[^}]*\}", html)
    assert comp, "the composer rule exists"
    for decl in ("position: sticky", "bottom: 14px", "margin-top: auto"):
        assert decl in comp.group(0), \
            f"the composer still sticks to its bottom ({decl})"


# ------------------------------------------------------------ routing
def test_send_flows_have_no_view_gate():
    html = _html()
    send = _body(html, "sendChat")
    assert "composerToChat" not in send and "setView(" not in send, \
        "chat send runs in place — the composer only exists on the chat screen"
    agent = _body(html, "startAgentSession")
    assert "composerToChat" not in agent and "setView(" not in agent, \
        "agent start runs in place too"
    sv = _body(html, "setView")
    assert "$('composer')" not in sv, "setView never touches the composer"
    ucc = _body(html, "updateConnectCard")
    assert "$('composer')" not in ucc, \
        "the landing stands the composer down by standing the chat view down"
    # the entry points are unchanged — one composer, one set of handlers
    assert "$('chatSend').onclick = sendChat;" in html
    assert "$('agentStartBtn').onclick = () => startAgentSession();" in html
    assert "if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }" in html


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 16, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
const state = { connected: process.argv[3] !== 'landing' };
function makeRoutes() {
  return (path, opts) => {
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
    if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
    if (path === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
    if (path === '/v1/chat' && method === 'POST') return { s: 200, j: { reply: 'Stub reply from the v29 test.' } };
    if (path === '/v1/agent-sessions' && method === 'POST') return { s: 200, j: { session: { id: 'ags_test1', status: 'running' } } };
    if (path === '/v1/agent-sessions/ags_test1/run' && method === 'POST') return { s: 200, j: { session: { id: 'ags_test1', status: 'completed' } } };
    if (path === '/v1/agent-sessions/ags_test1/events') return { s: 200, j: { events: [] } };
    return { s: 200, j: {} };
  };
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
(async () => {
  const out = {};
  const s = process.argv[2];
  const { window, calls } = makeWindow(makeRoutes()); const d = window.document;
  const comp = d.getElementById('composer');
  await flush();
  if (s === 'placement') {
    out.parent_id = comp.parentElement.id;
    out.in_chat_view = d.getElementById('chatView').contains(comp);
    out.in_dash_view = d.getElementById('dashView').contains(comp);
    out.after_thread = comp.previousElementSibling && comp.previousElementSibling.id === 'thread';
    out.chat_display = d.getElementById('chatView').style.display;
    window.setView('dash'); await flush(4);
    out.dash_view = d.body.dataset.view;
    out.dash_shown = d.getElementById('dashView').style.display === 'block';
    out.chat_hidden_on_dash = d.getElementById('chatView').style.display === 'none';
    window.setView('skills'); await flush(4);
    out.skills_shown = d.getElementById('skillsView').style.display === 'block';
    out.chat_hidden_on_skills = d.getElementById('chatView').style.display === 'none';
    window.setView('chat'); await flush(4);
    out.back_to_chat = d.getElementById('chatView').style.display === 'flex';
  } else if (s === 'landing') {
    out.card = d.getElementById('connectCard').style.display;
    out.chat = d.getElementById('chatView').style.display;
    out.welcome = d.getElementById('welcome').style.display;
    out.homeconn = d.getElementById('homeConn').style.display;
    state.connected = true;
    await window.loadModelState(); await flush();
    out.card_after_seat = d.getElementById('connectCard').style.display;
    out.chat_after_seat = d.getElementById('chatView').style.display;
  } else if (s === 'chatsend') {
    d.getElementById('chatInput').value = 'Hello there';
    d.getElementById('chatSend').click(); await flush(24);
    out.view_after_send = d.body.dataset.view;
    const chat = calls.find(c => c.method === 'POST' && c.path === '/v1/chat');
    out.chat_post = chat ? JSON.parse(chat.body) : null;
    out.thread = d.getElementById('thread').innerHTML;
  } else if (s === 'agentstart') {
    window.setMode('agent'); await flush(2);
    out.agent_row_shown = d.getElementById('agentRow').style.display === 'flex';
    d.getElementById('chatInput').value = 'List the files in the workspace';
    d.getElementById('agentStartBtn').click(); await flush(24);
    out.view_after_start = d.body.dataset.view;
    const create = calls.find(c => c.method === 'POST' && c.path === '/v1/agent-sessions');
    out.create_post = create ? JSON.parse(create.body) : null;
    out.agent_box = !!d.getElementById('agent-ags_test1');
    out.thread_has_goal = d.getElementById('thread').innerHTML.includes('List the files in the workspace');
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


def _run_harness(scenario: str, mode: str = "connected") -> dict:
    import pytest
    node, env = _jsdom_env()
    if not node:
        pytest.skip("node/jsdom not available in this environment")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(HARNESS)
        script = f.name
    try:
        proc = subprocess.run([node, script, scenario, mode], capture_output=True,
                              text=True, env=env, timeout=180)
        assert proc.returncode == 0, \
            f"harness {scenario}/{mode} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_composer_placement_in_dom():
    out = _run_harness("placement")
    assert out["parent_id"] == "chatView", "the composer is a child of the chat view"
    assert out["in_chat_view"] is True and out["in_dash_view"] is False
    assert out["after_thread"] is True, "it sits at the bottom, right after the thread"
    assert out["chat_display"] == "flex"
    assert out["dash_view"] == "dash" and out["dash_shown"] is True
    assert out["chat_hidden_on_dash"] is True, \
        "on Advanced the whole chat screen — chatbox included — stands down"
    assert out["skills_shown"] is True and out["chat_hidden_on_skills"] is True
    assert out["back_to_chat"] is True


def test_landing_stands_chat_down_and_seat_restores_it():
    out = _run_harness("landing", "landing")
    assert out["card"] == "block", \
        "first-time-ever (no model, zero saved connections): the landing owns the screen"
    assert out["chat"] == "none" and out["welcome"] == "none", \
        "the chat screen — composer included — stands down under the landing"
    assert out["homeconn"] == "none"
    assert out["card_after_seat"] == "none" and out["chat_after_seat"] == "flex", \
        "the moment a model seats, the landing hands the screen back"


def test_chat_send_from_the_chat_screen_fires():
    out = _run_harness("chatsend")
    assert out["view_after_send"] == "chat", "a chat send never moves the user"
    assert out["chat_post"] and out["chat_post"]["message"] == "Hello there", \
        "the existing conversation flow fired against the API"
    assert "Hello there" in out["thread"] \
        and "Stub reply from the v29 test." in out["thread"], \
        "the user sees their message and the answer in the thread"


def test_agent_start_from_the_chat_screen_fires():
    out = _run_harness("agentstart")
    assert out["agent_row_shown"] is True
    assert out["view_after_start"] == "chat"
    assert out["create_post"] and \
        out["create_post"]["goal"] == "List the files in the workspace", \
        "the existing agent-session create fired with the typed goal"
    assert out["agent_box"] is True and out["thread_has_goal"] is True, \
        "the session's live box is on screen in the thread"
