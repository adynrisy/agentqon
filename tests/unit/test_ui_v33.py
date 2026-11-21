"""v33 UI contracts — the Model self-test ("Test all", 2026-10-22,
Zohaib): in the Models section, the harness checks every connected
model and tells the user which models are working and which are not.

The backend half (app/modeltest.py) probes each model with one real
1-token completion and speaks in error codes:

    POST /v1/models/self-test            -> {run_id, total}
    GET  /v1/models/self-test/{run_id}   -> {run_id, state, total,
        done_count, capped, cap_note, results: [{connection_id,
        connection_label, model, ok, latency_ms, error_code}]}

This pass is the surface: a "Model self-test" block inside the
Model catalog card (the Models section) with a named Test all
button, a plain-words progress line, a "X of Y models working"
summary, results grouped by connection, WORKING stamps with the
answer time, and NOT WORKING stamps with the reason in simple
words — the seven backend codes translated here, next to the
surface. A run with nothing to test is a plain state, never an
error. Start/poll failures ride the v32 machinery (friendlyError,
inline state + Try again, quiet poll retries first).

Contract tests pin the shipped source; behavioral tests drive the
real page in jsdom (stubbed API) when node + jsdom are available
and skip otherwise — same standard as v24–v32.

M3 REPAIR (audit 2026-10-22): in the audit's full-family run all
four behavioral scenarios failed while the file passed standalone
minutes later. Root cause, determined in the repair loop: NOT a
product defect and NOT order dependence (the scenarios share no
state — a fresh node process and JSDOM window each — and the
v31+v32+v33 sequence is green under load). It was load flakiness
in THIS harness: three fixed wall-clock budgets that transient
multi-process starvation attacks directly — a 900 ms boot flush,
a progress line read at a fixed 600 ms (it only exists after the
POST + first poll complete: a pure wall-clock race with zero
slack), and 12 s waitFor caps on a run that inherently needs the
product's real 2 s poll cycle. Verified green standalone, green
in the full family under deliberate CPU load, and green 3×
consecutively under load — yet the budgets were still the wrong
shape. The harness now waits ONLY on observable conditions (the
progress text itself, the summary text, element state) with
generous caps: product and harness timers share one event loop,
so starvation stretches both together and the conditions are
observed regardless of load. Every assertion about product
behavior below is unchanged, verbatim; only the waiting changed.
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


REASONS = {
    "auth_failed": "The provider did not accept the connection’s key.",
    "model_not_found": "This model was not found at the provider.",
    "rate_limited": "The provider is busy right now (rate limit) — test again later.",
    "timeout": "The model took too long to answer.",
    "provider_down": "The provider is having trouble right now.",
    "unreachable": "Could not reach the provider — check its address and that it is running.",
    "unknown": "Something went wrong while testing this model.",
}

NO_MODELS_LINE = "There are no models to test yet — connect a model first."


# --------------------------------------------------------- contracts
def test_self_test_block_lives_inside_the_model_catalog_card():
    html = _html()
    card = html[html.index('id="cardModels"'):html.index('id="cardSimulations"')]
    assert "Model self-test" in card, "the block belongs to the Models section"
    assert "Check every model on your connections and see which ones answer." in card, \
        "one short plain explainer line"
    assert 'id="selfTestBtn"' in card and 'name="models-test-all"' in card, \
        "the named Test all button (named-button discipline)"
    assert ">Test all<" in card
    for el in ["selfTestStatus", "selfTestSummary", "selfTestNote",
               "selfTestResults", "selfTestError", "selfTestErrorMsg"]:
        assert f'id="{el}"' in card, f"self-test element missing: {el}"
    assert 'name="models-test-retry"' in card, "the inline Try again is named"
    assert 'onclick="runModelSelfTest()"' in card, "Try again reruns the test"


def test_the_seven_plain_reasons_are_pinned():
    html = _html()
    table = html[html.index("const SELF_TEST_REASONS"):html.index("};", html.index("const SELF_TEST_REASONS"))]
    for code, line in REASONS.items():
        assert f"{code}: '{line}'" in table, \
            f"error code {code} has no pinned plain reason"
    body = _body(html, "renderSelfTestResults")
    assert "SELF_TEST_REASONS[r.error_code] || SELF_TEST_REASONS.unknown" in body, \
        "an unmapped code falls back to the plain 'unknown' reason, never raw"


def test_run_flow_progress_summary_and_empty_wording():
    body = _body(_html(), "runModelSelfTest")
    assert "await api('/v1/models/self-test', {method:'POST'})" in body, \
        "Test all starts a run"
    assert "'/v1/models/self-test/' + encodeURIComponent(start.run_id)" in body, \
        "the run is polled by id"
    assert "'Testing models… ' + d.done_count + ' of ' + d.total + ' done'" in body, \
        "progress speaks in plain words with real counts"
    assert "working + ' of ' + d.total + ' models working'" in body, \
        "the summary format: 'X of Y models working'"
    assert NO_MODELS_LINE in body, "zero models is a plain line, not an error"
    assert "(d.capped && d.cap_note) ? d.cap_note : ''" in body, \
        "the backend's cap note is shown plainly under the summary"
    assert "fails >= 3" in body and "throw e" in body, \
        "a poll hiccup retries quietly a couple of times before surfacing"


def test_button_disables_while_running_and_the_guard_holds():
    html = _html()
    body = _body(html, "runModelSelfTest")
    assert "_selfTestBusy" in body, "a re-entry guard backs the disabled button"
    assert "if (_selfTestBusy) return;" in body
    assert "btn.disabled = true" in body and "btn.disabled = false" in body, \
        "the button disables in flight and comes back"
    assert "$('selfTestBtn').onclick = runModelSelfTest;" in html


def test_failures_surface_in_plain_words_inline():
    html = _html()
    body = _body(html, "runModelSelfTest")
    assert "selfTestShowError(e)" in body, "failures land in the inline state"
    show = _body(html, "selfTestShowError")
    assert "friendlyError(e)" in show, "the inline state speaks friendlyError"
    assert "msg.textContent" in show, "plain text only — never innerHTML raw"


def test_results_render_grouped_with_stamps():
    body = _body(_html(), "renderSelfTestResults")
    assert "connection_label" in body, "results group by connection"
    assert '<span class="stamp ok">Working</span>' in body
    assert '<span class="stamp bad">Not working</span>' in body
    assert "selfTestLatency(r.latency_ms)" in body, \
        "a working model shows how fast it answered"
    lat = _body(_html(), "selfTestLatency")
    assert "answered in " in lat


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
const MODE = process.argv[3] || 'full';
const state = { posts: 0, gets: 0, startOk: false };
const R = (cid, label, model, ok, latency_ms, error_code) =>
  ({ connection_id: cid, connection_label: label, model, ok, latency_ms, error_code });
const ALL = [
  R('ca', 'Provider A', 'model-alpha', true, 420, null),
  R('ca', 'Provider A', 'model-beta', true, 2300, null),
  R('ca', 'Provider A', 'model-gamma', false, 300, 'auth_failed'),
  R('ca', 'Provider A', 'model-delta', false, 800, 'rate_limited'),
  R('cb', 'Provider B', 'model-epsilon', false, 150, 'model_not_found'),
  R('cb', 'Provider B', 'model-zeta', false, 20000, 'timeout'),
  R('cb', 'Provider B', 'model-eta', false, 500, 'provider_down'),
  R('cb', 'Provider B', 'model-theta', false, 90, 'unreachable'),
  R('cb', 'Provider B', 'model-iota', false, 60, 'unknown'),
];
const CAP_NOTE = 'Only the first 40 models per connection were tested';
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
  if (MODE === 'full') {
    if (path === '/v1/models/self-test' && method === 'POST') { state.posts++; return { s: 200, j: { run_id: 'mtest_a', total: 9 } }; }
    if (path === '/v1/models/self-test/mtest_a') { state.gets++;
      if (state.gets === 1) return { s: 200, j: { run_id: 'mtest_a', state: 'running', total: 9, done_count: 4, capped: true, cap_note: CAP_NOTE, results: ALL.slice(0, 4) } };
      return { s: 200, j: { run_id: 'mtest_a', state: 'done', total: 9, done_count: 9, capped: true, cap_note: CAP_NOTE, results: ALL } };
    }
  }
  if (MODE === 'zero') {
    if (path === '/v1/models/self-test' && method === 'POST') { state.posts++; return { s: 200, j: { run_id: 'mtest_z', total: 0 } }; }
  }
  if (MODE === 'startfail') {
    if (path === '/v1/models/self-test' && method === 'POST') { state.posts++; if (!state.startOk) return { throw: true }; return { s: 200, j: { run_id: 'mtest_r', total: 0 } }; }
  }
  if (MODE === 'dblclick') {
    if (path === '/v1/models/self-test' && method === 'POST') { state.posts++; return { s: 200, j: { run_id: 'mtest_d', total: 1 }, delay: 80 }; }
    if (path === '/v1/models/self-test/mtest_d') return { s: 200, j: { run_id: 'mtest_d', state: 'done', total: 1, done_count: 1, results: [R('ca', 'Provider A', 'model-solo', true, 100, null)] } };
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
  /* The boot watchdog (6 s) is not what these scenarios test; a
   * starved boot under load must not flip the recovery gate
   * mid-scenario. Disarm it — determinism (audit M3). */
  try { dom.window.clearTimeout(dom.window.__agentqonBootTimer); } catch (e) {}
  return { window: dom.window, calls };
}
const waitFor = async (fn, timeout = 60000) => { const t0 = Date.now(); while (Date.now() - t0 < timeout) { if (fn()) return true; await new Promise(r => setTimeout(r, 50)); } return fn(); };
(async () => {
  const out = {};
  const { window } = makeWindow(); const d = window.document;
  /* Boot is an observable condition, not a fixed sleep (audit M3):
   * wait until the page has actually wired the self-test button. */
  await waitFor(() => typeof window.runModelSelfTest === 'function' && !!d.getElementById('selfTestBtn'));
  const btn = d.getElementById('selfTestBtn');
  const summary = d.getElementById('selfTestSummary');
  if (MODE === 'full') {
    btn.click();
    /* The progress line is observed, never raced (audit M3): it
     * exists only after the POST + first poll land, and it then
     * persists for the product's full 2 s poll interval on this
     * same event loop — waiting FOR the text is deterministic at
     * any load, reading at a fixed 600 ms was not. */
    await waitFor(() => d.getElementById('selfTestStatus').textContent.startsWith('Testing models…'));
    out.progress = d.getElementById('selfTestStatus').textContent;
    await waitFor(() => summary.textContent.includes('models working'));
    out.summary = summary.textContent;
    out.note = d.getElementById('selfTestNote').textContent;
    out.status_after = d.getElementById('selfTestStatus').textContent;
    out.results = d.getElementById('selfTestResults').textContent;
    out.ok_stamps = d.querySelectorAll('#selfTestResults .stamp.ok').length;
    out.bad_stamps = d.querySelectorAll('#selfTestResults .stamp.bad').length;
    out.btn_disabled_end = btn.disabled;
    out.posts = state.posts; out.gets = state.gets;
  }
  if (MODE === 'zero') {
    btn.click();
    await waitFor(() => summary.textContent.length > 0);
    out.summary = summary.textContent;
    out.err_display = d.getElementById('selfTestError').style.display;
    out.stamps = d.querySelectorAll('#selfTestResults .stamp').length;
    out.btn_disabled_end = btn.disabled;
    out.posts = state.posts;
  }
  if (MODE === 'startfail') {
    btn.click();
    await waitFor(() => d.getElementById('selfTestError').style.display === 'block');
    out.err_display = d.getElementById('selfTestError').style.display;
    out.err_msg = d.getElementById('selfTestErrorMsg').textContent;
    out.retry_btn = !!d.querySelector('button[name="models-test-retry"]');
    state.startOk = true;
    d.querySelector('button[name="models-test-retry"]').click();
    await waitFor(() => summary.textContent.length > 0);
    out.err_after = d.getElementById('selfTestError').style.display;
    out.summary_after = summary.textContent;
    out.posts = state.posts;
  }
  if (MODE === 'dblclick') {
    btn.click();
    out.disabled_during = btn.disabled;
    btn.click();
    await waitFor(() => summary.textContent.includes('models working'));
    out.posts = state.posts;
    out.summary = summary.textContent;
    out.disabled_end = btn.disabled;
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
                              text=True, env=env, timeout=300)
        assert proc.returncode == 0, \
            f"harness {mode} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_full_run_groups_results_and_counts_the_working():
    out = _run_harness("full")
    assert out["progress"] == "Testing models… 4 of 9 done", \
        f"the poll speaks progress in plain words: {out['progress']!r}"
    assert out["summary"] == "2 of 9 models working"
    assert out["note"] == "Only the first 40 models per connection were tested", \
        "the cap note shows plainly under the summary"
    assert out["status_after"] == "", "the progress line clears when the run ends"
    assert out["ok_stamps"] == 2 and out["bad_stamps"] == 7
    res = out["results"]
    for label in ["Provider A", "Provider B"]:
        assert label in res, f"results are grouped by connection: {label}"
    for model in ["model-alpha", "model-beta", "model-gamma", "model-delta",
                  "model-epsilon", "model-zeta", "model-eta",
                  "model-theta", "model-iota"]:
        assert model in res, f"model row missing: {model}"
    assert "answered in under a second" in res, "a fast answer shows its time in plain words"
    assert "answered in about 2 seconds" in res, "a slow answer shows its time in plain words"
    for code, line in REASONS.items():
        if code == "model_not_found":
            continue  # asserted below with the rest; all seven ride this run
        assert line in res, f"reason for {code} missing from the results"
    assert REASONS["model_not_found"] in res
    assert out["gets"] >= 2, "the run was polled to completion"
    assert out["btn_disabled_end"] is False, "the button comes back after the run"


def test_zero_models_is_a_plain_line_not_an_error():
    out = _run_harness("zero")
    assert out["summary"] == NO_MODELS_LINE
    assert out["err_display"] == "none", "nothing to test is never an error state"
    assert out["stamps"] == 0
    assert out["btn_disabled_end"] is False
    assert out["posts"] == 1


def test_start_failure_is_plain_with_a_working_retry():
    out = _run_harness("startfail")
    assert out["err_display"] == "block", "the inline error state shows"
    assert "Can’t reach the AgentQon server" in out["err_msg"], \
        "in plain words, not a TypeError"
    assert "Failed to fetch" not in out["err_msg"]
    assert "undefined" not in out["err_msg"]
    assert out["retry_btn"] is True
    assert out["posts"] == 2, "Try again started a fresh run"
    assert out["err_after"] == "none", "the error clears on success"
    assert out["summary_after"] == NO_MODELS_LINE


def test_double_click_fires_one_post_and_the_button_disables():
    out = _run_harness("dblclick")
    assert out["disabled_during"] is True, "the button disables while running"
    assert out["posts"] == 1, \
        f"a double click must not start two runs (saw {out['posts']})"
    assert out["summary"] == "1 of 1 models working"
    assert out["disabled_end"] is False
