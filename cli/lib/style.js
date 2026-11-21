'use strict';
/* AgentQon CLI styling — hand-rolled ANSI, zero dependencies.
 *
 * Violet → cyan identity. Everything degrades gracefully: when stdout is
 * not a TTY or NO_COLOR is set, every helper returns plain text so piped
 * output stays clean and scriptable.
 */
const tty = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;
const COLOR = tty;

const C = {
  reset: '\x1b[0m', bold: '\x1b[1m', dim: '\x1b[2m', italic: '\x1b[3m',
  violet: '\x1b[38;5;141m', purple: '\x1b[38;5;99m', cyan: '\x1b[38;5;51m',
  teal: '\x1b[38;5;49m', green: '\x1b[38;5;82m', yellow: '\x1b[38;5;220m',
  red: '\x1b[38;5;203m', orange: '\x1b[38;5;208m', grey: '\x1b[38;5;244m',
  white: '\x1b[38;5;255m',
};

function paint(code, s) { return COLOR ? code + s + C.reset : String(s); }
const violet = (s) => paint(C.violet, s);
const purple = (s) => paint(C.purple, s);
const cyan = (s) => paint(C.cyan, s);
const teal = (s) => paint(C.teal, s);
const green = (s) => paint(C.green, s);
const yellow = (s) => paint(C.yellow, s);
const red = (s) => paint(C.red, s);
const orange = (s) => paint(C.orange, s);
const grey = (s) => paint(C.grey, s);
const bold = (s) => paint(C.bold, s);
const dim = (s) => paint(C.dim, s);
const italic = (s) => paint(C.italic, s);

const strip = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, '');
const vlen = (s) => strip(s).length;

function termWidth() {
  const w = process.stdout.columns || 80;
  return Math.max(40, Math.min(w, 100));
}

/* ---------------- banner ---------------- */
const GLYPHS = {
  O: [' ████ ', '██  ██', '██  ██', '██  ██', ' ████ '],
  M: ['██   ██', '███ ███', '██ █ ██', '██   ██', '██   ██'],
  N: ['██  ██', '███ ██', '██ ███', '██  ██', '██  ██'],
  I: ['█████', '  █  ', '  █  ', '  █  ', '█████'],
  D: ['█████ ', '██  ██', '██  ██', '██  ██', '█████ '],
  A: [' ████ ', '██  ██', '██████', '██  ██', '██  ██'],
  G: [' ████ ', '██    ', '██ ███', '██  ██', ' ████ '],
  E: ['██████', '██    ', '█████ ', '██    ', '██████'],
  T: ['██████', '  ██  ', '  ██  ', '  ██  ', '  ██  '],
  Q: [' ████ ', '██  ██', '██  ██', '██ ▄██', ' ▀▀▀██'],
  ' ': ['   ', '   ', '   ', '   ', '   '],
};
const RAMP = [141, 141, 140, 139, 105, 104, 68, 67, 44, 43, 51, 51];

function wordArt(word) {
  const rows = ['', '', '', '', ''];
  for (const ch of word.toUpperCase()) {
    const g = GLYPHS[ch] || GLYPHS[' '];
    for (let r = 0; r < 5; r++) rows[r] += (rows[r] ? ' ' : '') + g[r];
  }
  return rows;
}

function gradientLine(line) {
  if (!COLOR) return line;
  const n = Math.max(1, vlen(line) - 1);
  let out = '';
  let col = 0;
  for (const ch of line) {
    if (ch === ' ') { out += ch; col++; continue; }
    const idx = Math.min(RAMP.length - 1, Math.floor((col / n) * RAMP.length));
    out += `\x1b[38;5;${RAMP[idx]}m${ch}`;
    col++;
  }
  return out + C.reset;
}

function banner(version) {
  const lines = wordArt('AGENTQON').map(gradientLine);
  lines.push('');
  lines.push('  ' + violet(bold('AGENTQON')) + dim('  ·  missions run by rival agents — critic-verified, evidence-labelled'));
  if (version) lines.push('  ' + dim(`terminal harness v${version}`));
  return lines.join('\n');
}

/* ---------------- layout primitives ---------------- */
function wrap(text, width) {
  const out = [];
  for (const para of String(text ?? '').split('\n')) {
    if (para === '') { out.push(''); continue; }
    let line = '';
    for (const word of para.split(/\s+/)) {
      if (word === '') continue;
      if (line && vlen(line) + 1 + vlen(word) > width) { out.push(line); line = word; }
      else line = line ? line + ' ' + word : word;
      while (vlen(line) > width) { out.push(line.slice(0, width)); line = line.slice(width); }
    }
    out.push(line);
  }
  return out;
}

