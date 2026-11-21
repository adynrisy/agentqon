'use strict';
/* Command implementations. Every API command resolves its target the
 * same way: remote mode (after `agentqon login --server …`) talks to the
 * hosted server with the stored session token; local mode (the default)
 * makes sure the bundled local server is running and talks to it.
 */
const fs = require('fs');
const path = require('path');
const s = require('./style');
const cfgMod = require('./config');
const { Api } = require('./api');
const prompt = require('./prompt');
const render = require('./render');
const missionLib = require('./mission');
const serverLib = require('./server');
const shield = require('./shield');
const skillsLib = require('./skills');

const VERSION = require('../package.json').version;
const out = (t) => process.stdout.write(String(t) + '\n');

/* Resolve { api, cfg, local, base } — starting the local server if needed. */
async function getApi() {
  const cfg = cfgMod.load();
  if (cfg.mode === 'remote' && cfg.server) {
    return { api: new Api(cfg), cfg, local: false, base: cfg.server };
  }
  const rec = await serverLib.startDetached();
  const localCfg = {
    ...cfg,
    server: `http://127.0.0.1:${rec.port}`,
    localToken: shield.loadLocalToken(),
    channelSecret: shield.loadChannelSecret(),
  };
  return { api: new Api(localCfg), cfg: localCfg, local: true, base: localCfg.server, rec };
}

/* ---------------- missions ---------------- */
/* A mission that ran and FAILED must exit non-zero: a run whose
 * POST itself failed already throws (exit 1), so the same user
 * outcome — no completed mission — used to exit 0 or 1 depending
 * on WHERE it failed, and scripts could not trust $?. */
function missionExitCode(status) { return status === 'failed' ? 1 : 0; }

async function cmdRun(text, opts = {}) {
  if (!text) throw new Error('give the mission text: agentqon run "your objective"');
  const { api } = await getApi();
  const detail = await missionLib.runMissionFlow(api, text, opts);
  if (missionExitCode(detail.mission.status)) process.exitCode = 1;
  let proof = null;
  if (detail.mission.status === 'completed') {
    try {
      const pack = await api.get(`/v1/missions/${detail.mission.id}/proof-pack`);
      proof = pack.confidence_summary || null;
    } catch { /* pack is best-effort garnish */ }
  }
  out(render.renderMissionResult(detail, proof));
  return detail;
}

async function cmdMissions(opts = {}) {
  const { api } = await getApi();
  const data = await api.get(`/v1/missions?limit=${opts.limit || 20}`);
  out(render.renderMissions(data.missions || []));
}

async function cmdProof(missionId) {
  if (!missionId) throw new Error('usage: agentqon proof <mission-id>');
  const { api } = await getApi();
  const pack = await api.get(`/v1/missions/${missionId}/proof-pack`);
  const cs = pack.confidence_summary || {};
  const lines = [
    `${s.dim('mission')}  ${missionId}`,
    `${s.dim('overall')}  ${s.bold(cs.overall || '—')}${cs.overall_reason ? s.dim(' — ' + cs.overall_reason) : ''}`,
    '',
    `${s.green('● verified')}  ${cs.verified ?? 0} claims`,
    `${s.yellow('● estimated')} ${cs.estimated ?? 0} claims`,
    `${s.grey('● assumed')}   ${cs.assumed ?? 0} claims`,
    s.dim(`of ${cs.claims_total ?? 0} claims on record — labels are computed from the recorded evidence, never asserted`),
  ];
  out(s.panel('🧾 Proof Pack', lines));
  let md = pack.markdown;
  if (!md && pack.markdown_artifact) {
    try {
      const art = await api.get(`/v1/artifacts/${pack.markdown_artifact.artifact_id}`);
      md = typeof art.content === 'string' ? art.content : (art.data || '');
    } catch { /* leave md unset */ }
  }
  if (md) {
    const file = path.join(process.cwd(), `proof-${missionId}.md`);
    fs.writeFileSync(file, md);
    out(s.footnote(`full pack saved → ${file}`));
  }
  return pack;
}

/* ---------------- agents / stats ---------------- */
async function cmdAgents(opts = {}) {
  const { api } = await getApi();
  const type = opts.type || 'strategy';
  const data = await api.get(`/v1/agents/suitability?task_type=${encodeURIComponent(type)}`);
  const ranking = (data.ranking || []).slice(0, opts.top || 10);
  if (!ranking.length) { out(s.dim(`no active agents ranked for ${type} yet — import a roster first (web UI → Advanced)`)); return; }
  out(render.renderAgents(ranking, data.task_type || type));
}

