'use strict';
/* Skill Library (v22.1, contract S4 — CLI half).
 *
 * The harness ships an on-device library of community skills
 * (SKILL.md playbooks harvested from public collections, each with
 * its source repo and license recorded). Missions and agent
 * sessions pull the skills that match a task automatically; this
 * command family is the human's window onto the same library:
 *
 *   agentqon skills                          overview + how to search
 *   agentqon skills search <words…>          ranked matches
 *   agentqon skills show <id>                full text + provenance
 *   agentqon skills pin / unpin <id>         favourites, offered first
 *
 * Everything goes through the caller's Api (commands.getApi — the
 * running server is reused, never duplicated). Servers from before
 * the library existed answer 404; that gets the honest "not
 * available in this server build" panel + the exact update command,
 * never an error dump — the evolve-report pattern.
 */
const s = require('./style');

const SEARCH_LIMIT = 20;

function fmtSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function unavailablePanel(out) {
  out(s.panel('📚 Skill Library', [
    s.yellow('not available in this server build'),
    s.dim('the Skill Library ships with the v22.1 platform — update with'),
    s.dim('`npm install -g agentqon@latest`, then run `agentqon skills` again'),
  ]));
  return null;
}

/* One library call with the shared 404 → honest-fallback handling.
 * Returns { ok: true, data } or { ok: false } after printing the
 * fallback panel; any other failure propagates to the CLI's
 * top-level error surface, like every other command. */
async function libCall(api, out, fn) {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e && e.status === 404) { unavailablePanel(out); return { ok: false }; }
    throw e;
  }
}

function metaLine(skill) {
  const bits = [skill.repo || 'unknown source'];
  if (skill.license) bits.push(skill.license);
  const size = fmtSize(skill.body_bytes);
  if (size) bits.push(size);
  if (skill.pack === 'extended') bits.push('extended library');
  return bits.join(' · ');
}

/* ---------------- overview ---------------- */
async function skillsOverview(deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const r = await libCall(deps.api, out, () => deps.api.get('/v1/skills/facets'));
  if (!r.ok) return null;
  const facets = r.data || {};
  const repos = (Array.isArray(facets.repos) ? [...facets.repos] : [])
    .sort((a, b) => (b.count || 0) - (a.count || 0));
  /* Honest two-number counting (v22.2): when the server reports
   * the split, the overview states it in both states — boxed
   * alone, and boxed + the optional extended library. */
  const countsLine = facets.extended_installed
    ? `${s.bold(Number(facets.total || 0).toLocaleString())} skills in the library — ${Number(facets.boxed_total || 0).toLocaleString()} in the box + ${Number(facets.extended_total || 0).toLocaleString()} from the extended library · ${Number(facets.unique_names || 0).toLocaleString()} unique names`
    : (Number.isFinite(facets.boxed_total) && Number.isFinite(facets.total_with_extended)
      ? `${s.bold(Number(facets.total || 0).toLocaleString())} skills in the box · ${Number(facets.total_with_extended || 0).toLocaleString()} with the extended library · ${Number(facets.unique_names || 0).toLocaleString()} unique names`
      : `${s.bold(Number(facets.total || 0).toLocaleString())} skills in the library · ${Number(facets.unique_names || 0).toLocaleString()} unique names`);
  const lines = [
    countsLine,
    s.dim('every skill carries its source collection and license — community'),
    s.dim('skills are credited to their authors, never presented as built-in.'),
    '',
    s.bold('Biggest collections'),
    ...(repos.length
      ? repos.slice(0, 8).map((x) => `  · ${x.repo}${s.dim(` — ${Number(x.count || 0).toLocaleString()} skills`)}`)
      : [s.dim('  (none reported)')]),
    ...(repos.length > 8 ? [s.dim(`  · … and ${repos.length - 8} more collections`)] : []),
    '',
    s.bold('Use it'),
    `  search   ${s.cyan('agentqon skills search <words…>')}  ${s.dim('(add --repo owner/name to stay inside one collection)')}`,
    `  read     ${s.cyan('agentqon skills show <id>')}`,
    `  pin      ${s.cyan('agentqon skills pin <id>')}  ${s.dim('— pinned skills are offered first when they match a task')}`,
    `  extended ${s.cyan('agentqon skills extended')}  ${s.dim('— the optional extended library: 213 security-research skills npm does not carry')}`,
    '',
    s.dim('Missions and agent sessions also draw on this library on their'),
    s.dim('own: the skills matching the work are pulled in automatically.'),
  ];
  out(s.panel('📚 Skill Library', lines));
  return facets;
}