function panel(title, bodyLines, opts = {}) {
  const width = opts.width || termWidth();
  const inner = width - 4; // border + one space of padding each side
  const accent = opts.color || C.violet;
  const titleTxt = title ? ` ${title} ` : '';
  const topFill = Math.max(0, width - 2 - vlen(titleTxt) - 1);
  const top = paint(accent, '╭─') + (title ? bold(paint(accent, titleTxt)) : '') +
    paint(accent, '─'.repeat(topFill) + '╮');
  const bottom = paint(accent, '╰' + '─'.repeat(width - 2) + '╯');
  const lines = [top];
  for (const raw of bodyLines) {
    for (const l of wrap(raw, inner)) {
      lines.push(paint(accent, '│ ') + l + ' '.repeat(Math.max(0, inner - vlen(l))) + paint(accent, ' │'));
    }
  }
  lines.push(bottom);
  return lines.join('\n');
}

function table(headers, rows, opts = {}) {
  const widths = headers.map((h, i) =>
    Math.max(vlen(h), ...rows.map((r) => vlen(r[i] ?? ''))));
  const renderRow = (cells, isHead) => cells.map((c, i) => {
    const s = String(c ?? '');
    const padded = s + ' '.repeat(Math.max(0, widths[i] - vlen(s)));
    return isHead ? bold(dim(padded)) : padded;
  }).join('  ');
  const sep = widths.map((w) => '─'.repeat(w)).join('  ');
  return [renderRow(headers, true), dim(sep), ...rows.map((r) => renderRow(r, false))].join('\n');
}

function bar(score, max = 5, width = 10) {
  const filled = Math.max(0, Math.min(width, Math.round((Number(score) / max) * width)));
  const s = '█'.repeat(filled) + '░'.repeat(width - filled);
  return (filled >= width * 0.7 ? teal(s) : filled >= width * 0.4 ? yellow(s) : red(s));
}

function dot(status) {
  const map = {
    completed: C.green, succeeded: C.green, active: C.green, ok: C.green, voted: C.green,
    running: C.cyan, in_progress: C.cyan, queued: C.grey, draft: C.grey,
    proposed: C.grey, pending: C.grey, scheduled: C.grey,
    failed: C.red, cancelled: C.red, rejected: C.red, error: C.red, dead: C.red,
    paused: C.yellow, degraded: C.yellow,
  };
  return paint(map[status] || C.grey, '●');
}

function statusText(status) {
  const color = { completed: green, failed: red, cancelled: red, running: cyan, active: green }[status] || grey;
  return `${dot(status)} ${color(status)}`;
}

/* ---------------- progress stream ----------------
 * Progress UI (spinner, bars, phase lines) never pollutes stdout when
 * stdout is piped: interactive runs see it on the terminal; piped runs
 * get the same plain lines on stderr, so stdout stays clean data —
 * anything machine-readable (now or future --json) is never mixed
 * with progress chatter. */
function progressStream() { return process.stdout.isTTY ? process.stdout : process.stderr; }

/* ---------------- spinner ---------------- */
const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
class Spinner {
  constructor() { this.timer = null; this.label = ''; this.frame = 0; this.open = false; this.ttySpin = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR; }
  _tickStart() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.timer = setInterval(() => {
      progressStream().write(`\r${violet(FRAMES[this.frame++ % FRAMES.length])} ${this.label}   `);
    }, 80);
  }
  start(label) {
    this.label = label;
    this.open = true;
    if (!this.ttySpin) { progressStream().write(dim(`  · ${label}\n`)); return this; }
    this._tickStart(); // clears any previous interval first — reuse can never leak a timer
    return this;
  }
  setLabel(label) {
    const changed = label !== this.label;
    this.label = label;
    if (!this.ttySpin) { if (changed) progressStream().write(dim(`  · ${label}\n`)); return; }
    /* If the spinner is mid-session but its timer is gone (e.g. an
     * intermediate phase line was printed), resume ticking — a phase
     * change must never leave a frozen spinner on screen. */
    if (this.open && !this.timer) this._tickStart();
  }
  _clear() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (this.ttySpin) progressStream().write('\r\x1b[2K');
  }
  /* An intermediate phase finished: print its ✓ line but KEEP the
   * spinner alive for the next phase. The terminal calls are
   * succeed()/fail()/stop() — those are what close the session. */
  succeedPhase(label) {
    if (this.ttySpin) progressStream().write('\r\x1b[2K');
    progressStream().write(`${green('✓')} ${label}\n`);
  }
  succeed(label) { this.open = false; this._clear(); progressStream().write(`${green('✓')} ${label}\n`); }
  fail(label) { this.open = false; this._clear(); progressStream().write(`${red('✗')} ${label}\n`); }
  stop() { this.open = false; this._clear(); }
}

