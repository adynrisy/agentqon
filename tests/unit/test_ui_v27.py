"""v22.1 — the Skill Library browser (contract S4, web half).

A Skills view over the on-device library, built against the FIXED
API contract the backend implements in parallel:
- GET /v1/skills?q=&repo=&limit=&offset= ->
  {skills: [{id, name, display_name, description, repo, license,
             body_bytes, pinned}], total}
- GET /v1/skills/{id} -> {skill: {…, body, url, path, truncated,
  pinned}}
- POST /v1/skills/{id}/pin {pinned: bool} -> {skill_id, pinned}
- GET /v1/skills/facets -> {repos: [{repo, count}], total,
  unique_names}

Design pins:
- A skill is COMMUNITY content: the detail renders the provenance
  block (source repo, path, source URL, license) BEFORE the body,
  and a body is third-party text rendered through renderSkillBody,
  which escapes EVERYTHING before lite formatting — a body with a
  <script> in it renders as inert visible text.
- Search is server-side: debounced input, sequenced responses,
  paged via limit/offset. The browser never fetches the library.
- Until a server offers /v1/skills the calls 404 and the view says
  so in plain language — no fake rows, no crash.

Contract tests pin the shipped source; behavioral tests drive the
real page in jsdom (stubbed API) when node + jsdom are available
and skip otherwise — same standard as test_ui_v24–v26.
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
def test_skills_view_landmarks_and_nav():
    html = _html()
    for landmark in ('id="skillsView"', 'id="skillsTotals"', 'id="skillsQuery"',
                     'id="skillsRepo"', 'id="skillsList"', 'id="skillsStatus"',
                     'id="skillsEmpty"', 'id="skillsNotAvail"', 'id="skillDetail"',
                     'id="skillProv"', 'id="skillBody"', 'id="cardSkillsBrowser"'):
        assert landmark in html, landmark
    # the sidebar's Skills entry sits inside the nav, before
    # Simulations in the Muse restyle's nav order (Chat · Missions ·
    # Agents · Skills · Simulations · Advanced; v36 pins the map)
    assert html.index('id="navSkills"') < html.index('id="navSims"') \
        < html.index('</nav>'), "Skills sits in the sidebar nav"
    # setView owns the view: shown only for 'skills', titled, and
    # opening it loads the library (facets + first search). The
    # per-view top-bar title is GONE by the Muse restyle (spec §3:
    # viewTitle removed) — setView switches views without titling,
    # even though the VIEW_TITLES map definition survives.
    sv = _body(html, "setView")
    assert "$('skillsView').style.display = v === 'skills' ? 'block' : 'none'" in sv
    assert 'id="viewTitle"' not in html, "no view-title label in the new top bar"
    assert "VIEW_TITLES[v]" not in sv, "setView no longer titles the view"
    assert "$('navSkills').classList.toggle('on', v === 'skills')" in sv
    assert "if (v === 'skills') loadSkillsView();" in sv


def test_skills_named_buttons():
    html = _html()
    for name, fid in (("nav-skills", "navSkills"), ("skills-search", "skillsSearchBtn"),
                      ("skills-more", "skillsMoreBtn"),
                      ("skill-pin", "skillPinBtn"), ("skill-back", "skillBackBtn")):
        assert f'name="{name}"' in html, name
        assert f'id="{fid}"' in html, fid
    # rendered rows carry the open + pin buttons, both named
    row = _body(html, "skillRowHtml")
    assert 'name="skill-open"' in row and 'name="skill-pin"' in row
    assert 'data-skill=' in row, "rows key their actions off the skill id"
    # pinned chip is the outline-star SVG + the word Pinned (icon audit:
    # the filled ★ glyph was replaced); the star polygon pins it exactly
    assert 'class="chip pin"' in row and "> Pinned</span>" in row \
        and 'polygon points="12 2 15.09 8.26' in row, \
        "a pinned skill is marked in its row"


# ---------------------------------------------------- API + honesty
def test_skills_api_shape_and_server_side_search():
    html = _html()
    load = _body(html, "loadSkillsView")
    assert "skillsFetch('/v1/skills/facets')" in load, "totals + sources come from /facets"
    assert "unique_names" in load and "skillsTotals" in load, \
        "the header shows the library totals (skills, unique names)"
    search = _body(html, "runSkillsSearch")
    assert "skillsFetch('/v1/skills?' + qs.toString())" in search
    for param in ("qs.set('q'", "qs.set('repo'", "qs.set('limit'", "qs.set('offset'"):
        assert param in search, param
    assert "skillsState.seq" in search, "responses are sequenced — a stale search cannot win"
    assert "SKILLS_PAGE" in html, "results are paged, never the whole library"
    changed = _body(html, "skillsSearchChanged")
    assert "setTimeout" in changed and "250" in changed, "search input is debounced"
    pin = _body(html, "toggleSkillPin")
    assert "'/pin'" in pin and "method: 'POST'" in pin
    assert "JSON.stringify({ pinned: next })" in pin, "the pin body is exactly {pinned: bool}"


def test_skills_honest_states():
    html = _html()
    assert "SKILLS_NOT_AVAILABLE" in html
    assert "not available on this server yet" in html
    assert "404 for /v1/skills" in html, "the message names the endpoint that 404'd"
    load = _body(html, "loadSkillsView")
    assert "fr.status === 404" in load and "skillsShowUnavailable()" in load
    search = _body(html, "runSkillsSearch")
    assert "r.status === 404" in search and "skillsShowUnavailable()" in search, \
        "a 404 mid-search degrades the same honest way"
    assert "No skills match — try broader words" in html, "the empty state helps, plainly"


def test_skill_detail_provenance_first_and_body_escaped():
    html = _html()
    open_fn = _body(html, "openSkill")
    assert "skillsFetch('/v1/skills/' + encodeURIComponent(id))" in open_fn
    # provenance is rendered before the body is
    assert open_fn.index("$('skillProv').innerHTML") < open_fn.index("$('skillBody').innerHTML"), \
        "provenance FIRST, body second — a community skill never looks AgentQon-authored"
    assert "A community skill from" in open_fn
    for field in ("s.repo", "s.path", "s.url", "s.license", "s.truncated"):
        assert field in open_fn, field
    # the body ONLY ever reaches the page through the escaping renderer
    assert "$('skillBody').innerHTML = renderSkillBody(s.body || '')" in open_fn
    render = _body(html, "renderSkillBody")
    assert "esc(String(md" in render, "the renderer escapes the whole body first"
    assert render.index("esc(String(md") < render.index("out.push"), \
        "escaping happens before any tag is generated"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 16, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const FACETS = { repos: [{ repo: 'acme/skills', count: 2 }, { repo: 'beta/tools', count: 1 }], total: 3, unique_names: 3 };
const SK1 = { id: 'sk-code', name: 'code-review', display_name: 'Code Review Pro', description: 'Reviews code for bugs and style', repo: 'acme/skills', license: 'MIT', body_bytes: 2048, pinned: false };
const SK2 = { id: 'sk-seo', name: 'seo-audit', display_name: 'SEO Audit', description: 'Audits pages for search engines', repo: 'beta/tools', license: 'Apache-2.0', body_bytes: 1024, pinned: true };
function makeRoutes(scenario) {
  const state = { pins: {} };
  const routes = (path, opts) => {
    const method = (opts && opts.method) || 'GET';
    if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
    if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
    if (path === '/v1/worlds') return { s: 200, j: { worlds: [] } };
    if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
    if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
    if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
    if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
    if (path === '/v1/model-state') return { s: 200, j: { connected: true, model: 'stub-model', source: 'byok' } };
    if (path === '/v1/models') return { s: 200, j: { connections: [], models: [] } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
    if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
    if (scenario === 'notavail' && path.startsWith('/v1/skills')) return { s: 404, j: { error: { message: 'not found' } } };
    if (path === '/v1/skills/facets' && method === 'GET') return { s: 200, j: FACETS };
    if ((path === '/v1/skills' || path.startsWith('/v1/skills?')) && method === 'GET') {
      if (scenario === 'empty') return { s: 200, j: { skills: [], total: 0 } };
      const u = new URL(path, 'http://localhost');
      const q = u.searchParams.get('q') || ''; const repo = u.searchParams.get('repo') || '';
      let list = [SK1, SK2];
      if (repo) list = list.filter(s => s.repo === repo);
      if (q) list = list.filter(s => (s.display_name + ' ' + s.description).toLowerCase().includes(q.toLowerCase()));
      list = list.map(s => ({ ...s, pinned: state.pins[s.id] !== undefined ? state.pins[s.id] : s.pinned }));
      return { s: 200, j: { skills: list, total: list.length } };
    }
    if (path === '/v1/skills/sk-evil' && method === 'GET') return { s: 200, j: { skill: { id: 'sk-evil', name: 'evil-fixture', display_name: 'Evil Fixture', description: 'A body that tries to inject markup', repo: 'acme/skills', license: 'MIT', body_bytes: 5120, pinned: state.pins['sk-evil'] || false, path: 'skills/evil/SKILL.md', url: 'https://github.com/acme/skills/blob/main/skills/evil/SKILL.md', truncated: true, body: '# Evil Skill\n\nRun <script>alert(1)</script> now.\n\n- first **bold** step\n- second step with `code`\n\n```\nrm -rf / <b>not bold</b>\n```\n' } } };
    const pinM = path.match(/^\/v1\/skills\/([\w-]+)\/pin$/);
    if (pinM && method === 'POST') { const b = JSON.parse(opts.body); state.pins[pinM[1]] = b.pinned; return { s: 200, j: { skill_id: pinM[1], pinned: b.pinned } }; }
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
const skillCalls = (calls) => calls.filter(c => c.path.startsWith('/v1/skills'));
(async () => {
  const out = {};
  const s = process.argv[2];
  const routes = makeRoutes(s); const { window, calls } = makeWindow(routes); const d = window.document;
  await flush();
  if (s === 'browse') {
    window.setView('skills'); await flush();
    out.totals = d.getElementById('skillsTotals').textContent;
    out.rows = d.querySelectorAll('#skillsList .skillrow').length;
    out.list_html = d.getElementById('skillsList').innerHTML;
    out.first_search = skillCalls(calls).map(c => c.path);
    out.repo_options = [...d.getElementById('skillsRepo').options].map(o => o.value);
    // repo filter passes the repo param to the server
    const sel = d.getElementById('skillsRepo');
    sel.value = 'acme/skills'; sel.dispatchEvent(new window.Event('change')); await flush();
    out.after_repo_calls = skillCalls(calls).map(c => c.path);
    out.after_repo_rows = d.querySelectorAll('#skillsList .skillrow').length;
    // back to all sources, then the debounced text search
    sel.value = ''; sel.dispatchEvent(new window.Event('change')); await flush();
    const q = d.getElementById('skillsQuery');
    q.value = 'seo'; q.dispatchEvent(new window.Event('input')); await sleep(450); await flush(4);
    out.after_query_calls = skillCalls(calls).map(c => c.path);
    out.after_query_rows = d.querySelectorAll('#skillsList .skillrow').length;
    out.after_query_html = d.getElementById('skillsList').innerHTML;
  } else if (s === 'detail') {
    window.setView('skills'); await flush();
    await window.openSkill('sk-evil'); await flush();
    const prov = d.getElementById('skillProv'), body = d.getElementById('skillBody');
    out.detail_shown = d.getElementById('skillDetail').style.display === 'block';
    out.browser_hidden = d.getElementById('skillsBrowser').style.display === 'none';
    out.prov_text = prov.textContent;
    out.prov_before_body = (prov.compareDocumentPosition(body) & window.Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    out.body_html = body.innerHTML;
    out.body_script_tags = body.querySelectorAll('script').length;
    out.body_has_h4 = !!body.querySelector('h4');
    out.body_text = body.textContent;
    // pin from the detail: POSTs {pinned:true} and flips the state
    d.getElementById('skillPinBtn').click(); await flush();
    const pin = calls.find(c => c.method === 'POST' && c.path === '/v1/skills/sk-evil/pin');
    out.pin_post = pin ? JSON.parse(pin.body) : null;
    out.pin_btn_text = d.getElementById('skillPinBtn').textContent;
    out.pin_status = d.getElementById('skillPinStatus').textContent;
  } else if (s === 'rowpin') {
    window.setView('skills'); await flush();
    const btn = d.querySelector('#skillsList .skillrow[data-skill="sk-code"] button[name="skill-pin"]');
    out.row_btn_found = !!btn;
    if (btn) { btn.click(); await flush(); }
    const pin = calls.find(c => c.method === 'POST' && c.path === '/v1/skills/sk-code/pin');
    out.pin_post = pin ? JSON.parse(pin.body) : null;
    const row = d.querySelector('#skillsList .skillrow[data-skill="sk-code"]');
    out.row_chip = row ? !!row.querySelector('.chip.pin') : false;
    out.row_btn_text = row ? row.querySelector('button[name="skill-pin"]').textContent : '';
    out.detail_opened = d.getElementById('skillDetail').style.display === 'block';
  } else if (s === 'notavail') {
    window.setView('skills'); await flush();
    out.notavail_shown = d.getElementById('skillsNotAvail').style.display === 'block';
    out.notavail_text = d.getElementById('skillsNotAvail').textContent;
    out.browser_hidden = d.getElementById('skillsBrowser').style.display === 'none';
    out.rows = d.querySelectorAll('#skillsList .skillrow').length;
  } else if (s === 'empty') {
    window.setView('skills'); await flush();
    out.empty_shown = d.getElementById('skillsEmpty').style.display === 'block';
    out.empty_text = d.getElementById('skillsEmpty').textContent;
    out.rows = d.querySelectorAll('#skillsList .skillrow').length;
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


def test_browse_search_and_repo_filter_are_server_side():
    out = _run_harness("browse")
    assert "3 skills" in out["totals"] and "3 unique names" in out["totals"], \
        "the header shows the /facets totals"
    assert out["rows"] == 2, "the first search renders the stub's rows"
    assert "Code Review Pro" in out["list_html"]
    # pinned chip is the outline-star SVG + the word Pinned (icon audit:
    # the filled ★ glyph was replaced); the star polygon pins it exactly
    assert 'class="chip pin"' in out["list_html"] \
        and "> Pinned</span>" in out["list_html"] \
        and 'polygon points="12 2 15.09 8.26' in out["list_html"], \
        "the pinned skill carries its chip"
    first = [p for p in out["first_search"] if p.startswith("/v1/skills?")]
    assert first and "limit=50" in first[0] and "offset=0" in first[0], \
        "search is paged server-side from the first call"
    queried = [p for p in out["after_query_calls"] if "q=seo" in p]
    assert queried, "typing in the search box queries the server (debounced)"
    assert out["after_query_rows"] == 1 and "SEO Audit" in out["after_query_html"]
    filtered = [p for p in out["after_repo_calls"] if "repo=acme" in p]
    assert filtered, "the source filter passes the repo param to the server"
    assert out["after_repo_rows"] == 1


def test_detail_provenance_first_body_inert_and_pin():
    out = _run_harness("detail")
    assert out["detail_shown"] is True and out["browser_hidden"] is True
    assert out["prov_before_body"] is True, "provenance precedes the body in the page"
    for needle in ("acme/skills", "skills/evil/SKILL.md", "MIT", "Truncated",
                   "github.com/acme/skills"):
        assert needle in out["prov_text"], needle
    # the malicious body rendered as inert text — no live script element,
    # the tag visible as escaped text, the markdown structure intact
    assert out["body_script_tags"] == 0, "a <script> in a skill body must never become an element"
    assert "&lt;script&gt;" in out["body_html"]
    assert "alert(1)" in out["body_text"], "the content is still readable as text"
    assert out["body_has_h4"] is True, "the body's heading renders as a heading"
    # pin: POSTs exactly {pinned: true} and flips the detail state
    assert out["pin_post"] == {"pinned": True}
    assert out["pin_btn_text"] == "Unpin skill"
    assert "offered first" in out["pin_status"]


def test_row_pin_toggle_updates_in_place():
    out = _run_harness("rowpin")
    assert out["row_btn_found"] is True
    assert out["pin_post"] == {"pinned": True}
    assert out["row_chip"] is True, "the row gains its pinned chip without a reload"
    assert out["row_btn_text"] == "Unpin"
    assert out["detail_opened"] is False, "pinning a row does not open the detail"


def test_skills_404_shows_the_honest_message():
    out = _run_harness("notavail")
    assert out["notavail_shown"] is True
    assert "not available on this server yet" in out["notavail_text"]
    assert out["browser_hidden"] is True
    assert out["rows"] == 0, "no fake rows are invented"


def test_empty_search_result_says_so_plainly():
    out = _run_harness("empty")
    assert out["empty_shown"] is True
    assert "No skills match" in out["empty_text"]
    assert out["rows"] == 0
