#!/usr/bin/env node
'use strict';
/* AgentQon — terminal harness.
 *
 * `agentqon` on a fresh machine sets up the bundled platform in
 * ~/.agentqon, starts the local server, opens the web UI and drops you
 * into the chat REPL. Everything else is a subcommand (agentqon --help).
 */
const s = require('../lib/style');
const cfgMod = require('../lib/config');
const { Api } = require('../lib/api');
const serverLib = require('../lib/server');
const shield = require('../lib/shield');
const commands = require('../lib/commands');
const repl = require('../lib/repl');
const chat = require('../lib/chat');
const wizard = require('../lib/wizard');

const VERSION = require('../package.json').version;
const out = (t) => process.stdout.write(String(t) + '\n');

/* `agentqon … | head` must never crash the harness. */
process.stdout.on('error', (e) => {
  if (e && e.code === 'EPIPE') process.exit(0);
  throw e;
});

const VALUE_FLAGS = new Set(['server', 'top', 'limit', 'type', 'label', 'model', 'repo']);
function parseArgs(argv) {
  const positional = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      if (VALUE_FLAGS.has(key)) flags[key] = argv[++i];
      else flags[key] = true;
    } else if (a === '-h') flags.help = true;
    else if (a === '-v') flags.version = true;
    else positional.push(a);
  }
  return { positional, flags };
}

async function defaultFlow(flags) {
  out(s.banner(VERSION));
  const cfg = cfgMod.load();
  if (cfg.mode === 'remote' && cfg.server) {
    out(s.dim(`remote mode → ${cfg.server}  (switch back any time with \`agentqon use-local\`)`));
    await repl.start(new Api(cfg), { base: cfg.server });
    return;
  }
  const rec = await serverLib.startDetached();
  const url = `http://127.0.0.1:${rec.port}`;
  out('');
  out(s.green('● AgentQon is running at ') + s.bold(url));
  out(s.dim(`  web UI ${url} · data ${serverLib.paths().dataDir} · logs: agentqon logs`));
  /* Shield: the browser proves install ownership with the local
   * token, handed over in the URL fragment (never sent to the
   * server, stripped by the UI into sessionStorage). */
  const token = shield.loadLocalToken();
  if (!flags['no-open']) serverLib.openBrowser(token ? `${url}/#lt=${token}` : url);
  out('');
  await repl.start(new Api({
    server: url, localToken: token, channelSecret: shield.loadChannelSecret(),
  }), { base: url });
}

