"""W10 API surface — thin HTTP over the fifthdoc engines.

Follows the app/custom_agents.py pattern: build_router(store, guard,
tenant_of, corr_of, not_found_of). State is per-tenant, in memory; the
engines themselves are stateless except where noted. User-facing text
follows the simple-words law: no raw JSON, no bare status codes in messages.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import FEATURE_MAP
from . import api_lab, browser_surface, disagreement, env_inspector
from . import extensions as ext_mod
from . import hitl as hitl_mod
from . import inbox as inbox_mod
from . import inspector as inspector_mod
from . import model_lab, output_quality, personalization, policy_tests
from . import queue_center, release as release_mod, replay, supply_chain
from . import tiny, transform, undo, watchers, workflows

_STATE: dict[str, dict] = {}


def _state(tenant: str) -> dict:
    return _STATE.setdefault(tenant, {
        "timeline": undo.Timeline(),
        "inbox": inbox_mod.Inbox(),
        "hitl": hitl_mod.Hitl(),
        "inspector": inspector_mod.Inspector(),
        "queue": queue_center.QueueCenter(),
        "watcher": watchers.Watcher(),
        "prefs": personalization.Preferences(),
        "registry": ext_mod.ExtensionRegistry(),
        "files": {},
    })


def build_router(*, store, guard, tenant_of, corr_of, not_found_of) -> APIRouter:
    router = APIRouter()

    def err(request: Request, status: int, code: str, message: str) -> JSONResponse:
        from ..domain import error_envelope
        return JSONResponse(status_code=status,
                            content=error_envelope(code, message, corr_of(request)))

    async def body_checked(request: Request):
        """(data, err_response) for a POST body.

        BUG-8 fix: an empty body is still {} (callers such as
        watchers/poll legitimately post no body, and degenerate {}
        inputs get honest engine answers), but malformed JSON or a
        non-object body is a 400 — never a silent {} that lets a
        security check "pass" input it never parsed.
        """
        raw = await request.body()
        if not raw.strip():
            return {}, None
        try:
            data = json.loads(raw)
        except Exception:
            return None, err(request, 400, "VALIDATION_ERROR",
                             "Request body is not valid JSON. "
                             "Send a JSON object.")
        if not isinstance(data, dict):
            return None, err(request, 400, "VALIDATION_ERROR",
                             "Request body must be a JSON object.")
        return data, None

    # BUG-2 fix: missing required fields used to fall through to
    # data["field"] (KeyError → the app's global 404 handler) or to
    # engine ValueError/IndexError (500). Required input is a 400
    # with a plain-words message, never a 404 or a 500.
    def require(request: Request, data: dict, field: str):
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return err(request, 400, "VALIDATION_ERROR",
                       f"Missing required field: {field}.")
        return None

    @router.get("/v1/deep/map")
    async def feature_map(request: Request):
        return {"features": {k: {"subsystem": v[0], "system": v[1], "seam": v[2]}
                             for k, v in FEATURE_MAP.items()}}

    # §1 undo ------------------------------------------------------------
    @router.post("/v1/deep/undo/record")
    async def undo_record(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("entry_id", "kind", "target"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        entry = undo.TimelineEntry(entry_id=data["entry_id"], kind=data["kind"],
                                   target=data["target"],
                                   before=data.get("before"), after=data.get("after"))
        _state(tenant_of(request))["timeline"].record(entry)
        return {"ok": True, "entry_id": entry.entry_id, "reversible": entry.reversible}

    @router.post("/v1/deep/undo/preview")
    async def undo_preview(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "entry_id")
        if bad is not None:
            return bad
        state = _state(tenant_of(request))
        return state["timeline"].preview_rollback(data["entry_id"], state["files"])

    @router.post("/v1/deep/undo/rollback")
    async def undo_rollback(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "entry_id")
        if bad is not None:
            return bad
        state = _state(tenant_of(request))
        return state["timeline"].rollback(data["entry_id"], state["files"],
                                          force=bool(data.get("force")))

    @router.get("/v1/deep/undo/manifest")
    async def undo_manifest(request: Request):
        return _state(tenant_of(request))["timeline"].recovery_manifest()

    # §2 inbox -----------------------------------------------------------
    @router.post("/v1/deep/inbox", status_code=201)
    async def inbox_add(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("item_id", "kind"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        item = inbox_mod.InboxItem(item_id=data["item_id"], kind=data["kind"],
                                   title=data.get("title", ""),
                                   mission_id=data.get("mission_id"),
                                   priority=data.get("priority", "normal"))
        _state(tenant_of(request))["inbox"].add(item)
        return {"ok": True, "item_id": item.item_id}

    @router.get("/v1/deep/inbox")
    async def inbox_list(request: Request):
        inbox = _state(tenant_of(request))["inbox"]
        items = inbox.list(kind=request.query_params.get("kind"),
                           priority=request.query_params.get("priority"))
        return {"items": [{**vars(i)} for i in items],
                "outstanding": inbox.outstanding_count()}

    @router.post("/v1/deep/inbox/{item_id}/action")
    async def inbox_action(request: Request, item_id: str):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "action")
        if bad is not None:
            return bad
        return _state(tenant_of(request))["inbox"].apply(item_id, data["action"],
                                                        **{k: v for k, v in data.items()
                                                            if k != "action"})

    # §3 HITL ------------------------------------------------------------
    @router.post("/v1/deep/hitl/gates", status_code=201)
    async def hitl_gate(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("gate_id", "kind", "mission_id"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        gate = hitl_mod.Gate(gate_id=data["gate_id"], kind=data["kind"],
                             mission_id=data["mission_id"],
                             payload=data.get("payload", {}))
        _state(tenant_of(request))["hitl"].open_gate(gate)
        return {"ok": True, "gate_id": gate.gate_id}

    @router.post("/v1/deep/hitl/approve-plan")
    async def hitl_approve_plan(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "gate_id")
        if bad is not None:
            return bad
        return _state(tenant_of(request))["hitl"].approve_plan(data["gate_id"])

    @router.post("/v1/deep/hitl/approve")
    async def hitl_approve(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "gate_id")
        if bad is not None:
            return bad
        return _state(tenant_of(request))["hitl"].approve(
            data["gate_id"], edited_payload=data.get("payload"))

    @router.post("/v1/deep/hitl/reject")
    async def hitl_reject(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "gate_id")
        if bad is not None:
            return bad
        return _state(tenant_of(request))["hitl"].reject(
            data["gate_id"], data.get("feedback", ""))

    # §4 workflows --------------------------------------------------------
    def _workflow_nodes_ok(data: dict) -> str | None:
        """Plain-words complaint for a malformed node list, else None."""
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            return "nodes must be a list."
        for raw in nodes:
            if not isinstance(raw, dict) or not raw.get("node_id"):
                return "Each workflow node needs a node_id."
            if not raw.get("kind"):
                return "Each workflow node needs a kind."
            # RETEST-3 fix: an unknown kind used to pass this check
            # and then raise ValueError inside add_node (bare 500).
            if raw["kind"] not in workflows.NODE_KINDS:
                return (f"Unknown node kind: {raw['kind']}. Use one of: "
                        f"{', '.join(workflows.NODE_KINDS)}.")
        return None

    def _workflow(data: dict) -> workflows.Workflow:
        wf = workflows.Workflow(workflow_id=data.get("workflow_id", "wf"))
        for raw in data.get("nodes", []):
            wf.add_node(workflows.Node(
                node_id=raw["node_id"], kind=raw["kind"],
                label=raw.get("label", ""), inputs=raw.get("inputs", {}),
                outputs=raw.get("outputs", {}),
                on_failure=raw.get("on_failure", "stop"),
                permissions=raw.get("permissions", []),
                idempotent=raw.get("idempotent", False),
                config=raw.get("config", {}),
                next_nodes=raw.get("next_nodes", [])))
        return wf

    @router.post("/v1/deep/workflows/validate")
    async def wf_validate(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = _workflow_nodes_ok(data)
        if bad is not None:
            return err(request, 400, "VALIDATION_ERROR", bad)
        return _workflow(data).validate()

    @router.post("/v1/deep/workflows/dry-run")
    async def wf_dry_run(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = _workflow_nodes_ok(data)
        if bad is not None:
            return err(request, 400, "VALIDATION_ERROR", bad)
        result = _workflow(data).dry_run()
        if not result.get("ok"):
            # A graph that cannot run (no start node, a cycle, a missing
            # target) is a client error in plain words — never a 200
            # with a silent failure, and never a 500 from the engine.
            return err(request, 400, "VALIDATION_ERROR",
                       "; ".join(result.get("errors", ["This graph cannot run."])))
        return result

    # §5 transform --------------------------------------------------------
    @router.post("/v1/deep/transform/json-to-csv")
    async def tr_json_csv(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        text, problems = transform.json_to_csv(data.get("rows", []))
        return {"csv": text, "problems": problems}

    @router.post("/v1/deep/transform/csv-to-json")
    async def tr_csv_json(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        rows, problems = transform.csv_to_json(data.get("text", ""))
        return {"rows": rows, "problems": problems}

    @router.post("/v1/deep/transform/preview")
    async def tr_preview(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return transform.preview(data.get("rows", []))

    # §6 browser ----------------------------------------------------------
    @router.post("/v1/deep/browser/barrier")
    async def browser_barrier(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return browser_surface.detect_access_barrier(data.get("page_text", ""),
                                                     data.get("status"))

    @router.post("/v1/deep/browser/redact")
    async def browser_redact(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return {"redacted": browser_surface.redact_record(data.get("record", {}))}

    # §7 api lab ----------------------------------------------------------
    @router.post("/v1/deep/api-lab/validate-schema")
    async def api_validate(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return api_lab.validate_json_schema(data.get("body", "{}"),
                                            data.get("required", []))

    @router.post("/v1/deep/api-lab/codegen")
    async def api_codegen(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        req = api_lab.ApiRequest(method=data.get("method", "GET"),
                                 url=data.get("url", ""))
        return {"code": api_lab.client_codegen(req, data.get("language", "python"))}

    @router.post("/v1/deep/api-lab/send-guard")
    async def api_send_guard(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        req = api_lab.ApiRequest(method=data.get("method", "GET"),
                                 url=data.get("url", ""))
        return api_lab.send(req, permission_granted=bool(data.get("permission")),
                            transport=lambda r: {"status": 200, "body": "{}",
                                                 "mock": True})

    # §8 inspector --------------------------------------------------------
    @router.post("/v1/deep/inspector/record", status_code=201)
    async def insp_record(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "event_id")
        if bad is not None:
            return bad
        if data.get("stage") not in inspector_mod.STAGES:
            return err(request, 400, "VALIDATION_ERROR",
                       f"Unknown stage: {data.get('stage')}. "
                       f"Use one of: {', '.join(inspector_mod.STAGES)}.")
        if data.get("severity", "info") not in inspector_mod.SEVERITIES:
            return err(request, 400, "VALIDATION_ERROR",
                       f"Unknown severity: {data.get('severity')}. "
                       f"Use one of: {', '.join(inspector_mod.SEVERITIES)}.")
        event = inspector_mod.InspectorEvent(
            event_id=data["event_id"], stage=data["stage"],
            operation=data.get("operation", ""), task_id=data.get("task_id", ""),
            agent_id=data.get("agent_id", ""),
            artifact_id=data.get("artifact_id"),
            severity=data.get("severity", "info"),
            detail=data.get("detail", ""))
        _state(tenant_of(request))["inspector"].record(event)
        return {"ok": True, "event_id": event.event_id}

    @router.get("/v1/deep/inspector/summary")
    async def insp_summary(request: Request):
        return _state(tenant_of(request))["inspector"].summary()

    # §9 model lab --------------------------------------------------------
    @router.post("/v1/deep/model-lab/context-check")
    async def model_context(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("model_max_tokens", "prompt_tokens"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        # BUG-3 fix: the unguarded int() cast turned non-numeric input
        # into a bare 500. Token counts must be numbers: 400 in
        # plain words, never a 500.
        try:
            max_tokens = int(data["model_max_tokens"])
            prompt_tokens = int(data["prompt_tokens"])
        except (TypeError, ValueError):
            return err(request, 400, "VALIDATION_ERROR",
                       "Give token counts as numbers.")
        lab = model_lab.ModelLab([])
        return lab.context_limit_check(max_tokens, prompt_tokens)

    # §10 policy ----------------------------------------------------------
    @router.post("/v1/deep/policy/check-secret")
    async def policy_secret(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        leaked = policy_tests.PolicySuite.check_secret_leakage(
            data.get("output", ""), data.get("secrets", []))
        return {"leaked": leaked, "passed": not leaked}

    # §11 environment -----------------------------------------------------
    @router.post("/v1/deep/env/inspect")
    async def env_inspect(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return env_inspector.inspect(data.get("root", "."),
                                     data.get("required_env_vars", []))

    # §12 output quality --------------------------------------------------
    @router.post("/v1/deep/quality/markdown")
    async def quality_md(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return output_quality.check_markdown(data.get("text", ""))

    @router.post("/v1/deep/quality/manifest")
    async def quality_manifest(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return output_quality.deliverable_manifest(
            data.get("requested", []), data.get("present", {}),
            data.get("verified", {}))

    # §13 watchers --------------------------------------------------------
    @router.post("/v1/deep/watchers/rules", status_code=201)
    async def watcher_rule(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("rule_id", "directory"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        rule = watchers.WatchRule(rule_id=data["rule_id"],
                                  directory=data["directory"])
        _state(tenant_of(request))["watcher"].add_rule(rule)
        return {"ok": True, "rule_id": rule.rule_id}

    @router.post("/v1/deep/watchers/poll")
    async def watcher_poll(request: Request):
        return {"fired": _state(tenant_of(request))["watcher"].poll()}

    # §14 queue -----------------------------------------------------------
    @router.post("/v1/deep/queue/submit")
    async def queue_submit(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        bad = require(request, data, "job_id")
        if bad is not None:
            return bad
        job = queue_center.QueueJob(job_id=data["job_id"],
                                    priority=int(data.get("priority", 5)),
                                    lane=data.get("lane", "background"))
        return _state(tenant_of(request))["queue"].submit(job)

    @router.get("/v1/deep/queue/metrics")
    async def queue_metrics(request: Request):
        return _state(tenant_of(request))["queue"].metrics()

    # §15 disagreement ----------------------------------------------------
    @router.post("/v1/deep/disagreement/resolve")
    async def disagreement_resolve(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        raw_positions = data.get("positions", [])
        if not isinstance(raw_positions, list) or not raw_positions:
            return err(request, 400, "VALIDATION_ERROR",
                       "Give at least one position to resolve.")
        for p in raw_positions:
            if not isinstance(p, dict) or not p.get("agent_id") \
                    or not p.get("answer"):
                return err(request, 400, "VALIDATION_ERROR",
                           "Each position needs an agent_id and an answer.")
        positions = [disagreement.Position(agent_id=p["agent_id"],
                                           answer=p["answer"],
                                           evidence=p.get("evidence", []),
                                           assumptions=p.get("assumptions", []))
                     for p in raw_positions]
        result = disagreement.resolve(positions,
                                      independent_check=data.get("independent_check"),
                                      tie_breaker=data.get("tie_breaker"))
        return {"status": result.status, "chosen": result.chosen,
                "summary": result.summary,
                "minority_report": result.minority_report,
                "method": result.method}

    # §16 replay ----------------------------------------------------------
    @router.post("/v1/deep/replay/plan")
    async def replay_plan(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        trace = replay.Trace(trace_id=data.get("trace_id", "t"),
                             performed_external_actions=bool(
                                 data.get("performed_external_actions")))
        return replay.replay_plan(trace)

    # §17 supply chain ----------------------------------------------------
    @router.get("/v1/deep/supply-chain/vulnerability-scan")
    async def supply_vuln(request: Request):
        return supply_chain.vulnerability_scan()

    @router.post("/v1/deep/supply-chain/report")
    async def supply_report(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        deps = [supply_chain.Dependency(name=d["name"],
                                        version=d.get("version"),
                                        licence=d.get("licence"),
                                        source=d.get("source", ""))
                for d in data.get("dependencies", [])]
        return supply_chain.compliance_report(deps, data.get("project", ""))

    # §18 release ---------------------------------------------------------
    @router.post("/v1/deep/release/checklist")
    async def release_checklist(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        rel = release_mod.Release(release_id=data.get("release_id", "r"),
                                  version=data.get("version", "0.1.0"),
                                  changes=data.get("changes", []),
                                  checks=data.get("checks", {}))
        return release_mod.checklist_status(rel)

    @router.post("/v1/deep/release/deploy-guard")
    async def release_deploy_guard(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        rel = release_mod.Release(release_id="r", version="0.1.0")
        return release_mod.deploy(rel, permission_granted=bool(
            data.get("permission")))

    # §19 personalization -------------------------------------------------
    @router.post("/v1/deep/preferences/set")
    async def prefs_set(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return _state(tenant_of(request))["prefs"].set(
            data["layer"], data["key"], data["value"])

    @router.get("/v1/deep/preferences/effective")
    async def prefs_effective(request: Request):
        return _state(tenant_of(request))["prefs"].effective()

    # §20 extensions ------------------------------------------------------
    @router.post("/v1/deep/extensions/validate")
    async def ext_validate(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return ext_mod.validate_manifest(data)

    @router.post("/v1/deep/extensions/install")
    async def ext_install(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        for field in ("name", "version"):
            bad = require(request, data, field)
            if bad is not None:
                return bad
        ext = ext_mod.Extension(name=data["name"], version=data["version"],
                                entrypoint=data.get("entrypoint", "index.js"),
                                permissions=data.get("permissions", []),
                                api_version=data.get("api_version", "1"))
        return _state(tenant_of(request))["registry"].install(ext)

    # §21 tiny ------------------------------------------------------------
    @router.post("/v1/deep/tiny/sanitize")
    async def tiny_sanitize(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return {"sanitized": tiny.sanitize_filename(data.get("name", ""))}

    @router.post("/v1/deep/tiny/shell-command")
    async def tiny_shell(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return {"command": tiny.copy_as_shell_command(data.get("argv", []),
                                                      data.get("shell", "posix"))}

    @router.post("/v1/deep/tiny/duplicate-check")
    async def tiny_dup(request: Request):
        data, _bad = await body_checked(request)
        if _bad is not None:
            return _bad
        return tiny.duplicate_mission_check(data.get("title", ""),
                                            data.get("existing", []))

    return router
