"""v31 UI contracts — the first-time-ever connect rule
(2026-10-22, Zohaib): the connect landing is "for the first
time ever a user connect — after that always shows the old
panel". Settled behavior:

- The landing page (#connectCard) may take the main screen ONLY
  when BOTH (a) no model is currently connected AND (b) the
  workspace has never connected — operationally, GET
  /v1/connections returns zero saved connections.
- A returning user (≥1 saved connection) whose connection is not
  answering lands on the panel welcome screen, where the
  welcome's model section (#homeConn) renders its NOT CONNECTED
  variant — same section, same place, same Manage connections
  action; only the status chip and one plain line change.
- If the connections check itself fails, the panel shows:
  a failed check never traps a returning user on the connect
  page.
- The composer lives on the main (chat) screen only (see the
  rewritten test_ui_v29): Advanced neither contains nor shows a
  chatbox.

Contract tests pin the shipped source; behavioral tests drive
the real page in jsdom (stubbed API) when node + jsdom are
available and skip otherwise — same standard as v24–v30.
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


# --------------------------------------------------------- contracts
def test_load_model_state_counts_saved_connections_when_disconnected():
    html = _html()
    ms = _body(html, "loadModelState")
    assert "await api('/v1/connections')" in ms, \
        "the first-time-ever check reads the saved connections"
    assert "if (!connected){" in ms, \
        "the check only runs when no model is live"
    assert "savedCount === 0" in ms and "noConnectionsSaved" in ms, \
        "zero saved connections is what 'never connected' means"
    assert "catch(e){ savedCount = null; }" in ms, \
        "a failed connections check falls back to returning-user treatment"
    assert "updateConnectCard(connected)" in ms, \
        "the card still follows the model state (v26/v28 pin)"
    assert "let noConnectionsSaved = false;" in html, \
        "the flag starts false — nobody is a first-timer until proven"


def test_landing_requires_first_time_ever():
    html = _html()
    ucc = _body(html, "updateConnectCard")
    assert "landingOn = !connected && noConnectionsSaved;" in ucc, \
        "the landing needs BOTH: no model live and never connected"
    assert "connected ? 'none' : 'block'" in ucc, "v28 pin preserved"
    assert "$('chatView').style.display = 'none'" in ucc, \
        "while the landing shows, the chat view stands down"
    assert "$('chatView').style.display = 'flex'" in ucc, \
        "connected or returning, the panel is what shows"


def test_homeconn_not_connected_variant():
    html = _html()
    ms = _body(html, "loadModelState")
    assert "st.textContent = 'Not connected'; st.className = 'chip warn';" in ms, \
        "the chip flips to the warn Not connected state"
    assert "Your saved connection is not answering right now." in ms, \
        "one plain line, in the product's voice"
    assert "$('hcModel').textContent = 'No model connected';" in ms
    # and the connected render resets both fields (no stale variant)
    assert "st.textContent = 'Connected'; st.className = 'chip ok';" in ms
    assert "$('hcVia').textContent = 'Via ' + connRouteText(h);" in ms, \
        "the connected rendering is the v30 one, untouched"
    # the variant is rendered by code into the same section — the
    # markup gains no second form and no forked section
    sec = html[html.index('id="homeConn"'):html.index('id="thread"')]
    assert sec.count('id="hcManageBtn"') == 1 and "<input" not in sec, \
        "same section, same single Manage action, still not a form"
    assert html.count("Your saved connection is not answering") == 1


def test_advanced_neither_contains_nor_shows_a_chatbox():
    html = _html()
    dash = html[html.index('id="dashView"'):html.index('id="skillsView"')]
    assert 'id="composer"' not in dash and 'id="chatInput"' not in dash, \
        "Advanced contains no chatbox"
    assert html.count('id="composer"') == 1
    assert html.index('id="composer"') < html.index('id="dashView"'), \
        "the one chatbox sits in the chat screen, before Advanced in the column"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 24, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
const MODE = process.argv[3] || 'firsttime';
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
    if (path === '/v1/model-state') return { s: 200, j: { connected: false } };
    if (path === '/v1/models') return { s: 200, j: { connections: [], models: [] } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
    if (path === '/v1/connections' && method === 'GET') {
      if (MODE === 'returning') return { s: 200, j: { connections: [{ id: 'c1', label: 'Old local', kind: 'local_endpoint', state: 'error' }] } };
      if (MODE === 'connfail') return { s: 500, j: {} };
      return { s: 200, j: { connections: [] } };
    }
    if (path === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
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
  const { window, calls } = makeWindow(makeRoutes()); const d = window.document;
  await flush();
  const comp = d.getElementById('composer');
  out.card = d.getElementById('connectCard').style.display;
  out.chat = d.getElementById('chatView').style.display;
  out.welcome = d.getElementById('welcome').style.display;
  const hc = d.getElementById('homeConn');
  out.homeconn = hc.style.display;
  out.hc_model = d.getElementById('hcModel').textContent;
  out.hc_status = d.getElementById('hcStatus').textContent;
  out.hc_status_class = d.getElementById('hcStatus').className;
  out.hc_via = d.getElementById('hcVia').textContent;
  out.side = d.getElementById('sideModel').textContent;
  out.composer_parent = comp.parentElement.id;
  out.composer_in_dash = d.getElementById('dashView').contains(comp);
  out.checked_connections = calls.some(c => c.method === 'GET' && c.path === '/v1/connections');
  if (MODE === 'returning') {
    d.getElementById('hcManageBtn').click(); await flush(6);
    out.view_after_manage = d.body.dataset.view;
    out.connections_folded = d.getElementById('cardConnections').dataset.folded;
    out.chat_hidden_on_dash = d.getElementById('chatView').style.display === 'none';
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
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(HARNESS)
        script = f.name
    try:
        proc = subprocess.run([node, script, "boot", mode], capture_output=True,
                              text=True, env=env, timeout=180)
        assert proc.returncode == 0, \
            f"harness {mode} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_first_time_ever_gets_the_landing():
    out = _run_harness("firsttime")
    assert out["checked_connections"] is True, \
        "the page actually counted the saved connections"
    assert out["card"] == "block", \
        "no model + zero saved connections: the connect landing owns the screen"
    assert out["chat"] == "none" and out["welcome"] == "none", \
        "the chat screen stands down under it"
    assert out["homeconn"] == "none", "no model section on the landing"
    assert "No model connected" in out["side"]


def test_returning_user_gets_the_panel_not_the_landing():
    out = _run_harness("returning")
    assert out["card"] == "none", \
        "a saved connection exists: the landing never shows again"
    assert out["chat"] == "flex" and out["welcome"] != "none", \
        "the old panel welcome is the screen"
    assert out["homeconn"] == "block", "the model section renders in place"
    assert out["hc_status"] == "Not connected" \
        and "warn" in out["hc_status_class"], "the chip says it plainly"
    assert "not answering" in out["hc_via"], "and one plain line explains"
    assert out["hc_model"] == "No model connected"
    assert out["composer_parent"] == "chatView" \
        and out["composer_in_dash"] is False, \
        "the chatbox is at the bottom of this screen — and nowhere else"
    # ROUTE KEPT, BY DESIGN (2026-10-23, Zohaib's refined map):
    # connections live ONLY in Advanced — Manage connections is a
    # deliberate settings route to that card, unfolded on arrival
    # (v36 pins the whole navigation contract).
    assert out["view_after_manage"] == "dash" \
        and out["connections_folded"] == "0", \
        "Manage connections routes to Advanced's connections card, unfolded"
    assert out["chat_hidden_on_dash"] is True, \
        "on Advanced the chat screen (chatbox included) is not shown"


def test_failed_connections_check_still_shows_the_panel():
    out = _run_harness("connfail")
    assert out["card"] == "none", \
        "a failed check must never trap a returning user on the connect page"
    assert out["chat"] == "flex", "the panel shows"
    assert out["homeconn"] == "none", \
        "and the section stays hidden — it cannot vouch for a saved connection"
