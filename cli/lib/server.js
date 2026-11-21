'use strict';
/* Local platform lifecycle.
 *
 * The npm package bundles the full AgentQon platform under `server/`
 * (FastAPI app + web UI + requirements.txt). On first run it is copied to
 * ~/.agentqon/server/<version>/, a dedicated venv is built at
 * ~/.agentqon/venv, and the platform runs from there — while ALL user
 * data (SQLite DB, artifacts, auto-generated vault key) lives in
 * ~/.agentqon/data via the AGENTQON_DATA env var the app honours, so
 * npm upgrades replace code, never data.
 */
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const cfgMod = require('./config');
const shield = require('./shield');
const s = require('./style');

const VERSION = require('../package.json').version;

function paths() {
  const home = cfgMod.configDir();
  return {
    home,
    serverDir: path.join(home, 'server', VERSION),
    stageDir: path.join(home, '.stage', VERSION),
    runDir: path.join(home, 'run', VERSION),
    venvDir: path.join(home, 'venv'),
    dataDir: path.join(home, 'data'),
    logsDir: path.join(home, 'logs'),
    logPath: path.join(home, 'logs', 'server.log'),
    pidPath: path.join(home, 'server.pid'),
  };
}

function bundledServerDir() { return path.join(__dirname, '..', 'server'); }
function venvPython(p) {
  return process.platform === 'win32'
    ? path.join(p.venvDir, 'Scripts', 'python.exe')
    : path.join(p.venvDir, 'bin', 'python');
}

/* ---------------- python detection ----------------
 * Candidates are { cmd, pre } pairs — on Windows the `py` launcher is
 * the reliable entry point (the bare `python` name can be the inert
 * Microsoft Store stub), so it is tried first with an explicit 3.12
 * request, then plain `python`. OMNIMIND_PYTHON always wins. */
function pythonCandidates() {
  const list = [];
  if (process.env.OMNIMIND_PYTHON) list.push({ cmd: process.env.OMNIMIND_PYTHON, pre: [] });
  if (process.platform === 'win32') {
    list.push({ cmd: 'py', pre: ['-3.12'] }, { cmd: 'py', pre: ['-3'] },
      { cmd: 'python', pre: [] }, { cmd: 'python3', pre: [] });
  } else {
    list.push({ cmd: 'python3', pre: [] }, { cmd: 'python', pre: [] });
  }
  return list;
}

/* The protected payload only runs on the exact interpreter minor
 * it was built for — the release manifest in the bundle records it.
 * A repo-dev bundle has no manifest; the product's pinned minor is
 * the fallback. */
function requiredPython() {
  try {
    const m = JSON.parse(fs.readFileSync(path.join(bundledServerDir(), 'manifest.json'), 'utf8'));
    if (m && typeof m.python === 'string' && /^\d+\.\d+$/.test(m.python)) return m.python;
  } catch { /* no manifest — fall through to the pinned minor */ }
  return '3.12';
}

function compareVersion(a, b) {
  const pa = String(a).split('.').map(Number);
  const pb = String(b).split('.').map(Number);
  return (pa[0] - pb[0]) || ((pa[1] || 0) - (pb[1] || 0));
}

const PYTHON_PROBE_TIMEOUT_MS = 5000;

/* Probe ONE candidate. Returns its "major.minor" string, or null
 * when it does not run, is a stub launcher (prints nothing useful),
 * or hangs — the probe is time-boxed so a broken PATH shim can
 * never block setup forever. */
function probePython(cand) {
  const r = spawnSync(cand.cmd, [...cand.pre, '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
    { encoding: 'utf8', timeout: PYTHON_PROBE_TIMEOUT_MS });
  if (r.error || r.status !== 0) return null;
  const out = String(r.stdout || '').trim();
  if (!/^\d+\.\d+$/.test(out)) return null; // stub launchers print nothing useful
  return out;
}

/* Evaluate EVERY candidate and pick one whose version exactly
 * matches the payload's required minor. (The old loop returned
 * "too old" from the FIRST runnable candidate, so a machine with
 * python3=3.10 ahead of python=3.12 on PATH was wrongly refused.)
 * When nothing matches exactly, the best runnable interpreter is
 * returned flagged tooOld so the guidance can name what was found. */
function findPython(want = requiredPython()) {
  let best = null;
  for (const cand of pythonCandidates()) {
    const version = probePython(cand);
    if (!version) continue;
    const found = { cmd: cand.cmd, pre: cand.pre, display: [cand.cmd, ...cand.pre].join(' '), version };
    if (version === want) return found;
    if (!best || compareVersion(version, best.version) > 0) best = found;
  }
  if (best) return { ...best, tooOld: true };
  return null;
}

/* The exact install command for a Python minor version, per platform —
 * setup failures END with this line, never with a dead end. */
function pythonInstallLines(version = '3.12') {
  if (process.platform === 'win32') return [`    Windows: winget install Python.Python.${version}`, '             (or https://www.python.org/downloads/ — tick "Add python to PATH", then open a NEW terminal)'];
  if (process.platform === 'darwin') return [`    macOS:   brew install python@${version}`, '             (or https://www.python.org/downloads/)'];
  return [`    Debian/Ubuntu: sudo apt install python${version} python${version}-venv`,
    '    Fedora:        sudo dnf install python3',
    '    Arch:          sudo pacman -S python'];
}

function pythonGuidance(found, want = '3.12') {
  const lines = [
    s.red(`✗ Python ${want} is required`) +
      (found ? s.dim(` — found ${found.display} ${found.version}, but the protected payload runs only on exactly Python ${want}`) : s.dim(' — no suitable Python found on PATH')),
    '  AgentQon\'s local server is a Python app; the CLI runs it for you once Python exists. Install it with:',
    ...pythonInstallLines(want),
    s.dim('  Then run `agentqon` again — setup resumes where it left off.'),
  ];
  return lines.join('\n');
}

/* ---------------- bootstrap ---------------- */
/* For a Shield bundle the check is `app/main.pyc` (bytecode payload);
 * a plain repo-dev bundle carries `app/main.py`. Either proves the
 * platform is present. */
function bundlePresent(dir) {
  return fs.existsSync(path.join(dir, 'app', 'main.pyc')) ||
    fs.existsSync(path.join(dir, 'app', 'main.py'));
}

function copyBundledServer(dest, { prune = true } = {}) {
  const src = bundledServerDir();
  if (!bundlePresent(src)) {
    throw new Error(`bundled platform missing at ${src} — this install is incomplete. Reinstall the package: npm install -g agentqon@latest, then run \`agentqon setup --repair\`.`);
  }
  fs.rmSync(dest, { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });
  fs.cpSync(src, dest, {
    recursive: true,
    filter: (p) => !p.includes('__pycache__'),
  });
  if (!prune) return;
  // prune other versions' code copies; user data is elsewhere and safe
  const versionsRoot = path.dirname(dest);
  for (const v of fs.readdirSync(versionsRoot)) {
    if (v !== VERSION) fs.rmSync(path.join(versionsRoot, v), { recursive: true, force: true });
  }
}

/* Every child gets a deadline (default 10 min): a stalled pip or a
 * hanging interpreter shim must never block setup forever. On
 * timeout the child is killed and the result resolves with
 * status -2 and timedOut set, so callers take their normal failure
 * path with the cause visible in the output tail. */
const RUN_DEFAULT_TIMEOUT_MS = 600_000;

function run(cmd, args, opts = {}) {
  const { onLine, timeoutMs = RUN_DEFAULT_TIMEOUT_MS, ...spawnOpts } = opts;
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], ...spawnOpts });
    let out = '';
    let pending = '';
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
    }, timeoutMs);
    const feed = (d) => {
      const text = d.toString();
      out += text;
      if (!onLine) return;
      pending += text;
      let idx;
      while ((idx = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, idx).replace(/\r$/, '');
        pending = pending.slice(idx + 1);
        try { onLine(line); } catch { /* progress must never break the child */ }
      }
    };
    child.stdout.on('data', feed);
    child.stderr.on('data', feed);
    child.on('error', () => { clearTimeout(timer); resolve({ status: -1, out, timedOut }); });
    child.on('close', (status) => {
      clearTimeout(timer);
      if (timedOut) out += `\n[agentqon: command timed out after ${Math.round(timeoutMs / 1000)}s and was killed]`;
      if (onLine && pending.trim()) { try { onLine(pending.trim()); } catch { /* ignore */ } }
      resolve({ status: timedOut ? -2 : status, out, timedOut });
    });
  });
}

