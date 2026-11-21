'use strict';
/* Extended Library Pack (v22.2) — the CLI half.
 *
 * The npm package ships 4,840 skills. A further 213
 * security-research skills (the cluster removed in v22.1.3 so the
 * package could pass npm's approval gate) are offered as an
 * OPTIONAL download that npm never carries:
 *
 *   agentqon setup                     offers it once, default no
 *   agentqon skills extended           status — or install, if asked
 *   agentqon skills extended --install download + install it
 *   agentqon skills extended --remove  delete it, cleanly
 *
 * Trust, with no silent accept and no fallback: the pack travels
 * with a manifest (file list + per-file SHA-256 + tarball hash)
 * signed with a DEDICATED Ed25519 pack key. The public key and
 * the v1 tarball's SHA-256 are pinned below (mirrored in
 * app/extended_pack.py). Signature wrong, hash wrong, a file
 * missing or extra — the install is refused and nothing lands.
 *
 * The pack lands ONLY in the user's data home
 * (<config home>/extended-library/) — never inside the npm
 * package. The local server merges it into the Skill Library
 * from there and reports the counts separately (4,840 in the
 * box · 5,053 with the extended library).
 */
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const cfgMod = require('./config');
const shield = require('./shield');
const promptMod = require('./prompt');
const s = require('./style');

/* The ONE place the pack's home on the web is named (mirror of
 * app/extended_pack.py EXTENDED_PACK_BASE_URL). The three assets
 * sit side by side in the same release; if the host moves, change
 * this base — every URL derives from it. AGENTQON_EXTENDED_PACK_URL
 * overrides the base (used by tests with a local stand-in). */
const EXTENDED_PACK_BASE_URL =
  'https://github.com/adynrisy/agentqon/releases/download/extended-library-v1';
const EXTENDED_PACK_TARBALL_NAME = 'extended-library-v1.tar.gz';
const EXTENDED_PACK_SKILL_COUNT = 213;
const BOXED_SKILL_TOTAL = 4840;

