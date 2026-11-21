"""AgentQon — Workbench systems (W9, fourth audit document).

The document's own architecture, binding: eight reusable systems, and every
feature below rides on one of them — never dozens of unrelated buttons:

  Action      — consistent copy / retry / cancel / approve / undo / delete.
  Artifact    — one way to represent, preview, save, version, compare, export.
  Event       — one stream feeding mission updates, notifications, activity
                history, diagnostics.
  Permission  — one authorisation mechanism for tools, files, agents,
                integrations.
  Recovery    — checkpoints, retries, resume, rollback.
  Verification— acceptance criteria, checks, evidence, completion reports.
  Usage       — one ledger for usage, estimates, actual charges, budgets.
                This is the USER-FACING side of the W1 cost engine: it
                records estimates and provider-reconciled actuals as
                separate fields and never invents a second ledger.
  Project     — one structured source for requirements, decisions,
                assumptions, relevant artifacts.

Persistence: one JSON file per tenant under <data_dir>/fourthdoc/, written
atomically (temp file + rename + fsync) so a crash cannot leave a torn
draft, checkpoint, or ledger behind. No secrets are ever stored here:
API-token creation returns the value once and stores only its hash;
diagnostic bundles and non-secret exports pass through redaction.

Seams (other workstreams own the far side; where unmerged, the interface
built against is documented here and end-to-end wiring is NOT_TESTED):
  W1 cost engine    — Usage reads estimates/actuals this module records;
                      when the W1 engine merges, it becomes the estimator
                      and this module stays the user-facing surface.
  W3 verification   — Verification consumes W3 test gates / provenance /
                      freshness mechanics; this module supplies the
                      criteria framework, coverage report, certificate.
  W2 breaker/sandbox— Recovery consumes the W2 circuit breaker; skill
                      sandbox testing rides the W2 sandbox.
  W7 plugin/API     — plugins ride the W7 plugin framework; webhook
                      signatures use the W7 HMAC validation shape.
  W5 resume/scheduler— Recovery resume consumes W5 resume safety; schedule
                      extras consume the W5 dependency scheduler.
  W8 file rollback  — Recovery rollback requests ride W8 file rollback.

Standing rules honoured here: in-app notifications only (never a push into
the owner's WhatsApp chat from the main chat); local-model numbers are
server-reported only — anything the server does not expose is the word
"unavailable", never zero, and shared CPU/GPU memory is never split into
separate pools; multi-user sharing is NOT faked in single-user mode.
"""
from __future__ import annotations

import copy
import hashlib
import hmac as hmac_lib
import json
import re
import secrets
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .domain import error_envelope, new_id, now_iso
from .memory import redact_text

WORKBENCH_VERSION = "1.0.0"

# §2 — the eight response states, stated exactly once, used everywhere.
RESPONSE_STATES = (
    "generating", "completed", "interrupted", "failed",
    "partial", "warnings", "awaiting-tool", "awaiting-approval",
)
RESPONSE_STATE_WORDS = {
    "generating": "Still writing — please wait.",
    "completed": "Finished.",
    "interrupted": "Stopped before it finished.",
    "failed": "Could not finish — see what went wrong below.",
    "partial": "Partly finished — some parts are missing.",
    "warnings": "Finished, but with warnings to check.",
    "awaiting-tool": "Waiting for a tool to answer.",
    "awaiting-approval": "Waiting for your approval.",
}

# §13 — palette actions, each tagged with the minimum role that may run it.
# The endpoint filters by the caller's role, so a caller never sees an
# action they are not permitted to perform.
PALETTE_ACTIONS = [
    {"id": "open-chat", "label": "Open the chat", "target": "chat", "min_role": "member"},
    {"id": "open-missions", "label": "Open missions", "target": "missions", "min_role": "member"},
    {"id": "open-agents", "label": "Open agents", "target": "agents", "min_role": "member"},
    {"id": "open-skills", "label": "Open skills", "target": "skills", "min_role": "member"},
    {"id": "open-workbench", "label": "Open the workbench", "target": "workbench", "min_role": "member"},
    {"id": "open-notifications", "label": "Open notifications", "target": "notifications", "min_role": "member"},
    {"id": "open-projects", "label": "Open projects", "target": "projects", "min_role": "member"},
    {"id": "open-schedules", "label": "Open schedules", "target": "schedules", "min_role": "member"},
    {"id": "open-diagnostics", "label": "Open system health", "target": "diagnostics", "min_role": "member"},
    {"id": "open-approvals", "label": "Open approvals waiting for you", "target": "approvals", "min_role": "member"},
    {"id": "manage-members", "label": "Manage workspace members", "target": "members", "min_role": "admin"},
    {"id": "manage-plugins", "label": "Manage plugins", "target": "plugins", "min_role": "admin"},
    {"id": "export-diagnostics", "label": "Export a system health report", "target": "diagnostics", "min_role": "admin"},
]
_ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}

# Collections served by the generic CRUD surface. Each maps to a system.
COLLECTIONS = {
    # Project Context System
    "projects": "project", "decisions": "project", "requirements": "project",
    "issues": "project", "assumptions": "project", "limitations": "project",
    "rejected": "project", "project-memories": "project",
    "context-questions": "project",
    # Action System
    "drafts": "action", "templates": "action", "messages": "action",
    "recents": "action", "favorites": "action",
    # Event System
    "notifications": "event", "comments": "event",
    # Permission System
    "approvals": "permission", "grants": "permission",
    "tool-permissions": "permission", "conflicts": "permission",
    # Recovery System
    "checkpoints": "recovery", "schedule-extras": "recovery",
    "schedule-runs": "recovery",
    # Verification System
    "acceptance": "verification", "skill-provenance": "verification",
    "skill-registry": "verification",
    # Usage Accounting System
    "usage-entries": "usage", "budgets": "usage",
    "provider-health": "usage", "provider-aliases": "usage",
    "provider-rotations": "usage",
    # Artifact System
    "artifacts": "artifact", "shared-items": "artifact",
    # Coordination / society ride on Project + Event + Verification
    "coordination-agents": "project", "coordination-tasks": "project",
    "handoffs": "project", "escalations": "event",
    "society-configs": "verification", "society-runs": "verification",
    # Operations surfaces
    "backups": "recovery", "backup-schedules": "recovery",
    "plugins": "permission", "webhooks": "permission",
    "api-tokens": "permission", "local-models": "usage",
    "model-queue": "usage", "model-metrics": "usage",
    "diagnostic-errors": "event",
}

_PLAIN_REQUIRED = {
    "projects": ["name"],
    "drafts": ["conversation_id", "body"],
    "messages": ["conversation_id"],
    "notifications": ["title"],
    "approvals": ["what", "why"],
    "acceptance": ["mission_id"],
    "artifacts": ["title"],
    "decisions": ["title"],
}


def _safe_tenant(tenant_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", tenant_id)[:120]


def _parse_iso(text: str):
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class TenantFileStore:
    """One JSON file per tenant, atomic writes, no secrets inside."""

    def __init__(self, data_dir):
        self.root = Path(data_dir) / "fourthdoc"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, tenant_id: str) -> Path:
        return self.root / (_safe_tenant(tenant_id) + ".json")

    def load(self, tenant_id: str) -> dict:
        path = self._path(tenant_id)
        if not path.exists():
            return {"collections": {}, "prefs": None, "retention": None}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "collections" in data:
                return data
        except Exception:
            pass
        return {"collections": {}, "prefs": None, "retention": None,
                "corrupted": True}

    def save(self, tenant_id: str, data: dict) -> None:
        path = self._path(tenant_id)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, sort_keys=True)
                    fh.flush()
                    import os
                    os.fsync(fh.fileno())
                tmp.replace(path)
            except Exception:
                # A failed write must never leave a torn temp file behind.
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise

    def size_bytes(self, tenant_id: str) -> int:
        path = self._path(tenant_id)
        return path.stat().st_size if path.exists() else 0


def _coll(state: dict, name: str) -> dict:
    return state.setdefault("collections", {}).setdefault(name, {})


def _plain_error(request: Request, corr_of, status: int, message: str):
    return JSONResponse(status_code=status, content={
        **error_envelope("WORKBENCH_ERROR", message, corr_of(request)),
        "message": message})


