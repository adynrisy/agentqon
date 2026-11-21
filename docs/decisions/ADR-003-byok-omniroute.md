# ADR-003: BYOK vault + OmniRoute as an isolated sidecar

Date: 2026-10-10 · Status: accepted

## Context
Users want to run missions on their own provider budget ("bring your own
key"), including through their own OmniRoute gateway. The platform must
never mix keys across tenants or quietly spend platform budget on a
user's mission.

## Decision
1. OmniRoute runs as a **separate pinned process** (v3.8.51). OmniMind
   integrates only through its OpenAI-compatible inference plane with a
   gateway key, via a typed client. No vendoring, no modification.
2. Tenant credentials live in OmniMind's **envelope-encrypted vault**
   (Fernet; versioned master key), accepted only at create/rotate,
   displayed masked only, crypto-shredded on revoke/delete.
3. Routing is OmniMind's own deterministic engine over a synced catalog;
   OmniRoute is transport. Route decisions are persisted with reasons.
4. A mission pinned to a connection **fails rather than substituting**
   any other key. Mock fallback exists only for the platform default in
   dev agent mode and is always recorded as an event when it fires.

## Consequences
- Two independent encryption layers (OmniMind vault + OmniRoute store).
- Catalog completeness depends on gateway listing behaviour; the
  user-declared entry mechanism absorbs the observed gap honestly.
- Upgrade of the sidecar is a deliberate, audited event.
