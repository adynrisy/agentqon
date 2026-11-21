'use strict';
/* Interactive chat REPL — `agentqon chat` (v22, contract R4).
 *
 * Plain conversation with the connected model over POST /v1/chat:
 * no mission, no panel — the same split the web chatbox makes. The
 * session history lives in this process and is sent with each turn.
 *
 * Slash commands: /model (show + pin the chat model), /agent <goal>
 * (agent mode against POST /v1/agent/sessions, with terminal
 * approvals), /missions, /doctor, /help, /exit.
 *
 * Input is consumed through one line queue fed by readline, so a
 * sub-flow (an agent approval prompt) reads from the same stream as
 * the main loop — no competing readline interfaces, piped or TTY.
 * Ctrl+C cancels the in-flight wait (chat call or agent watch),
 * never the REPL itself; a second idle Ctrl+C (or /exit, or EOF)
 * leaves.
 */
const readline = require('readline');
const s = require('./style');
const commands = require('./commands');
const render = require('./render');

function makeLineQueue(rl) {
  const lines = [];
  const waiters = [];
  let closed = false;
  rl.on('line', (l) => {
    const w = waiters.shift();
    if (w) w(l); else lines.push(l);
  });
  rl.on('close', () => {
    closed = true;
    let w;
    while ((w = waiters.shift())) w(null);
  });
  return {
    next() {
      if (lines.length) return Promise.resolve(lines.shift());
      if (closed) return Promise.resolve(null);
      return new Promise((res) => waiters.push(res));
    },
  };
}

function chatFixHint(e) {
  const m = (e && e.message) || '';
  if (e && e.status === 0) return 'start the server with `agentqon`, or run `agentqon doctor`';
  if (/no model/i.test(m) || (e && e.code === 'model_not_connected')) {
    return 'connect a model with `agentqon setup` (guided), or in the web UI under Connections';
  }
  if (/cannot reach/i.test(m)) return 'start the server with `agentqon`, or run `agentqon doctor`';
  return 'run `agentqon doctor` for a health checklist';
}

/* Events arrive in the platform's event envelope:
 * { event_id, event_type, payload, occurred_at }. Render each type
 * as one plain step line; unknown types fall back to their payload
 * message or the type name — never a raw dump. */
function renderAgentEvent(ev) {
  if (!ev || typeof ev !== 'object') return String(ev);
  const type = String(ev.event_type || ev.type || '');
  const p = ev.payload || {};
  const short = (v, n = 220) => {
    const t = typeof v === 'string' ? v : JSON.stringify(v ?? '');
    return t.length > n ? t.slice(0, n) + '…' : t;
  };
  switch (type) {
    case 'AgentSessionCreated': return 'session started';
    case 'AgentStep': return `step ${p.step ?? ''} — ${short(p.thought || '')}`.trim();
    case 'ToolCall': return `tool ${p.tool || ''} ${short(p.args)}${p.needs_approval ? s.yellow(' (needs approval)') : ''}`;
    case 'ToolResult': return `${p.ok ? '↳ ok' : '↳ failed'} — ${short(p.summary || '')}`;
    case 'ApprovalNeeded': return s.yellow(`approval needed: ${p.tool || 'action'} — ${p.reason || ''}`);
    case 'ApprovalResolved': return `approval ${p.decision || 'resolved'}`;
    case 'ProviderRateLimited': return `provider rate-limited — waiting ${p.wait_s ?? p.wait ?? ''}s`.trim();
    case 'AgentCompleted': return p.verified === false ? s.yellow(`⚠ done claimed but UNVERIFIED — last tool result failed${p.unresolved_failure && p.unresolved_failure.tool ? ` (${p.unresolved_failure.tool})` : ''}: ${short(p.summary || '')}`) : s.green(`✓ done — ${short(p.summary || '')}`);
    case 'AgentFailed': return s.red(`✗ failed — ${p.category || ''}: ${short(p.cause || '')}`);
    case 'AgentCancelled': return 'session cancelled';
    default: return short(p.message || p.text || type || 'step');
  }
}