/* ---------------- determinate progress ----------------
 * For work with a known size (pip downloads counted per package,
 * payload files hashed/encrypted per file): a real bar + percentage
 * on a terminal; plain milestone lines (every 10%) when piped. With
 * no total it degrades to a spinner + elapsed clock — the point is
 * that a long phase NEVER sits silent: something on screen always
 * moves, so a working terminal never reads as frozen. */
function fmtElapsed(ms) {
  const sec = Math.round(ms / 1000);
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`;
}
const BAR_WIDTH = 22;
class Progress {
  constructor() {
    this.label = ''; this.done = 0; this.total = 0;
    this.startedAt = Date.now(); this.frame = 0;
    this.tty = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;
    this.stream = progressStream();
    this.timer = null; this.lastDraw = 0; this.lastPct = -1; this.nextMilestone = 0;
    this.active = false;
  }
  _elapsed() { return fmtElapsed(Date.now() - this.startedAt); }
  start(label, total = 0) {
    /* Leak-proof reuse: one Progress is shared across every setup
     * phase, so a fresh start MUST retire the previous interval —
     * an orphaned interval holds the event loop open and the CLI
     * would hang after printing success. */
    this._stopTimer();
    this.active = true; this.total = total; this.done = 0;
    this.startedAt = Date.now(); this.lastPct = -1; this.nextMilestone = 0;
    this.setLabel(label);
    if (this.tty) {
      this.timer = setInterval(() => this._draw(), 120);
      this._draw();
    }
    return this;
  }
  setLabel(label) {
    if (label === this.label) return;
    this.label = label;
    if (!this.tty) this.stream.write(dim(`  · ${label}\n`));
    else this._draw();
  }
  update(done, total) {
    if (total) this.total = total;
    this.done = done;
    const pct = this.total ? Math.min(100, Math.floor((this.done / this.total) * 100)) : -1;
    if (this.tty) {
      if (pct !== this.lastPct || Date.now() - this.lastDraw > 250) this._draw();
    } else if (pct >= 0 && pct >= this.nextMilestone) {
      this.nextMilestone = pct - (pct % 10) + 10;
      this.stream.write(dim(`  · ${this.label} — ${pct}% (${this.done}/${this.total})\n`));
    }
    if (pct >= 0) this.lastPct = pct;
  }
  _draw() {
    if (!this.tty || !this.active) return;
    this.lastDraw = Date.now();
    const frame = FRAMES[this.frame++ % FRAMES.length];
    let line;
    if (this.total > 0) {
      const pct = Math.min(100, Math.floor((this.done / this.total) * 100));
      const filled = Math.round((pct / 100) * BAR_WIDTH);
      const b = '█'.repeat(filled) + '░'.repeat(BAR_WIDTH - filled);
      line = `${violet(frame)} ${this.label}  ${teal(b)} ${pct}% ${dim(`· ${this.done}/${this.total} · ${this._elapsed()}`)}`;
    } else {
      line = `${violet(frame)} ${this.label} ${dim(`· ${this._elapsed()}`)}`;
    }
    this.stream.write('\r\x1b[2K' + line);
  }
  _stopTimer() { if (this.timer) { clearInterval(this.timer); this.timer = null; } }
  _clearLine() { if (this.tty && this.active) this.stream.write('\r\x1b[2K'); }
  succeed(label) {
    this._stopTimer(); this._clearLine(); this.active = false;
    this.stream.write(`${green('✓')} ${label || this.label}\n`);
  }
  fail(label) {
    this._stopTimer(); this._clearLine(); this.active = false;
    this.stream.write(`${red('✗')} ${label || this.label}\n`);
  }
  stop() { this._stopTimer(); this._clearLine(); this.active = false; }
}

/* ---------------- messages ---------------- */
function errorBox(err, hint) {
  const lines = [`${red('✗ Error')}  ${err && err.message ? err.message : String(err)}`];
  if (hint) lines.push(dim(hint));
  return lines.join('\n');
}
function footnote(s) { return dim(s); }

module.exports = {
  COLOR, C, violet, purple, cyan, teal, green, yellow, red, orange, grey,
  bold, dim, italic, strip, vlen, termWidth, banner, wrap, panel, table,
  bar, dot, statusText, Spinner, Progress, progressStream, fmtElapsed, errorBox, footnote,
};
