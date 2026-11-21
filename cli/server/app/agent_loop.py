"""AgentQon — the agent loop (v22, R1).

A session-based agent, distinct from the fixed mission panel: it
holds a GOAL and a WORKSPACE, and iterates — the model proposes the
next action as strict JSON, a permission-gated tool executes it, the
result feeds back as an observation — until the model declares
completion, the step cap stops it, or the session's budget ceiling
stops it.

Design notes:
- The tool protocol rides the existing structured-JSON invoke path
  (GatewayClient.chat_json): a strict action schema in the system
  prompt, parsed defensively with the persona-schema recovery
  patterns proven in the panel adapters (alias keys accepted, a
  content-bearing reply without an action key reads as completion,
  an unusable reply earns a correction observation, never a crash).
  Models with native function-calling are NOT required.
- Every model call goes through GatewayClient, so provider 429s get
  the v21.4.4 retry + pacing for free, and rate-limit waits surface
  as session events via on_rate_limited.
- Approvals park the session: status awaiting_approval, the pending
  action stored on the record and visible via the API. Approve
  executes it; deny feeds the agent a denial observation and the
  session CONTINUES — a denial is information, not death.
- Execution is inline (the run/approve calls drive the loop until it
  parks or terminates), mirroring the API's inline mission runs; the
  loop re-reads the session record at every step, so a cancel from
  another request lands at the next step boundary.
- Terminal states write a structured lesson via app.evolve (R2), and
  the model pick consults recorded lessons (a repeatedly failing
  model is deprioritized for a clean alternative).
"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import agent_tools, evolve
from .config import Settings
from .domain import MISSION_BUDGET_CEILING_USD, event_envelope, new_id, now_iso
from .gateway_client import GatewayClient, GatewayError
from .modelstate import resolve_state

DEFAULT_MAX_STEPS = 25
MAX_STEPS_LIMIT = 200
MAX_FORMAT_FAILURES = 3
# Completion guards, sharing ONE rejection budget: a "done" claim
# is rejected — with the reason quoted back — at most this many
# times, when (1) the most recent tool result is a failure with no
# successful tool call after it (live evidence: session
# ags_4b8ba2c14610, whose model claimed "the unittest run passed"
# over a final ToolResult ok:false, and the loop believed it), or
# (2) the agent changed files (successful fs_write/fs_edit) but ran
# no observation after the last change — it never checked its own
# work (live evidence: sessions ags_986a8c0f9966 and
# ags_9f444a7cdb49, where completion was claimed after a lone
# fs_write — in one case claiming a second file that was never
# created). A claim that survives the budget ends the session
# flagged UNVERIFIED, never presented as a verified success.
MAX_COMPLETION_REJECTIONS = 2
# Tools that change the workspace, and tools that count as LOOKING
# at the result (verify-after-build guard). A shell_run counts as
# an observation: running the test IS checking the work.
MUTATION_TOOLS = ("fs_write", "fs_edit")
OBSERVATION_TOOLS = ("fs_read", "fs_list", "fs_search", "shell_run",
                     "web_read")
VERIFICATION_MISSING_ERROR = (
    "no verification step ran after the last successful file change "
    "(fs_write/fs_edit): the agent never read a changed file back, "
    "listed or searched the workspace, read a page, or ran a command "
    "to check the result of its changes")
# Token allowance used to price a call before it happens (budget
# checks) when the catalog price is known; unknown prices fall back
# to the platform's conservative per-call reserve, as missions do.
EST_INPUT_TOKENS = 6000
EST_OUTPUT_TOKENS = 1500
OBSERVATION_CAP_CHARS = 4000
TRANSCRIPT_TURNS_KEPT = 40

AGENT_SYSTEM_PROMPT = """You are AgentQon in agent mode: an autonomous agent working in a workspace to achieve a goal. You act by replying with ONE JSON object per turn — no prose outside the JSON.

To use a tool:
{"action": "tool", "tool": "<tool name>", "args": {...}, "thought": "<one short sentence: why this step>"}

When the goal is fully achieved:
{"action": "done", "summary": "<what was accomplished and how you verified it>"}

