"""v32 UI contracts — error handling and error showing in simple
words (2026-10-22, Zohaib): "better error handling and error
showing for users in simple words that user can understand."

This pass completes the job the v28 first pass started:

- The translator (friendlyError/friendlyErrorText) is TOTAL:
  every failure class maps to a plain what-happened + what-to-do
  sentence — unreachable server, timeout, sign-in expired,
  not allowed, not found, conflict, bad fields (422), provider
  rate limit, provider down vs AgentQon down (worded
  differently), model gone, API key refused, out of credit, and
  the named server codes (LOCAL_TOKEN_REQUIRED,
  model_not_connected, model_pin_unresolvable, channel
  not-configured). An envelope with a code but no message can
  never render "undefined"; unknown/technical text collapses to
  one plain generic line (raw detail goes to console only).
- api() errors carry their HTTP status (err.status), so the
  translator can speak from the status when the detail is empty
  or a bare "HTTP nnn".
- Errors that need action use sticky error toasts (toastError)
  instead of the 6-second auto-dismiss news toast.
- Every list/load surface renders a real error state with a
  Try again button that reruns its loader — a failed load never
  looks like an empty list (connections, missions, usage,
  sources, models, registry, suitability, worlds, members,
  recent, skills).
- Action buttons disable / guard while in flight — no duplicate
  creates from double-clicks.
- Form validation sits inline under its form in plain words.

Contract tests pin the shipped source; behavioral tests drive
the real page in jsdom (stubbed API) when node + jsdom are
available and skip otherwise — same standard as v24–v31.
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


BANNED = ["undefined", "TypeError", "Failed to fetch", "{", "}",
          "[object", "stack trace", "Traceback"]


def _assert_plain(out: str):
    for b in BANNED:
        assert b not in out, f"banned text {b!r} in shown error: {out!r}"
    assert not re.search(r"HTTP \d{3}", out), \
        f"bare HTTP status in shown error: {out!r}"


# --------------------------------------------------------- contracts
def test_api_errors_carry_their_status():
    html = _html()
    seg = html[html.index("err.code = code;"):][:160]
    assert "err.status = r.status;" in seg, \
        "the translator needs the HTTP status when the detail is empty"


def test_translator_covers_every_class():
    html = _html()
    consts = html[html.index("const ERROR_CODE_LINES"):html.index("function errorStatusLine")]
    fe = (consts + _body(html, "errorStatusLine")
          + _body(html, "friendlyErrorText") + _body(html, "friendlyError"))
    for needle in [
        "Can’t reach the AgentQon server",          # unreachable
        "took too long",                            # timeout/abort
        "sign-in has expired",                      # 401
        "not allowed for your account",             # 403
        "could not be found",                       # 404
        "clashes with what is already saved",       # 409
        "one of the fields is not right",           # 422
        "rate-limiting",                            # 429
        "Wait a minute",
        "model provider is having trouble",         # provider 5xx
        "restarting AgentQon usually fixes",        # AgentQon 5xx
        "did not accept that API key",              # key refused
        "out of credit",                            # quota/credit
        "not available on your connection anymore",  # model gone
        "Something went wrong.",                    # generic fallback
    ]:
        assert needle in fe, f"translator is missing the plain line: {needle}"
    for code in ["LOCAL_TOKEN_REQUIRED", "model_not_connected",
                 "model_pin_unresolvable", "TELEGRAM_NOT_CONFIGURED",
                 "WHATSAPP_NOT_CONFIGURED"]:
        assert code in html and "ERROR_CODE_LINES" in html, \
            f"named code {code} has no plain line"
    lt = _body(html, "looksTechnical")
    assert "undefined" in lt and "TypeError" in lt, \
        "technical text is screened to the generic line, never shown"


def test_no_raw_error_displays_remain():
    html = _html()
    assert html.count("toast(e.message)") == 0, \
        "every error toast routes through toastError/friendlyError"
    assert ".textContent = e.message" not in html, "no raw status fields"
    assert "esc(e.message)" not in html, "no raw escaped dumps"
    assert "'(' + e.message" not in html, "no parenthesised raw errors"
    assert "agentErrorText(r)" not in html and "agentErrorText(created)" not in html, \
        "the raw envelope reader is gone from every call site"


def test_error_toasts_stay_until_dismissed():
    html = _html()
    te = _body(html, "toastError")
    assert "friendlyError(e)" in te and "true" in te, \
        "error toasts are sticky — they wait for the user"
    st = _body(html, "showToast")
    assert "if (!sticky)" in st and "6000" in st, \
        "only non-error toasts auto-dismiss"


def test_every_list_surface_has_a_retry_state():
    html = _html()
    for name, rerun in [
        ("connections-retry", "loadConnections()"),
        ("missions-retry", "refreshLists()"),
        # usage-retry removed with the spend displays (owner hard rule:
        # no money figures anywhere) — the Usage money table it retried
        # no longer exists; the card now holds only the Source library.
        ("sources-retry", "refreshLists()"),
        ("models-retry", "loadModels()"),
        ("registry-retry", "loadRegistry()"),
        ("suitability-retry", "loadSuitability()"),
        ("worlds-retry", "loadWorlds()"),
        ("members-retry", "loadMembers()"),
        ("recent-retry", "renderRecent()"),
        ("skills-retry", "loadSkillsView()"),
    ]:
        assert f'name="{name}"' in html, f"{name} button missing"
        assert rerun in html, f"{name} must rerun {rerun}"
    for prefix in ["Could not load missions",
                   "Could not load sources", "Could not load the model catalog",
                   "Could not load the agent registry", "Could not load worlds",
                   "Could not load your connections"]:
        assert prefix in html, f"plain error prefix missing: {prefix}"


def test_skills_error_state_is_not_the_unavailable_state():
    html = _html()
    lsv = _body(html, "loadSkillsView")
    assert "skillsShowError(frErr)" in lsv, \
        "a network failure is an error state, not 'not available'"
    assert "skillsShowError(responseError(fr))" in lsv, \
        "a non-404 failure is an error state, not 'not available'"
    assert "if (fr.status === 404){ skillsShowUnavailable(); return; }" in lsv, \
        "only a true 404 earns the update wording (v27 pin)"
    assert 'id="skillsError"' in html and 'id="skillsErrorMsg"' in html


def test_in_flight_guards_prevent_duplicates():
    html = _html()
    for guard in ["_connBusy", "_candBusy", "_pinBusy",
                  "_missionActionBusy", "_importBusy", "_briefBusy"]:
        assert guard in html, f"in-flight guard missing: {guard}"
    ca = _body(html, "connAction")
    assert "_connBusy" in ca and "btn.disabled = true" in ca
    pin = _body(html, "toggleSkillPin")
    assert "_pinBusy" in pin, "a second pin click must not undo the first"


def test_form_validation_is_inline_and_plain():
    html = _html()
    assert "Add the connection address first" in html, \
        "the create form names the missing field, in plain words"
    sca = _body(html, "saveCustomAgent")
    assert "Give the agent a name first." in sca
    assert "Add the member’s email address first." in html
    assert "Could not read your saved connections" in _body(html, "detectLocalModels"), \
        "auto-detect stops instead of creating duplicates blind"


def test_show_session_only_drops_the_token_on_a_real_401():
    ss = _body(_html(), "showSession")
    assert "if (e.status === 401) localStorage.removeItem('omnimind_token')" in ss, \
        "a server hiccup must not sign the user out"


def test_boot_watchdog_never_shows_a_raw_script_error():
    html = _html()
    assert "'a script error interrupted startup.'" in html
    assert "interrupted startup: '" not in html, \
        "the raw engine message stays off the recovery panel"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 30, ms = 30) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
const MODE = process.argv[3] || 'translator';
const state = { facetsCalls: 0, connOk: false, postCount: 0 };
function baseRoutes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
  if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
  if (path === '/v1/worlds' && method === 'GET') return { s: 200, j: { worlds: [] } };
  if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
  if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
  if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
  if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
  if (path === '/v1/model-state') return { s: 200, j: { connected: false } };
  if (path === '/v1/models' && method === 'GET') return { s: 200, j: { connections: [], models: [] } };
  if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
  if (path === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
  if (path === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
  return { s: 200, j: {} };
}
function routes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  if (MODE === 'skills500') {
    if (path === '/v1/skills/facets') { state.facetsCalls++; return state.facetsCalls === 1 ? { s: 500, j: {} } : { s: 200, j: { repos: [{ repo: 'demo/repo', count: 1 }], total: 1, unique_names: 1 } }; }
    if (path.startsWith('/v1/skills?')) return { s: 200, j: { skills: [{ id: 'sk1', display_name: 'Demo Skill', description: 'A demo.', repo: 'demo/repo', license: 'MIT', pinned: false }], total: 1 } };
  }
  if (MODE === 'connfail') {
    if (path === '/v1/connections' && method === 'GET') {
      if (!state.connOk) return { s: 500, j: {} };
      return { s: 200, j: { connections: [{ id: 'c9', label: 'Recovered Conn', kind: 'openai_compat', state: 'active', base_url: 'https://api.example.com/v1', masked_key: 'sk-…9', models_count: 2 }] } };
    }
  }
  if (MODE === 'chatdown') {
    if (path === '/v1/chat') return { throw: true };
  }
  if (MODE === 'envelope') {
    if (path === '/v1/models/sync') return { s: 500, j: { error: { code: 'SOME_FUTURE_CODE' } } };
  }
  if (MODE === 'dblclick') {
    if (path === '/v1/connections' && method === 'POST') { state.postCount++; return { s: 200, j: { id: 'c-new', masked_key: 'sk-…1' }, delay: 60 }; }
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
      window.fetch = async (path, opts = {}) => { calls.push({ method: opts.method || 'GET', path });
        const r = routes(path, opts);
        if (r.throw) throw new TypeError('Failed to fetch');
        if (r.delay) await new Promise(res => setTimeout(res, r.delay));
        return { ok: r.s < 300, status: r.s, json: async () => r.j, blob: async () => new window.Blob(['x']) }; };
    } });
  return { window: dom.window, calls };
}
(async () => {
  const out = {};
  const { window, calls } = makeWindow(); const d = window.document;
  await flush();
  if (MODE === 'translator') {
    const E = (message, extra) => Object.assign(new Error(message), extra || {});
    const T = window.friendlyErrorText, F = window.friendlyError;
    out.network = F(new TypeError('Failed to fetch'));
    out.abort = F(Object.assign(new Error('x'), { name: 'AbortError' }));
    out.s401 = F(E('HTTP 401', { status: 401 }));
    out.token = F(E('LOCAL_TOKEN_REQUIRED: HTTP 401', { code: 'LOCAL_TOKEN_REQUIRED', status: 401 }));
    out.s403 = T('HTTP 403');
    out.s404 = T('HTTP 404');
    out.s409 = T('HTTP 409');
    out.s422 = T('HTTP 422');
    out.s429 = T('HTTP 429');
    out.server5xx = F(E('HTTP 500', { status: 500 }));
    out.provider5xx = F(E('provider upstream HTTP 502', { status: 502 }));
    out.modelgone = T('The model "gpt-x" was not found on this server');
    out.keybad = T('Incorrect API key provided');
    out.quota = T('You exceeded your current quota, please check your plan');
    out.envelope = F(E('HTTP 422', { code: 'SOME_FUTURE_CODE', status: 422 }));
    out.technical = F(new Error("TypeError: Cannot read properties of undefined (reading 'x')"));
    out.bareword = T('undefined');
    out.plaindetail = F(new Error('Mission is already completed'));
    out.success = T('Connection active · catalog: 3 models');
    out.nomodel = F(E('model_not_connected: No model is connected', { code: 'model_not_connected' }));
    out.pin = F(E('HTTP 500', { code: 'model_pin_unresolvable', status: 500 }));
  }
  if (MODE === 'skills500') {
    d.getElementById('navSkills').click(); await flush();
    out.err_display = d.getElementById('skillsError').style.display;
    out.err_msg = d.getElementById('skillsErrorMsg').textContent;
    out.notavail = d.getElementById('skillsNotAvail').style.display;
    out.view_text = d.getElementById('skillsView').textContent;
    d.getElementById('skillsRetryBtn').click(); await flush();
    out.err_after = d.getElementById('skillsError').style.display;
    out.rows = d.querySelectorAll('#skillsList .skillrow').length;
    out.totals = d.getElementById('skillsTotals').textContent;
    out.facets_calls = state.facetsCalls;
  }
  if (MODE === 'connfail') {
    out.before = d.getElementById('connections').textContent;
    out.retry_btn = !!d.querySelector('button[name="connections-retry"]');
    state.connOk = true;
    d.querySelector('button[name="connections-retry"]').click(); await flush();
    out.after = d.getElementById('connections').textContent;
  }
  if (MODE === 'chatdown') {
    d.getElementById('chatInput').value = 'hello';
    await window.sendChat(); await flush(10, 30);
    out.thread = d.getElementById('thread').textContent;
    out.retry_btn = !!d.querySelector('#thread button[name="chat-retry"]');
  }
  if (MODE === 'envelope') {
    d.getElementById('syncBtn').click(); await flush(10, 30);
    out.sync = d.getElementById('syncStatus').textContent;
  }
  if (MODE === 'dblclick') {
    d.getElementById('cBase').value = 'https://api.example.com/v1';
    const btn = d.getElementById('connAddBtn');
    btn.click(); btn.click();
    await flush(12, 30);
    out.posts = state.postCount;
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


def test_translator_speaks_plain_words_for_every_class():
    out = _run_harness("translator")
    expect = {
        "network": ["Can’t reach the AgentQon server", "try again"],
        "abort": ["took too long", "try again"],
        "s401": ["sign-in has expired", "Sign in again"],
        "token": ["Close this tab", "agentqon"],
        "s403": ["not allowed for your account"],
        "s404": ["could not be found"],
        "s409": ["clashes with what is already saved"],
        "s422": ["fields is not right"],
        "s429": ["rate-limiting", "Wait a minute"],
        "server5xx": ["AgentQon hit a problem", "restarting AgentQon usually fixes"],
        "provider5xx": ["model provider is having trouble"],
        "modelgone": ["not available on your connection anymore"],
        "keybad": ["did not accept that API key"],
        "quota": ["out of credit"],
        "envelope": ["fields is not right"],
        "technical": ["Something went wrong"],
        "nomodel": ["No AI is connected yet"],
        "pin": ["no longer available on your connections"]  # redesign F10 rewording,
    }
    for key, needles in expect.items():
        for n in needles:
            assert n in out[key], f"{key}: expected {n!r} in {out[key]!r}"
        _assert_plain(out[key])
    assert out["bareword"] != "undefined" and "Something went wrong" in out["bareword"], \
        "the bare word 'undefined' can never be the shown message"
    _assert_plain(out["bareword"])
    assert out["plaindetail"] == "Mission is already completed", \
        "a plain server detail in plain terms is used as-is"
    assert out["success"] == "Connection active · catalog: 3 models", \
        "success/status strings pass through the translator untouched"


def test_skills_load_failure_is_an_error_state_with_a_working_retry():
    out = _run_harness("skills500")
    assert out["err_display"] == "block", "the error state shows"
    assert "AgentQon hit a problem" in out["err_msg"], \
        "in plain words, not an HTTP code"
    _assert_plain(out["err_msg"])
    assert out["notavail"] == "none", \
        "a 500 is NOT the 'update AgentQon' state — that is 404 only"
    assert "not available on this server yet" not in out["view_text"]
    assert out["facets_calls"] >= 2, "Try again reran the loader"
    assert out["err_after"] == "none", "the error clears on success"
    assert out["rows"] == 1, "the retry renders the rows"
    assert "1 skills" in out["totals"]


def test_connections_load_failure_is_not_an_empty_list():
    out = _run_harness("connfail")
    assert "Could not load your connections" in out["before"]
    assert "No connections yet" not in out["before"], \
        "a failed load never poses as an empty list"
    assert out["retry_btn"] is True
    assert "Recovered Conn" in out["after"], "the retry renders the list"


def test_chat_send_with_the_server_down_stays_plain_and_offers_retry():
    out = _run_harness("chatdown")
    assert "Can’t reach the AgentQon server" in out["thread"]
    assert out["retry_btn"] is True, "the in-thread Try again is offered"
    assert "Failed to fetch" not in out["thread"]
    assert "undefined" not in out["thread"]


def test_an_envelope_with_a_code_but_no_message_gets_a_plain_fallback():
    out = _run_harness("envelope")
    assert "AgentQon hit a problem" in out["sync"]
    assert "SOME_FUTURE_CODE" not in out["sync"], "raw codes never show"
    _assert_plain(out["sync"])


def test_a_double_clicked_create_fires_one_post():
    out = _run_harness("dblclick")
    assert out["posts"] == 1, \
        f"the in-flight disable must hold the second click (saw {out['posts']})"
