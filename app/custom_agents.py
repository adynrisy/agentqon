"""AgentQon — workspace custom agents (v21.2).

Until now the registry only held bundled rosters (agency-agents, the
subagents library, framework packs) installed by import endpoints.
This module gives a workspace its own authoring surface: an operator
creates an agent from the UI (name, role, expertise, instructions) and
the agent becomes a first-class citizen of the same machinery —

  * a manifest in the tenant's registry (so GET /v1/agents lists it),
  * its brief in the persona store, in the TENANT's scope (v21.4 — so
    the orchestrator injects it into the LLM call exactly like a roster
    persona, the arena can duel it, and a same-named agent in another
    workspace never overwrites it),
  * declared task types the suitability layer scores at full fit (so
    it is ranked, and can win a panel seat, for the work it is for),
  * performance-ledger rows when it runs (put there by the
    orchestrator's generic candidate bookkeeping — nothing special).

Onboarding mirrors the roster importers: ACTIVE on arrival (the
operator's standing order — quarantine is the bench) with the same
declared priors (quality 3.8 / reliability 0.90 / 950 ms / $0.30),
explicitly unmeasured until the ledger says otherwise.

The routes live in a router built by `build_router` so main.py only
adds one include line; identity/role helpers are injected by main.py
(they close over its settings + store) rather than imported, which
keeps this module free of import cycles.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .domain import error_envelope
from .tasktypes import TASK_TYPES

CUSTOM_VERSION = "1.0.0"
NAME_MAX = 80
TITLE_MAX = 120
DESCRIPTION_MAX = 400
INSTRUCTIONS_MAX = 20_000

# Declared priors for a brand-new custom agent — identical to the
# agency roster import (app/agency.py): below the curated core, high
# enough to compete on declared turf, honest about being unmeasured.
CUSTOM_PRIORS = {"quality": 3.8, "reliability": 0.90,
                 "latency_ms": 950, "cost_per_run_usd": 0.30}


class CustomAgentBody(BaseModel):
    """Deliberately permissive: the endpoint validates by hand so the
    operator gets a human sentence, not a schema dump."""
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    title: str = ""
    description: str = ""
    instructions: str = ""
    task_types: list[str] = Field(default_factory=list)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def compose_persona(name: str, title: str, instructions: str) -> str:
    head = f"# {name}"
    if title.strip():
        head += f"\nRole: {title.strip()}"
    return f"{head}\n\n{instructions.strip()}"


def validate(body: CustomAgentBody) -> tuple[dict, str | None]:
    """Normalise the body; on failure return a human-readable reason."""
    name = (body.name or "").strip()
    if not name:
        return {}, "Give your agent a name."
    if len(name) > NAME_MAX:
        return {}, f"That name is {len(name)} characters — keep it under {NAME_MAX}."
    slug = slugify(name)
    if not slug:
        return {}, ("That name doesn't produce a usable agent id — "
                    "include at least one letter or digit.")
    title = (body.title or "").strip()
    if len(title) > TITLE_MAX:
        return {}, f"The role is {len(title)} characters — keep it under {TITLE_MAX}."
    description = (body.description or "").strip()
    if len(description) > DESCRIPTION_MAX:
        return {}, (f"The description is {len(description)} characters — "
                    f"keep it under {DESCRIPTION_MAX}.")
    instructions = (body.instructions or "").strip()
    if not instructions:
        return {}, ("Write the agent's instructions — they are the brief "
                    "the agent works from on every mission.")
    if len(instructions) > INSTRUCTIONS_MAX:
        return {}, (f"The instructions are {len(instructions)} characters — "
                    f"the limit is {INSTRUCTIONS_MAX}. Trim the brief and save again.")
    task_types: list[str] = []
    for t in body.task_types or []:
        t = str(t).strip().lower()
        if t not in TASK_TYPES:
            return {}, (f"Unknown task type '{t}' — choose from: "
                        + ", ".join(TASK_TYPES) + ".")
        if t not in task_types:
            task_types.append(t)
    if not task_types:
        task_types = ["general"]
    if not description:
        description = title or name
    return {"name": name, "slug": slug, "title": title,
            "description": description, "instructions": instructions,
            "task_types": task_types}, None


def build_manifest(agent_id: str, fields: dict, lifecycle: str = "active") -> dict:
    return {
        "agent_id": agent_id, "version": CUSTOM_VERSION,
        "owner": "custom (workspace-authored)",
        "name": fields["name"], "title": fields["title"],
        "description": fields["description"],
        "capabilities": ["persona.task", "research.web"],
        "permissions_requested": ["web.read", "artifact.write"],
        "strategy": "persona", "lifecycle": lifecycle,
        **CUSTOM_PRIORS,
        "origin": "custom", "persona_ref": agent_id,
        "task_types": fields["task_types"],
        "instructions": fields["instructions"],
    }


def is_custom(manifest: dict) -> bool:
    return (manifest.get("origin") == "custom"
            or str(manifest.get("agent_id", "")).startswith("custom."))


def build_router(*, store, guard, tenant_of, corr_of, not_found_of) -> APIRouter:
    router = APIRouter()

    def err(request: Request, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status,
                            content=error_envelope(code, message, corr_of(request)))

    def find_custom(request: Request, agent_id: str):
        """(tenant, manifest) for a custom agent in the caller's
        workspace — anything else is a 404 (existence is not leaked
        across tenants, and bundled agents are not editable here)."""
        t = tenant_of(request)
        m = store.get_agent(t, agent_id)
        if m is None or not is_custom(m):
            return t, None
        return t, m

    @router.get("/v1/agents/custom")
    async def list_custom(request: Request):
        t = tenant_of(request)
        agents = [a for a in store.list_agents(t) if is_custom(a)]
        agents.sort(key=lambda a: a.get("name", a["agent_id"]))
        return {"agents": agents, "total": len(agents)}

    @router.post("/v1/agents/custom", status_code=201)
    async def create_custom(request: Request, body: CustomAgentBody):
        guard(request, "admin")
        t = tenant_of(request)
        fields, problem = validate(body)
        if problem:
            return err(request, 400, "VALIDATION_ERROR", problem)
        agent_id = f"custom.{fields['slug']}"
        if store.get_agent(t, agent_id) is not None:
            return err(request, 409, "STATE_CONFLICT",
                       f"An agent named '{fields['name']}' already exists in "
                       "this workspace — pick a different name, or edit the "
                       "existing one.")
        manifest = build_manifest(agent_id, fields)
        store.put_persona(agent_id, compose_persona(
            fields["name"], fields["title"], fields["instructions"]),
            tenant_id=t)
        avid = store.upsert_agent(t, manifest)
        created = store.get_agent(t, agent_id)
        return {**created, "agent_version_id": avid}

    @router.get("/v1/agents/custom/{agent_id}")
    async def get_custom(request: Request, agent_id: str):
        _, m = find_custom(request, agent_id)
        if m is None:
            return not_found_of(request)
        return m

    @router.put("/v1/agents/custom/{agent_id}")
    async def update_custom(request: Request, agent_id: str, body: CustomAgentBody):
        guard(request, "admin")
        t, existing = find_custom(request, agent_id)
        if existing is None:
            return not_found_of(request)
        fields, problem = validate(body)
        if problem:
            return err(request, 400, "VALIDATION_ERROR", problem)
        if fields["slug"] != slugify(existing.get("name", "")):
            clash = store.get_agent(t, f"custom.{fields['slug']}")
            if clash is not None and clash["agent_id"] != agent_id:
                return err(request, 409, "STATE_CONFLICT",
                           f"Another agent in this workspace already uses "
                           f"the name '{fields['name']}' — pick a different one.")
        # Editing refreshes the brief, never the operator's lifecycle
        # decision — the same rule the roster re-imports follow.
        manifest = build_manifest(agent_id, fields,
                                  lifecycle=existing.get("lifecycle", "active"))
        store.put_persona(agent_id, compose_persona(
            fields["name"], fields["title"], fields["instructions"]),
            tenant_id=t)
        store.upsert_agent(t, manifest)
        return store.get_agent(t, agent_id)

    @router.delete("/v1/agents/custom/{agent_id}")
    async def delete_custom(request: Request, agent_id: str):
        guard(request, "admin")
        t, existing = find_custom(request, agent_id)
        if existing is None:
            return not_found_of(request)
        avid = existing["agent_version_id"]
        store.delete_agent(t, avid)
        store.delete_agent_stats(t, avid)
        # The brief lives in THIS tenant's scope: deleting it can never
        # remove another workspace's same-named agent's brief (v21.4).
        store.delete_persona(agent_id, tenant_id=t)
        # Legacy shared-scope briefs (pre-v21.4 the store was global):
        # drop the shared row only when no workspace still holds this
        # agent, so a brief another tenant still uses is never removed.
        if not store.agent_tenants(agent_id):
            store.delete_persona(agent_id)
        return {"deleted": agent_id, "agent_version_id": avid}

    return router
