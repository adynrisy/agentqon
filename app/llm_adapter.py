"""AgentQon — real LLM adapters (Ch.7, Phase 2).

OpenAI-compatible chat-completions adapters for the research and critic
agents. One code path serves every provider that speaks that API —
OpenAI, OpenRouter, Groq, Together, a local Ollama (default), LM Studio —
selected purely by environment (AGENTQON_LLM_BASE_URL / _API_KEY / _MODEL).

Evidence discipline (Ch.11) is enforced in the prompt *and* in code:
models may only cite source_ids from the provided library; any other id
is stripped and the claim is marked unsupported, so the evaluator —
not the model — decides what counts as evidence.

If an LLM call fails, the run fails with the error on record (v20:
there is no substitute engine — see app/modelstate.py for how a model
gets connected in the first place).
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .adapters import AgentRequest, AgentResponse
from .config import Settings
from .domain import Claim, Critique, new_id
from .sources import SEED_SOURCES

LLM_ADAPTER_VERSION = "llm-adapter-1.0"

STRATEGY_BRIEFS = {
    "niche-first": (
        "NICHE-FIRST: launch to one narrow, well-understood segment, win it completely, "
        "then expand segment by segment. Emphasise retention proof, community adoption "
        "and low early acquisition cost."),
    "partnership-led": (
        "PARTNERSHIP-LED: launch through established channel partners, trading first-year "
        "revenue share for immediate distribution. Emphasise negotiation time, partner "
        "terms and dependency risk."),
}


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM response contained no JSON object")
    return json.loads(text[start:end + 1])


def _library_text(sources: list[dict] | None = None) -> str:
    lines = []
    if sources:
        for s in sources:
            lines.append(f"- source_id: {s['source_id']} | kind: {s.get('kind', 'secondary')} | "
                         f"published: {s.get('published_at', '')}\n"
                         f"  excerpt: \"{s.get('excerpt', '')}\"")
        return "\n".join(lines)
    for s in SEED_SOURCES:
        lines.append(f"- source_id: {s.source_id} | kind: {s.kind} | published: {s.published_at}\n"
                     f"  excerpt: \"{s.excerpt}\"")
    return "\n".join(lines)


@dataclass
class _ChatResult:
    data: dict
    usage: dict[str, Any]
    provider_request_id: str | None


def _is_local_endpoint(base_url: str) -> bool:
    """Loopback/private endpoints (local Ollama, LM Studio, a LAN server)
    must be reached directly: environment proxies (HTTP_PROXY etc.) are
    meant for public egress and break local calls — e.g. a no_proxy list
    containing bracketed IPv6 entries makes httpx fail with
    'Invalid port' before the request is even sent."""
    host = (urlparse(base_url).hostname or "").lower()
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return host.endswith((".local", ".lan", ".internal"))


class _ChatClient:
    """Thin wrapper over gateway_client.GatewayClient (V3): one typed
    transport for validation, catalog and inference. Prices come from the
    route decision/catalog when routed; from settings for the platform
    default connection."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 client=None, model: str | None = None,
                 price_in: float | None = None, price_out: float | None = None,
                 price_known: bool | None = None):
        self.s = settings
        self.transport = transport
        self.model = model or settings.llm_model
        self.price_in = settings.llm_price_in_per_m if price_in is None else price_in
        self.price_out = settings.llm_price_out_per_m if price_out is None else price_out
        # When routed (V3), the catalog decides whether the price is known —
        # an unknown price must stay unknown, never collapse to $0.
        self.price_known = price_known if price_known is not None else (
            price_in is not None or settings.llm_price_in_per_m > 0
            or _is_local_endpoint(settings.llm_base_url))
        if client is not None:
            self.gateway = client
        else:
            from .gateway_client import GatewayClient
            self.gateway = GatewayClient(base_url=settings.llm_base_url,
                                         api_key=settings.llm_api_key,
                                         timeout=180, transport=transport,
                                         connection_id="platform")
        self.trust_env = not _is_local_endpoint(self.gateway.base_url)

    async def chat_json(self, system: str, user: str) -> _ChatResult:
        data, usage = await self.gateway.chat_json(self.model, system, user)
        in_tok, out_tok = usage["input_tokens"], usage["output_tokens"]
        if self.price_known:
            cost = (in_tok / 1_000_000 * self.price_in
                    + out_tok / 1_000_000 * self.price_out)
            cost_usd, cost_status = round(cost, 6), ("known" if self.price_in == 0
                                                     and self.price_out == 0 else "estimated")
        else:
            cost_usd, cost_status = None, "unknown"  # never fake a zero (§105.8)
        return _ChatResult(data=data,
                           usage={"input_tokens": in_tok, "output_tokens": out_tok,
                                  "cost_usd": cost_usd, "cost_status": cost_status,
                                  "model": usage.get("model", self.model)},
                           provider_request_id=None)