/* ------------- library verification -------------
 * requirements.txt declares distributions; Python imports modules.
 * The mapping is the distribution name lowercased with -/. → _,
 * plus a small alias table for the well-known exceptions. */
const DIST_MODULE_ALIASES = {
  beautifulsoup4: 'bs4', pyyaml: 'yaml', pillow: 'PIL',
  'scikit-learn': 'sklearn', 'python-dateutil': 'dateutil',
  'uvicorn': 'uvicorn', 'psycopg': 'psycopg',
  'opentelemetry-api': 'opentelemetry', 'python-dotenv': 'dotenv',
};

/* Parse the requirements file into { dist, module, marker } entries.
 * Hash-locked requirements (Shield H1) span lines: a requirement line
 * ends with a backslash and its --hash continuations follow. The
 * marker is carried through and evaluated by the probe against the
 * venv's own interpreter, so platform/python-conditional entries
 * (tzdata on win32, exceptiongroup below 3.11) are only required
 * where pip would actually install them. */
function requirementModules(reqPath) {
  try {
    const raw = fs.readFileSync(reqPath, 'utf8');
    /* Join continuations, then strip the pip option tokens (--hash=…)
     * that ride along on the joined line: they belong to neither the
     * spec nor the marker, and a strict marker parser chokes on them
     * (v21.4.0 demanded uvloop + exceptiongroup on Windows because
     * their markers arrived polluted with hash text and the parser's
     * conservative fallback read them as "required"). */
    const joined = raw.replace(/\\\r?\n/g, ' ').replace(/--hash=\S+/g, ' ');
    return joined.split('\n')
      .map((l) => l.split('#')[0].trim())
      .filter(Boolean)
      .map((l) => {
        const [spec, ...markerParts] = l.split(';');
        const name = spec.split(/\s+/)[0].split(/[<>=!~[]/)[0].trim();
        return {
          dist: name,
          module: DIST_MODULE_ALIASES[name.toLowerCase()] || name.toLowerCase().replace(/[-.]/g, '_'),
          marker: markerParts.join(';').trim(),
        };
      })
      .filter((e) => e.dist && !e.dist.startsWith('-'));
  } catch { return []; }
}

/* The library-check probe. requirements.txt entries carry PEP 508
 * environment markers; the probe evaluates them with the venv's own
 * interpreter and only demands the modules pip would actually have
 * installed for this platform. The marker evaluator below is a
 * small recursive-descent parser for the full marker grammar —
 * compound and/or expressions, parentheses, single- or double-quoted
 * strings, every comparison operator (==, !=, <, <=, >, >=, ~=, ===,
 * in, not in), version-prefix matching (== "3.*") and all standard
 * marker variables. (The previous probe understood exactly ONE
 * comparison over four variables with double quotes, so pip-valid
 * markers like `os_name == "nt"` or a compound `and` were read as
 * "required everywhere" and setup demanded libraries the platform
 * had deliberately skipped — the uvloop failure family.) Anything
 * the evaluator cannot parse is treated as "required": it may
 * over-demand, it must never silently skip. */
const LIBRARY_CHECK_SCRIPT = `
import importlib, importlib.metadata, json, os, platform, sys
mods = json.loads(sys.argv[1])
ENV = {
    "python_version": "%d.%d" % sys.version_info[:2],
    "python_full_version": platform.python_version(),
    "os_name": os.name,
    "sys_platform": sys.platform,
    "platform_machine": platform.machine(),
    "platform_system": platform.system(),
    "platform_release": platform.release(),
    "platform_version": platform.version(),
    "implementation_name": sys.implementation.name,
    "implementation_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "extra": "",
}
VERSION_VARS = {"python_version", "python_full_version", "implementation_version"}

def tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "(":
            tokens.append(("lp", ch)); i += 1
        elif ch == ")":
            tokens.append(("rp", ch)); i += 1
        elif ch == "'" or ch == '"':
            j = text.find(ch, i + 1)
            if j < 0: raise ValueError("unterminated string")
            tokens.append(("str", text[i + 1:j])); i = j + 1
        elif text.startswith("===", i):
            tokens.append(("op", "===")); i += 3
        elif text[i:i + 2] in ("==", "!=", "<=", ">=", "~="):
            tokens.append(("op", text[i:i + 2])); i += 2
        elif ch in "<>":
            tokens.append(("op", ch)); i += 1
        else:
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_.*-"):
                j += 1
            if j == i: raise ValueError("bad character in marker")
            tokens.append(("word", text[i:j])); i = j
    return tokens

def seg_int(seg):
    digits = ""
    for ch in seg:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0

def vkey(v):
    return tuple(seg_int(seg) for seg in str(v).split("."))

def vpad(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))

def vcmp(have, op, want):
    if op == "===":
        return have == want
    if op in ("==", "!=") and want.endswith(".*"):
        prefix = vkey(want[:-2])
        match = vkey(have)[:len(prefix)] == prefix
        return match if op == "==" else not match
    h, w = vpad(vkey(have), vkey(want))
    if op == "==": return h == w
    if op == "!=": return h != w
    if op == "<": return h < w
    if op == "<=": return h <= w
    if op == ">": return h > w
    if op == ">=": return h >= w
    if op == "~=":
        base = vkey(want)
        if len(base) < 2: return False
        prefix = base[:-1]
        hh, bb = vpad(h, base)
        return hh >= bb and h[:len(prefix)] == prefix
    raise ValueError("bad version operator")

def scmp(have, op, want):
    if op in ("==", "==="): return have == want
    if op == "!=": return have != want
    if op == "<": return have < want
    if op == "<=": return have <= want
    if op == ">": return have > want
    if op == ">=": return have >= want
    raise ValueError("bad string operator")

def compare(var, op, want):
    have = ENV[var]
    if op in ("in", "not in"):
        found = have in want
        return found if op == "in" else not found
    if var in VERSION_VARS:
        return vcmp(have, op, want)
    return scmp(have, op, want)

def compare_flipped(literal, op, var):
    if op in ("in", "not in"):
        found = literal in ENV[var]
        return found if op == "in" else not found
    inv = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!=", "~=": "~=", "===": "==="}
    return compare(var, inv[op], literal)

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0
    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)
    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok
    def parse(self):
        value = self.parse_or()
        if self.i != len(self.tokens):
            raise ValueError("trailing tokens in marker")
        return value
    def parse_or(self):
        value = self.parse_and()
        while self.peek() == ("word", "or"):
            self.advance()
            rhs = self.parse_and()
            value = bool(value or rhs)
        return value
    def parse_and(self):
        value = self.parse_atom()
        while self.peek() == ("word", "and"):
            self.advance()
            rhs = self.parse_atom()
            value = bool(value and rhs)
        return value
    def parse_atom(self):
        kind, val = self.peek()
        if kind == "lp":
            self.advance()
            value = self.parse_or()
            if self.advance() != ("rp", ")"):
                raise ValueError("missing closing paren")
            return value
        return self.parse_comparison()
    def parse_comparison(self):
        k1, v1 = self.advance()
        k2, v2 = self.advance()
        if k2 == "op":
            op = v2
        elif (k2, v2) == ("word", "in"):
            op = "in"
        elif (k2, v2) == ("word", "not"):
            if self.advance() != ("word", "in"):
                raise ValueError("expected 'in' after 'not'")
            op = "not in"
        else:
            raise ValueError("expected marker operator")
        k3, v3 = self.advance()
        if k1 == "word" and k3 == "str":
            if v1 not in ENV: raise ValueError("unknown marker variable")
            return compare(v1, op, v3)
        if k1 == "str" and k3 == "word":
            if v3 not in ENV: raise ValueError("unknown marker variable")
            return compare_flipped(v1, op, v3)
        raise ValueError("marker comparison needs a variable and a quoted string")

def marker_ok(marker):
    if not marker:
        return True
    try:
        return bool(Parser(tokenize(marker.strip())).parse())
    except Exception:
        return True

missing = []
for entry in mods:
    if not marker_ok(entry.get("marker", "")):
        continue
    try:
        importlib.import_module(entry["module"])
    except Exception:
        missing.append(entry["module"])
count = sum(1 for _ in importlib.metadata.distributions())
print(json.dumps({"count": count, "missing": missing}))
`;

/* Verify the venv actually carries the platform's libraries: every
 * module requirements.txt declares must import, and count the
 * installed distributions. Returns { venvPresent, ok, count, missing, error? }. */
async function checkLibraries(p = paths(), reqDir = null) {
  const py = venvPython(p);
  if (!fs.existsSync(py)) return { venvPresent: false, ok: false, count: 0, missing: [], error: 'venv not created yet' };
  let reqPath = path.join(reqDir || p.serverDir, 'requirements.txt');
  if (!fs.existsSync(reqPath)) reqPath = path.join(bundledServerDir(), 'requirements.txt');
  const mods = requirementModules(reqPath);
  const script = LIBRARY_CHECK_SCRIPT;
  const r = await run(py, ['-c', script, JSON.stringify(mods)]);
  if (r.status !== 0) {
    return { venvPresent: true, ok: false, count: 0, missing: mods.map((m) => m.module), error: 'venv python failed the import check:\n' + r.out.split('\n').slice(-10).join('\n') };
  }
  try {
    const parsed = JSON.parse(r.out.trim().split('\n').pop());
    return { venvPresent: true, ok: parsed.missing.length === 0, count: parsed.count, missing: parsed.missing };
  } catch {
    return { venvPresent: true, ok: false, count: 0, missing: mods.map((m) => m.module), error: 'could not parse the import check output' };
  }
}

function shieldInstalled(p) {
  return fs.existsSync(path.join(p.serverDir, 'install.json'));
}

/* The venv is usable only if its interpreter EXISTS, RUNS, and is
 * the exact minor the payload was built for. (Setup used to create
 * the venv only when the binary was absent, so a corrupt or
 * wrong-version venv survived every repair.) */
async function venvInterpreterOk(p, want = requiredPython()) {
  const vpy = venvPython(p);
  if (!fs.existsSync(vpy)) return false;
  const r = await run(vpy, ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], { timeoutMs: 15_000 });
  if (r.status !== 0) return false;
  return String(r.out).trim().split('\n').pop() === want;
}

