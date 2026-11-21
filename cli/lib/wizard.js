'use strict';
/* Guided setup wizard (v22, contract R4/R6).
 *
 * When `agentqon setup` runs interactively on an install with no
 * model connected, setup used to end with "now just run agentqon"
 * and left a first-time user facing an empty workspace that could
 * not answer anything. The wizard walks them to a working model in
 * the same flow:
 *
 *   1. a model is already connected → say so, done;
 *   2. probe the local model servers people actually run (Ollama,
 *      LM Studio, OmniRouter, llama.cpp) in parallel — if any answer,
 *      offer the pick by number, create a local (no-key) connection
 *      and validate it;
 *   3. otherwise (or if the pick is declined) offer one API-key
 *      prompt: base URL + key → BYOK connection, validated;
 *   4. otherwise finish honestly model-less, with the exact next
 *      steps printed.
 *
 * Non-interactive installs never reach this module — bin/agentqon.js
 * gates it on an interactive TTY and --no-wizard — so scripted
 * setup behaves exactly as it always has.
 */
const s = require('./style');
const promptMod = require('./prompt');

/* The probe list is injectable (deps.probes) so tests can point it
 * at an ephemeral stub port instead of the real product ports. */
const LOCAL_PROBES = [
  { name: 'Ollama', base_url: 'http://127.0.0.1:11434/v1' },
  { name: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1' },
  { name: 'OmniRouter', base_url: 'http://127.0.0.1:20128/v1' },
  { name: 'llama.cpp', base_url: 'http://127.0.0.1:8080/v1' },
];

async function probeLocalServers(endpoints = LOCAL_PROBES, deps = {}) {
  const fetchImpl = deps.fetchImpl || fetch;
  const timeoutMs = deps.timeoutMs || 1200;
  const hits = await Promise.all((endpoints || []).map(async (ep) => {
    try {
      const res = await fetchImpl(`${String(ep.base_url).replace(/\/+$/, '')}/models`, {
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!res.ok) return null;
      const data = await res.json().catch(() => null);
      const models = data && Array.isArray(data.data)
        ? data.data.map((m) => m && m.id).filter(Boolean)
        : [];
      return { name: ep.name, base_url: ep.base_url, models };
    } catch { return null; }
  }));
  return hits.filter(Boolean);
}

/* Create a connection and validate it. Returns
 * { ok, state, model } — model is the tenant-resolved model name
 * once the connection is active (read back from /v1/model-state,
 * the same source the header chip and doctor trust). */
async function connectAndValidate(api, body) {
  const created = await api.post('/v1/connections', body);
  const id = created && (created.id || created.connection_id);
  if (!id) return { ok: false, state: 'unknown', message: 'the server did not return a connection id' };
  const result = await api.post(`/v1/connections/${id}/validate`, {});
  const state = (result && result.state) || 'unknown';
  if (state !== 'active') {
    return { ok: false, state, message: (result && (result.error || result.message)) || '' };
  }
  let model = null;
  try {
    const ms = await api.get('/v1/model-state');
    if (ms && ms.connected) model = ms.model;
  } catch { /* the connection is active; the name is garnish */ }
  if (!model && result.catalog_sync && result.catalog_sync.models) {
    model = `${result.catalog_sync.models} model(s) synced`;
  }
  return { ok: true, state, model: model || 'connected' };
}

async function byokFlow(api, deps) {
  const { ask, askPassword, out } = deps;
  const baseUrl = (await ask('API base URL (e.g. https://api.openai.com/v1):')).trim();
  if (!baseUrl) { out(s.dim('no URL given — skipping the API-key connect')); return null; }
  const apiKey = await askPassword('API key:');
  if (!apiKey) { out(s.dim('no key given — skipping the API-key connect')); return null; }
  try {
    const r = await connectAndValidate(api, {
      kind: 'direct_provider', label: 'API key', base_url: baseUrl, api_key: apiKey,
    });
    if (r.ok) return r.model;
    out(s.errorBox(
      new Error(`the provider did not validate (connection state: ${r.state})${r.message ? ' — ' + r.message : ''}`),
      'check the base URL and key, then re-run `agentqon setup`'));
  } catch (e) {
    out(s.errorBox(e, 'check the base URL and key, then re-run `agentqon setup`'));
  }
  return null;
}

async function runSetupWizard(api, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const ask = deps.ask || promptMod.ask;
  const askPassword = deps.askPassword || promptMod.askPassword;
  const askChoice = deps.askChoice || promptMod.askChoice;
  const probe = deps.probe || ((eps) => probeLocalServers(eps, deps));
  const probes = deps.probes || LOCAL_PROBES;
  const io = { ask, askPassword, out };

  out('');
  out(s.bold("Let's connect a model — one short step and AgentQon can answer."));

  let state = null;
  try { state = await api.get('/v1/model-state'); } catch { state = null; }
  if (state && state.connected) {
    out(s.green(`✓ a model is already connected: ${state.model || 'unknown'}`) + s.dim(` (via ${state.source || 'platform'})`));
    out(s.dim('Setup is complete — run `agentqon` to open the workspace, or `agentqon chat` to talk right here.'));
    return { handled: true, connected: true, model: state.model };
  }

  const found = await probe(probes);
  let connectedModel = null;

  if (found.length) {
    out(s.green(`✓ found ${found.length} local model server${found.length > 1 ? 's' : ''} on this machine:`));
    const options = found.map((f) => {
      const tail = f.models.length
        ? ` — ${f.models.length} model${f.models.length > 1 ? 's' : ''}: ${f.models.slice(0, 3).join(', ')}${f.models.length > 3 ? ', …' : ''}`
        : '';
      return `${f.name}  ${s.dim(f.base_url)}${tail}`;
    });
    options.push('None of these — connect with an API key instead');
    options.push("Skip — I'll connect a model later");
    const pick = await askChoice('Which one should AgentQon use?', options);
    if (pick >= 0 && pick < found.length) {
      const f = found[pick];
      try {
        const r = await connectAndValidate(api, {
          kind: 'local_endpoint', label: f.name, base_url: f.base_url,
        });
        if (r.ok) connectedModel = r.model;
        else {
          out(s.errorBox(
            new Error(`${f.name} answered, but the connection did not become active (state: ${r.state})${r.message ? ' — ' + r.message : ''}`),
            'make sure a model is loaded in the model server, then re-run `agentqon setup`'));
        }
      } catch (e) {
        out(s.errorBox(e, 're-run `agentqon setup` to try again, or connect from the web UI under Connections'));
      }
    } else if (pick === found.length) {
      connectedModel = await byokFlow(api, io);
    }
  } else {
    out(s.dim(`No local model server answered (checked ${probes.map((p) => `${p.name} ${p.base_url}`).join(', ')}).`));
    const pick = await askChoice('How do you want to connect a model?', [
      'Paste an API base URL + key',
      "Skip — I'll connect a model later",
    ]);
    if (pick === 0) connectedModel = await byokFlow(api, io);
  }

  out('');
  if (connectedModel) {
    out(s.green('✓ Setup complete') + s.dim(` — model connected: ${connectedModel}`));
    out(s.dim('Run `agentqon` to open the workspace, or `agentqon chat` to talk right here.'));
    return { handled: true, connected: true, model: connectedModel };
  }
  out(s.yellow('Setup complete — no model connected yet.'));
  out(s.dim('AgentQon will still open, but it cannot answer until a model is connected. To connect later:'));
  out(s.dim('  · re-run `agentqon setup` — this wizard offers the same choices, or'));
  out(s.dim('  · run `agentqon` and use the Connections card in the web UI.'));
  return { handled: true, connected: false };
}

module.exports = { LOCAL_PROBES, probeLocalServers, connectAndValidate, runSetupWizard };