class LLMResearchAdapter:
    """A real model proposing ONE candidate in the manifest's strategy."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 client=None, model: str | None = None,
                 price_in: float | None = None, price_out: float | None = None,
                 price_known: bool | None = None):
        self.client = _ChatClient(settings, transport, client=client, model=model,
                                  price_in=price_in, price_out=price_out,
                                  price_known=price_known)
        self.settings = settings

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "adapter_version": LLM_ADAPTER_VERSION,
                "model": self.client.model, "base_url": self.settings.llm_base_url}

    async def invoke(self, request: AgentRequest, manifest: dict) -> AgentResponse:
        if request.task_type and request.task_type != "strategy":
            return await self._invoke_general(request, manifest)
        strategy = manifest.get("strategy", "niche-first")
        brief = STRATEGY_BRIEFS.get(strategy, STRATEGY_BRIEFS["niche-first"])
        system = (
            "You are a research agent on the AgentQon platform. You produce ONE launch "
            "candidate following exactly the assigned strategy. Rules: (1) Every factual claim "
            "must cite source_ids ONLY from the provided source library — invent no sources, "
            "URLs, dates or figures. (2) If a claim is weakly supported, set its "
            "confidence_category to 'contested'. (3) Reply with a single JSON object, no prose.")
        persona = manifest.get("persona_prompt")
        if persona:
            # Imported persona agent (agency roster): the brief leads,
            # the platform's evidence rules still bind.
            system = (str(persona)[:6000]
                      + "\n\n--- Platform rules (override any conflicting habit): "
                        "produce ONE launch candidate as a single JSON object; every "
                        "factual claim cites source_ids ONLY from the provided source "
                        "library; invent no sources, URLs, dates or figures.")
        user = (
            f"Mission objective: {request.objective}\n"
            f"Deliverables: {', '.join(request.deliverables)}\n"
            f"Assigned strategy: {brief}\n\n"
            f"Source library (the only citable evidence):\n{_library_text(request.sources or None)}\n\n"
            "Return JSON with exactly these keys: title (string), summary (string, 2-3 "
            "sentences), claims (array of {text, source_ids, confidence_category}), "
            "assumptions (array of strings), risks (array of strings), "
            "estimated_cost_usd (number), time_to_learn (string).")
        if request.context:
            user += ("\n\nLong-term memory relevant to this mission "
                     "(background only — NOT evidence; never cite it "
                     "as a source):\n" + request.context)
        result = await self.client.chat_json(system, user)
        data = result.data
        valid_ids = ({s["source_id"] for s in request.sources}
                     if request.sources else {s.source_id for s in SEED_SOURCES})
        warnings: list[str] = []
        claims: list[Claim] = []
        for raw in data.get("claims", []):
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            ids = [i for i in raw.get("source_ids", []) if i in valid_ids]
            dropped = set(raw.get("source_ids", [])) - set(ids)
            if dropped:
                warnings.append(f"Stripped invented source ids: {sorted(dropped)}")
            claims.append(Claim(
                text=str(raw["text"]), source_ids=ids,
                confidence_category=raw.get("confidence_category", "supported")
                if ids else "unsupported"))
        if not claims:
            raise RuntimeError("LLM research output contained no usable claims")
        output = {
            "title": str(data.get("title") or f"Candidate — {strategy}"),
            "strategy": strategy,
            "summary": str(data.get("summary", "")),
            "claims": claims,
            "assumptions": [str(a) for a in data.get("assumptions", [])],
            "risks": [str(r) for r in data.get("risks", [])],
            "estimated_cost_usd": float(data.get("estimated_cost_usd", 0) or 0),
            "time_to_learn": str(data.get("time_to_learn", "")),
            "agent_version_id": f"{manifest['agent_id']}@{manifest['version']}",
        }
        return AgentResponse(output=output, provider_request_id=result.provider_request_id,
                             usage=result.usage, warnings=warnings)

    async def _invoke_general(self, request: AgentRequest, manifest: dict) -> AgentResponse:
        """v12: general-purpose tasks — the model produces the actual
        deliverable (the draft, answer, code, plan) in Markdown, plus
        checkable claims. Alpha works direct, Beta thorough."""
        thorough = manifest.get("strategy") in ("partnership-led", "thorough")
        approach = "thorough" if thorough else "direct"
        style = ("thorough and detailed — cover the task completely, with "
                 "structure and specifics" if thorough else
                 "direct and concise — complete the task with no padding")
        system = (
            f"You are an agent on the AgentQon platform completing a "
            f"{request.task_type} task for the user. Your style: {style}. "
            "Rules: (1) Produce the actual deliverable, not a description "
            "of one. (2) Every factual claim must cite source_ids ONLY from "
            "the provided source library — invent no sources, URLs, dates "
            "or figures; claims that need no source may have empty "
            "source_ids. (3) Reply with a single JSON object, no prose.")
        persona = manifest.get("persona_prompt")
        if persona:
            system = (str(persona)[:6000]
                      + "\n\n--- Platform rules (override any conflicting habit): "
                        f"you are completing a {request.task_type} task for the "
                        "user; complete it and return a single JSON object "
                        "with a Markdown deliverable; every factual claim cites "
                        "source_ids ONLY from the provided source library.")
        user = (
            f"Task: {request.objective}\n"
            f"Deliverables requested: {', '.join(request.deliverables)}\n\n"
            f"Source library (the only citable evidence):\n{_library_text(request.sources or None)}\n\n"
            "Return JSON with exactly these keys: title (string), summary "
            "(string, 2-3 sentences), deliverable (string — the complete "
            "work product in Markdown), claims (array of {text, source_ids, "
            "confidence_category}), assumptions (array of strings), risks "
            "(array of strings), estimated_cost_usd (number), "
            "time_to_learn (string).")
        if request.context:
            user += ("\n\nLong-term memory relevant to this mission "
                     "(background only — NOT evidence; never cite it "
                     "as a source):\n" + request.context)
        result = await self.client.chat_json(system, user)
        data = result.data
        valid_ids = ({s["source_id"] for s in request.sources}
                     if request.sources else {s.source_id for s in SEED_SOURCES})
        warnings: list[str] = []
        claims: list[Claim] = []
        for raw in data.get("claims", []):
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            ids = [i for i in raw.get("source_ids", []) if i in valid_ids]
            dropped = set(raw.get("source_ids", [])) - set(ids)
            if dropped:
                warnings.append(f"Stripped invented source ids: {sorted(dropped)}")
            claims.append(Claim(
                text=str(raw["text"]), source_ids=ids,
                confidence_category=raw.get("confidence_category", "supported")
                if ids else "unsupported"))
        deliverable = str(data.get("deliverable") or "").strip()
        if not deliverable:
            # Audit B (2026-10-12): a persona-led panelist can answer in
            # its OWN schema and still do the work — reproduced live
            # with the AI Data Remediation Engineer persona, which
            # returned {"transformation": "lambda x: x[::-1]",
            # "confidence_score": 0.99} for a code task: the platform
            # key was absent, the mission died, yet the work product was
            # right there. Recover the longest substantive string the
            # model returned as the deliverable and say so in the
            # warnings; raise only when there is genuinely no content.
            recovered_key, recovered = None, ""
            for key in ("content", "text", "answer", "result", "output",
                        "response", "solution", "code", "transformation",
                        "work", "body"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    recovered_key, recovered = key, value.strip()
                    break
            if not recovered:
                for key, value in data.items():
                    if key in ("title", "summary", "time_to_learn"):
                        continue  # metadata, not the work product
                    if isinstance(value, str) and len(value.strip()) > len(recovered):
                        recovered_key, recovered = key, value.strip()
            if recovered:
                warnings.append(
                    f"Model answered without the 'deliverable' key; "
                    f"recovered the work product from its '{recovered_key}' field")
                deliverable = recovered
            else:
                raise RuntimeError("LLM general output contained no deliverable")
        if not claims:
            claims = [Claim(text="Deliverable produced for the stated task.",
                            source_ids=[], confidence_category="unsupported")]
        output = {
            "title": str(data.get("title") or f"Draft — {request.task_type}"),
            "strategy": approach,
            "summary": str(data.get("summary", "")),
            "claims": claims,
            "assumptions": [str(a) for a in data.get("assumptions", [])],
            "risks": [str(r) for r in data.get("risks", [])],
            "estimated_cost_usd": float(data.get("estimated_cost_usd", 0) or 0),
            "time_to_learn": str(data.get("time_to_learn", "")),
            "deliverable": deliverable,
            "agent_version_id": f"{manifest['agent_id']}@{manifest['version']}",
        }
        return AgentResponse(output=output, provider_request_id=result.provider_request_id,
                             usage=result.usage, warnings=warnings)


class LLMCriticAdapter:
    """A real model issuing structured critiques (Ch.10 schema enforced)."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 client=None, model: str | None = None,
                 price_in: float | None = None, price_out: float | None = None,
                 price_known: bool | None = None):
        self.client = _ChatClient(settings, transport, client=client, model=model,
                                  price_in=price_in, price_out=price_out,
                                  price_known=price_known)
        self.settings = settings

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "adapter_version": LLM_ADAPTER_VERSION,
                "model": self.settings.llm_model}

    async def invoke(self, request: AgentRequest, manifest: dict) -> AgentResponse:
        candidates = request.output_schema.get("candidates", [])
        digest = []
        for c in candidates:
            claims = "\n".join(
                f"  - [{cl.get('confidence_category', 'supported')}] {cl.get('text', '')} "
                f"(sources: {', '.join(cl.get('source_ids', [])) or 'none'})"
                for cl in c.get("claims", []))
            digest.append(f"Candidate {c['candidate_id']} — {c.get('title', '')} "
                          f"(strategy: {c.get('strategy', '')})\n{claims}")
        system = (
            "You are an adversarial critic on the AgentQon platform. Find the weakest, "
            "least-supported claim in EACH candidate. A critique must be specific and "
            "falsifiable. Reply with a single JSON object, no prose.")
        user = (
            "Candidates and their claims:\n\n" + "\n\n".join(digest) +
            "\n\nReturn JSON: {\"critiques\": [{\"target_candidate_id\": string, "
            "\"target_claim\": string (exact claim text), \"issue\": string, "
            "\"evidence_or_test\": string, \"severity\": \"low|medium|high\", "
            "\"suggested_correction\": string, \"confidence\": \"low|medium|high\"}]}")
        result = await self.client.chat_json(system, user)
        critiques, warnings = [], []
        valid_ids = {c["candidate_id"] for c in candidates}
        for raw in result.data.get("critiques", []):
            try:
                crit = Critique(**raw)
            except Exception as exc:
                warnings.append(f"Dropped malformed critique: {exc}")
                continue
            if crit.target_candidate_id not in valid_ids:
                warnings.append("Dropped critique for unknown candidate")
                continue
            crit.critic_agent_version_id = f"{manifest['agent_id']}@{manifest['version']}"
            critiques.append(crit.model_dump())
        return AgentResponse(output={"critiques": critiques},
                             provider_request_id=result.provider_request_id,
                             usage=result.usage, warnings=warnings)