/* Replace destDir with srcDir as one rename pair, restoring the old
 * tree if the incoming rename fails. Both dirs live under the same
 * parent, so each rename is atomic on the filesystem — an install
 * is never left half-copied or half-encrypted in place. */
function swapDirIntoPlace(srcDir, destDir) {
  const oldDir = `${destDir}.old-${process.pid}`;
  fs.rmSync(oldDir, { recursive: true, force: true });
  const hadDest = fs.existsSync(destDir);
  if (hadDest) fs.renameSync(destDir, oldDir);
  try {
    fs.renameSync(srcDir, destDir);
  } catch (e) {
    if (hadDest && !fs.existsSync(destDir)) {
      try { fs.renameSync(oldDir, destDir); } catch { /* best effort restore */ }
    }
    throw e;
  }
  fs.rmSync(oldDir, { recursive: true, force: true });
}

async function ensureBootstrapped(opts = {}) {
  const p = paths();
  const cfg = cfgMod.load();
  const repair = !!opts.repair;

  /* Repair rotates the payload key and the per-install credentials
   * — exactly what a running server is still using. Mirror the
   * rotate-channel guard: refuse while a server is up rather than
   * stranding it (and never let the stop/restart race corrupt the
   * swap below). Liveness comes from anyLiveServer, NOT the single
   * health probe (audit 2026-10-22 M1): a server busy with a long
   * mission can miss the 1.5 s probe, and rotating under it strands
   * it on dead credentials. */
  if (repair) {
    const live = await anyLiveServer(opts.deps || {});
    if (live) {
      throw new Error('the local server is running — stop it first with `agentqon stop`, ' +
        'then run `agentqon setup --repair` (repair rotates the payload key and credentials ' +
        'the running server is still using).');
    }
  }

  /* Fast path: this version is installed and intact. A Shield install
   * that fails its ciphertext check is NOT silently healed — tamper
   * and corruption are indistinguishable, and both get the same
   * answer: refuse, with the repair path named (spec §A Rung 3). */
  if (!repair && cfg.serverVersion === VERSION && fs.existsSync(venvPython(p))) {
    if (shieldInstalled(p)) {
      const progress = new s.Progress().start('Shield: verifying the installed payload…');
      const v = shield.verifyInstalled(p.serverDir, (done, total) => progress.update(done, total));
      if (!v.ok) {
        progress.fail('Shield: payload integrity check failed');
        throw new Error(
          `Shield: the installed payload has been modified (${v.bad}). ` +
          'Refusing to run a tampered install. Restore it with ' +
          '`agentqon setup --repair`; if repair itself fails, reinstall ' +
          'the package (npm install -g agentqon@latest) and run ' +
          '`agentqon setup --repair` again.');
      }
      progress.stop();
      shield.ensureCredentials();
      return p;
    }
    if (bundlePresent(p.serverDir)) {  // legacy plaintext install
      shield.ensureCredentials();
      return p;
    }
  }

  const py = findPython();
  if (!py || py.tooOld) throw new Error(pythonGuidance(py, requiredPython()));

  /* Every slow phase reports through one Progress: a labelled bar
   * with a percentage where the work is countable (payload files,
   * pip packages), an animated elapsed clock where it is not. The
   * terminal must never sit silent during setup again. */
  const progress = new s.Progress();
  /* Set true only once the staged payload has been swapped into
   * place — the failure message below states exactly which side of
   * that line setup stopped on, and claims nothing more. */
  let payloadInstalled = false;
  try {
    progress.start('Copying the AgentQon platform…');
    copyBundledServer(p.stageDir, { prune: false });

    /* Shield: verify the release manifest (Ed25519 signature + every
     * file hash) and the bytecode/Python match BEFORE anything from
     * the bundle is installed or executed. */
    progress.setLabel('Shield: verifying release signature + payload hashes…');
    const bundle = shield.verifyBundle(p.stageDir, (done, total) => progress.update(done, total));
    if (bundle.shield) {
      if (!bundle.ok) {
        throw new Error('Shield: the bundled payload failed integrity ' +
          'verification — ' + bundle.errors.slice(0, 3).join('; ') +
          '. This copy cannot be trusted, so nothing was installed or run.\n' +
          'Recovery: 1) npm install -g agentqon@latest  2) agentqon setup --repair');
      }
      const want = bundle.manifest.python;
      if (want && want !== py.version) {
        throw new Error(
          `Shield: this release's protected payload was built for Python ${want}, ` +
          `but this machine offers Python ${py.version} via \`${py.display}\`. ` +
          'A bytecode payload only runs on the exact interpreter it was built for.\n' +
          `Install Python ${want}:\n` + pythonInstallLines(want).join('\n') +
          '\nThen re-run `agentqon setup`.');
      }
    }

    fs.mkdirSync(p.dataDir, { recursive: true, mode: 0o700 });
    fs.mkdirSync(p.logsDir, { recursive: true, mode: 0o700 });
    try { fs.chmodSync(p.dataDir, 0o700); fs.chmodSync(p.logsDir, 0o700); } catch { /* best effort */ }

    /* The venv is (re)built whenever its interpreter is missing,
     * fails to run, or is the wrong minor — not only when the binary
     * is absent, so a corrupt venv is something repair can fix. */
    if (!(await venvInterpreterOk(p, py.version))) {
      fs.rmSync(p.venvDir, { recursive: true, force: true });
      progress.start(`Creating a Python environment (Python ${py.version})…`);
      const v = await run(py.cmd, [...py.pre, '-m', 'venv', p.venvDir]);
      if (v.status !== 0) {
        throw new Error('venv creation failed — last lines:\n' + v.out.split('\n').slice(-15).join('\n') +
          '\nFix: make sure Python\'s venv module is installed (Debian/Ubuntu: sudo apt install python3-venv; ' +
          'Windows/macOS: reinstall Python from python.org), then re-run `agentqon setup`.');
      }
    }
    const vpy = venvPython(p);
    /* Shield H1: dependencies install from the pinned, hash-locked
     * requirements — pip verifies every artifact's SHA-256. There is
     * deliberately NO pip self-upgrade step: the venv's bundled pip
     * is the pinned tool, and an unpinned self-upgrade would reopen
     * the supply-chain hole the lock file closes.
     * pip's own output is piped (never inherited raw, never TTY-bar)
     * and folded into our bar: each collected/downloaded distribution
     * advances it against the requirements count, so the download
     * visibly moves instead of the terminal sitting blank. */
    const reqFile = path.join(p.stageDir, 'requirements.txt');
    const reqTotal = requirementModules(reqFile).length;
    progress.start('Installing platform dependencies (hash-verified, first run only)…', reqTotal);
    const seenDists = new Set();
    const onPipLine = (line) => {
      const m = /^(?:Collecting|Downloading|Using cached)\s+([A-Za-z0-9_.-]+)/.exec(line);
      if (m) { seenDists.add(m[1].toLowerCase()); progress.update(seenDists.size, reqTotal); }
      else if (/^Installing collected packages:/.test(line)) progress.update(Math.max(seenDists.size, Math.floor(reqTotal * 0.9)), reqTotal);
      else if (/^Successfully installed/.test(line)) progress.update(reqTotal, reqTotal);
    };
    const reqArgs = ['-m', 'pip', 'install', '--disable-pip-version-check', '--progress-bar', 'off',
      '--require-hashes', '-r', reqFile];
    let pip = await run(vpy, reqArgs, { onLine: onPipLine });
    if (pip.status !== 0) {
      progress.setLabel('Dependency install failed — retrying once…');
      pip = await run(vpy, reqArgs, { onLine: onPipLine });
    }
    if (pip.status !== 0) {
      throw new Error('pip install failed (after one retry) — last lines:\n' + pip.out.split('\n').slice(-20).join('\n') +
        '\nFix: check the network connection and free disk space, then re-run `agentqon setup` — it resumes safely.');
    }

    progress.start('Verifying the installed libraries…');
    const check = await checkLibraries(p, p.stageDir);
    if (!check.ok) {
      throw new Error('library verification failed — these modules did not import in the new venv: ' +
        (check.missing.join(', ') || check.error || 'unknown') +
        '\nRe-run `agentqon setup`; if it persists, run `agentqon setup --repair`.');
    }
    cfg.libCheck = { count: check.count, at: new Date().toISOString() };

    /* R3 local-key mode: the payload is stored ENCRYPTED at rest
     * under a per-install key; only a wiped-on-exit runtime copy is
     * ever plaintext. No manifest (repo-dev bundle) = legacy plain
     * copy, so source checkouts keep working.
     *
     * The install is STAGED, never written in place: the new tree is
     * built in a sibling temp dir, self-verified, then swapped over
     * the old tree with a rename pair. A repair's new payload key is
     * generated in memory and written to disk ONLY after the tree
     * encrypted under it is in place — the old code rotated the key
     * first and deleted-then-rewrote the payload in place, so a
     * crash mid-repair left an install whose ciphertext no key
     * could open. Until the swap, the existing install is byte-for-
     * byte untouched, and every state after it is recoverable by
     * simply re-running setup. */
    if (bundle.shield) {
      progress.start('Shield: encrypting the payload at rest…');
      const key = repair ? crypto.randomBytes(32) : shield.ensurePayloadKey();
      const newTree = `${p.serverDir}.new-${process.pid}`;
      fs.rmSync(newTree, { recursive: true, force: true });
      try {
        shield.encryptTree(p.stageDir, newTree, key, {
          version: VERSION,
          python: bundle.manifest.python || py.version,
          manifestFiles: bundle.manifest.files,
        }, (done, total) => progress.update(done, total));
        const selfCheck = shield.verifyInstalled(newTree);
        if (!selfCheck.ok) {
          throw new Error(`Shield: the freshly encrypted payload failed its self-check (${selfCheck.bad}) — your existing install was not touched. Re-run \`agentqon setup --repair\`.`);
        }
        swapDirIntoPlace(newTree, p.serverDir);
        if (repair) shield.writePayloadKey(key); // the key lands only once its tree does
        payloadInstalled = true;
      } finally {
        fs.rmSync(newTree, { recursive: true, force: true });
      }
      const versionsRoot = path.dirname(p.serverDir);
      for (const v of fs.readdirSync(versionsRoot)) {
        if (v !== VERSION && !v.startsWith(`${VERSION}.`)) fs.rmSync(path.join(versionsRoot, v), { recursive: true, force: true });
      }
    } else {
      const newTree = `${p.serverDir}.new-${process.pid}`;
      fs.rmSync(newTree, { recursive: true, force: true });
      fs.cpSync(p.stageDir, newTree, {
        recursive: true,
        filter: (q) => !q.includes('__pycache__'),
      });
      swapDirIntoPlace(newTree, p.serverDir);
      fs.rmSync(newTree, { recursive: true, force: true });
      payloadInstalled = true;
      const versionsRoot = path.dirname(p.serverDir);
      for (const v of fs.readdirSync(versionsRoot)) {
        if (v !== VERSION && !v.startsWith(`${VERSION}.`)) fs.rmSync(path.join(versionsRoot, v), { recursive: true, force: true });
      }
    }
    fs.rmSync(p.stageDir, { recursive: true, force: true });

    /* Per-install credentials (Shield C1/B1): generated once; a
     * repair rotates them so a leaked old token/secret dies. */
    if (repair) shield.rotateCredentials(); else shield.ensureCredentials();
    progress.succeed(`Libraries verified — ${check.count} distributions installed`);
  } catch (e) {
    progress.fail(payloadInstalled
      ? 'Setup stopped after the new payload was installed and verified — re-run `agentqon setup` to finish; if it keeps failing, `agentqon setup --repair` rebuilds it cleanly'
      : 'Setup stopped — the existing install was not touched; fix the cause and re-run `agentqon setup`');
    throw e;
  }
  cfg.serverVersion = VERSION;
  cfgMod.save(cfg);
  process.stdout.write(s.green('✓ AgentQon platform ready\n'));
  return p;
}