/* ---------------- search ---------------- */
async function skillsSearch(query, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const q = String(query || '').trim();
  if (!q) throw new Error('usage: agentqon skills search <words…> — e.g. agentqon skills search code review');
  const params = [`q=${encodeURIComponent(q)}`, `limit=${SEARCH_LIMIT}`];
  if (deps.repo) params.push(`repo=${encodeURIComponent(deps.repo)}`);
  const r = await libCall(deps.api, out, () => deps.api.get(`/v1/skills?${params.join('&')}`));
  if (!r.ok) return null;
  const data = r.data || {};
  const skills = Array.isArray(data.skills) ? data.skills : [];
  const total = Number(data.total ?? skills.length);
  if (!skills.length) {
    out(s.panel('📚 Skill Library', [
      s.yellow(`No skills match '${q}'${deps.repo ? ` in ${deps.repo}` : ''}`),
      s.dim('try broader words — one or two plain words about the job usually'),
      s.dim('work better than a whole sentence.'),
    ]));
    return data;
  }
  const lines = [];
  for (const sk of skills) {
    const name = sk.display_name || sk.name || sk.id;
    lines.push(`${sk.pinned ? s.yellow('★ ') : ''}${s.bold(name)}${sk.id ? s.dim(`  (${sk.id})`) : ''}`);
    if (sk.description) lines.push(`  ${String(sk.description).slice(0, 110)}`);
    lines.push(`  ${s.dim(metaLine(sk))}${sk.pinned ? s.dim(' · pinned') : ''}`);
  }
  lines.push('');
  lines.push(s.dim(total > skills.length
    ? `showing the top ${skills.length} of ${total.toLocaleString()} matches — add more words to narrow it down`
    : `${total.toLocaleString()} match${total === 1 ? '' : 'es'} — read one with \`agentqon skills show <id>\``));
  out(s.panel(`📚 Skills matching '${q}'${deps.repo ? ` in ${deps.repo}` : ''}`, lines));
  return data;
}

/* ---------------- show ---------------- */
async function skillsShow(id, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const sid = String(id || '').trim();
  if (!sid) throw new Error('usage: agentqon skills show <id> — find ids with `agentqon skills search <words…>`');
  const r = await libCall(deps.api, out, () => deps.api.get(`/v1/skills/${encodeURIComponent(sid)}`));
  if (!r.ok) return null;
  const skill = (r.data && r.data.skill) || {};
  const name = skill.display_name || skill.name || sid;
  const lines = [
    `${s.dim('name')}     ${s.bold(name)}${skill.pinned ? s.yellow('  ★ pinned') : ''}`,
    `${s.dim('source')}   ${skill.repo || '—'}`,
    ...(skill.path ? [`${s.dim('path')}     ${skill.path}`] : []),
    ...(skill.url ? [`${s.dim('url')}      ${skill.url}`] : []),
    `${s.dim('license')}  ${skill.license || 'not stated in the source collection'}`,
    `${s.dim('size')}     ${fmtSize(skill.body_bytes) || '—'} in the library`,
    ...(skill.pack === 'extended' ? [`${s.dim('pack')}     extended library — the optional download, not part of the npm package`] : []),
    `${s.dim('pinned')}   ${skill.pinned ? 'yes — offered first when it matches a task' : `no — pin it with \`agentqon skills pin ${sid}\``}`,
  ];
  out(s.panel('📚 Skill', lines));
  out('');
  if (skill.body) {
    out(String(skill.body).replace(/\s+$/, ''));
  } else {
    out(s.dim('(this skill has no body text in the library)'));
  }
  if (skill.truncated) {
    out('');
    out(s.yellow('Note: this skill\'s text is truncated in the library — the full original is at the source URL above.'));
  }
  return skill;
}

/* ---------------- pin / unpin ---------------- */
async function skillsPin(id, pinned, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const sid = String(id || '').trim();
  if (!sid) throw new Error(`usage: agentqon skills ${pinned ? 'pin' : 'unpin'} <id> — find ids with \`agentqon skills search <words…>\``);
  const r = await libCall(deps.api, out, () => deps.api.post(`/v1/skills/${encodeURIComponent(sid)}/pin`, { pinned }));
  if (!r.ok) return null;
  const now = r.data && typeof r.data.pinned === 'boolean' ? r.data.pinned : pinned;
  if (now) {
    out(s.green(`✓ pinned ${sid}`) + s.dim(' — pinned skills are offered first when they match a task'));
  } else {
    out(s.green(`✓ unpinned ${sid}`) + s.dim(' — it stays in the library and can still match tasks normally'));
  }
  return r.data;
}

/* ---------------- subcommand router ---------------- */
async function runSkills(args, deps = {}) {
  const argv = Array.isArray(args) ? args : [];
  const sub = argv[0];
  if (sub === undefined) return skillsOverview(deps);
  if (sub === 'search') return skillsSearch(argv.slice(1).join(' '), deps);
  if (sub === 'show') return skillsShow(argv[1], deps);
  if (sub === 'pin') return skillsPin(argv[1], true, deps);
  if (sub === 'unpin') return skillsPin(argv[1], false, deps);
  if (sub === 'extended') return require('./extended').runExtended(argv.slice(1), deps);
  throw new Error(`unknown skills command "${sub}" — use \`agentqon skills\`, \`agentqon skills search <words…>\`, \`agentqon skills show <id>\`, \`agentqon skills pin/unpin <id>\` or \`agentqon skills extended\``);
}

module.exports = { runSkills, skillsOverview, skillsSearch, skillsShow, skillsPin, fmtSize };