function describePendingAction(pa) {
  if (!pa || typeof pa !== 'object') return 'perform an action';
  const args = pa.args ? ` ${JSON.stringify(pa.args)}` : '';
  return `${pa.tool || 'action'}${args}${pa.reason ? ` — ${pa.reason}` : ''}`;
}

/* Drive an agent session from the terminal, mirroring the backend
 * protocol (app/agent_loop.py): POST /v1/agent-sessions creates it;
 * POST …/run drives the loop and BLOCKS until the session parks on
 * an approval or reaches a terminal state; POST …/approve
 * {decision} executes/denies the parked call and resumes the loop.
 * While a drive call is in flight, a pump polls …/events?after= so
 * steps print live. Ctrl+C (deps.isCancelled) stops the watching —
 * the session itself keeps its server-side state.
 * deps (tests): { out, nextLine, pollMs, signal } */
async function runAgentSession(api, goal, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const pollMs = deps.pollMs || 1000;
  const driveOpts = { timeout: 1_800_000, signal: deps.signal };
  let created;
  try {
    created = await api.post('/v1/agent-sessions', { goal });
  } catch (e) {
    if (e && e.status === 404) {
      out(s.yellow('agent mode is not available in this server build') + s.dim(` — run it as a mission instead: agentqon run "${goal}"`));
      return { ok: false, unavailable: true };
    }
    throw e;
  }
  const session0 = (created && created.session) || created || {};
  const id = session0.id || session0.session_id;
  if (!id) {
    out(s.errorBox(new Error('the server accepted the agent session but returned no session id'), 'run `agentqon doctor`'));
    return { ok: false };
  }
  out(s.dim(`agent session ${id} — working on: ${goal}`));

  let cursor = null;
  let pumpStopped = false;
  const pump = (async () => {
    while (!pumpStopped) {
      try {
        const data = await api.get(`/v1/agent-sessions/${id}/events${cursor ? `?after=${encodeURIComponent(cursor)}` : ''}`);
        for (const ev of (data && data.events) || []) {
          if (ev.event_id) cursor = ev.event_id;
          out(`  ${s.dim('·')} ${renderAgentEvent(ev)}`);
        }
      } catch { /* a transient poll failure must not kill the watch */ }
      await new Promise((r) => setTimeout(r, pollMs));
    }
  })();

  const stopPump = async () => {
    pumpStopped = true;
    await Promise.race([pump, new Promise((r) => setTimeout(r, pollMs + 500))]);
  };
  const unwatched = async () => {
    await stopPump();
    out(s.dim(`stopped watching — session ${id} keeps its state server-side; see it in the web UI, or cancel it there`));
    return { ok: false, status: 'unwatched' };
  };

  let session = session0;
  try {
    session = ((await api.post(`/v1/agent-sessions/${id}/run`, {}, driveOpts)) || {}).session || session;
    for (;;) {
      if (deps.isCancelled && deps.isCancelled()) return await unwatched();
      if (!(session && session.status === 'awaiting_approval' && session.pending_action)) break;
      const pa = session.pending_action;
      out('');
      out(s.yellow(`approval needed: ${describePendingAction(pa)}`));
      process.stdout.write('approve? [y/n] ');
      const ans = deps.nextLine ? await deps.nextLine() : null;
      if (deps.isCancelled && deps.isCancelled()) return await unwatched();
      const decision = /^y(es)?$/i.test(String(ans || '').trim()) ? 'approve' : 'deny';
      out(s.dim(`→ ${decision === 'approve' ? 'approved' : 'denied'}`));
      session = ((await api.post(`/v1/agent-sessions/${id}/approve`, { decision }, driveOpts)) || {}).session || session;
    }
  } catch (e) {
    await stopPump();
    if ((deps.isCancelled && deps.isCancelled()) || (e && e.name === 'AbortError')) return await unwatched();
    if (e && e.status === 404) {
      out(s.yellow('the server no longer has this agent session') + s.dim(' — it may have been cleaned up; check the web UI'));
      return { ok: false, unavailable: true };
    }
    throw e;
  }
  await stopPump();
  const status = String((session && session.status) || '');
  if (status === 'completed') {
    if (session.summary) out(session.summary);
    out(s.green('✓ agent session completed') + s.dim(` — ${session.steps_used ?? 0} steps · $${Number(session.spent_usd || 0).toFixed(2)}`));
    return { ok: true, status };
  }
  if (status === 'failed') {
    const f = session.failure || {};
    out(s.errorBox(new Error(`agent session failed — ${f.category || 'error'}: ${f.cause || 'see the web UI for the full session'}`),
      'check the model connection with `agentqon doctor`, or see the session in the web UI'));
    return { ok: false, status };
  }
  if (status === 'cancelled') {
    out(s.dim('agent session cancelled'));
    return { ok: false, status };
  }
  out(s.dim(`agent session is "${status || 'unknown'}" — see it in the web UI`));
  return { ok: false, status };
}