/* Is a live server process running from this exact runtime dir?
 * The pid record is the evidence — health is NOT consulted: a
 * server busy with a long inline mission can miss the short health
 * probe while very much alive, and on Windows its payload files are
 * locked either way, so wiping the dir under it dies with a raw
 * EPERM. Returns the pid record when the dir is in use. */
function runtimeInUse(p) {
  const rec = readPid();
  if (!rec || !rec.runtimeDir || !rec.pid) return null;
  let a = path.resolve(rec.runtimeDir);
  let b = path.resolve(p.runDir);
  if (process.platform === 'win32') { a = a.toLowerCase(); b = b.toLowerCase(); }
  if (a !== b) return null;
  return pidAlive(rec.pid) ? rec : null;
}

/* Windows refuses to delete or overwrite files a live process has
 * open (the running server's payload, above all) with bare
 * EPERM/EACCES/EBUSY errors. Translate those — and only those —
 * into the human form that names the remedy; every other error
 * passes through untouched. */
const FS_LOCK_CODES = new Set(['EPERM', 'EACCES', 'EBUSY']);
function translateFsLockError(e) {
  if (e && FS_LOCK_CODES.has(e.code)) {
    return new Error(
      'the runtime copy is locked — a file it needs is held open, most likely by a server ' +
      'still running from it or by antivirus scanning it. Stop the server with `agentqon stop`, ' +
      'close other agentqon terminals, then try again ' +
      `(the system said: ${e.code} — ${String(e.message || '').trim()})`);
  }
  return e;
}

