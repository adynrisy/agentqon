"""AgentQon — Tool Gateway (Ch.12).

Agents never call powerful tools directly. Every invocation passes the
lifecycle: authenticate the run -> tenant policy -> schema/scope check ->
data classification -> budget check -> risk classification -> approval
(if high risk) -> execute -> audit record. The gateway's record, not the
agent's word, is what happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .domain import HIGH_RISK_TOOLS, KNOWN_TOOLS, MISSION_BUDGET_CEILING_USD

ESTIMATED_COSTS = {  # USD per invocation, slice estimates
    "web.read": 0.02, "artifact.write": 0.005, "artifact.read": 0.002,
    "code.execute": 0.05, "email.send": 0.01, "message.send": 0.01,
    "purchase.create": 0.0, "file.delete": 0.0, "deploy.production": 0.10,
    "reach.read": 0.01, "reach.rss": 0.005, "reach.github": 0.005,
    "reach.youtube": 0.01, "reach.reddit": 0.005, "reach.exa": 0.02,
    "code.symbol_context": 0.002, "code.impact": 0.002, "code.search": 0.002,
}


@dataclass
class GatewayDecision:
    allowed: bool
    reason: str
    risk: str = "low"
    invocation_id: Optional[str] = None
    approval_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class ToolGateway:
    def __init__(self, store, research_provider=None, research_mode: str = "seed",
                 reach_channels: set | None = None):
        self.store = store
        self.research_provider = research_provider
        self.research_mode = research_mode
        # v21: operator-enabled reach channels (AGENTQON_REACH_CHANNELS).
        # Default EMPTY: every reach tool is denied as not_configured
        # until its channel is switched on.
        self.reach_channels = set(reach_channels or set())
        self._live_cache: dict[str, dict] = {}

    def authorize_and_run(self, tenant_id: str, mission: dict, tool: str,
                          args: dict, estimated_cost: Optional[float] = None) -> GatewayDecision:
        contract = mission["contract"]
        policy = contract.get("tools", {})
        cost = estimated_cost if estimated_cost is not None else ESTIMATED_COSTS.get(tool, 0.01)
        risk = "high" if tool in HIGH_RISK_TOOLS else ("moderate" if tool == "code.execute" else "low")

        def record(status: str, data: dict) -> str:
            return self.store.put_invocation(tenant_id, mission["id"], tool, risk, status, data)

        # 1. Known tool?
        if tool not in KNOWN_TOOLS:
            iid = record("denied", {"args": args, "reason": "UNKNOWN_TOOL"})
            return GatewayDecision(False, "UNKNOWN_TOOL", risk, iid)
        # 2. Mission policy: deny list wins, then allow list.
        if tool in policy.get("deny", []):
            iid = record("denied", {"args": args, "reason": "DENIED_BY_POLICY"})
            return GatewayDecision(False, "DENIED_BY_POLICY", risk, iid)
        if tool not in policy.get("allow", []):
            iid = record("denied", {"args": args, "reason": "NOT_IN_ALLOW_LIST"})
            return GatewayDecision(False, "NOT_IN_ALLOW_LIST", risk, iid)
        # 3. Data classification: confidential missions may not use external send/purchase tools.
        if contract.get("data_classification") == "confidential" and tool in HIGH_RISK_TOOLS:
            iid = record("denied", {"args": args, "reason": "CLASSIFICATION_BLOCKS_EXTERNAL_ACTION"})
            return GatewayDecision(False, "CLASSIFICATION_BLOCKS_EXTERNAL_ACTION", risk, iid)
        # 3b. v21 reach enablement: a reach tool whose channel the
        # operator has not enabled is denied as NOT_CONFIGURED — the
        # capability is declared, never silently live.
        if tool.startswith("reach."):
            from .reach import channel_for
            channel = channel_for(tool)
            if channel not in self.reach_channels:
                iid = record("denied", {"args": args, "reason": "REACH_NOT_CONFIGURED",
                                        "channel": channel,
                                        "hint": "enable with AGENTQON_REACH_CHANNELS"})
                return GatewayDecision(False, "REACH_NOT_CONFIGURED", risk, iid)
        # 4. Runaway ceiling (v21.3): budgets are not user-facing, so
        # the only spend gate left is the internal ceiling — a call
        # that would push the mission past it is blocked; ordinary
        # missions never get near it.
        if mission["spent"] + cost > MISSION_BUDGET_CEILING_USD:
            iid = record("denied", {"args": args, "reason": "BUDGET_EXCEEDED",
                                    "spent": mission["spent"],
                                    "limit": MISSION_BUDGET_CEILING_USD})
            return GatewayDecision(False, "BUDGET_EXCEEDED", risk, iid)
        # 5. High risk -> approval request bound to an exact action preview.
        if risk == "high" and contract.get("approval_policy", {}).get("external_side_effects") == "required":
            preview = f"{tool} with args {args} on mission {mission['id']} (cost est. ${cost:.2f})"
            iid = record("awaiting_approval", {"args": args, "preview": preview})
            aid = self.store.put_approval(tenant_id, mission["id"], iid, preview)
            return GatewayDecision(False, "APPROVAL_REQUIRED", risk, iid, approval_id=aid)
        # 6. Execute and audit. web.read executes REAL live research
        # when enabled (Phase 5); reach.* executes the v21 reach pack;
        # everything else keeps slice behaviour. Reach audit records
        # store the redacted, truncated audit view (M5-style), never a
        # second full copy of fetched content.
        result = self._execute(tool, args, mission, tenant_id)
        if tool.startswith("reach."):
            from .reach import audit_view
            iid = record("completed", {"args": args,
                                       "result": audit_view(result)})
        else:
            iid = record("completed", {"args": args, "result": result})
        if cost > 0:
            self.store.add_usage(tenant_id, mission["id"], "tools", cost, f"{tool} via gateway")
        return GatewayDecision(True, "OK", risk, iid, result=result)

    def _execute(self, tool: str, args: dict, mission: dict, tenant_id: str) -> dict[str, Any]:
        if tool.startswith("reach."):
            from .reach import execute as reach_execute
            return reach_execute(tool, args)
        if tool.startswith("code."):
            return self._execute_code(tool, args)
        if tool == "web.read" and args.get("query"):
            return self._execute_web_read(args, mission, tenant_id)
        return self._execute_recorded(tool, args)

    def _execute_code(self, tool: str, args: dict) -> dict[str, Any]:
        """v21 native code graph tools: built per workspace on first
        use, cached, rebuilt when the workspace fingerprint changes.
        Read-only over the workspace the caller names."""
        from pathlib import Path

        from .codegraph import ensure_graph
        root = str(args.get("workspace", ""))
        if not root or not Path(root).is_dir():
            return {"status": "invalid_args",
                    "reason": "code tools require args.workspace (an existing directory)"}
        data_dir = Path(self.store.artifacts_dir).parent
        # Audit F11 (mode-aware; same root as F3/F7): in accounts/
        # hosted mode a mission may only index a workspace inside
        # the platform data area, where tenant workspaces live —
        # naming an arbitrary server directory discloses its layout
        # (symbol names, file paths, line numbers) and burns
        # indexing cost. Local single-user mode keeps the user's
        # freedom over their own projects.
        from .config import load_settings
        if load_settings().auth_mode == "accounts":
            resolved = Path(root).resolve()
            data_resolved = data_dir.resolve()
            if resolved != data_resolved \
                    and data_resolved not in resolved.parents:
                return {"status": "denied",
                        "reason": "in accounts mode, code tools may "
                                  "only index workspaces inside the "
                                  "platform data area"}
        try:
            graph, status = ensure_graph(data_dir, root)
        except Exception as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        if tool == "code.search":
            result = graph.search(str(args.get("query", "")))
        elif tool == "code.symbol_context":
            result = graph.symbol_context(str(args.get("symbol", "")))
        else:
            result = graph.impact(str(args.get("symbol", "")))
        return {**result, "graph_built": status.get("built", False),
                "graph_stale": status.get("stale", False),
                "graph_stats": status.get("stats", {})}

    def _execute_web_read(self, args: dict, mission: dict, tenant_id: str) -> dict[str, Any]:
        """Live web research (Phase 5) with honest fallbacks.

        Decision order: research mode off / no provider -> seed note.
        Classification gate: confidential/restricted missions keep
        queries on-platform (seed corpus) unless the contract explicitly
        sets tools.live_web=true; public/internal research live unless
        the contract sets live_web=false. Provider failure or an empty
        corpus -> fallback result (the caller surfaces the event).
        """
        seed_note = {"status": "ok", "mode": "seed",
                     "note": "served from the local source library", "url": ""}
        if self.research_mode != "live" or self.research_provider is None:
            return seed_note
        contract = mission["contract"]
        override = contract.get("tools", {}).get("live_web")
        classification = contract.get("data_classification", "internal")
        live_ok = override if override is not None else classification in ("public", "internal")
        if not live_ok:
            return {**seed_note, "note":
                    f"live web skipped: data classification '{classification}' keeps "
                    "research on the local corpus (set tools.live_web=true to override)"}
        mission_id = mission["id"]
        if mission_id in self._live_cache:
            return self._live_cache[mission_id]
        query = args["query"]
        queries = [query, f"{query} comparison analysis"]
        try:
            sources = self.research_provider.research(queries)
        except Exception as exc:  # provider/network down — never crash a mission
            result = {"status": "fallback", "mode": "seed",
                      "reason": f"live research unavailable: {type(exc).__name__}"}
            self._live_cache[mission_id] = result
            return result
        if not sources:
            result = {"status": "fallback", "mode": "seed",
                      "reason": "live research returned no usable sources"}
            self._live_cache[mission_id] = result
            return result
        for src in sources:
            self.store.put_source(tenant_id, src)
        result = {"status": "ok", "mode": "live", "queries": queries,
                  "sources": len(sources),
                  "source_ids": [s["source_id"] for s in sources]}
        self._live_cache[mission_id] = result
        return result

    @staticmethod
    def _execute_recorded(tool: str, args: dict) -> dict[str, Any]:
        """Non-web tools: the gateway authorises and AUDITS the call;
        the side effect itself belongs to the tool's own service (the
        platform's artifact store, a connected mail provider, …), so
        the recorded result says exactly that — nothing is simulated."""
        if tool == "web.read":
            return {"status": "ok", "note": "served from the seed source library",
                    "url": args.get("url", "")}
        if tool in ("artifact.write", "artifact.read"):
            return {"status": "ok", "note": "artifact store operation recorded by the gateway"}
        return {"status": "ok", "note": f"gateway-recorded invocation of {tool}"}
