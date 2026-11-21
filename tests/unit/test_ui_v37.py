"""Item 5–7 contracts (2026-10-23 fix list): per-connection custom
headers + the AgentRouter preset; the provider preset registry
built from OmniRoute's provider list; and model combos
(OmniRoute-style named chains with strategies, glob mappings,
auto-combo and failover) in routing, chat and the UI.

Header VALUES are secrets: they must never come back in an API
response (masked summaries only), mirroring the api-key law.
Preset honesty: the picker holds only providers that can work
through an OpenAI-style endpoint; the rest are in the data with a
plain reason and never offered. Combo honesty: only strategies
with real behavior are offered; the deferred ones are listed
with reasons by the strategies endpoint.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "apps" / "web" / "index.html"
HTML = INDEX.read_text(encoding="utf-8")
REGISTRY = (ROOT.parent / "goals" / "omnimind-ai-agent-platform-build"
            / "hidden_files" / "omniroute-providers.json")


# ------------------------------------------------------- item 5: headers
def test_gateway_headers_merge_connection_headers():
    from app.gateway_client import GatewayClient
    gw = GatewayClient("https://x.example/v1", "sk-live-key", timeout=5,
                       headers={"User-Agent": "claude-cli/1.0.0 (external, cli)",
                                "anthropic-version": "2023-06-01",
                                "x-app": "cli",
                                "Authorization": "Bearer SHOULD-NOT-WIN"})
    h = gw._headers()
    assert h["User-Agent"] == "claude-cli/1.0.0 (external, cli)"
    assert h["anthropic-version"] == "2023-06-01"
    assert h["x-app"] == "cli"
    # The stored api key owns Authorization — a custom header must
    # not be able to hijack the credential channel.
    assert h["Authorization"] == "Bearer sk-live-key"
    assert h["Content-Type"] == "application/json"


def test_gateway_headers_keyless_connection_keeps_custom_authorization():
    from app.gateway_client import GatewayClient
    gw = GatewayClient("https://x.example/v1", None, timeout=5,
                       headers={"Authorization": "Bearer alt-token"})
    assert gw._headers()["Authorization"] == "Bearer alt-token"


def test_custom_headers_validation():
    import pytest
    from app.connections import validate_custom_headers
    assert validate_custom_headers({"X-App": "cli"}) == {"X-App": "cli"}
    assert validate_custom_headers(None) == {}
    for bad in ({"Bad Name": "x"}, {"X-Ok": "a\r\nInjected: 1"},
                {"X-Ok": "a\nb"}, {"X-Ok": "v" * 600}):
        with pytest.raises(ValueError):
            validate_custom_headers(bad)


def test_connection_with_headers_masks_values_via_api(client, tenant):
    from tests.conftest import hdr
    import os as _os
    body = {"kind": "direct_provider", "label": "Headery",
            "base_url": _os.environ["OMNIMIND_LLM_BASE_URL"],
            "api_key": "sk-test-123",
            "headers": {"X-Secret-Header": "super-secret-value-123",
                        "User-Agent": "claude-cli/1.0.0 (external, cli)"}}
    r = client.post("/v1/connections", json=body, headers=hdr(tenant))
    assert r.status_code == 201, r.text[:300]
    text = r.text
    assert "super-secret-value-123" not in text
    assert "claude-cli/1.0.0" not in text
    view = r.json()
    assert set(view["headers_masked"]) == {"X-Secret-Header", "User-Agent"}
    r = client.get("/v1/connections", headers=hdr(tenant))
    assert "super-secret-value-123" not in r.text
    assert "claude-cli/1.0.0" not in r.text


def test_connection_with_bad_headers_is_400(client, tenant):
    from tests.conftest import hdr
    import os as _os
    body = {"kind": "direct_provider", "label": "Bad headers",
            "base_url": _os.environ["OMNIMIND_LLM_BASE_URL"],
            "api_key": "sk-test-123",
            "headers": {"X-Ok": "a\r\nInjected: 1"}}
    r = client.post("/v1/connections", json=body, headers=hdr(tenant))
    assert r.status_code == 400, r.text[:300]


# ------------------------------------------------------- item 6: presets
def test_presets_data_covers_the_honest_subset():
    from app.presets import excluded_presets, picker_presets, preset_by_id
    picker = picker_presets()
    excluded = excluded_presets()
    assert len(picker) == 115, "114 openai-format + AgentRouter"
    assert len(excluded) == 17
    assert all(e["note"] for e in excluded), \
        "every excluded provider carries its plain reason"
    ar = preset_by_id("agentrouter")
    assert ar["base_url"] == "https://agentrouter.org/v1"
    # Main's deliberate replacement (b4bacabf + 2da06a56): the Claude Code
    # identity headers failed their first live phone test, so the preset now
    # sends exactly the documented opencode identity — no x-app, no
    # anthropic-version, no claude-cli string.
    assert ar["headers"] == {"User-Agent": "opencode/1.18.25"}


def test_presets_match_the_omniroute_registry():
    import pytest
    if not REGISTRY.exists():
        pytest.skip("OmniRoute registry snapshot not present")
    from app.presets import picker_presets
    registry = {p["id"]: p for p in json.loads(REGISTRY.read_text())}
    for preset in picker_presets():
        if preset["id"] == "agentrouter":
            continue  # item 5's special: /v1 + headers, not the registry URL
        assert preset["id"] in registry, preset["id"]
        assert preset["base_url"] == registry[preset["id"]]["agentqonBase"], \
            preset["id"] + " must prefill its exact registry base URL"


def test_presets_endpoint(client, tenant):
    from tests.conftest import hdr
    r = client.get("/v1/provider-presets", headers=hdr(tenant))
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert len(presets) == 115
    assert any(p["id"] == "agentrouter" for p in presets)


def test_preset_ui_contract():
    for el in ('id="cPreset"', 'id="ccPreset"', 'id="cPresetNote"',
               'id="ccPresetNote"', 'id="cHeadersFold"', 'id="ccHeadersFold"',
               'id="cHeaders"', 'id="ccHeaders"'):
        assert el in HTML, el + " on both connection forms"
    assert HTML.count("<b>Extra request details</b>") == 2
    assert "applyPreset(" in HTML and "loadProviderPresets" in HTML
    assert "parseHeadersText" in HTML
    assert "provider_key: $('cPreset').value" in HTML
    assert "provider_key: $('ccPreset').value" in HTML
    add_body = HTML[HTML.index("$('connAddBtn').onclick"):]
    assert "headers" in add_body[:2500], "the card save posts the headers"
    land_body = HTML[HTML.index("provider_key: $('ccPreset').value"):]
    assert "headers" in land_body[:300], "the landing save posts the headers"


# ------------------------------------------------------- item 7: combos
def _entries():
    def e(cid, mid, pin=10.0, pout=20.0, health="healthy"):
        return {"connection_id": cid, "model_id": mid, "price_in": pin,
                "price_out": pout, "price_status": "known",
                "health": health, "deprecated": False}
    return [e("c1", "alpha"), e("c1", "beta", 1.0, 2.0), e("c2", "gamma", 5.0, 5.0)]


def _conns():
    return {"c1": {"id": "c1", "state": "active"},
            "c2": {"id": "c2", "state": "active"}}


def _combo(strategy, targets=None, state=None):
    from app.combos import empty_state
    return {"combo_id": "combo_t", "name": "T", "strategy": strategy,
            "enabled": True,
            "targets": targets or [
                {"connection_id": "c1", "model_id": "alpha", "weight": 1},
                {"connection_id": "c1", "model_id": "beta", "weight": 3},
                {"connection_id": "c2", "model_id": "gamma", "weight": 1}],
            "state": state if state is not None else empty_state()}


def _keys(pairs):
    return [f"{e['connection_id']}:{e['model_id']}" for e, _c in pairs]


def test_combo_priority_and_cost_ordering():
    from app.combos import resolve_chain
    assert _keys(resolve_chain(_combo("priority"), _entries(), _conns())) \
        == ["c1:alpha", "c1:beta", "c2:gamma"]
    assert _keys(resolve_chain(_combo("cost-optimized"), _entries(), _conns()))[0] \
        == "c1:beta", "cheapest estimated call leads"


def test_combo_stateful_strategies():
    from app.combos import empty_state, resolve_chain
    rr = _combo("round-robin")
    first = _keys(resolve_chain(rr, _entries(), _conns()))[0]
    second = _keys(resolve_chain(rr, _entries(), _conns()))[0]
    assert first != second, "round-robin rotates the lead"
    st = empty_state(); st["calls"] = {"c1:alpha": 9}
    assert _keys(resolve_chain(_combo("least-used", state=st),
                               _entries(), _conns()))[0] != "c1:alpha"
    st = empty_state(); st["last_good"] = "c2:gamma"
    assert _keys(resolve_chain(_combo("lkgp", state=st),
                               _entries(), _conns()))[0] == "c2:gamma"
    st = empty_state(); st["sticky"] = "c1:beta"
    assert _keys(resolve_chain(_combo("fill-first", state=st),
                               _entries(), _conns()))[0] == "c1:beta"


def test_combo_weighted_covers_all_targets():
    from app.combos import resolve_chain
    got = _keys(resolve_chain(_combo("weighted"), _entries(), _conns()))
    assert sorted(got) == ["c1:alpha", "c1:beta", "c2:gamma"]


def test_combo_eligibility_drops_dead_connections():
    from app.combos import eligible_targets
    conns = _conns(); conns["c2"] = {"id": "c2", "state": "error"}
    got = eligible_targets(_combo("priority")["targets"], _entries(), conns)
    assert [e["model_id"] for e, _c in got] == ["alpha", "beta"]


def test_combo_auto_targets_from_connected_providers():
    from app.combos import auto_targets
    got = auto_targets(_entries(), _conns())
    assert {e["model_id"] for e, _c in got} == {"alpha", "beta", "gamma"}


def test_combo_mapping_priority_and_glob():
    from app.combos import match_mapping, validate_mapping
    maps = [validate_mapping({"pattern": "gpt-*", "combo_id": "c_a",
                              "priority": 50, "enabled": True}),
            validate_mapping({"pattern": "*", "combo_id": "c_b",
                              "priority": 100, "enabled": True}),
            validate_mapping({"pattern": "gpt-4*", "combo_id": "c_c",
                              "priority": 10, "enabled": False})]
    assert match_mapping(maps, "gpt-4o")["combo_id"] == "c_a", \
        "lowest enabled priority wins; disabled mappings never fire"
    assert match_mapping(maps, "llama-3")["combo_id"] == "c_b"
    assert match_mapping(maps, "GPT-4o")["combo_id"] == "c_b", \
        "matching is exact-case: gpt-* does not catch GPT-4o, the catch-all does"


def test_combo_validation_rejects_deferred_and_empty():
    import pytest
    from app.combos import validate_combo
    with pytest.raises(ValueError):
        validate_combo({"name": "X", "strategy": "fusion",
                        "targets": [{"connection_id": "c", "model_id": "m"}]})
    with pytest.raises(ValueError):
        validate_combo({"name": "X", "strategy": "priority", "targets": []})
    with pytest.raises(ValueError):
        validate_combo({"name": "", "strategy": "priority",
                        "targets": [{"connection_id": "c", "model_id": "m"}]})


class _FakeGateway:
    def __init__(self, outcome):
        self.outcome = outcome
        self.base_url = "https://fake.example/v1"

    async def chat_json(self, model, system, user, **kw):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"text": "ok:" + model}, {"model": model}

    async def chat_text(self, model, messages, **kw):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return "text:" + model, {"model": model}


def test_failover_moves_on_provider_failure_not_on_bad_request():
    from app.combos import FailoverGateway
    from app.gateway_client import GatewayError
    outcomes, failovers = [], []
    chain = [(_FakeGateway(GatewayError("provider_unavailable", "down")),
              "m1", "c1:m1"),
             (_FakeGateway("fine"), "m2", "c2:m2")]
    fg = FailoverGateway(chain, on_failover=failovers.append,
                         on_outcome=lambda k, ok: outcomes.append((k, ok)))

    async def go():
        return await fg.chat_json("ignored", "sys", "usr")
    result, _usage = asyncio.run(go())
    assert result == {"text": "ok:m2"}
    assert outcomes == [("c1:m1", False), ("c2:m2", True)]
    assert failovers and failovers[0]["category"] == "provider_unavailable"

    outcomes.clear()
    chain2 = [(_FakeGateway(GatewayError("bad_request", "bad prompt")),
               "m1", "c1:m1"),
              (_FakeGateway("fine"), "m2", "c2:m2")]
    fg2 = FailoverGateway(chain2,
                          on_outcome=lambda k, ok: outcomes.append((k, ok)))

    async def go2():
        return await fg2.chat_json("ignored", "sys", "usr")
    try:
        asyncio.run(go2())
        raise AssertionError("bad_request must NOT fail over")
    except GatewayError as exc:
        assert exc.category == "bad_request"
    assert outcomes == [("c1:m1", False)], "the second target never ran"


def test_combos_api_crud_mappings_and_strategies(client, tenant):
    from tests.conftest import hdr
    body = {"name": "Reliable", "strategy": "priority",
            "targets": [{"connection_id": "c1", "model_id": "alpha"},
                        {"connection_id": "c2", "model_id": "gamma"}]}
    r = client.post("/v1/combos", json=body, headers=hdr(tenant))
    assert r.status_code == 201, r.text[:300]
    combo = r.json()
    assert combo["state"]["last_good"] is None
    r = client.get("/v1/combos", headers=hdr(tenant))
    assert [c["name"] for c in r.json()["combos"]] == ["Reliable"]
    r = client.get("/v1/combos", headers=hdr(tenant + "_other"))
    assert r.json()["combos"] == [], "tenant isolation holds"
    # mapping to a missing combo is refused
    r = client.post("/v1/combo-mappings",
                    json={"pattern": "gpt-*", "combo_id": "combo_nope"},
                    headers=hdr(tenant))
    assert r.status_code == 400
    r = client.post("/v1/combo-mappings",
                    json={"pattern": "gpt-*", "combo_id": combo["combo_id"],
                          "priority": 10},
                    headers=hdr(tenant))
    assert r.status_code == 201, r.text[:300]
    # strategies endpoint: real set + deferred with reasons
    r = client.get("/v1/combos/strategies", headers=hdr(tenant))
    data = r.json()
    real = {s["id"] for s in data["strategies"]}
    assert {"priority", "fill-first", "weighted", "round-robin", "auto",
            "least-used", "cost-optimized", "lkgp"} <= real
    deferred = {s["id"]: s["reason"] for s in data["deferred"]}
    assert "reset-window" in deferred and deferred["reset-window"]
    assert "fusion" in deferred and "pipeline" in deferred
    # a deferred strategy cannot be saved as a combo
    r = client.post("/v1/combos", json={**body, "strategy": "fusion"},
                    headers=hdr(tenant))
    assert r.status_code == 400
    # auto preview shape
    r = client.get("/v1/combos/auto/preview", headers=hdr(tenant))
    assert r.status_code == 200 and "chain" in r.json()
    # delete
    r = client.delete("/v1/combos/" + combo["combo_id"], headers=hdr(tenant))
    assert r.status_code == 200
    r = client.get("/v1/combos", headers=hdr(tenant))
    assert r.json()["combos"] == []


def test_combo_ui_contract():
    for el in ('id="comboName"', 'id="comboStrategy"', 'id="comboTargets"',
               'id="comboSaveBtn"', 'id="combosList"', 'id="mapPattern"',
               'id="mapCombo"', 'id="mapPriority"', 'id="mappingsList"',
               'id="autoPreview"', 'id="comboDeferred"'):
        assert el in HTML, el
    assert '<optgroup label="Combos">' in HTML
    assert 'value="combo:auto"' in HTML
    assert "loadCombos" in HTML and "addComboTargetRow" in HTML
    models_card = HTML[HTML.index('id="cardModels"'):HTML.index('id="cardAdmin"')]
    assert 'id="comboName"' in models_card, "the editor lives in the catalog card"


# --------------------------------------------------------- behavioral
HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const HTML = fs.readFileSync(process.env.AQ_HTML, 'utf-8');
const MODE = process.argv[3] || 'presets';
const state = { posts: [] };
const PRESETS = { presets: [
  { id: 'custom', name: 'Custom', base_url: '', headers: {}, note: '' },
  { id: 'agentrouter', name: 'AgentRouter', base_url: 'https://agentrouter.org/v1',
    headers: { 'User-Agent': 'claude-cli/1.0.0 (external, cli)', 'anthropic-version': '2023-06-01', 'x-app': 'cli' }, note: 'needs headers' },
  { id: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', headers: {}, note: '' } ] };
const MODELS = { connections: [ { id: 'c1', label: 'Main', state: 'active' } ],
  models: [ { model_id: 'alpha', connection_id: 'c1' }, { model_id: 'beta', connection_id: 'c1' } ] };
const STRATS = { strategies: [ { id: 'priority', description: 'First healthy target wins.' },
    { id: 'weighted', description: 'Weights steer the order.' } ],
  deferred: [ { id: 'reset-window', reason: 'No quota reset clocks are tracked.' } ] };
function routes(path, opts) {
  const method = (opts && opts.method) || 'GET';
  const clean = path.split('?')[0];
  if (method === 'POST' || method === 'PUT') state.posts.push({ method, path: clean, body: opts.body || '' });
  if (clean === '/v1/auth/config') return { s: 200, j: { auth_mode: 'local' } };
  if (clean === '/v1/provider-presets') return { s: 200, j: PRESETS };
  if (clean === '/v1/models' && method === 'GET') return { s: 200, j: MODELS };
  if (clean === '/v1/combos/strategies') return { s: 200, j: STRATS };
  if (clean === '/v1/combos/auto/preview') return { s: 200, j: { chain: [ { model_id: 'alpha' } ] } };
  if (clean === '/v1/combos' && method === 'GET') return { s: 200, j: { combos: [], mappings: [] } };
  if (clean === '/v1/combos' && method === 'POST') return { s: 201, j: { combo: { combo_id: 'combo_x' } } };
  if (clean === '/v1/projects') return { s: 200, j: { projects: [] } };
  if (clean === '/v1/worlds' && method === 'GET') return { s: 200, j: { worlds: [] } };
  if (clean === '/v1/societies' && method === 'GET') return { s: 200, j: { societies: [] } };
  if (clean === '/v1/channels/telegram/status' || clean === '/v1/channels/whatsapp/status') return { s: 200, j: { linked: false } };
  if (clean === '/v1/whatsapp/onboard/public-config') return { s: 200, j: { configured: false } };
  if (clean === '/v1/usage') return { s: 200, j: { by_category: {}, total_usd: 0 } };
  if (clean === '/v1/sources') return { s: 200, j: { sources: [] } };
  if (clean === '/v1/model-state') return { s: 200, j: { connected: true, model: 'stub-model', connections: 1 } };
  if (clean === '/v1/missions' && method === 'GET') return { s: 200, j: { missions: [] } };
  if (clean === '/v1/connections' && method === 'GET') return { s: 200, j: { connections: [] } };
  if (clean === '/v1/skills/facets') return { s: 200, j: { repos: [], total: 0, unique_names: 0 } };
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
  if (MODE === 'presets') {
    await waitFor(() => $id('cPreset') && $id('cPreset').options.length === 4);
    out.picker_count = $id('cPreset').options.length;
    $id('cPreset').value = 'agentrouter';
    $id('cPreset').dispatchEvent(new window.Event('change'));
    out.base_filled = $id('cBase').value;
    out.headers_filled = $id('cHeaders').value;
    out.fold_open = $id('cHeadersFold').open;
    out.note_shown = ($id('cPresetNote').textContent || '').length > 0;
    // custom entry still works: typing a URL by hand stays possible
    $id('cPreset').value = 'custom';
    $id('cPreset').dispatchEvent(new window.Event('change'));
    $id('cBase').value = 'https://my-own.example/v1';
    out.custom_url_kept = $id('cBase').value;
    // the landing form carries the same preset machinery
    await waitFor(() => $id('ccPreset') && $id('ccPreset').options.length === 4);
    $id('ccPreset').value = 'agentrouter';
    $id('ccPreset').dispatchEvent(new window.Event('change'));
    out.landing_base = $id('ccBaseUrl').value;
    out.landing_headers = $id('ccHeaders').value.includes('x-app');
  }
  if (MODE === 'combos') {
    await window.loadModels();
    out.chat_picker_has_auto_combo = [...$id('chatModel').options].some(o => o.value === 'combo:auto');
    await waitFor(() => $id('comboStrategy').options.length === 2);
    out.strategy_options = [...$id('comboStrategy').options].map(o => o.value);
    out.deferred_named = ($id('comboDeferred').textContent || '').includes('reset-window');
    out.auto_preview_shown = ($id('autoPreview').textContent || '').includes('alpha');
    $id('comboName').value = 'Reliable';
    $id('comboStrategy').value = 'priority';
    await waitFor(() => d.querySelectorAll('.combo-target-row').length >= 1);
    $id('comboSaveBtn').click();
    await waitFor(() => state.posts.some(p => p.path === '/v1/combos' && p.method === 'POST'));
    const post = state.posts.find(p => p.path === '/v1/combos' && p.method === 'POST');
    out.combo_post_body = post ? post.body : '';
  }
  console.log(JSON.stringify(out));
  process.exit(0);  // redesign: the live activity poller keeps the loop alive; results already printed (v25 harness precedent)
})().catch(e => { console.error(e); process.exit(1); });
"""