/* Prepare the server runtime. Shield mode: verify the installed
 * ciphertext, decrypt into the private runtime dir (0700, wiped by
 * stopServer / next start), and run from there. Throws — refusing to
 * start — on any integrity failure. NEVER rebuilds a runtime dir a
 * live server is running from: callers reuse that server instead
 * (startDetached checks first), and if reuse was impossible this
 * fails with the human remedy, not a raw EPERM. */
function prepareRuntime(p, deps = {}) {
  if (!shieldInstalled(p)) return { cwd: p.serverDir, shield: false };
  const inUse = (deps.runtimeInUse || runtimeInUse)(p);
  if (inUse) {
    throw new Error(
      `the local server is running from this runtime (pid ${inUse.pid}) — ` +
      'refusing to rebuild it under a live process. Stop it first with ' +
      '`agentqon stop`, then try again.');
  }
  /* Verify + decrypt ~9.4k payload files takes seconds — show it,
   * with a real percentage, instead of a silent pause before boot. */
  const progress = new s.Progress().start('Shield: verifying the installed payload…');
  try {
    const v = (deps.verifyInstalled || shield.verifyInstalled)(p.serverDir, (done, total) => progress.update(done, total));
    if (!v.ok) {
      progress.fail('Shield: payload integrity check failed');
      throw new Error(
        `Shield: the installed payload has been modified (${v.bad}). ` +
        'Refusing to run a tampered install. Restore it with ' +
        '`agentqon setup --repair`; if repair itself fails, reinstall ' +
        'the package (npm install -g agentqon@latest) and run ' +
        '`agentqon setup --repair` again.');
    }
    const key = (deps.loadPayloadKey || shield.loadPayloadKey)();
    if (!key) {
      progress.fail('Shield: payload key missing');
      throw new Error('Shield: the payload key is missing — run `agentqon setup --repair`.');
    }
    progress.start('Shield: decrypting the payload for this run…');
    (deps.decryptTree || shield.decryptTree)(p.serverDir, p.runDir, key, (done, total) => progress.update(done, total));
  } catch (e) {
    progress.fail('Shield: payload preparation stopped');
    throw translateFsLockError(e);
  }
  progress.succeed('Shield: payload integrity verified — running from a protected runtime copy');
  return { cwd: p.runDir, shield: true };
}