async function main() {
  const { positional, flags } = parseArgs(process.argv.slice(2));
  const cmd = positional[0];
  const rest = positional.slice(1).join(' ');

  if (flags.version || cmd === 'version') { out(VERSION); return; }
  if (flags.help || cmd === 'help') { commands.cmdHelp(); return; }

  switch (cmd) {
    case undefined:
      await defaultFlow(flags); return;
    case 'setup': {
      out(s.banner(VERSION));
      await serverLib.ensureBootstrapped({ repair: !!flags.repair });
      if (flags.repair) {
        out(s.dim('Repair complete — payload restored from the verified bundle and all local credentials rotated. Start fresh with `agentqon`.'));
        return;
      }
      /* Extended Library Pack (v22.2): one ask-first offer during
       * interactive setup (default no; declining or failing leaves
       * setup exactly as successful). Non-interactive setup never
       * sees it. The offer itself never throws into setup. */
      try {
        await require('../lib/extended').maybeOfferDuringSetup();
      } catch { /* the optional pack can never break setup */ }
      /* v22 guided wizard: interactive TTY only, and only as an
       * offer — scripted/non-interactive setup (and --no-wizard)
       * behaves byte-for-byte as it always has. The wizard itself
       * checks whether a model is already connected and stops there. */
      if (!flags['no-wizard'] && process.stdin.isTTY) {
        try {
          const { api } = await commands.getApi();
          const res = await wizard.runSetupWizard(api);
          if (res && res.handled) return;
        } catch (e) {
          out(s.errorBox(e, 'setup itself finished — connect a model later from the web UI under Connections'));
        }
      }
      out(s.dim('Now just run `agentqon` — it starts the server and opens your workspace.'));
      return;
    }
    case 'chat': {
      const cfg = cfgMod.load();
      if (cfg.mode === 'remote' && cfg.server) {
        await chat.start(new Api(cfg), { base: cfg.server });
        return;
      }
      /* getApi() goes through startDetached — a running verified
       * server is reused, never duplicated. */
      const { api, base } = await commands.getApi();
      await chat.start(api, { base });
      return;
    }
    case 'evolve': await commands.cmdEvolve(); return;
    case 'skills': await commands.cmdSkills(positional.slice(1), flags); return;
    case 'rotate-channel': {
      /* Liveness via anyLiveServer, not the single health probe
       * (audit 2026-10-22 M1): a server busy with a long mission
       * can miss the 1.5 s probe, and rotating under it strands
       * the live server on credentials that are already dead. */
      const rec = await serverLib.anyLiveServer();
      if (rec) { out(s.errorBox(new Error('the local server is running — stop it first with `agentqon stop`, then rotate'), 'rotate-channel')); process.exitCode = 1; return; }
      shield.rotateCredentials();
      out(s.green('✓ channel secret + local token rotated') + s.dim(' — the old credentials are dead; the next `agentqon` start uses the new ones'));
      return;
    }
    case 'start': {
      const rec = await serverLib.startDetached();
      out(s.green(`● AgentQon running at http://127.0.0.1:${rec.port}`) + s.dim(` · pid ${rec.pid} · stop with \`agentqon stop\``));
      return;
    }
    case 'stop': {
      const r = await serverLib.stopServer();
      out(r.stopped ? s.green(`✓ AgentQon stopped`) + s.dim(` (was on port ${r.port})`) : s.dim(`AgentQon is not running (${r.reason})`));
      return;
    }
    case 'status': {
      const rec = await serverLib.runningServer();
      const p = serverLib.paths();
      if (!rec) { out(s.panel('AgentQon status', [`${s.dot('failed')} not running`, s.dim('start it with `agentqon` — first run sets everything up')])); return; }
      const h = rec.health || {};
      /* /health's model block covers platform sources only; a
       * workspace whose model is an active BYOK connection read
       * "no model connected" (or the platform model) here while
       * missions ran fine. Prefer the authenticated /v1/model-state
       * — the same source doctor uses — and fall back to health. */
      let model = h.model ? { connected: !!h.model.connected, name: h.model.name, source: h.model.source } : { connected: false };
      try {
        const ms = await new Api({
          server: `http://127.0.0.1:${rec.port}`,
          localToken: shield.loadLocalToken(),
          channelSecret: shield.loadChannelSecret(),
        }).get('/v1/model-state');
        if (ms && typeof ms.connected === 'boolean') {
          model = { connected: ms.connected, name: ms.model, source: ms.source };
        }
      } catch { /* health payload stands */ }
      out(s.panel('AgentQon status', [
        `${s.dot('completed')} running   ${s.dim('pid')} ${rec.pid}   ${s.dim('port')} ${rec.port}   ${s.dim('since')} ${rec.startedAt || '—'}`,
        `${s.dim('url')}      http://127.0.0.1:${rec.port}`,
        `${s.dim('platform')} ${h.product || 'AgentQon'} · auth ${h.auth_mode || '?'} · ${h.backend || '?'} · workers ${h.worker_mode || '?'}`,
        `${s.dim('model')}    ${model.connected ? `${model.name} · via ${model.source || 'platform'}` : 'no model connected — generation is OFF'}`,
        `${s.dim('code')}     ${p.serverDir}`,
        `${s.dim('data')}     ${p.dataDir}`,
        `${s.dim('log')}      ${p.logPath}`,
      ]));
      return;
    }
    case 'serve': out(s.banner(VERSION)); await serverLib.serveForeground(); return;
    case 'open': {
      const cfg = cfgMod.load();
      if (cfg.mode === 'remote' && cfg.server) { serverLib.openBrowser(cfg.server); out(s.dim(`opened ${cfg.server}`)); return; }
      const rec = await serverLib.startDetached();
      const token = shield.loadLocalToken();
      serverLib.openBrowser(token ? `http://127.0.0.1:${rec.port}/#lt=${token}` : `http://127.0.0.1:${rec.port}`);
      out(s.green(`● opened http://127.0.0.1:${rec.port}`));
      return;
    }
    case 'logs': {
      const tail = serverLib.tailLogs(80);
      out(tail === null ? s.dim('no server log yet') : tail);
      return;
    }
    case 'run': await commands.cmdRun(rest, { live: !!flags.live, model: flags.model }); return;
    case 'agents': await commands.cmdAgents({ top: flags.top ? parseInt(flags.top, 10) : 10, type: flags.type }); return;
    case 'stats': await commands.cmdStats(); return;
    case 'missions': await commands.cmdMissions({ limit: flags.limit ? parseInt(flags.limit, 10) : 20 }); return;
    case 'proof': await commands.cmdProof(positional[1]); return;
    case 'arena': await commands.cmdArena(rest, { type: flags.type }); return;
    case 'schedules':
      if (positional[1] === 'add') await commands.cmdSchedulesAdd();
      else await commands.cmdSchedules();
      return;
    case 'simulate': await commands.cmdSimulate(positional[1]); return;
    case 'brain': await commands.cmdBrain(positional[1], { label: flags.label }); return;
    case 'login': await commands.cmdLogin({ server: flags.server }); return;
    case 'logout': await commands.cmdLogout(); return;
    case 'whoami': await commands.cmdWhoami(); return;
    case 'use-local': commands.cmdUseLocal(); return;
    case 'doctor': await commands.cmdDoctor(); return;
    case 'update': commands.cmdUpdate(); return;
    default:
      out(s.errorBox(new Error(`unknown command "${cmd}"`), 'run `agentqon --help` for the command list'));
      process.exitCode = 1;
  }
}