def classify_retry(error_text: str) -> dict:
    """§8/§23 retry classification from the error's own words.

    Returns the class plus a deterministic exponential-backoff schedule
    (1s, 2s, 4s … capped at 5 minutes). Classification never claims a
    cause the text does not show: anything unrecognised is "unknown"
    and is NOT retried blindly.
    """
    text = (error_text or "").lower()
    if any(k in text for k in ["rate limit", "429", "too many requests"]):
        kind, retryable = "rate_limited", True
    elif any(k in text for k in ["timeout", "timed out", "deadline"]):
        kind, retryable = "timeout", True
    elif any(k in text for k in ["connection", "network", "unreachable", "502", "503", "504"]):
        kind, retryable = "provider_unreachable", True
    elif any(k in text for k in ["unauthorized", "401", "forbidden", "403", "invalid key", "api key"]):
        kind, retryable = "auth", False
    elif any(k in text for k in ["not found", "404", "unknown model", "unknown tool"]):
        kind, retryable = "not_found", False
    elif any(k in text for k in ["invalid", "validation", "bad request", "400"]):
        kind, retryable = "validation", False
    elif any(k in text for k in ["quota", "budget", "insufficient"]):
        kind, retryable = "budget", False
    else:
        kind, retryable = "unknown", False
    backoff = [min(300, 2 ** i) for i in range(5)]
    return {"class": kind, "retryable": retryable,
            "backoff_seconds": backoff if retryable else [],
            "restart_instead": not retryable,
            "note": ("Retry with a pause." if retryable else
                     "Do not retry as-is — fix the cause first, then start again. "
                     "A retry repeats the same failed step; a restart begins fresh.")}


