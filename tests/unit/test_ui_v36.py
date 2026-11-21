"""v36 UI contracts — Zohaib's fix list (2026-10-23), from his own
live run of the shipped product:

1. STOP MISSION. The engine always honoured POST /v1/missions/{id}
   /cancel; the screens had no control. The live chat card and the
   mission detail both carry a plainly-labelled "Stop mission",
   shown only while the mission can still be stopped, one click,
   and the existing cancelled rendering takes over after.

2. NAVIGATION RULE (Zohaib's refined map, 21:30): sidebar = Chat ·
   Missions · Agents · Simulations · Skills · Advanced — the
   Connections entry is REMOVED. Missions (list + detail, society
   panel included), Agents (registry) and Simulations (worlds +
   world runs) live ONLY on their own pages; Skills keeps its own
   page. Connections live ONLY in Advanced (no sidebar entry, no
   page); Manage connections is a deliberate settings route there.
   No workspace click lands in Advanced.

3. RETRY VISIBILITY. While a mission's newest recorded activity
   is a failed model call, the status lines say "Your model isn't
   answering — retrying" in plain words — derived from the event
   record, never guessed.

4. WORLD OUTPUT, PLAIN FIRST. The deterministic world's detail
   leads with the story in simple words and the SIMULATED
   ESTIMATE label; the technical record (outcome hash, baseline
   hash, seed/engine, sensitivity, CAC/spend) sits behind a
   "Full record" fold — kept as proof, never first.

Contract tests pin the shipped source; behavioral tests drive the
real page in jsdom (stubbed API) when node + jsdom are available
and skip otherwise — same standard as v24–v35. Behavioral waits
are condition-based (waitFor), never sleeps.
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
    i = html.index(f'id="{section_id}"')
    start = html.rindex("<section", 0, i)
    end = html.index("</section>", i)
    return html[start:end]


# ------------------------------------------------- fix 1: stop
def test_detail_carries_a_stop_control_wired_to_cancel():
    html = _html()
    tag = re.search(r'<button[^>]*id="stopBtn"[^>]*>', html)
    assert tag and 'name="mission-stop"' in tag.group(0), \
        "the detail's Stop button exists and is named"
    assert ">Stop mission<" in html
    detail = _body(html, "renderDetail")
    assert "$('stopBtn').style.display" in detail, \
        "the detail decides the button's visibility from the status"
    for state in ("'draft'", "'planned'", "'running'",
                  "'awaiting_approval'", "'paused'"):
        assert state in detail, f"stoppable state missing: {state}"
    handler = re.search(r"\$\('stopBtn'\)\.onclick = async \(\) => \{(.*?)\n\};",
                        html, re.S)
    assert handler and "/cancel" in handler.group(1) \
        and "method:'POST'" in handler.group(1), \
        "one click posts the cancel the engine already honours"


def test_live_card_carries_a_stop_control():
    html = _html()
    run = _body(html, "runChatMission")
    assert 'name="mission-stop"' in run and ">Stop mission<" in run, \
        "the live card renders its Stop button while the mission runs"
    assert "chatStopMission(" in run, "wired to the card's stop action"
    stop = _body(html, "chatStopMission")
    assert "/cancel" in stop and "method:'POST'" in stop
    assert "stoppedMissions.add(mid)" in stop
    finish_at = run.index("if (runError")
    assert "stoppedMissions.has(mid)" in run[finish_at:finish_at + 60], \
        "a deliberate stop is never reported back as a failed run"


# ------------------------------------------- fix 2: the map
def test_sidebar_is_exactly_the_refined_map():
    html = _html()
    nav = html[html.index('<nav class="snav">'):html.index('</nav>')]
    ids = re.findall(r'id="(nav\w+)"', nav)
    assert ids == ["navChat", "navMissions", "navAgents", "navSkills",
                   "navSims"], \
        f"sidebar entries are Chat · Missions · Agents · Skills · Simulations — got {ids}"
    assert "navConns" not in html and 'name="nav-connections"' not in html, \
        "the Connections sidebar entry is removed entirely"
    assert 'id="railAdvBtn"' in html, "Advanced keeps its own entry"


def test_each_workspace_field_lives_on_its_own_page():
    html = _html()
    missions = _section(html, "missionsView")
    for kept in ('id="cardMissions"', 'id="missionCard"',
                 'id="compareCard"', 'id="societyBlock"'):
        assert kept in missions, kept + " — the Missions page holds it"
    agents = _section(html, "agentsView")
    assert 'id="cardAgents"' in agents
    sims = _section(html, "simsView")
    assert 'id="cardSimulations"' in sims, "the worlds card's home"
    assert 'id="cardSocietyRuns"' in sims and 'id="societyRuns"' in sims, \
        "the world runs list rides the Simulations page"
    dash = html[html.index('id="dashView"'):html.index('id="missionsView"')]
    for moved in ('id="cardMissions"', 'id="missionCard"',
                  'id="compareCard"', 'id="cardAgents"',
                  'id="cardSimulations"', 'id="societyBlock"'):
        assert moved not in dash, moved + " — out of Advanced"
    for kept in ('id="cardConnections"', 'id="cardModels"',
                 'id="cardUsage"', 'id="cardTelegram"',
                 'id="cardWhatsApp"', 'id="cardAdmin"'):
        assert kept in dash, kept + " — Advanced keeps it"


def test_moved_cards_stand_open_and_connections_stay_folded():
    html = _html()
    for cid in ("cardMissions", "cardAgents", "cardSimulations"):
        tag = re.search(r'<div class="card[^"]*" id="' + cid + r'"', html)
        assert tag and "foldable" not in tag.group(0), \
            cid + " stands open on its own page"
    tag = re.search(r'<div class="card[^"]*" id="cardConnections"', html)
    assert tag and "foldable" in tag.group(0), \
        "connections remain a folded Advanced docket"


def test_no_workspace_route_enters_advanced():
    html = _html()
    for entry, view in (("navMissions", "missions"), ("navAgents", "agents"),
                        ("navSims", "sims")):
        line = re.search(r"\$\('" + entry + r"'\)\.onclick = \(\) => \{[^}]*\}",
                         html)
        assert line and f"setView('{view}')" in line.group(0), \
            f"{entry} opens its own section"
        assert "dash" not in line.group(0)
    go = _body(html, "goSocietyPanel")
    assert "setView('missions')" in go and "openMission(mid)" in go, \
        "the society jump lands on the Missions page's detail"
    assert "setView('dash')" not in go
    manage = re.search(r"\$\('hcManageBtn'\)\.onclick = ([^;]+);", html)
    assert manage and manage.group(1).strip() == "goDash('cardConnections')", \
        "Manage connections is the one deliberate route into Advanced"
    view = _body(html, "setView")
    for v in ("missions", "agents", "sims", "skills", "dash"):
        assert f"'{v}'" in view
    assert "conns" not in view, "there is no Connections view at all"


# ------------------------------------------- fix 3: retry words
def test_retry_wording_is_derived_from_the_event_record():
    html = _html()
    labels = html[html.index("const STEP_LABELS"):html.index("function friendlySteps")]
    assert "ModelCallFailed:" in labels \
        and "A model call failed — retrying" in labels, \
        "the minutes/steps vocabulary names a failed model call plainly"
    run = _body(html, "runChatMission")
    assert "Your model isn't answering — retrying" in run, \
        "the live card's status line carries the plain sentence"
    assert "lastEvt.event_type === 'ModelCallFailed'" in run, \
        "derived from the newest recorded event — never guessed"
    detail = _body(html, "renderDetail")
    assert "missionRetryNote" in detail, \
        "the detail shows the same note under the same evidence"
    watch = _body(html, "watchMission")
    assert "missionRetryNote" in watch and "ModelCallFailed" in watch, \
        "a live failure event raises the note without a reload"


# ------------------------------------------- fix 4: plain first
def test_world_detail_leads_plain_and_folds_the_record():
    html = _html()
    body = _body(html, "openWorld")
    assert '<span class="chip warn">SIMULATED ESTIMATE</span>' in body, \
        "the honesty label leads, in a warning chip"
    assert "adopters</b> —" in body, \
        "each branch's outcome is a plain sentence, not a metric row"
    assert '<details class="fullrecord"><summary>Full record</summary>' in body, \
        "the technical record sits behind a labelled fold"
    first_fold = body.index("<details")
    for raw in ("Baseline hash", "Outcome hash"):
        assert body.index(raw) > first_fold, \
            raw + " appears only inside the Full record fold"
    # Sensitivity is plainified inside the fold (P-55); the raw label is gone.
    assert body.index("drops by a fifth") > first_fold, \
        "the plain sensitivity sentence sits inside the Full record fold"
    assert body.index("SIMULATED ESTIMATE") < first_fold, \
        "the label is met before any fold"


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


# --------------------------------------------------------- the list endpoint
def test_societies_list_endpoint_serves_the_runs_list(client, tenant):
    from tests.conftest import hdr
    import app.main as main_mod
    r = client.get("/v1/societies", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    assert r.json() == {"societies": []}, "a fresh workspace has no runs"
    main_mod.store.put_society(tenant, {
        "society_id": "soc_list_1", "state": "done", "mission_id": None,
        "task_text": "A standalone question", "objective": "standalone",
        "model": "stub-model", "label": "SIMULATED ESTIMATE",
        "sizer": {"size": 25}, "result": None, "created_at": 1700000000.0,
        "cast": [], "events": []})
    r = client.get("/v1/societies", headers=hdr(tenant))
    rows = r.json()["societies"]
    assert len(rows) == 1 and rows[0]["society_id"] == "soc_list_1"
    assert rows[0]["state"] == "done" and rows[0]["size"] == 25
    r = client.get("/v1/societies", headers=hdr(tenant + "_other"))
    assert r.json() == {"societies": []}, "tenant isolation holds"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const MODE = process.argv[3] || 'nav';
const state = { calls: [], cancelled: false, eventsMode: 'fail' };
const DETAIL_RUNNING = { mission: { id: 'mrun', title: 'Retry watch', status: 'running', spent: 0,
    contract: { objective: 'Watch the retry wording' } },
  tasks: [], candidates: [], evaluations: [], critiques: [], route_decisions: [],
  events: [ { event_type: 'MissionStarted', occurred_at: '', payload: {} },
            { event_type: 'ModelCallFailed', occurred_at: '', payload: { error: 'gateway unreachable' } } ] };
const WORLD = { world_id: 'w1', name: 'Launch world', status: 'complete', seed: 42,
  engine_version: 'sim-1', baseline_hash: 'BASEHASH123',
  baseline: { market_size: 10000, price_usd_month: 20, budget_usd: 5000 },
  provenance: { grounding: { market_size: { value: 10000, origin: 'data', evidence: 'research' } } },
  branches: [ { branch_id: 'b1', label: 'Niche first', strategy: 'niche-first', status: 'complete',
    result: { series: [ { tick: 1, adopters: 10 }, { tick: 2, adopters: 40 } ] } } ] };
const COMPARE = { rows: [ { branch_id: 'b1', adopters: 1234, market_share_pct: 12.3,
  blended_cac_usd: 18, retention_90d_pct: 78, total_spend_usd: 22123,
  budget_exhausted_tick: null, outcome_hash: 'OUTHASH999',
  sensitivity: [ { assumption: 'price', minus_20pct: 1000, plus_20pct: 1400 } ] } ] };
function routes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  const clean = path.split('?')[0];
  state.calls.push({ method, path: clean });
  if (clean === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
  if (clean === '/v1/projects') return { s: 200, j: { projects: [] } };
  if (clean === '/v1/worlds' && method === 'GET') return { s: 200, j: { worlds: [] } };
  if (clean === '/v1/worlds/w1') return { s: 200, j: WORLD };
  if (clean === '/v1/worlds/w1/compare') return { s: 200, j: COMPARE };
  if (clean === '/v1/societies' && method === 'GET') return { s: 200, j: { societies: [] } };
  if (clean === '/v1/channels/telegram/status' || clean === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
  if (clean === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
  if (clean === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
  if (clean === '/v1/sources') return { s: 200, j: { sources: [] } };
  if (clean === '/v1/model-state') return { s: 200, j: { connected: true, model: 'stub-model', connections: 1 } };
  if (clean === '/v1/models' && method === 'GET') return { s: 200, j: { connections: [], models: [] } };
  if (clean === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
  if (clean === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
  if (clean === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
  if (clean === '/v1/missions/mstop/run' && method === 'POST') return { s: 200, j: {} };
  if (clean === '/v1/missions/mstop/cancel' && method === 'POST') { state.cancelled = true; return { s: 200, j: {} }; }
  if (clean === '/v1/missions/mstop/events') return { s: 200, j: { events: [ { event_type: 'MissionStarted', payload: {} } ] } };
  if (clean === '/v1/missions/mstop') return { s: 200, j: { mission: { id: 'mstop', title: 'Stoppable', status: state.cancelled ? 'cancelled' : 'running', spent: 0, contract: { objective: 'x' } }, tasks: [], candidates: [], evaluations: [], critiques: [], events: [], route_decisions: [] } };
  if (clean === '/v1/missions/mretry/run' && method === 'POST') return { s: 200, j: {} };
  if (clean === '/v1/missions/mretry/events') {
    const base = [ { event_type: 'MissionStarted', payload: {} }, { event_type: 'ModelCallFailed', payload: {} } ];
    if (state.eventsMode === 'recovered') base.push({ event_type: 'ResearchCompleted', payload: {} });
    return { s: 200, j: { events: base } };
  }
  if (clean === '/v1/missions/mretry') return { s: 200, j: { mission: { id: 'mretry', title: 'Retry', status: 'running', spent: 0, contract: { objective: 'x' } }, tasks: [], candidates: [], evaluations: [], critiques: [], events: [], route_decisions: [] } };
  if (clean === '/v1/missions/mrun/cancel' && method === 'POST') { state.cancelled = true; return { s: 200, j: {} }; }
  if (clean === '/v1/missions/mrun') {
    if (!state.cancelled) return { s: 200, j: DETAIL_RUNNING };
    const done = JSON.parse(JSON.stringify(DETAIL_RUNNING));
    done.mission.status = 'cancelled';
    return { s: 200, j: done };
  }
  return { s: 200, j: {} };
}
function makeWindow() {
  const dom = new JSDOM(HTML, { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.Element.prototype.scrollIntoView = function () {};
      window.scrollTo = () => {}; window.prompt = () => 'x'; window.confirm = () => true;
      window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
      window.fetch = async (path, opts = {}) => { const r = routes(path, opts);
        return { ok: r.s < 300, status: r.s, json: async () => r.j, blob: async () => new window.Blob(['x']) }; };
    } });
  return dom.window;
}
const waitFor = async (fn, timeout = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < timeout) { if (fn()) return true; await new Promise(r => setTimeout(r, 50)); } return fn(); };
(async () => {
  const out = {};
  const window = makeWindow(); const d = window.document;
  const $id = id => d.getElementById(id);
  await waitFor(() => typeof window.setView === 'function');
  if (MODE === 'nav') {
    const seen = {};
    for (const [btn, view] of [ ['navMissions', 'missions'], ['navAgents', 'agents'], ['navSims', 'sims'], ['navSkills', 'skills'] ]) {
      $id(btn).click();
      seen[view] = { view: d.body.dataset.view, shown: $id(view + 'View').style.display, dash: $id('dashView').style.display };
    }
    out.seen = seen;
    $id('railAdvBtn').click();
    out.adv_view = d.body.dataset.view;
    out.no_conns_entry = !$id('navConns');
    $id('navChat').click();
    out.back_to_chat = d.body.dataset.view;
  }
  if (MODE === 'stopcard') {
    await window.runChatMission('mstop');
    await waitFor(() => !!d.querySelector('#live-mstop button[name="mission-stop"]'));
    out.stop_btn = true;
    d.querySelector('#live-mstop button[name="mission-stop"]').click();
    await waitFor(() => state.calls.some(c => c.method === 'POST' && c.path === '/v1/missions/mstop/cancel'));
    out.cancel_posted = true;
    await waitFor(() => !$id('live-mstop'), 20000);
    out.card_gone = true;
    out.thread_says_cancelled = $id('thread').textContent.toLowerCase().includes('cancelled');
    out.no_failure_box = !$id('thread').textContent.includes('This run could not complete');
  }
  if (MODE === 'stopdetail') {
    await window.openMission('mrun');
    out.stop_shown = $id('stopBtn').style.display !== 'none';
    out.retry_note_shown = $id('missionRetryNote').style.display === 'block';
    $id('stopBtn').click();
    await waitFor(() => state.calls.some(c => c.method === 'POST' && c.path === '/v1/missions/mrun/cancel'));
    out.cancel_posted = true;
    await waitFor(() => $id('mStatus').textContent.toLowerCase().includes('stopped'));
    out.status_after = $id('mStatus').textContent;
    out.stop_hidden_after = $id('stopBtn').style.display === 'none';
    out.retry_note_hidden_after = $id('missionRetryNote').style.display === 'none';
  }
  if (MODE === 'retry') {
    await window.runChatMission('mretry');
    await waitFor(() => ($id('qmeta-mretry') || {}).textContent && $id('qmeta-mretry').textContent.includes("Your model isn't answering — retrying"), 20000);
    out.retry_line = $id('qmeta-mretry').textContent;
    state.eventsMode = 'recovered';
    await waitFor(() => $id('qmeta-mretry') && !$id('qmeta-mretry').textContent.includes("isn't answering"), 20000);
    out.recovered_line = $id('qmeta-mretry').textContent;
  }
  if (MODE === 'world') {
    await window.openWorld('w1');
    const host = $id('world-detail');
    out.plain_sentence = host.textContent.includes('1,234 adopters');
    out.label_shown = host.textContent.includes('SIMULATED ESTIMATE');
    const clone = host.cloneNode(true);
    clone.querySelectorAll('details').forEach(x => x.remove());
    out.hash_outside_fold = clone.textContent.includes('OUTHASH999') || clone.textContent.includes('BASEHASH123');
    const folds = [...host.querySelectorAll('details.fullrecord')];
    out.fold_count = folds.length;
    out.folds_closed = folds.every(f => !f.open);
    out.fold_label = folds.length ? folds[0].querySelector('summary').textContent : '';
    out.hash_in_fold = folds.some(f => f.textContent.includes('OUTHASH999'));
    folds.forEach(f => { f.open = true; });
    out.hash_after_open = folds.some(f => f.open && f.textContent.includes('OUTHASH999'));
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
                              text=True, env=env, timeout=240)
        assert proc.returncode == 0, \
            f"harness {mode} failed: {proc.stderr[-2000:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_nav_clicks_land_on_their_own_pages():
    out = _run_harness("nav")
    for view in ("missions", "agents", "sims", "skills"):
        assert out["seen"][view]["view"] == view, view
        assert out["seen"][view]["shown"] == "block", view
        assert out["seen"][view]["dash"] == "none", \
            view + " — Advanced is NOT the active view"
    assert out["adv_view"] == "dash", "Advanced is reached by its own entry"
    assert out["no_conns_entry"] is True
    assert out["back_to_chat"] == "chat"


def test_live_card_stop_button_cancels_the_mission():
    out = _run_harness("stopcard")
    assert out["stop_btn"] is True, "the running card shows Stop mission"
    assert out["cancel_posted"] is True, "one click posts the cancel"
    assert out["card_gone"] is True and out["thread_says_cancelled"] is True, \
        "the cancelled rendering takes over"
    assert out["no_failure_box"] is True, \
        "a deliberate stop is not reported as a failed run"


def test_detail_stop_button_and_retry_note():
    out = _run_harness("stopdetail")
    assert out["stop_shown"] is True, "Stop shows while the mission runs"
    assert out["retry_note_shown"] is True, \
        "the newest event is a failed model call — the note says so"
    assert out["cancel_posted"] is True
    assert "stopped" in out["status_after"].lower()
    assert out["stop_hidden_after"] is True, "nothing left to stop"
    assert out["retry_note_hidden_after"] is True


def test_retry_line_appears_and_clears_with_the_record():
    out = _run_harness("retry")
    assert "Your model isn't answering — retrying" in out["retry_line"]
    assert "isn't answering" not in out["recovered_line"], \
        "a newer event takes the line back — the state is never faked"


def test_world_output_plain_first_record_folded():
    out = _run_harness("world")
    assert out["plain_sentence"] is True, "the story leads, in words"
    assert out["label_shown"] is True, "SIMULATED ESTIMATE stays prominent"
    assert out["hash_outside_fold"] is False, \
        "no hash in the default-visible surface"
    assert out["fold_count"] >= 1 and out["folds_closed"] is True
    assert out["fold_label"] == "Full record"
    assert out["hash_in_fold"] is True, "the proof is kept, behind the fold"
    assert out["hash_after_open"] is True, "opening the fold reveals it"