/* Top-level error surface (error audit): every failure prints ONE
 * clean block — what happened, then the exact fix command — never a
 * stack trace or an unhandled-rejection dump. Known failure classes
 * get their specific recovery; everything else points at doctor. */
function fixHint(e) {
  const m = (e && e.message) || '';
  if (/^Shield:/.test(m) || m.includes('integrity')) {
    return 'recover with `agentqon setup --repair` — if that fails: npm install -g agentqon@latest, then `agentqon setup --repair`';
  }
  if (m.includes('running from this runtime') || m.includes('runtime copy is locked')) {
    return 'stop the running server with `agentqon stop`, close other agentqon terminals, then try again';
  }
  if (/no model|model_not_connected/i.test(m)) {
    return 'connect a model with `agentqon setup` (guided), or in the web UI under Connections';
  }
  if (m.includes('pip install failed')) return 'check the network connection, then re-run `agentqon setup`';
  if (m.includes('cannot reach')) return 'start the local server with `agentqon` — or run `agentqon doctor`';
  if (m.includes('failed to start') || m.includes('start timed out')) return 'run `agentqon doctor` — and `agentqon logs` shows the server log';
  return 'run `agentqon doctor` for a health checklist';
}

function reportError(e) {
  if (e && typeof e.message === 'string' && /Python .* is required/.test(e.message)) {
    out(e.message); // Python guidance is already styled, fix command included
  } else {
    out(s.errorBox(e, fixHint(e)));
  }
  process.exitCode = 1;
}

process.on('unhandledRejection', (e) => { reportError(e instanceof Error ? e : new Error(String(e))); });
process.on('uncaughtException', (e) => { reportError(e); process.exit(1); });

main().catch(reportError);
