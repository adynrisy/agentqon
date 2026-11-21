"""AgentQon — agent adapter contract (Ch.7).

A platform-neutral interface protects the platform from provider
formats. Every run records its adapter version. Rules enforced here
and by the orchestrator: adapters never grant permissions, output is
schema-checked by the caller, and non-idempotent operations are never
blindly retried.

v20: the platform ships exactly one adapter family — real models over
the OpenAI-compatible chat API (app/llm_adapter.py), reached directly,
through a tenant's own BYOK connection, through an OmniRoute sidecar,
or through a local Ollama. There is no offline or canned generation
mode: with no model connected, generation is refused up front (see
app/modelstate.py) instead of being faked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .domain import new_id


@dataclass
class AgentRequest:
    mission_id: str
    task_id: str
    objective: str
    deliverables: list[str]
    output_schema: dict[str, Any]
    deadline_seconds: int = 90
    correlation_id: str = field(default_factory=lambda: new_id("corr"))
    # Phase 5: the mission's live-research corpus (SourceRecord dicts).
    # Empty = the seed library is the citable corpus.
    sources: list[dict] = field(default_factory=list)
    # v12: the mission's resolved task type (strategy | research |
    # writing | code | plan | qa | general). Adapters branch on it.
    task_type: str = "strategy"
    # v21: provenance-labelled long-term memory context (background
    # only — never citable evidence). Empty = nothing retrieved.
    context: str = ""


@dataclass
class AgentResponse:
    output: dict[str, Any]
    provider_request_id: str | None
    usage: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class AgentAdapter(Protocol):
    async def invoke(self, request: AgentRequest, manifest: dict) -> AgentResponse: ...
    async def health(self) -> dict[str, Any]: ...
