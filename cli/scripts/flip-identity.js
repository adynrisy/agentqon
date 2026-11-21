#!/usr/bin/env node
'use strict';
/* flip-identity — the scripted omnimind-ai → agentqon package flip.
 *
 * The canonical tree in this repo builds a package NAMED
 * `omnimind-ai` (package.json, plus install/update strings in
 * lib/*.js, bin/agentqon.js and README.md). The AgentQon release
 * is produced from a COPY of that tree, and the copy's identity
 * flip is THIS script — never a hand edit. Usage: copy the package
 * tree to the staging directory, then run
 * `node scripts/flip-identity.js <staging-dir>` (add `--check` to
 * verify a tree without writing). The script replaces every
 * literal `omnimind-ai` in package.json, lib/, bin/ and README.md
 * with `agentqon`, then verifies the result: package.json parses,
 * its name is `agentqon`, its bin declares the `agentqon` command,
 * and no flipped file still contains the old name. It is
 * idempotent (an already-flipped tree verifies clean), refuses a
 * tree whose package name is neither identity, and the pack gate's
 * `--expect-name agentqon` assertion re-checks all of it against
 * the final tarball before anything is staged on npm.
 */
const fs = require('fs');
const path = require('path');

const FROM = 'omnimind-ai';
const TO = 'agentqon';

function collectFiles(target) {
  const out = [];
  const add = (rel) => {
    const p = path.join(target, rel);
    if (fs.existsSync(p)) out.push(rel);
  };
  add('package.json');
  add('README.md');
  for (const dir of ['lib', 'bin']) {
    const base = path.join(target, dir);
    if (!fs.existsSync(base)) continue;
    (function walk(d) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name);
        if (e.isDirectory()) walk(p);
        else out.push(path.relative(target, p).split(path.sep).join('/'));
      }
    })(base);
  }
  return out.sort();
}

function main() {
  const args = process.argv.slice(2);
  const checkOnly = args.includes('--check');
  const positional = args.filter((a) => !a.startsWith('--'));
  const target = positional[0] ? path.resolve(positional[0]) : path.join(__dirname, '..');

  const pkgPath = path.join(target, 'package.json');
  if (!fs.existsSync(pkgPath)) {
    console.error(`flip-identity: no package.json under ${target}`);
    process.exit(2);
  }
  let pkg;
  try {
    pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
  } catch (e) {
    console.error('flip-identity: package.json unparseable: ' + e.message);
    process.exit(2);
  }
  if (pkg.name !== FROM && pkg.name !== TO) {
    console.error(`flip-identity: refusing — package name is "${pkg.name}", expected "${FROM}" or "${TO}"`);
    process.exit(2);
  }

  const files = collectFiles(target);
  let totalReplacements = 0;
  const changed = [];
  const finalContent = new Map();
  for (const rel of files) {
    const p = path.join(target, ...rel.split('/'));
    const before = fs.readFileSync(p, 'utf8');
    const hits = before.split(FROM).length - 1;
    const after = hits ? before.split(FROM).join(TO) : before;
    finalContent.set(rel, after);
    if (hits) {
      totalReplacements += hits;
      changed.push(`${rel} (${hits})`);
      if (!checkOnly) fs.writeFileSync(p, after);
    }
  }

  /* Verification — over the post-flip content, written or not. */
  const problems = [];
  let newPkg;
  try {
    newPkg = JSON.parse(finalContent.get('package.json'));
  } catch (e) {
    problems.push('package.json unparseable after flip: ' + e.message);
  }
  if (newPkg) {
    if (newPkg.name !== TO) problems.push(`package.json name is "${newPkg.name}", expected "${TO}"`);
    if (!newPkg.bin || !(TO in newPkg.bin)) problems.push(`package.json bin does not declare the "${TO}" command`);
  }
  for (const rel of files) {
    if (finalContent.get(rel).includes(FROM)) {
      problems.push(`${rel} still contains "${FROM}"`);
    }
  }

  if (problems.length) {
    console.error(`flip-identity: VERIFICATION FAILED (${problems.length})`);
    for (const p of problems) console.error('  ✗ ' + p);
    process.exit(1);
  }
  console.log(`flip-identity: ${checkOnly ? 'CHECK' : 'OK'} — target ${target}`);
  console.log(`  package name: ${pkg.name} → ${TO}; replacements: ${totalReplacements}` +
    (changed.length ? ` in ${changed.join(', ')}` : ' (already flipped)'));
  console.log('  verified: name + bin correct, no user-facing string names the old package');
}

main();
