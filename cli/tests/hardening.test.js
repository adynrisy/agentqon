'use strict';
/* Regression tests for the v21.4.0 CLI hardening wave.
 * Run: node --test cli/tests/hardening.test.js
 * (the bare-directory form `node --test cli/tests/` fails on
 * Node 24 — it resolves the directory as a module, audit C.)
 * Zero dependencies (node:test + node:assert); platform-specific
 * cases skip cleanly where they cannot apply. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn, execFileSync } = require('child_process');

const serverLib = require('../lib/server');
const cfgMod = require('../lib/config');
const style = require('../lib/style');

function tmpDir(prefix) { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); }

/* ------------------------------------------------ 1. timer leaks */
function stubTimers() {
  const live = new Map();
  let seq = 0;
  const realSet = global.setInterval;
  const realClear = global.clearInterval;
  global.setInterval = (fn) => { const id = ++seq; live.set(id, fn); return id; };
  global.clearInterval = (id) => { live.delete(id); };
  return { live, restore() { global.setInterval = realSet; global.clearInterval = realClear; } };
}

test('Progress: reuse across phases leaks no intervals', () => {
  process.stdout.isTTY = true;
  const timers = stubTimers();
  const realWrite = process.stdout.write;
  process.stdout.write = () => true;
  try {
    const p = new style.Progress();
    for (let i = 0; i < 6; i++) p.start('phase ' + i); // the ensureBootstrapped pattern
    assert.strictEqual(timers.live.size, 1, 'exactly one interval may be live while running');
    p.succeed('done');
    assert.strictEqual(timers.live.size, 0, 'no interval may survive succeed()');
    const p2 = new style.Progress();
    p2.start('a'); p2.start('b'); p2.fail('x');
    assert.strictEqual(timers.live.size, 0, 'no interval may survive fail()');
  } finally {
    process.stdout.write = realWrite;
    timers.restore();
  }
});

test('Spinner: an intermediate phase completion keeps it ticking', () => {
  process.stdout.isTTY = true;
  const timers = stubTimers();
  const realWrite = process.stdout.write;
  process.stdout.write = () => true;
  try {
    const sp = new style.Spinner();
    sp.start('Planning…');
    sp.succeedPhase('Planning');            // intermediate — must NOT kill the spinner
    sp.setLabel('Drafting…');               // next phase begins
    assert.strictEqual(timers.live.size, 1, 'spinner must still be ticking after a phase change');
    sp.start('Critic…');                    // explicit restart is also leak-proof
    assert.strictEqual(timers.live.size, 1);
    sp.succeed('Evaluating');               // terminal
    assert.strictEqual(timers.live.size, 0, 'terminal succeed() stops the timer');
  } finally {
    process.stdout.write = realWrite;
    timers.restore();
  }
});

/* ------------------------------------------------ 2. marker probe */
function pythonEnv() {
  const out = execFileSync('python3', ['-c',
    'import json,os,platform,sys;print(json.dumps({' +
    '"sys_platform":sys.platform,"os_name":os.name,"machine":platform.machine(),' +
    '"py":list(sys.version_info[:2]),"full":platform.python_version()}))'], { encoding: 'utf8' });
  return JSON.parse(out.trim());
}

test('marker probe: full PEP 508 grammar, incl. the proven-failing shapes', { skip: !spawnSyncOk('python3') }, () => {
  const env = pythonEnv();
  const otherPlatform = env.sys_platform === 'win32' ? 'linux' : 'win32';
  const otherOs = env.os_name === 'nt' ? 'posix' : 'nt';
  const pyGE311 = env.py[0] > 3 || (env.py[0] === 3 && env.py[1] >= 11);
  const run = (mods) => {
    const out = execFileSync('python3', ['-c', serverLib.libraryCheckScript(), JSON.stringify(mods)], { encoding: 'utf8' });
    return JSON.parse(out.trim().split('\n').pop());
  };
  const mods = [
    // marker FALSE here -> module must NOT be demanded
    { module: 'fx_other_platform', marker: `sys_platform == "${otherPlatform}"` },
    { module: 'fx_compound', marker: `python_version >= "3.11" and sys_platform == "${otherPlatform}"` }, // proven failing shape
    { module: 'fx_single_quotes', marker: `sys_platform == '${otherPlatform}'` },                          // proven failing shape
    { module: 'fx_os_name_skip', marker: `os_name == "${otherOs}"` },                                      // proven failing shape
    { module: 'fx_parens_or', marker: `(sys_platform == "${otherPlatform}" or sys_platform == "darwin-rest")` },
    { module: 'fx_compat_no', marker: 'python_version ~= "99.1"' },
    { module: 'fx_extra', marker: 'extra == "socks"' },
    { module: 'fx_old_py', marker: 'python_version < "3.11"', want: !pyGE311 },
    // marker TRUE here -> module must be demanded (it does not exist)
    { module: 'fx_this_platform', marker: `sys_platform == "${env.sys_platform}"` },
    { module: 'fx_os_name_hit', marker: `os_name == "${env.os_name}"` },
    { module: 'fx_full_version', marker: 'python_full_version >= "3.0.0"' },
    { module: 'fx_compat_yes', marker: 'python_version ~= "3.0"' },
    { module: 'fx_star', marker: 'python_version == "3.*"', want: env.py[0] === 3 },
    { module: 'fx_in', marker: `sys_platform in "${env.sys_platform} extra"` },
    { module: 'fx_not_in', marker: `sys_platform not in "${otherPlatform}"` },
    { module: 'fx_garbage', marker: 'this is not a marker' }, // unparseable -> conservatively required
  ];
  const res = run(mods);
  for (const m of mods) {
    const demanded = res.missing.includes(m.module);
    const expected = m.want !== undefined ? m.want : m.module.startsWith('fx_') &&
      !['fx_other_platform', 'fx_compound', 'fx_single_quotes', 'fx_os_name_skip', 'fx_parens_or', 'fx_compat_no', 'fx_extra'].includes(m.module);
    assert.strictEqual(demanded, expected, `${m.module} (${m.marker}) demanded=${demanded}, expected ${expected}`);
  }
});

test('marker probe: every marker in the shipped requirements files', { skip: !spawnSyncOk('python3') }, () => {
  const env = pythonEnv();
  const files = [path.join(__dirname, '..', 'server', 'requirements.txt'),
    path.join(__dirname, '..', '..', 'requirements.txt')];
  const seen = new Map();
  for (const f of files) {
    if (!fs.existsSync(f)) continue;
    for (const e of serverLib.requirementModules(f)) if (e.marker) seen.set(e.marker, e.dist);
  }
  assert.ok(seen.size >= 3, `expected several shipped markers, saw ${seen.size}`);
  const expectedFor = (marker) => {
    if (marker === 'sys_platform != "win32"') return env.sys_platform !== 'win32';
    if (marker === 'sys_platform == "win32"') return env.sys_platform === 'win32';
    if (marker === 'python_version < "3.11"') return !pyAtLeast(env, 3, 11);
    if (marker === 'platform_machine != "ARM64"') return env.machine !== 'ARM64';
    return null; // unknown shipped marker — presence is asserted, value left to the grammar test
  };
  const mods = [...seen.keys()].map((marker, i) => ({ module: 'fx_ship_' + i, marker }));
  const out = execFileSync('python3', ['-c', serverLib.libraryCheckScript(), JSON.stringify(mods)], { encoding: 'utf8' });
  const res = JSON.parse(out.trim().split('\n').pop());
  mods.forEach((m, i) => {
    const expected = expectedFor(m.marker);
    if (expected === null) return;
    assert.strictEqual(res.missing.includes(m.module), expected,
      `shipped marker ${JSON.stringify(m.marker)} (${[...seen.values()][i]}) evaluated wrong`);
  });
});

function pyAtLeast(env, maj, min) { return env.py[0] > maj || (env.py[0] === maj && env.py[1] >= min); }
function spawnSyncOk(cmd) {
  try { return require('child_process').spawnSync(cmd, ['--version']).status === 0; } catch { return false; }
}
function python3Path() {
  return execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim();
}

test('checkLibraries: marker-skipped modules are not demanded', { skip: !spawnSyncOk('python3') }, async () => {
  const home = tmpDir('aq-libcheck-');
  const venvDir = path.join(home, 'venv');
  fs.mkdirSync(path.join(venvDir, 'bin'), { recursive: true });
  fs.symlinkSync(python3Path(), path.join(venvDir, 'bin', 'python'));
  const reqDir = path.join(home, 'reqs');
  fs.mkdirSync(reqDir, { recursive: true });
  const otherPlatform = process.platform === 'win32' ? 'linux' : 'win32';
  fs.writeFileSync(path.join(reqDir, 'requirements.txt'),
    `json==1.0\nfxskip==1.0; sys_platform == "${otherPlatform}"\n`);
  const res = await serverLib.checkLibraries({ serverDir: reqDir, venvDir }, reqDir);
  assert.strictEqual(res.ok, true, JSON.stringify(res));
  assert.deepStrictEqual(res.missing, []);
  fs.rmSync(home, { recursive: true, force: true });
});

/* ------------------------------------------------ 3/4. findPython + venv */
function stubPython(dir, name, body) {
  const p = path.join(dir, name);
  fs.writeFileSync(p, `#!/bin/sh\n${body}\n`);
  fs.chmodSync(p, 0o755);
}

test('findPython: a 3.10 first on PATH does not hide a 3.12 beside it', { skip: process.platform === 'win32' }, () => {
  const bin = tmpDir('aq-pybin-');
  stubPython(bin, 'python3', 'echo 3.10');
  stubPython(bin, 'python', 'echo 3.12');
  const savedPath = process.env.PATH;
  const savedOverride = process.env.OMNIMIND_PYTHON;
  process.env.PATH = bin;
  delete process.env.OMNIMIND_PYTHON;
  try {
    const found = serverLib.findPython('3.12');
    assert.ok(found, 'a match must be found');
    assert.strictEqual(found.version, '3.12');
    assert.strictEqual(found.cmd, 'python');
    assert.ok(!found.tooOld);
  } finally {
    process.env.PATH = savedPath;
    if (savedOverride !== undefined) process.env.OMNIMIND_PYTHON = savedOverride;
    fs.rmSync(bin, { recursive: true, force: true });
  }
});

