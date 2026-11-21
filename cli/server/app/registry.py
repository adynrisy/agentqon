"""AgentQon — agent registry seed data (Ch.6).

The Phase 1 slice ships with a small, manually curated registry: two
independent research agents with genuinely different strategies and one
critic. Ranking applies hard eligibility filters first, then measured
quality / reliability / latency / cost (Ch.6, Ch.30 scheduler pseudocode).
"""
from __future__ import annotations

from .domain import CapabilityManifest

SEED_AGENTS: list[CapabilityManifest] = [
    CapabilityManifest(
        agent_id="agent.research.alpha", version="1.3.0",
        description="Research and source synthesis — evidence-first, narrow-segment strategy",
        capabilities=["research.web", "summarize.sources"],
        permissions_requested=["web.read", "artifact.write"],
        strategy="niche-first",
        quality=4.4, reliability=0.96, latency_ms=820, cost_per_run_usd=0.32,
    ),
    CapabilityManifest(
        agent_id="agent.research.beta", version="1.1.0",
        description="Research and market synthesis — partnership-led strategy",
        capabilities=["research.web", "summarize.sources"],
        permissions_requested=["web.read", "artifact.write"],
        strategy="partnership-led",
        quality=4.1, reliability=0.94, latency_ms=1040, cost_per_run_usd=0.38,
    ),
    CapabilityManifest(
        agent_id="agent.critic.core", version="2.0.0",
        description="Adversarial reviewer — targeted, evidence-linked critique",
        capabilities=["critique.review"],
        permissions_requested=["artifact.read"],
        strategy="critic",
        quality=4.6, reliability=0.97, latency_ms=610, cost_per_run_usd=0.21,
    ),
]


def seed_registry(store, tenant_id: str) -> list[str]:
    return [store.upsert_agent(tenant_id, a.model_dump()) for a in SEED_AGENTS]


def rank_agents(agents: list[dict], required_capabilities: list[str]) -> list[dict]:
    """Scheduler (Ch.30): hard eligibility filters, then measured ranking.

    Filters: lifecycle must be active, capability must match, and agents
    with unknown data handling are excluded from sensitive work upstream.
    Ranking keeps dimensions visible — quality, reliability, latency and
    cost are combined into a transparent score, never a hidden one.
    """
    eligible = []
    for a in agents:
        if a.get("lifecycle") != "active":
            continue
        if not set(required_capabilities).issubset(set(a.get("capabilities", []))):
            continue
        eligible.append(a)

    def score(a: dict) -> float:
        # Transparent weighted score; dimensions are also returned raw in
        # the API so a user can see *why* an agent ranked where it did.
        return (
            0.45 * (a.get("quality", 0) / 5.0)
            + 0.30 * a.get("reliability", 0)
            + 0.15 * (1.0 - min(a.get("latency_ms", 1000), 5000) / 5000.0)
            + 0.10 * (1.0 - min(a.get("cost_per_run_usd", 1.0), 2.0) / 2.0)
        )

    return sorted(eligible, key=score, reverse=True)
