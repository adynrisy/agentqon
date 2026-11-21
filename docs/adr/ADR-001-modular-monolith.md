# ADR-001: Modular monolith first

- **Status:** Accepted
- **Date:** 2026-10-10
- **Owner:** Platform engineering
- **Context:** OmniMind AI implements the AI Agent Ecosystem textbook. Ch.1/5 direct: start as a modular monolith; extract services only when evidence supports the operational cost.
- **Options considered:** (1) Microservices from day one. (2) Modular monolith with clear internal module boundaries (domain, registry, planner, gateway, evaluator, orchestrator, store).
- **Decision:** Option 2. One FastAPI codebase, strong internal boundaries, durable state transitions.
- **Consequences:** (+) One deployable, simple operations, fast iteration for the Phase 1 slice. (−) Modules must be disciplined — no reaching across boundaries; the store is accessed only through the repository interface so PostgreSQL can replace SQLite in Phase 2 without touching orchestration.
- **Security/privacy impact:** Single trust boundary in the slice; tenant authorisation enforced in the store layer on every read.
- **Revisit trigger:** Independent scaling, ownership, security boundary, or release-cadence pressure on any single module.