async function cmdStats() {
  const { api } = await getApi();
  const [stats, board] = await Promise.all([
    api.get('/v1/agents/stats'),
    api.get('/v1/arena/leaderboard').catch(() => ({ entries: [] })),
  ]);
  out(render.renderStats(stats.stats || [], board));
}

/* ---------------- arena ---------------- */
async function cmdArena(text, opts = {}) {
  if (!text) throw new Error('give the duel task: agentqon arena "write a launch tweet"');
  const { api } = await getApi();
  const spinner = new s.Spinner().start('Two agents are drafting blind…');
  let duel;
  try {
    duel = await api.post('/v1/arena/duels', { task_type: opts.type || 'strategy', prompt: text }, { timeout: 300_000 });
  } finally { spinner.stop(); }
  out('');
  out(render.renderDuelSide('Draft A — identity hidden', duel.side_a.output));
  out(render.renderDuelSide('Draft B — identity hidden', duel.side_b.output));
  if (!process.stdin.isTTY) { out(s.footnote(`vote later in the web UI — duel ${duel.duel_id}`)); return duel; }
  const ans = (await prompt.ask('Your vote — 1 for A, 2 for B (Enter to skip)')).trim();
  if (ans !== '1' && ans !== '2') { out(s.dim('no vote cast — the duel stays open in the web UI')); return duel; }
  const voted = await api.post(`/v1/arena/duels/${duel.duel_id}/vote`, { winner: ans === '1' ? 'a' : 'b' });
  const winSide = voted.winner_side === 'a' ? voted.side_a : voted.side_b;
  out('');
  out(s.panel('🏆 Result', [
    `winner: ${s.bold(winSide.agent_id || '')}${winSide.title ? s.dim(' — ' + winSide.title) : ''}`,
    s.dim('the vote was written into the same performance ledger as mission picks (agentqon stats)'),
  ]));
  return voted;
}

/* ---------------- schedules ---------------- */
async function cmdSchedules() {
  const { api } = await getApi();
  const data = await api.get('/v1/schedules');
  out(render.renderSchedules(data.schedules || []));
}

