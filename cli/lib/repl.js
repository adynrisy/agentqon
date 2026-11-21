'use strict';
/* Interactive chat REPL — the harness face of AgentQon.
 * Type a mission in plain words; slash commands reach the rest.
 * Exits cleanly on /exit, Ctrl-D (EOF) and Ctrl-C at an idle prompt.
 */
const readline = require('readline');
const s = require('./style');
const commands = require('./commands');
const missionLib = require('./mission');
const render = require('./render');

async function start(api, opts = {}) {
  const out = (t) => process.stdout.write(String(t) + '\n');
  out(s.dim(`connected to ${opts.base || ''} — type a mission, /help for commands, /exit to leave`));
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: s.violet('agentqon ❯ '),
    terminal: Boolean(process.stdin.isTTY),
  });

  let busy = false;
  let sigints = 0;
  rl.on('SIGINT', () => {
    if (busy && sigints === 0) {
      sigints++;
      out(s.dim('(mission running — Ctrl-C again to quit)'));
      return;
    }
    rl.close();
  });

  const runMission = async (text, runOpts) => {
    const detail = await missionLib.runMissionFlow(api, text, runOpts);
    let proof = null;
    if (detail.mission.status === 'completed') {
      try { proof = (await api.get(`/v1/missions/${detail.mission.id}/proof-pack`)).confidence_summary || null; } catch { /* garnish */ }
    }
    out(render.renderMissionResult(detail, proof));
  };

  rl.prompt();
  for await (const line of rl) {
    const text = line.trim();
    sigints = 0;
    if (!text) { rl.prompt(); continue; }
    busy = true;
    rl.pause();
    try {
      if (text === '/exit' || text === '/quit') break;
      else if (text === '/help') commands.cmdHelp();
      else if (text.startsWith('/agents')) {
        const type = text.split(/\s+/)[1];
        const data = await api.get(`/v1/agents/suitability?task_type=${encodeURIComponent(type || 'strategy')}`);
        out(render.renderAgents((data.ranking || []).slice(0, 10), data.task_type || 'strategy'));
      } else if (text === '/missions') {
        const data = await api.get('/v1/missions?limit=15');
        out(render.renderMissions(data.missions || []));
      } else if (text.startsWith('/proof ')) {
        const pack = await commands.cmdProof(text.slice(7).trim());
        void pack;
      } else if (text.startsWith('/arena')) {
        const promptText = text.slice(6).trim();
        if (!promptText) out(s.dim('usage: /arena <task text>'));
        else await commands.cmdArena(promptText, {});
      } else if (text === '/schedules') {
        const data = await api.get('/v1/schedules');
        out(render.renderSchedules(data.schedules || []));
      } else if (text.startsWith('/simulate ')) {
        await commands.cmdSimulate(text.slice(10).trim());
      } else if (text.startsWith('/')) {
        out(s.errorBox(new Error(`unknown command ${text.split(/\s+/)[0]}`), 'try /help'));
      } else {
        await runMission(text, {});
      }
    } catch (e) {
      out(s.errorBox(e));
    } finally {
      busy = false;
      rl.resume();
      rl.prompt();
    }
  }
  out(s.dim('— agentqon session closed —'));
}

module.exports = { start };
