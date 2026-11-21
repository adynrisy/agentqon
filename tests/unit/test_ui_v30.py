"""v30 UI contracts — Zohaib's three edits (2026-10-22, on review
of the Verdict Room + composer-dock build):

1. The welcome screen's three Exhibit stat tiles (#homeStats —
   skills / missions / top agent, fed by loadHomeStats) are
   REMOVED: markup, CSS, the loader and both call sites.
2. In their place, the welcome carries THE MODEL CONNECTION: one
   section (#homeConn) rendered from loadModelState — the same
   state that feeds the sidebar — showing the seated model, the
   connection it came through in plain words, its Connected
   status, and ONE action ("Manage connections") routed to
   Advanced's AI connections card via goDash/unfoldCard. It is
   connected-state only: no model → the first-run landing owns
   the screen (untouched); a failed state load hides the section.
   No second connect form exists anywhere.
3. Advanced loses the New mission creation card (#cardNew):
   mission creation lives only in the composer's Mission tab.
   The Your missions records card and every mission detail/run/
   proof flow stay; the fold docket still numbers 01–09 with no
   gap (cardNew was never a fold).

Contract tests pin the shipped source; behavioral tests drive
the real page in jsdom (stubbed API) when node + jsdom are
available and skip otherwise — same standard as v24–v29.
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


# ------------------------------------------------------- edit 1: tiles
def test_exhibit_tiles_and_loader_are_gone():
    html = _html()
    for gone in ('id="homeStats"', 'id="hsSkills"', 'id="hsMissions"',
                 'id="hsLatest"', 'id="hsLatestTile"', 'id="hsAgent"',
                 'id="hsAgentTile"', "stat-tile", "loadHomeStats"):
        assert gone not in html, gone + " — the Exhibit tiles were removed by the owner"
    sv = _body(html, "setView")
    assert "loadHomeStats" not in sv, "no dead call in setView"
    assert "homeStats" not in html, "no dead reference anywhere"


# ------------------------------------------------- edit 2: the section
def test_welcome_connection_section_lives_where_tiles_were():
    html = _html()
    chat = _section(html, "chatView")
    assert 'id="homeConn"' in chat, "the section is on the welcome screen"
    assert chat.index('class="examples"') < chat.index('id="homeConn"') \
        < chat.index('id="thread"'), "it sits exactly where the tiles were"
    sec = chat[chat.index('id="homeConn"'):]
    assert "Model in use" in sec, "the model-in-use label"
    assert 'id="hcModel"' in sec and 'id="hcVia"' in sec
    assert 'id="hcStatus"' in sec and "Connected" in sec, "the live status chip"
    assert 'name="welcome-manage-connections"' in sec
    assert 'id="hcManageBtn"' in sec and "Manage connections" in sec, \
        "exactly one action: manage connections"
    # connected-state only markup: it starts hidden; loadModelState
    # is the only thing that shows it.
    tag = re.search(r'<div id="homeConn"[^>]*>', chat)
    assert tag and "display:none" in tag.group(0)


def test_homeconn_renders_from_load_model_state():
    html = _html()
    ms = _body(html, "loadModelState")
    assert "$('homeConn')" in ms, "loadModelState owns the section"
    assert "$('hcModel').textContent = h.model" in ms, \
        "the seated model's name comes from the model state"
    assert "$('hcVia').textContent = 'Via ' + connRouteText(h);" in ms
    assert "hc.style.display = 'block';" in ms, "shown when connected"
    assert "hc.style.display = 'none';" in ms, "hidden when not"
    # the failure path hides it too — never a stale connection
    assert "const hc = $('homeConn'); if (hc) hc.style.display = 'none';" in ms
    route = _body(html, "connRouteText")
    for words in ("API key connection", "Local model server",
                  "Ollama", "OmniRouter", "Configured endpoint"):
        assert words in route, words + " — the route in plain words"


def test_manage_connections_uses_the_goDash_route():
    # ROUTE KEPT, BY DESIGN (2026-10-23, Zohaib's refined map):
    # connections live ONLY in Advanced, so the welcome card's one
    # action still rides goDash to that card — now the helper's
    # ONLY job in the product (v36 pins the whole map).
    html = _html()
    assert "$('hcManageBtn').onclick = goDash('cardConnections');" in html, \
        "one action, routed exactly like the sidebar's connections jump"


def test_no_second_connect_form():
    html = _html()
    # the landing's full form is the one and only connect form
    assert html.count('id="ccApiKey"') == 1 and html.count('id="ccConnectBtn"') == 1
    sec = html[html.index('id="homeConn"'):html.index('id="thread"')]
    assert "<input" not in sec and "ccApiKey" not in sec, \
        "the welcome section shows the connection — it is not a form"


# ------------------------------------------------- edit 3: no creation
def test_advanced_has_no_mission_creation_card():
    html = _html()
    dash = html[html.index('id="dashView"'):html.index('id="missionsView"')]
    for gone in ('id="cardNew"', 'name="mission-create-run"',
                 'name="mission-load-example"', 'id="title"',
                 'id="objective"'):
        assert gone not in dash, gone
    # Records + detail flows are untouched as FEATURES — but since
    # 2026-10-23 (Zohaib's navigation rule) they live in the
    # Missions section, not in Advanced; the full contract is v36.
    for moved in ('id="cardMissions"', 'id="missions"', 'id="missionCard"',
                  'id="compareCard"', 'id="pauseBtn"', 'id="exportBtn"'):
        assert moved not in dash, moved + " — no longer inside Advanced"
        assert moved in html, moved + " — the feature itself survives"
    # creation lives in the composer
    assert 'id="modeMissionBtn"' in html and 'name="mode-mission"' in html


def test_fold_docket_runs_unbroken_without_cardNew():
    html = _html()
    # SUPERSEDED INVENTORY (2026-10-23, Zohaib's refined map):
    # Agents, Simulations and Your missions left the Advanced
    # dockets for pages of their own, where their cards stand open
    # (a page is not a fold). Connections STAY in Advanced, folded
    # like the other docket tools that live there.
    # SEVEN folds (2026-10-24, gap-program gate repair): the W9
    # Workbench card is an ordered Advanced feature — it lives in
    # Advanced as a fold like the other docket tools (it is not a
    # sidebar page), so it joins the docket as the 7th fold.
    folds = re.findall(r'<div class="card foldable" id="(\w+)"', html)
    assert folds == ['cardUsage', 'cardConnections', 'cardTelegram',
                     'cardWhatsApp', 'cardModels', 'cardWorkbench',
                     'cardAdmin'], \
        "seven folds remain — exactly Advanced's own docket tools"
    init = _body(html, "initFolds")
    # the docket numbering is GONE by the restyle (spec §7 Tier C):
    # folds are no longer auto-numbered. The fold mechanism itself
    # survives: every foldable card gets a chevron affordance.
    assert "indexOf(card) + 1" not in init, "folds are no longer auto-numbered"
    assert "querySelectorAll('.card.foldable')" in init, "folds are still wired"
    assert "fold-chev" in init, "the fold affordance is the chevron"
    assert "$('navMissions').onclick = () => { setView('missions'); closeSide(); };" in html, \
        "the Missions nav opens the Missions section, not a creation form"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 16, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
const MODE = process.argv[3] || 'local';
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
    if (path === '/v1/model-state') {
      if (MODE === 'fail') return { s: 500, j: {} };
      if (MODE === 'byok') return { s: 200, j: { connected: true, model: 'acme/gpt-x', source: 'byok', base_url: 'https://api.acme.example/v1' } };
      return { s: 200, j: { connected: true, model: 'stub-model-1', source: 'ollama', base_url: 'http://127.0.0.1:11434/v1' } };
    }
    if (path === '/v1/models') return { s: 200, j: { connections: [], models: [] } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
    if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
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
  const s = process.argv[2];
  const { window, calls } = makeWindow(makeRoutes()); const d = window.document;
  await flush();
  if (s === 'welcome') {
    out.tiles_exist = !!d.getElementById('homeStats');
    const hc = d.getElementById('homeConn');
    out.section_display = hc.style.display;
    out.model = d.getElementById('hcModel').textContent;
    out.via = d.getElementById('hcVia').textContent;
    out.status = d.getElementById('hcStatus').textContent;
    d.getElementById('hcManageBtn').click(); await flush(6);
    out.view_after_manage = d.body.dataset.view;
    out.dash_shown = d.getElementById('dashView').style.display === 'block';
    out.connections_folded = d.getElementById('cardConnections').dataset.folded;
    out.new_card_exists = !!d.getElementById('cardNew');
    out.missions_card_exists = !!d.getElementById('cardMissions');
  } else if (s === 'byok') {
    out.section_display = d.getElementById('homeConn').style.display;
    out.model = d.getElementById('hcModel').textContent;
    out.via = d.getElementById('hcVia').textContent;
  } else if (s === 'fail') {
    out.section_display = d.getElementById('homeConn').style.display;
    out.side = d.getElementById('sideModel').textContent;
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


def _run_harness(scenario: str, mode: str = "local") -> dict:
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


def test_welcome_section_renders_and_manage_routes():
    out = _run_harness("welcome", "local")
    assert out["tiles_exist"] is False, "the Exhibit tiles are gone from the DOM"
    assert out["section_display"] == "block", "the connection section shows when seated"
    assert out["model"] == "stub-model-1"
    assert "Ollama" in out["via"] and "Local model server" in out["via"], \
        "the connection it came through, in plain words"
    assert out["status"] == "Connected"
    assert out["view_after_manage"] == "dash" and out["dash_shown"] is True, \
        "Manage connections jumps to Advanced"
    assert out["connections_folded"] == "0", \
        "and unfolds the AI connections card"
    assert out["new_card_exists"] is False, "no New mission card in Advanced"
    assert out["missions_card_exists"] is True, "Your missions stays"


def test_welcome_section_byok_route_words():
    out = _run_harness("byok", "byok")
    assert out["section_display"] == "block"
    assert out["model"] == "acme/gpt-x"
    assert "API key connection" in out["via"]


def test_welcome_section_hides_when_state_fails():
    out = _run_harness("fail", "fail")
    assert out["section_display"] == "none", \
        "a failed model-state load hides the section — never a stale connection"
    assert "Model status unknown" in out["side"]