function shieldStatus(p = paths()) {
  if (!shieldInstalled(p)) return { mode: 'plain', installed: bundlePresent(p.serverDir) };
  const v = shield.verifyInstalled(p.serverDir);
  return { mode: 'local-key', installed: true, ok: v.ok, bad: v.bad,
           version: v.record && v.record.version, python: v.record && v.record.python };
}

/* ---------------- port + process ---------------- */
function portFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

async function pickPort(preferred) {
  for (let port = preferred; port < preferred + 50; port++) {
    if (await portFree(port)) return port;
  }
  throw new Error(`no free port found near ${preferred} — close whatever is using those ports (or set "localPort" in ~/.agentqon/config.json), then try again`);
}

function readPid() {
  try { return JSON.parse(fs.readFileSync(paths().pidPath, 'utf8')); } catch { return null; }
}
function pidAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (e) {
    /* EPERM means the process EXISTS but belongs to another user or
     * a higher integrity level — it is alive, just not ours to
     * signal. Only ESRCH (no such process) means dead. Treating
     * EPERM as dead made status/stop drop the record of a server
     * that was still running. */
    return !!(e && e.code === 'EPERM');
  }
}

async function fetchHealth(port, timeoutMs = 1500) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

/* The web UI root must answer 200 before anyone is sent to it —
 * opening a browser at a half-started server is how users used to
 * land on a blank page that never recovered. The UI root is one of
 * the few routes exempt from the local-token guard, so a plain GET
 * is the honest readiness probe. */
async function fetchUiRoot(port, timeoutMs = 1500) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return false;
    await res.text();
    return true;
  } catch { return false; }
}

const PRODUCT_NAME = 'AgentQon';

/* Is the live process at `pid` clearly NOT our uvicorn server?
 * Used only when /health does not answer (a hung server cannot
 * prove itself): if the OS shows the pid running something
 * unrelated, the pid was recycled and must never be signalled.
 * Any doubt (tool missing, unparsable output) answers false —
 * doubt must not strand a hung server we cannot otherwise stop. */
function processLooksForeign(pid) {
  try {
    if (process.platform === 'win32') {
      const r = spawnSync('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], { encoding: 'utf8', timeout: 5000 });
      if (r.error || r.status !== 0 || !r.stdout) return false;
      const m = /^"([^"]+)"/.exec(r.stdout.trim());
      if (!m) return false;
      return !/^python\w*\.exe$/i.test(m[1]);
    }
    const r = spawnSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8', timeout: 5000 });
    if (r.error || r.status !== 0 || !r.stdout) return false;
    const cmdline = r.stdout.trim();
    if (!cmdline) return false;
    return !(/uvicorn/.test(cmdline) && /app\.main/.test(cmdline));
  } catch { return false; }
}

async function runningServer() {
  const rec = readPid();
  if (!rec || !pidAlive(rec.pid)) return null;
  const health = await fetchHealth(rec.port);
  if (!health) return null;
  /* Identity: whatever answers on the recorded port must actually
   * BE AgentQon — any JSON responder used to be accepted as "the
   * server". */
  if (health.product && health.product !== PRODUCT_NAME) return null;
  const version = (typeof health.version === 'string' && health.version) || rec.version || null;
  if (version && version !== VERSION) {
    /* A server started by a different package version is still
     * running — the post-upgrade trap where the new CLI silently
     * reused the old server's code. Stop it (identity-verified)
     * and report "not running" so the caller starts the current
     * payload instead. */
    await stopServer();
    return null;
  }
  return { ...rec, health };
}