const CHAT_HELP = [
  'Type to chat. Slash commands:',
  '  /model            show the chat model + the catalog; /model <n|id> pins one, /model auto clears',
  '  /agent <goal>     run an agent session (tools, steps, approvals) toward a goal',
  '  /missions         recent missions',
  '  /doctor           health checklist',
  '  /help             this list',
  '  /exit             leave (Ctrl+C cancels a wait; twice at the prompt quits)',
].join('\n');

async function start(api, opts = {}) {
  const out = opts.out || ((t) => process.stdout.write(String(t) + '\n'));
  const history = [];
  let catalogCache = null;
  let pin = opts.pin || null;

  let intro = `connected to ${opts.base || ''}`;
  try {
    const ms = await api.get('/v1/model-state');
    if (ms && ms.connected) intro += ` — model: ${ms.model} (via ${ms.source || 'platform'})`;
    else intro += ' — no model connected yet: chat cannot answer until one is (run `agentqon setup`)';
  } catch { /* the first send will surface the real error */ }
  out(s.dim(intro + ' — /help for commands, /exit to leave'));

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: s.violet('chat ❯ '),
    terminal: Boolean(process.stdin.isTTY),
  });
  const queue = makeLineQueue(rl);
  /* Piped stdin auto-closes the interface at EOF even while
   * buffered lines are still being processed (an /agent run can
   * outlast the pipe) — prompting a closed interface throws
   * ERR_USE_AFTER_CLOSE, so every prompt goes through this guard. */
  let rlClosed = false;
  rl.on('close', () => { rlClosed = true; });
  const prompt = () => { if (!rlClosed) { try { rl.prompt(); } catch { /* closed mid-flight */ } } };

  let cancelCurrent = null;
  let sigints = 0;
  rl.on('SIGINT', () => {
    if (cancelCurrent) { cancelCurrent(); return; }
    if (sigints > 0) { rl.close(); return; }
    sigints++;
    out(s.dim('(Ctrl-C again to quit, or type /exit)'));
  });

  async function catalog() {
    const data = await api.get('/v1/models');
    catalogCache = (data && data.models) || [];
    return catalogCache;
  }

  async function modelCommand(arg) {
    if (arg === 'auto' || arg === 'clear') {
      pin = null;
      out(s.dim('chat model pin cleared — using the connected model'));
      return;
    }
    if (arg) {
      const models = catalogCache || await catalog();
      let chosen = null;
      if (/^\d+$/.test(arg)) {
        const idx = parseInt(arg, 10) - 1;
        if (idx >= 0 && idx < models.length) chosen = models[idx].model_id;
      } else if (models.some((m) => m.model_id === arg)) {
        chosen = arg;
      }
      if (!chosen) {
        out(s.errorBox(new Error(`no catalog model matches "${arg}"`), 'see the list with /model'));
        return;
      }
      pin = chosen;
      out(s.green(`✓ chat pinned to ${pin}`) + s.dim(' — for this session; /model auto clears'));
      return;
    }
    let current = null;
    try { current = await api.get('/v1/model-state'); } catch { /* list still prints */ }
    out(current && current.connected
      ? `${s.dim('current model')}  ${current.model} ${s.dim(`(via ${current.source || 'platform'})`)}${pin ? s.dim(` · chat pinned to ${pin}`) : ''}`
      : s.yellow('no model connected') + s.dim(' — connect one with `agentqon setup`'));
    const models = await catalog();
    if (!models.length) { out(s.dim('the model catalog is empty — validate a connection to sync it (web UI → Connections)')); return; }
    models.forEach((m, i) => out(`  ${i + 1}. ${m.model_id}${pin === m.model_id ? s.green('  ← pinned') : ''}`));
    out(s.dim('switch with /model <number or id> · /model auto for the connected default'));
  }

  async function sendChat(text) {
    const ac = new AbortController();
    cancelCurrent = () => ac.abort();
    const attempt = async (withPin) => {
      const spinner = new s.Spinner().start('thinking…');
      try {
        const body = { message: text, history };
        if (withPin && pin) body.model = pin;
        return await api.post('/v1/chat', body, { timeout: 300_000, signal: ac.signal });
      } finally { spinner.stop(); }
    };
    let res;
    try {
      try {
        res = await attempt(true);
      } catch (e) {
        if (pin && e && e.status === 422 && !ac.signal.aborted) {
          out(s.dim('this server build does not support chat model pins — continuing with the connected model'));
          pin = null;
          res = await attempt(false);
        } else throw e;
      }
    } catch (e) {
      if (ac.signal.aborted || (e && e.name === 'AbortError')) {
        out(s.dim('(cancelled — nothing was sent to history)'));
        return;
      }
      throw e;
    } finally {
      cancelCurrent = null;
    }
    history.push({ role: 'user', content: text }, { role: 'assistant', content: res.reply || '' });
    if (history.length > 20) history.splice(0, history.length - 20);
    out('');
    out(res.reply || '');
    out(s.dim(`— ${res.model || 'model'}${res.source ? ` via ${res.source}` : ''}`));
  }

  prompt();
  for (;;) {
    const line = await queue.next();
    if (line === null) break;
    const text = line.trim();
    sigints = 0;
    if (!text) { prompt(); continue; }
    try {
      if (text === '/exit' || text === '/quit') break;
      else if (text === '/help') out(CHAT_HELP);
      else if (text === '/missions') {
        const data = await api.get('/v1/missions?limit=15');
        out(render.renderMissions(data.missions || []));
      } else if (text === '/doctor') {
        await commands.cmdDoctor();
      } else if (text === '/model' || text.startsWith('/model ')) {
        await modelCommand(text.slice(6).trim());
      } else if (text === '/agent') {
        out(s.dim('usage: /agent <goal>'));
      } else if (text.startsWith('/agent ')) {
        let unwatched = false;
        const ac = new AbortController();
        cancelCurrent = () => { unwatched = true; ac.abort(); };
        try {
          await runAgentSession(api, text.slice(7).trim(), {
            out,
            pollMs: opts.agentPollMs,
            nextLine: () => queue.next(),
            isCancelled: () => unwatched,
            signal: ac.signal,
          });
        } finally { cancelCurrent = null; }
      } else if (text.startsWith('/')) {
        out(s.errorBox(new Error(`unknown command ${text.split(/\s+/)[0]}`), 'try /help'));
      } else {
        await sendChat(text);
      }
    } catch (e) {
      out(s.errorBox(e, chatFixHint(e)));
    }
    prompt();
  }
  rl.close();
  out(s.dim('— chat closed —'));
}

module.exports = { start, runAgentSession, makeLineQueue, renderAgentEvent, describePendingAction };
