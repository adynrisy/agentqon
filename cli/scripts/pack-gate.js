#!/usr/bin/env node
'use strict';
/* Shield pack gate (v20.2, spec §C-18 + audit H1/M1; hardened v21.4.0).
 *
 * Fails the release if the tarball contains any of:
 *   - secret material: private-key blocks, cloud/LLM/npm token
 *     shapes, key/db/env FILES by name;
 *   - forbidden path segments (tests/test/fixtures/__tests__/
 *     examples) — upstream fixture content must never ship;
 *   - unexpected file types in the payload: Python SOURCES (.py)
 *     under server/ (the bundle is bytecode-only), archives, or
 *     executables outside bin/;
 *   - an unsigned or hash-mismatched payload: server/manifest.json
 *     + manifest.sig must verify against the release public key
 *     pinned in lib/shield.js.
 *
 * v21.4.0 hardening (full-audit fixes):
 *   - the secret scan covers the COMPILED payload too (.pyc files
 *     under server/): string literals survive compilation, so a
 *     secret committed in a Python source would otherwise ship
 *     invisible inside the bytecode. Raw-byte pattern matching is
 *     used, which is exactly what .pyc literals need.
 *   - a payload file (server/**) larger than the scan limit is a
 *     hard FAIL, not a silent skip — an unscanned payload file is
 *     an unverified payload file.
 *   - the payload manifest's Python minor must be exactly 3.12 —
 *     the runtime refuses anything else, so a tarball built with
 *     another interpreter must never pass this gate.
 *   - case-insensitive duplicate paths and a worst-case Windows
 *     total-path-length check (< 260 with a long npm prefix).
 *   - more secret shapes (Telegram bot tokens, generic sk- keys,
 *     Stripe live keys, Google AIza keys, JWTs, *_API_KEY string
 *     assignments) and more forbidden file names (SSH keys of
 *     every flavour, .envrc, .npmrc, key containers, secrets.*).
 *   - optional identity assertion: --expect-name agentqon fails
 *     the gate when the tarball's package.json name/bin or the
 *     user-facing strings shipped in bin/, lib/, mcp/ and the root
 *     docs name the WRONG npm package. The flip that produces the
 *     agentqon identity is scripts/flip-identity.js — never edit
 *     a staging copy by hand.
 *
 * 2026-10-22 audit fixes (CLI/packaging axis):
 *   - M2: .py sources OUTSIDE server/ (the shipped mcp/ file) are
 *     secret-scanned too — .py joined SCAN_EXTS. server/**.py
 *     still hard-fails on the type rule before scanning matters.
 *   - L3: a file in the scan set but over MAX_SCAN_BYTES is a hard
 *     FAIL wherever it lives, not a silent skip — the PASS line
 *     claims "no secrets" over the whole tarball, so unscanned
 *     content must never ride under it. (server/ files carry their
 *     own payload-size failure above.)
 *   - I1: scan decoding is latin1 (byte-preserving), so a secret
 *     pattern can no longer hide across an invalid UTF-8 boundary
 *     turned into U+FFFD. Every pattern is ASCII-shaped, so real
 *     text files scan identically.
 *
 * Usage: node scripts/pack-gate.js [--expect-name <pkg>] <tarball.tgz>
 * The tarball argument is REQUIRED (v21.4.0): gating "the newest
 * .tgz in cli/" silently passed stale artifacts — name the exact
 * file you intend to ship.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const shield = require('../lib/shield');

const FORBIDDEN_SEGMENTS = new Set(['tests', 'test', 'fixtures', '__tests__', 'examples']);
const FORBIDDEN_NAMES = [/\.env($|\.)/i, /\.envrc$/i, /^\.npmrc$/i, /\.pem$/i, /\.key$/i,
  /^id_(rsa|dsa|ecdsa|ed25519)/i, /\.(p12|pfx|keystore)$/i, /^secrets(\.|$)/i,
  /vault\.key$/i, /\.db$/i, /\.sqlite3?$/i, /credentials/i];
const SECRET_PATTERNS = [
  /* A real private key has a base64 BODY — the pattern requires one,
   * so security documentation that merely shows a BEGIN header line
   * as a grep example (the SecOps persona does exactly that) is not
   * flagged, while any actual key material is. */
  /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----\s*\n(?:[A-Za-z0-9+/=]{20,}\s*\n?){2,}/,
  /AKIA[0-9A-Z]{16}/,
  /ghp_[A-Za-z0-9]{36,}/,
  /github_pat_[A-Za-z0-9_]{50,}/,
  /npm_[A-Za-z0-9]{36,}/,
  /xox[baprs]-[A-Za-z0-9-]{10,}/,
  /sk-ant-[A-Za-z0-9_-]{20,}/,
  /sk-proj-[A-Za-z0-9_-]{20,}/,
  /* v21.4.0 additions — shapes verified to have zero false
   * positives across the shipped roster + payload corpus. */
  /(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}/,             // Telegram bot token (no trailing \b: in .pyc bytes a marshal tag byte can abut the literal)
  /(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}/,          // generic OpenAI-style key
  /[sr]k_live_[A-Za-z0-9]{16,}/,                    // Stripe live secret/restricted key
  /AIza[0-9A-Za-z_-]{35}/,                          // Google API key
  /eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}/, // JWT (header.payload.sig)
  /[A-Z][A-Z0-9_]*API_KEY\s*=\s*["'][A-Za-z0-9_\-+=\/.]{16,}["']/, // *_API_KEY = "…" assignment
];
const SCAN_EXTS = new Set(['.js', '.json', '.md', '.txt', '.html', '.sh', '.yml', '.yaml', '.toml', '.cfg', '.pyc', '.py', '']);
const MAX_SCAN_BYTES = 4_000_000;
const REQUIRED_PAYLOAD_PYTHON = '3.12';
/* Windows MAX_PATH is 260. Worst realistic npm layout:
 * C:\Users\<32-char name>\AppData\Roaming\npm\node_modules\<pkg>\<rel>
 * — the gate computes that total for every shipped path. */