async function cmdSchedulesAdd() {
  const { api } = await getApi();
  const objective = await prompt.ask('Mission objective:');
  if (!objective) throw new Error('an objective is required');
  const title = await prompt.ask('Title', objective.slice(0, 60));
  out('Cadence:  1) every N hours   2) daily at HH:MM (UTC)   3) weekly');
  const pick = (await prompt.ask('Choose', '1')).trim();
  let cadence;
  if (pick === '2') {
    cadence = { kind: 'daily', time_of_day: await prompt.ask('Time of day UTC (HH:MM)', '09:00') };
  } else if (pick === '3') {
    const wd = await prompt.askChoice('Weekday:', ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    cadence = { kind: 'weekly', weekday: wd, time_of_day: await prompt.ask('Time of day UTC (HH:MM)', '09:00') };
  } else {
    const hrs = parseInt(await prompt.ask('Every how many hours?', '24'), 10);
    cadence = { kind: 'interval_hours', every_hours: Number.isInteger(hrs) && hrs > 0 ? hrs : 24 };
  }
  const created = await api.post('/v1/schedules', { title, objective, cadence });
  out(s.green(`✓ schedule created`) + s.dim(` — ${render.cadenceText(cadence)} · next run ${String(created.next_run_at || '').replace('T', ' ').slice(0, 16)} UTC`));
  return created;
}

/* ---------------- simulation ---------------- */
async function cmdSimulate(missionId) {
  if (!missionId) throw new Error('usage: agentqon simulate <mission-id>');
  const { api } = await getApi();
  const spinner = new s.Spinner().start('Building a world from this mission\'s research…');
  try {
    const detail = await api.get(`/v1/missions/${missionId}`);
    const strategies = [...new Set((detail.candidates || []).map((c) => c.strategy))];
    if (!strategies.length) throw new Error('that mission has no candidates to simulate');
    const created = await api.post('/v1/worlds', { name: 'World — ' + detail.mission.title.slice(0, 50), mission_id: missionId });
    const world = await api.get(`/v1/worlds/${created.world_id}`);
    const g = (world.provenance && world.provenance.grounding) || {};
    spinner.stop();
    for (const [k, v] of Object.entries(g)) {
      out(`  ${s.dim(k)}  ${v.value}${v.origin === 'data' ? s.dim(` — from research ("${String(v.evidence || '').slice(0, 60)}")`) : s.yellow(' — assumption (no figure in the research)')}`);
    }
    spinner.start(`Running ${detail.candidates.length} candidate branches through 26 simulated weeks…`);
    for (const c of detail.candidates || []) {
      await api.post(`/v1/worlds/${created.world_id}/branches`, { strategy: c.strategy, label: c.title, origin_candidate_id: c.candidate_id });
    }
    await api.post(`/v1/worlds/${created.world_id}/run`, {});
    const comp = await api.get(`/v1/worlds/${created.world_id}/compare`);
    spinner.stop();
    /* compare rows carry strategy reliably; their `label` field is
     * overwritten by the platform with the SIMULATED ESTIMATE tag. */
    const rows = (comp.rows || []).map((r) => [
      String(r.strategy || '').slice(0, 34),
      Number(r.adopters).toLocaleString(),
      `${r.market_share_pct}%`,
      `$${Number(r.total_revenue_usd).toLocaleString()}`,
      `${r.retention_90d_pct}%`,
    ]);
    out('');
    out(s.yellow('SIMULATED ESTIMATE') + s.dim(' — a seeded model world grounded in the mission\'s research, not a forecast'));
    out(s.table(['strategy', 'adopters', 'share', 'revenue', '90d retention'], rows));
    out(s.footnote(`world ${created.world_id} · seed ${comp.seed} · promote a branch into a draft mission from the web UI`));
    return comp;
  } catch (e) { spinner.stop(); throw e; }
}

/* ---------------- identity ---------------- */
async function cmdLogin(opts = {}) {
  const cfg = cfgMod.load();
  const rawServer = opts.server || await prompt.ask('AgentQon server URL', cfg.mode === 'remote' ? cfg.server : 'https://');
  /* Strip trailing slashes off the HOST part only: stripping the
   * bare default "https://" produced "https:" and a misleading
   * error; the host check below rejects a scheme with no host. */
  const server = String(rawServer).replace(/(.+[^:/])\/+$/, '$1');
  if (!/^https?:\/\/[^/\s]+/.test(server)) throw new Error('server URL must start with http:// or https:// and include a host');
  const email = await prompt.ask('Email:');
  const password = await prompt.askPassword('Password:');
  const probe = new Api({ server });
  let health = null;
  try { health = await probe.get('/health'); } catch { throw new Error(`cannot reach ${server} — check the URL and that the server is up`); }
  let res;
  try {
    res = await probe.post('/v1/auth/login', { email, password });
  } catch (e) {
    /* A packaged local install demands its per-install token on
     * every route, so `login` against one can never succeed — say
     * that plainly instead of surfacing the guard's raw refusal. */
    if (/per-install local token/.test(e.message || '')) {
      throw new Error('that server is a packaged local install — it only accepts clients on its own machine. Run `agentqon` there (local mode), or log in to a hosted AgentQon server instead.');
    }
    throw e;
  }
  let tenantId = res.tenant_id || null;
  const memberships = res.memberships || [];
  if (!tenantId && memberships.length === 1) tenantId = memberships[0].tenant_id;
  if (!tenantId && memberships.length > 1) {
    const idx = await prompt.askChoice('Which workspace?', memberships.map((m) => `${m.tenant_id} (${m.role})`));
    tenantId = memberships[idx].tenant_id;
  }
  const next = { ...cfg, mode: 'remote', server, token: res.token, tenantId, email };
  cfgMod.save(next);
  out(s.green(`✓ signed in as ${email}`) + (tenantId ? s.dim(` · workspace ${tenantId}`) : '') +
    (health && health.auth_mode === 'dev' ? s.dim('\n  note: that server runs in dev mode — the token is stored, header identity applies') : ''));
  out(s.footnote(`config saved to ${cfgMod.configPath()} (file permissions 0600)`));
}

async function cmdLogout() {
  const cfg = cfgMod.load();
  if (cfg.token && cfg.server) {
    try { await new Api(cfg).post('/v1/auth/logout', {}); } catch { /* session may already be gone */ }
  }
  const { token, email, tenantId, ...rest } = cfg;
  cfgMod.save(rest);
  out(s.green('✓ signed out') + s.dim(' — session token removed from this machine'));
}

async function cmdWhoami() {
  const cfg = cfgMod.load();
  if (cfg.mode === 'remote' && cfg.server) {
    const me = await new Api(cfg).get('/v1/auth/me');
    out(s.panel('Identity', [
      `${s.dim('mode')}    remote server`,
      `${s.dim('server')}  ${cfg.server}`,
      me.email ? `${s.dim('email')}   ${me.email}` : `${s.dim('user')}    ${me.user_id || '—'}`,
      `${s.dim('tenant')}  ${cfg.tenantId || (me.memberships && me.memberships[0] && me.memberships[0].tenant_id) || '—'}`,
    ]));
    return;
  }
  const rec = await serverLib.runningServer();
  out(s.panel('Identity', [
    `${s.dim('mode')}    local (this machine)`,
    `${s.dim('server')}  ${rec ? `http://127.0.0.1:${rec.port}` : s.yellow('not running — start with `agentqon`')}`,
    `${s.dim('auth')}    shield local token + signed channel (per-install, in ~/.agentqon)`,
    `${s.dim('data')}    ${serverLib.paths().dataDir}`,
  ]));
}

function cmdUseLocal() {
  const cfg = cfgMod.load();
  const { token, email, tenantId, ...rest } = cfg;
  cfgMod.save({ ...rest, mode: 'local' });
  out(s.green('✓ back to local mode') + s.dim(' — `agentqon` will run the platform on this machine'));
}

/* ---------------- doctor / update ---------------- */
/* The config doctor hands the API client. In local mode the packaged
 * server demands the per-install Shield credentials on every route —
 * a client built from the raw config file has neither the local token
 * nor the channel secret, so doctor's own authenticated probes
 * (/v1/model-state, /v1/agents) were refused and it silently fell
 * back to the /health payload (and reported the registry as broken
 * on a healthy install). Mirror getApi(): attach the credentials
 * whenever doctor targets the local server. */
function doctorApiCfg(cfg, base, local) {
  if (!local) return { ...cfg, server: base };
  return {
    ...cfg, server: base,
    localToken: shield.loadLocalToken(),
    channelSecret: shield.loadChannelSecret(),
  };
}

async function cmdDoctor() {
  const cfg = cfgMod.load();
  const p = serverLib.paths();
  const rows = [];
  const mark = (ok, warn) => (ok ? s.green('✓') : warn ? s.yellow('!') : s.red('✗'));
  const nodeOk = parseInt(process.versions.node, 10) >= 18;
  rows.push([mark(nodeOk), 'Node.js', process.versions.node + (nodeOk ? '' : ' — need ≥ 18')]);
  const py = serverLib.findPython();
  const wantPy = serverLib.requiredPython();
  rows.push([mark(!!py && !py.tooOld), `Python ${wantPy} (exact)`,
    !py ? `not found — install Python ${wantPy} (see \`agentqon setup\` guidance)`
      : py.tooOld ? `${py.display || py.cmd} ${py.version} found, but the protected payload runs only on Python ${wantPy} — install it (see \`agentqon setup\` guidance)`
        : `${py.display || py.cmd} ${py.version} — matches the protected payload`]);
  rows.push([mark(cfgMod.exists(), true), 'Config', cfgMod.exists() ? cfgMod.configPath() : 'none yet (created on first run)']);
  rows.push([mark(true), 'Mode', cfg.mode === 'remote' ? `remote → ${cfg.server}` : 'local (bundled server)']);
  rows.push([mark(cfg.serverVersion === VERSION, true), 'Platform bootstrap',
    cfg.serverVersion === VERSION ? `v${VERSION} at ${p.serverDir}` : 'pending — run `agentqon setup` or just `agentqon`']);
  const sh = serverLib.shieldStatus(p);
  rows.push(sh.mode === 'local-key'
    ? [mark(sh.ok), 'Shield payload', sh.ok
        ? `local-key mode · encrypted at rest · integrity verified (Python ${sh.python})`
        : `TAMPERED (${sh.bad}) — run \`agentqon setup --repair\``]
    : sh.mode === 'plain' && sh.installed
      ? [mark(true, true), 'Shield payload', 'plain bundle installed (repo/dev build — no encryption at rest; Shield applies to packaged releases)']
      : [mark(false, true), 'Shield payload', 'not installed yet — run `agentqon setup`']);
  rows.push([mark(!!shield.loadLocalToken()), 'Shield credentials',
    shield.loadLocalToken()
      ? 'local token + channel secret present (0600, ~/.agentqon)'
      : 'missing — run `agentqon setup`']);
  rows.push([mark(fs.existsSync(serverLib.paths().venvDir), true), 'Python venv', fs.existsSync(p.venvDir) ? p.venvDir : 'not created yet']);
  if (!fs.existsSync(p.venvDir)) {
    rows.push([mark(false, true), 'Libraries', 'not installed yet — run `agentqon setup` (or re-run the installer)']);
  } else {
    try {
      const chk = await serverLib.checkLibraries(p);
      rows.push(chk.ok
        ? [mark(true), 'Libraries', `${chk.count} distributions in venv · imports OK (fastapi, uvicorn, pydantic, cryptography…)`]
        : [mark(false), 'Libraries', `import check failed: ${(chk.missing || []).join(', ') || chk.error || 'unknown'} — re-run \`agentqon setup\``]);
    } catch (e) {
      rows.push([mark(false, true), 'Libraries', `check failed (${e.message})`]);
    }
  }

  let health = null; let base = null; let local = !(cfg.mode === 'remote' && cfg.server);
  if (local) {
    const rec = await serverLib.runningServer();
    if (rec) { health = rec.health; base = `http://127.0.0.1:${rec.port}`; }
    rows.push([mark(!!rec), 'Local server', rec ? `running · pid ${rec.pid} · ${base}` : 'not running — start with `agentqon`']);
  } else {
    base = cfg.server;
    try { health = await new Api(cfg).get('/health', { timeout: 5000 }); } catch { health = null; }
    rows.push([mark(!!health), 'Remote server', health ? `${base} · reachable` : `${base} · UNREACHABLE`]);
  }
  if (health) {
    rows.push([mark(true), 'Platform', `${health.product || 'AgentQon'} · auth ${health.auth_mode} · ${health.backend} · workers ${health.worker_mode}`]);
    /* v21.4.2: /health's model block covers platform sources only —
     * a workspace whose only model is an active BYOK connection read
     * "no model connected" forever. Prefer the authenticated
     * tenant-scoped /v1/model-state (the same resolve_state missions
     * actually use); fall back to health.model when it fails. */
    let model = null;
    try {
      const ms = await new Api(doctorApiCfg(cfg, base, local)).get('/v1/model-state');
      if (ms && typeof ms.connected === 'boolean') {
        model = { connected: ms.connected, name: ms.model, source: ms.source };
      }
    } catch { /* fall back to the health payload below */ }
    if (!model) {
      const hm = health.model || {};
      model = { connected: !!hm.connected, name: hm.name, source: hm.source };
    }
    rows.push([mark(model.connected, true), 'Model', model.connected
      ? `${model.name} · connected via ${model.source || 'platform'} — generation is live`
      : 'no model connected — generation is OFF (add a connection in the web UI under Connections, or start a local model server)']);
    try {
      const agents = await new Api(doctorApiCfg(cfg, base, local)).get('/v1/agents');
      rows.push([mark(true), 'Agent registry', `${(agents.agents || []).length} agents installed for the current workspace`]);
    } catch (e) {
      rows.push([mark(false, true), 'Agent registry', `could not read (${e.message})`]);
    }
  }
  out(s.panel('🩺 AgentQon doctor', rows.map((r) => `${r[0]} ${s.bold(String(r[1]).padEnd(20))} ${r[2]}`)));
}

function cmdUpdate() {
  /* Shield H2: npm is the ONLY update path. An earlier build printed
   * a curl-pipe-shell one-liner pointing at a GitHub namespace that
   * is not AgentQon's — executing a mutable remote script as an
   * update mechanism is exactly the trust hole Shield exists to
   * close, so the option is gone, not merely corrected.
   * The npm PACKAGE is agentqon — `agentqon` is both the package
   * name and the command name (bin). This string is pinned by the
   * error-surface audit. */
  out(s.panel('Update AgentQon', [
    'Run:  ' + s.cyan('npm install -g agentqon@latest'),
    '',
    s.dim('That is the only supported update path — the npm registry serves'),
    s.dim('the exact, integrity-checked release. Your data in ~/.agentqon/data'),
    s.dim('is never touched by an update — only the code is replaced, and the'),
    s.dim('new payload is signature-verified before it is installed.'),
  ]));
}

/* ---------------- brain (v21.2) ----------------
 * AgentQon as a brain for the user's own external agent: one scoped
 * key (aqb_…, server stores only a hash) + POST /v1/brain. The CLI
 * keeps the plaintext in the local config (0600, like the session
 * token) so `agentqon brain` can print ready-to-paste snippets. */
function brainSnippets(base, key) {
  const url = `${base}/v1/brain`;
  const curl = [
    `curl -s ${url} \\`,
    `  -H "Authorization: Bearer ${key}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"prompt": "What should we do first?"}'`,
  ].join('\n');
  const python = [
    'import json, urllib.request',
    '',
    `KEY = "${key}"`,
    `URL = "${url}"`,
    '',
    'def ask(prompt, session=None):',
    '    body = {"prompt": prompt}',
    '    if session:',
    '        body["session"] = session',
    '    req = urllib.request.Request(',
    '        URL, data=json.dumps(body).encode(),',
    '        headers={"Authorization": f"Bearer {KEY}",',
    '                 "Content-Type": "application/json"})',
    '    with urllib.request.urlopen(req, timeout=900) as r:',
    '        return json.loads(r.read())["answer"]',
    '',
    'print(ask("What should we do first?"))',
  ].join('\n');
  const node = [
    `const KEY = "${key}";`,
    `const URL = "${url}";`,
    'const res = await fetch(URL, {',
    '  method: "POST",',
    '  headers: { authorization: `Bearer ${KEY}`,',
    '             "content-type": "application/json" },',
    '  body: JSON.stringify({ prompt: "What should we do first?" }),',
    '});',
    'console.log((await res.json()).answer);',
  ].join('\n');
  return { curl, python, node };
}

function printBrainSnippets(base, key) {
  const sn = brainSnippets(base, key);
  out('');
  out(s.bold('curl'));
  out(sn.curl);
  out('');
  out(s.bold('Python (stdlib only — nothing to install)'));
  out(sn.python);
  out('');
  out(s.bold('Node (ESM, built-in fetch)'));
  out(sn.node);
}

async function cmdBrain(sub, opts = {}) {
  const { api, base } = await getApi();
  const url = `${base}/v1/brain`;
  if (sub === 'key') {
    const created = await api.post('/v1/brain/keys', { label: opts.label || 'brain' });
    const cfg = cfgMod.load();
    cfgMod.save({ ...cfg, brainKey: created.key, brainLabel: created.label });
    out(s.green('✓ brain key created') + s.dim(` — label "${created.label}", prefix ${created.key_prefix}`));
    out(s.yellow('This is the only time the server shows it; it is also saved in your local config (0600).'));
    out(s.bold(created.key));
    out(s.dim('Creating a new key revokes the previous one. Your agent calls:'));
    printBrainSnippets(base, created.key);
    return created;
  }
  if (sub === 'revoke') {
    const res = await api.del('/v1/brain/keys');
    const cfg = cfgMod.load();
    const { brainKey, brainLabel, ...rest } = cfg;
    cfgMod.save(rest);
    out(res.revoked
      ? s.green('✓ brain key revoked') + s.dim(' — external agents can no longer call the brain endpoint')
      : s.dim('no active brain key to revoke'));
    return res;
  }
  if (sub && sub !== 'status') {
    throw new Error(`unknown brain subcommand "${sub}" — use \`agentqon brain\`, \`agentqon brain key\` or \`agentqon brain revoke\``);
  }
  const status = await api.get('/v1/brain/keys');
  const cfg = cfgMod.load();
  const lines = [
    `${s.dim('endpoint')}  ${url}`,
    status.active
      ? `${s.dim('key')}       active · label "${status.label}" · prefix ${status.key_prefix} · created ${String(status.created_at || '').replace('T', ' ').slice(0, 16)}${status.last_used_at ? ` · last used ${String(status.last_used_at).replace('T', ' ').slice(0, 16)}` : ' · never used'}`
      : `${s.dim('key')}       none yet — create one with \`agentqon brain key\``,
    `${s.dim('scope')}     the key authorises POST /v1/brain only — nothing else`,
  ];
  out(s.panel('🧠 AgentQon as a brain for your own agent', lines));
  const storedMatches = cfg.brainKey && status.active
    && cfg.brainKey.slice(0, 12) === status.key_prefix;
  if (storedMatches) {
    out(s.dim('Your agent calls the endpoint like this (key filled in from your local config):'));
    printBrainSnippets(base, cfg.brainKey);
  } else if (status.active) {
    out(s.dim('A key is active but its plaintext is not in this machine\'s config (it is shown only at creation).'));
    out(s.dim('Run `agentqon brain key` to rotate it and get printable snippets.'));
  }
  out('');
  out(s.footnote('MCP instead? This package ships mcp/agentqon_brain_mcp.py — a stdlib-only stdio MCP server exposing ask_agentqon and agentqon_limits (env: AGENTQON_BRAIN_KEY, AGENTQON_BASE_URL).'));
  return status;
}

/* ---------------- evolve (v22, contract R2) ----------------
 * The self-evolution surface: what the harness learned from its own
 * outcomes — recent failure patterns, the lessons written to the
 * memory layer, and the tunings applied or proposed (never silent
 * self-modification: proposals are listed as proposals). Servers
 * without the endpoint get an honest fallback, not an error dump. */
function evolveLines(report) {
  const lines = [];
  const listOf = (v) => (Array.isArray(v) ? v : []);
  const patterns = listOf(report.failure_patterns || report.patterns);
  if (patterns.length) {
    lines.push(s.bold('Recent failure patterns'));
    for (const p of patterns.slice(0, 10)) {
      if (typeof p === 'string') { lines.push(`  · ${p}`); continue; }
      /* real shape (app/evolve.py): {category, subject_type, subject, count} */
      const head = p.pattern || p.category || 'pattern';
      const subj = p.subject ? ` — ${p.subject}` : '';
      lines.push(`  · ${head}${subj}${p.count ? s.dim(` × ${p.count}`) : ''}${p.detail ? s.dim(` — ${p.detail}`) : ''}`);
    }
    lines.push('');
  }
  const lessons = listOf(report.lessons);
  if (lessons.length) {
    lines.push(s.bold('Lessons on record'));
    for (const l of lessons.slice(0, 10)) {
      if (typeof l === 'string') { lines.push(`  · ${l}`); continue; }
      /* real shape: {subject_type, subject, outcome, category, cause} */
      if (l.subject || l.cause) {
        lines.push(`  · [${l.outcome || 'recorded'}] ${l.subject_type || ''} ${l.subject || ''}${l.category ? ` · ${l.category}` : ''}${l.cause ? ` — ${l.cause}` : ''}`);
      } else lines.push(`  · ${l.lesson || l.text || JSON.stringify(l)}`);
    }
    lines.push('');
  }
  /* real shape: report.tunings = {applied: [...], proposed: [...]};
   * the flat key spellings stay as fallbacks for older servers. */
  const tunings = (report.tunings && typeof report.tunings === 'object') ? report.tunings : {};
  const applied = listOf(tunings.applied || report.tunings_applied || report.applied_tunings || report.applied);
  if (applied.length) {
    lines.push(s.bold('Tunings applied'));
    for (const t of applied.slice(0, 10)) lines.push(`  · ${typeof t === 'string' ? t : (t.detail || t.description || t.name || JSON.stringify(t))}`);
    lines.push('');
  }
  const proposed = listOf(tunings.proposed || report.tunings_proposed || report.proposed_tunings || report.proposed);
  if (proposed.length) {
    lines.push(s.bold('Tunings proposed (not applied)'));
    for (const t of proposed.slice(0, 10)) lines.push(`  · ${typeof t === 'string' ? t : (t.detail || t.description || t.name || JSON.stringify(t))}`);
    lines.push('');
  }
  if (report.safety && report.safety.statement) {
    lines.push(s.dim(String(report.safety.statement)));
    lines.push('');
  }
  if (!lines.length) {
    const keys = Object.keys(report || {});
    lines.push(keys.length
      ? s.dim(`the report has no patterns, lessons or tunings yet (keys: ${keys.join(', ')}) — run more missions and check back`)
      : s.dim('the report is empty — run more missions and check back'));
  }
  while (lines.length && lines[lines.length - 1] === '') lines.pop();
  if (report.generated_at) lines.push('', s.dim(`generated ${String(report.generated_at).replace('T', ' ').slice(0, 16)}`));
  return lines;
}

async function cmdEvolve(deps = {}) {
  const api = deps.api || (await getApi()).api;
  let report;
  try {
    report = await api.get('/v1/evolve/report');
  } catch (e) {
    if (e && e.status === 404) {
      out(s.panel('🧬 Self-evolution report', [
        s.yellow('not available in this server build yet'),
        s.dim('the report endpoint ships with the v22 platform — update with'),
        s.dim('`npm install -g agentqon@latest`, then run `agentqon evolve` again'),
      ]));
      return null;
    }
    throw e;
  }
  out(s.panel('🧬 Self-evolution report', evolveLines(report || {})));
  return report;
}

/* ---------------- skills (v22.1, contract S4) ----------------
 * The Skill Library window: overview / search / show / pin against
 * /v1/skills* (lib/skills.js). The api resolves through getApi()
 * like every other command — a running server is reused, never
 * duplicated; deps.api is the test seam, same as cmdEvolve. */
async function cmdSkills(args, flags = {}, deps = {}) {
  const api = deps.api || (await getApi()).api;
  return skillsLib.runSkills(args, { api, repo: flags.repo, out: deps.out, flags });
}

/* ---------------- help ---------------- */
const HELP_ROWS = [
  ['agentqon', 'set up (first run), start the local server, open the UI, chat'],
  ['agentqon chat', 'interactive chat with the connected model (/model, /agent, /missions, /doctor inside)'],
  ['agentqon run <mission…> [--model ID]', 'run one mission and watch it happen (--model pins the model, like the web picker)'],
  ['agentqon setup [--no-wizard]', 'install the bundled platform into ~/.agentqon (interactive runs offer a guided model connect)'],
  ['agentqon evolve', 'self-evolution report — failure patterns, lessons, tunings'],
  ['agentqon skills', 'the Skill Library — size, biggest collections, how to search'],
  ['agentqon skills search <words…> [--repo owner/name]', 'search the library for skills matching a job'],
  ['agentqon skills show <id>', 'read a skill in full, with its source + license'],
  ['agentqon skills pin / unpin <id>', 'pin a skill so it is offered first for matching work'],
  ['agentqon skills extended [--install | --remove]', 'the optional extended library — 213 security-research skills npm does not carry'],
  ['agentqon setup --repair', 'restore a tampered/corrupt install from the verified bundle + rotate credentials'],
  ['agentqon rotate-channel', 'rotate the local token + channel secret (server must be stopped)'],
  ['agentqon start / stop / status', 'manage the local server'],
  ['agentqon serve', 'run the local server in the foreground'],
  ['agentqon open / logs', 'open the web UI / tail the server log'],
  ['agentqon agents [--top N] [--type T]', 'suitability-ranked agents'],
  ['agentqon stats', 'performance ledger + arena leaderboard'],
  ['agentqon missions [--limit N]', 'recent missions'],
  ['agentqon proof <mission-id>', 'evidence labels + save the proof pack'],
  ['agentqon arena <task…>', 'blind agent duel — you pick the winner'],
  ['agentqon schedules / schedules add', 'recurring missions'],
  ['agentqon simulate <mission-id>', 'run a mission\'s strategies in a model world'],
  ['agentqon brain', 'AgentQon as a brain for your own agent — endpoint, key status, snippets'],
  ['agentqon brain key [--label NAME]', 'create/rotate the brain key (shown once, saved locally)'],
  ['agentqon brain revoke', 'revoke the brain key'],
  ['agentqon login [--server URL]', 'use a hosted AgentQon instead of local'],
  ['agentqon logout / whoami / use-local', 'identity + mode'],
  ['agentqon doctor', 'styled health checklist'],
  ['agentqon update', 'how to update'],
];
function cmdHelp() {
  out(s.banner(VERSION));
  out('');
  out(s.table(['command', 'what it does'], HELP_ROWS.map(([a, b]) => [s.cyan(a), b])));
  out('');
  out(s.footnote('Inside the chat REPL: type a mission, or /agents /missions /proof <id> /arena /schedules /simulate <id> /help /exit'));
  out(s.footnote('Styling honours NO_COLOR and pipes — output stays plain when not on a terminal.'));
}

module.exports = {
  VERSION, getApi, cmdRun, cmdMissions, cmdProof, cmdAgents, cmdStats,
  cmdArena, cmdSchedules, cmdSchedulesAdd, cmdSimulate, cmdBrain, cmdLogin,
  cmdLogout, cmdWhoami, cmdUseLocal, cmdDoctor, cmdUpdate, cmdHelp, cmdEvolve,
  cmdSkills, doctorApiCfg, missionExitCode, evolveLines,
};
