'use strict';
/* Renderers: turn API payloads into styled terminal output.
 * Chat-first philosophy: the outcome leads; cost is a dim footnote.
 */
const s = require('./style');

const money = (n) => `$${Number(n || 0).toFixed(2)}`;

function candidateMean(detail, candidate) {
  const ev = (detail.evaluations || []).find((e) => e.candidate_id === candidate.candidate_id);
  if (!ev || !ev.dimensions) return { ev: null, mean: null };
  const dims = Object.entries(ev.dimensions);
  if (!dims.length) return { ev, mean: null };
  return { ev, mean: dims.reduce((a, [, d]) => a + Number(d.score || 0), 0) / dims.length };
}

function renderMissionResult(detail, proofSummary) {
  const m = detail.mission;
  const out = [];
  out.push('');
  out.push(s.panel(`${s.dot(m.status)}  ${m.title}`, [
    `${s.dim('status')}  ${s.statusText(m.status)}   ${s.dim('task')}  ${m.contract && m.contract.task_type ? m.contract.task_type : 'strategy'}   ${s.dim('id')}  ${s.grey(m.id)}`,
  ]));

  const candidates = detail.candidates || [];
  let best = null;
  const scored = candidates.map((c) => {
    const { ev, mean } = candidateMean(detail, c);
    if (mean !== null && (best === null || mean > best.mean)) best = { c, mean };
    return { c, ev, mean };
  });

  for (const { c, ev, mean } of scored) {
    const isBest = best && best.c === c && candidates.length > 1;
    const title = `${isBest ? s.violet('★ ') : ''}${c.title}${isBest ? s.violet('  · recommended') : ''}`;
    const lines = [`${s.dim('strategy')}  ${c.strategy}   ${s.dim('by')}  ${s.grey(c.agent_version_id)}`];
    if (c.summary) lines.push('', c.summary);
    if (ev && ev.dimensions) {
      lines.push('');
      for (const [name, d] of Object.entries(ev.dimensions)) {
        lines.push(`${name.padEnd(18)} ${s.bar(d.score, d.max || 5)}  ${Number(d.score).toFixed(1)}/${d.max || 5}`);
      }
      if (mean !== null) lines.push(`${s.bold('overall'.padEnd(18))} ${s.bar(mean, 5)}  ${s.bold(mean.toFixed(2) + '/5')}`);
    }
    if (c.deliverable) {
      lines.push('', s.dim('— deliverable —'), c.deliverable);
    }
    const claims = c.claims || [];
    if (claims.length) lines.push('', s.dim(`claims on record: ${claims.length}`));
    out.push(s.panel(title, lines, { color: isBest ? s.C.violet : s.C.purple }));
  }

  const critiques = (detail.critiques || []).filter((x) => x.severity === 'high');
  if (critiques.length) {
    out.push(s.orange(`⚠ critic raised ${critiques.length} high-severity finding${critiques.length > 1 ? 's' : ''}:`));
    for (const cr of critiques.slice(0, 3)) out.push('  ' + s.wrap(`• ${cr.issue}`, s.termWidth() - 2).join('\n  '));
  }

  if (proofSummary) {
    const cs = proofSummary;
    out.push('');
    out.push(`${s.bold('Proof')}  ${s.green('● verified ' + cs.verified)}   ${s.yellow('● estimated ' + cs.estimated)}   ${s.grey('● assumed ' + cs.assumed)}` +
      (cs.overall ? s.dim(`   overall confidence: ${cs.overall}`) : ''));
  }

  const usage = detail.usage || {};
  const spent = usage.total_usd !== undefined ? usage.total_usd : m.spent;
  /* v21.3.0 simplification law: the internal per-mission ceiling is
   * runaway protection only and never appears in the user's face —
   * the summary shows what the run cost, nothing it was "of". */
  out.push(s.footnote(`run cost ${money(spent)} · mission ${m.id} · full evidence pack: agentqon proof ${m.id}`));
  return out.join('\n');
}

function renderAgents(ranking, taskType) {
  const rows = ranking.map((a, i) => [
    String(i + 1),
    a.agent_id,
    s.bar(a.score, 1) + ' ' + Number(a.score).toFixed(2),
    a.measured_eval !== null && a.measured_eval !== undefined ? Number(a.measured_eval).toFixed(2) : s.dim('unmeasured'),
    String(a.runs ?? 0),
    String(a.selections ?? 0),
  ]);
  return `${s.bold(`Suitability — ${taskType}`)}  ${s.dim('(score blends capability fit, quality prior and the measured ledger)')}\n` +
    s.table(['#', 'agent', 'suitability', 'mean eval', 'runs', 'picked'], rows);
}

function renderStats(stats, leaderboard) {
  const out = [];
  const rows = (stats || []).slice(0, 15).map((x) => [
    x.agent_version_id, x.task_type, String(x.runs ?? 0),
    x.mean_eval !== null && x.mean_eval !== undefined ? Number(x.mean_eval).toFixed(2) : s.dim('—'),
    String(x.selections ?? 0), String(x.arena_wins ?? 0) + '/' + String(x.arena_losses ?? 0),
  ]);
  out.push(s.bold('Agent performance ledger'));
  out.push(rows.length ? s.table(['agent', 'task', 'runs', 'mean eval', 'picked', 'arena W/L'], rows) : s.dim('no runs recorded yet'));
  const entries = (leaderboard && leaderboard.entries) || [];
  if (entries.length) {
    out.push('', s.bold('Arena leaderboard'));
    out.push(s.table(['#', 'agent', 'win rate', 'duels', 'ranked'],
      entries.map((e, i) => [String(i + 1), e.agent_id, (e.win_rate * 100).toFixed(0) + '%', String(e.duels), e.ranked ? s.green('ranked') : s.dim('provisional')])));
  }
  return out.join('\n');
}

function renderMissions(missions) {
  const rows = missions.map((m) => [
    s.grey(String(m.id).slice(0, 18)),
    String(m.title || '').slice(0, 44),
    s.statusText(m.status),
    `${money(m.spent)}`,
  ]);
  return s.table(['id', 'title', 'status', 'spent'], rows.length ? rows : [['', s.dim('no missions yet'), '', '']]);
}

function cadenceText(c) {
  if (!c) return '';
  if (c.kind === 'interval_hours') return `every ${c.every_hours}h`;
  if (c.kind === 'daily') return `daily ${c.time_of_day} UTC`;
  if (c.kind === 'weekly') return `${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][c.weekday ?? 0]} ${c.time_of_day} UTC`;
  return c.kind;
}

function renderSchedules(schedules) {
  const rows = schedules.map((x) => [
    s.grey(String(x.schedule_id || '').slice(0, 18)),
    String(x.title || '').slice(0, 36),
    cadenceText(x.cadence),
    x.enabled ? s.green('enabled') : s.dim('paused'),
    x.next_run_at ? String(x.next_run_at).replace('T', ' ').slice(0, 16) : s.dim('—'),
  ]);
  return s.table(['id', 'title', 'cadence', 'state', 'next run (UTC)'], rows.length ? rows : [['', s.dim('no schedules yet'), '', '', '']]);
}

function renderDuelSide(label, output) {
  return s.panel(label, [output || s.dim('(no output)')], { color: s.C.purple });
}

module.exports = {
  money, renderMissionResult, renderAgents, renderStats,
  renderMissions, renderSchedules, renderDuelSide, cadenceText,
};
