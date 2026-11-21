# Security Threat Model (V3)

## Assets
Tenant mission data; BYOK provider credentials; budget integrity; the
platform's own provider key; audit history.

## Boundaries & controls
- **Tenant isolation:** every query is tenant-scoped; cross-tenant access
  returns a generic 404 (existence is not leaked). Enforced in the store
  layer + tested for missions, jobs, artifacts, connections.
- **Credentials:** accepted only at connection create/rotate over the API;
  envelope-encrypted (Fernet, per-value data key wrapped by a versioned
  master key); only masked metadata (`••••last4`) is ever returned;
  revoke/delete overwrite the ciphertext with a tombstone (crypto-shred);
  secrets never appear in logs, events, route decisions, exports or
  errors — provider error bodies are classified, never echoed.
- **The golden rule:** a mission pinned to a tenant connection can only
  use that connection. Any failure fails the mission loudly. Platform
  keys are never substituted (tested).
- **SSRF:** connection URLs pass a policy check (https for public
  endpoints, metadata/link-local always blocked, credentials-in-URL
  rejected, loopback/private only for local endpoints or an explicit dev
  gateway override).
- **Budget integrity:** reservations are claimed with an atomic
  conditional UPDATE inside the store lock; concurrent missions cannot
  overspend the cap (race-tested). Reservations reconcile to actuals or
  release on failure.
- **Admin surface:** separate `X-Admin-Key` (constant-time compare),
  disabled entirely when unconfigured.
- **Audit:** connection lifecycle, kill-switch flips and admin reads are
  written to an append-only audit log with masked metadata only.

## Known gaps (tracked, not hidden)
- Dev-header auth until the OIDC phase.
- DNS-rebinding pinning at request time (see KNOWN_LIMITATIONS #3).
- Master key lives in an env var / dev file (0600); production should
  use a KMS — the versioned envelope format is KMS-ready.
- Rate limiting is per-deployment infrastructure, not in-app yet.