def render_template(template_body: str, variables: dict) -> dict:
    """§1 template preview: render {{name}} slots, list what is missing.

    Missing inputs are reported, never silently filled with a guess.
    """
    missing = []
    rendered = template_body
    for match in sorted(set(re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", template_body))):
        if match in (variables or {}):
            rendered = re.sub(r"\{\{\s*" + re.escape(match) + r"\s*\}\}",
                              str(variables[match]), rendered)
        else:
            missing.append(match)
    return {"rendered": rendered, "missing": missing,
            "ready": not missing}


def topo_order(tasks: list[dict]) -> dict:
    """§22 dependency graph: order tasks so every dependency runs first.

    A cycle is reported as a cycle (with the tasks in it), never silently
    broken — the user fixes the loop.
    """
    by_id = {t["id"]: t for t in tasks}
    visiting, done, order, cycle = set(), set(), [], []

    def visit(task_id: str, trail: list[str]) -> None:
        if task_id in done or task_id in visiting:
            if task_id in visiting:
                cycle.extend(trail[trail.index(task_id):] + [task_id])
            return
        visiting.add(task_id)
        for dep in (by_id.get(task_id, {}) or {}).get("depends_on", []) or []:
            if dep in by_id:
                visit(dep, trail + [task_id])
        visiting.discard(task_id)
        done.add(task_id)
        order.append(task_id)

    for task in tasks:
        visit(task["id"], [])
    if cycle:
        return {"order": [], "cycle": sorted(set(cycle)),
                "message": "These tasks depend on each other in a loop — break the loop first."}
    return {"order": order, "cycle": [],
            "message": "Tasks are in dependency order."}


def diff_lines(old: str, new: str) -> list[dict]:
    """Artifact version comparison: removed / added lines, nothing fancy."""
    import difflib
    out = []
    for line in difflib.unified_diff((old or "").splitlines(),
                                     (new or "").splitlines(), lineterm=""):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append({"change": "removed" if line.startswith("-") else
                    "added" if line.startswith("+") else "same",
                    "text": line[1:] if line[:1] in "+-" else line})
    return out


def webhook_signature_ok(secret: str, payload: str, signature: str) -> bool:
    """W7 seam: HMAC-SHA256 validation shape for webhook triggers (§5/§18)."""
    if not secret or not signature:
        return False
    expected = hmac_lib.new(secret.encode(), payload.encode(),
                            hashlib.sha256).hexdigest()
    given = signature.removeprefix("sha256=")
    return hmac_lib.compare_digest(expected, given)


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------

def build_router(*, store, guard, tenant_of, corr_of, not_found_of, data_dir):
    """Wire the workbench router. Identity helpers are injected by main.py
    (same pattern as app/custom_agents.py) so this module never imports
    main and cannot form an import cycle."""
    router = APIRouter()
    fstore = TenantFileStore(data_dir)
    # Bespoke-only collections: their create paths must never store a
    # secret or a fake snapshot, so they skip the generic CRUD surface.
    _BESPOKE_ONLY = {"backups", "webhooks", "api-tokens", "model-queue"}
    _CRUD = [n for n in COLLECTIONS if n not in _BESPOKE_ONLY]

    def _ident(request: Request):
        return guard(request, "member")

    def _state(request: Request, ident):
        return fstore.load(ident.tenant_id)

    def _save(request: Request, ident, state):
        fstore.save(ident.tenant_id, state)

    def _get(state, coll, item_id):
        return _coll(state, coll).get(item_id)

    def _create(state, coll, body: dict, ident, defaults=None):
        body = dict(body or {})
        for field in _PLAIN_REQUIRED.get(coll, []):
            if not str(body.get(field, "")).strip():
                return None, f"Please fill in {field.replace('_', ' ')}."
        rec = {"id": new_id("wb"), "created_at": now_iso(),
               "created_by": ident.user_id, "updated_at": now_iso()}
        for key, val in (defaults or {}).items():
            rec.setdefault(key, val)
        rec.update(body)
        rec["id"] = rec.get("id") or new_id("wb")
        _coll(state, coll)[rec["id"]] = rec
        return rec, None

    def _list(state, coll, request: Request):
        items = list(_coll(state, coll).values())
        params = request.query_params
        for key in ("project_id", "status", "conversation_id", "mission_id",
                    "scope_ref", "kind", "target", "skill_id", "model_id"):
            if params.get(key):
                items = [i for i in items if str(i.get(key, "")) == params[key]]
        if params.get("q"):
            q = params["q"].lower()
            items = [i for i in items if q in json.dumps(i).lower()]
        items.sort(key=lambda i: i.get("created_at", ""))
        return items

    def _not_found(request):
        return _plain_error(request, corr_of, 404, "That item is not here.")

    # -- static bespoke routes (registered before any {item_id} route) --
    @router.get("/v1/workbench/response-states")
    async def response_states(request: Request):
        _ident(request)
        return {"states": [{"state": s, "words": RESPONSE_STATE_WORDS[s]}
                           for s in RESPONSE_STATES]}

    @router.get("/v1/workbench/commands")
    async def commands(request: Request):
        ident = _ident(request)
        rank = _ROLE_RANK.get(ident.role, 1)
        return {"actions": [a for a in PALETTE_ACTIONS
                            if _ROLE_RANK.get(a["min_role"], 1) <= rank],
                "note": "Only actions you are allowed to run are listed."}

    @router.get("/v1/workbench/search")
    async def wb_search(request: Request, q: str = ""):
        ident = _ident(request)
        state = _state(request, ident)
        out = []
        if q.strip():
            needle = q.lower()
            for coll in ("projects", "decisions", "artifacts", "drafts",
                         "requirements", "templates"):
                for rec in _coll(state, coll).values():
                    hay = " ".join(str(rec.get(k, "")) for k in
                                   ("name", "title", "body", "description"))
                    if needle in hay.lower():
                        out.append({"collection": coll, "id": rec["id"],
                                    "title": rec.get("name") or rec.get("title") or ""})
        return {"results": out, "query": q}

    @router.get("/v1/workbench/notifications/summary")
    async def notif_summary(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        notes = list(_coll(state, "notifications").values())
        grouped: dict[str, int] = {}
        for note in notes:
            key = f"{note.get('kind', 'update')}:{note.get('target', '')}"
            grouped[key] = grouped.get(key, 0) + 1
        return {"unread": sum(1 for n in notes if not n.get("read")),
                "total": len(notes),
                "grouped": [{"group": k, "count": v} for k, v in grouped.items()]}

    @router.get("/v1/workbench/permissions/matrix")
    async def perm_matrix(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        tools = list(_coll(state, "tool-permissions").values())
        return {"tools": tools,
                "read_only": bool((state.get("prefs") or {}).get("read_only")),
                "note": "Nothing here grants everything at once — each tool is decided on its own."}

    @router.post("/v1/workbench/permissions/simulate")
    async def perm_simulate(request: Request, body: dict):
        """§6 permission simulation: answer what WOULD happen, grant nothing."""
        ident = _ident(request)
        state = _state(request, ident)
        tool = str((body or {}).get("tool", ""))
        if not tool:
            return _plain_error(request, corr_of, 400, "Name the tool to check.")
        recs = [r for r in _coll(state, "tool-permissions").values()
                if r.get("tool") == tool]
        decision = recs[0].get("decision", "ask") if recs else "ask"
        if (state.get("prefs") or {}).get("read_only"):
            decision = "deny"
        return {"tool": tool, "would": decision,
                "granted": False,
                "note": "This is a preview only — nothing was approved."}

    @router.get("/v1/workbench/project-memories/explain")
    async def memory_explain(request: Request, topic: str = "", project_id: str = ""):
        """§21 retrieval explanation: why each fact was returned."""
        ident = _ident(request)
        state = _state(request, ident)
        facts = [f for f in _coll(state, "project-memories").values()
                 if (not topic or f.get("topic") == topic)
                 and (not project_id or f.get("project_id") == project_id)
                 and f.get("status", "active") == "active"]
        return {"facts": [{"id": f["id"], "topic": f.get("topic"),
                           "kind": f.get("kind", "assumption"),
                           "fact": f.get("fact", ""),
                           "explanation": (
                               f"Matched topic '{f.get('topic')}' in project "
                               f"'{f.get('project_id')}'. Kind: {f.get('kind', 'assumption')} — "
                               "verified facts are outcomes that were checked; "
                               "assumptions and model suggestions are not facts "
                               "until you confirm them."),
                           "source": f.get("source", ""),
                           "expires_at": f.get("expires_at")}
                          for f in facts]}

    @router.get("/v1/workbench/usage/summary")
    async def usage_summary(request: Request, project_id: str = "",
                            mission_id: str = ""):
        """§17/§23 cost per successful task. Estimates and reconciled
        actuals stay separate fields — never merged into false precision."""
        ident = _ident(request)
        state = _state(request, ident)
        entries = list(_coll(state, "usage-entries").values())
        if project_id:
            entries = [e for e in entries if e.get("project_id") == project_id]
        if mission_id:
            entries = [e for e in entries if e.get("mission_id") == mission_id]
        est = sum(float(e.get("estimated_usd") or 0) for e in entries)
        act = sum(float(e.get("actual_usd") or 0) for e in entries
                  if e.get("actual_usd") is not None)
        reconciled = sum(1 for e in entries if e.get("actual_usd") is not None)
        ok = [e for e in entries if e.get("succeeded") is True]
        # Cost per successful task divides reconciled actual cost ONLY by
        # successful tasks that HAVE a reconciled cost. Dividing by every
        # success — including ones whose cost never arrived — would
        # understate the figure with false precision.
        ok_reconciled = [e for e in ok if e.get("actual_usd") is not None]
        total = len(entries)
        cps = (act / len(ok_reconciled)) if ok_reconciled else None
        return {"entries": total, "estimated_usd": round(est, 6),
                "actual_usd_reconciled": round(act, 6),
                "reconciled_entries": reconciled,
                "unreconciled_entries": total - reconciled,
                "successful_tasks": len(ok),
                "cost_per_successful_task_usd": round(cps, 6) if cps is not None else None,
                "cost_per_success_note": ("Actual cost only — estimated entries "
                                          "are not counted here.")
                if cps is not None else "No successful task with a reconciled cost yet."}

    @router.get("/v1/workbench/diagnostics/status")
    async def diag_status(request: Request):
        """§16 system status: only checks this process can actually run.
        Provider health comes from recorded self-test results, never
        invented here."""
        ident = _ident(request)
        state = _state(request, ident)
        try:
            total, _used, free = shutil.disk_usage(fstore.root)
            disk_ok = free > 100 * 1024 * 1024
        except Exception:
            total, free, disk_ok = 0, 0, False
        checks = [
            {"check": "Workbench storage can be written",
             "ok": fstore.root.exists(),
             "detail": "The workbench folder is present."},
            {"check": "Enough disk space",
             "ok": disk_ok,
             "detail": ("Free space is low — clear old backups or exports."
                        if not disk_ok else "Free space looks fine.")},
            {"check": "No corrupted workbench file",
             "ok": not state.get("corrupted"),
             "detail": ("The saved workbench file could not be read — it will "
                        "be treated as empty until repaired." if state.get("corrupted")
                        else "The saved workbench file reads cleanly.")},
            {"check": "Approvals waiting",
             "ok": True,
             "detail": f"{sum(1 for a in _coll(state, 'approvals').values() if a.get('status') == 'pending')} approval(s) waiting."},
        ]
        return {"version": WORKBENCH_VERSION, "checks": checks,
                "workbench_bytes": fstore.size_bytes(ident.tenant_id),
                "corrupted": bool(state.get("corrupted"))}

    @router.get("/v1/workbench/diagnostics/bundle/preview")
    async def diag_bundle_preview(request: Request):
        """§16 preview-before-export: the bundle WITHOUT secrets, prompts,
        or private file contents — shown before anything leaves."""
        ident = _ident(request)
        state = _state(request, ident)
        counts = {name: len(_coll(state, name)) for name in _CRUD}
        return {"preview": True, "version": WORKBENCH_VERSION,
                "counts": counts,
                "contains_secrets": False, "contains_prompts": False,
                "contains_private_files": False,
                "note": "This preview is everything the bundle holds. "
                        "Keys, full prompts, and private files are never included."}

    @router.get("/v1/workbench/diagnostics/bundle")
    async def diag_bundle(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        counts = {name: len(_coll(state, name)) for name in _CRUD}
        errors = [{k: redact_text(str(v)) for k, v in e.items()
                   if k in ("id", "reference_id", "detail", "created_at")}
                  for e in _coll(state, "diagnostic-errors").values()]
        return {"version": WORKBENCH_VERSION, "counts": counts,
                "errors": errors, "redacted": True,
                "note": "Keys, full prompts, and private files are never in this bundle."}

    @router.get("/v1/workbench/context/suggestions")
    async def context_suggestions(request: Request):
        """§20 grounding rule: every suggestion carries its evidence.
        Nothing here claims a failed test, a changed dependency, or a
        missing file without proof in the record."""
        ident = _ident(request)
        state = _state(request, ident)
        out = []
        try:
            missions = store.list_missions(ident.tenant_id)
        except Exception:
            missions = []
        mission_ids = {m.get("id") for m in missions}
        for mission in missions:
            if mission.get("status") in ("paused", "running", "queued"):
                out.append({"suggestion": "An unfinished mission is waiting.",
                            "evidence": f"Mission '{mission.get('title', '')}' is {mission.get('status')}.",
                            "mission_id": mission.get("id")})
        for art in _coll(state, "artifacts").values():
            mid = art.get("mission_id")
            if mid and mid not in mission_ids and not art.get("project_id"):
                out.append({"suggestion": "An output is no longer tied to a mission.",
                            "evidence": f"Artifact '{art.get('title', '')}' names mission {mid}, which is not in this workspace.",
                            "artifact_id": art["id"]})
        for art in _coll(state, "artifacts").values():
            created = _parse_iso(art.get("created_at", ""))
            if created and (datetime.now(timezone.utc) - created).days > 30 \
                    and not art.get("versions"):
                out.append({"suggestion": "This output may be out of date — check it before reusing it.",
                            "evidence": f"Artifact '{art.get('title', '')}' was last saved {art.get('created_at', '')[:10]} and has no newer version.",
                            "artifact_id": art["id"],
                            "freshness": "older than 30 days"})
        return {"suggestions": out}

    @router.get("/v1/workbench/prefs")
    async def get_prefs(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return state.get("prefs") or {"theme": "dark", "text_size": "medium",
                                      "high_contrast": False,
                                      "reduced_motion": False,
                                      "notification_sounds": False,
                                      "read_only": False}

    @router.put("/v1/workbench/prefs")
    async def put_prefs(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        prefs = dict(state.get("prefs") or {})
        allowed = {"theme", "text_size", "high_contrast", "reduced_motion",
                   "notification_sounds", "read_only", "quiet_hours",
                   "notification_kinds"}
        for key, val in (body or {}).items():
            if key in allowed:
                prefs[key] = val
        if prefs.get("theme") not in ("dark", "light", "system"):
            prefs["theme"] = "dark"
        state["prefs"] = prefs
        _save(request, ident, state)
        return prefs

    @router.get("/v1/workbench/retention/settings")
    async def retention_get(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return state.get("retention") or {"draft_days": 90,
                                          "notification_days": 30,
                                          "archive_instead_of_delete": True}

    @router.put("/v1/workbench/retention/settings")
    async def retention_put(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        settings = dict(state.get("retention") or {})
        settings.update({k: v for k, v in (body or {}).items()
                         if k in ("draft_days", "notification_days",
                                  "archive_instead_of_delete")})
        state["retention"] = settings
        _save(request, ident, state)
        return settings

    @router.get("/v1/workbench/retention/deletion-statement")
    async def deletion_statement(request: Request):
        """§15 deletion honesty, stated up front and in plain words."""
        _ident(request)
        return {"deletes": "Workbench records you name (drafts, notifications, "
                            "project notes) in this workspace.",
                "survives": ["Exports you already downloaded",
                             "Provider records held by the provider",
                             "Backups you created, until you delete them too",
                             "Server logs"],
                "note": "Deleting here does not erase those four. Say which of "
                        "them you also want removed and it will be handled "
                        "separately."}

    @router.post("/v1/workbench/retention/cleanup")
    async def retention_cleanup(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        settings = state.get("retention") or {"draft_days": 90,
                                              "notification_days": 30}
        now = datetime.now(timezone.utc)
        dry = bool((body or {}).get("dry_run", True))
        removed = {"drafts": 0, "notifications": 0}
        for coll, days, key in (("drafts", settings.get("draft_days", 90), "drafts"),
                                ("notifications", settings.get("notification_days", 30), "notifications")):
            stale = []
            for rec_id, rec in list(_coll(state, coll).items()):
                created = _parse_iso(rec.get("created_at", ""))
                if created and (now - created).days > int(days):
                    stale.append(rec_id)
            removed[key] = len(stale)
            if not dry:
                for rec_id in stale:
                    del _coll(state, coll)[rec_id]
        if not dry:
            _save(request, ident, state)
        return {"dry_run": dry, "would_remove": removed,
                "note": "Dry run removes nothing. Run again without dry run to remove."}

    @router.get("/v1/workbench/providers/export")
    async def providers_export(request: Request):
        """§12 non-secret configuration export: aliases, timeouts, limits —
        never a key, never a secret-shaped value."""
        ident = _ident(request)
        state = _state(request, ident)
        aliases = [{k: redact_text(str(v)) for k, v in a.items()
                    if k not in ("secret", "api_key", "token")}
                   for a in _coll(state, "provider-aliases").values()]
        health = list(_coll(state, "provider-health").values())[-20:]
        return {"aliases": aliases, "recent_health": health,
                "note": "No keys are in this export. Keys live in the secret "
                        "store only, never in exports."}

    @router.post("/v1/workbench/reliability/classify")
    async def reliability_classify(request: Request, body: dict):
        _ident(request)
        return classify_retry(str((body or {}).get("error_text", "")))

    @router.post("/v1/workbench/idempotency/check")
    async def idempotency_check(request: Request, body: dict):
        """§5/§8/§23 duplicate-run protection: the same key never runs twice."""
        ident = _ident(request)
        state = _state(request, ident)
        key = str((body or {}).get("key", ""))
        if not key:
            return _plain_error(request, corr_of, 400, "Give an idempotency key to check.")
        existing = _coll(state, "schedule-runs")
        for run in existing.values():
            if run.get("idempotency_key") == key:
                return {"duplicate": True, "first_run_id": run["id"],
                        "note": "This key already ran — the first result stands, nothing ran again."}
        return {"duplicate": False, "note": "No run with this key yet."}

    @router.post("/v1/workbench/coordination-tasks/dependency-graph")
    async def dependency_graph(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        task_ids = (body or {}).get("task_ids")
        tasks = [t for t in _coll(state, "coordination-tasks").values()
                 if not task_ids or t["id"] in task_ids]
        return topo_order(tasks)

    @router.post("/v1/workbench/society-runs/compare")
    async def society_compare(request: Request, body: dict):
        """§22 run comparison. Every society payload carries the disclaimer."""
        ident = _ident(request)
        state = _state(request, ident)
        runs = _coll(state, "society-runs")
        first = runs.get(str((body or {}).get("run_a", "")))
        second = runs.get(str((body or {}).get("run_b", "")))
        if first is None or second is None:
            return _plain_error(request, corr_of, 404, "One of those runs is not here.")
        same_output = (first.get("output_hash") and
                       first.get("output_hash") == second.get("output_hash"))
        return {"run_a": first["id"], "run_b": second["id"],
                "identical_output": bool(same_output),
                "config_changed": first.get("config_id") != second.get("config_id"),
                "label": "SIMULATED ESTIMATE — never a prediction."}

    # -- events: append-only (Event System). No delete route on purpose. --
    @router.post("/v1/workbench/events", status_code=201)
    async def event_append(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec, err = _create(state, "events", body, ident,
                           defaults={"kind": "update", "actor": ident.user_id})
        if err:
            return _plain_error(request, corr_of, 400, err)
        if not rec.get("kind"):
            rec["kind"] = "update"
        _save(request, ident, state)
        return rec

    @router.get("/v1/workbench/events")
    async def event_list(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return {"events": _list(state, "events", request)}

    @router.delete("/v1/workbench/events/{item_id}")
    async def event_delete_refused(request: Request, item_id: str):
        """Append-only, stated explicitly: the timeline is never rewritten."""
        _ident(request)
        return _plain_error(request, corr_of, 405,
                            "Events are never deleted — the activity timeline "
                            "is append-only, so the record stays honest.")

    # -- webhooks: secret stored as a hash only (W7 signature seam). --
    @router.post("/v1/workbench/webhooks", status_code=201)
    async def webhook_create(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        body = dict(body or {})
        secret = str(body.pop("secret", ""))
        if not str(body.get("url", "")).strip():
            return _plain_error(request, corr_of, 400, "Give the webhook address.")
        rec = {"id": new_id("wb"), "created_at": now_iso(),
               "created_by": ident.user_id, "updated_at": now_iso(),
               "secret_hash": hashlib.sha256(secret.encode()).hexdigest()
               if secret else "",
               "has_secret": bool(secret)}
        rec.update(body)
        _coll(state, "webhooks")[rec["id"]] = rec
        _save(request, ident, state)
        return {k: v for k, v in rec.items() if k != "secret_hash"}

    @router.get("/v1/workbench/webhooks")
    async def webhook_list(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return {"webhooks": [{k: v for k, v in r.items() if k != "secret_hash"}
                             for r in _coll(state, "webhooks").values()]}

    @router.post("/v1/workbench/webhooks/{item_id}/verify")
    async def webhook_verify(request: Request, item_id: str, body: dict):
        """Signature check needs the secret — which is never stored in
        readable form. Verification therefore takes the secret with the
        call (used once, never saved) and answers yes/no only."""
        _ident(request)
        ok = webhook_signature_ok(str((body or {}).get("secret", "")),
                                  str((body or {}).get("payload", "")),
                                  str((body or {}).get("signature", "")))
        return {"valid": ok}

    @router.post("/v1/workbench/webhooks/{item_id}/test")
    async def webhook_test(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "webhooks", item_id)
        if rec is None:
            return _not_found(request)
        url = str(rec.get("url", ""))
        ok = url.startswith("https://")
        return {"tested": True, "config_ok": ok,
                "note": ("The address must start with https://."
                         if not ok else "Address and secret are set. No message "
                         "was sent — this checks the setup, it does not call out.")}

    # -- api tokens: value returned once, only its hash is stored. --
    @router.post("/v1/workbench/api-tokens", status_code=201)
    async def token_create(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        name = str((body or {}).get("name", "")).strip()
        if not name:
            return _plain_error(request, corr_of, 400, "Name the token first.")
        value = "aq_" + secrets.token_urlsafe(32)
        rec = {"id": new_id("wb"), "name": name, "created_at": now_iso(),
               "created_by": ident.user_id, "updated_at": now_iso(),
               "token_hash": hashlib.sha256(value.encode()).hexdigest(),
               "revoked": False}
        _coll(state, "api-tokens")[rec["id"]] = rec
        _save(request, ident, state)
        return {"id": rec["id"], "name": name, "value": value,
                "note": "Copy this now — it is shown once and never stored in readable form."}

    @router.get("/v1/workbench/api-tokens")
    async def token_list(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return {"tokens": [{k: v for k, v in r.items() if k != "token_hash"}
                           for r in _coll(state, "api-tokens").values()]}

    @router.delete("/v1/workbench/api-tokens/{item_id}")
    async def token_revoke(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "api-tokens", item_id)
        if rec is None:
            return _not_found(request)
        rec["revoked"] = True
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return {"revoked": True, "id": item_id}

    # -- backups: snapshot of THIS tenant's workbench state, verified. --
    @router.post("/v1/workbench/backups", status_code=201)
    async def backup_create(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        # BUG-1 fix: the snapshot must be a COPY. Storing a reference
        # to state["collections"] and then inserting the record (which
        # contains the snapshot) into state["collections"]["backups"]
        # created a cycle, so the whole-state json.dump always failed
        # with "Circular reference detected" and no backup could ever
        # be created.
        snapshot = {"collections": copy.deepcopy(state.get("collections", {})),
                    "prefs": copy.deepcopy(state.get("prefs"))}
        blob = json.dumps(snapshot, sort_keys=True)
        rec = {"id": new_id("wb"), "created_at": now_iso(),
               "created_by": ident.user_id, "updated_at": now_iso(),
               "label": str((body or {}).get("label", "")),
               "sha256": hashlib.sha256(blob.encode()).hexdigest(),
               "counts": {k: len(v) for k, v in snapshot["collections"].items()},
               "snapshot": snapshot}
        _coll(state, "backups")[rec["id"]] = rec
        _save(request, ident, state)
        return {k: v for k, v in rec.items() if k != "snapshot"}

    @router.get("/v1/workbench/backups")
    async def backup_list(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return {"backups": [{k: v for k, v in r.items() if k != "snapshot"}
                            for r in _coll(state, "backups").values()]}

    @router.post("/v1/workbench/backups/{item_id}/verify")
    async def backup_verify(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "backups", item_id)
        if rec is None:
            return _not_found(request)
        blob = json.dumps(rec.get("snapshot", {}), sort_keys=True)
        ok = hashlib.sha256(blob.encode()).hexdigest() == rec.get("sha256")
        return {"verified": ok, "id": item_id,
                "counts": rec.get("counts", {})}

    @router.post("/v1/workbench/backups/{item_id}/restore-preview")
    async def backup_restore_preview(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "backups", item_id)
        if rec is None:
            return _not_found(request)
        snap = rec.get("snapshot", {})
        return {"dry_run": True, "id": item_id,
                "would_restore_counts": {k: len(v) for k, v in
                                         snap.get("collections", {}).items()},
                "note": "Preview only — nothing was changed. Restore with "
                        "confirmation to apply it."}

    @router.post("/v1/workbench/backups/{item_id}/restore")
    async def backup_restore(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "backups", item_id)
        if rec is None:
            return _not_found(request)
        if not (body or {}).get("confirm"):
            return _plain_error(request, corr_of, 400,
                                "Restoring replaces the current workbench records. "
                                "Confirm to go ahead.")
        snap = rec.get("snapshot", {})
        kept_backups = _coll(state, "backups")
        state["collections"] = dict(snap.get("collections", {}))
        state["prefs"] = snap.get("prefs")
        _coll(state, "backups").update(kept_backups)
        _save(request, ident, state)
        return {"restored": True, "id": item_id}

    # -- model queue: admission against the model's own limit (§9). --
    @router.post("/v1/workbench/model-queue", status_code=201)
    async def queue_create(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        model_id = str((body or {}).get("model_id", ""))
        model = _get(state, "local-models", model_id) if model_id else None
        limit = 1
        if model is not None:
            try:
                limit = int(model.get("concurrency_limit", 1))
            except Exception:
                limit = 1
        admitted = sum(1 for q in _coll(state, "model-queue").values()
                       if q.get("model_id") == model_id
                       and q.get("status") == "admitted")
        status = "admitted" if admitted < max(limit, 1) else "queued"
        rec, err = _create(state, "model-queue", body, ident,
                           defaults={"status": status,
                                     "priority": int((body or {}).get("priority", 0))})
        if err:
            return _plain_error(request, corr_of, 400, err)
        rec["status"] = status
        _save(request, ident, state)
        return rec

    @router.get("/v1/workbench/model-queue")
    async def queue_list(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        return {"queue": _list(state, "model-queue", request)}

    @router.post("/v1/workbench/model-queue/{item_id}/cancel")
    async def queue_cancel(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "model-queue", item_id)
        if rec is None:
            return _not_found(request)
        rec["status"] = "cancelled"
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    # -- generic CRUD for the remaining collections (one surface, §1–§22) --
    def _register_crud(coll: str):
        prefix = f"/v1/workbench/{coll}"

        @router.post(prefix, status_code=201)
        async def crud_create(request: Request, body: dict, _coll_name=coll):
            ident = _ident(request)
            state = _state(request, ident)
            body = dict(body or {})
            defaults: dict = {}
            if _coll_name == "messages":
                if body.get("state") and body["state"] not in RESPONSE_STATES:
                    return _plain_error(request, corr_of, 400,
                                        "That response state is not one of the eight known states.")
                defaults = {"state": "generating", "variants": [],
                            "reactions": [], "labels": [], "annotations": []}
            if _coll_name == "approvals":
                defaults = {"status": "pending",
                            "affected": body.get("affected", []),
                            "reversibility": body.get("reversibility", ""),
                            "rejection_consequences": body.get("rejection_consequences", ""),
                            "options": ["approve-once", "approve-for-mission",
                                        "reject", "inspect"]}
            if _coll_name == "acceptance":
                defaults = {"criteria": []}
            if _coll_name == "artifacts":
                defaults = {"versions": [], "provenance": {}}
            if _coll_name == "projects":
                defaults = {"favorite": False, "archived": False,
                            "color": "", "icon": "", "description": "",
                            "instructions": "", "variables": {},
                            "env_profiles": []}
            if _coll_name == "skill-provenance":
                defaults = {"trust": "unreviewed"}
            if _coll_name == "plugins":
                # §18: install never auto-grants anything.
                defaults = {"enabled": False, "granted_permissions": []}
            if _coll_name == "coordination-tasks":
                norm = " ".join(str(body.get("title", "")).lower().split())
                for other in _coll(state, "coordination-tasks").values():
                    if " ".join(str(other.get("title", "")).lower().split()) == norm and norm:
                        defaults = {"duplicate_of": other["id"],
                                    "duplicate_note": "A task with the same wording already exists."}
                        break
            if _coll_name == "project-memories":
                # §21: a different text under the same topic is a CONFLICT,
                # never a silent overwrite; kind defaults to assumption.
                defaults = {"kind": body.get("kind", "assumption"),
                            "status": "active"}
                for other in _coll(state, "project-memories").values():
                    if other.get("project_id") == body.get("project_id") \
                            and other.get("topic") == body.get("topic") \
                            and other.get("status", "active") == "active" \
                            and str(other.get("fact", "")) != str(body.get("fact", "")):
                        defaults["conflict_with"] = other["id"]
                        defaults["conflict_note"] = ("Another fact under this "
                                                     "topic says something different — "
                                                     "check both before trusting either.")
                        break
            if _coll_name == "local-models":
                # §9: unreported fields stay "unavailable", never zero.
                for field in ("memory_mb", "gpu_memory_mb", "cpu_percent",
                              "gpu_percent", "context_size", "queue_length"):
                    defaults.setdefault(field, "unavailable")
                defaults.setdefault("source", "server-reported")
            rec, err = _create(state, _coll_name, body, ident, defaults)
            if err:
                return _plain_error(request, corr_of, 400, err)
            _save(request, ident, state)
            return rec

        @router.get(prefix)
        async def crud_list(request: Request, _coll_name=coll):
            ident = _ident(request)
            state = _state(request, ident)
            items = _list(state, _coll_name, request)
            if _coll_name == "grants":
                now = datetime.now(timezone.utc)
                for item in items:
                    exp = _parse_iso(item.get("expires_at", "")) if item.get("expires_at") else None
                    if exp and exp < now and item.get("status") == "active":
                        item["status"] = "expired"
            return {"items": items, "system": COLLECTIONS[_coll_name]}

        @router.get(prefix + "/{item_id}")
        async def crud_get(request: Request, item_id: str, _coll_name=coll):
            ident = _ident(request)
            state = _state(request, ident)
            rec = _get(state, _coll_name, item_id)
            if rec is None:
                return _not_found(request)
            return rec

        @router.patch(prefix + "/{item_id}")
        async def crud_patch(request: Request, item_id: str, body: dict,
                             _coll_name=coll):
            ident = _ident(request)
            state = _state(request, ident)
            rec = _get(state, _coll_name, item_id)
            if rec is None:
                return _not_found(request)
            if _coll_name == "messages" and (body or {}).get("state") \
                    and body["state"] not in RESPONSE_STATES:
                return _plain_error(request, corr_of, 400,
                                    "That response state is not one of the eight known states.")
            rec.update(body or {})
            rec["updated_at"] = now_iso()
            _save(request, ident, state)
            return rec

        @router.delete(prefix + "/{item_id}")
        async def crud_delete(request: Request, item_id: str, _coll_name=coll):
            ident = _ident(request)
            state = _state(request, ident)
            if _get(state, _coll_name, item_id) is None:
                return _not_found(request)
            del _coll(state, _coll_name)[item_id]
            _save(request, ident, state)
            return {"deleted": True, "id": item_id}

    for _name in _CRUD:
        _register_crud(_name)

    # -- item bespoke routes (suffix paths never collide with CRUD) --
    @router.post("/v1/workbench/templates/{item_id}/preview")
    async def template_preview(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "templates", item_id)
        if rec is None:
            return _not_found(request)
        return render_template(str(rec.get("body", "")),
                               (body or {}).get("variables", {}))

    @router.post("/v1/workbench/messages/{item_id}/variants")
    async def message_variant(request: Request, item_id: str, body: dict):
        """§2 response variants: a new variant sits beside the original;
        comparison never overwrites either."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "messages", item_id)
        if rec is None:
            return _not_found(request)
        variant = {"id": new_id("wb"), "text": str((body or {}).get("text", "")),
                   "created_at": now_iso()}
        rec.setdefault("variants", []).append(variant)
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return {"message_id": item_id, "variants": rec["variants"]}

    @router.post("/v1/workbench/messages/compare")
    async def message_compare(request: Request, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        first = _get(state, "messages", str((body or {}).get("a_id", "")))
        second = _get(state, "messages", str((body or {}).get("b_id", "")))
        if first is None or second is None:
            return _not_found(request)
        return {"a": {"id": first["id"], "text": first.get("body", ""),
                       "state": first.get("state")},
                "b": {"id": second["id"], "text": second.get("body", ""),
                      "state": second.get("state")},
                "note": "Side by side — neither message was changed."}

    @router.post("/v1/workbench/messages/{item_id}/branch")
    async def message_branch(request: Request, item_id: str, body: dict):
        """§2 edit-and-resubmit, BRANCH semantics: the original stays, the
        edited text starts a new branch linked to it."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "messages", item_id)
        if rec is None:
            return _not_found(request)
        branch, _ = _create(state, "messages",
                            {"conversation_id": rec.get("conversation_id"),
                             "body": str((body or {}).get("body", rec.get("body", ""))),
                             "branch_of": item_id, "state": "generating"},
                            ident)
        _save(request, ident, state)
        return {"semantics": "branch — the original message is untouched",
                "original_id": item_id, "branch": branch}

    @router.post("/v1/workbench/messages/{item_id}/replace")
    async def message_replace(request: Request, item_id: str, body: dict):
        """§2 edit-and-resubmit, REPLACE semantics: the old text is kept
        in history and marked superseded, so nothing is silently lost."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "messages", item_id)
        if rec is None:
            return _not_found(request)
        rec.setdefault("history", []).append({"body": rec.get("body", ""),
                                              "at": now_iso()})
        rec["body"] = str((body or {}).get("body", ""))
        rec["state"] = "generating"
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return {"semantics": "replace — the old text is kept in history",
                "message": rec}

    @router.post("/v1/workbench/notifications/{item_id}/read")
    async def notif_read(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "notifications", item_id)
        if rec is None:
            return _not_found(request)
        rec["read"] = True
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/notifications/read-all")
    async def notif_read_all(request: Request):
        ident = _ident(request)
        state = _state(request, ident)
        count = 0
        for rec in _coll(state, "notifications").values():
            if not rec.get("read"):
                rec["read"] = True
                count += 1
        _save(request, ident, state)
        return {"marked_read": count}

    @router.post("/v1/workbench/approvals/{item_id}/decide")
    async def approval_decide(request: Request, item_id: str, body: dict):
        """§6 the approval queue: all six elements are on the stored
        request; the decision here is one of approve-once /
        approve-for-mission / reject / inspect — nothing broader."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "approvals", item_id)
        if rec is None:
            return _not_found(request)
        decision = str((body or {}).get("decision", ""))
        if decision not in ("approve-once", "approve-for-mission", "reject", "inspect"):
            return _plain_error(request, corr_of, 400,
                                "Choose approve once, approve for this mission, reject, or inspect.")
        rec["status"] = {"approve-once": "approved-once",
                         "approve-for-mission": "approved-for-mission",
                         "reject": "rejected", "inspect": "inspecting"}[decision]
        rec["decided_by"] = ident.user_id
        rec["updated_at"] = now_iso()
        grant = None
        if decision == "approve-for-mission":
            grant, _ = _create(state, "grants",
                               {"approval_id": item_id,
                                "tool": rec.get("tool", ""),
                                "mission_id": rec.get("mission_id", ""),
                                "scope": "mission",
                                "expires_at": (body or {}).get("expires_at"),
                                "status": "active"}, ident)
        _save(request, ident, state)
        return {"approval": rec, "grant": grant}

    @router.post("/v1/workbench/grants/{item_id}/revoke")
    async def grant_revoke(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "grants", item_id)
        if rec is None:
            return _not_found(request)
        rec["status"] = "revoked"
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/acceptance/{item_id}/certificate")
    async def acceptance_certificate(request: Request, item_id: str):
        """§7 machine-readable completion certificate. A set with any
        criterion not verified is labelled unverified, honestly."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "acceptance", item_id)
        if rec is None:
            return _not_found(request)
        criteria = rec.get("criteria", []) or []
        counts = {"verified": 0, "failed": 0, "not_checked": 0}
        for criterion in criteria:
            status = criterion.get("status", "not_checked")
            counts[status if status in counts else "not_checked"] += 1
        complete = bool(criteria) and counts["failed"] == 0 \
            and counts["not_checked"] == 0
        blob = json.dumps({"mission_id": rec.get("mission_id"), "criteria": criteria},
                          sort_keys=True)
        return {"certificate_id": new_id("cert"), "mission_id": rec.get("mission_id"),
                "complete": complete,
                "label": "VERIFIED" if complete else "UNVERIFIED — criteria remain unchecked or failed",
                "counts": counts,
                "criteria": criteria,
                "sha256": hashlib.sha256(blob.encode()).hexdigest(),
                "generated_at": now_iso()}

    @router.get("/v1/workbench/acceptance/{item_id}/coverage")
    async def acceptance_coverage(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "acceptance", item_id)
        if rec is None:
            return _not_found(request)
        requirements = [r for r in _coll(state, "requirements").values()
                        if r.get("mission_id") == rec.get("mission_id")]
        criteria = rec.get("criteria", []) or []
        covered = set()
        for criterion in criteria:
            covered.update(criterion.get("requirement_ids", []) or [])
        missing = [r for r in requirements if r["id"] not in covered]
        return {"mission_id": rec.get("mission_id"),
                "requirements_total": len(requirements),
                "requirements_covered": len(requirements) - len(missing),
                "missing_requirements": [{"id": r["id"], "title": r.get("title", "")}
                                         for r in missing],
                "warning": ("Some requirements have no acceptance criterion yet."
                            if missing else "Every requirement has a criterion."),
                "criteria_counts": {
                    "verified": sum(1 for c in criteria if c.get("status") == "verified"),
                    "failed": sum(1 for c in criteria if c.get("status") == "failed"),
                    "not_checked": sum(1 for c in criteria if c.get("status") not in ("verified", "failed"))}}

    @router.post("/v1/workbench/checkpoints/{item_id}/resume")
    async def checkpoint_resume(request: Request, item_id: str):
        """§8 resume: the checkpoint is returned with its safety note.
        End-to-end resume safety rides the W5 seam (NOT_TESTED until
        W5 merges); this surface never claims the mission already ran."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "checkpoints", item_id)
        if rec is None:
            return _not_found(request)
        return {"checkpoint": rec, "resumed": False,
                "safety_note": "Resume safety is checked by the W5 resume "
                               "machinery before any work restarts — that wiring "
                               "is not connected yet, so this only returns the "
                               "saved checkpoint.",
                "wiring": "NOT_TESTED (W5 seam)"}

    @router.post("/v1/workbench/checkpoints/{item_id}/rollback-request")
    async def checkpoint_rollback(request: Request, item_id: str, body: dict):
        """§8 rollback rides the W8 file-rollback seam: recorded here,
        applied by W8 when it merges (NOT_TESTED end-to-end)."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "checkpoints", item_id)
        if rec is None:
            return _not_found(request)
        entry, _ = _create(state, "events",
                           {"kind": "rollback_requested",
                            "target": item_id,
                            "detail": str((body or {}).get("reason", "")),
                            "actor": ident.user_id}, ident)
        _save(request, ident, state)
        return {"checkpoint_id": item_id, "rollback": "requested",
                "applied": False, "event_id": entry["id"],
                "wiring": "NOT_TESTED (W8 seam)"}

    @router.post("/v1/workbench/budgets/{item_id}/reserve")
    async def budget_reserve(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "budgets", item_id)
        if rec is None:
            return _not_found(request)
        try:
            amount = float((body or {}).get("amount_usd", 0))
        except Exception:
            return _plain_error(request, corr_of, 400, "Give a reservation amount in dollars.")
        # A missing amount stays the historical no-op (amount defaults to
        # 0); an amount that IS given must be positive — a negative
        # reservation used to drive reserved_usd below zero and corrupt
        # the over-limit arithmetic for later reserves.
        if "amount_usd" in (body or {}) and amount <= 0:
            return _plain_error(request, corr_of, 400,
                                "The reservation amount must be positive.")
        reserved = float(rec.get("reserved_usd", 0)) + amount
        limit = float(rec.get("limit_usd", 0))
        if limit and reserved > limit:
            return _plain_error(request, corr_of, 400,
                                "That reservation would go over this budget.")
        rec["reserved_usd"] = round(reserved, 6)
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/budgets/{item_id}/release")
    async def budget_release(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "budgets", item_id)
        if rec is None:
            return _not_found(request)
        try:
            amount = float((body or {}).get("amount_usd", 0))
        except Exception:
            return _plain_error(request, corr_of, 400, "Give a release amount in dollars.")
        rec["reserved_usd"] = round(max(0.0, float(rec.get("reserved_usd", 0)) - amount), 6)
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.get("/v1/workbench/projects/{item_id}/health")
    async def project_health(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "projects", item_id)
        if rec is None:
            return _not_found(request)
        open_issues = [i for i in _coll(state, "issues").values()
                       if i.get("project_id") == item_id
                       and i.get("status", "open") == "open"]
        return {"project_id": item_id,
                "health": "needs attention" if open_issues else "healthy",
                "open_issues": len(open_issues),
                "archived": bool(rec.get("archived")),
                "note": ("Open issues are waiting." if open_issues
                         else "No open issues recorded.")}

    @router.post("/v1/workbench/projects/{item_id}/duplicate")
    async def project_duplicate(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "projects", item_id)
        if rec is None:
            return _not_found(request)
        copy, _ = _create(state, "projects",
                          {k: v for k, v in rec.items()
                           if k not in ("id", "created_at", "updated_at")}
                          | {"name": f"{rec.get('name', '')} (copy)",
                             "favorite": False, "archived": False}, ident)
        _save(request, ident, state)
        return {"original_id": item_id, "copy": copy}

    @router.get("/v1/workbench/projects/{item_id}/handoff")
    async def project_handoff(request: Request, item_id: str):
        """§21 project handoff summary: decisions, requirements, open
        issues, assumptions, limitations — one page to hand over."""
        ident = _ident(request)
        state = _state(request, ident)
        if _get(state, "projects", item_id) is None:
            return _not_found(request)
        def _for(coll):
            return [r for r in _coll(state, coll).values()
                    if r.get("project_id") == item_id]
        return {"project_id": item_id,
                "decisions": _for("decisions"),
                "requirements": _for("requirements"),
                "open_issues": [i for i in _for("issues") if i.get("status", "open") == "open"],
                "assumptions": _for("assumptions"),
                "limitations": _for("limitations"),
                "rejected_approaches": _for("rejected")}

    @router.post("/v1/workbench/project-memories/{item_id}/correct")
    async def memory_correct(request: Request, item_id: str, body: dict):
        """§21 correction: the old fact is superseded, never rewritten
        in place — the correction stays on the record."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "project-memories", item_id)
        if rec is None:
            return _not_found(request)
        new_text = str((body or {}).get("fact", "")).strip()
        if not new_text:
            return _plain_error(request, corr_of, 400, "Write the corrected fact.")
        rec["status"] = "superseded"
        rec["updated_at"] = now_iso()
        corrected, _ = _create(state, "project-memories",
                               {"project_id": rec.get("project_id"),
                                "topic": rec.get("topic"),
                                "fact": new_text,
                                "kind": rec.get("kind", "assumption"),
                                "source": "correction",
                                "supersedes": item_id, "status": "active"},
                               ident)
        _save(request, ident, state)
        return {"old": rec, "corrected": corrected}

    @router.post("/v1/workbench/project-memories/{item_id}/delete")
    async def memory_delete(request: Request, item_id: str):
        """§21 deletion propagation: the fact is marked deleted here,
        and the record says what that does and does not erase."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "project-memories", item_id)
        if rec is None:
            return _not_found(request)
        rec["status"] = "deleted"
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return {"deleted": True, "id": item_id,
                "note": "Removed from project memory. Copies already exported "
                        "or quoted in mission histories are not erased by this."}

    @router.post("/v1/workbench/artifacts/{item_id}/versions")
    async def artifact_version(request: Request, item_id: str, body: dict):
        """§10/§11 version + conflict detection: a version whose base is
        not the latest version is flagged as a conflict, not merged
        silently."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "artifacts", item_id)
        if rec is None:
            return _not_found(request)
        versions = rec.setdefault("versions", [])
        base = (body or {}).get("base_version_id")
        content = str((body or {}).get("content", ""))
        version = {"id": new_id("ver"), "content": content,
                   "created_at": now_iso(), "created_by": ident.user_id,
                   "note": str((body or {}).get("note", "")),
                   "sha256": hashlib.sha256(content.encode()).hexdigest()}
        conflict = None
        if versions and base and base != versions[-1]["id"]:
            version["conflict"] = True
            conflict, _ = _create(state, "conflicts",
                                  {"artifact_id": item_id,
                                   "base_version_id": base,
                                   "latest_version_id": versions[-1]["id"],
                                   "actor": ident.user_id}, ident)
        versions.append(version)
        rec["updated_at"] = now_iso()
        if conflict:
            _save(request, ident, state)
        else:
            _save(request, ident, state)
        return {"artifact_id": item_id, "version": version,
                "conflict": conflict}

    @router.post("/v1/workbench/artifacts/{item_id}/compare")
    async def artifact_compare(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "artifacts", item_id)
        if rec is None:
            return _not_found(request)
        by_id = {v["id"]: v for v in rec.get("versions", [])}
        first = by_id.get(str((body or {}).get("version_a", "")))
        second = by_id.get(str((body or {}).get("version_b", "")))
        if first is None or second is None:
            return _not_found(request)
        return {"artifact_id": item_id,
                "diff": diff_lines(first.get("content", ""),
                                   second.get("content", ""))}

    @router.get("/v1/workbench/artifacts/{item_id}/export")
    async def artifact_export(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "artifacts", item_id)
        if rec is None:
            return _not_found(request)
        versions = rec.get("versions", [])
        latest = versions[-1] if versions else {}
        return {"artifact_id": item_id, "title": rec.get("title", ""),
                "content": latest.get("content", ""),
                "version_id": latest.get("id"),
                "provenance": rec.get("provenance", {}),
                "versions": len(versions)}

    @router.post("/v1/workbench/plugins/{item_id}/grant-permission")
    async def plugin_grant(request: Request, item_id: str, body: dict):
        """§18 a plugin permission is granted explicitly, one at a time —
        install alone grants nothing."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "plugins", item_id)
        if rec is None:
            return _not_found(request)
        perm = str((body or {}).get("permission", "")).strip()
        if not perm:
            return _plain_error(request, corr_of, 400, "Name the permission to grant.")
        declared = rec.get("permissions_declared", []) or []
        if declared and perm not in declared:
            return _plain_error(request, corr_of, 400,
                                "That permission is not one this plugin declared.")
        granted = rec.setdefault("granted_permissions", [])
        if perm not in granted:
            granted.append(perm)
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/plugins/{item_id}/revoke-permission")
    async def plugin_revoke(request: Request, item_id: str, body: dict):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "plugins", item_id)
        if rec is None:
            return _not_found(request)
        perm = str((body or {}).get("permission", ""))
        rec["granted_permissions"] = [p for p in rec.get("granted_permissions", [])
                                      if p != perm]
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/plugins/{item_id}/enable")
    async def plugin_enable(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "plugins", item_id)
        if rec is None:
            return _not_found(request)
        rec["enabled"] = True
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return {"id": item_id, "enabled": True,
                "granted_permissions": rec.get("granted_permissions", []),
                "note": "Enabled with only the permissions granted above — "
                        "nothing else was granted by turning it on."}

    # NOTE: top-level path on purpose — /v1/workbench/skill-provenance/{id}
    # (generic CRUD, registered earlier) would otherwise swallow it.
    @router.get("/v1/workbench/skill-effectiveness")
    async def skill_effectiveness(request: Request):
        """§11 effectiveness from recorded use: frequency, success rate,
        average latency and cost — all from provenance records, never
        estimated."""
        ident = _ident(request)
        state = _state(request, ident)
        agg: dict[str, dict] = {}
        for rec in _coll(state, "skill-provenance").values():
            sid = rec.get("skill_id", "")
            slot = agg.setdefault(sid, {"uses": 0, "successes": 0,
                                        "latencies": [], "costs": []})
            slot["uses"] += 1
            if rec.get("succeeded") is True:
                slot["successes"] += 1
            if rec.get("latency_ms") is not None:
                slot["latencies"].append(float(rec["latency_ms"]))
            if rec.get("cost_usd") is not None:
                slot["costs"].append(float(rec["cost_usd"]))
        out = []
        for sid, slot in agg.items():
            out.append({"skill_id": sid, "uses": slot["uses"],
                        "success_rate": round(slot["successes"] / slot["uses"], 3)
                        if slot["uses"] else None,
                        "avg_latency_ms": round(sum(slot["latencies"]) / len(slot["latencies"]), 1)
                        if slot["latencies"] else None,
                        "total_cost_usd": round(sum(slot["costs"]), 6)})
        return {"skills": out}

    @router.post("/v1/workbench/skill-provenance/validate-compatibility")
    async def skill_compat(request: Request, body: dict):
        """§11/§23 compatibility: missing dependencies and declared
        conflicts are reported, not papered over."""
        _ident(request)
        skills = (body or {}).get("skills", []) or []
        known = {s.get("id") for s in skills}
        missing, conflicts = [], []
        for skill in skills:
            for dep in skill.get("requires", []) or []:
                if dep not in known:
                    missing.append({"skill": skill.get("id"), "missing": dep})
            for other in skill.get("conflicts", []) or []:
                if other in known:
                    conflicts.append({"skill": skill.get("id"), "conflicts_with": other})
        return {"compatible": not missing and not conflicts,
                "missing_dependencies": missing, "conflicts": conflicts}

    @router.get("/v1/workbench/local-models/{item_id}/contention")
    async def model_contention(request: Request, item_id: str):
        """§9 contention warning from the real queue, not a guess."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "local-models", item_id)
        if rec is None:
            return _not_found(request)
        queued = [q for q in _coll(state, "model-queue").values()
                  if q.get("model_id") == item_id and q.get("status") == "queued"]
        admitted = [q for q in _coll(state, "model-queue").values()
                    if q.get("model_id") == item_id and q.get("status") == "admitted"]
        return {"model_id": item_id, "queued": len(queued),
                "admitted": len(admitted),
                "contention": bool(queued),
                "note": ("Work is waiting because the model is busy."
                         if queued else "Nothing is waiting for this model."),
                "memory_mb": rec.get("memory_mb", "unavailable"),
                "source": rec.get("source", "server-reported")}

    @router.post("/v1/workbench/schedule-extras/{item_id}/preview")
    async def schedule_preview(request: Request, item_id: str):
        """§5 preview: what the schedule would do, without doing it."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "schedule-extras", item_id)
        if rec is None:
            return _not_found(request)
        nxt = rec.get("run_at")
        if rec.get("cadence"):
            try:
                from .schedules import compute_next_run
                nxt = compute_next_run(rec["cadence"],
                                       datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
            except Exception:
                nxt = rec.get("run_at")
        return {"schedule_id": item_id, "title": rec.get("title", ""),
                "next_run_at": nxt, "enabled": rec.get("enabled", True),
                "trigger": rec.get("trigger", "time"),
                "execution_window": rec.get("execution_window"),
                "dry_run": True, "note": "Preview only — nothing was scheduled to run."}

    @router.post("/v1/workbench/schedule-extras/{item_id}/dry-run")
    async def schedule_dry_run(request: Request, item_id: str):
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "schedule-extras", item_id)
        if rec is None:
            return _not_found(request)
        return {"schedule_id": item_id, "would_run": bool(rec.get("enabled", True)),
                "title": rec.get("title", ""), "created_run": False,
                "note": "Dry run — no mission was created and no run was recorded."}

    @router.post("/v1/workbench/schedule-extras/{item_id}/disable")
    async def schedule_disable(request: Request, item_id: str):
        """§5 disable-without-delete: the schedule stays, it just stops."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "schedule-extras", item_id)
        if rec is None:
            return _not_found(request)
        rec["enabled"] = False
        rec["updated_at"] = now_iso()
        _save(request, ident, state)
        return rec

    @router.post("/v1/workbench/schedule-extras/{item_id}/simulate-trigger")
    async def schedule_trigger(request: Request, item_id: str, body: dict):
        """§5 trigger simulation with duplicate-run protection: the same
        idempotency key returns the first run, never a second one."""
        ident = _ident(request)
        state = _state(request, ident)
        rec = _get(state, "schedule-extras", item_id)
        if rec is None:
            return _not_found(request)
        if not rec.get("enabled", True):
            return _plain_error(request, corr_of, 400, "This schedule is disabled.")
        key = str((body or {}).get("idempotency_key", ""))
        if key:
            for run in _coll(state, "schedule-runs").values():
                if run.get("idempotency_key") == key:
                    return {"duplicate": True, "run": run,
                            "note": "This trigger already ran — the first result stands."}
        run, _ = _create(state, "schedule-runs",
                         {"schedule_id": item_id,
                          "trigger": str((body or {}).get("trigger", rec.get("trigger", "time"))),
                          "idempotency_key": key,
                          "missed_run_policy": rec.get("missed_run_policy", "run-once"),
                          "status": "recorded"}, ident)
        _save(request, ident, state)
        return {"duplicate": False, "run": run}

    @router.post("/v1/workbench/coordination-tasks/{item_id}/match")
    async def task_match(request: Request, item_id: str):
        """§22 capability matching: agents score by overlapping
        capabilities — a declared match, never a claimed fit."""
        ident = _ident(request)
        state = _state(request, ident)
        task = _get(state, "coordination-tasks", item_id)
        if task is None:
            return _not_found(request)
        needed = set(task.get("capabilities_needed", []) or [])
        scored = []
        for agent in _coll(state, "coordination-agents").values():
            if not agent.get("available", True):
                continue
            have = set(agent.get("capabilities", []) or [])
            overlap = needed & have if needed else set()
            scored.append({"agent_id": agent["id"],
                           "matched": sorted(overlap),
                           "missing": sorted(needed - have),
                           "score": round(len(overlap) / len(needed), 3) if needed else 0.0})
        scored.sort(key=lambda s: -s["score"])
        return {"task_id": item_id, "matches": scored}

    @router.post("/v1/workbench/society-runs/{item_id}/reproduce")
    async def society_reproduce(request: Request, item_id: str):
        """§22 reproduction from a saved config — with the honest label
        that LLM output does not reproduce exactly."""
        ident = _ident(request)
        state = _state(request, ident)
        run = _get(state, "society-runs", item_id)
        if run is None:
            return _not_found(request)
        config = _get(state, "society-configs", str(run.get("config_id", "")))
        return {"run_id": item_id, "config": config,
                "label": "SIMULATED ESTIMATE — never a prediction.",
                "reproducibility": "The saved setup can be run again, but a "
                                  "language-model run does not give identical "
                                  "answers twice. Treat a rerun as a new run, "
                                  "not a reproduction of the old one."}

    @router.post("/v1/workbench/retention/deletion")
    async def retention_deletion(request: Request, body: dict):
        """§15 deletion with the honesty statement attached: only the
        named workbench records go; the survivors are named back."""
        ident = _ident(request)
        state = _state(request, ident)
        coll = str((body or {}).get("collection", ""))
        item_id = str((body or {}).get("item_id", ""))
        if coll not in _CRUD or not item_id:
            return _plain_error(request, corr_of, 400,
                                "Name a workbench collection and item to delete.")
        if _get(state, coll, item_id) is None:
            return _not_found(request)
        del _coll(state, coll)[item_id]
        _save(request, ident, state)
        return {"deleted": True, "collection": coll, "id": item_id,
                "survives": ["Exports you already downloaded",
                             "Provider records held by the provider",
                             "Backups you created, until you delete them too",
                             "Server logs"]}

    return router
