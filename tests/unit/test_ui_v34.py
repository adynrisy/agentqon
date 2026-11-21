"""v34 UI contracts — repair pass for the 2026-10-22 web-UI audit
(AUDIT_UI.md): 3 Mediums + 5 Lows, each fixed and pinned here.

M1 — the v32 translator let raw Python exception text through
verbatim ("KeyError: 'tenant_id'" and friends): looksTechnical now
screens the exception SHAPE (a CamelCase class ending in
Error/Exception, dotted module paths included), the suffix-less
Python exceptions, and traceback furniture (File "….py", line N).
Screened text collapses to the one plain generic line and the raw
detail goes to the console only. The auditor's five live repro
inputs are pinned in the behavioral battery below.

M2 — the Advanced "Download proof pack" button downloaded the
brief: it now calls chatProofDownload (the real proof pack), and
the chat thread keeps its own honestly labeled "Download brief".

M3 — gate reliability: owned by test_ui_v33.py itself (its jsdom
scenarios now wait on observable conditions, not fixed sleeps).

L1 — the kill-switch button asserted OFF before any server read
and derived the toggle from its own label: the label starts
unknown (—), state lives in _killOn, written only by server
responses, and a toggle with no read yet reads first.
L2 — esc() did not escape single quotes and ids were interpolated
raw into single-quoted inline-handler strings: esc() now escapes
', and every handler argument goes through jsq() — because the
browser decodes &#39; back to ' inside an attribute value, esc()
alone provably cannot hold a JS string in a handler (the breakout
is demonstrated in the xss scenario below); jsq escapes for both
the JS string and the HTML attribute.
L3 — chatSimulate died on rows[0] when a world produced no branch
results: it now throws its own plain diagnosis instead.
L4 — roster counts were baked into UI copy: the counts are gone
from the markup and the registry render writes the LIVE total
into the folded card's summary line.
L5 — a failed model self-test surfaced twice (inline + sticky
toast): the inline block is the one surface.

Two older assertions were retargeted by this pass, each with the
reason recorded at the assertion: test_ui_v22's export pin (now
chatProofDownload — M2) and its roster-copy pin (counts are live
data now — L4).

Contract tests pin the shipped source; behavioral tests drive the
real page in jsdom (stubbed API) when node + jsdom are available
and skip otherwise — same standard as v24–v33. Behavioral waits
are condition-based (waitFor), never fixed sleeps.
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

GENERIC = "Something went wrong. Try again — if it keeps happening, restart AgentQon."


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


# ------------------------------------------------------- M1 contracts
def test_technical_screen_catches_the_exception_shape():
    lt = _body(_html(), "looksTechnical")
    assert "Error|Exception" in lt, \
        "the screen must match the exception-class shape, not a word list"
    assert "StopIteration" in lt and "KeyboardInterrupt" in lt, \
        "the suffix-less Python exceptions are screened too"
    assert ".py" in lt, "traceback file/line furniture is screened"
    fet = _body(_html(), "friendlyErrorText")
    assert "looksTechnical(m)" in fet and "ERROR_GENERIC" in fet
    assert "console.error" in fet, \
        "screened detail goes to the console only, never the screen"


def test_reason_feeds_route_through_the_translator():
    html = _html()
    # the MissionFailed and AgentFailed reason strings — M1's other
    # two feed paths — pass through friendlyErrorText (and so
    # through the strengthened screen)
    assert "esc(friendlyErrorText(reason))" in html


# ------------------------------------------------------- M2 contracts
def test_proof_pack_button_downloads_the_proof_pack():
    html = _html()
    handler = re.search(r"\$\('exportBtn'\)\.onclick = [^;]+;", html)
    assert handler and "chatProofDownload" in handler.group(0), \
        "the button labeled 'Download proof pack' must fetch the proof pack"
    assert "chatBrief" not in handler.group(0)
    assert 'id="exportBtn">Download proof pack<' in html, \
        "the label stays — the wiring was the lie, not the words"
    dl = _body(html, "chatProofDownload")
    assert "/proof-pack" in dl and "agentqon-proof-pack-" in dl
    # the distinction: the brief keeps its own honest button
    assert "Download brief" in html and "chatBrief(" in html


# ------------------------------------------------------- L1 contracts
def test_kill_switch_never_asserts_a_state_it_has_not_read():
    html = _html()
    assert 'id="killBtn">Kill switch: —<' in html, \
        "the static label is unknown (—), never a guessed OFF"
    kill = html[html.index("$('killBtn').onclick"):][:900]
    assert "textContent.includes" not in kill, \
        "the toggle must not derive state from its own label text"
    assert "_killOn === null" in kill and "loadAdminOverview()" in kill, \
        "a toggle with no server read yet reads first, then flips _killOn"
    seg = html[html.index("let _killOn = null;"):html.index("$('killBtn').onclick")]
    assert "loadAdminOverview" in seg and "renderAdminOverview" in seg, \
        "one server-read path writes both the state and the label"
    body = _body(html, "loadAdminOverview") if "async function loadAdminOverview" in html else ""
    assert "/v1/admin/overview" in body


# ------------------------------------------------------- L2 contracts
def test_esc_escapes_single_quotes_and_handlers_use_jsq():
    html = _html()
    # esc is a one-liner whose regex literal defeats the family's
    # brace matcher — read its line directly
    i = html.index("function esc(s)")
    esc = html[i:html.index("\n", i)]
    assert "&#39;" in esc and "[&<>\"']" in esc, \
        "esc() escapes single quotes along with the other four"
    assert "function jsq(s)" in html, \
        "handler arguments go through jsq (JS-string + attribute escaping)"
    # the sweep: every single-quoted ${…} interpolation left in the
    # file is a jsq(…) call — no raw or esc-only handler arguments
    for m in re.finditer(r"'\$\{([^}]+)\}'", html):
        assert m.group(1).startswith("jsq("), \
            f"unwrapped handler interpolation: {m.group(0)}"


# ------------------------------------------------------- L3 contracts
def test_simulation_guards_the_empty_compare():
    body = _body(_html(), "chatSimulate")
    assert "if (!rows.length) throw" in body, \
        "an empty compare gets its own diagnosis, not a rows[0] crash"
    assert "no branch results to compare" in body


# ------------------------------------------------------- L4 contracts
def test_roster_counts_are_live_data_not_copy():
    html = _html()
    for baked in ["6,799", "6,516", "280-agent"]:
        assert baked not in html, f"baked roster count in copy: {baked}"
    render = _body(html, "renderAgents")
    assert "agents on the roster" in render, \
        "the registry render states the live total"
    assert ".fold-meta" in render and "dataset.foldMeta" in render, \
        "…and writes it into the folded card's summary line"


# ------------------------------------------------------- L5 contracts
def test_self_test_failure_surfaces_once():
    body = _body(_html(), "runModelSelfTest")
    assert "selfTestShowError(e)" in body, "the inline state is the surface"
    assert "toastError(e)" not in body, \
        "no duplicate sticky toast for the same failure (audit L5)"


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


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const MODE = process.argv[3] || 'm1';
const state = { kill: 'on', adminCalls: [], proofCalls: [], exportCalls: 0 };
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
  if (path.startsWith('/v1/admin/')) {
    state.adminCalls.push(method + ' ' + path);
    if (path === '/v1/admin/overview') return { s: 200, j: { kill_switch: state.kill, jobs_by_status: {}, dead_jobs: 0, backend: 'x', spend_by_tenant: [], connections_by_state: {} } };
    if (path === '/v1/admin/kill-switch') { const b = JSON.parse(opts.body); state.kill = b.on ? 'on' : 'off'; state.lastKillBody = opts.body; return { s: 200, j: { kill_switch: state.kill } }; }
  }
  if (MODE === 'proof') {
    if (path === '/v1/missions/m1' && method === 'GET') return { s: 200, j: { mission: { id: 'm1', title: 'Proof Mission', status: 'completed', spent: 0 }, tasks: [], candidates: [], evaluations: [], critiques: [], events: [], route_decisions: [] } };
    if (path === '/v1/missions/m1/proof-pack') { state.proofCalls.push(path); return { s: 200, j: { confidence_summary: {}, candidates: [], sources: [], critiques: [], markdown: '# PROOF PACK m1' } }; }
    if (path === '/v1/missions/m1/export') { state.exportCalls++; return { s: 200, j: {} }; }
  }
  if (MODE === 'sim') {
    if (path === '/v1/missions/m9' && method === 'GET') return { s: 200, j: { mission: { id: 'm9', title: 'Sim Mission', status: 'completed', spent: 0 }, candidates: [] } };
    if (path === '/v1/worlds' && method === 'POST') return { s: 200, j: { world_id: 'w1' } };
    if (path === '/v1/worlds/w1' && method === 'GET') return { s: 200, j: { world_id: 'w1', provenance: {} } };
    if (path === '/v1/worlds/w1/run') return { s: 200, j: {} };
    if (path === '/v1/worlds/w1/compare') return { s: 200, j: { rows: [] } };
  }
  if (MODE === 'selftest') {
    if (path === '/v1/models/self-test' && method === 'POST') return { throw: true };
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
      window.fetch = async (path, opts = {}) => { calls.push({ method: opts.method || 'GET', path });
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
  if (MODE === 'm1') {
    await waitFor(() => typeof window.friendlyErrorText === 'function');
    const T = window.friendlyErrorText, F = window.friendlyError;
    out.key = T("KeyError: 'tenant_id'");
    out.value = T("ValueError: invalid literal for int() with base 10: 'x'");
    out.runtime = T("RuntimeError: worker exploded unexpectedly");
    out.attr = T("AttributeError: 'NoneType' object has no attribute 'get'");
    out.conn = T("ConnectionError: HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded");
    out.errpath = F(new Error("KeyError: 'tenant_id'"));
    out.dotted = T("sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: x");
    out.trace = T('Traceback (most recent call last): File "app/worker.py", line 42, in run');
    out.stopit = T("StopIteration");
    out.fileline = T('worker died: File "jobs.py", line 7, in <module>');
    out.plain = T("Mission is already completed");
    out.success = T("Connection active · catalog: 3 models");
    out.http500 = T("HTTP 500");
    out.modelgone = T('The model "gpt-x" was not found on this server');
    out.prose = T("The provider returned an error page, please retry");
    out.keybad = T("Incorrect API key provided");
  }
  if (MODE === 'proof') {
    await waitFor(() => typeof window.openMission === 'function');
    await window.openMission('m1');
    out.card_shown = d.getElementById('missionCard').style.display;
    d.getElementById('exportBtn').click();
    await waitFor(() => !!window.__dl);
    out.download = window.__dl ? window.__dl.download : null;
    out.proof_calls = state.proofCalls.length;
    out.export_calls = state.exportCalls;
  }
  if (MODE === 'kill') {
    await waitFor(() => !!d.getElementById('killBtn'));
    out.label_pre = d.getElementById('killBtn').textContent;
    d.getElementById('adminLoadBtn').click();
    await waitFor(() => d.getElementById('killBtn').textContent === 'Kill switch: ON');
    out.label_loaded = d.getElementById('killBtn').textContent;
    d.getElementById('killBtn').click();
    await waitFor(() => d.getElementById('killBtn').textContent === 'Kill switch: OFF');
    out.label_toggled = d.getElementById('killBtn').textContent;
    out.body_after_load = state.lastKillBody;
    // second window: toggle with NO prior read — server says ON, so
    // the toggle must read first and then send {on:false}
    state.kill = 'on'; state.adminCalls = []; state.lastKillBody = null;
    const w2 = makeWindow(); const d2 = w2.window.document;
    await waitFor(() => !!d2.getElementById('killBtn'));
    d2.getElementById('killBtn').click();
    await waitFor(() => state.lastKillBody !== null);
    out.cold_first_call = state.adminCalls[0];
    out.cold_body = state.lastKillBody;
    out.cold_label = d2.getElementById('killBtn').textContent;
  }
  if (MODE === 'xss') {
    await waitFor(() => typeof window.renderAgents === 'function');
    const PAYLOAD = "x');window.__pwn=true;//";
    window._agentRec = {};
    window._agentsAll = [{ agent_id: 'evil', name: 'Evil', title: 't', version: '1', cost_per_run_usd: 0, latency_ms: 1, capabilities: ['general'], quality: 3, lifecycle: 'active', agent_version_id: PAYLOAD, origin: 'library' }];
    window.renderAgents();
    window.__lifeArgs = null;
    window.setLife = function (...a) { window.__lifeArgs = a; };
    const btn = d.querySelector('#agents button[name="agent-quarantine"]');
    out.btn_found = !!btn;
    if (btn) btn.click();
    out.pwn = window.__pwn === true;
    out.life_args = window.__lifeArgs;
    // the distinction that justifies jsq: esc() alone in this exact
    // context BREAKS OUT (the browser decodes &#39; back to '),
    // jsq delivers the value verbatim and inert
    const scratch = d.createElement('div'); d.body.appendChild(scratch);
    window.__capVal = undefined;
    window.__cap = function (v) { window.__capVal = v; };
    window.__pwn = false;
    scratch.innerHTML = '<button id="pb1" onclick="window.__cap(\'' + window.esc(PAYLOAD) + '\')">x</button>';
    d.getElementById('pb1').click();
    out.esc_pwn = window.__pwn === true;
    window.__capVal = undefined; window.__pwn = false;
    scratch.innerHTML = '<button id="pb2" onclick="window.__cap(\'' + window.jsq(PAYLOAD) + '\')">x</button>';
    d.getElementById('pb2').click();
    out.jsq_pwn = window.__pwn === true;
    out.jsq_cap = window.__capVal;
    out.payload = PAYLOAD;
  }
  if (MODE === 'sim') {
    await waitFor(() => typeof window.chatSimulate === 'function');
    await window.chatSimulate('m9');
    await waitFor(() => d.getElementById('thread').textContent.includes('Simulation could not run'));
    out.thread = d.getElementById('thread').textContent;
  }
  if (MODE === 'selftest') {
    await waitFor(() => !!d.getElementById('selfTestBtn'));
    d.getElementById('selfTestBtn').click();
    await waitFor(() => d.getElementById('selfTestError').style.display === 'block');
    await new Promise(r => setTimeout(r, 400)); // a duplicate toast would have fired by now
    out.err_msg = d.getElementById('selfTestErrorMsg').textContent;
    out.toast_display = d.getElementById('toast').style.display;
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


# --------------------------------------------------------- behavioral
def test_m1_auditor_repro_inputs_all_collapse_to_the_generic_line():
    out = _run_harness("m1")
    for key in ["key", "value", "runtime", "attr", "conn", "errpath",
                "dotted", "trace", "stopit", "fileline"]:
        assert out[key] == GENERIC, \
            f"{key}: raw exception text reached the screen: {out[key]!r}"
        _assert_plain(out[key])


def test_m1_plain_language_still_passes_and_classes_still_map():
    out = _run_harness("m1")
    assert out["plain"] == "Mission is already completed", \
        "a plain server detail in plain terms is used as-is"
    assert out["success"] == "Connection active · catalog: 3 models", \
        "success/status strings pass through untouched"
    assert out["prose"] == "The provider returned an error page, please retry", \
        "lowercase prose about an error is plain English, not an exception"
    assert "AgentQon hit a problem" in out["http500"], \
        "a bare HTTP status still maps to its own plain line, not the generic"
    assert "not available on your connection anymore" in out["modelgone"]
    assert "did not accept that API key" in out["keybad"]


def test_m2_the_button_really_downloads_the_proof_pack():
    out = _run_harness("proof")
    assert out["card_shown"] == "block"
    assert out["proof_calls"] == 1, "the proof pack endpoint was fetched"
    assert out["export_calls"] == 0, "the brief endpoint was NOT fetched"
    assert out["download"] == "agentqon-proof-pack-m1.md", \
        "the saved file is the proof pack, under its own name"


def test_l1_the_label_speaks_only_from_server_reads():
    out = _run_harness("kill")
    assert out["label_pre"] == "Kill switch: —", \
        "before any read the button asserts nothing"
    assert out["label_loaded"] == "Kill switch: ON", \
        "the server said ON — the old UI claimed OFF here"
    assert out["label_toggled"] == "Kill switch: OFF"
    assert out["body_after_load"] == '{"on":false}'
    assert out["cold_first_call"] == "GET /v1/admin/overview", \
        "a toggle with no read yet reads the true state first"
    assert out["cold_body"] == '{"on":false}', \
        "…and therefore turns an ON switch off, not on again"
    assert out["cold_label"] == "Kill switch: OFF"


def test_l2_a_quote_bearing_id_cannot_break_out_of_its_handler():
    out = _run_harness("xss")
    assert out["btn_found"] is True
    assert out["pwn"] is False, "the hostile id executed as script"
    assert out["life_args"] == [out["payload"], "quarantined"], \
        "the handler received the id verbatim, as data"
    assert out["esc_pwn"] is True, \
        "documented: esc() alone breaks out in this context — why jsq exists"
    assert out["jsq_pwn"] is False
    assert out["jsq_cap"] == out["payload"]


def test_l3_an_empty_world_says_what_actually_happened():
    out = _run_harness("sim")
    assert "Simulation could not run" in out["thread"]
    assert "no branch results to compare" in out["thread"], \
        "the honest diagnosis, not the generic line"
    assert GENERIC not in out["thread"]
    assert "TypeError" not in out["thread"]


def test_l5_a_failed_self_test_shows_one_surface_not_two():
    out = _run_harness("selftest")
    assert "Can’t reach the AgentQon server" in out["err_msg"]
    assert out["toast_display"] != "block", \
        "no duplicate sticky toast over the inline error"
