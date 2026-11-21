'use strict';
/* Minimal interactive prompts (readline + raw-mode password entry). */
const readline = require('readline');

/* Piped stdin: a fresh readline per question loses the input —
 * the first interface buffers the whole pipe and its 'close'
 * handler resolves the default before any buffered line is
 * delivered, so EVERY piped answer silently became the default
 * (login read its server URL as the bare "https://" default and
 * failed). Feed all questions from ONE shared line source instead:
 * lines queue in order, EOF resolves pending questions with null
 * (→ the caller's default). TTY keeps the per-question readline. */
let pipeState = null;
function pipedLine() {
  if (!pipeState) {
    pipeState = { lines: [], waiters: [], done: false };
    const rl = readline.createInterface({ input: process.stdin });
    rl.on('line', (l) => {
      const w = pipeState.waiters.shift();
      if (w) w(l); else pipeState.lines.push(l);
    });
    rl.on('close', () => {
      pipeState.done = true;
      let w;
      while ((w = pipeState.waiters.shift())) w(null);
    });
  }
  if (pipeState.lines.length) return Promise.resolve(pipeState.lines.shift());
  if (pipeState.done) return Promise.resolve(null);
  return new Promise((res) => pipeState.waiters.push(res));
}

function ask(question, def) {
  if (!process.stdin.isTTY) {
    process.stdout.write(def ? `${question} [${def}] ` : `${question} `);
    return pipedLine().then((line) => (line || '').trim() || def || '');
  }
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(def ? `${question} [${def}] ` : `${question} `, (ans) => {
      rl.close();
      resolve((ans || '').trim() || def || '');
    });
    rl.on('close', () => resolve(def || ''));
  });
}

function askPassword(question) {
  if (!process.stdin.isTTY) return ask(question);
  return new Promise((resolve) => {
    const stdin = process.stdin;
    process.stdout.write(`${question} `);
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    let buf = '';
    const onData = (ch) => {
      if (ch === '\r' || ch === '\n' || ch === '\u0004') {
        stdin.setRawMode(false); stdin.pause();
        stdin.removeListener('data', onData);
        process.stdout.write('\n');
        resolve(buf);
      } else if (ch === '\u0003') { // Ctrl-C
        process.stdout.write('\n');
        process.exit(130);
      } else if (ch === '\u007f' || ch === '\b') {
        if (buf) { buf = buf.slice(0, -1); process.stdout.write('\b \b'); }
      } else if (ch >= ' ') {
        buf += ch; process.stdout.write('*');
      }
    };
    stdin.on('data', onData);
  });
}

async function askChoice(question, options) {
  options.forEach((o, i) => process.stdout.write(`  ${i + 1}. ${o}\n`));
  const ans = await ask(question, '1');
  const n = parseInt(ans, 10);
  return Number.isInteger(n) && n >= 1 && n <= options.length ? n - 1 : 0;
}

module.exports = { ask, askPassword, askChoice };
