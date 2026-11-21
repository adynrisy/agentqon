'use strict';
/* AgentQon CLI config — ~/.agentqon/config.json, directory 0700, file 0600.
 * Holds the server URL and the session token issued by POST /v1/auth/login
 * (the same opaque `omk_` token the web UI uses). The token is never
 * printed by any command. AGENTQON_HOME overrides the config directory.
 *
 * Rebrand bridge (OmniMind -> AgentQon):
 *  - AGENTQON_* is the preferred env prefix; every AGENTQON_<X> is
 *    mirrored onto OMNIMIND_<X> below so existing OMNIMIND_* read sites
 *    (and OMNIMIND_HOME) keep working as a legacy fallback.
 *  - On first run, if ~/.agentqon does not exist but the legacy
 *    ~/.omnimind does, the user's state (config, credentials, data,
 *    logs) is copied over. Installed code (server/, venv/, run/) is
 *    NOT copied — setup rebuilds it — and the old directory is never
 *    deleted.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

for (const name of Object.keys(process.env)) {
  if (name.startsWith('AGENTQON_')) {
    process.env['OMNIMIND_' + name.slice('AGENTQON_'.length)] = process.env[name];
  }
}

const DEFAULT_SERVER = 'http://127.0.0.1:8000'; // managed local server default port

const LEGACY_COPY_SKIP = new Set(['venv', 'server', 'run', '.stage', 'server.pid']);

function migrateLegacyHome(dir) {
  try {
    if (fs.existsSync(dir)) return;
    const legacy = path.join(os.homedir(), '.omnimind');
    if (!fs.existsSync(legacy)) return;
    const tmp = `${dir}.migrating-${process.pid}`;
    fs.rmSync(tmp, { recursive: true, force: true });
    fs.mkdirSync(tmp, { recursive: true, mode: 0o700 });
    for (const entry of fs.readdirSync(legacy)) {
      if (LEGACY_COPY_SKIP.has(entry)) continue;
      fs.cpSync(path.join(legacy, entry), path.join(tmp, entry), { recursive: true });
    }
    fs.renameSync(tmp, dir);
  } catch { /* best effort — save() creates a fresh home if this failed */ }
}

function configDir() {
  if (process.env.AGENTQON_HOME) return process.env.AGENTQON_HOME;
  if (process.env.OMNIMIND_HOME) return process.env.OMNIMIND_HOME;
  const dir = path.join(os.homedir(), '.agentqon');
  migrateLegacyHome(dir);
  return dir;
}
function configPath() { return path.join(configDir(), 'config.json'); }

function load() {
  const p = configPath();
  let raw;
  try {
    raw = fs.readFileSync(p, 'utf8');
  } catch {
    return { server: DEFAULT_SERVER }; // no config yet — a normal first run
  }
  try {
    return { server: DEFAULT_SERVER, ...JSON.parse(raw) };
  } catch {
    /* A corrupt config must not silently reset the user to a
     * "mysterious fresh install": move the bad file aside (nothing
     * is deleted), say exactly what happened, and start from
     * defaults. Server data, credentials and payload are separate
     * files and are untouched by this. */
    const quarantine = `${p}.corrupt-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    try { fs.renameSync(p, quarantine); } catch { /* best effort */ }
    process.stderr.write(
      `agentqon: ${p} was not valid JSON and has been moved aside to ${quarantine}; ` +
      'starting with default settings (your server, data and credentials are untouched). ' +
      'If you had a remote server configured, re-run `agentqon login`.\n');
    return { server: DEFAULT_SERVER };
  }
}

function save(cfg) {
  const dir = configDir();
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort */ }
  const p = configPath();
  /* Atomic write: a crash mid-save must never leave a torn
   * config.json (it carries the session token) — write a sibling
   * temp file, then rename over the target in one operation. */
  const tmp = `${p}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(tmp, p);
  try { fs.chmodSync(p, 0o600); } catch { /* best effort */ }
  return p;
}

function exists() { return fs.existsSync(configPath()); }

module.exports = { DEFAULT_SERVER, configDir, configPath, load, save, exists };