test('findPython: no exact match -> best runnable flagged tooOld', { skip: process.platform === 'win32' }, () => {
  const bin = tmpDir('aq-pybin-');
  stubPython(bin, 'python3', 'echo 3.10');
  const savedPath = process.env.PATH;
  const savedOverride = process.env.OMNIMIND_PYTHON;
  process.env.PATH = bin;
  delete process.env.OMNIMIND_PYTHON;
  try {
    const found = serverLib.findPython('3.12');
    assert.ok(found && found.tooOld === true && found.version === '3.10');
  } finally {
    process.env.PATH = savedPath;
    if (savedOverride !== undefined) process.env.OMNIMIND_PYTHON = savedOverride;
    fs.rmSync(bin, { recursive: true, force: true });
  }
});

test('findPython: a hanging shim is skipped on the probe timeout', { skip: process.platform === 'win32' }, () => {
  const bin = tmpDir('aq-pybin-');
  stubPython(bin, 'python3', 'exec /bin/sleep 30');
  const savedPath = process.env.PATH;
  const savedOverride = process.env.OMNIMIND_PYTHON;
  process.env.PATH = bin;
  delete process.env.OMNIMIND_PYTHON;
  try {
    const t0 = Date.now();
    const found = serverLib.findPython('3.12');
    assert.strictEqual(found, null);
    assert.ok(Date.now() - t0 < 20_000, 'probe must time out well before the shim wakes');
  } finally {
    process.env.PATH = savedPath;
    if (savedOverride !== undefined) process.env.OMNIMIND_PYTHON = savedOverride;
    fs.rmSync(bin, { recursive: true, force: true });
  }
});

test('venvInterpreterOk: missing, broken and healthy venvs', { skip: !spawnSyncOk('python3') }, async () => {
  const home = tmpDir('aq-venv-');
  const p = { venvDir: path.join(home, 'venv') };
  assert.strictEqual(await serverLib.venvInterpreterOk(p, '3.12'), false, 'missing venv');
  fs.mkdirSync(path.join(p.venvDir, 'bin'), { recursive: true });
  stubPython(path.join(p.venvDir, 'bin'), 'python', 'exit 1');
  assert.strictEqual(await serverLib.venvInterpreterOk(p, '3.12'), false, 'broken interpreter');
  fs.rmSync(path.join(p.venvDir, 'bin', 'python'));
  fs.symlinkSync(python3Path(), path.join(p.venvDir, 'bin', 'python'));
  const env = pythonEnv();
  const want = `${env.py[0]}.${env.py[1]}`;
  assert.strictEqual(await serverLib.venvInterpreterOk(p, want), true, 'healthy interpreter');
  assert.strictEqual(await serverLib.venvInterpreterOk(p, '9.99'), false, 'wrong minor refused');
  fs.rmSync(home, { recursive: true, force: true });
});

/* ------------------------------------------------ 3. staged swap */
test('swapDirIntoPlace: replaces atomically and restores nothing stale', () => {
  const root = tmpDir('aq-swap-');
  const src = path.join(root, 'v.new-1');
  const dest = path.join(root, 'v');
  fs.mkdirSync(src); fs.mkdirSync(dest);
  fs.writeFileSync(path.join(src, 'new.txt'), 'new');
  fs.writeFileSync(path.join(dest, 'old.txt'), 'old');
  serverLib.swapDirIntoPlace(src, dest);
  assert.ok(fs.existsSync(path.join(dest, 'new.txt')));
  assert.ok(!fs.existsSync(path.join(dest, 'old.txt')));
  assert.ok(!fs.existsSync(src));
  assert.deepStrictEqual(fs.readdirSync(root), ['v'], 'no temp or backup dirs may remain');
  fs.rmSync(root, { recursive: true, force: true });
});

/* ------------------------------------------------ 5. lifecycle */
test('pidAlive: EPERM means alive, ESRCH means dead', () => {
  const realKill = process.kill;
  try {
    process.kill = () => { const e = new Error('perm'); e.code = 'EPERM'; throw e; };
    assert.strictEqual(serverLib.pidAlive(4242), true);
    process.kill = () => { const e = new Error('gone'); e.code = 'ESRCH'; throw e; };
    assert.strictEqual(serverLib.pidAlive(4242), false);
  } finally { process.kill = realKill; }
  assert.strictEqual(serverLib.pidAlive(process.pid), true);
});

function fakeHealthServer(payload) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      if (req.url === '/health') { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(payload)); }
      else { res.writeHead(200, { 'content-type': 'text/html' }); res.end('<html>ok</html>'); }
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

function withHome() {
  const home = tmpDir('aq-home-');
  const saved = process.env.AGENTQON_HOME;
  process.env.AGENTQON_HOME = home;
  fs.mkdirSync(path.join(home, 'logs'), { recursive: true });
  return { home, restore() { if (saved === undefined) delete process.env.AGENTQON_HOME; else process.env.AGENTQON_HOME = saved; fs.rmSync(home, { recursive: true, force: true }); } };
}

test('stopServer: never kills a pid whose port answers as another product', { skip: process.platform === 'win32' }, async () => {
  const { home, restore } = withHome();
  const srv = await fakeHealthServer({ product: 'OtherApp', status: 'ok' });
  const port = srv.address().port;
  const innocent = spawn('/bin/sleep', ['30'], { stdio: 'ignore' });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: innocent.pid, port, version: serverLib.VERSION, runtimeDir: null }));
    const r = await serverLib.stopServer();
    assert.strictEqual(r.stopped, false);
    assert.match(r.reason, /OtherApp|not AgentQon/);
    assert.doesNotThrow(() => process.kill(innocent.pid, 0), 'the foreign process must be left alive');
    assert.ok(!fs.existsSync(path.join(home, 'server.pid')), 'stale record cleared');
  } finally {
    try { innocent.kill('SIGKILL'); } catch { /* gone */ }
    srv.close();
    restore();
  }
});

test('runningServer: a stale-version server is stopped, never silently reused', { skip: process.platform === 'win32' }, async () => {
  const { home, restore } = withHome();
  const srv = await fakeHealthServer({ product: 'AgentQon', status: 'ok' });
  const port = srv.address().port;
  const stale = spawn('/bin/sleep', ['30'], { stdio: 'ignore' });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: stale.pid, port, version: '0.0.0-stale', runtimeDir: null }));
    const rec = await serverLib.runningServer();
    assert.strictEqual(rec, null, 'stale server must not be reported as running');
    const deadline = Date.now() + 5000;
    let alive = true;
    while (Date.now() < deadline) {
      try { process.kill(stale.pid, 0); await new Promise((r) => setTimeout(r, 100)); }
      catch { alive = false; break; }
    }
    assert.strictEqual(alive, false, 'the stale server process was stopped');
    assert.ok(!fs.existsSync(path.join(home, 'server.pid')));
  } finally {
    try { stale.kill('SIGKILL'); } catch { /* gone */ }
    srv.close();
    restore();
  }
});

/* ------------------------------------------------ 6. mediums */
test('run(): a child that stalls is killed at the timeout', { skip: process.platform === 'win32' }, async () => {
  const t0 = Date.now();
  const r = await serverLib.run('/bin/sh', ['-c', 'exec /bin/sleep 30'], { timeoutMs: 300 });
  assert.strictEqual(r.timedOut, true);
  assert.notStrictEqual(r.status, 0);
  assert.ok(Date.now() - t0 < 10_000);
  assert.match(r.out, /timed out/);
});

test('config: save is atomic; a corrupt config is quarantined, not silently dropped', () => {
  const { home, restore } = withHome();
  try {
    const savedPath = cfgMod.save({ server: 'http://127.0.0.1:9', serverVersion: '1.2.3' });
    assert.ok(fs.existsSync(savedPath));
    assert.deepStrictEqual(fs.readdirSync(home).filter((f) => f.includes('.tmp-')), [], 'no temp files remain');
    assert.strictEqual(cfgMod.load().serverVersion, '1.2.3');
    cfgMod.save({ server: 'http://127.0.0.1:10', serverVersion: '1.2.4' }); // rename-over works
    assert.strictEqual(cfgMod.load().serverVersion, '1.2.4');

    fs.writeFileSync(cfgMod.configPath(), '{ this is not json');
    const realWrite = process.stderr.write;
    let said = '';
    process.stderr.write = (chunk) => { said += chunk; return true; };
    let loaded;
    try { loaded = cfgMod.load(); } finally { process.stderr.write = realWrite; }
    assert.strictEqual(loaded.server, cfgMod.DEFAULT_SERVER, 'defaults returned');
    assert.match(said, /moved aside/, 'the user is told what happened');
    const quarantined = fs.readdirSync(home).filter((f) => f.startsWith('config.json.corrupt-'));
    assert.strictEqual(quarantined.length, 1, 'the bad file is preserved aside, not deleted');
    assert.ok(!fs.existsSync(cfgMod.configPath()), 'the corrupt file no longer blocks the path');
  } finally { restore(); }
});

test('tailLogs: returns the tail of a large log without the head', () => {
  const { home, restore } = withHome();
  try {
    const lines = [];
    for (let i = 0; i < 5000; i++) lines.push('log line ' + i);
    fs.writeFileSync(path.join(home, 'logs', 'server.log'), lines.join('\n') + '\n');
    const tail = serverLib.tailLogs(10);
    assert.ok(tail.includes('log line 4999'));
    assert.ok(!tail.includes('log line 4000'));
    assert.ok(tail.split('\n').length <= 11);
  } finally { restore(); }
});

/* ---------------- v21.4.1: hash-polluted markers (the Windows setup failure)
 * The hash-locked requirements file joins each requirement with its
 * --hash continuations; requirementModules must hand the probe CLEAN
 * markers. In v21.4.0 the hashes rode along, the strict parser threw,
 * its conservative fallback marked uvloop + exceptiongroup "required",
 * and Windows setup failed demanding modules pip had skipped. */