function serverEnv(p) {
  /* Shield credentials travel to the spawned server via the
   * environment ONLY (never argv — argv is world-readable in process
   * listings). AGENTQON_PACKAGED=1 arms the server's startup refusals
   * and Host allowlist; the declared bind host is always loopback.
   * AGENTQON_* is the preferred prefix; the AGENTQON_* twins carry the
   * same values so a previously-installed (pre-rename) server copy
   * spawned by this CLI still sees its configuration. */
  const creds = shield.ensureCredentials();
  return {
    ...process.env,
    AGENTQON_DATA: p.dataDir,
    AGENTQON_DATA: p.dataDir,
    PYTHONUNBUFFERED: '1',
    AGENTQON_PACKAGED: '1',
    AGENTQON_PACKAGED: '1',
    AGENTQON_BIND_HOST: '127.0.0.1',
    AGENTQON_BIND_HOST: '127.0.0.1',
    /* Free-roam: the folder THIS CLI was launched from becomes the
     * server's home-project source — agent sessions created without
     * a named workspace adopt it. The server's own working dir is
     * its install tree, so the folder must travel via the env. (A
     * server already running keeps the folder it was started from.) */
    AGENTQON_LAUNCH_DIR: process.cwd(),
    OMNIMIND_LAUNCH_DIR: process.cwd(),
    AGENTQON_LOCAL_TOKEN: creds.token,
    AGENTQON_LOCAL_TOKEN: creds.token,
    AGENTQON_CHANNEL_SECRET: creds.channelSecret,
    AGENTQON_CHANNEL_SECRET: creds.channelSecret,
  };
}

/* A recorded pid that is ALIVE but whose health probe just missed
 * is not "not running": the server may be busy (a long inline
 * mission can starve the 1.5 s health probe). Spawning anyway
 * starts a SECOND server against the same data dir and overwrites
 * the pid record — orphaning the first server beyond the CLI's
 * reach (audit C, live repro: `start` on :8000, the next command's
 * slow probe spawned a duplicate on :8001, `stop` killed only the
 * duplicate). Give the live pid a bounded window to answer; if it
 * is ours and still silent, REFUSE with the remedy rather than
 * duplicating. Returns the server record, or null when no live
 * recorded pid stands in the way. */
async function awaitLiveServer(deps = {}) {
  const rec = readPid();
  if (!rec || !rec.pid || !pidAlive(rec.pid)) return null;
  if (processLooksForeign(rec.pid)) return null; // recycled pid — stale record
  const waitMs = deps.aliveWaitMs !== undefined ? deps.aliveWaitMs : 10_000;
  const deadline = Date.now() + waitMs;
  for (;;) {
    const again = await (deps.runningServer || runningServer)();
    if (again) return again;
    if (!pidAlive(rec.pid) || Date.now() >= deadline) break;
    await new Promise((r) => setTimeout(r, 400));
  }
  if (pidAlive(rec.pid)) {
    throw new Error(
      `a local server process (pid ${rec.pid}) is alive but not answering health checks on port ${rec.port} — ` +
      'it may be busy with a long mission. Refusing to start a second server against the same data. ' +
      'Wait and retry, or stop it with `agentqon stop`.');
  }
  return null;
}

/* anyLiveServer(deps): the liveness answer for the credential-
 * MUTATING paths (`setup --repair`, `rotate-channel`) — audit
 * 2026-10-22 M1. runningServer()'s single 1.5 s probe can miss a
 * server busy with a long inline mission, and both mutation guards
 * used to read that miss as "no server": the payload key and
 * credentials rotated under the still-running server, which kept
 * the old ones in memory while the CLI presented the new ones —
 * a lockout until `agentqon stop` + restart. So the guards consult
 * the bounded awaitLiveServer window too (5 s default — a mutation
 * waits less than a start does), and its alive-but-silent refusal
 * is translated into a positive answer: the recorded pid is alive,
 * is ours (not a recycled foreign process), and still has not
 * answered — that IS a live server for any path that must not act
 * while one is up. Returns the server record (with `silent: true`
 * when it never answered), or null when nothing live stands in
 * the way. */
async function anyLiveServer(deps = {}) {
  try {
    const live = await (deps.awaitLiveServer || awaitLiveServer)({
      ...deps,
      aliveWaitMs: deps.aliveWaitMs !== undefined ? deps.aliveWaitMs : 5_000,
    });
    return live || null;
  } catch {
    /* awaitLiveServer throws ONLY for the alive-but-silent case;
     * re-verify the pid facts before calling it live. */
    const rec = readPid();
    if (rec && rec.pid && pidAlive(rec.pid) && !processLooksForeign(rec.pid)) {
      return { ...rec, silent: true };
    }
    return null;
  }
}

