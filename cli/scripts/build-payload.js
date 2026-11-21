#!/usr/bin/env node
'use strict';
/* Shield payload build (v20.2, spec §A Rung 1 + Rung 3 local-key mode).
 *
 * Runs on OUR side at pack time (wired as npm `prepack`, after
 * sync-server): compiles every Python file in cli/server/ to
 * hash-based .pyc bytecode and deletes the .py sources, then writes
 * a signed manifest — SHA-256 of every remaining payload file,
 * signed with the release Ed25519 key. The matching public key is
 * pinned in cli/lib/shield.js; the CLI refuses a bundle whose
 * manifest does not verify, before anything is installed or run.
 *
 * The private key NEVER enters the repo or the tarball: it is read
 * from AGENTQON_RELEASE_KEY_FILE or the default location beside the
 * Shield spec (~/workspace/omnimind-shield/). A build without the key
 * fails loudly — an unsigned payload must never ship.
 */
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const serverDir = path.join(__dirname, '..', 'server');
const pkg = require('../package.json');

const COMPILE_PY = `
import pathlib, py_compile, sys
root = pathlib.Path(sys.argv[1])
fails = []
count = 0
for p in sorted(root.rglob('*.py')):
    if '__pycache__' in p.parts:
        continue
    try:
        py_compile.compile(str(p), cfile=str(p.with_suffix('.pyc')),
                           doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        p.unlink()
        count += 1
    except Exception as e:
        fails.append(f'{p}: {e}')
print(f'compiled {count} files; failures: {len(fails)}')
for f in fails:
    print(f)
sys.exit(1 if fails else 0)
`;

function sha256Hex(buf) { return crypto.createHash('sha256').update(buf).digest('hex'); }

function walkFiles(root) {
  const out = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else out.push(path.relative(root, p).split(path.sep).join('/'));
    }
  })(root);
  return out.sort();
}

function main() {
  if (!fs.existsSync(path.join(serverDir, 'app'))) {
    throw new Error('cli/server is not synced — run `node scripts/sync-server.js` first');
  }
  // Idempotent rebuilds: drop any previous manifest before hashing.
  for (const f of ['manifest.json', 'manifest.sig']) {
    fs.rmSync(path.join(serverDir, f), { force: true });
  }
  const python = process.env.AGENTQON_BUILD_PYTHON || process.env.OMNIMIND_BUILD_PYTHON || 'python3';
  const ver = spawnSync(python, ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], { encoding: 'utf8' });
  if (ver.status !== 0) throw new Error(`cannot run build python (${python})`);
  const pythonMinor = ver.stdout.trim();
  /* v21.4.0: the payload is pinned to Python 3.12 — the runtime
   * refuses any other minor, and bytecode is minor-specific. A
   * build on whatever `python3` happens to be (3.11/3.13) used to
   * record that minor into the manifest and ship a release that
   * failed setup for every user. Refuse loudly, here, instead. */
  if (pythonMinor !== '3.12') {
    throw new Error(
      `build python must be exactly 3.12 — got ${pythonMinor} from "${python}". ` +
      'The payload is pinned to 3.12; point AGENTQON_BUILD_PYTHON at a ' +
      'Python 3.12 interpreter and rebuild.');
  }

  const compiled = spawnSync(python, ['-c', COMPILE_PY, serverDir], { encoding: 'utf8' });
  process.stdout.write(compiled.stdout || '');
  if (compiled.status !== 0) {
    throw new Error('bytecode compilation failed:\n' + (compiled.stderr || ''));
  }
  const leftovers = walkFiles(serverDir).filter((f) => f.endsWith('.py'));
  if (leftovers.length) {
    throw new Error('sources remain in the payload after compile: ' + leftovers.slice(0, 5).join(', '));
  }

  const files = {};
  for (const rel of walkFiles(serverDir)) {
    files[rel] = sha256Hex(fs.readFileSync(path.join(serverDir, ...rel.split('/'))));
  }
  const manifest = {
    product: 'agentqon',
    version: pkg.version,
    python: pythonMinor,
    built: new Date().toISOString(),
    mode: 'local-key',
    files,
  };
  const manifestBytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n', 'utf8');
  fs.writeFileSync(path.join(serverDir, 'manifest.json'), manifestBytes);

  const keyPath = process.env.AGENTQON_RELEASE_KEY_FILE || process.env.OMNIMIND_RELEASE_KEY_FILE ||
    path.join(os.homedir(), 'workspace', 'omnimind-shield', 'release-ed25519-private.pem');
  if (!fs.existsSync(keyPath)) {
    throw new Error(
      `release signing key not found at ${keyPath} — refusing to ship an ` +
      'unsigned payload. Set AGENTQON_RELEASE_KEY_FILE to the release ' +
      'Ed25519 private key (kept outside the repo, never in the tarball).');
  }
  const sig = crypto.sign(null, manifestBytes, fs.readFileSync(keyPath, 'utf8'));
  fs.writeFileSync(path.join(serverDir, 'manifest.sig'), sig.toString('base64') + '\n');
  console.log(`shield payload built: ${Object.keys(files).length} files, ` +
    `python ${pythonMinor}, manifest signed (key: ${path.basename(keyPath)})`);
}

main();