test('requirementModules: hash-locked requirements yield clean markers', () => {
  for (const rel of ['../../requirements.txt', '../server/requirements.txt']) {
    const reqPath = path.join(__dirname, rel);
    if (!fs.existsSync(reqPath)) continue;
    const mods = serverLib.requirementModules(reqPath);
    assert.ok(mods.length >= 25, `${rel}: expected the full pinned set, got ${mods.length}`);
    for (const m of mods) {
      assert.ok(!m.marker.includes('--'), `${rel}: ${m.dist} marker polluted: ${m.marker.slice(0, 80)}`);
    }
    const byDist = Object.fromEntries(mods.map((m) => [m.dist, m.marker]));
    assert.equal(byDist['uvloop'], 'sys_platform != "win32"');
    assert.equal(byDist['exceptiongroup'], 'python_version < "3.11"');
    assert.equal(byDist['tzdata'], 'sys_platform == "win32"');
    assert.equal(byDist['psycopg-binary'], 'platform_machine != "ARM64"');
  }
});

test('marker probe: a win32 environment skips the Windows-excluded modules', { skip: !spawnSyncOk('python3') }, () => {
  const src = fs.readFileSync(path.join(__dirname, '../lib/server.js'), 'utf8');
  const script = src.match(/const LIBRARY_CHECK_SCRIPT = `\n([\s\S]*?)`;/)[1];
  const winScript = script.replace('"sys_platform": sys.platform', '"sys_platform": "win32"');
  assert.notEqual(winScript, script);
  const mods = JSON.stringify([
    { dist: 'uvloop', module: 'uvloop', marker: 'sys_platform != "win32"' },
    { dist: 'exceptiongroup', module: 'exceptiongroup', marker: 'python_version < "3.11"' },
    { dist: 'fakepkg', module: 'definitely_missing_mod_xyz', marker: 'sys_platform != "win32"' },
  ]);
  const runProbe = (s) => JSON.parse(execFileSync('python3', ['-c', s, mods], { encoding: 'utf8' }).trim().split('\n').pop());
  assert.deepEqual(runProbe(winScript).missing, []);
  assert.ok(runProbe(script).missing.includes('definitely_missing_mod_xyz'));
});

/* --------------------------------------- F. runtime reuse (v21.4.2) */
const render = require('../lib/render');

test('startDetached: a verified running server is reused — prepareRuntime never runs', async () => {
  const fake = { pid: 4242, port: 8123, version: serverLib.VERSION, health: { product: 'AgentQon' } };
  let prepared = false;
  const rec = await serverLib.startDetached({
    runningServer: async () => fake,
    prepareRuntime: () => { prepared = true; throw new Error('prepareRuntime must not be called'); },
  });
  assert.strictEqual(rec, fake, 'the running server record is returned as-is');
  assert.strictEqual(prepared, false, 'no runtime preparation, no decryption, when a server already runs');
});

test('prepareRuntime: refuses to rebuild a runtime dir a live server is running from', () => {
  const { restore } = withHome();
  try {
    const p = serverLib.paths();
    fs.mkdirSync(p.serverDir, { recursive: true });
    fs.writeFileSync(path.join(p.serverDir, 'install.json'), '{}');
    fs.mkdirSync(p.runDir, { recursive: true });
    const sentinel = path.join(p.runDir, 'keep.txt');
    fs.writeFileSync(sentinel, 'live payload');
    /* The pid record points at THIS process (alive) running from runDir. */
    fs.writeFileSync(p.pidPath, JSON.stringify({ pid: process.pid, port: 8123, version: serverLib.VERSION, runtimeDir: p.runDir, startedAt: new Date().toISOString() }));
    const inUse = serverLib.runtimeInUse(p);
    assert.ok(inUse && inUse.pid === process.pid, 'runtimeInUse spots the live server on this dir');
    assert.throws(
      () => serverLib.prepareRuntime(p),
      (e) => /running from this runtime/.test(e.message) && /agentqon stop/.test(e.message) && !/^EPERM/.test(e.message),
      'fails with the human remedy, never a raw EPERM');
    assert.strictEqual(fs.readFileSync(sentinel, 'utf8'), 'live payload', 'the live runtime was not wiped');
  } finally { restore(); }
});

test('prepareRuntime: a locked-file EPERM during decrypt becomes the human remedy', () => {
  const { restore } = withHome();
  try {
    const p = serverLib.paths();
    fs.mkdirSync(p.serverDir, { recursive: true });
    fs.writeFileSync(path.join(p.serverDir, 'install.json'), '{}');
    const raw = new Error("EPERM: operation not permitted, unlink '\\\\?\\C:\\Users\\X\\.agentqon\\run\\x\\app\\main.py'");
    raw.code = 'EPERM';
    assert.throws(
      () => serverLib.prepareRuntime(p, {
        verifyInstalled: () => ({ ok: true }),
        loadPayloadKey: () => Buffer.alloc(32),
        decryptTree: () => { throw raw; },
      }),
      (e) => /runtime copy is locked/.test(e.message) && /agentqon stop/.test(e.message) && /close other agentqon terminals/.test(e.message),
      'EPERM is translated, naming `agentqon stop` as the remedy');
    /* The translator touches ONLY lock-class codes. */
    const other = new Error('integrity check failed');
    assert.strictEqual(serverLib.translateFsLockError(other), other, 'non-lock errors pass through untouched');
    const enoent = new Error('gone'); enoent.code = 'ENOENT';
    assert.strictEqual(serverLib.translateFsLockError(enoent), enoent);
    for (const code of ['EACCES', 'EBUSY']) {
      const e = new Error('locked'); e.code = code;
      assert.match(serverLib.translateFsLockError(e).message, /runtime copy is locked/);
    }
  } finally { restore(); }
});

/* ---------------- audit C: doctor's own probes carry Shield credentials
 * cmdDoctor built its Api from the raw config file — no local token,
 * no channel secret — so against the packaged local server its
 * /v1/model-state probe was refused (silent /health fallback) and
 * /v1/agents reported "could not read" on a healthy install. */
const commands = require('../lib/commands');
const shieldMod = require('../lib/shield');

test('doctorApiCfg: local mode attaches the Shield credentials, remote does not', () => {
  const { restore } = withHome();
  try {
    const creds = shieldMod.ensureCredentials();
    const local = commands.doctorApiCfg({ server: 'http://x' }, 'http://127.0.0.1:8000', true);
    assert.strictEqual(local.localToken, creds.token);
    assert.strictEqual(local.channelSecret, creds.channelSecret);
    assert.strictEqual(local.server, 'http://127.0.0.1:8000');
    const remote = commands.doctorApiCfg({ server: 'http://x', token: 'omk_t' }, 'https://hosted.example', false);
    assert.strictEqual(remote.localToken, undefined);
    assert.strictEqual(remote.channelSecret, undefined);
    assert.strictEqual(remote.token, 'omk_t');
  } finally { restore(); }
});

test('doctor: a plain (repo/dev) install is reported as installed, not "not installed yet"', async () => {
  const { home, restore } = withHome();
  const realWrite = process.stdout.write;
  let outText = '';
  process.stdout.write = (chunk) => { outText += chunk; return true; };
  try {
    const p = serverLib.paths();
    fs.mkdirSync(path.join(p.serverDir, 'app'), { recursive: true });
    fs.writeFileSync(path.join(p.serverDir, 'app', 'main.py'), '# plain bundle\n');
    await commands.cmdDoctor();
    assert.match(outText, /plain bundle installed/, 'plain install must be reported honestly');
    assert.ok(!outText.includes('Shield payload') || !/not installed yet/.test(outText.split('\n').find((l) => l.includes('Shield payload')) || ''),
      'the Shield row must not claim the payload is missing when it is installed');
  } finally {
    process.stdout.write = realWrite;
    restore();
  }
});

/* ---------------- audit C: piped stdin answers must reach the prompts
 * Each ask() used to create a fresh readline; with piped input the
 * first interface buffered the whole pipe and its 'close' handler
 * resolved the default before any line was delivered — `agentqon
 * login` read the bare "https://" default and failed, and every
 * scripted answer was silently dropped. */
test('prompt: piped answers arrive in order, defaults only on EOF', () => {
  const script = `
    const prompt = require(${JSON.stringify(path.join(__dirname, '..', 'lib', 'prompt'))});
    (async () => {
      console.log('url=' + JSON.stringify(await prompt.ask('URL', 'https://')));
      console.log('email=' + JSON.stringify(await prompt.ask('Email:')));
      console.log('pw=' + JSON.stringify(await prompt.askPassword('Password:')));
      console.log('late=' + JSON.stringify(await prompt.ask('Late', 'fallback')));
    })();
  `;
  const out = execFileSync(process.execPath, ['-e', script], {
    input: 'http://127.0.0.1:8000\naudit@example.com\npw123456\n', encoding: 'utf8',
  });
  assert.match(out, /url="http:\/\/127\.0\.0\.1:8000"/);
  assert.match(out, /email="audit@example\.com"/);
  assert.match(out, /pw="pw123456"/);
  assert.match(out, /late="fallback"/, 'EOF resolves to the default, not a hang or a crash');
});

/* ---------------- audit C: CLI model pinning parity with the web picker
 * The web chatbox pins via contract.routing_mode/pinned_model_id
 * (v21.4.2); the CLI sent neither key, so a terminal user could not
 * steer a run off a model auto-routing picked. `run --model` now
 * sets exactly the web picker's contract fields. */
const missionLib = require('../lib/mission');

test('missionBody: --model pins routing; without it the contract stays auto', () => {
  const pinned = missionLib.missionBody('Do the thing', { model: 'auto/qwen' });
  assert.strictEqual(pinned.contract.routing_mode, 'pinned_model');
  assert.strictEqual(pinned.contract.pinned_model_id, 'auto/qwen');
  const auto = missionLib.missionBody('Do the thing', {});
  assert.strictEqual(auto.contract.routing_mode, undefined);
  assert.strictEqual(auto.contract.pinned_model_id, undefined);
});

