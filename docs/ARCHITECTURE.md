# Architecture (V3)

Modular monolith (per the textbook), three processes max:

- **API** (`app/main.py`) — FastAPI. Validates mission contracts, owns
  connections/catalog/admin APIs, enqueues jobs (or runs them inline in
  dev mode), serves the single-page UI.
- **Worker** (`app/worker.py`) — claims jobs from the durable DB queue
  (lease + heartbeat of attempts, exponential backoff, DLQ), executes
  missions and revisions through the orchestrator.
- **Optional sidecars** — OmniRoute (pinned) and/or Ollama, reached only
  via the typed `GatewayClient`.

## Core modules

| Module | Responsibility |
|---|---|
| `app/domain.py` | Contracts, state machines, event/result types |
| `app/store.py` | Repository interface over SQLite/PostgreSQL; queue; vault tables; catalog; reservations; audit |
| `app/vault.py` | Envelope encryption, key versioning, masking |
| `app/netpolicy.py` | SSRF URL policy for provider endpoints |
| `app/connections.py` | BYOK lifecycle state machine + validation |
| `app/catalog.py` | Catalog sync/normalization, freshness, price provenance |
| `app/router.py` | Pure deterministic router (hard filters → scoring) |
| `app/orchestrator.py` | Plan → route → reserve → run → critique → evaluate; pause/revision; strict BYOK |
| `app/gateway_client.py` | Typed OpenAI-compatible client, normalized errors |
| `app/llm_adapter.py` | Research/critic adapters over the gateway client; real models only — no mock fallback (v20 removed it; the sole test stub lives under `tests/`) |
| `app/tool_gateway.py` | Tool authorization + budget checks (Phase 1, unchanged contract) |
| `app/exporter.py` | Mission → Markdown decision brief |

## Data invariants
- Tenant scoping on every row; cross-tenant reads return 404-shape.
- Secrets exist only as vault ciphertext at rest, plaintext only in
  memory during a call, masked everywhere else.
- Money moves via reservations: `reserved_usd` on the mission plus a
  reservation row per call; reconcile or release, never both, exactly
  once (state-checked transitions).
- Route decisions, spend ledger and events are append-only history.
