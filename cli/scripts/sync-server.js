#!/usr/bin/env node
'use strict';
/* Copies the platform (app/, apps/web/, requirements.txt) into
 * cli/server/ so `npm pack` ships the complete product. Run after any
 * platform change:  node cli/scripts/sync-server.js
 * Excludes caches, tests and local databases — the bundle is code only;
 * user data is created under ~/.agentqon/data on first run.
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const dest = path.join(__dirname, '..', 'server');

const SKIP_DIRS = new Set(['__pycache__', '.pytest_cache', 'node_modules',
  // Security gate (2026-10-11 audit): upstream test/fixture/example content
  // must never ship in the bundle — the library importer would otherwise
  // register fixture text as live agent personas.
  'tests', 'test', 'fixtures', '__tests__', 'examples']);
function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) continue;
    const s = path.join(src, entry.name);
    // Windows gate (v20.2.1 hotfix, made permanent): NTFS forbids
    // [\\:*?"<>|] and control characters in file names — an upstream
    // roster file named e.g. `gsd:add-backlog.md` extracts fine on
    // Linux but cannot be created on Windows at all, which corrupts
    // the payload and trips Shield verification there. Sanitize on
    // copy so the shipped bundle is identical on every platform.
    let name = entry.name.replace(/[\\:*?"<>|\x00-\x1f\x7f]+/g, '-');
    let d = path.join(dst, name);
    if (name !== entry.name) {
      let n = 1;
      const ext = path.extname(name);
      const stem = name.slice(0, name.length - ext.length);
      while (fs.existsSync(d)) { name = `${stem}-${++n}${ext}`; d = path.join(dst, name); }
      console.log(`sync-server: windows-sanitized ${entry.name} -> ${name}`);
    }
    if (entry.isDirectory()) copyDir(s, d);
    else if (!entry.name.endsWith('.pyc')) fs.copyFileSync(s, d);
  }
}

fs.rmSync(dest, { recursive: true, force: true });
copyDir(path.join(root, 'app'), path.join(dest, 'app'));
copyDir(path.join(root, 'apps'), path.join(dest, 'apps'));
fs.copyFileSync(path.join(root, 'requirements.txt'), path.join(dest, 'requirements.txt'));
fs.copyFileSync(path.join(root, 'run.sh'), path.join(dest, 'run.sh'));

let files = 0; let bytes = 0;
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p); else { files++; bytes += fs.statSync(p).size; }
  }
})(dest);
console.log(`server bundle synced: ${files} files, ${(bytes / 1024 / 1024).toFixed(2)} MB → ${dest}`);
