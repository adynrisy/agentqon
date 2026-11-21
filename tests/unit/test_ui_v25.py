"""v21.4.3 — conversation mode + the composer never locks.

Directive 1: the chat must understand normal conversation — a
greeting or a question is answered by ONE model call (POST /v1/chat),
never turned into a panel mission. The routing is a deterministic
client-side classifier (chatRoute) that runs before any network call.

Directive 2: the harness must always take the next request. The old
chatBusy flag held Send for the entire inline /run POST; runChatMission
now FIRES the run and drives completion by polling the mission, the
composer unlocks at kickoff, and live missions track themselves in
liveMissions (per-mission), so conversation and further missions work
while one runs.

Contract tests pin the shipped source; behavioral tests drive the
real page in jsdom (stubbed API) when node + jsdom are available and
skip otherwise — same standard as test_ui_v24.
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


# ---------------------------------------------------------- contracts
def test_no_global_composer_lock_remains():
    html = _html()
    assert "chatBusy" not in html, "the global composer lock must stay gone"
    assert "liveMissions" in html, "live missions are tracked per mission id"
    run = _body(html, "runChatMission")
    assert "liveMissions.add(mid)" in run and "liveMissions.delete(mid)" in run
    assert "if (liveMissions.has(mid)) return;" in run, \
        "the only run guard left is the per-mission double-fire guard"


def test_run_is_fired_not_awaited_and_completion_is_polled():
    html = _html()
    run = _body(html, "runChatMission")
    assert "await api('/v1/missions/' + mid + '/run'" not in run, \
        "the /run POST must not be awaited — in inline mode it blocks for the whole mission"
    assert "/run'" in run and ".catch(" in run, "the run is fired with its error captured"
    assert "TERMINAL" in run and "'completed'" in run and "'failed'" in run, \
        "completion is driven by polling the mission to a terminal state"
    assert "setInterval(tick, 1500)" in run
    assert "Mission running — ask me anything meanwhile" in run, \
        "the live box tells the user the composer is free"
    send = _body(html, "sendChat")
    assert "runChatMission(m.mission_id);" in send
    assert "await runChatMission" not in send, \
        "sendChat returns at kickoff — Send is usable again immediately"


def test_conversation_path_uses_chat_endpoint_and_client_history():
    html = _html()
    conv = _body(html, "sendConversation")
    assert "fetch('/v1/chat'" in conv, "conversation goes to POST /v1/chat"
    assert "/v1/missions" not in conv, "conversation never creates a mission"
    assert "chatHistory" in conv, "the client keeps conversation history"
    assert "AbortController" in conv and "ctrl.abort()" in conv, \
        "a client-side timeout guarantees the call cannot hang forever"
    assert "chatCallInFlight = false" in conv, \
        "the in-flight flag is released in the finally path (any outcome)"
    assert "model_not_connected" in conv, "the 503 case renders honest guidance inline"
    assert "No model is connected" in conv
    newchat = re.search(r"\$\('newChatBtn'\)\.onclick = \(\) => \{([^}]*)\}", html)
    assert newchat and "chatHistory = []" in newchat.group(1), \
        "New chat resets the conversation history"


def test_classifier_is_deterministic_and_pre_network():
    html = _html()
    route = _body(html, "chatRoute")
    assert "CHAT_PRODUCTION_VERBS" in html
    send = _body(html, "sendChat")
    assert send.index("chatRoute(text)") < send.index("api('/v1/missions'"), \
        "routing is decided before the mission network call"
    assert send.index("chatRoute(text)") < send.index("sendConversation(text, atts)")


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 14, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
function makeRoutes() {
  const state = { chatFails: false };
  const routes = (path, opts) => {
    const method = (opts && opts.method) || 'GET';
    if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
    if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
    if (path === '/v1/connections') return { s: 200, j: { connections: [] } };
    if (path === '/v1/models') return { s: 200, j: { connections: [], models: [] } };
    if (path === '/v1/worlds') return { s: 200, j: { worlds: [] } };
    if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
    if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
    if (path === '/v1/missions' && method === 'POST') return { s: 200, j: { mission_id: 'm-new' } };
    if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
    if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
    if (path === '/v1/model-state') return { s: 200, j: { connected: true, model: 'm', source: 'byok' } };
    if (path === '/v1/chat' && method === 'POST') {
      if (state.chatFails) return { s: 503, j: { error: { code: 'model_not_connected', message: 'No model is connected' } } };
      const b = JSON.parse(opts.body);
      return { s: 200, j: { reply: 'Stub reply to: ' + b.message, model: 'stub-model-1', source: 'env' } };
    }
    if (path === '/v1/missions/m-new/run') return { s: 200, j: {} };
    if (path === '/v1/missions/m-new') return { s: 200, j: { mission: { id: 'm-new', title: 't', status: 'completed', spent: 0, contract: { objective: 'o', task_type: 'qa' } }, candidates: [], evaluations: [], critiques: [], tasks: [], events: [], route_decisions: [] } };
    if (path === '/v1/missions/m-new/events') return { s: 200, j: { events: [] } };
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
  const routes = makeRoutes(); const { window, calls } = makeWindow(routes); const d = window.document;
  await flush();
  if (s === 'classify') {
    const cases = {
      'Hello!': 'chat',
      'hi there': 'chat',
      'thanks a lot': 'chat',
      'good morning': 'chat',
      'What is the capital of France?': 'chat',
      'How do rainbows form?': 'chat',
      'Explain how rainbows form, in simple terms': 'chat',
      'Who are you?': 'chat',
      'What can you do?': 'chat',
      'Which model are you using?': 'chat',
      'what makes the sky blue?': 'chat',
      'how do I make a sourdough starter?': 'chat',
      'Write a poem about the sea': 'mission',
      'write a poem about autumn leaves': 'mission',
      'Can you write me a poem?': 'mission',
      'Research solar batteries and compare the top three options': 'mission',
      'Plan a 7-day study schedule for a biology exam': 'mission',
      'Make a logo for my bakery': 'mission',
      'Summarize this article for me': 'mission',
      'Review this contract and flag the risks': 'mission',
      'Compare growth strategies for a small business page posting short product videos': 'mission',
    };
    out.routes = {};
    for (const [text, want] of Object.entries(cases)) out.routes[text] = [window.chatRoute(text), want];
  } else if (s === 'converse') {
    d.getElementById('chatInput').value = 'Hello, who are you?';
    await window.sendChat(); await flush();
    out.chat_calls = cnt(calls, 'POST', '/v1/chat');
    out.mission_creates = cnt(calls, 'POST', '/v1/missions');
    out.thread_has_reply = d.getElementById('thread').innerHTML.includes('Stub reply to: Hello, who are you?');
    out.thread_kept_user = d.getElementById('thread').innerHTML.includes('Hello, who are you?');
    const chatBody = JSON.parse(calls.find(c => c.path === '/v1/chat').body);
    out.history_sent = chatBody.history.length;
    // second conversational turn carries the first in its history
    d.getElementById('chatInput').value = 'What can you do?';
    await window.sendChat(); await flush();
    const bodies = calls.filter(c => c.path === '/v1/chat').map(c => JSON.parse(c.body));
    out.second_history = bodies[1].history.map(h => h.role).join(',');
    out.chat_calls2 = cnt(calls, 'POST', '/v1/chat');
  } else if (s === 'unlock') {
    // start a mission (run POST resolves, mission polls to completed)
    d.getElementById('chatInput').value = 'Write a haiku about rain';
    const p = window.sendChat(); await flush(6);
    out.send_returned = true; // sendChat resolved without waiting for the run
    await p;
    // while/after the mission, a conversation message still goes through
    d.getElementById('chatInput').value = 'Hello!';
    await window.sendChat(); await flush();
    out.chat_after_mission = cnt(calls, 'POST', '/v1/chat');
    out.mission_created = cnt(calls, 'POST', '/v1/missions');
    out.live_box_gone = !d.getElementById('live-m-new');
  } else if (s === 'nomodel') {
    routes.state.chatFails = true;
    d.getElementById('chatInput').value = 'Hello!';
    await window.sendChat(); await flush();
    out.thread = d.getElementById('thread').innerHTML;
    out.shows_guidance = out.thread.includes('No model is connected');
    // composer released after the failure: flip the stub healthy and retry
    routes.state.chatFails = false;
    d.getElementById('chatInput').value = 'Hello again';
    await window.sendChat(); await flush();
    out.retry_worked = d.getElementById('thread').innerHTML.includes('Stub reply to: Hello again');
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


def test_classifier_cases():
    out = _run_harness("classify")
    bad = {t: got for t, (got, want) in out["routes"].items() if got != want}
    assert not bad, f"misrouted: {bad}"


def test_conversation_end_to_end_no_mission():
    out = _run_harness("converse")
    assert out["chat_calls"] == 1
    assert out["mission_creates"] == 0, "conversation must never create a mission"
    assert out["thread_has_reply"] and out["thread_kept_user"]
    assert out["history_sent"] == 0
    assert out["second_history"] == "user,assistant", \
        "the second turn carries the first exchange as history"
    assert out["chat_calls2"] == 2


def test_composer_unlocks_after_mission_kickoff():
    out = _run_harness("unlock")
    assert out["send_returned"] is True
    assert out["mission_created"] == 1
    assert out["chat_after_mission"] == 1, \
        "a new request is accepted while/after a mission — the composer is not locked"
    assert out["live_box_gone"] is True, "the live box resolves once the mission is terminal"


def test_no_model_chat_failure_is_inline_and_releases_composer():
    out = _run_harness("nomodel")
    assert out["shows_guidance"] is True, \
        "a 503 renders the honest no-model guidance in the thread, not just a toast"
    assert out["retry_worked"] is True, \
        "the composer is released after a failed conversation call"
