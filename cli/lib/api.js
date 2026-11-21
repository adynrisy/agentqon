'use strict';
/* Thin client over the AgentQon HTTP API (global fetch, Node >= 18).
 * Auth mirrors the web UI: `Authorization: Bearer <omk_ session token>`
 * plus the `X-Tenant-Id` header selecting the workspace. Against a
 * dev-mode server (the local default) the headers are simply optional —
 * the server falls back to its demo tenant.
 *
 * Shield (v20.2): against the packaged local server the client also
 * presents the per-install local token (X-OmniMind-Local-Token) and
 * HMAC-signs every request with the per-install channel secret
 * (spec §B1). Remote/hosted servers keep the session-token flow only.
 */
const shield = require('./shield');

class ApiError extends Error {
  constructor(status, message, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/* F9 (security audit 2026-10-22): the Shield credentials are
 * per-install secrets for THIS machine's packaged server. They are
 * attached ONLY when the configured base URL is loopback — a
 * remote/hosted server must never receive the local token or the
 * HMAC channel headers, even if a hand-edited (or future) config
 * object carries the values into remote mode. Call sites already
 * attach the values for local targets only; this is the client-side
 * fence that keeps that true by construction. */
function isLoopbackBase(base) {
  let host;
  try { host = new URL(base).hostname.toLowerCase(); } catch { return false; }
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host === '::1' || host === '[::1]') return true; // URL keeps the brackets on IPv6 hosts
  const m = /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  return !!m && [m[1], m[2], m[3]].every((o) => Number(o) <= 255);
}

class Api {
  constructor(cfg) {
    this.cfg = cfg;
    this.base = String(cfg.server || '').replace(/\/+$/, '');
  }

  _shieldHeaders(method, path, bodyStr) {
    const headers = {};
    /* Loopback only (F9 above): against a non-loopback base the
     * Shield headers are withheld even when cfg carries them. */
    if (!isLoopbackBase(this.base)) return headers;
    if (this.cfg.localToken) headers['x-omnimind-local-token'] = this.cfg.localToken;
    if (this.cfg.channelSecret) {
      Object.assign(headers, shield.signRequest(this.cfg.channelSecret, method, path, bodyStr || ''));
    }
    return headers;
  }

  async request(method, path, body, opts = {}) {
    const headers = { accept: 'application/json' };
    const bodyStr = body !== undefined ? JSON.stringify(body) : undefined;
    if (body !== undefined) headers['content-type'] = 'application/json';
    if (this.cfg.token) headers.authorization = `Bearer ${this.cfg.token}`;
    if (this.cfg.tenantId) headers['x-tenant-id'] = this.cfg.tenantId;
    if (this.cfg.userId) headers['x-user-id'] = this.cfg.userId;
    if (opts.idempotencyKey) headers['idempotency-key'] = opts.idempotencyKey;
    Object.assign(headers, this._shieldHeaders(method, path, bodyStr));
    let res;
    try {
      /* opts.signal lets an interactive caller (the chat REPL) cancel
       * an in-flight wait with Ctrl+C; it is combined with the normal
       * timeout so neither can wedge the caller. */
      const timeout = AbortSignal.timeout(opts.timeout || 60_000);
      const signal = opts.signal
        ? (typeof AbortSignal.any === 'function' ? AbortSignal.any([timeout, opts.signal]) : opts.signal)
        : timeout;
      res = await fetch(this.base + path, {
        method,
        headers,
        body: bodyStr,
        signal,
      });
    } catch (e) {
      throw new ApiError(0, `cannot reach AgentQon at ${this.base} — is the server running? Start it with \`agentqon\`, or run \`agentqon doctor\`. (${e.cause ? e.cause.code || e.message : e.message})`);
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!res.ok) {
      const err = (data && data.error) || {};
      /* v21.4.2: prefer the envelope's operator `detail` when the
       * server sent one (local installs do) — the real reason beats
       * the sanitized message on the operator's own terminal. */
      const msg = err.detail || err.message || (data && data.detail) || `HTTP ${res.status}`;
      throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg), err.code);
    }
    return data;
  }

  get(path, opts) { return this.request('GET', path, undefined, opts); }
  post(path, body, opts) { return this.request('POST', path, body ?? {}, opts); }
  del(path, opts) { return this.request('DELETE', path, undefined, opts); }
  /* No getText: proof-pack Markdown is served as JSON (`markdown`
   * field) and read through request() above. The old raw-text
   * variant had zero callers and threw a bare `HTTP <status>` —
   * the error shape the rest of the CLI deliberately avoids
   * (audit 2026-10-22 L2) — so it is deleted, not carried. */
}

module.exports = { Api, ApiError, isLoopbackBase };