const EXTENDED_PACK_PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbN9LGhsHNUaII6P6BxIbWoaNKV+34G92Ai8hKe25FH8=
-----END PUBLIC KEY-----
`;
const EXTENDED_PACK_TARBALL_SHA256 =
  'b8b26b163a8e28c998e58e2994f23652badcaf8b6486598fbd652be98e96c678';

const QUESTION =
  'Extended library: 213 security-research skills that npm does not carry. ' +
  'Download them now? (yes/no)';

/* ------------------------------------------------------------ paths */
function extendedDir(deps = {}) {
  if (deps.extendedDir) return path.resolve(deps.extendedDir);
  const home = deps.homeDir ? path.resolve(deps.homeDir) : cfgMod.configDir();
  return path.join(home, 'extended-library');
}

function packStatus(deps = {}) {
  const dir = extendedDir(deps);
  const manifestPath = path.join(dir, 'manifest.json');
  const skillsDir = path.join(dir, 'skills');
  if (!fs.existsSync(manifestPath) || !fs.existsSync(skillsDir)) {
    return { installed: false, dir };
  }
  let count = EXTENDED_PACK_SKILL_COUNT;
  try {
    const m = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (Number.isFinite(m.count)) count = m.count;
  } catch { /* the pinned count stands */ }
  return { installed: true, dir, count };
}

/* ----------------------------------------------------------- fetch */
function packBase(deps = {}) {
  return deps.packBaseUrl || process.env.AGENTQON_EXTENDED_PACK_URL
    || EXTENDED_PACK_BASE_URL;
}

function assetLocation(base, name) {
  if (/^https?:\/\//i.test(base)) return `${base.replace(/\/+$/, '')}/${name}`;
  if (base.startsWith('file://')) return `${base.replace(/\/+$/, '')}/${name}`;
  return path.join(base, name);
}

/* Fetch one pack asset as a Buffer. deps.fetchImpl(url) may be
 * injected (tests); the default reads local paths / file:// URLs
 * straight from disk and everything else over HTTPS. */
async function fetchAsset(base, name, deps = {}) {
  const loc = assetLocation(base, name);
  if (deps.fetchImpl) return deps.fetchImpl(loc, name);
  if (!/^https?:\/\//i.test(loc)) {
    const p = loc.startsWith('file://') ? new URL(loc).pathname : loc;
    return fs.readFileSync(p);
  }
  const res = await fetch(loc);
  if (!res.ok) throw new Error(`download failed (HTTP ${res.status})`);
  return Buffer.from(await res.arrayBuffer());
}

/* ---------------------------------------------------------- verify */
function refuse(reason) {
  return new Error(
    `the extended library failed its safety check (${reason}) — ` +
    'nothing was installed');
}

function verifyManifestAndTarball(manifestBytes, sigText, tarballBytes,
                                  deps = {}) {
  const pubKey = deps.publicKeyPem || EXTENDED_PACK_PUBLIC_KEY_PEM;
  const sig = Buffer.from(String(sigText || '').trim(), 'base64');
  let ok = false;
  try { ok = crypto.verify(null, manifestBytes, pubKey, sig); } catch { ok = false; }
  if (!ok) throw refuse('the signature did not match');
  let manifest;
  try { manifest = JSON.parse(manifestBytes.toString('utf8')); }
  catch { throw refuse('the pack list was not readable'); }
  const wantCount = deps.expectedSkillCount || EXTENDED_PACK_SKILL_COUNT;
  if (!manifest || manifest.pack !== 'extended-library'
      || manifest.version !== 1
      || manifest.count !== wantCount
      || !manifest.files || typeof manifest.files !== 'object') {
    throw refuse('the pack contents were not what was promised');
  }
  const pinned = deps.expectedTarballSha256 || EXTENDED_PACK_TARBALL_SHA256;
  if (manifest.tarball_sha256 !== pinned) {
    throw refuse('the pack fingerprint did not match the pinned one');
  }
  if (shield.sha256Hex(tarballBytes) !== manifest.tarball_sha256) {
    throw refuse('the downloaded pack did not match its fingerprint');
  }
  return manifest;
}

function verifyExtractedTree(stageDir, manifest) {
  const listed = new Set(Object.keys(manifest.files));
  for (const rel of listed) {
    const p = path.join(stageDir, ...rel.split('/'));
    if (!fs.existsSync(p)) throw refuse(`a promised file was missing: ${rel}`);
    if (shield.sha256Hex(fs.readFileSync(p)) !== manifest.files[rel]) {
      throw refuse(`a file did not match its recorded fingerprint: ${rel}`);
    }
  }
  for (const rel of shield.walkFiles(stageDir)) {
    if (!listed.has(rel)) throw refuse(`an unlisted file rode along: ${rel}`);
  }
}

/* ---------------------------------------------------------- install */
async function installExtended(deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const st = packStatus(deps);
  if (st.installed) {
    out(s.green(`✓ the extended library is already installed`) +
      s.dim(` — ${st.count} extra skills, at ${st.dir}`));
    return { installed: true, already: true, count: st.count };
  }
  const base = packBase(deps);
  let manifestBytes; let sigText; let tarballBytes;
  try {
    manifestBytes = await fetchAsset(base, 'manifest.json', deps);
    sigText = (await fetchAsset(base, 'manifest.sig', deps)).toString('utf8');
    tarballBytes = await fetchAsset(base, EXTENDED_PACK_TARBALL_NAME, deps);
  } catch (e) {
    throw new Error(
      'the extended library could not be downloaded — check the ' +
      'network connection, then try again with ' +
      '`agentqon skills extended --install`');
  }
  const manifest = verifyManifestAndTarball(
    manifestBytes, sigText, tarballBytes, deps);

  const dir = extendedDir(deps);
  const stage = `${dir}.installing-${process.pid}`;
  const tmpTar = path.join(os.tmpdir(), `agentqon-extended-${process.pid}.tgz`);
  fs.rmSync(stage, { recursive: true, force: true });
  try {
    fs.mkdirSync(stage, { recursive: true, mode: 0o700 });
    fs.writeFileSync(tmpTar, tarballBytes);
    const untar = spawnSync('tar', ['-xzf', tmpTar, '-C', stage],
      { encoding: 'utf8' });
    if (untar.status !== 0) throw refuse('the download could not be opened');
    verifyExtractedTree(stage, manifest);
    fs.writeFileSync(path.join(stage, 'manifest.json'), manifestBytes);
    fs.rmSync(dir, { recursive: true, force: true });
    fs.renameSync(stage, dir);
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
    fs.rmSync(tmpTar, { force: true });
  }
  out(s.green(`✓ extended library installed`) +
    s.dim(` — ${manifest.count} security-research skills added`));
  out(s.dim(`your library is now ${(BOXED_SKILL_TOTAL + manifest.count).toLocaleString()} skills ` +
    `(${BOXED_SKILL_TOTAL.toLocaleString()} in the box + ${manifest.count} from the extended library). ` +
    'A running server picks the pack up straight away.'));
  return { installed: true, already: false, count: manifest.count, dir };
}

/* ----------------------------------------------------------- remove */
function removeExtended(deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const st = packStatus(deps);
  if (!st.installed) {
    out(s.dim('the extended library is not installed — nothing to remove'));
    return { removed: false };
  }
  fs.rmSync(st.dir, { recursive: true, force: true });
  out(s.green('✓ extended library removed') +
    s.dim(` — your library is back to the ${BOXED_SKILL_TOTAL.toLocaleString()} skills in the box`));
  return { removed: true };
}

/* ------------------------------------------------------------ panel */
function statusPanel(deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const st = packStatus(deps);
  if (st.installed) {
    out(s.panel('📚 Extended Library', [
      s.green('installed') + s.dim(` — ${st.count} security-research skills, on top of the ${BOXED_SKILL_TOTAL.toLocaleString()} in the box`),
      s.dim(`your library: ${(BOXED_SKILL_TOTAL + st.count).toLocaleString()} skills in total`),
      s.dim(`stored at ${st.dir} — your data home, never inside the app package`),
      '',
      s.dim(`remove it any time with \`agentqon skills extended --remove\``),
    ]));
  } else {
    out(s.panel('📚 Extended Library', [
      s.yellow('not installed'),
      s.dim(`${EXTENDED_PACK_SKILL_COUNT} security-research skills that npm does not carry,`),
      s.dim('offered as an optional download — signed, and checked before it lands.'),
      '',
      `your library: ${s.bold(`${BOXED_SKILL_TOTAL.toLocaleString()} skills in the box`)}` +
        s.dim(` · ${(BOXED_SKILL_TOTAL + EXTENDED_PACK_SKILL_COUNT).toLocaleString()} with the extended library`),
      '',
      s.dim('install it with `agentqon skills extended --install`'),
    ]));
  }
  return st;
}