test('missionBody: no user-facing budget is ever sent (v21.3.0 simplification law)', () => {
  // Regression (audit R): missionBody used to send
  // constraints.max_cost_usd = opts.budget || 10, silently capping every
  // CLI run at $10 while the UI/API relied on the server's single hidden
  // runaway ceiling. The CLI must send no budget figure at all — not even
  // when a stale caller still passes opts.budget.
  const plain = missionLib.missionBody('Do the thing', {});
  assert.strictEqual(plain.contract.constraints.max_cost_usd, undefined);
  const stale = missionLib.missionBody('Do the thing', { budget: 5 });
  assert.strictEqual(stale.contract.constraints.max_cost_usd, undefined);
});

test('missionExitCode: a failed mission exits 1, completed/cancelled stay 0', () => {
  assert.strictEqual(commands.missionExitCode('failed'), 1);
  assert.strictEqual(commands.missionExitCode('completed'), 0);
  assert.strictEqual(commands.missionExitCode('cancelled'), 0);
});

/* ---------------- audit C: alive-but-silent server must not be duplicated
 * Live repro: `agentqon start` booted a healthy server on :8000;
 * the next command's 1.5 s health probe missed while it was busy,
 * runningServer() said "not running", and startDetached spawned a
 * SECOND server on :8001 against the same data dir, overwriting
 * the pid record — `stop` then killed only the duplicate and the
 * first server was orphaned beyond the CLI's reach. */
test('startDetached: a live recorded pid that stays silent is refused, never duplicated', { skip: process.platform === 'win32' || !spawnSyncOk('python3') }, async () => {
  const { home, restore } = withHome();
  /* A process whose cmdline looks like our uvicorn server but which
   * never answers health on the recorded port. */
  const fake = spawn('python3', ['-c', 'import time; time.sleep(30)  # uvicorn app.main'], { stdio: 'ignore' });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: fake.pid, port: 8999, version: serverLib.VERSION, runtimeDir: null }));
    await assert.rejects(
      serverLib.startDetached({ runningServer: async () => null, aliveWaitMs: 300 }),
      (e) => /alive but not answering/.test(e.message) && /agentqon stop/.test(e.message)
        && /second server/.test(e.message),
      'must refuse with the human remedy instead of spawning a duplicate');
    assert.doesNotThrow(() => process.kill(fake.pid, 0), 'the live server is left alone');
  } finally {
    try { fake.kill('SIGKILL'); } catch { /* gone */ }
    restore();
  }
});

test('awaitLiveServer: a server that starts answering inside the window is reused', { skip: process.platform === 'win32' || !spawnSyncOk('python3') }, async () => {
  const { home, restore } = withHome();
  const fake = spawn('python3', ['-c', 'import time; time.sleep(30)  # uvicorn app.main'], { stdio: 'ignore' });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: fake.pid, port: 8999, version: serverLib.VERSION, runtimeDir: null }));
    const record = { pid: fake.pid, port: 8999, version: serverLib.VERSION, health: { product: 'AgentQon' } };
    let probes = 0;
    const rec = await serverLib.awaitLiveServer({
      runningServer: async () => (++probes >= 2 ? record : null),
      aliveWaitMs: 5_000,
    });
    assert.strictEqual(rec, record, 'the late-answering server is reused, not duplicated');
    assert.ok(probes >= 2, 'the guard actually re-probed');
    /* A pid whose process is clearly NOT our server (recycled) is
     * never awaited and never signalled: the guard steps aside. */
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: process.pid, port: 8999, version: serverLib.VERSION, runtimeDir: null }));
    const skipped = await serverLib.awaitLiveServer({ runningServer: async () => record, aliveWaitMs: 300 });
    assert.strictEqual(skipped, null, 'a foreign-looking pid is stepped aside (recycled-pid safety)');
  } finally {
    try { fake.kill('SIGKILL'); } catch { /* gone */ }
    restore();
  }
});

/* --------------------------------------- G. no budget in the face (v21.4.2) */
test('mission summary: shows the run cost and never a budget or ceiling figure', () => {
  const detail = {
    mission: { id: 'm_test_1', title: 'Grow the thing', status: 'completed', spent: 0.02, budget_limit: 1000, contract: { task_type: 'strategy' } },
    candidates: [], critiques: [], evaluations: [], usage: { total_usd: 0.02 },
  };
  const text = render.renderMissionResult(detail, null);
  assert.match(text, /run cost \$0\.02/, 'the actual run cost still prints');
  assert.ok(!/budget/i.test(text), 'the summary line contains no "budget" wording');
  assert.ok(!text.includes('$1000'), 'the internal ceiling figure never prints');
  const table = render.renderMissions([{ id: 'm_test_1', title: 'Grow the thing', status: 'completed', spent: 1.5, budget_limit: 1000 }]);
  assert.ok(table.includes('$1.50'), 'the missions table still shows what was spent');
  assert.ok(!/budget/i.test(table), 'the missions table has no budget column or figure');
});

/* ================= v22: wizard + chat REPL + evolve (contract R4/R6) ====
 * The guided setup wizard (probe → pick → connect+validate, or the
 * one API-key prompt, or an honest model-less finish), the `agentqon
 * chat` REPL over POST /v1/chat with /agent sessions + approvals, and
 * the evolve report. Wizard/REPL internals take injected deps, so
 * the flows run in-process or as scripted piped-stdin children
 * against stub servers — the prompt-test pattern, no real server. */
const wizard = require('../lib/wizard');
const chatLib = require('../lib/chat');

function captureOut() {
  const chunks = [];
  return { chunks, out: (t) => chunks.push(String(t)), text: () => chunks.join('\n') };
}

function stubHttp(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}
function readBody(req) {
  return new Promise((res) => {
    let b = '';
    req.on('data', (c) => { b += c; });
    req.on('end', () => { try { res(JSON.parse(b || '{}')); } catch { res({}); } });
  });
}

test('wizard probe: a stub local server on an injected port is found; dead endpoints are not', async () => {
  const srv = await stubHttp((req, res) => {
    if (req.url === '/v1/models') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ data: [{ id: 'stub-1' }, { id: 'stub-2' }] }));
    } else { res.writeHead(404); res.end('{}'); }
  });
  try {
    const port = srv.address().port;
    const found = await wizard.probeLocalServers([
      { name: 'Stub', base_url: `http://127.0.0.1:${port}/v1` },
      { name: 'Dead', base_url: 'http://127.0.0.1:1/v1' },
    ], { timeoutMs: 800 });
    assert.strictEqual(found.length, 1);
    assert.strictEqual(found[0].name, 'Stub');
    assert.deepStrictEqual(found[0].models, ['stub-1', 'stub-2']);
  } finally { await new Promise((r) => srv.close(r)); }
});

function fakeWizardApi({ connected = false, validateState = 'active' } = {}) {
  const calls = [];
  return {
    calls,
    async get(p) {
      calls.push(['GET', p]);
      if (p === '/v1/model-state') return connected
        ? { connected: true, model: 'already/model', source: 'byok' }
        : { connected: false, model: null, source: null };
      throw new Error('unexpected GET ' + p);
    },
    async post(p, body) {
      calls.push(['POST', p, body]);
      if (p === '/v1/connections') return { id: 'conn_test_1' };
      if (p === '/v1/connections/conn_test_1/validate') return { state: validateState, catalog_sync: { models: 2 } };
      throw new Error('unexpected POST ' + p);
    },
  };
}

test('wizard: a model already connected short-circuits — no probe, no creation', async () => {
  const api = fakeWizardApi({ connected: true });
  const cap = captureOut();
  let probed = false;
  const res = await wizard.runSetupWizard(api, {
    out: cap.out,
    probe: async () => { probed = true; return []; },
    ask: async () => { throw new Error('must not ask'); },
    askChoice: async () => { throw new Error('must not offer choices'); },
  });
  assert.strictEqual(res.connected, true);
  assert.strictEqual(probed, false);
  assert.ok(!api.calls.some((c) => c[0] === 'POST'), 'no connection is created');
  assert.match(cap.text(), /already connected/);
});

test('wizard: picking a found local server creates + validates a local (no-key) connection', async () => {
  const api = fakeWizardApi();
  const cap = captureOut();
  const res = await wizard.runSetupWizard(api, {
    out: cap.out,
    probe: async () => [{ name: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', models: ['qwen'] }],
    askChoice: async (q, options) => { assert.strictEqual(options.length, 3); return 0; },
    ask: async () => { throw new Error('local pick must not ask for a key'); },
    askPassword: async () => { throw new Error('local pick must not ask for a key'); },
  });
  assert.strictEqual(res.connected, true);
  const create = api.calls.find((c) => c[0] === 'POST' && c[1] === '/v1/connections');
  assert.ok(create, 'a connection was created');
  assert.strictEqual(create[2].kind, 'local_endpoint');
  assert.strictEqual(create[2].base_url, 'http://127.0.0.1:11434/v1');
  assert.ok(!('api_key' in create[2]), 'a local connection carries no key');
  assert.ok(api.calls.some((c) => c[0] === 'POST' && /\/validate$/.test(c[1])), 'the connection was validated');
  assert.match(cap.text(), /Setup complete/);
});

test('wizard: no local server → the API-key path creates a validated BYOK connection', async () => {
  const api = fakeWizardApi();
  const cap = captureOut();
  const res = await wizard.runSetupWizard(api, {
    out: cap.out,
    probe: async () => [],
    askChoice: async () => 0, // "Paste an API base URL + key"
    ask: async () => 'https://api.example.test/v1',
    askPassword: async () => 'sk-test-key',
  });
  assert.strictEqual(res.connected, true);
  const create = api.calls.find((c) => c[0] === 'POST' && c[1] === '/v1/connections');
  assert.strictEqual(create[2].kind, 'direct_provider');
  assert.strictEqual(create[2].base_url, 'https://api.example.test/v1');
  assert.strictEqual(create[2].api_key, 'sk-test-key');
});

test('wizard: skipping finishes model-less and prints the exact next steps', async () => {
  const api = fakeWizardApi();
  const cap = captureOut();
  const res = await wizard.runSetupWizard(api, {
    out: cap.out,
    probe: async () => [],
    askChoice: async (q, options) => options.length - 1, // Skip
  });
  assert.strictEqual(res.connected, false);
  assert.ok(!api.calls.some((c) => c[0] === 'POST'), 'nothing was created');
  assert.match(cap.text(), /no model connected yet/);
  assert.match(cap.text(), /agentqon setup/);
});

test('wizard: a failed validation is reported plainly and setup still finishes honestly', async () => {
  const api = fakeWizardApi({ validateState: 'error' });
  const cap = captureOut();
  const res = await wizard.runSetupWizard(api, {
    out: cap.out,
    probe: async () => [{ name: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', models: [] }],
    askChoice: async () => 0,
  });
  assert.strictEqual(res.connected, false);
  assert.match(cap.text(), /did not become active/);
  assert.match(cap.text(), /no model connected yet/);
});

/* The wizard is an interactive-only offer: bin gates it on a TTY and
 * --no-wizard, so scripted setup is byte-for-byte the old behavior.
 * Pin the gate in the source (the flows above pin the wizard itself). */
test('setup gate: wizard needs a TTY, honours --no-wizard; chat + evolve commands wired', () => {
  const bin = fs.readFileSync(path.join(__dirname, '..', 'bin', 'agentqon.js'), 'utf8');
  const setupCase = bin.slice(bin.indexOf("case 'setup'"), bin.indexOf("case 'chat'"));
  assert.match(setupCase, /flags\['no-wizard'\]/);
  assert.match(setupCase, /process\.stdin\.isTTY/);
  assert.match(setupCase, /runSetupWizard/);
  assert.match(bin, /case 'chat':/);
  assert.match(bin, /case 'evolve':/);
});

/* ---- chat REPL, scripted over piped stdin against a stub server ---- */
/* The chat child talks HTTP back to the stub server hosted by THIS
 * process, so it must run async (execFileSync would freeze the event
 * loop and deadlock the stub) — and via spawn + an explicit stdin
 * write: async execFile's `input` option never delivers the data to
 * the child on this Node (verified empirically; spawn works). */
function runChatChild(input, stubUrl) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['-e', chatChildScript()], {
      env: { ...process.env, STUB_URL: stubUrl },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* gone */ }
      reject(new Error(`chat child timed out\n--- child stdout ---\n${out}\n--- child stderr ---\n${err}`));
    }, 30_000);
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(`chat child exited ${code}\n--- child stdout ---\n${out}\n--- child stderr ---\n${err}`));
    });
    child.stdin.write(input);
    child.stdin.end();
  });
}