def _run_jsdom(mode):
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not available in this environment")
    harness = ROOT / "tests" / "unit" / "_aq_v37_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        env = dict(os.environ, AQ_HTML=str(INDEX),
                   NODE_PATH="/home/hatch/workspace/toolmere/node_modules")
        proc = subprocess.run([node, str(harness), "x", mode],
                              capture_output=True, text=True, env=env,
                              timeout=180)
        assert proc.returncode == 0, proc.stderr[-1500:]
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        return json.loads(lines[-1])
    finally:
        harness.unlink(missing_ok=True)


def test_agentrouter_preset_fills_both_forms():
    out = _run_jsdom("presets")
    assert out["picker_count"] == 4, "placeholder + the 3 fixture presets"
    assert out["base_filled"] == "https://agentrouter.org/v1"
    assert "claude-cli" in out["headers_filled"]
    assert "x-app: cli" in out["headers_filled"]
    assert out["fold_open"] is True
    assert out["custom_url_kept"] == "https://my-own.example/v1"
    assert out["landing_base"] == "https://agentrouter.org/v1"
    assert out["landing_headers"] is True


def test_combo_editor_creates_and_picker_gains_combos():
    out = _run_jsdom("combos")
    assert out["chat_picker_has_auto_combo"] is True
    assert out["strategy_options"] == ["priority", "weighted"]
    assert out["deferred_named"] is True
    assert out["auto_preview_shown"] is True
    body = json.loads(out["combo_post_body"])
    assert body["name"] == "Reliable" and body["strategy"] == "priority"
    assert body["targets"] and body["targets"][0]["model_id"] == "alpha"