const WIN_MAX_PATH = 260;
const WIN_NPM_PREFIX_WORST = 'C:\\Users\\' + 'u'.repeat(32) + '\\AppData\\Roaming\\npm\\node_modules\\';

/* Identity expectations per shippable package name. requiredBins:
 * bin keys the package must declare. wrongPackage: regexes over
 * user-facing shipped text (bin/, lib/, mcp/, root docs — NOT the
 * server/ payload, whose content is product data) that name the
 * wrong package for this identity. */
const IDENTITY_EXPECTATIONS = {
  agentqon: {
    requiredBins: ['agentqon'],
    wrongPackage: [/omnimind-ai/],
  },
  /* Audit I2: retained for completeness, but UNSATISFIABLE from
   * this tree by design — lib/commands.js hardcodes the agentqon
   * update strings (correct for the only ship target since the
   * 21.2.0 pointer), so a legacy omnimind-ai rebuild from this
   * tree fails this identity block on purpose. Rebuilding the
   * legacy package means flipping those strings back first. */
  'omnimind-ai': {
    requiredBins: ['agentqon', 'omnimind'],
    wrongPackage: [/npm install -g agentqon(?=[@\s"']|$)/],
  },
};

function fail(errors, msg) { errors.push(msg); }

function parseArgs(argv) {
  const out = { tarball: null, expectName: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--expect-name') { out.expectName = argv[++i]; }
    else if (a.startsWith('--expect-name=')) { out.expectName = a.slice('--expect-name='.length); }
    else if (a.startsWith('--')) { console.error(`pack-gate: unknown option ${a}`); process.exit(2); }
    else if (!out.tarball) { out.tarball = a; }
    else { console.error(`pack-gate: unexpected extra argument ${a}`); process.exit(2); }
  }
  return out;
}

function main() {
  const { tarball, expectName } = parseArgs(process.argv.slice(2));
  if (!tarball) {
    console.error('pack-gate: FAIL — no tarball specified. Pass the exact artifact to gate:\n' +
      '  node scripts/pack-gate.js [--expect-name agentqon] <tarball.tgz>\n' +
      '(Gating "the newest .tgz around" silently passed stale artifacts; it no longer guesses.)');
    process.exit(2);
  }
  if (expectName && !IDENTITY_EXPECTATIONS[expectName]) {
    console.error(`pack-gate: FAIL — no identity expectation registered for "${expectName}" ` +
      `(known: ${Object.keys(IDENTITY_EXPECTATIONS).join(', ')})`);
    process.exit(2);
  }
  const tmpBase = process.env.PACK_GATE_TMPDIR || os.tmpdir();
  const tmp = fs.mkdtempSync(path.join(tmpBase, 'pack-gate-'));
  const untar = spawnSync('tar', ['-xzf', tarball, '-C', tmp, '--no-same-owner'], { encoding: 'utf8' });
  if (untar.status !== 0) { console.error('pack-gate: cannot extract tarball: ' + untar.stderr); process.exit(2); }
  const root = path.join(tmp, 'package');
  const errors = [];

  let pkgJson = null;
  try {
    pkgJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  } catch (e) {
    fail(errors, 'package.json missing or unparseable in tarball');
  }
  const pkgName = (pkgJson && pkgJson.name) || '';

  const files = shield.walkFiles(root);
  const WIN_RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL',
    ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
    ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`)]);
  const seenLower = new Map();
  for (const rel of files) {
    const segments = rel.split('/');
    const base = segments[segments.length - 1];
    /* Windows gate (v20.2.1 hotfix, made permanent): a payload path
     * Windows cannot create — illegal characters, control characters,
     * reserved device names, trailing dot/space — extracts incomplete
     * there and Shield refuses the install. Never ship one. */
    for (const seg of segments) {
      if (/[\\:*?"<>|]/.test(seg) || /[\x00-\x1f\x7f]/.test(seg) ||
          WIN_RESERVED.has(seg.split('.')[0].toUpperCase()) ||
          seg.endsWith('.') || seg.endsWith(' ')) {
        fail(errors, `windows-hostile path segment "${seg}" in: ${rel}`); break;
      }
    }
    /* v21.4.0: two paths that differ only by case (or collapse to
     * the same file on a case-insensitive filesystem) extract onto
     * each other on Windows/macOS and corrupt the payload hashes. */
    const lower = rel.toLowerCase();
    if (seenLower.has(lower) && seenLower.get(lower) !== rel) {
      fail(errors, `case-insensitive duplicate path: ${seenLower.get(lower)} vs ${rel}`);
    } else {
      seenLower.set(lower, rel);
    }
    /* v21.4.0: worst-case total path length on a Windows install. */
    const worstTotal = WIN_NPM_PREFIX_WORST.length + (pkgName ? pkgName.length + 1 : 0) + rel.length;
    if (worstTotal >= WIN_MAX_PATH) {
      fail(errors, `path too long for Windows installs (worst-case ${worstTotal} >= ${WIN_MAX_PATH}): ${rel}`);
    }
    for (const seg of segments) {
      if (FORBIDDEN_SEGMENTS.has(seg)) { fail(errors, `forbidden path segment in: ${rel}`); break; }
    }
    /* File-TYPE rules (.pem/.key/.env/db) apply everywhere — those
     * are never content. The loose `credentials` NAME rule skips the
     * roster data trees: upstream documentation pages legitimately
     * named e.g. credentials.md live there, and they are content the
     * product ships on purpose, scanned by the patterns above. */
    const inDataTree = segments.includes('data');
    for (const rx of FORBIDDEN_NAMES) {
      if (inDataTree && rx.source.includes('credentials')) continue;
      if (rx.test(base)) { fail(errors, `forbidden file name: ${rel}`); break; }
    }
    const ext = path.extname(base).toLowerCase();
    if (rel.startsWith('server/') && ext === '.py') {
      fail(errors, `python source in payload (bytecode only): ${rel}`);
    }
    if (['.zip', '.tar', '.tgz', '.gz', '.exe', '.dll', '.so', '.dylib'].includes(ext) && !rel.startsWith('bin/')) {
      fail(errors, `unexpected binary/archive file: ${rel}`);
    }
    /* v21.4.0: ANY oversized payload file is a hard failure —
     * the gate cannot content-verify what it cannot scan, whatever
     * its extension. (This used to be a silent skip inside the scan
     * block, which unscanned extensions escaped entirely.) */
    if (rel.startsWith('server/')) {
      const size = fs.statSync(path.join(root, ...segments)).size;
      if (size > MAX_SCAN_BYTES) {
        fail(errors, `payload file too large to secret-scan (${size} bytes > ${MAX_SCAN_BYTES}): ${rel}`);
      }
    }
    if (SCAN_EXTS.has(ext) || base === 'manifest.json') {
      const p = path.join(root, ...segments);
      const buf = fs.readFileSync(p);
      if (buf.length > MAX_SCAN_BYTES) {
        /* Audit 2026-10-22 L3: an oversized file in the scan set
         * is a hard FAIL, not the historical silent skip — the
         * gate cannot content-verify what it cannot scan, whatever
         * its extension or directory. (server/ files already failed
         * above under the payload-size rule; don't fail them twice.) */
        if (!rel.startsWith('server/')) {
          fail(errors, `file too large to secret-scan (${buf.length} bytes > ${MAX_SCAN_BYTES}): ${rel}`);
        }
      } else {
        /* latin1: byte-preserving decode (audit I1) — invalid
         * UTF-8 sequences must not become U+FFFD and split a
         * secret pattern across the replacement boundary. */
        const text = buf.toString('latin1');
        for (const rx of SECRET_PATTERNS) {
          if (rx.test(text)) { fail(errors, `secret pattern ${rx} in: ${rel}`); break; }
        }
        /* v21.4.0 identity: user-facing CLI-side strings must name
         * THIS package, not the other one. */
        if (expectName && !rel.startsWith('server/')) {
          for (const rx of IDENTITY_EXPECTATIONS[expectName].wrongPackage) {
            if (rx.test(text)) {
              fail(errors, `identity mismatch: ${rel} names the wrong package for "${expectName}" (matched ${rx})`);
              break;
            }
          }
        }
      }
    }
  }

  /* v21.4.0 identity: the manifest of record — package name + bins. */
  if (expectName && pkgJson) {
    const exp = IDENTITY_EXPECTATIONS[expectName];
    if (pkgJson.name !== expectName) {
      fail(errors, `identity mismatch: package.json name is "${pkgJson.name}", expected "${expectName}"`);
    }
    const bins = Object.keys(pkgJson.bin || {});
    for (const b of exp.requiredBins) {
      if (!bins.includes(b)) {
        fail(errors, `identity mismatch: package.json bin is missing "${b}" for "${expectName}"`);
      }
    }
  }

  const serverDir = path.join(root, 'server');
  if (!fs.existsSync(path.join(serverDir, 'manifest.json'))) {
    fail(errors, 'server/manifest.json missing — payload is not a Shield build');
  } else {
    /* v21.4.0: the payload must have been built with Python 3.12 —
     * the runtime refuses every other minor, so any other value
     * here is a tarball that fails for every single user. */
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(serverDir, 'manifest.json'), 'utf8'));
      if (manifest.python !== REQUIRED_PAYLOAD_PYTHON) {
        fail(errors, `payload manifest python is "${manifest.python}", required exactly "${REQUIRED_PAYLOAD_PYTHON}"`);
      }
    } catch (e) {
      fail(errors, 'server/manifest.json unparseable: ' + e.message);
    }
    const v = shield.verifyBundle(serverDir);
    if (!v.shield || !v.ok) {
      fail(errors, 'payload manifest verification failed: ' + (v.errors || []).join('; '));
    }
  }
  fs.rmSync(tmp, { recursive: true, force: true });

  if (errors.length) {
    console.error(`pack-gate: FAIL (${errors.length} problem${errors.length === 1 ? '' : 's'})`);
    for (const e of errors.slice(0, 25)) console.error('  ✗ ' + e);
    process.exit(1);
  }
  console.log(`pack-gate: PASS — ${files.length} files, no secrets, no forbidden paths, payload signature + hashes verified` +
    (expectName ? `, identity "${expectName}" verified` : '') +
    `, payload python ${REQUIRED_PAYLOAD_PYTHON}`);
}

main();