Tools (all paths are relative to the workspace root; escaping it is refused):
- fs_list {"path": "."} — list a directory.
- fs_read {"path": "file"} — read a file.
- fs_write {"path": "file", "content": "..."} — create/overwrite a file. May need approval.
- fs_edit {"path": "file", "old": "exact text", "new": "replacement"} — replace one exact occurrence. May need approval.
- fs_search {"query": "text", "path": "."} — search file contents (and code symbols when a graph exists).
- shell_run {"argv": ["prog", "arg1"]} — run a command, argv array only (never a shell string). Mutating or network commands may need approval.
- web_read {"url": "https://..."} — fetch and read a web page.

Rules: work in small steps and observe each result before the next. If a tool call is denied or fails, adapt — do not repeat the identical call. Prefer verifying your work (read the file back, run the test). Claim completion only when your tool results support it — a completion claim made over a failed tool result, or after file changes you never verified (no read-back, listing, search, or command run after your last change), is rejected, and repeated claims end the session marked UNVERIFIED. Reply with exactly one JSON object."""

def environment_facts(workspace_root: str) -> str:
    """Host facts stated to the agent in its system context, derived
    from the machine the session actually runs on — never hardcoded.

    Live evidence for why this exists: the first live rounds' model
    burned its steps on shell_run ["python", ...] because this host
    has no `python` command, only `python3` — a fact the harness knew
    (it executed the command) but never told the agent. Real
    harnesses state the environment up front: OS, workspace, which
    interpreters truly exist, and how shell execution really works."""
    os_fact = f"{platform.system()} {platform.release()}".strip() \
        or "unknown OS"
    py3 = shutil.which("python3")
    py = shutil.which("python")
    if py3 and py:
        python_fact = (f"python is available as: python3 ({py3}) and "
                       f"python ({py})")
    elif py3:
        python_fact = (f"python is available as: python3 ({py3}) "
                       f"(no 'python' command on this host)")
    elif py:
        python_fact = (f"python is available as: python ({py}) "
                       f"(no 'python3' command on this host)")
    else:
        python_fact = ("no python interpreter ('python3' or 'python') "
                       "exists on this host")
    return "\n".join([
        "Environment (host facts, derived when this session started):",
        f"- Operating system: {os_fact}.",
        f"- Workspace root: {workspace_root} — tool paths are relative "
        "to it and shell_run commands start there.",
        f"- {python_fact}.",
        "- shell_run is non-interactive argv execution: the argv array "
        "is executed directly with NO shell — no pipes, redirects, "
        "globs, quoting, or variable expansion — and a command cannot "
        "receive interactive input.",
    ])


# factory(tenant_id, on_rate_limited) ->
#   (gateway_client, model_id, catalog_entry, connection_dict)
GatewayFactory = Callable[..., tuple]


class ModelNotConnected(Exception):
    """No real model is connected for this tenant (mirrors the
    orchestrator's exception of the same name; the API maps it to
    the honest 503 model_not_connected envelope)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "model_not_connected"


def orchestrator_gateway_factory(orchestrator) -> GatewayFactory:
    """Production model resolution for agent sessions: exactly the
    missions/chat resolution (tenant BYOK → platform via
    resolve_state), with the BYOK pick made lesson-aware (R2): a
    model with repeated recent failures yields to a clean
    alternative when one exists."""
    store, settings = orchestrator.store, orchestrator.settings

    def factory(tenant_id: str, on_rate_limited=None):
        state = resolve_state(settings, store, tenant_id)
        if not state.connected:
            raise ModelNotConnected(state.message)
        if state.source == "byok":
            active = {c["id"]: c for c in store.list_connections(tenant_id)
                      if c.get("state") == "active"}
            entries = [(e, active[e.get("connection_id")])
                       for e in store.list_catalog(tenant_id)
                       if e.get("connection_id") in active]
            entry, conn = evolve.pick_with_lessons(
                settings, store, tenant_id, entries)
            conn = {**conn, "tenant_id": tenant_id}
        else:
            conn, entry = orchestrator._platform_connection(
                base_url=state.base_url, model=state.model)
            conn = {**conn, "tenant_id": tenant_id}
        gateway = orchestrator._gateway_for(
            conn, on_rate_limited=on_rate_limited)
        return gateway, entry.get("model_id") or state.model, entry, conn

    return factory