/* ----------------------------------------------------- subcommand */
async function runExtended(args, deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const flags = deps.flags || {};
  const argv = Array.isArray(args) ? args : [];
  if (flags.remove || argv.includes('--remove')) return removeExtended(deps);
  if (flags.install || argv.includes('--install')) {
    return installExtended(deps);
  }
  const st = packStatus(deps);
  if (st.installed) return statusPanel(deps);
  /* Not installed: on an interactive terminal, offer the choice
   * (default no). Piped or scripted runs get the status and the
   * exact install command — never a silent download. */
  const isTTY = deps.isTTY !== undefined ? deps.isTTY : !!process.stdin.isTTY;
  if (!isTTY) return statusPanel(deps);
  statusPanel(deps);
  const ask = deps.ask || promptMod.ask;
  const answer = String(await ask(QUESTION, 'no')).trim().toLowerCase();
  if (answer === 'yes' || answer === 'y') return installExtended(deps);
  out(s.dim('not installed — your library stays at the ' +
    `${BOXED_SKILL_TOTAL.toLocaleString()} skills in the box`));
  return { installed: false, declined: true };
}

/* --------------------------------------------------- setup offer */
async function maybeOfferDuringSetup(deps = {}) {
  const out = deps.out || ((t) => process.stdout.write(String(t) + '\n'));
  const isTTY = deps.isTTY !== undefined ? deps.isTTY : !!process.stdin.isTTY;
  if (!isTTY) return { offered: false, reason: 'not-interactive' };
  if (packStatus(deps).installed) return { offered: false, reason: 'already-installed' };
  const ask = deps.ask || promptMod.ask;
  const answer = String(await ask(QUESTION, 'no')).trim().toLowerCase();
  if (answer !== 'yes' && answer !== 'y') {
    out(s.dim('skipped — your library stays at the ' +
      `${BOXED_SKILL_TOTAL.toLocaleString()} skills in the box; add the ` +
      'extended library any time with `agentqon skills extended`'));
    return { offered: true, installed: false, declined: true };
  }
  try {
    await installExtended(deps);
    return { offered: true, installed: true };
  } catch (e) {
    /* A failed optional download must never fail setup. */
    out(s.yellow('the extended library was not installed') +
      s.dim(` — ${(e && e.message) || 'the download did not complete'}. ` +
        'Setup carries on as normal; try again any time with ' +
        '`agentqon skills extended`'));
    return { offered: true, installed: false, error: e };
  }
}

module.exports = {
  EXTENDED_PACK_BASE_URL, EXTENDED_PACK_TARBALL_NAME,
  EXTENDED_PACK_SKILL_COUNT, BOXED_SKILL_TOTAL,
  EXTENDED_PACK_PUBLIC_KEY_PEM, EXTENDED_PACK_TARBALL_SHA256, QUESTION,
  extendedDir, packStatus, installExtended, removeExtended,
  statusPanel, runExtended, maybeOfferDuringSetup,
  verifyManifestAndTarball,
};