function chatChildScript() {
  return `
    const { Api } = require(${JSON.stringify(path.join(__dirname, '..', 'lib', 'api'))});
    const chat = require(${JSON.stringify(path.join(__dirname, '..', 'lib', 'chat'))});
    (async () => {
      const api = new Api({ server: process.env.STUB_URL });
      await chat.start(api, { base: process.env.STUB_URL, agentPollMs: 25 });
    })().catch((e) => { console.error('CHILD FAIL', e); process.exit(1); });
  `;
}

test('chat REPL: greeting → stub reply printed; /model lists + pins; /agent 404 is honest; /exit quits 0', async () => {
  const chatBodies = [];
  const srv = await stubHttp(async (req, res) => {
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (req.method === 'POST' && req.url === '/v1/chat') {
      chatBodies.push(await readBody(req));
      return send(200, { reply: 'Hello from the stub model', model: 'stub-1', source: 'byok' });
    }
    if (req.url === '/v1/model-state') return send(200, { connected: true, model: 'stub-1', source: 'byok' });
    if (req.url === '/v1/models') return send(200, { models: [{ model_id: 'stub-1' }, { model_id: 'stub-2' }], connections: [] });
    if (req.url === '/v1/missions?limit=15') return send(200, { missions: [] });
    if (req.method === 'POST' && req.url === '/v1/agent-sessions') return send(404, { error: { message: 'not found' } });
    return send(404, { error: { message: 'unhandled ' + req.url } });
  });
  try {
    const url = `http://127.0.0.1:${srv.address().port}`;
    const out = await runChatChild('hello there\n/model\n/model 2\n/agent build a thing\n/exit\n', url);
    assert.match(out, /Hello from the stub model/, 'the chat reply is printed');
    assert.match(out, /current model/, '/model shows the current model');
    assert.match(out, /stub-2/, 'the catalog lists stub-2');
    assert.match(out, /chat pinned to stub-2/, '/model 2 pins the catalog model');
    assert.match(out, /agent mode is not available in this server build/, 'a 404 agent endpoint gets the honest line');
    assert.match(out, /chat closed/, 'the session closes cleanly');
    assert.strictEqual(chatBodies.length, 1, 'exactly one chat call was made');
    assert.strictEqual(chatBodies[0].message, 'hello there');
    assert.ok(!('model' in chatBodies[0]), 'no pin is sent before one is chosen');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('chat REPL: a server that rejects chat pins (422) drops the pin and still answers', async () => {
  const srv = await stubHttp(async (req, res) => {
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (req.method === 'POST' && req.url === '/v1/chat') {
      const body = await readBody(req);
      if (body.model) return send(422, { detail: [{ loc: ['body', 'model'], msg: 'Extra inputs are not permitted' }] });
      return send(200, { reply: 'unpinned answer', model: 'stub-1', source: 'byok' });
    }
    if (req.url === '/v1/model-state') return send(200, { connected: true, model: 'stub-1', source: 'byok' });
    if (req.url === '/v1/models') return send(200, { models: [{ model_id: 'stub-1' }], connections: [] });
    return send(404, { error: { message: 'unhandled ' + req.url } });
  });
  try {
    const url = `http://127.0.0.1:${srv.address().port}`;
    const out = await runChatChild('/model 1\nhi again\n/exit\n', url);
    assert.match(out, /does not support chat model pins/);
    assert.match(out, /unpinned answer/, 'the retry without the pin delivers the reply');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('chat REPL: /agent drives the real session protocol — steps stream, the parked action prompts, the decision resumes', async () => {
  /* Mirrors app/agent_loop.py exactly: POST /v1/agent-sessions →
   * {session}; POST …/run blocks until the session parks with
   * status awaiting_approval + pending_action; events come from
   * GET …/events?after=<event_id> in the platform envelope;
   * POST …/approve {decision} resumes the loop. */
  const decisions = [];
  let approved = false;
  const allEvents = [
    { event_id: 'evt_1', event_type: 'AgentSessionCreated', payload: {} },
    { event_id: 'evt_2', event_type: 'AgentStep', payload: { step: 1, thought: 'reading the repo' } },
    { event_id: 'evt_3', event_type: 'ToolCall', payload: { step: 1, call_id: 'call_1', tool: 'run_shell', args: { command: 'npm test' }, needs_approval: true } },
    { event_id: 'evt_4', event_type: 'ApprovalNeeded', payload: { call_id: 'call_1', tool: 'run_shell', args: { command: 'npm test' }, reason: 'mutating shell command' } },
    { event_id: 'evt_5', event_type: 'ToolResult', payload: { call_id: 'call_1', tool: 'run_shell', ok: true, summary: '3 tests passed' } },
    { event_id: 'evt_6', event_type: 'AgentCompleted', payload: { summary: 'all tests green', steps_used: 2 } },
  ];
  const srv = await stubHttp(async (req, res) => {
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    const u = new URL(req.url, 'http://stub');
    if (req.url === '/v1/model-state') return send(200, { connected: true, model: 'stub-1', source: 'byok' });
    if (req.method === 'POST' && req.url === '/v1/agent-sessions') {
      await readBody(req);
      return send(201, { session: { id: 'sess_1', status: 'created', goal: 'fix the tests' } });
    }
    if (req.method === 'POST' && req.url === '/v1/agent-sessions/sess_1/run') {
      await readBody(req);
      return send(200, { session: { id: 'sess_1', status: 'awaiting_approval',
        pending_action: { call_id: 'call_1', tool: 'run_shell', args: { command: 'npm test' }, step: 1, reason: 'mutating shell command' } } });
    }
    if (req.method === 'POST' && req.url === '/v1/agent-sessions/sess_1/approve') {
      decisions.push(await readBody(req));
      approved = true;
      return send(200, { session: { id: 'sess_1', status: 'completed', summary: 'all tests green', steps_used: 2, spent_usd: 0.01 } });
    }
    if (req.method === 'GET' && u.pathname === '/v1/agent-sessions/sess_1/events') {
      const after = u.searchParams.get('after');
      const upto = approved ? allEvents.length : 4;
      let evts = allEvents.slice(0, upto);
      if (after) {
        const idx = evts.findIndex((e) => e.event_id === after);
        evts = idx >= 0 ? evts.slice(idx + 1) : evts;
      }
      return send(200, { events: evts });
    }
    return send(404, { error: { message: 'unhandled ' + req.url } });
  });
  try {
    const url = `http://127.0.0.1:${srv.address().port}`;
    const out = await runChatChild('/agent fix the tests\ny\n/exit\n', url);
    assert.match(out, /reading the repo/, 'step events print as they land');
    assert.match(out, /approval needed: run_shell/, 'the parked action prompts with its tool');
    assert.match(out, /mutating shell command/, '…and its reason');
    assert.deepStrictEqual(decisions, [{ decision: 'approve' }], 'the y answer is posted as the decision');
    assert.match(out, /all tests green/, 'the completion summary prints');
    assert.match(out, /agent session completed/);
  } finally { await new Promise((r) => srv.close(r)); }
});

/* ---- evolve ---- */
test('evolve: patterns, lessons and tunings render; a 404 falls back honestly', async () => {
  const realWrite = process.stdout.write;
  let outText = '';
  process.stdout.write = (chunk) => { outText += chunk; return true; };
  try {
    const report = await commands.cmdEvolve({
      api: { get: async () => ({
        /* the real app/evolve.py shape: patterns/lessons are
         * objects; tunings nest under report.tunings */
        failure_patterns: [{ category: 'provider_rate_limit', subject_type: 'connection', subject: 'conn_1', count: 4 }],
        lessons: [{ subject_type: 'connection', subject: 'conn_1', outcome: 'failed', category: 'provider_rate_limit', cause: 'pace calls after a 429' }],
        tunings: {
          applied: [{ kind: 'provider_pacing', connection_id: 'conn_1', gap_s: 5, detail: 'Calls to this connection are paced (5s minimum gap)' }],
          proposed: [{ kind: 'proposal', category: 'output_format', detail: 'prefer models advertising structured output' }],
        },
        generated_at: '2026-10-21T10:00:00Z',
      }) },
    });
    assert.ok(report, 'the report object is returned');
    let text = outText;
    assert.match(text, /provider_rate_limit/);
    assert.match(text, /pace calls after a 429/);
    assert.match(text, /Tunings applied/);
    assert.match(text, /5s minimum gap/);
    assert.match(text, /Tunings proposed \(not applied\)/);

    outText = '';
    const { ApiError } = require('../lib/api');
    const missing = await commands.cmdEvolve({
      api: { get: async () => { throw new ApiError(404, 'HTTP 404'); } },
    });
    assert.strictEqual(missing, null, 'a missing endpoint is a fallback, not a throw');
    text = outText;
    assert.match(text, /not available in this server build/);
    assert.match(text, /npm install -g agentqon@latest/, 'the fallback ends with the exact fix command');
  } finally { process.stdout.write = realWrite; }
});

/* ================= v22.1: Skill Library CLI (contract S4) ============
 * `agentqon skills` (overview / search / show / pin / unpin) against
 * the /v1/skills* API. The commands run in-process with a REAL Api
 * client pointed at a stub HTTP server — query encoding, JSON
 * parsing and POST bodies are exercised for real — with output
 * captured through the same deps.out seam the wizard tests use. */
const skillsLib = require('../lib/skills');
const { Api } = require('../lib/api');

const SKILL_FIXTURE = {
  id: 'sk_code_review',
  name: 'code-review',
  display_name: 'Code Review',
  description: 'Review a diff for bugs, security issues and clarity before it ships.',
  repo: 'addyosmani/agent-skills',
  license: 'MIT',
  body_bytes: 2048,
  pinned: true,
};

function skillsStubServer() {
  const seen = { urls: [], pinBodies: [] };
  const srvPromise = stubHttp(async (req, res) => {
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    const u = new URL(req.url, 'http://stub');
    seen.urls.push(req.method + ' ' + req.url);
    if (u.pathname === '/v1/skills/facets') {
      return send(200, {
        total: 5534, unique_names: 4901,
        repos: [
          { repo: 'addyosmani/agent-skills', count: 24 },
          { repo: 'obra/superpowers', count: 31 },
          { repo: 'anthropics/skills', count: 19 },
        ],
      });
    }
    if (req.method === 'GET' && u.pathname === '/v1/skills') {
      const q = u.searchParams.get('q') || '';
      if (q === 'zzz-no-match') return send(200, { skills: [], total: 0 });
      return send(200, { skills: [SKILL_FIXTURE, { ...SKILL_FIXTURE, id: 'sk_tests', name: 'testing', display_name: 'Testing', pinned: false }], total: 2 });
    }
    if (req.method === 'GET' && u.pathname === '/v1/skills/sk_code_review') {
      return send(200, { skill: { ...SKILL_FIXTURE, path: 'skills/code-review/SKILL.md', url: 'https://github.com/addyosmani/agent-skills', body: '# Code Review\n\n1. Read the diff.\n2. Flag the risks.', truncated: false } });
    }
    if (req.method === 'GET' && u.pathname === '/v1/skills/sk_big') {
      return send(200, { skill: { ...SKILL_FIXTURE, id: 'sk_big', path: 'skills/big/SKILL.md', url: 'https://github.com/x/y', body: 'partial text…', truncated: true, pinned: false } });
    }
    if (req.method === 'POST' && u.pathname === '/v1/skills/sk_code_review/pin') {
      const body = await readBody(req);
      seen.pinBodies.push(body);
      return send(200, { skill_id: 'sk_code_review', pinned: body.pinned });
    }
    return send(404, { error: { message: 'unhandled ' + req.url } });
  });
  return srvPromise.then((srv) => ({ srv, seen, api: new Api({ server: `http://127.0.0.1:${srv.address().port}` }) }));
}

test('skills overview: totals, biggest collections first, and how to search', async () => {
  const { srv, api } = await skillsStubServer();
  try {
    const cap = captureOut();
    const facets = await commands.cmdSkills([], {}, { api, out: cap.out });
    assert.ok(facets, 'the facets payload is returned');
    const text = cap.text();
    assert.match(text, /5,534 skills in the library/, 'the total is stated');
    assert.match(text, /4,901 unique names/);
    assert.ok(text.indexOf('obra/superpowers') < text.indexOf('addyosmani/agent-skills'), 'collections sort by size, biggest first');
    assert.match(text, /agentqon skills search <words…>/, 'the search how-to is printed');
    assert.match(text, /agentqon skills pin <id>/);
    assert.match(text, /pulled in automatically/, 'the orchestration note is present');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills search: ranked rows with provenance + pin marker; query is encoded; --repo filters', async () => {
  const { srv, api, seen } = await skillsStubServer();
  try {
    const cap = captureOut();
    const data = await commands.cmdSkills(['search', 'code', 'review'], {}, { api, out: cap.out });
    assert.strictEqual(data.total, 2);
    const text = cap.text();
    assert.match(text, /Code Review/, 'the display name renders');
    assert.match(text, /★/, 'the pinned marker renders');
    assert.match(text, /addyosmani\/agent-skills · MIT/, 'repo + license render on the row');
    assert.ok(seen.urls.some((u) => u.includes('q=code%20review') && u.includes('limit=20')), 'the words are joined and URL-encoded: ' + seen.urls.join(' | '));

    const cap2 = captureOut();
    await commands.cmdSkills(['search', 'seo'], { repo: 'obra/superpowers' }, { api, out: cap2.out });
    assert.ok(seen.urls.some((u) => u.includes('repo=obra%2Fsuperpowers')), 'the --repo filter reaches the API encoded: ' + seen.urls.join(' | '));
    assert.match(cap2.text(), /in obra\/superpowers/);
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills search: an honest empty state, never a fake result', async () => {
  const { srv, api } = await skillsStubServer();
  try {
    const cap = captureOut();
    await commands.cmdSkills(['search', 'zzz-no-match'], {}, { api, out: cap.out });
    const text = cap.text();
    assert.match(text, /No skills match 'zzz-no-match'/);
    assert.match(text, /broader words/);
    assert.ok(!text.includes('Code Review'), 'no fixture leaks into the empty state');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills show: provenance block, then the body; a truncated skill says so', async () => {
  const { srv, api } = await skillsStubServer();
  try {
    const cap = captureOut();
    const skill = await commands.cmdSkills(['show', 'sk_code_review'], {}, { api, out: cap.out });
    assert.strictEqual(skill.name, 'code-review');
    const text = cap.text();
    assert.match(text, /source\s+addyosmani\/agent-skills/);
    assert.match(text, /path\s+skills\/code-review\/SKILL\.md/);
    assert.match(text, /url\s+https:\/\/github\.com\/addyosmani\/agent-skills/);
    assert.match(text, /license\s+MIT/);
    assert.match(text, /★ pinned/);
    assert.ok(text.includes('# Code Review'), 'the body prints');
    assert.ok(text.includes('2. Flag the risks.'), '…in full');
    assert.ok(!/truncated/.test(text), 'no truncation note for a whole skill');

    const cap2 = captureOut();
    await commands.cmdSkills(['show', 'sk_big'], {}, { api, out: cap2.out });
    assert.match(cap2.text(), /truncated in the library/, 'a truncated skill carries the note + points at the source');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills pin/unpin: the toggle posts {pinned} and confirms what pins do', async () => {
  const { srv, api, seen } = await skillsStubServer();
  try {
    const cap = captureOut();
    await commands.cmdSkills(['pin', 'sk_code_review'], {}, { api, out: cap.out });
    assert.match(cap.text(), /✓ pinned sk_code_review/);
    assert.match(cap.text(), /offered first when they match a task/, 'the confirmation explains pins');
    const cap2 = captureOut();
    await commands.cmdSkills(['unpin', 'sk_code_review'], {}, { api, out: cap2.out });
    assert.match(cap2.text(), /✓ unpinned sk_code_review/);
    assert.match(cap2.text(), /stays in the library/);
    assert.deepStrictEqual(seen.pinBodies, [{ pinned: true }, { pinned: false }], 'both toggles posted the exact contract body');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills: an older server (404) gets the honest fallback + the exact update command', async () => {
  const srv = await stubHttp((req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: { message: 'not found' } })); });
  try {
    const api = new Api({ server: `http://127.0.0.1:${srv.address().port}` });
    const cap = captureOut();
    const r = await commands.cmdSkills([], {}, { api, out: cap.out });
    assert.strictEqual(r, null, 'a missing library is a fallback, not a throw');
    assert.match(cap.text(), /not available in this server build/);
    assert.match(cap.text(), /npm install -g agentqon@latest/, 'the fallback ends with the exact fix command');
    const cap2 = captureOut();
    const r2 = await commands.cmdSkills(['show', 'sk_anything'], {}, { api, out: cap2.out });
    assert.strictEqual(r2, null, 'detail calls fall back the same way');
  } finally { await new Promise((r) => srv.close(r)); }
});

test('skills: usage errors are plain; bin wiring + help rows are pinned', async () => {
  await assert.rejects(() => skillsLib.runSkills(['search'], { api: {}, out: () => {} }), /usage: agentqon skills search/);
  await assert.rejects(() => skillsLib.runSkills(['show'], { api: {}, out: () => {} }), /usage: agentqon skills show/);
  await assert.rejects(() => skillsLib.runSkills(['pin'], { api: {}, out: () => {} }), /usage: agentqon skills pin/);
  await assert.rejects(() => skillsLib.runSkills(['bogus'], { api: {}, out: () => {} }), /unknown skills command "bogus"/);

  const bin = fs.readFileSync(path.join(__dirname, '..', 'bin', 'agentqon.js'), 'utf8');
  assert.match(bin, /case 'skills': await commands\.cmdSkills\(positional\.slice\(1\), flags\)/);
  assert.match(bin, /'repo'/, '--repo is a value flag');

  const realWrite = process.stdout.write;
  let outText = '';
  process.stdout.write = (chunk) => { outText += chunk; return true; };
  try { commands.cmdHelp(); } finally { process.stdout.write = realWrite; }
  assert.match(outText, /agentqon skills/, 'help lists the library overview');
  assert.match(outText, /agentqon skills search <words…>/);
  assert.match(outText, /terminal harness v22\.8\.1/, 'the banner carries the bumped version');
});

/* ================= v22.2: Extended Library Pack =================
 * The optional signed download that returns the 213
 * security-research skills npm does not carry. Pinned here:
 * (a) a good pack installs; a tampered pack and a wrongly-signed
 *     pack are REFUSED in plain words, with nothing installed;
 * (c) the CLI overview states the honest two numbers in both
 *     states (boxed alone / boxed + extended);
 * (d) --remove deletes the pack cleanly;
 * (e) the setup offer asks first (default no) and declining — or
 *     a non-interactive setup — downloads NOTHING.
 * The server/library half lives in tests/unit/test_extended_pack.py
 * and tests/integration/test_extended_pack_api.py. */
const crypto2 = require('crypto');
const extendedLib = require('../lib/extended');
const shieldForExt = require('../lib/shield');

function captureLines() {
  const lines = [];
  return { lines, out: (t) => lines.push(String(t)), text: () => lines.join('\n') };
}

/* A 1-skill stand-in pack, signed by a throwaway key — the
 * production pins are injected through deps, exactly as the real
 * constants are wired in runExtended/installExtended. */
function buildExtendedFixture() {
  const dir = tmpDir('aq-ext-fixture-');
  const stage = path.join(dir, 'stage');
  const skillDir = path.join(stage, 'skills', 'ext-fixture-skill');
  fs.mkdirSync(skillDir, { recursive: true });
  fs.writeFileSync(path.join(skillDir, 'skill.md'), '# Fixture skill\n\nA stand-in body.\n');
  fs.writeFileSync(path.join(skillDir, 'meta.json'),
    JSON.stringify({ name: 'ext-fixture-skill', repo: 'acme/dev-skills', license: 'MIT' }));
  fs.writeFileSync(path.join(stage, 'pack.json'),
    JSON.stringify({ pack: 'extended-library', version: 1, skill_count: 1 }));
  const tarball = path.join(dir, 'extended-library-v1.tar.gz');
  execFileSync('tar', ['-czf', tarball, '-C', stage, 'pack.json', 'skills']);
  const files = {};
  for (const rel of shieldForExt.walkFiles(stage)) {
    files[rel] = shieldForExt.sha256Hex(fs.readFileSync(path.join(stage, ...rel.split('/'))));
  }
  const manifest = {
    pack: 'extended-library', version: 1, count: 1,
    boxed_total: 4840, total_with_pack: 5053,
    tarball: 'extended-library-v1.tar.gz',
    tarball_sha256: shieldForExt.sha256Hex(fs.readFileSync(tarball)),
    files,
  };
  const manifestBytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
  const kp = crypto2.generateKeyPairSync('ed25519');
  fs.writeFileSync(path.join(dir, 'manifest.json'), manifestBytes);
  fs.writeFileSync(path.join(dir, 'manifest.sig'),
    crypto2.sign(null, manifestBytes, kp.privateKey).toString('base64') + '\n');
  return {
    serveDir: dir, tarballPath: tarball, manifestBytes,
    pubPem: kp.publicKey.export({ type: 'spki', format: 'pem' }),
    tarballSha: manifest.tarball_sha256,
  };
}

function extDeps(fx, homeDir, extra = {}) {
  return {
    homeDir, packBaseUrl: fx.serveDir, publicKeyPem: fx.pubPem,
    expectedTarballSha256: fx.tarballSha, expectedSkillCount: 1,
    out: () => {}, ...extra,
  };
}

test('extended pack: a good pack installs into the data home only', async () => {
  const fx = buildExtendedFixture();
  const home = tmpDir('aq-ext-home-');
  const r = await extendedLib.installExtended(extDeps(fx, home));
  assert.strictEqual(r.installed, true);
  assert.strictEqual(r.count, 1);
  const dir = path.join(home, 'extended-library');
  assert.ok(fs.existsSync(path.join(dir, 'skills', 'ext-fixture-skill', 'skill.md')));
  assert.ok(fs.existsSync(path.join(dir, 'manifest.json')));
  assert.deepStrictEqual(extendedLib.packStatus({ homeDir: home }).installed, true);
  /* a second install is a no-op that says so */
  const again = await extendedLib.installExtended(extDeps(fx, home));
  assert.strictEqual(again.already, true);
});

test('extended pack: tampered tarball and wrong signature are refused', async () => {
  const fx = buildExtendedFixture();
  /* flip one byte of the tarball — the hash no longer matches */
  const tamperedDir = tmpDir('aq-ext-tampered-');
  for (const f of ['manifest.json', 'manifest.sig']) {
    fs.copyFileSync(path.join(fx.serveDir, f), path.join(tamperedDir, f));
  }
  const tb = Buffer.from(fs.readFileSync(fx.tarballPath));
  tb[120] = tb[120] ^ 0xff;
  fs.writeFileSync(path.join(tamperedDir, 'extended-library-v1.tar.gz'), tb);
  const home1 = tmpDir('aq-ext-home-');
  await assert.rejects(
    () => extendedLib.installExtended(extDeps(fx, home1, { packBaseUrl: tamperedDir })),
    /failed its safety check/);
  assert.strictEqual(extendedLib.packStatus({ homeDir: home1 }).installed, false);
  assert.ok(!fs.existsSync(path.join(home1, 'extended-library')));

  /* a manifest signed by a DIFFERENT key — signature refuses */
  const badSigDir = tmpDir('aq-ext-badsig-');
  fs.copyFileSync(fx.tarballPath, path.join(badSigDir, 'extended-library-v1.tar.gz'));
  fs.writeFileSync(path.join(badSigDir, 'manifest.json'), fx.manifestBytes);
  const other = crypto2.generateKeyPairSync('ed25519');
  fs.writeFileSync(path.join(badSigDir, 'manifest.sig'),
    crypto2.sign(null, fx.manifestBytes, other.privateKey).toString('base64'));
  const home2 = tmpDir('aq-ext-home-');
  await assert.rejects(
    () => extendedLib.installExtended(extDeps(fx, home2, { packBaseUrl: badSigDir })),
    /signature did not match/);
  assert.strictEqual(extendedLib.packStatus({ homeDir: home2 }).installed, false);
});

test('extended pack: remove deletes cleanly; status is honest when absent', async () => {
  const fx = buildExtendedFixture();
  const home = tmpDir('aq-ext-home-');
  await extendedLib.installExtended(extDeps(fx, home));
  const cap = captureLines();
  const r = await extendedLib.runExtended([], {
    ...extDeps(fx, home), flags: { remove: true }, out: cap.out,
  });
  assert.strictEqual(r.removed, true);
  assert.ok(!fs.existsSync(path.join(home, 'extended-library')));
  assert.match(cap.text(), /back to the 4,840 skills in the box/);
  /* removing again is a plain no-op, and bare `extended` in a pipe
   * reports status without downloading anything */
  const cap2 = captureLines();
  const r2 = await extendedLib.runExtended([], {
    ...extDeps(fx, home), isTTY: false, out: cap2.out,
  });
  assert.strictEqual(r2.installed, false);
  assert.match(cap2.text(), /not installed/);
  assert.match(cap2.text(), /4,840 skills in the box · 5,053 with the extended library/);
});

test('extended pack: setup offer declines by default and downloads nothing', async () => {
  let fetches = 0;
  const countingFetch = async () => { fetches++; throw new Error('must not be called'); };
  /* non-interactive setup: never even asked */
  const r0 = await extendedLib.maybeOfferDuringSetup({
    homeDir: tmpDir('aq-ext-home-'), isTTY: false, fetchImpl: countingFetch, out: () => {},
  });
  assert.strictEqual(r0.offered, false);
  /* interactive, user just presses Enter / says no */
  const cap = captureLines();
  const r1 = await extendedLib.maybeOfferDuringSetup({
    homeDir: tmpDir('aq-ext-home-'), isTTY: true, fetchImpl: countingFetch,
    ask: async (q, def) => { assert.match(q, /213 security-research skills that npm does not carry/); return def; },
    out: cap.out,
  });
  assert.strictEqual(r1.offered, true);
  assert.strictEqual(r1.installed, false);
  assert.strictEqual(fetches, 0, 'declining downloads nothing');
  /* interactive yes: installs through the same verified path */
  const fx = buildExtendedFixture();
  const home = tmpDir('aq-ext-home-');
  const r2 = await extendedLib.maybeOfferDuringSetup({
    ...extDeps(fx, home), isTTY: true, ask: async () => 'yes',
  });
  assert.strictEqual(r2.installed, true);
  assert.strictEqual(extendedLib.packStatus({ homeDir: home }).installed, true);
});

test('extended pack: overview states both numbers honestly in both states', async () => {
  function facetsServer(facets) {
    return new Promise((resolve) => {
      const srv = http.createServer((req, res) => {
        const send = (code, obj) => {
          res.writeHead(code, { 'content-type': 'application/json' });
          res.end(JSON.stringify(obj));
        };
        if (req.url.startsWith('/v1/skills/facets')) return send(200, facets);
        return send(404, { error: 'nope' });
      });
      srv.listen(0, '127.0.0.1', () => resolve(srv));
    });
  }
  const states = [
    {
      facets: { total: 4840, unique_names: 4585, repos: [], boxed_total: 4840, extended_total: 0, extended_installed: false, total_with_extended: 5053 },
      want: /4,840 skills in the box · 5,053 with the extended library/,
    },
    {
      facets: { total: 5053, unique_names: 4798, repos: [], boxed_total: 4840, extended_total: 213, extended_installed: true, total_with_extended: 5053 },
      want: /5,053 skills in the library — 4,840 in the box \+ 213 from the extended library/,
    },
  ];
  for (const { facets, want } of states) {
    const srv = await facetsServer(facets);
    try {
      const api = new Api({ server: `http://127.0.0.1:${srv.address().port}` });
      const cap = captureLines();
      await skillsLib.skillsOverview({ api, out: cap.out });
      /* the panel wraps long lines at its border — flatten the
       * box drawing before matching phrases that can straddle a
       * wrap */
      const flat = cap.text().replace(/│/g, ' ').replace(/\s+/g, ' ');
      assert.match(flat, want);
      assert.match(flat, /agentqon skills extended/, 'the overview points at the pack command');
    } finally { await new Promise((r) => srv.close(r)); }
  }
});

test('extended pack: production pins are the true pack identity', () => {
  assert.strictEqual(extendedLib.EXTENDED_PACK_SKILL_COUNT, 213);
  assert.strictEqual(extendedLib.BOXED_SKILL_TOTAL, 4840);
  assert.match(extendedLib.EXTENDED_PACK_BASE_URL,
    /^https:\/\/github\.com\/adynrisy\/agentqon\/releases\/download\/extended-library-v1$/);
  assert.match(extendedLib.EXTENDED_PACK_TARBALL_SHA256, /^[0-9a-f]{64}$/);
  assert.match(extendedLib.EXTENDED_PACK_PUBLIC_KEY_PEM, /BEGIN PUBLIC KEY/);
  const bin = fs.readFileSync(path.join(__dirname, '..', 'bin', 'agentqon.js'), 'utf8');
  assert.match(bin, /maybeOfferDuringSetup/, 'setup carries the ask-first offer');
});

/* ------------------- M1. rotation guards (audit 2026-10-22) ---
 * setup --repair and rotate-channel both rotate credentials a
 * running server is still using. Their guards used to ask only
 * runningServer() — a single 1.5 s health probe. A server busy
 * with a long inline mission misses that probe, read as "not
 * running", and the rotation stranded it on dead credentials
 * (lockout until `agentqon stop` + restart). The guards now go
 * through anyLiveServer: a bounded re-probe window plus the
 * alive-but-silent pid check. These tests pin all three sides:
 * the helper's contract, the repair refusal, and the real
 * rotate-channel CLI refusing — then rotating once the server
 * is genuinely gone. */
function slowHealthServer(payload, delayMs) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      if (req.url === '/health') {
        setTimeout(() => {
          if (res.destroyed) return;
          try {
            res.writeHead(200, { 'content-type': 'application/json' });
            res.end(JSON.stringify(payload));
          } catch { /* the probe already gave up on us */ }
        }, delayMs);
      } else { res.writeHead(200, { 'content-type': 'text/html' }); res.end('<html>ok</html>'); }
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

test('anyLiveServer: answering → record, alive-but-silent → silent marker, no record → null', { skip: process.platform === 'win32' || !spawnSyncOk('python3') }, async () => {
  const { home, restore } = withHome();
  const fake = spawn('python3', ['-c', 'import time; time.sleep(30)  # uvicorn app.main'], { stdio: 'ignore' });
  try {
    assert.strictEqual(await serverLib.anyLiveServer({ aliveWaitMs: 100 }), null,
      'no pid record: nothing stands in the way');
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: fake.pid, port: 8999, version: serverLib.VERSION, runtimeDir: null }));
    const silent = await serverLib.anyLiveServer({ aliveWaitMs: 300 });
    assert.ok(silent && silent.silent === true && silent.pid === fake.pid,
      'an alive, ours, never-answering pid is reported live (silent), never "not running"');
    const srv = await fakeHealthServer({ product: 'AgentQon', version: serverLib.VERSION, status: 'ok' });
    try {
      fs.writeFileSync(path.join(home, 'server.pid'),
        JSON.stringify({ pid: fake.pid, port: srv.address().port, version: serverLib.VERSION, runtimeDir: null }));
      const live = await serverLib.anyLiveServer({ aliveWaitMs: 2_000 });
      assert.ok(live && live.health && live.health.product === 'AgentQon',
        'an answering server is reported live with its verified record');
    } finally { srv.close(); }
  } finally {
    try { fake.kill('SIGKILL'); } catch { /* gone */ }
    restore();
  }
});

test('setup --repair: a live server whose health answers too slowly still blocks repair', { skip: process.platform === 'win32' || !spawnSyncOk('python3') }, async () => {
  const { home, restore } = withHome();
  /* /health needs 3 s — beyond the 1.5 s probe — so the old
   * single-probe guard read this server as "not running" and let
   * repair rotate the payload key + credentials underneath it. */
  const srv = await slowHealthServer({ product: 'AgentQon', version: serverLib.VERSION, status: 'ok' }, 3_000);
  const fake = spawn('python3', ['-c', 'import time; time.sleep(30)  # uvicorn app.main'], { stdio: 'ignore' });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: fake.pid, port: srv.address().port, version: serverLib.VERSION, runtimeDir: null }));
    await assert.rejects(
      serverLib.ensureBootstrapped({ repair: true, deps: { aliveWaitMs: 1_500 } }),
      (e) => /the local server is running/.test(e.message) && /agentqon stop/.test(e.message)
        && /setup --repair/.test(e.message),
      'repair must refuse while a live-but-busy server holds the credentials it would rotate');
    assert.doesNotThrow(() => process.kill(fake.pid, 0), 'the live server is left alone');
  } finally {
    try { fake.kill('SIGKILL'); } catch { /* gone */ }
    if (srv.closeAllConnections) srv.closeAllConnections();
    srv.close();
    restore();
  }
});

test('rotate-channel: a live-but-slow-health server blocks rotation; a stopped one lets it through', { skip: process.platform === 'win32' || !spawnSyncOk('python3') }, async () => {
  const { spawnSync } = require('child_process');
  const { home, restore } = withHome();
  const bin = path.join(__dirname, '..', 'bin', 'agentqon.js');
  const tokenPath = path.join(home, 'local-token');
  const secretPath = path.join(home, 'channel.secret');
  fs.writeFileSync(tokenPath, 'sentinel-token\n', { mode: 0o600 });
  fs.writeFileSync(secretPath, 'sentinel-secret\n', { mode: 0o600 });
  const srv = await slowHealthServer({ product: 'AgentQon', version: serverLib.VERSION, status: 'ok' }, 3_000);
  const fake = spawn('python3', ['-c', 'import time; time.sleep(30)  # uvicorn app.main'], { stdio: 'ignore' });
  const run = () => spawnSync('node', [bin, 'rotate-channel'],
    { env: { ...process.env, AGENTQON_HOME: home }, encoding: 'utf8', timeout: 60_000 });
  try {
    fs.writeFileSync(path.join(home, 'server.pid'),
      JSON.stringify({ pid: fake.pid, port: srv.address().port, version: serverLib.VERSION, runtimeDir: null }));
    const blocked = run();
    assert.strictEqual(blocked.status, 1, 'rotation is refused while the server is up');
    assert.match(blocked.stdout, /the local server is running/, 'the refusal is the plain stop-first message');
    assert.strictEqual(fs.readFileSync(tokenPath, 'utf8'), 'sentinel-token\n',
      'the token was NOT rotated under the live server');
    assert.strictEqual(fs.readFileSync(secretPath, 'utf8'), 'sentinel-secret\n',
      'the channel secret was NOT rotated under the live server');
    /* Control: once no server stands in the way, the same command
     * rotates — the guard refuses the dangerous case, not the verb. */
    fs.rmSync(path.join(home, 'server.pid'), { force: true });
    const allowed = run();
    assert.strictEqual(allowed.status, 0, `rotation proceeds with no server (stderr: ${allowed.stderr || ''})`);
    assert.notStrictEqual(fs.readFileSync(tokenPath, 'utf8'), 'sentinel-token\n',
      'the token rotated once the server was gone');
  } finally {
    try { fake.kill('SIGKILL'); } catch { /* gone */ }
    if (srv.closeAllConnections) srv.closeAllConnections();
    srv.close();
    restore();
  }
});

/* ---------------- audit F9: Shield credentials are loopback-only
 * The local token + HMAC channel headers are per-install secrets for
 * this machine's packaged server. api.js must withhold them from any
 * non-loopback base URL even when the cfg object carries them (a
 * hand-edited or future config must not leak them into remote mode),
 * while the session-token flow to remote servers stays intact. */
const { isLoopbackBase } = require('../lib/api');

test('F9: isLoopbackBase recognises only true loopback hosts', () => {
  for (const u of ['http://127.0.0.1:8000', 'http://127.1.2.3', 'http://localhost:8000',
                   'http://dev.localhost:9000', 'http://[::1]:8000']) {
    assert.strictEqual(isLoopbackBase(u), true, u);
  }
  for (const u of ['https://hosted.example', 'http://192.168.1.10:8000',
                   'http://127.0.0.1.evil.com', 'http://127.999.0.1', 'http://0.0.0.0:8000',
                   'not a url', '']) {
    assert.strictEqual(isLoopbackBase(u), false, u || '(empty)');
  }
});

test('F9: shield headers attach to loopback, never to a remote base', async () => {
  const realFetch = global.fetch;
  const seen = [];
  global.fetch = async (url, opts) => {
    seen.push({ url, headers: opts.headers });
    return { ok: true, status: 200, text: async () => '{"ok": true}' };
  };
  const creds = { localToken: 'lt-secret', channelSecret: 'cs-secret', token: 'omk_session' };
  try {
    const local = new Api({ server: 'http://127.0.0.1:8123', ...creds });
    await local.get('/v1/model-state');
    assert.strictEqual(seen[0].headers['x-omnimind-local-token'], 'lt-secret',
      'loopback base receives the local token');
    assert.ok(seen[0].headers['x-omnimind-sig'], 'loopback base receives the HMAC signature');

    const remote = new Api({ server: 'https://hosted.example', ...creds });
    await remote.get('/v1/model-state');
    const rh = seen[1].headers;
    assert.strictEqual(rh['x-omnimind-local-token'], undefined,
      'remote base must NOT receive the local token');
    assert.strictEqual(rh['x-omnimind-sig'], undefined,
      'remote base must NOT receive the HMAC signature');
    assert.strictEqual(rh['x-omnimind-nonce'], undefined);
    assert.strictEqual(rh['x-omnimind-ts'], undefined);
    assert.strictEqual(rh.authorization, 'Bearer omk_session',
      'the remote session-token flow is untouched');
  } finally { global.fetch = realFetch; }
});
