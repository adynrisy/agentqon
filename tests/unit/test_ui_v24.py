"""Audit-D regression pins (2026-10-12 full harness audit, section 14/18):

1 — refreshAll() ran refreshLists() and loadRegistry() back to back
    while BOTH sequenced on the shared _refreshSeq counter: the
    registry load's increment invalidated the panel refresh
    mid-flight, so missions/usage/sources silently kept their
    boot-time contents on every dashboard open, Refresh click and
    tenant switch. loadRegistry now sequences on _registrySeq.
2 — the header roster count was hardcoded ("6,799 agents") and
    asserted a number before any roster load — stale the moment a
    custom agent is added. It now starts empty; loadRegistry writes
    the true count.

The behavioral tests drive the real page in jsdom (stubbed API,
fake IndexedDB) when node + jsdom are available, and skip otherwise
— same standard as the audit harness that found the bugs.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

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


# ------------------------------------------------------------------ 1
def test_registry_and_panel_refresh_use_independent_sequence_counters():
    html = _html()
    reg = _body(html, "loadRegistry")
    assert "_registrySeq" in reg, "loadRegistry must sequence on its own counter"
    assert "_refreshSeq" not in reg, \
        "loadRegistry must not touch _refreshSeq — refreshAll() runs it back-to-back with refreshLists()"
    lst = _body(html, "refreshLists")
    assert "_refreshSeq" in lst and "_registrySeq" not in lst


# ------------------------------------------------------------------ 2
def test_roster_count_lives_only_in_the_registry():
    html = _html()
    assert 'id="rosterCount"' not in html, \
        "the header roster count duplicated the registry's own number — removed in the dedupe pass"
    reg = _body(html, "loadRegistry")
    assert "rosterCount" not in reg, "loadRegistry no longer writes a header count"
    render = _body(html, "renderAgents")
    assert "agentsNote" in render and "Showing the first" in render, \
        "the registry table note is the one place the roster total is stated"


# ------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const flush = async (n = 14, ms = 25) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, ms)); };
function makeFakeIDB(backing) {
  return { open() { const req = {};
    setTimeout(() => { const db = { createObjectStore() {},
      transaction() { return { objectStore() { return {
        get(k) { const r = {}; setTimeout(() => { r.result = backing.get(k); r.onsuccess && r.onsuccess(); }, 0); return r; },
        put(v, k) { const r = {}; setTimeout(() => { backing.set(k, v); r.onsuccess && r.onsuccess(); }, 0); return r; },
        delete(k) { const r = {}; setTimeout(() => { backing.delete(k); r.onsuccess && r.onsuccess(); }, 0); return r; },
      }; } }; } };
      req.result = db; if (req.onupgradeneeded) req.onupgradeneeded(); if (req.onsuccess) req.onsuccess(); }, 0);
    return req; } };
}
const AGENTS = [0, 1, 2].map(i => ({ agent_id: 'a' + i, name: 'A' + i, version: '1', cost_per_run_usd: 0, latency_ms: 1, capabilities: ['general'], quality: 4, lifecycle: 'active', agent_version_id: 'a' + i + '@v1', origin: 'library' }));
const CONNS = [
  { id: 'c1', label: 'C1', kind: 'local_endpoint', state: 'active', masked_key: '', base_url: 'http://x/v1', models_count: 1 },
  { id: 'c2', label: 'C2', kind: 'direct_provider', state: 'paused', masked_key: '', base_url: 'http://y/v1', models_count: 1 }];
function makeRoutes() {
  const state = { missions: [{ id: 'mA', title: 'Mission A', status: 'completed', spent: 0 }], kill: 'off' };
  const routes = (path, opts) => {
    const method = (opts && opts.method) || 'GET';
    if (path === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
    if (path === '/v1/projects') return { s: 200, j: { projects: [] } };
    if (path === '/v1/connections') return { s: 200, j: { connections: CONNS } };
    if (path === '/v1/models') return { s: 200, j: { connections: CONNS, models: [
      { model_id: 'model-1', connection_id: 'c1', price_status: 'unknown', health: 'ok', freshness: 'f', last_seen: '2026-01-01', capabilities: {} },
      { model_id: 'model-2', connection_id: 'c2', price_status: 'unknown', health: 'ok', freshness: 'f', last_seen: '2026-01-01', capabilities: {} }] } };
    if (path === '/v1/worlds') return { s: 200, j: { worlds: [] } };
    if (path === '/v1/channels/telegram/status' || path === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
    if (path === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
    if (path === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: state.missions } };
    if (path === '/v1/missions' && method === 'POST') return { s: 200, j: { mission_id: 'm-new' } };
    if (path === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
    if (path === '/v1/sources') return { s: 200, j: { sources: [] } };
    if (path === '/v1/model-state') return { s: 200, j: { connected: true, model: 'm', source: 'byok' } };
    if (path === '/v1/agents/stats') return { s: 200, j: { stats: [] } };
    if (path === '/v1/agents/version') return { s: 200, j: { version: 'v1' } };
    if (path === '/v1/agents') return { s: 200, j: { agents: AGENTS } };
    if (path.startsWith('/v1/agents/suitability')) return { s: 200, j: { ranking: [] } };
    if (path === '/v1/missions/m-new/run') return { s: 200, j: {} };
    if (path === '/v1/missions/m-new') return { s: 200, j: { mission: { id: 'm-new', title: 't', status: 'completed', spent: 0, contract: { objective: 'o', task_type: 'qa' } }, candidates: [], evaluations: [], critiques: [], tasks: [], events: [], route_decisions: [] } };
    if (path === '/v1/missions/m-new/events') return { s: 200, j: { events: [] } };
    if (path === '/v1/admin/overview') return { s: 200, j: { kill_switch: state.kill, jobs_by_status: {}, dead_jobs: 0, backend: 'x', spend_by_tenant: [], connections_by_state: {} } };
    if (path === '/v1/admin/kill-switch') { const b = JSON.parse(opts.body); state.kill = b.on ? 'on' : 'off'; return { s: 200, j: { kill_switch: state.kill } }; }
    return { s: 200, j: {} };
  };
  routes.state = state;
  return routes;
}
function makeWindow(routes, backing) {
  const calls = [];
  const dom = new JSDOM(HTML, { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.Element.prototype.scrollIntoView = function () {};
      window.scrollTo = () => {}; window.prompt = () => 'x'; window.confirm = () => true;
      window.URL.createObjectURL = () => 'blob:x'; window.URL.revokeObjectURL = () => {};
      if (backing) window.indexedDB = makeFakeIDB(backing);
      window.fetch = async (path, opts = {}) => { calls.push({ method: opts.method || 'GET', path, body: opts.body || null });
        const r = routes(path, opts); return { ok: r.s < 300, status: r.s, json: async () => r.j, blob: async () => new window.Blob(['x']) }; };
    } });
  return { window: dom.window, calls };
}
const cnt = (calls, m, p) => calls.filter(c => c.method === m && c.path === p).length;
(async () => {
  const out = {};
  const s = process.argv[2];
  if (s === 'refreshall') {
    const routes = makeRoutes(); const { window } = makeWindow(routes); const d = window.document;
    await flush();
    out.boot_A = d.getElementById('missions').innerHTML.includes('Mission A');
    routes.state.missions = [{ id: 'mB', title: 'Mission B', status: 'draft', spent: 0 }];
    window.refreshAll(); await flush(20);
    out.after_refreshAll_B = d.getElementById('missions').innerHTML.includes('Mission B');
    out.after_refreshAll_agents = d.getElementById('agents').innerHTML.includes('a0');
    window.close();
  } else if (s === 'roster') {
    const backing = new Map();
    const w1 = makeWindow(makeRoutes(), backing); await flush();
    w1.window.setView('dash'); await flush(20);
    out.cold_full = cnt(w1.calls, 'GET', '/v1/agents');
    w1.window.close();
    const w2 = makeWindow(makeRoutes(), backing); await flush();
    w2.window.setView('dash'); await flush(20);
    out.warm_full = cnt(w2.calls, 'GET', '/v1/agents');
    out.warm_probes = cnt(w2.calls, 'GET', '/v1/agents/version');
    out.warm_rendered = w2.window.document.getElementById('agents').innerHTML.includes('a0');
    out.warm_count_label = w2.window.document.getElementById('agentsNote').textContent.trim();
    w2.window.close();
  } else if (s === 'picker') {
    const { window, calls } = makeWindow(makeRoutes()); const d = window.document;
    await flush();
    const sel = d.getElementById('chatModel');
    out.options = [...sel.options].map(o => o.value);
    out.chatClass = !!d.getElementById('chatClass');
    sel.value = 'model-1'; d.getElementById('chatInput').value = 'task';
    await window.sendChat(); await flush();
    const body = JSON.parse(calls.find(c => c.method === 'POST' && c.path === '/v1/missions').body);
    out.pin_mode = body.contract.routing_mode; out.pin_id = body.contract.pinned_model_id;
    out.pin_class = body.contract.data_classification;
    window.close();
  } else if (s === 'kill') {
    const { window, calls } = makeWindow(makeRoutes()); const d = window.document;
    await flush();
    d.getElementById('adminLoadBtn').click(); await flush();
    out.label0 = d.getElementById('killBtn').textContent;
    d.getElementById('killBtn').click(); await flush();
    out.label1 = d.getElementById('killBtn').textContent;
    d.getElementById('killBtn').click(); await flush();
    out.label2 = d.getElementById('killBtn').textContent;
    out.bodies = calls.filter(c => c.path === '/v1/admin/kill-switch').map(c => c.body);
    window.close();
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
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
    node, env = _jsdom_env()
    if not node:
        pytest.skip("node/jsdom not available in this environment")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(HARNESS)
        script = f.name
    try:
        r = subprocess.run([node, script, scenario], capture_output=True,
                           text=True, timeout=180, env=env)
        assert r.returncode == 0, f"harness {scenario} failed: {r.stderr[-2000:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(script)


def test_jsdom_refreshall_renders_fresh_panels():
    out = _run_harness("refreshall")
    assert out["boot_A"] is True
    assert out["after_refreshAll_B"] is True, \
        "refreshAll must re-render missions — the shared-sequence bug froze them at boot contents"
    assert out["after_refreshAll_agents"] is True


def test_jsdom_roster_warm_load_fetches_nothing_full():
    out = _run_harness("roster")
    assert out["cold_full"] == 1
    assert out["warm_full"] == 0, "warm load must make ZERO full /v1/agents fetches"
    assert out["warm_probes"] >= 1
    assert out["warm_rendered"] is True
    assert out["warm_count_label"] == "Showing 3 agents."


def test_jsdom_chat_picker_pins_the_picked_model():
    out = _run_harness("picker")
    # Item 7 (2026-10-23, Zohaib's order) extends the picker with
    # the Combos group — Auto combo is always offered; named combos
    # join it when they exist. The original pin (Auto + active
    # models only) is superseded by that order.
    assert out["options"] == ["", "model-1", "combo:auto"], \
        "picker lists Auto plus models on ACTIVE connections only, then the Combos group"
    assert out["chatClass"] is False, "no privacy select in the chatbox"
    assert out["pin_mode"] == "pinned_model" and out["pin_id"] == "model-1"
    assert out["pin_class"] == "internal"


def test_jsdom_kill_switch_toggles_both_ways():
    out = _run_harness("kill")
    assert out["label0"] == "Kill switch: OFF"
    assert out["label1"] == "Kill switch: ON"
    assert out["label2"] == "Kill switch: OFF"
    assert out["bodies"] == ['{"on":true}', '{"on":false}']
