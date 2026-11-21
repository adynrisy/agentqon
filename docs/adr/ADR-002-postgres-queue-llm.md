# ADR-002: PostgreSQL store, database-backed durable queue, OpenAI-compatible LLM adapters

- **Status:** Accepted
- **Date:** 2026-10-10
- **Owner:** Platform engineering
- **Context:** Phase 2 of the roadmap (textbook Ch.17, 19): reliable execution — a durable store, a durable queue with retries/cancellation, and real AI agents instead of mocks. No provider API key is available in this environment, and provider choice should not be baked into the platform (Ch.7).
- **Options considered:**
  1. Store: managed Postgres only / SQLite only / one repository interface with both dialects.
  2. Queue: Redis/RabbitMQ broker / Postgres-backed jobs table with leases (SKIP LOCKED) / in-process queue.
  3. Agents: hard-wire one provider SDK / OpenAI-compatible HTTP adapter configurable by environment.
- **Decision:** (1) One repository interface; PostgreSQL in production (OMNIMIND_DATABASE_URL), SQLite for zero-setup dev. (2) Jobs table in the same database — claim with lease, exponential backoff, dead-letter + replay; a separate worker process drains it, and the API can also drain inline for dev. (3) OpenAI-compatible adapter (OMNIMIND_LLM_BASE_URL/_API_KEY/_MODEL) serving cloud providers and local Ollama through the identical code path.
- **Consequences:** (+) No extra infrastructure for the queue at this scale; queue and mission state share transactions and one backup story; provider swap is an env change; a local model proves the real path with no key. (−) DB-queue needs care at high throughput (revisit if job rate outgrows it); SQLite/Postgres dialect differences must stay inside the store layer — enforced by running the integration suite against both.
- **Security/privacy impact:** Provider keys live only in the environment, never in the DB or repo. Which provider receives mission text is an operator decision, disclosed in the README and .env.example.
- **Revisit trigger:** Sustained queue contention, multi-host workers at scale, or a provider requiring a non-OpenAI-compatible API.