def parse_action(data: Any) -> Optional[dict]:
    """Defensive parse of one model reply into an action.

    Accepts the strict schema; recovers the audit-proven alias shapes
    (a bare {"tool": ...} object, completion declared under content
    keys). Returns {"kind": "tool", ...} / {"kind": "done", ...} or
    None when nothing usable is present."""
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    tool = data.get("tool") or data.get("tool_name") or data.get("name")
    if action in ("done", "complete", "completed", "finish", "finished",
                  "final"):
        summary = data.get("summary") or data.get("result") or ""
        return {"kind": "done", "summary": str(summary)}
    if action == "tool" or (tool and action not in ("done",)):
        if not tool:
            return None
        args = data.get("args")
        if not isinstance(args, dict):
            args = data.get("arguments") or data.get("input") or {}
        if not isinstance(args, dict):
            args = {}
        return {"kind": "tool", "tool": str(tool), "args": args,
                "thought": str(data.get("thought") or "")}
    if action == "" and not tool:
        # Persona-schema recovery: a substantive reply with no action
        # envelope — if it carries the work product in a content key,
        # the model means "here is the finished answer".
        for key in ("summary", "answer", "result", "content", "response",
                    "message", "deliverable"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return {"kind": "done", "summary": value.strip()}
    return None


class AgentRunner:
    """Drives agent sessions for one store. gateway_factory resolves
    (client, model, entry, conn) per tenant; usage_recorder, when
    given, is called (tenant_id, session_id, usage, label) after each
    model call with the call's costed usage."""

    def __init__(self, store, settings: Settings, gateway_factory: GatewayFactory,
                 usage_recorder: Optional[Callable] = None):
        self.store = store
        self.settings = settings
        self.gateway_factory = gateway_factory
        self.usage_recorder = usage_recorder

    # ------------------------------------------------------------ CRUD
    def create_session(self, tenant_id: str, created_by: str, goal: str,
                       workspace_root: Optional[str] = None,
                       auto_approve_rw: bool = False,
                       max_steps: int = DEFAULT_MAX_STEPS,
                       budget_cap_usd: Optional[float] = None) -> dict:
        session_id = new_id("ags")
        if workspace_root:
            root = Path(workspace_root).expanduser().resolve()
            # Audit F3 (mode-aware): in accounts/hosted mode the
            # caller may NOT pick an arbitrary root — the fs tools
            # confine to whatever root the caller names, so an
            # unrestricted choice reads the whole server (the data
            # dir's vault.key + DB, other tenants' workspaces). The
            # root must sit inside THIS tenant's own workspace area.
            # In local single-user mode an arbitrary root stays the
            # product's purpose (the agent works on the user's own
            # project); session confinement to the chosen root still
            # applies there during the run.
            if self.settings.auth_mode == "accounts":
                area = (self.settings.data_dir / "agent-workspaces"
                        / tenant_id).resolve()
                if root != area and area not in root.parents:
                    raise ValueError(
                        "in accounts mode the workspace root must be "
                        "inside the tenant's own workspace area "
                        f"({area})")
        else:
            root = (self.settings.data_dir / "agent-workspaces"
                    / session_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        record = {
            "id": session_id, "tenant_id": tenant_id,
            "created_by": created_by, "goal": str(goal),
            "workspace_root": str(root),
            "status": "created",
            "auto_approve_rw": bool(auto_approve_rw),
            "max_steps": max(1, min(int(max_steps), MAX_STEPS_LIMIT)),
            "budget_cap_usd": (float(budget_cap_usd)
                               if budget_cap_usd else MISSION_BUDGET_CEILING_USD),
            "steps_used": 0, "spent_usd": 0.0,
            "skills": None,  # v22.1: retrieved library skill ids (lazy)
            "transcript": [], "pending_action": None,
            "completion_rejections": 0, "completion": None,
            "model": None, "connection_id": None, "source": None,
            "summary": "", "failure": None,
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        self.store.put_agent_session(tenant_id, record)
        self._event(tenant_id, record, "AgentSessionCreated",
                    {"goal": record["goal"], "workspace_root": str(root),
                     "max_steps": record["max_steps"],
                     "auto_approve_rw": record["auto_approve_rw"]})
        return record

    def get_session(self, tenant_id: str, session_id: str) -> Optional[dict]:
        return self.store.get_agent_session(tenant_id, session_id)

    def list_sessions(self, tenant_id: str) -> list[dict]:
        return self.store.list_agent_sessions(tenant_id)

    def cancel_session(self, tenant_id: str, session_id: str) -> dict:
        record = self._require(tenant_id, session_id)
        if record["status"] in ("completed", "failed", "cancelled"):
            return record
        record["status"] = "cancelled"
        record["pending_action"] = None
        self._save(record)
        self._event(tenant_id, record, "AgentCancelled",
                    {"steps_used": record["steps_used"]})
        self._lesson(record, outcome="cancelled", category="other",
                     cause="cancelled by the user")
        return record

    # ------------------------------------------------------------ events
    def _event(self, tenant_id: str, record: dict, event_type: str,
               payload: dict) -> None:
        self.store.put_event(tenant_id, event_envelope(
            tenant_id, "agent_session", record["id"], event_type, payload))

    def list_events(self, tenant_id: str, session_id: str,
                    after: Optional[str] = None) -> list[dict]:
        return self.store.list_events(tenant_id, session_id, after=after)

    # ------------------------------------------------------------ helpers
    def _require(self, tenant_id: str, session_id: str) -> dict:
        record = self.store.get_agent_session(tenant_id, session_id)
        if record is None:
            raise KeyError("agent session not found")
        return record

    def _save(self, record: dict) -> None:
        record["updated_at"] = now_iso()
        self.store.put_agent_session(record["tenant_id"], record)

    def _lesson(self, record: dict, *, outcome: str, category: str,
                cause: str) -> None:
        """R2: every terminal session writes its lesson. Best-effort —
        a lesson failure must never break the session's own ending."""
        try:
            subject = record.get("model") or record["id"]
            evolve.record_lesson(
                self.store, record["tenant_id"],
                subject_type="model" if record.get("model") else "session",
                subject=subject, outcome=outcome, category=category,
                cause=cause, evidence_id=record["id"],
                source_kind="agent_session", source_id=record["id"])
        except Exception:
            pass
        # v22.1 (S3): every skill injected into the session earns the
        # run's outcome too — a per-skill lesson with the SAME
        # outcome/category, so the ledger learns which skills ride
        # along on successes and which on failures.
        for skill_id in record.get("skills") or []:
            try:
                evolve.record_lesson(
                    self.store, record["tenant_id"],
                    subject_type="skill", subject=skill_id,
                    outcome=outcome, category=category,
                    cause=f"skill injected into agent session: {cause}",
                    evidence_id=record["id"],
                    source_kind="agent_session", source_id=record["id"])
            except Exception:
                pass

    def _skills_for_session(self, record: dict) -> str:
        """v22.1 (S3): retrieve the top-matching library skills for
        the session's goal — ONCE per session (the ids ride the
        record, so a re-drive after an approval reuses the same set
        and terminal lessons can name them) — and render the
        injection section. Emits SkillInjected on first retrieval.
        Strictly best-effort: any failure means no section, never a
        broken session."""
        ids = record.get("skills")
        if ids is None:
            ids = []
            try:
                from .skills import retrieve_for_task
                found = retrieve_for_task(
                    self.store, record["tenant_id"], record["goal"],
                    self.settings)
                ids = [s["id"] for s in found]
            except Exception:
                ids = []
            record["skills"] = ids
            if ids:
                self._event(record["tenant_id"], record, "SkillInjected",
                            {"skills": ids, "source": "skill_library"})
        if not ids:
            return ""
        try:
            from .skills import format_skills_section, get_library
            lib = get_library(self.settings)
            found = [lib.get(sid) for sid in ids]
            return format_skills_section([f for f in found if f])
        except Exception:
            return ""

    def _fail(self, record: dict, category: str, cause: str) -> dict:
        record["status"] = "failed"
        record["failure"] = {"category": category, "cause": cause}
        record["pending_action"] = None
        self._save(record)
        self._event(record["tenant_id"], record, "AgentFailed",
                    {"category": category, "cause": cause,
                     "steps_used": record["steps_used"]})
        self._lesson(record, outcome="failed", category=category, cause=cause)
        return record

    def _complete(self, record: dict, summary: str, *,
                  verified: bool = True,
                  unresolved_failure: Optional[dict] = None,
                  unresolved_category: str = "tool_error") -> dict:
        record["status"] = "completed"
        record["summary"] = summary
        record["pending_action"] = None
        completion = {"claimed_complete": True, "verified": verified}
        payload = {"summary": summary[:1000],
                   "steps_used": record["steps_used"],
                   "claimed_complete": True, "verified": verified}
        if not verified and unresolved_failure is not None:
            completion["unresolved_failure"] = unresolved_failure
            payload["unresolved_failure"] = unresolved_failure
        record["completion"] = completion
        self._save(record)
        self._event(record["tenant_id"], record, "AgentCompleted", payload)
        if verified:
            self._lesson(record, outcome="completed", category="none",
                         cause="goal achieved: " + summary[:200])
        else:
            # Honest ending: the model claimed success over tool
            # evidence that contradicts it. The lesson records the
            # failure — never the claim (R2 evidence discipline).
            tool = (unresolved_failure or {}).get("tool", "a tool")
            error = (unresolved_failure or {}).get("error", "failed")
            if tool == "verification":
                cause = ("agent claimed completion but never verified "
                         f"its changes: {error}")
            else:
                cause = (f"agent claimed completion but the last tool "
                         f"result failed and was never fixed: {tool} — "
                         f"{error}")
            self._lesson(record, outcome="failed",
                         category=unresolved_category, cause=cause)
        return record

    def _unresolved_failure(self, record: dict) -> Optional[dict]:
        """The most recent tool result, if it was a failure with no
        successful tool call after it. Denials and format corrections
        carry no `ok` flag and never count as tool results; a later
        success supersedes an earlier failure (a genuine fix)."""
        for turn in reversed(record["transcript"]):
            if turn.get("role") != "observation" or "ok" not in turn:
                continue
            if turn["ok"]:
                return None
            return {"tool": turn.get("tool", "unknown tool"),
                    "error": turn.get("error")
                    or _summarize_result_text(turn.get("text", "")),
                    "call_id": turn.get("call_id")}
        return None

    def _unverified_changes(self, record: dict) -> Optional[dict]:
        """The verify-after-build gap: a successful mutation
        (fs_write/fs_edit with ok:true) after which NO observation
        (fs_read/fs_list/fs_search/shell_run/web_read, any result —
        the agent looked) ever ran. Only ok:true mutations open the
        gap — a failed write changed nothing; any later observation
        closes it until the next successful mutation reopens it.
        Denials and format corrections carry no `ok` flag and count
        as neither."""
        gap_open = False
        gap_call_id = None
        for turn in record["transcript"]:
            if turn.get("role") != "observation" or "ok" not in turn:
                continue
            tool = turn.get("tool")
            if tool in MUTATION_TOOLS:
                if turn["ok"]:
                    gap_open, gap_call_id = True, turn.get("call_id")
            elif tool in OBSERVATION_TOOLS:
                gap_open, gap_call_id = False, None
        if not gap_open:
            return None
        return {"tool": "verification",
                "error": VERIFICATION_MISSING_ERROR,
                "call_id": gap_call_id}

    def _call_cost(self, entry: dict, usage: dict) -> tuple[float, bool]:
        """(cost_usd, known). Known catalog price → price the actual
        tokens; unknown → the conservative per-call reserve amount,
        flagged unknown so accounting never presents it as exact."""
        price_in, price_out = entry.get("price_in"), entry.get("price_out")
        if entry.get("price_status") == "known" and price_in is not None \
                and price_out is not None:
            return round(usage.get("input_tokens", 0) / 1_000_000 * float(price_in)
                         + usage.get("output_tokens", 0) / 1_000_000 * float(price_out), 6), True
        return self.settings.reserve_unknown_call_usd, False

    def _reserve_estimate(self, entry: dict) -> float:
        price_in, price_out = entry.get("price_in"), entry.get("price_out")
        if entry.get("price_status") == "known" and price_in is not None \
                and price_out is not None:
            return (EST_INPUT_TOKENS / 1_000_000 * float(price_in)
                    + EST_OUTPUT_TOKENS / 1_000_000 * float(price_out))
        return self.settings.reserve_unknown_call_usd

    # ------------------------------------------------------------- prompt
    def _render_prompt(self, record: dict,
                       reference_section: str = "") -> str:
        lines = [f"GOAL: {record['goal']}", "",
                 "Workspace root (all tool paths are relative to it): "
                 f"{record['workspace_root']}",
                 f"Steps used: {record['steps_used']} of {record['max_steps']}",
                 ""]
        if reference_section:
            # Audit F6: retrieved skill content rides the USER turn
            # as delimited reference data (see format_skills_section's
            # boundary text) — never the system prompt, where
            # third-party text would read as standing instructions.
            lines += [reference_section, ""]
        transcript = record["transcript"][-TRANSCRIPT_TURNS_KEPT:]
        if not transcript:
            lines.append("No actions taken yet. Propose the first action.")
        for turn in transcript:
            if turn["role"] == "action":
                lines.append(
                    f"You proposed: {json.dumps(turn['action'], default=str)[:600]}")
            else:
                lines.append(f"Observation: {turn['text']}")
        lines.append("")
        lines.append("Reply with ONE JSON action object now.")
        return "\n".join(lines)

    # ------------------------------------------------------------ the loop
    async def run(self, tenant_id: str, session_id: str) -> dict:
        """Drive the loop until it completes, fails, or parks on an
        approval. Resumable: the transcript lives on the record."""
        record = self._require(tenant_id, session_id)
        if record["status"] in ("completed", "failed", "cancelled"):
            return record
        if record["status"] == "awaiting_approval":
            return record  # parked — approve/deny drives it forward
        if record["status"] == "running":
            # Audit F5: a live drive already owns this session — a
            # second concurrent run() must not double-drive it (two
            # loops interleaving steps on one record). A session
            # orphaned in "running" by a process restart is recovered
            # via cancel, which works from any non-terminal state.
            return record
        record["status"] = "running"
        self._save(record)
        try:
            return await self._drive(record)
        except ModelNotConnected:
            # No model at all: the session never started — restore
            # the pre-run status so it can be run again once a model
            # is connected, and let the API answer the honest 503.
            record["status"] = "created"
            self._save(record)
            raise

    async def resolve_approval(self, tenant_id: str, session_id: str,
                               approve: bool) -> dict:
        record = self._require(tenant_id, session_id)
        pending = record.get("pending_action")
        if record["status"] != "awaiting_approval" or not pending:
            raise ValueError("session is not awaiting an approval")
        # Audit F5 — consume the approval ATOMICALLY, before any
        # await: the parked action is claimed on the stored record
        # (pending cleared + status flipped + saved) in one
        # synchronous segment, so a concurrent second resolve —
        # double-click, client retry, two tabs — re-reads a record
        # whose action is already consumed and gets the
        # already-consumed answer above ("not awaiting an approval",
        # HTTP 409 at the API). The action the user approved once
        # executes exactly once.
        record["pending_action"] = None
        record["status"] = "running"
        self._save(record)
        self._event(tenant_id, record, "ApprovalResolved",
                    {"call_id": pending["call_id"],
                     "decision": "approve" if approve else "deny"})
        if approve:
            result = await self._execute_tool(record, pending)
            self._append_observation(record, pending, result)
        else:
            record["transcript"].append({
                "role": "observation",
                "text": (f"DENIED by the user: {pending['tool']} "
                         f"{json.dumps(pending['args'], default=str)[:300]} "
                         "was NOT executed. Do not retry the identical "
                         "call; adapt your plan or finish without it."),
                "call_id": pending["call_id"], "denied": True})
            try:
                evolve.record_lesson(
                    self.store, tenant_id, subject_type="tool",
                    subject=pending["tool"], outcome="failed",
                    category="approval_denied",
                    cause=f"user denied {pending['tool']} in agent session",
                    evidence_id=session_id, source_kind="agent_session",
                    source_id=session_id)
            except Exception:
                pass
        record["status"] = "running"
        self._save(record)
        return await self._drive(record)

    async def _drive(self, record: dict) -> dict:
        tenant_id = record["tenant_id"]

        def _rate_limited(info: dict) -> None:
            self._event(tenant_id, record, "ProviderRateLimited", info)

        try:
            gateway, model, entry, conn = self.gateway_factory(
                tenant_id, on_rate_limited=_rate_limited)
        except ModelNotConnected:
            raise
        except Exception as exc:
            return self._fail(record, evolve.classify_failure_text(str(exc)),
                              str(exc)[:300])
        record["model"] = model
        record["connection_id"] = conn.get("id")
        record["source"] = conn.get("kind")
        # v22.1 (S3): the library skills retrieved for this goal ride
        # the record (ids only) and join the system context below.
        skills_section = self._skills_for_session(record)
        self._save(record)
        # The system context for this drive: the standing protocol
        # plus the host facts derived at session start (OS, workspace,
        # the interpreters that truly exist, argv-only shell). The
        # retrieved skills section is NOT part of it (audit F6): it
        # is third-party text and rides the user turn as delimited
        # reference data via _render_prompt.
        system_prompt = (AGENT_SYSTEM_PROMPT + "\n\n"
                         + environment_facts(record["workspace_root"]))

        format_failures = 0
        while True:
            # Re-read at every step boundary: a cancel from another
            # request lands here, not mid-call.
            fresh = self.store.get_agent_session(tenant_id, record["id"])
            if fresh and fresh["status"] == "cancelled":
                record.update(fresh)
                self._lesson(record, outcome="cancelled", category="other",
                             cause="cancelled by the user")
                return record
            if fresh:
                record = fresh
            if record["steps_used"] >= record["max_steps"]:
                return self._fail(
                    record, "planning",
                    f"step cap reached ({record['max_steps']} steps) "
                    "without the agent declaring completion")
            reserve = self._reserve_estimate(entry)
            if record["spent_usd"] + reserve > record["budget_cap_usd"]:
                return self._fail(
                    record, "other",
                    f"budget ceiling reached: spent "
                    f"${record['spent_usd']:.4f} of "
                    f"${record['budget_cap_usd']:.2f} cap")
            record["steps_used"] += 1
            step = record["steps_used"]
            try:
                data, usage = await gateway.chat_json(
                    model, system_prompt,
                    self._render_prompt(record, skills_section))
            except GatewayError as exc:
                self._save(record)
                return self._fail(record, evolve.classify_gateway_error(exc),
                                  str(exc)[:300])
            except Exception as exc:
                self._save(record)
                return self._fail(record, evolve.classify_failure_text(str(exc)),
                                  str(exc)[:300])
            cost, known = self._call_cost(entry, usage)
            record["spent_usd"] = round(record["spent_usd"] + cost, 6)
            if self.usage_recorder is not None:
                try:
                    self.usage_recorder(
                        tenant_id, record["id"],
                        {**usage, **({"cost_usd": cost} if known else {})},
                        f"agent step {step} via {model}")
                except Exception:
                    pass  # bookkeeping never breaks the loop
            action = parse_action(data)
            if action is None:
                format_failures += 1
                record["transcript"].append({
                    "role": "observation",
                    "text": ("Your last reply was not a usable action "
                             "object. Reply with exactly one JSON object: "
                             '{"action": "tool", "tool": "...", "args": {...}} '
                             'or {"action": "done", "summary": "..."}.'),
                    "format_error": True})
                self._save(record)
                if format_failures >= MAX_FORMAT_FAILURES:
                    return self._fail(
                        record, "output_format",
                        f"model produced {format_failures} unusable "
                        "action replies in a row")
                continue
            format_failures = 0
            if action.get("thought"):
                self._event(tenant_id, record, "AgentStep",
                            {"step": step, "thought": action["thought"]})
            if action["kind"] == "done":
                summary = action["summary"] or \
                    "Goal achieved (no summary given)."
                # Guard 1: the claim contradicts the latest tool
                # evidence (a failure nothing fixed). Guard 2: the
                # agent changed files but never checked the result.
                # Both share the one rejection budget.
                unresolved = self._unresolved_failure(record)
                unresolved_category = "tool_error"
                if unresolved is None:
                    unresolved = self._unverified_changes(record)
                    unresolved_category = "planning"
                if unresolved is None:
                    return self._complete(record, summary)
                rejections = int(record.get("completion_rejections") or 0)
                if rejections >= MAX_COMPLETION_REJECTIONS:
                    # The claim was pushed back twice and the gap is
                    # still open: end the session, flagged UNVERIFIED
                    # — the claim is recorded, the success is not.
                    return self._complete(
                        record, summary, verified=False,
                        unresolved_failure=unresolved,
                        unresolved_category=unresolved_category)
                record["completion_rejections"] = rejections + 1
                if unresolved["tool"] == "verification":
                    rejection_text = (
                        f"COMPLETION REJECTED ({rejections + 1} of "
                        f"{MAX_COMPLETION_REJECTIONS}): you changed "
                        "files in the workspace but never verified "
                        "the result — after your last successful "
                        "fs_write/fs_edit, no fs_read, fs_list, "
                        "fs_search, shell_run, or web_read checked "
                        "the outcome. Verify your work now: read the "
                        "changed files back, list the workspace, or "
                        "run the relevant command or test, then claim "
                        "completion. Or, if verification is genuinely "
                        "impossible, explain why explicitly in your "
                        "summary — note that a further claim with "
                        "your changes still unverified ends the "
                        "session marked UNVERIFIED, not successful.")
                else:
                    rejection_text = (
                        f"COMPLETION REJECTED ({rejections + 1} of "
                        f"{MAX_COMPLETION_REJECTIONS}): your last tool "
                        f"result FAILED — {unresolved['tool']} returned "
                        f"ok:false: {unresolved['error']}. Your "
                        "completion claim contradicts this tool "
                        "evidence. Fix the problem, then verify with a "
                        "successful tool result before claiming "
                        "completion. Or, if the goal is genuinely met "
                        "despite this failure, explain why explicitly "
                        "in your summary — note that a further claim "
                        "with the failure still unresolved ends the "
                        "session marked UNVERIFIED, not successful.")
                record["transcript"].append({
                    "role": "observation", "text": rejection_text,
                    "completion_rejected": True})
                self._save(record)
                continue
            call = {"call_id": new_id("call"), "tool": action["tool"],
                    "args": action["args"], "step": step}
            record["transcript"].append(
                {"role": "action", "action": action, "call_id": call["call_id"]})
            needs, reason = agent_tools.tool_requires_approval(
                call["tool"], call["args"], record["auto_approve_rw"])
            self._event(tenant_id, record, "ToolCall",
                        {"step": step, "call_id": call["call_id"],
                         "tool": call["tool"], "args": call["args"],
                         "needs_approval": needs})
            if needs:
                record["pending_action"] = {**call, "reason": reason}
                record["status"] = "awaiting_approval"
                self._save(record)
                self._event(tenant_id, record, "ApprovalNeeded",
                            {"call_id": call["call_id"], "tool": call["tool"],
                             "args": call["args"], "reason": reason})
                return record
            result = await self._execute_tool(record, call)
            self._append_observation(record, call, result)
            self._save(record)

    async def _execute_tool(self, record: dict, call: dict) -> dict:
        root = Path(record["workspace_root"])
        # Audit F3/F4 plumbing: hosted (accounts) mode confines
        # shell_run's path-bearing arguments to the workspace, and
        # web_read always goes through the gateway's reach-channel
        # enablement with an audit event on this session's stream
        # (the session's invocation record, mirroring the mission
        # gateway's redacted audit view).
        reach_channels = {c.strip() for c in
                          self.settings.reach_channels.split(",")
                          if c.strip()}

        def _web_audit(audit_args: dict, audit_result: dict) -> None:
            self._event(record["tenant_id"], record, "WebReadAudited",
                        {"tool": "reach.read", "channel": "web",
                         "args": audit_args, "result": audit_result})

        result = await agent_tools.execute_tool(
            call["tool"], call["args"], root=root,
            data_dir=self.settings.data_dir,
            confine_paths=self.settings.auth_mode == "accounts",
            reach_channels=reach_channels,
            web_audit=_web_audit)
        self._event(record["tenant_id"], record, "ToolResult",
                    {"call_id": call["call_id"], "tool": call["tool"],
                     "ok": result.get("ok", False),
                     "summary": _summarize_result(result)})
        return result

    def _append_observation(self, record: dict, call: dict,
                            result: dict) -> None:
        text = json.dumps(result, default=str)
        if len(text) > OBSERVATION_CAP_CHARS:
            text = text[:OBSERVATION_CAP_CHARS] + "…[truncated]"
        turn = {"role": "observation",
                "text": f"{call['tool']} result: {text}",
                "call_id": call["call_id"], "tool": call["tool"],
                "ok": result.get("ok", False)}
        if not turn["ok"]:
            turn["error"] = str(result.get("error", "failed"))[:300]
        record["transcript"].append(turn)


def _summarize_result_text(text: str) -> str:
    """Error excerpt from a stored observation text ("<tool> result:
    {json}") — the fallback for transcripts written before failure
    turns carried their own `error` field."""
    payload = text.split(" result: ", 1)[-1]
    try:
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])[:300]
    except (ValueError, TypeError):
        pass
    return payload[:300] or "failed"


def _summarize_result(result: dict) -> str:
    if not result.get("ok"):
        return str(result.get("error", "failed"))[:300]
    bits = []
    for key in ("path", "bytes", "returncode", "count", "chars"):
        if key in result:
            bits.append(f"{key}={result[key]}")
    return ", ".join(bits) or "ok"