async function startDetached(deps = {}) {
  /* Reuse first (v21.4.2): a verified running server is simply USED
   * — no runtime preparation, no decryption. Preparing a runtime
   * while a server already runs from it is what produced the raw
   * Windows EPERM on `agentqon run`: the live server's payload files
   * are locked, so the rebuild must never start. (deps are the
   * regression-test seam for this decision.) */
  const existing = await (deps.runningServer || runningServer)();
  if (existing) return existing;
  const live = await awaitLiveServer(deps);
  if (live) return live;
  const p = await ensureBootstrapped();
  const runtime = (deps.prepareRuntime || prepareRuntime)(p);
  const cfg = cfgMod.load();
  const port = await pickPort(cfg.localPort || 8000);
  fs.mkdirSync(p.logsDir, { recursive: true, mode: 0o700 });
  const logFd = fs.openSync(p.logPath, 'a');
  const child = spawn(venvPython(p), ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: runtime.cwd, env: serverEnv(p), detached: true, stdio: ['ignore', logFd, logFd],
  });
  child.unref();
  fs.writeFileSync(p.pidPath, JSON.stringify({ pid: child.pid, port, version: VERSION, runtimeDir: runtime.shield ? runtime.cwd : null, startedAt: new Date().toISOString() }, null, 2));
  /* Visible progression — starting → waiting for health → checking
   * the UI → ready — and the caller only hears "ready" once BOTH
   * /health and the UI root answer, so the browser is never opened
   * onto a server that cannot serve the page yet. */
  const progress = new s.Progress().start(`Starting AgentQon on http://127.0.0.1:${port} — waiting for the server…`);
  const deadline = Date.now() + 45_000;
  let health = null;
  while (Date.now() < deadline) {
    if (!health) {
      health = await fetchHealth(port);
      if (health) progress.setLabel(`Starting AgentQon on http://127.0.0.1:${port} — server is up, checking the web UI…`);
    } else if (await fetchUiRoot(port)) {
      progress.succeed(`AgentQon is ready at http://127.0.0.1:${port}`);
      return { pid: child.pid, port, version: VERSION, health };
    }
    if (!pidAlive(child.pid)) {
      progress.fail('Server exited during startup');
      fs.rmSync(p.pidPath, { force: true });
      if (runtime.shield) fs.rmSync(runtime.cwd, { recursive: true, force: true });
      const tail = tailLogText(20) || '';
      throw new Error(`the local server failed to start — log tail:\n${tail}\n(full log: ${p.logPath})\nRun \`agentqon doctor\` for a health checklist.`);
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  progress.fail('Server did not become ready in time');
  /* A timed-out start must not strand the child: it may come up
   * later on that port as an unrecorded orphan. Kill it, drop the
   * pid record, and wipe the decrypted runtime copy. */
  try {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    else child.kill('SIGKILL');
  } catch { /* already gone */ }
  fs.rmSync(p.pidPath, { force: true });
  if (runtime.shield) fs.rmSync(runtime.cwd, { recursive: true, force: true });
  throw new Error(`server start timed out — see ${p.logPath}, and run \`agentqon doctor\` for a health checklist`);
}

async function stopServer() {
  const p = paths();
  const rec = readPid();
  if (!rec) return { stopped: false, reason: 'no pid record' };
  if (!pidAlive(rec.pid)) { fs.rmSync(p.pidPath, { force: true }); return { stopped: false, reason: 'process already gone', port: rec.port }; }
  /* Verify identity BEFORE killing anything: pids get recycled, and
   * the record alone is not proof the live process is ours. If the
   * port answers as a different product, or the process is clearly
   * a different program, leave it alone and just drop the record. */
  const health = await fetchHealth(rec.port);
  if (health && health.product && health.product !== PRODUCT_NAME) {
    fs.rmSync(p.pidPath, { force: true });
    return { stopped: false, reason: `port ${rec.port} is held by "${health.product}", not ${PRODUCT_NAME} — left it alone and cleared the stale record`, port: rec.port };
  }
  if (!health && processLooksForeign(rec.pid)) {
    fs.rmSync(p.pidPath, { force: true });
    return { stopped: false, reason: 'the recorded pid belongs to a different program now — left it alone and cleared the stale record', port: rec.port };
  }
  try {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(rec.pid), '/T'], { stdio: 'ignore' });
    else process.kill(rec.pid, 'SIGTERM');
  } catch { /* raced exit */ }
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline && pidAlive(rec.pid)) await new Promise((r) => setTimeout(r, 200));
  if (pidAlive(rec.pid)) { try { process.kill(rec.pid, 'SIGKILL'); } catch { /* gone */ } }
  fs.rmSync(p.pidPath, { force: true });
  /* The decrypted runtime copy is wiped on exit (spec §A Rung 3):
   * plaintext exists only while the server runs. */
  if (rec.runtimeDir) fs.rmSync(rec.runtimeDir, { recursive: true, force: true });
  return { stopped: true, port: rec.port };
}

async function serveForeground() {
  /* Same reuse rule as every other path: if a verified server is
   * already running, say so and use it — never prepare a second
   * runtime over the live one's files. */
  const existing = await runningServer();
  if (existing) {
    process.stdout.write(s.green(`● AgentQon is already running at http://127.0.0.1:${existing.port}`) + s.dim(` · pid ${existing.pid} · stop with \`agentqon stop\`\n`));
    return existing;
  }
  const live = await awaitLiveServer();
  if (live) {
    process.stdout.write(s.green(`● AgentQon is already running at http://127.0.0.1:${live.port}`) + s.dim(` · pid ${live.pid} · stop with \`agentqon stop\`\n`));
    return live;
  }
  const p = await ensureBootstrapped();
  const runtime = prepareRuntime(p);
  const cfg = cfgMod.load();
  const port = await pickPort(cfg.localPort || 8000);
  process.stdout.write(s.cyan(`AgentQon server (foreground) → http://127.0.0.1:${port}`) + s.dim('   Ctrl-C to stop\n'));
  const child = spawn(venvPython(p), ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: runtime.cwd, env: serverEnv(p), stdio: 'inherit',
  });
  process.on('SIGINT', () => child.kill('SIGINT'));
  await new Promise((resolve) => child.on('close', resolve));
  if (runtime.shield) fs.rmSync(runtime.cwd, { recursive: true, force: true });
}

function openBrowser(url) {
  if (process.env.OMNIMIND_NO_OPEN) return;
  const cmd = process.platform === 'darwin' ? 'open' : process.platform === 'win32' ? 'cmd' : 'xdg-open';
  const args = process.platform === 'win32' ? ['/c', 'start', '', url] : [url];
  try { spawn(cmd, args, { detached: true, stdio: 'ignore' }).unref(); } catch { /* headless boxes */ }
}

/* Read only the TAIL of the server log. The log is append-only
 * and grows for the life of the install; reading the whole file
 * into memory to show the last lines made `agentqon logs` and
 * crash reporting slower (and hungrier) the longer an install
 * lived. Reads a bounded window from the end instead. */
function tailLogText(lines = 60) {
  const p = paths();
  if (!fs.existsSync(p.logPath)) return null;
  const fd = fs.openSync(p.logPath, 'r');
  try {
    const size = fs.fstatSync(fd).size;
    if (size === 0) return '';
    const want = Math.min(size, Math.max(64 * 1024, lines * 512));
    const buf = Buffer.alloc(want);
    fs.readSync(fd, buf, 0, want, size - want);
    let text = buf.toString('utf8');
    if (size > want) { // drop the partial first line of the window
      const nl = text.indexOf('\n');
      if (nl >= 0) text = text.slice(nl + 1);
    }
    return text.split('\n').slice(-lines).join('\n');
  } finally { fs.closeSync(fd); }
}

function tailLogs(lines = 60) { return tailLogText(lines); }

module.exports = {
  VERSION, paths, bundledServerDir, findPython, requiredPython, pythonGuidance, pythonInstallLines,
  ensureBootstrapped, startDetached, stopServer, serveForeground,
  runningServer, awaitLiveServer, anyLiveServer, fetchHealth, fetchUiRoot, openBrowser, tailLogs, tailLogText, readPid, pidAlive,
  checkLibraries, requirementModules, prepareRuntime, shieldStatus,
  runtimeInUse, translateFsLockError,
  bundlePresent, run, libraryCheckScript: () => LIBRARY_CHECK_SCRIPT,
  venvInterpreterOk, swapDirIntoPlace, processLooksForeign,
};
