"""AgentQon — API layer (Ch.18).

Versioned JSON API with tenant authorisation on every route, validation,
idempotency keys on mission creation, correlation IDs, consistent error
envelopes, and the mission UI served at /. Dev auth uses X-Tenant-Id /
X-User-Id headers — replace with real auth before production (Ch.16).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import (BRAIN_KEY_PREFIX, Identity, OIDCVerifier, hash_password,
                   new_brain_key, new_session_token, session_expiry,
                   session_valid, token_hash, verify_password)
from .config import load_settings
from .shield import (assert_secure_startup, boot_self_check, security_log,
                     verify_channel)
from .domain import (MemoryWrite, MissionCreate, StateConflict, content_hash,
                     error_envelope, MISSION_BUDGET_CEILING_USD, new_id)
from .modeltest import ModelSelfTester
from .society import SocietyService
from .orchestrator import Orchestrator
from .registry import SEED_AGENTS, seed_registry
from .sources import SEED_SOURCES
from .store import Store, default_store

WEB_INDEX = Path(__file__).resolve().parent.parent / "apps" / "web" / "index.html"

log = logging.getLogger(__name__)

settings = load_settings()
# Shield (v20.2): the hard startup refusals run before anything is
# built or served — a packaged spawn on dev-header auth without a
# local token, or dev auth on a non-loopback bind, never starts (C1/C2).
assert_secure_startup(settings)
store: Store = default_store()
try:
    from .id_migration import migrate_store_ids
    migrate_store_ids(store)
except Exception:
    log.warning("agent/skill id rename migration failed", exc_info=True)
try:
    boot_self_check(settings)
except Exception:
    log.warning("shield boot self-check failed", exc_info=True)
try:
    from .vault import Vault
    vault = Vault.from_settings(settings, settings.data_dir)
except Exception as exc:
    vault = None  # BYOK connections will refuse calls; platform default still works
    # v21.4: never silent — a vault that fails to load disables every
    # BYOK connection, and "503 with no cause anywhere" cost hours.
    # Boot continues (the platform default still works); BYOK endpoints
    # answer VAULT_UNAVAILABLE and THIS log line carries the reason.
    log.error("credential vault failed to load (%s: %s) — BYOK "
              "connections are unavailable until the vault "
              "configuration is fixed", type(exc).__name__, exc,
              exc_info=True)

oidc_verifier = (OIDCVerifier(settings.oidc_issuer, settings.oidc_client_id)
                 if settings.oidc_issuer and settings.oidc_client_id else None)
orchestrator = Orchestrator(store, settings, vault=vault)
model_tester = ModelSelfTester(store, settings, orchestrator)


def _society_speed_evidence(tenant_id: str, model: str):
    """Sizer evidence from the model self-test registry: the calling
    tenant's latest self-test verdict for the seated model — its
    measured latency when it answered, or the rate-limit flag when
    the provider throttled it. No recent verdict ⇒ (None, False) and
    the sizer falls back to its conservative default pace."""
    try:
        run_id = model_tester._by_tenant.get(tenant_id)
        run = model_tester._runs.get(run_id or "")
        if run is None:
            return None, False
        for result in run.results:
            if result.get("model") == model:
                if result.get("ok"):
                    return float(result.get("latency_ms") or 0) or None, False
                return None, result.get("error_code") == "rate_limited"
    except Exception:
        pass
    return None, False


society_service = SocietyService(store, settings, orchestrator,
                                 speed_evidence=_society_speed_evidence)
_seeded_tenants: set[str] = set()

app = FastAPI(title="AgentQon", version="1.0.0",
              description="Collaborative, evidence-driven agent platform — Phase 1 vertical slice")
# v21.4.2: gzip every JSON response of any size — the one-time full
# roster download (several MB) and the larger list payloads shrink
# several-fold on the wire; small responses pass through untouched.
app.add_middleware(GZipMiddleware, minimum_size=1024)


def ensure_tenant(tenant_id: str) -> None:
    if tenant_id not in _seeded_tenants:
        seed_registry(store, tenant_id)
        for s in SEED_SOURCES:
            store.put_source(tenant_id, s.model_dump())
        _seed_fresh_tenant(tenant_id)
        _seeded_tenants.add(tenant_id)


def _seed_fresh_tenant(tenant_id: str) -> None:
    """v20.1: a fresh workspace ships with the full roster, not just
    the seed agents — the bundled agency roster and the bundled
    subagents mega-library are imported automatically on first touch.

    Runs only while the registry holds nothing beyond the seed agents
    (the count guard makes it a no-op on every later boot; the imports
    themselves are upserts, and manual re-import via
    /v1/agents/import-agency and /v1/agents/import-library is
    unchanged). Each roster is failure-isolated: a broken or absent
    bundle logs a warning and never breaks the request or boot.
    """
    try:
        if len(store.list_agents(tenant_id)) > len(SEED_AGENTS):
            return
    except Exception:
        return
    bundled = Path(__file__).parent / "data"
    if (bundled / "agency-agents").is_dir():
        try:
            from .agency import import_roster
            with store.bulk():
                import_roster(store, tenant_id)
        except Exception:
            log.warning("auto-seed: agency roster import failed for "
                        "tenant %s", tenant_id, exc_info=True)
    if (bundled / "subagents-library").is_dir():
        try:
            from .library import import_library
            with store.bulk():
                import_library(store, tenant_id)
        except Exception:
            log.warning("auto-seed: subagents library import failed "
                        "for tenant %s", tenant_id, exc_info=True)


def _authenticated_user(request: Request):
    """Accounts mode: resolve the Bearer credential to a user row.

    Accepts session tokens (omk_…) and, when configured, OIDC JWTs
    (verified against the issuer JWKS; users are provisioned just in
    time from the email claim). Returns None for anything invalid.
    """
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer "):
        return None
    token = authz[len("Bearer "):].strip()
    if token.startswith("omk_"):
        sess = store.get_session(token_hash(token))
        if sess and session_valid(sess):
            return store.get_user(sess["user_id"])
        return None
    if token.count(".") == 2 and oidc_verifier is not None:
        claims = oidc_verifier.verify(token)
        if claims:
            email = (claims.get("email") or "").lower()
            if email:
                row = store.get_user_by_email(email)
                if row is None:
                    import secrets as _secrets
                    row = store.create_user(
                        email, hash_password(_secrets.token_urlsafe(32)),
                        claims.get("name", "")) or store.get_user_by_email(email)
                return row
    return None


def identity(request: Request) -> Identity:
    """The one identity path every business route goes through.

    Dev mode: header identity, role owner (local development only).
    Accounts mode: a valid credential AND a tenant membership are both
    required; a tenant the user does not belong to is a 404, so tenant
    existence is never leaked.
    """
    from fastapi import HTTPException
    if settings.auth_mode != "accounts":
        t = request.headers.get("x-tenant-id", "org_demo")
        ensure_tenant(t)
        return Identity(request.headers.get("x-user-id", "user_demo"), "",
                        t, "owner", "dev-header")
    row = _authenticated_user(request)
    if row is None:
        raise HTTPException(401, "authentication required")
    t = request.headers.get("x-tenant-id", "")
    membership = store.get_membership(t, row["id"]) if t else None
    if membership is None:
        raise HTTPException(404, "tenant not found")
    ensure_tenant(t)
    return Identity(row["id"], row["email"], t, membership["role"],
                    "session" if request.headers.get("authorization", "").startswith("Bearer omk_") else "oidc")


def tenant(request: Request) -> str:
    return identity(request).tenant_id


def user(request: Request) -> str:
    return identity(request).user_id


def guard(request: Request, min_role: str) -> Identity:
    from fastapi import HTTPException
    ident = identity(request)
    if not ident.require(min_role):
        raise HTTPException(403, f"this action requires the {min_role} role")
    return ident


def rate_guard(key: str, limit: int) -> None:
    from fastapi import HTTPException
    allowed, retry_after = store.rate_hit(key, limit)
    if not allowed:
        raise HTTPException(429, "rate limit exceeded",
                            headers={"Retry-After": str(retry_after)})


def _client_ip(request: Request) -> str:
    """The direct peer address, for per-IP rate limits (audit F12).
    X-Forwarded-For is deliberately NOT trusted: a client can spoof
    it to dodge the brake. Behind a proxy the peer is the proxy —
    the honest aggregation point available to the app."""
    return request.client.host if request.client else "unknown"


def corr(request: Request) -> str:
    return getattr(request.state, "correlation_id", new_id("corr"))


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    request.state.correlation_id = request.headers.get("x-correlation-id") or new_id("corr")
    response = await call_next(request)
    response.headers["x-correlation-id"] = request.state.correlation_id
    return response


# ------------------------------------------------------------ Shield (v20.2)
# The packaged local harness is a loopback-only, credential-gated
# surface (audit C1 + spec §B1). Everything below is inert unless the
# server was spawned with the per-install credentials (the CLI always
# passes them; hosted accounts deployments and repo-dev runs do not):
#   - packaged: exact Host allowlist (loopback names only) on every
#     route except the webhooks, whose authenticity is their own
#     signature, and cross-site Origin/Referer refusal on mutations;
#   - channel: an X-OmniMind-Sig signature, when presented, must
#     verify (ts window + nonce replay cache) — invalid = 403, never
#     a downgrade to another credential;
#   - local token: when configured, every route except GET /health,
#     GET /v1/auth/config, GET / (UI shell) and the webhooks requires
#     the X-OmniMind-Local-Token header or a verified signature. The
#     one exception is POST /v1/brain (v21.2), which carries its own
#     scoped brain key instead — validated by the endpoint, never by
#     anything the key could be replayed against elsewhere.
_SHIELD_EXEMPT_EXACT = {"/health", "/v1/auth/config", "/"}
_SHIELD_WEBHOOK_PREFIXES = ("/v1/channels/telegram/webhook",
                            "/v1/channels/whatsapp/webhook")
# v21.2: the brain endpoint authenticates with its own scoped key
# (Authorization: Bearer aqb_…), so a request carrying that credential
# shape is not ALSO required to present the local token at the Shield
# gate — the endpoint validates the key against its hash and refuses
# anything invalid with 401. Everything else about Shield still
# applies to /v1/brain: the Host allowlist, the cross-site Origin
# refusal, and the presented-signature hard refusal above.
_BRAIN_PATH = "/v1/brain"
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _shield_host_ok(host_header: str) -> bool:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.split(":")[0]
    return host in {"127.0.0.1", "localhost", "[::1]", "::1"}


def _shield_origin_host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


@app.middleware("http")
async def shield_middleware(request: Request, call_next):
    path = request.url.path
    is_webhook = any(path.startswith(p) for p in _SHIELD_WEBHOOK_PREFIXES)
    if settings.packaged and not is_webhook:
        if not _shield_host_ok(request.headers.get("host", "")):
            security_log(settings, "shield.host_refused", path=path)
            return JSONResponse(status_code=403, content=error_envelope(
                "FORBIDDEN", "host is not on the local allowlist",
                corr(request)))
        if request.method in _STATE_CHANGING:
            origin = request.headers.get("origin") or \
                request.headers.get("referer") or ""
            if origin and not _shield_host_ok(_shield_origin_host(origin)):
                security_log(settings, "shield.origin_refused", path=path)
                return JSONResponse(status_code=403, content=error_envelope(
                    "FORBIDDEN", "cross-site request refused",
                    corr(request)))
    signature_present = "x-omnimind-sig" in request.headers
    signature_ok = False
    if signature_present:
        body = await request.body()
        full_path = path + (f"?{request.url.query}" if request.url.query
                            else "")
        ok, reason = verify_channel(
            settings.channel_secret, request.method, full_path,
            request.headers.get("x-omnimind-ts", ""),
            request.headers.get("x-omnimind-nonce", ""),
            request.headers.get("x-omnimind-sig", ""), body)
        if not ok:
            security_log(settings, "shield.signature_refused",
                         path=path, reason=reason)
            return JSONResponse(status_code=403, content=error_envelope(
                "FORBIDDEN", f"channel signature refused: {reason}",
                corr(request)))
        signature_ok = True
    brain_credential = path == _BRAIN_PATH and request.headers.get(
        "authorization", "").startswith("Bearer " + BRAIN_KEY_PREFIX)
    if settings.local_token and not is_webhook \
            and path not in _SHIELD_EXEMPT_EXACT and not signature_ok \
            and not brain_credential:
        presented = request.headers.get("x-omnimind-local-token", "")
        import hmac as _hmac
        if not presented or not _hmac.compare_digest(
                presented, settings.local_token):
            security_log(settings, "shield.token_refused", path=path)
            return JSONResponse(status_code=401, content=error_envelope(
                "LOCAL_TOKEN_REQUIRED",
                "this install requires its per-install local token",
                corr(request)))
    return await call_next(request)


# ------------------------------------------------------------ error handling
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400,
                        content=error_envelope("VALIDATION_ERROR", str(exc.errors()[:2]), corr(request)))


@app.exception_handler(StateConflict)
async def state_handler(request: Request, exc: StateConflict):
    return JSONResponse(status_code=409,
                        content=error_envelope("STATE_CONFLICT", str(exc), corr(request)))


@app.exception_handler(KeyError)
async def key_handler(request: Request, exc: KeyError):
    return JSONResponse(status_code=404,
                        content=error_envelope("NOT_FOUND", "Resource not found", corr(request)))


def not_found(request: Request) -> JSONResponse:
    # 404 also for resources the tenant cannot access — existence is hidden (Ch.18).
    return JSONResponse(status_code=404,
                        content=error_envelope("NOT_FOUND", "Resource not found", corr(request)))


def _error_detail_visible(request: Request) -> bool:
    """v21.4.2: error DETAIL follows deployment mode. The v21.4.0
    hygiene pass overcorrected for local installs: on a packaged
    single-owner install the caller IS the operator, and hiding the
    reason left them with no way to see why anything failed. So:
    local/dev modes (anything that is not accounts mode) always see
    the detail; hosted accounts mode keeps the sanitized envelope for
    ordinary members, while tenant owners/admins — the operators of
    their workspace — still get it."""
    if settings.auth_mode != "accounts":
        return True
    try:
        return identity(request).role in ("owner", "admin")
    except Exception:
        return False


def internal_error(request: Request, exc: Exception, what: str) -> JSONResponse:
    """v21.4 error hygiene: exception text (DB errors, file paths,
    provider internals) goes to the SERVER log with the traceback;
    the client gets a stable coded envelope and the correlation id to
    quote. v21.4.2: when the deployment mode allows it (see
    _error_detail_visible), the envelope also carries a trimmed
    `detail` — the exception's own message — so the operator of a
    local install can see the actual reason in the UI/CLI."""
    log.exception("%s failed: %s", what, exc)
    detail = None
    if _error_detail_visible(request):
        detail = (str(exc).strip() or exc.__class__.__name__)[:500]
    return JSONResponse(status_code=500, content=error_envelope(
        "INTERNAL_ERROR",
        "An internal error occurred; the failure was logged server-side. "
        "Quote the correlation id when reporting it.", corr(request),
        detail=detail))


# ------------------------------------------------------------ routes
@app.get("/health")
async def health(request: Request):
    # v20: the server reports its MODEL state (a real model, or none) —
    # there is no generation mode to report any more.
    ms = orchestrator.platform_model_state()
    return {"status": "ok", "product": "AgentQon", "phase": "7-simulation-worlds",
            "auth_mode": settings.auth_mode,
            "shield": {"packaged": settings.packaged,
                       "local_token": bool(settings.local_token),
                       # Audit R (X4): `auth_mode: "dev"` names the
                       # IDENTITY mode (dev-header vs accounts) — it does
                       # NOT mean credentials are off. When a per-install
                       # token is configured the middleware enforces it
                       # on every non-exempt path whatever the identity
                       # mode is; say so in so many words so /health can
                       # never be misread as "auth disabled".
                       "token_required": bool(settings.local_token),
                       "channel": bool(settings.channel_secret),
                       "mode": "local-key"},
            "backend": "postgres" if store.pg else "sqlite",
            "model": {"connected": ms.connected, "name": ms.model,
                      "source": ms.source},
            "worker_mode": settings.worker_mode,
            "correlation_id": corr(request)}


@app.get("/v1/model-state")
async def model_state_ep(request: Request):
    # v21.4.2: the TENANT-aware model state — the caller's own BYOK
    # connection counts here, exactly as it does for mission runs
    # (resolve_state). /health stays platform-only (it is the
    # unauthenticated liveness probe and has no tenant), which is why
    # the UI chip and `agentqon doctor` read THIS endpoint instead:
    # a user whose only model is an active BYOK connection used to be
    # told "No model connected" forever while their missions ran fine.
    t = tenant(request)
    return orchestrator.model_state(t).as_dict()


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    history: Optional[list[dict]] = None
    model: Optional[str] = None


@app.post("/v1/chat")
async def chat_ep(request: Request, body: ChatBody):
    """v21.4.3: plain conversation. A greeting or a question is NOT a
    mission — this endpoint makes ONE routed model call and returns
    the reply. No mission row, no queue job, no panel. The model
    resolves exactly as missions resolve it (tenant BYOK → platform);
    no model → the honest 503 model_not_connected envelope. v22: an
    optional `model` pin (a /v1/models model_id) is honored through
    its catalog entry's connection, or answered 422
    model_pin_unresolvable when it cannot be."""
    guard(request, "member")
    t = tenant(request)
    from .gateway_client import GatewayError
    from .orchestrator import ChatModelPinUnresolvable, ModelNotConnected
    try:
        # v22: the optional model pin is passed only when present, so a
        # no-pin request invokes chat_reply exactly as before the pin
        # existed (same call, same behaviour).
        if body.model is None:
            return await orchestrator.chat_reply(t, body.message, body.history)
        return await orchestrator.chat_reply(t, body.message, body.history,
                                             model=body.model)
    except ChatModelPinUnresolvable as exc:
        return JSONResponse(status_code=422, content=error_envelope(
            exc.code, str(exc), corr(request)))
    except ModelNotConnected as exc:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", str(exc), corr(request)))
    except GatewayError as exc:
        return JSONResponse(status_code=502, content=error_envelope(
            exc.category, str(exc), corr(request)))
    except Exception as exc:  # logged in full; client gets a clean coded error
        return internal_error(request, exc, "chat reply")


# ---------------- v22: agent sessions (R1) + evolve report (R2) ----------------
def _agent_runner():
    from .agent_loop import AgentRunner, orchestrator_gateway_factory

    def _record(tenant_id, session_id, usage, label):
        orchestrator._record_inference_usage(
            tenant_id, session_id, usage, label, category="agent")

    return AgentRunner(store, settings,
                       orchestrator_gateway_factory(orchestrator),
                       usage_recorder=_record)


def _session_view(record: dict, full: bool = False) -> dict:
    view = {k: v for k, v in record.items() if k != "transcript"}
    if full:
        view["transcript"] = record.get("transcript", [])
    return view


class AgentSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=4000)
    workspace_root: Optional[str] = None
    auto_approve_rw: bool = False
    max_steps: Optional[int] = Field(default=None, ge=1, le=200)
    budget_cap_usd: Optional[float] = Field(default=None, gt=0, le=1000)


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approve|deny)$")


@app.post("/v1/agent-sessions", status_code=201)
async def create_agent_session(request: Request, body: AgentSessionBody):
    """v22 (R1): an agent session — a goal + a workspace the agent
    works in with permission-gated tools, iterating until done, the
    step cap, or its budget ceiling (app/agent_loop.py)."""
    guard(request, "member")
    t = tenant(request)
    try:
        record = _agent_runner().create_session(
            t, user(request), body.goal, workspace_root=body.workspace_root,
            auto_approve_rw=body.auto_approve_rw,
            max_steps=body.max_steps or 25,
            budget_cap_usd=body.budget_cap_usd)
    except ValueError as exc:
        # e.g. audit F3: a hosted-mode workspace_root outside the
        # tenant's own workspace area.
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    return {"session": _session_view(record)}


@app.get("/v1/agent-sessions")
async def list_agent_sessions(request: Request):
    t = tenant(request)
    return {"sessions": [_session_view(s)
                         for s in _agent_runner().list_sessions(t)]}


@app.get("/v1/agent-sessions/{session_id}")
async def get_agent_session(request: Request, session_id: str):
    t = tenant(request)
    record = _agent_runner().get_session(t, session_id)
    if record is None:
        return not_found(request)
    return {"session": _session_view(record, full=True)}


@app.get("/v1/agent-sessions/{session_id}/events")
async def agent_session_events(request: Request, session_id: str,
                               after: Optional[str] = None):
    t = tenant(request)
    runner = _agent_runner()
    if runner.get_session(t, session_id) is None:
        return not_found(request)
    return {"events": runner.list_events(t, session_id, after)}


@app.post("/v1/agent-sessions/{session_id}/run")
async def run_agent_session(request: Request, session_id: str):
    """Drive the loop until it completes, fails, or parks on an
    approval (status awaiting_approval + pending_action)."""
    guard(request, "member")
    t = tenant(request)
    runner = _agent_runner()
    if runner.get_session(t, session_id) is None:
        return not_found(request)
    from .agent_loop import ModelNotConnected as AgentNoModel
    from .gateway_client import GatewayError
    try:
        record = await runner.run(t, session_id)
    except AgentNoModel as exc:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", str(exc), corr(request)))
    except GatewayError as exc:
        return JSONResponse(status_code=502, content=error_envelope(
            exc.category, str(exc), corr(request)))
    except Exception as exc:
        return internal_error(request, exc, "agent session run")
    return {"session": _session_view(record)}


@app.post("/v1/agent-sessions/{session_id}/approve")
async def approve_agent_session(request: Request, session_id: str,
                                body: ApprovalDecisionBody):
    """Approve → the parked tool call executes and the loop resumes.
    Deny → the agent is told the action was denied and the loop
    continues without it."""
    guard(request, "member")
    t = tenant(request)
    runner = _agent_runner()
    if runner.get_session(t, session_id) is None:
        return not_found(request)
    from .agent_loop import ModelNotConnected as AgentNoModel
    try:
        record = await runner.resolve_approval(
            t, session_id, approve=body.decision == "approve")
    except AgentNoModel as exc:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", str(exc), corr(request)))
    except ValueError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))
    except Exception as exc:
        return internal_error(request, exc, "agent session approval")
    return {"session": _session_view(record)}


@app.post("/v1/agent-sessions/{session_id}/cancel")
async def cancel_agent_session(request: Request, session_id: str):
    guard(request, "member")
    t = tenant(request)
    runner = _agent_runner()
    if runner.get_session(t, session_id) is None:
        return not_found(request)
    return {"session": _session_view(runner.cancel_session(t, session_id))}


@app.get("/v1/evolve/report")
async def evolve_report(request: Request):
    """v22 (R2): the self-evolution report — recent failure patterns,
    the lessons behind them, and the safe tunings (applied runtime
    parameters vs proposals). The report states in its own payload
    that the system never modifies its code, safeguards, or
    permission rules."""
    t = tenant(request)
    from .evolve import build_report
    return build_report(store, t)


# ------------------------------------------------------------ skills (v22.1)
# The skill library (app/skills.py): browse/search the shipped
# community catalog, read one skill with its provenance, and pin
# favourites per tenant. NOTE: /v1/skills/facets is registered
# BEFORE /v1/skills/{skill_id} so "facets" is never captured as an
# id.

def _skill_library():
    from .skills import get_library
    return get_library(settings)


def _skill_view(meta: dict, pins: set) -> dict:
    return {"id": meta["id"], "name": meta.get("name", ""),
            "display_name": meta.get("display_name") or meta.get("name", ""),
            "description": meta.get("description", ""),
            "repo": meta.get("repo", ""), "license": meta.get("license", ""),
            "body_bytes": meta.get("body_bytes", 0),
            # Provenance (v22.2): "boxed" ships in the npm package;
            # "extended" came from the optional Extended Library
            # Pack the user chose to install.
            "pack": meta.get("pack", "boxed"),
            "pinned": meta["id"] in pins}


class SkillPinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pinned: bool


@app.get("/v1/skills/facets")
async def skill_facets(request: Request):
    """Catalog shape: per-repo counts, total skills, unique names."""
    tenant(request)
    return _skill_library().facets()


@app.get("/v1/skills")
async def list_skills(request: Request, q: Optional[str] = None,
                      repo: Optional[str] = None, limit: int = 50,
                      offset: int = 0):
    """Browse (no q) or ranked keyword search (q), optionally
    filtered to one source repo. Paged server-side; `pinned` is the
    CALLING tenant's own favourite flag."""
    t = tenant(request)
    lib = _skill_library()
    pins = store.pinned_skill_ids(t)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    if q:
        items, total = lib.search(q, limit=limit, offset=offset, repo=repo)
    else:
        items, total = lib.browse(repo=repo, limit=limit, offset=offset)
    out = []
    for meta in items:
        view = _skill_view(meta, pins)
        if "score" in meta:
            view["score"] = meta["score"]
        out.append(view)
    return {"skills": out, "total": total}


@app.get("/v1/skills/{skill_id}")
async def get_skill(request: Request, skill_id: str):
    """One skill in full: metadata, provenance (repo/path/url/
    license/truncated) and the body. A community skill is always
    served WITH its source — never as AgentQon-authored content."""
    t = tenant(request)
    lib = _skill_library()
    skill = lib.get(skill_id)
    if skill is None:
        return not_found(request)
    view = _skill_view(skill, store.pinned_skill_ids(t))
    view.update({"body": skill.get("body", ""),
                 "url": skill.get("url", ""), "path": skill.get("path", ""),
                 "truncated": bool(skill.get("truncated", False))})
    return {"skill": view}


@app.post("/v1/skills/{skill_id}/pin")
async def pin_skill(request: Request, skill_id: str, body: SkillPinBody):
    """Pin/unpin a favourite. Pins are per-tenant: they order that
    tenant's retrieval (pinned matches are offered first) and never
    leak into another tenant's views."""
    guard(request, "member")
    t = tenant(request)
    if _skill_library().get_meta(skill_id) is None:
        return not_found(request)
    store.set_skill_pin(t, skill_id, body.pinned, user(request))
    return {"skill_id": skill_id, "pinned": body.pinned}


@app.get("/")
async def index():
    return FileResponse(WEB_INDEX)


# ---------------- Phase 4: accounts, sessions, members ----------------
class AuthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None


@app.get("/v1/auth/config")
async def auth_config():
    return {"auth_mode": settings.auth_mode,
            "oidc_enabled": oidc_verifier is not None}


def _issue_session(user_row: dict) -> str:
    token = new_session_token()
    store.create_session(user_row["id"], token_hash(token),
                         session_expiry(settings.session_ttl_hours))
    return token


@app.post("/v1/auth/register", status_code=201)
async def register(request: Request, body: AuthBody):
    from fastapi import HTTPException
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(400, "a valid email address is required")
    if len(body.password) < 10:
        raise HTTPException(400, "password must be at least 10 characters")
    rate_guard(f"register-ip:{_client_ip(request)}",
               settings.rate_login_ip_per_min)
    rate_guard(f"register:{email}", settings.rate_login_per_min)
    row = store.create_user(email, hash_password(body.password))
    if row is None:
        raise HTTPException(409, "an account with this email already exists")
    tenant_id = (body.tenant_id or "").strip() or new_id("org")
    if store.list_memberships(tenant_id):
        raise HTTPException(409, "tenant id already in use")
    ensure_tenant(tenant_id)
    store.upsert_membership(tenant_id, row["id"], "owner")
    token = _issue_session(row)
    return {"token": token, "user": {"id": row["id"], "email": email},
            "tenant_id": tenant_id, "role": "owner"}


@app.post("/v1/auth/login")
async def login(request: Request, body: AuthBody):
    from fastapi import HTTPException
    email = body.email.strip().lower()
    rate_guard(f"login-ip:{_client_ip(request)}",
               settings.rate_login_ip_per_min)
    rate_guard(f"login:{email}", settings.rate_login_per_min)
    row = store.get_user_by_email(email)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "invalid email or password")
    token = _issue_session(row)
    memberships = [{"tenant_id": m["tenant_id"], "role": m["role"]}
                   for m in store._all(
                       "SELECT * FROM memberships WHERE user_id=?", (row["id"],))]
    return {"token": token, "user": {"id": row["id"], "email": email},
            "memberships": memberships}


@app.post("/v1/auth/logout")
async def logout(request: Request):
    authz = request.headers.get("authorization", "")
    if authz.startswith("Bearer "):
        store.revoke_session(token_hash(authz[len("Bearer "):].strip()))
    return {"logged_out": True}


@app.get("/v1/auth/me")
async def me(request: Request):
    from fastapi import HTTPException
    if settings.auth_mode != "accounts":
        return {"mode": "dev", "user_id": user(request), "tenant_id": tenant(request),
                "role": "owner"}
    row = _authenticated_user(request)
    if row is None:
        raise HTTPException(401, "authentication required")
    memberships = [{"tenant_id": m["tenant_id"], "role": m["role"]}
                   for m in store._all(
                       "SELECT * FROM memberships WHERE user_id=?", (row["id"],))]
    return {"mode": "accounts", "user_id": row["id"], "email": row["email"],
            "memberships": memberships}


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(viewer|member|admin|owner)$")


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(viewer|member|admin|owner)$")


@app.get("/v1/tenants/members")
async def list_members(request: Request):
    identity(request)  # any member of the tenant may see the roster
    return {"members": store.list_memberships(tenant(request))}


@app.post("/v1/tenants/members", status_code=201)
async def add_member(request: Request, body: MemberBody):
    from fastapi import HTTPException
    ident = guard(request, "admin")
    if body.role == "owner" and ident.role != "owner":
        raise HTTPException(403, "only an owner can grant the owner role")
    target = store.get_user_by_email(body.email.strip().lower())
    if target is None:
        raise HTTPException(404, "no account with that email — they must register first")
    m = store.upsert_membership(ident.tenant_id, target["id"], body.role)
    store.audit(ident.tenant_id, ident.user_id, "member.added", "user",
                    target["id"], {"role": body.role})
    return {"member": m}


@app.patch("/v1/tenants/members/{member_id}")
async def change_member_role(request: Request, member_id: str, body: RoleBody):
    from fastapi import HTTPException
    ident = guard(request, "admin")
    current = store.get_membership(ident.tenant_id, member_id)
    if current is None:
        raise HTTPException(404, "member not found")
    if (current["role"] == "owner" or body.role == "owner") and ident.role != "owner":
        raise HTTPException(403, "only an owner can change owner roles")
    m = store.upsert_membership(ident.tenant_id, member_id, body.role)
    store.audit(ident.tenant_id, ident.user_id, "member.role_changed", "user",
                    member_id, {"role": body.role})
    return {"member": m}


@app.delete("/v1/tenants/members/{member_id}")
async def remove_member(request: Request, member_id: str):
    from fastapi import HTTPException
    ident = guard(request, "admin")
    current = store.get_membership(ident.tenant_id, member_id)
    if current is None:
        raise HTTPException(404, "member not found")
    if current["role"] == "owner":
        if ident.role != "owner":
            raise HTTPException(403, "only an owner can remove an owner")
        owners = [m for m in store.list_memberships(ident.tenant_id)
                  if m["role"] == "owner"]
        if len(owners) <= 1:
            raise HTTPException(409, "a tenant must keep at least one owner")
    store.remove_membership(ident.tenant_id, member_id)
    store.audit(ident.tenant_id, ident.user_id, "member.removed", "user",
                    member_id, {})
    return {"removed": True}


@app.post("/v1/missions", status_code=201)
async def create_mission(request: Request, body: MissionCreate,
                         idempotency_key: str = Header(min_length=8, max_length=128)):
    guard(request, "member")
    t, u = tenant(request), user(request)
    req_hash = content_hash(body.model_dump())
    existing = store.find_idempotent(t, idempotency_key)
    if existing:
        if existing["request_hash"] != req_hash:
            return JSONResponse(status_code=409, content=error_envelope(
                "STATE_CONFLICT", "Idempotency key already used with a different request", corr(request)))
        mission = store.get_mission(t, existing["mission_id"])
        return {"mission_id": mission["id"], "status": mission["status"], "idempotent_replay": True}
    if body.project_id and store.get_project(t, body.project_id) is None:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "project_id does not belong to this tenant", corr(request)))
    # v12: resolve the task type once, at creation — an explicit contract
    # value wins; otherwise the deterministic classifier reads the
    # objective. Stored on the contract so every later stage (planner,
    # adapters, UI) sees the same answer.
    if body.contract.task_type is None:
        from .tasktypes import classify_task
        body.contract.task_type = classify_task(body.contract.objective)
    # v21: resolve the mission mode the same way — explicit contract
    # value wins; otherwise the conservative suggester reads the
    # objective (most missions get None = the standard pipeline).
    if body.contract.mode is None:
        from .modes import suggest_mode
        body.contract.mode = suggest_mode(body.contract.objective,
                                          body.contract.task_type)
    mission = store.create_mission(t, u, body.title, body.contract.model_dump(),
                                   body.contract.constraints.max_cost_usd,
                                   project_id=body.project_id)
    store.save_idempotent(t, idempotency_key, req_hash, mission["id"])
    from .domain import event_envelope
    store.put_event(t, event_envelope(t, "mission", mission["id"], "MissionCreated",
                                      {"title": body.title,
                                       "mode": body.contract.mode}))
    return {"mission_id": mission["id"], "status": mission["status"]}


@app.get("/v1/modes")
async def list_modes_ep(request: Request):
    """v21: the mission modes a client can choose (role pack, phase
    pipeline, deliverable shape per mode)."""
    tenant(request)
    from .modes import list_modes
    return {"modes": list_modes()}


@app.get("/v1/reach")
async def list_reach_ep(request: Request):
    """v21: the reach pack's declared capabilities — every channel,
    its tool, whether the operator enabled it, and whether it still
    needs an API key (honest not_configured states, never faked)."""
    tenant(request)
    from .reach import declared_capabilities
    enabled = {c.strip() for c in settings.reach_channels.split(",")
               if c.strip()}
    return {"channels": declared_capabilities(enabled)}


@app.get("/v1/memory")
async def list_memory_ep(request: Request, scope: str | None = None,
                         scope_ref: str | None = None,
                         include_superseded: bool = False):
    """v21: scoped long-term memory facts for this tenant
    (app/memory.py). Reads are member+; the store is tenant-scoped."""
    guard(request, "member")
    t = tenant(request)
    from .memory import list_facts
    return {"facts": list_facts(store, t, scope=scope, scope_ref=scope_ref,
                                include_superseded=include_superseded)}


@app.post("/v1/memory", status_code=201)
async def add_memory_ep(request: Request, body: MemoryWrite):
    """Write rule (app/memory.can_write): owners/admins manage every
    scope; members write only their own user-scope facts."""
    ident = guard(request, "member")
    t = tenant(request)
    from .memory import add_fact, can_write
    if not can_write(ident.role, ident.user_id, body.scope, body.scope_ref):
        raise HTTPException(403, "members may only write their own "
                                 "user-scope memory; agent/session scopes "
                                 "are admin-managed")
    record = add_fact(store, t, body.scope, body.scope_ref, body.topic,
                      body.fact, source="manual", actor=ident.user_id)
    return record


@app.delete("/v1/memory/{memory_id}")
async def delete_memory_ep(request: Request, memory_id: str):
    ident = guard(request, "member")
    t = tenant(request)
    from .memory import can_write
    record = store.get_memory(t, memory_id)
    if record is None:
        return not_found(request)
    if not can_write(ident.role, ident.user_id, record["scope"],
                     record["scope_ref"]):
        raise HTTPException(403, "you may only remove memory you may write")
    store.delete_memory(t, memory_id)
    return {"removed": True, "memory_id": memory_id}


@app.get("/v1/missions")
async def list_missions(request: Request, limit: int = 50):
    t = tenant(request)
    return {"missions": store.list_missions(t, limit)}


@app.get("/v1/missions/{mission_id}")
async def get_mission(request: Request, mission_id: str):
    t = tenant(request)
    detail = orchestrator.mission_detail(t, mission_id)
    if detail is None:
        return not_found(request)
    return detail


@app.post("/v1/missions/{mission_id}/run")
async def run_mission(request: Request, mission_id: str):
    """Enqueue a durable job for the mission (Phase 2).

    inline mode (dev default): the API drains the job itself and returns the
    full mission detail, exactly like Phase 1. external mode: returns 202
    with a job_id; a separate worker process executes it — poll
    GET /v1/jobs/{job_id} or the mission itself."""
    guard(request, "member")
    t = tenant(request)
    rate_guard(f"run:{t}", settings.rate_run_per_min)
    mission = store.get_mission(t, mission_id)
    if mission is None:
        return not_found(request)
    # Audit B4: a terminal mission can never run again — refuse
    # BEFORE enqueueing (previously the click enqueued a job whose
    # execution re-planned the mission's tasks and then died on the
    # state machine; inline callers got a 500 for it).
    if mission["status"] in ("completed", "cancelled"):
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT",
            f"This mission is {mission['status']} and cannot be run again",
            corr(request)))
    if store.get_flag("kill_switch") == "on":
        return JSONResponse(status_code=503, content=error_envelope(
            "KILL_SWITCH_ACTIVE", "Execution is paused platform-wide by an administrator",
            corr(request)))
    # v20: fail fast — generation requires a connected real model.
    ms = orchestrator.model_state(t)
    if not ms.connected:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", ms.message, corr(request)))
    # Audit B3: dedupe — a second Run request while a run is queued
    # or executing returns the SAME job instead of enqueueing a
    # duplicate execution.
    job = store.enqueue_job(t, mission_id, kind="mission_run",
                            dedupe_active=True)
    if settings.worker_mode == "inline":
        from .worker import run_job_inline_async
        if job.get("existing") and job["status"] == "running":
            # The owning request is executing it right now; the
            # mission runs exactly once — answer with its detail.
            return orchestrator.mission_detail(t, mission_id)
        try:
            await run_job_inline_async(store, orchestrator, job["id"])
        except Exception as exc:  # logged in full; client gets a clean coded error
            if isinstance(exc, RuntimeError) and "could not claim" in str(exc):
                # Audit B3 race residue: a concurrent duplicate Run
                # request claimed this job first — the mission is
                # being run exactly once, by that request; answer
                # with its current detail rather than a 500.
                fresh = store.get_job(t, job["id"])
                if fresh and fresh["status"] in ("running", "completed"):
                    return orchestrator.mission_detail(t, mission_id)
            return internal_error(request, exc, f"mission run {mission_id}")
        return orchestrator.mission_detail(t, mission_id)
    return JSONResponse(status_code=202,
                        content={"job_id": job["id"], "mission_id": mission_id, "status": "queued"})


# ------------------------------------------------------------ brain (v21.2)
# AgentQon as a brain for the user's own external agent: one scoped
# key (created/rotated/revoked below, admin-guarded through the normal
# identity path) + one synchronous endpoint that runs the REAL mission
# pipeline — panel, critic, evaluation, memory — behind a single call.
# The key is stored as a SHA-256 hash only and authorises nothing
# else: it is not a session token (different prefix, rejected by the
# session path by construction) and in Shield local mode it replaces
# the local token for this path only — Host/Origin/signature rules
# are untouched (see shield_middleware).

def _brain_key_row(request: Request):
    """Resolve `Authorization: Bearer aqb_…` to its active brain_keys
    row, or None. The tenant (and acting user) come from the key
    itself — an external agent never names a tenant."""
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer "):
        return None
    token = authz[len("Bearer "):].strip()
    if not token.startswith(BRAIN_KEY_PREFIX):
        return None
    return store.get_brain_key_by_hash(token_hash(token))


def _brain_key_status(row) -> dict:
    if row is None:
        return {"active": False}
    return {"active": True, "label": row["label"],
            "key_prefix": row["key_prefix"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"]}


class BrainKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="brain", max_length=80)


@app.post("/v1/brain/keys", status_code=201)
async def create_brain_key_ep(request: Request, body: BrainKeyBody | None = None):
    """Create (or rotate) the tenant's brain key. The plaintext is
    returned exactly once — the server stores only its hash."""
    ident = guard(request, "admin")
    label = ((body.label if body else "brain") or "brain").strip() or "brain"
    key = new_brain_key()
    row = store.create_brain_key(ident.tenant_id, ident.user_id, label,
                                 token_hash(key), key[:12])
    security_log(settings, "brain.key_rotated", tenant=ident.tenant_id,
                 label=label)
    return {"key": key, "label": label, "key_prefix": row["key_prefix"],
            "created_at": row["created_at"], "endpoint": "/v1/brain",
            "note": "shown once — the server stores only a hash; "
                    "creating a new key revokes the previous one"}


@app.get("/v1/brain/keys")
async def brain_key_status_ep(request: Request):
    guard(request, "admin")
    return _brain_key_status(store.get_active_brain_key(tenant(request)))


@app.delete("/v1/brain/keys")
async def revoke_brain_key_ep(request: Request):
    guard(request, "admin")
    t = tenant(request)
    n = store.revoke_brain_keys(t)
    security_log(settings, "brain.key_revoked", tenant=t, count=n)
    return {"revoked": n, "active": False}


class BrainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20000)
    context: str | None = Field(default=None, max_length=50000)
    session: str | None = Field(default=None, max_length=200)


def _brain_answer(detail: dict) -> tuple[str, Optional[str]]:
    """The answer text + winning agent, chosen exactly the way the
    CLI renders a mission result: the candidate with the highest
    evaluation mean (deliverable first, summary as fallback)."""
    evals = {e["candidate_id"]: e for e in detail.get("evaluations", [])}

    def mean(c) -> Optional[float]:
        ev = evals.get(c["candidate_id"])
        if not ev or not ev.get("dimensions"):
            return None
        scores = [float(d.get("score", 0)) for d in ev["dimensions"].values()]
        return sum(scores) / len(scores) if scores else None

    best, best_mean = None, None
    for c in detail.get("candidates", []):
        m = mean(c)
        if m is not None and (best_mean is None or m > best_mean):
            best, best_mean = c, m
    if best is None:
        selected = detail["mission"].get("selected_candidate_id")
        best = next((c for c in detail.get("candidates", [])
                     if c["candidate_id"] == selected), None) \
            or (detail["candidates"][-1] if detail.get("candidates") else None)
    if best is None:
        return "", None
    text = best.get("deliverable") or best.get("summary") or best.get("title") or ""
    return text, best.get("agent_version_id")


@app.post("/v1/brain")
async def brain_ep(request: Request, body: BrainBody):
    """One synchronous call: prompt (+ optional caller context and a
    session id for memory continuity) → the panel's answer. No model
    connected means the platform's honest 503, exactly like a mission
    run — a brain call is never faked."""
    row = _brain_key_row(request)
    if row is None:
        return JSONResponse(status_code=401, content=error_envelope(
            "BRAIN_KEY_INVALID",
            "a valid brain key is required — create one with "
            "`agentqon brain key`", corr(request)))
    t = row["tenant_id"]
    u = row["user_id"] or "brain"
    ensure_tenant(t)
    rate_guard(f"brain:{t}", settings.rate_run_per_min)
    try:
        store.touch_brain_key(row["id"])
    except Exception:
        pass  # last-used bookkeeping never breaks a call
    if store.get_flag("kill_switch") == "on":
        return JSONResponse(status_code=503, content=error_envelope(
            "KILL_SWITCH_ACTIVE",
            "Execution is paused platform-wide by an administrator",
            corr(request)))
    ms = orchestrator.model_state(t)
    if not ms.connected:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", ms.message, corr(request)))
    objective = body.prompt
    extras: list[str] = []
    if body.context:
        extras.append("Context supplied by the calling agent:\n"
                      + body.context)
    if body.session:
        try:
            from .memory import list_facts
            facts = list_facts(store, t, scope="session",
                               scope_ref=body.session)
            if facts:
                lines = "\n".join(f"- {f['fact']}" for f in facts[-10:])
                extras.append("Earlier in this session (long-term "
                              "memory):\n" + lines)
        except Exception:
            log.warning("brain: session memory read failed", exc_info=True)
    if extras:
        objective = objective + "\n\n" + "\n\n".join(extras)
    from .modes import suggest_mode
    from .tasktypes import classify_task
    mc = MissionCreate(
        title=(body.prompt[:60] or "Brain request"),
        contract={
            "objective": objective,
            "deliverables": ["Answer"],
            "acceptance": ["Material claims cite sources"],
            "constraints": {"max_cost_usd": MISSION_BUDGET_CEILING_USD},
            "tools": {"allow": ["web.read", "artifact.write"], "deny": []},
            "approval_policy": {"external_side_effects": "required"},
            "data_classification": "internal",
        })
    if mc.contract.task_type is None:
        mc.contract.task_type = classify_task(body.prompt)
    if mc.contract.mode is None:
        mc.contract.mode = suggest_mode(body.prompt, mc.contract.task_type)
    mission = store.create_mission(t, u, mc.title, mc.contract.model_dump(),
                                   mc.contract.constraints.max_cost_usd)
    from .domain import event_envelope
    store.put_event(t, event_envelope(t, "mission", mission["id"],
                                      "MissionCreated",
                                      {"title": mc.title,
                                       "mode": mc.contract.mode,
                                       "via": "brain"}))
    job = store.enqueue_job(t, mission["id"], kind="mission_run")
    if settings.worker_mode == "inline":
        from .worker import run_job_inline_async
        try:
            await run_job_inline_async(store, orchestrator, job["id"])
        except Exception as exc:  # logged in full; client gets a clean coded error
            return internal_error(request, exc, "brain run")
    else:
        # Brain is synchronous by contract: with an external worker,
        # wait for the mission to reach a terminal state.
        import asyncio
        import time as _time
        deadline = _time.monotonic() + 900
        while _time.monotonic() < deadline:
            current = store.get_mission(t, mission["id"])
            if current and current["status"] in (
                    "completed", "failed", "cancelled", "rejected"):
                break
            await asyncio.sleep(1)
    detail = orchestrator.mission_detail(t, mission["id"])
    m = detail["mission"]
    if m["status"] != "completed":
        reason = next((e["payload"].get("reason", "")
                       for e in detail["events"]
                       if e["event_type"] == "MissionFailed"), "")
        return JSONResponse(status_code=502, content=error_envelope(
            "BRAIN_RUN_FAILED",
            f"the panel run ended {m['status']}"
            + (f": {reason}" if reason else "")
            + f" (mission {mission['id']})", corr(request)))
    answer, agent_id = _brain_answer(detail)
    if body.session:
        try:
            from .memory import add_fact
            add_fact(store, t, "session", body.session,
                     f"brain:{mission['id']}",
                     f"Asked: {body.prompt[:300]} "
                     f"Answered: {answer[:600]}",
                     source="brain", actor=u)
        except Exception:
            log.warning("brain: session memory write failed", exc_info=True)
    security_log(settings, "brain.call", tenant=t, label=row["label"],
                 mission=mission["id"])
    return {"answer": answer, "mission_id": mission["id"],
            "status": m["status"], "session": body.session,
            "agent": agent_id, "candidates": len(detail["candidates"]),
            "usage": detail.get("usage")}


@app.get("/v1/jobs")
async def list_jobs(request: Request, status: Optional[str] = None):
    t = tenant(request)
    return {"jobs": store.list_jobs(t, status)}


@app.get("/v1/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    t = tenant(request)
    job = store.get_job(t, job_id)
    if job is None:
        return not_found(request)
    return job


@app.post("/v1/jobs/{job_id}/replay")
async def replay_job(request: Request, job_id: str):
    """Replay a dead/failed job (dead-letter recovery, Ch.19)."""
    guard(request, "member")
    t = tenant(request)
    job = store.replay_job(t, job_id)
    if job is None:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", "Only dead or failed jobs can be replayed", corr(request)))
    if settings.worker_mode == "inline":
        from .worker import run_job_inline_async
        try:
            await run_job_inline_async(store, orchestrator, job["id"])
        except Exception as exc:
            return internal_error(request, exc, f"job replay {job_id}")
        job = store.get_job(t, job_id)
    return job


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


@app.post("/v1/missions/{mission_id}/cancel")
async def cancel_mission(request: Request, mission_id: str, body: CancelBody | None = None):
    guard(request, "member")
    t = tenant(request)
    mission = store.get_mission(t, mission_id)
    if mission is None:
        return not_found(request)
    from .domain import MISSION_TRANSITIONS, assert_transition
    assert_transition(mission["status"], "cancelled", MISSION_TRANSITIONS, "Mission")
    store.set_mission_status(t, mission_id, "cancelled")
    # Audit R (bug #3): a cancelled mission's open tasks close with it
    # (a paused mission being cancelled is the reachable case).
    store.close_open_tasks(t, mission_id, "cancelled")
    return {"mission_id": mission_id, "status": "cancelled"}


class SelectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)


@app.post("/v1/missions/{mission_id}/select")
async def select_candidate(request: Request, mission_id: str, body: SelectBody):
    guard(request, "member")
    t = tenant(request)
    cand = store.get_candidate(t, mission_id, body.candidate_id)
    if cand is None:
        return not_found(request)
    # Selection is exclusive: a previously selected candidate returns to
    # "proposed" so exactly one candidate is ever marked selected.
    for other in store.list_candidates(t, mission_id):
        if (other["candidate_id"] != body.candidate_id
                and other.get("status") == "selected"):
            other["status"] = "proposed"
            store.put_candidate(t, other)
    cand["status"] = "selected"
    store.put_candidate(t, cand)
    store.set_mission_selected(t, mission_id, body.candidate_id)
    # v13: the user's pick is the strongest suitability signal there is.
    try:
        from .suitability import empty_stat
        mission = store.get_mission(t, mission_id)
        tt = (mission or {}).get("contract", {}).get("task_type") or "strategy"
        key = f"{cand['agent_version_id']}|{tt}"
        stat = next((x for x in store.list_agent_stats(t)
                     if f"{x['agent_version_id']}|{x['task_type']}" == key),
                    empty_stat(cand["agent_version_id"], tt))
        stat["selections"] += 1
        store.put_agent_stat(t, stat)
    except Exception:
        pass
    return {"mission_id": mission_id, "selected_candidate_id": body.candidate_id}


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(approved|denied)$")


@app.post("/v1/missions/{mission_id}/approve")
async def approve_action(request: Request, mission_id: str, body: ApproveBody):
    guard(request, "member")
    t = tenant(request)
    result = store.decide_approval(t, mission_id, body.approval_id, body.decision)
    if result is None:
        return not_found(request)
    return {"approval_id": body.approval_id, "status": result["status"]}


@app.get("/v1/missions/{mission_id}/events")
async def mission_events(request: Request, mission_id: str, after: Optional[str] = None):
    t = tenant(request)
    if store.get_mission(t, mission_id) is None:
        return not_found(request)
    return {"events": store.list_events(t, mission_id, after)}


@app.get("/v1/missions/{mission_id}/events/stream")
async def stream_mission_events(request: Request, mission_id: str):
    """Server-sent events for a mission (Phase 5): the UI watches the
    run live instead of polling. Emits each event once, heartbeats while
    running, and closes after the mission reaches a terminal state and
    the backlog is drained."""
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse
    t = tenant(request)
    if store.get_mission(t, mission_id) is None:
        return not_found(request)

    async def gen():
        last_id: Optional[str] = None
        idle_rounds = 0
        for _ in range(600):  # hard cap ~10 min at 1s cadence
            if await request.is_disconnected():
                return
            events = store.list_events(t, mission_id, after=last_id)
            for e in events:
                last_id = e["event_id"]
                yield f"data: {_json.dumps(e)}\n\n"
            mission = store.get_mission(t, mission_id)
            terminal = mission and mission["status"] in (
                "completed", "failed", "cancelled", "rejected")
            if events:
                idle_rounds = 0
            elif terminal:
                return
            else:
                idle_rounds += 1
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/v1/agents/import-agency")
async def import_agency_ep(request: Request):
    """Install the agency-agents roster (MIT) into this workspace's
    registry: manifests land ACTIVE (operator's standing order) with
    persona briefs in the persona store. Admin only; re-import
    preserves each agent's existing lifecycle."""
    guard(request, "admin")
    t = tenant(request)
    from .agency import import_roster
    try:
        with store.bulk():
            result = import_roster(store, t)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    return {**result, "lifecycle": "active",
            "note": "Imported agents are ACTIVE and join the suitability "
                    "pool immediately (operator's standing order). "
                    "Quarantine any agent from the agents table to bench it."}


@app.post("/v1/agents/import-library")
async def import_library_ep(request: Request):
    """Install the subagents mega-library (harvested from 65 public
    collections; per-source licenses/provenance ride in each agent's
    owner field) into this workspace's registry as a second roster
    beside agency-agents: manifests land ACTIVE (operator's standing
    order) with persona briefs in the persona store. Admin only;
    re-import preserves each agent's existing lifecycle."""
    guard(request, "admin")
    t = tenant(request)
    from .library import import_library
    try:
        with store.bulk():
            result = import_library(store, t)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    return {**result, "lifecycle": "active",
            "note": "Imported agents are ACTIVE and join the suitability "
                    "pool immediately (operator's standing order). "
                    "Quarantine any agent from the agents table to bench it."}


@app.get("/v1/agents/stats")
async def agent_stats_ep(request: Request):
    """v13: the performance ledger — per agent, per task type: runs,
    mean evaluation, user selections, high-severity critiques."""
    t = tenant(request)
    stats = store.list_agent_stats(t)
    out = []
    for x in stats:
        runs = max(0, int(x.get("runs", 0)))
        out.append({**x, "mean_eval": (round(x["eval_sum"] / runs, 2) if runs else None)})
    return {"stats": sorted(out, key=lambda x: -x["runs"])}


@app.get("/v1/agents/suitability")
async def agent_suitability_ep(request: Request, task_type: str = "strategy"):
    """v13: who is suitable for this kind of task, and why — ranked,
    with the score components visible. The panel picker uses exactly
    this ranking."""
    from .tasktypes import TASK_TYPES
    from .suitability import rank_panel
    if task_type not in TASK_TYPES:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", f"unknown task_type {task_type}", corr(request)))
    t = tenant(request)
    panel = rank_panel(store.list_agents(t), task_type,
                       store.list_agent_stats(t), k=12)
    from .display_names import agent_display
    for e in panel:
        e.pop("manifest", None)
        name = agent_display(e.get("agent_id", ""))
        if name:
            e["name"] = name
    return {"task_type": task_type, "ranking": panel}


@app.get("/v1/agents/version")
async def agents_version_ep(request: Request):
    """v21.4.2: the roster version probe behind the UI's device cache
    — ~100 bytes saying whether the tenant's registry changed since
    the client's cached copy. A match means the client renders its
    cached roster with ZERO roster download."""
    t = tenant(request)
    return store.agents_version(t)


@app.get("/v1/agents")
async def list_agents(request: Request, capability: Optional[str] = None):
    t = tenant(request)
    agents = store.list_agents(t)
    if capability:
        agents = [a for a in agents if capability in a.get("capabilities", [])]
    from .display_names import agent_display
    for a in agents:
        aid = a.get("agent_id") or \
            (a.get("agent_version_id") or "").split("@")[0]
        name = agent_display(aid)
        if name:
            a["name"] = name
            a["display_name"] = name
    return {"agents": agents}


class LifecycleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle: str = Field(pattern="^(active|degraded|quarantined|deprecated|retired|testing|approved)$")


@app.post("/v1/agents/{agent_version_id}/lifecycle")
async def set_lifecycle(request: Request, agent_version_id: str, body: LifecycleBody):
    # Shield H3: agent lifecycle is an admin action — a viewer/member
    # must not be able to quarantine (or resurrect) the roster.
    guard(request, "admin")
    t = tenant(request)
    agents = {a["agent_version_id"]: a for a in store.list_agents(t)}
    current = agents.get(agent_version_id)
    if current is None:
        return not_found(request)
    from .domain import AGENT_TRANSITIONS, assert_transition
    assert_transition(current["lifecycle"], body.lifecycle, AGENT_TRANSITIONS, "Agent")
    store.set_agent_lifecycle(t, agent_version_id, body.lifecycle)
    return {"agent_version_id": agent_version_id, "lifecycle": body.lifecycle}


# v21.2: workspace custom agents — CRUD + registry citizenship lives
# in app/custom_agents.py; this include is its only footprint here.
from .custom_agents import build_router as _build_custom_agents_router

app.include_router(_build_custom_agents_router(
    store=store, guard=guard, tenant_of=tenant, corr_of=corr,
    not_found_of=not_found))


@app.get("/v1/artifacts/{artifact_id}")
async def get_artifact(request: Request, artifact_id: str):
    t = tenant(request)
    art = store.get_artifact(t, artifact_id)
    if art is None:
        return not_found(request)
    return art


@app.get("/v1/sources")
async def list_sources(request: Request):
    t = tenant(request)
    return {"sources": store.list_sources(t)}


# ======================================================================
# V3 — projects, pause/resume, revision, export (Master Build §7)
# ======================================================================
class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


@app.post("/v1/projects", status_code=201)
async def create_project(request: Request, body: ProjectBody):
    guard(request, "member")
    t = tenant(request)
    from .store import now_iso
    project = {"id": new_id("prj"), "tenant_id": t, "name": body.name,
               "created_at": now_iso()}
    store.put_project(t, project)
    return project


# ------------------------------------------------------------ worlds (Phase 7)
from .simulation import BranchCreate as _BranchCreate
from .simulation import ExperimentCreate as _sim_experiment_create
from .simulation import WorldCreate as _WorldCreate
from .simulation import (add_branch as _sim_add_branch)
from .simulation import (compare as _sim_compare)
from .simulation import (create_world as _sim_create_world)
from .simulation import (run_world as _sim_run_world)


@app.post("/v1/worlds", status_code=201)
async def create_world_ep(request: Request, body: _WorldCreate):
    guard(request, "member")
    t, u = tenant(request), user(request)
    try:
        world = _sim_create_world(store, t, u, body)
    except KeyError:
        return not_found(request)
    return {"world_id": world["world_id"], "status": world["status"],
            "label": "SIMULATED ESTIMATE"}


@app.get("/v1/worlds")
async def list_worlds_ep(request: Request):
    t = tenant(request)
    return {"worlds": [{"world_id": w["world_id"], "name": w["name"],
                        "status": w["status"], "branches": len(w["branches"]),
                        "label": "SIMULATED ESTIMATE"}
                       for w in store.list_worlds(t)]}


@app.get("/v1/worlds/{world_id}")
async def get_world_ep(request: Request, world_id: str):
    t = tenant(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    return world


@app.post("/v1/worlds/{world_id}/branches", status_code=201)
async def add_branch_ep(request: Request, world_id: str, body: _BranchCreate):
    guard(request, "member")
    t = tenant(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    try:
        branch = _sim_add_branch(store, t, world, body)
    except ValueError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))
    return branch


@app.post("/v1/worlds/{world_id}/run")
async def run_world_ep(request: Request, world_id: str):
    guard(request, "member")
    t = tenant(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    if not world["branches"]:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "Add at least one branch before running", corr(request)))
    world = _sim_run_world(store, t, world)
    return {"world_id": world_id, "status": world["status"],
            "label": "SIMULATED ESTIMATE",
            "outcome_hashes": [b["result"]["outcome_hash"]
                                for b in world["branches"] if b["result"]]}


@app.get("/v1/worlds/{world_id}/compare")
async def compare_world_ep(request: Request, world_id: str):
    t = tenant(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    return _sim_compare(world)


@app.post("/v1/worlds/{world_id}/experiments", status_code=201)
async def run_experiment_ep(request: Request, world_id: str,
                            body: _sim_experiment_create):
    """v21: register + run a baseline-vs-intervention experiment pair
    on the world's seed (metrics pre-registered before the run)."""
    guard(request, "member")
    t = tenant(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    from .simulation import run_experiment
    try:
        experiment = run_experiment(store, t, world, body)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    return experiment


@app.post("/v1/worlds/{world_id}/promote", status_code=201)
async def promote_world_ep(request: Request, world_id: str, body: dict):
    """The ONLY exit from simulation mode: an explicit, logged action
    that creates a new DRAFT mission (normal approval flow applies from
    there). Nothing in a world can act on the real world directly."""
    guard(request, "member")
    t, u = tenant(request), user(request)
    world = store.get_world(t, world_id)
    if world is None:
        return not_found(request)
    branch = next((b for b in world["branches"]
                   if b["branch_id"] == body.get("branch_id")), None)
    if branch is None or not branch.get("result"):
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "branch_id must name a completed branch", corr(request)))
    objective = (world["provenance"].get("objective")
                 or f"Execute the {branch['strategy']} strategy")
    contract = {
        "objective": f"{objective} (strategy selected in simulation world "
                     f"{world_id}, branch {branch['branch_id']}: SIMULATED ESTIMATE "
                     f"of {branch['result']['final']['adopters']} adopters — verify "
                     "against real evidence before committing)",
        "deliverables": ["Execution plan", "Assumptions ledger"],
        "acceptance": ["Material claims cite sources"],
        "constraints": {"max_cost_usd": MISSION_BUDGET_CEILING_USD},
        "tools": {"allow": ["web.read", "artifact.write"], "deny": []},
        "approval_policy": {"external_side_effects": "required"},
        "data_classification": "internal",
    }
    mission = store.create_mission(t, u, f"Promoted: {world['name']} / {branch['label']}",
                                   contract, MISSION_BUDGET_CEILING_USD)
    from .domain import event_envelope
    store.put_event(t, event_envelope(
        t, "world", world_id, "BranchPromoted",
        {"branch_id": branch["branch_id"], "mission_id": mission["id"],
         "promoted_by": u}))
    store.put_event(t, event_envelope(
        t, "mission", mission["id"], "MissionCreated",
        {"title": mission["title"], "origin": "simulation-promotion",
         "world_id": world_id, "branch_id": branch["branch_id"]}))
    return {"mission_id": mission["id"], "status": mission["status"],
            "note": "Draft mission created from a SIMULATED ESTIMATE. "
                    "It has not run; normal approvals apply."}


@app.get("/v1/projects")
async def list_projects(request: Request):
    t = tenant(request)
    return {"projects": store.list_projects(t)}


def _mission_or_404(request: Request, t: str, mission_id: str):
    mission = store.get_mission(t, mission_id)
    return mission


@app.post("/v1/missions/{mission_id}/pause")
async def pause_mission(request: Request, mission_id: str):
    guard(request, "member")
    t = tenant(request)
    mission = _mission_or_404(request, t, mission_id)
    if mission is None:
        return not_found(request)
    from .domain import assert_transition, MISSION_TRANSITIONS
    assert_transition(mission["status"], "paused", MISSION_TRANSITIONS, "Mission")
    store.set_mission_status(t, mission_id, "paused")
    from .domain import event_envelope
    store.put_event(t, event_envelope(t, "mission", mission_id, "MissionPaused", {}))
    return {"mission_id": mission_id, "status": "paused"}


@app.post("/v1/missions/{mission_id}/resume")
async def resume_mission(request: Request, mission_id: str):
    guard(request, "member")
    t = tenant(request)
    mission = _mission_or_404(request, t, mission_id)
    if mission is None:
        return not_found(request)
    if mission["status"] != "paused":
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", "Only a paused mission can be resumed", corr(request)))
    if store.get_flag("kill_switch") == "on":
        return JSONResponse(status_code=503, content=error_envelope(
            "KILL_SWITCH_ACTIVE", "Execution is paused platform-wide by an administrator",
            corr(request)))
    ms = orchestrator.model_state(t)
    if not ms.connected:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", ms.message, corr(request)))
    store.set_mission_status(t, mission_id, "running")
    from .domain import event_envelope
    store.put_event(t, event_envelope(t, "mission", mission_id, "MissionResumed", {}))
    # If no live job exists for this mission (it was requeued-consumed or
    # ran inline), enqueue a fresh one; a still-queued job will simply
    # pick the mission up.
    live = [j for j in store.list_jobs(t) if j["mission_id"] == mission_id
            and j["status"] in ("queued", "running")]
    if settings.worker_mode == "inline":
        # Audit R: inline mode means THIS process is the only worker —
        # a still-queued job (the normal state after a mid-run pause
        # requeues it) is picked up by NOBODY if we merely answer
        # 'running'. The mission then sat in 'running' forever with no
        # execution behind it (live repro). Run the live job — or a
        # fresh one — here, exactly like the no-live-job path.
        from .worker import run_job_inline_async
        job_id = live[0]["id"] if live else \
            store.enqueue_job(t, mission_id, kind="mission_run")["id"]
        try:
            await run_job_inline_async(store, orchestrator, job_id)
        except Exception as exc:
            return internal_error(request, exc, f"mission resume {mission_id}")
        return orchestrator.mission_detail(t, mission_id)
    if not live:
        job = store.enqueue_job(t, mission_id, kind="mission_run")
        return JSONResponse(status_code=202, content={"job_id": job["id"],
                                                       "mission_id": mission_id,
                                                       "status": "queued"})
    return {"mission_id": mission_id, "status": "running"}


class ReviseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=3, max_length=80)
    instructions: str = Field(default="", max_length=2000)


@app.post("/v1/missions/{mission_id}/revise")
async def revise_mission(request: Request, mission_id: str, body: ReviseBody):
    guard(request, "member")
    t = tenant(request)
    rate_guard(f"run:{t}", settings.rate_run_per_min)
    if store.get_mission(t, mission_id) is None:
        return not_found(request)
    ms = orchestrator.model_state(t)
    if not ms.connected:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", ms.message, corr(request)))
    job = store.enqueue_job(t, mission_id, kind="mission_revision",
                            payload={"candidate_id": body.candidate_id,
                                     "instructions": body.instructions})
    if settings.worker_mode == "inline":
        from .worker import run_job_inline_async
        try:
            await run_job_inline_async(store, orchestrator, job["id"])
        except KeyError:
            return not_found(request)
        except ValueError as exc:
            return JSONResponse(status_code=409, content=error_envelope(
                "STATE_CONFLICT", str(exc), corr(request)))
        except Exception as exc:
            return internal_error(request, exc, f"mission revise {mission_id}")
        return orchestrator.mission_detail(t, mission_id)
    return JSONResponse(status_code=202, content={"job_id": job["id"],
                                                   "mission_id": mission_id, "status": "queued"})


@app.get("/v1/missions/{mission_id}/export")
async def export_mission(request: Request, mission_id: str, format: str = "md"):
    t = tenant(request)
    detail = orchestrator.mission_detail(t, mission_id)
    if detail is None:
        return not_found(request)
    if format == "json":
        return detail
    from .exporter import mission_markdown
    from fastapi.responses import PlainTextResponse
    # Audit F14: the id is path-supplied — keep the download filename
    # to the id alphabet so a crafted id can't break the header's
    # quoting (CR/LF are already rejected by the HTTP stack).
    import re as _re
    safe_id = _re.sub(r"[^A-Za-z0-9_-]", "_", mission_id)
    return PlainTextResponse(
        mission_markdown(detail), media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="mission-{safe_id}.md"'})


# ======================================================================
# v15 — Proof Pack (trust), Agent Arena (measured signal), Scheduled
# Missions (retention). Feature logic lives in app/proofpack.py,
# app/arena.py and app/schedules.py; these routes are thin wrappers in
# the same envelope/guard style as the rest of the API.
# ======================================================================
@app.get("/v1/missions/{mission_id}/proof-pack")
async def proof_pack_ep(request: Request, mission_id: str):
    """The mission's proof pack: candidates, evaluation scores, critic
    findings, every cited source, and per-claim confidence labels
    COMPUTED from the recorded evidence (see app/proofpack.py).
    Completed missions only — a pack asserts what the work rests on,
    so it is issued when the work is done. The pack and its Markdown
    rendering are stored as artifacts and linked in the response."""
    t = tenant(request)
    mission = store.get_mission(t, mission_id)
    if mission is None:
        return not_found(request)
    if mission["status"] != "completed":
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT",
            f"Proof packs are issued for completed missions "
            f"(this one is {mission['status']})", corr(request)))
    from .proofpack import build_proof_pack, proof_pack_markdown
    pack = build_proof_pack(store, t, mission_id)
    markdown = proof_pack_markdown(pack)
    art = store.put_artifact(t, mission_id, "proof_pack", pack,
                             {"produced_by": "proof-pack",
                              "pack_version": pack["pack_version"]})
    md_art = store.put_artifact(t, mission_id, "proof_pack_md", markdown,
                                {"produced_by": "proof-pack", "format": "markdown"})
    return {**pack,
            "artifact": {"artifact_id": art["artifact_id"],
                         "version": art["version"], "kind": "proof_pack"},
            "markdown_artifact": {"artifact_id": md_art["artifact_id"],
                                  "version": md_art["version"],
                                  "kind": "proof_pack_md"},
            "markdown": markdown}


class DuelCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str = Field(pattern="^(strategy|research|writing|code|plan|qa|general)$")
    prompt: str = Field(min_length=1, max_length=4000)
    agent_a: Optional[str] = None
    agent_b: Optional[str] = None


class DuelVoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    winner: str = Field(pattern="^(a|b)$")


@app.post("/v1/arena/duels", status_code=201)
async def create_duel_ep(request: Request, body: DuelCreateBody):
    """Run a blind head-to-head between two persona agents on one
    prompt. Omit agent_a/agent_b to auto-pick the top-2 suitable
    ACTIVE persona agents for the task type. The response is blind —
    identities are revealed only after the vote."""
    guard(request, "member")
    t, u = tenant(request), user(request)
    from .arena import DuelError, create_duel, public_duel
    from .orchestrator import ModelNotConnected
    try:
        duel = await create_duel(store, orchestrator, t, u, body.task_type,
                                 body.prompt, body.agent_a, body.agent_b)
    except ModelNotConnected as exc:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", str(exc), corr(request)))
    except DuelError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    return public_duel(duel)


@app.get("/v1/arena/duels")
async def list_duels_ep(request: Request):
    t = tenant(request)
    from .arena import public_duel
    return {"duels": [public_duel(d) for d in store.list_duels(t)]}


@app.get("/v1/arena/duels/{duel_id}")
async def get_duel_ep(request: Request, duel_id: str):
    t = tenant(request)
    duel = store.get_duel(t, duel_id)
    if duel is None:
        return not_found(request)
    from .arena import public_duel
    return public_duel(duel)


@app.post("/v1/arena/duels/{duel_id}/vote")
async def vote_duel_ep(request: Request, duel_id: str, body: DuelVoteBody):
    """Cast the deciding vote (winner = blind side 'a' or 'b'). Reveals
    the agents and writes arena_wins / arena_losses into the tenant's
    performance ledger (GET /v1/agents/stats). One vote per duel."""
    guard(request, "member")
    t = tenant(request)
    from .arena import cast_vote, public_duel
    duel = cast_vote(store, t, duel_id, body.winner)  # KeyError->404, StateConflict->409
    return public_duel(duel)


@app.get("/v1/arena/leaderboard")
async def arena_leaderboard_ep(request: Request, task_type: Optional[str] = None):
    """Arena standings from the performance ledger, merged with the
    mission record. Ordering rule (documented in app/arena.py):
    agents with >= min_duels_for_ranking duels rank above provisional
    ones; within a group, win rate desc, then wins desc."""
    from .tasktypes import TASK_TYPES
    if task_type is not None and task_type not in TASK_TYPES:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", f"unknown task_type {task_type}", corr(request)))
    t = tenant(request)
    from .arena import leaderboard
    return leaderboard(store, t, task_type)


from .schedules import ScheduleCreate as _ScheduleCreate
from .schedules import SchedulePatch as _SchedulePatch


@app.post("/v1/schedules", status_code=201)
async def create_schedule_ep(request: Request, body: _ScheduleCreate):
    """Create a recurring mission schedule. Firing happens in the
    worker tick (no external cron): a due schedule creates an
    ordinary mission through the normal contract path — classification
    gating, budget cap and approvals apply unchanged."""
    ident = guard(request, "member")
    t = ident.tenant_id
    if body.project_id and store.get_project(t, body.project_id) is None:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "project_id does not belong to this tenant",
            corr(request)))
    from .schedules import build_schedule
    schedule = build_schedule(t, ident.user_id, body)
    store.put_schedule(t, schedule)
    store.audit(t, ident.user_id, "schedule.created", "schedule",
                schedule["schedule_id"], {"cadence": schedule["cadence"]["kind"]})
    return schedule


@app.get("/v1/schedules")
async def list_schedules_ep(request: Request):
    t = tenant(request)
    return {"schedules": store.list_schedules(t)}


@app.get("/v1/schedules/{schedule_id}")
async def get_schedule_ep(request: Request, schedule_id: str):
    t = tenant(request)
    schedule = store.get_schedule(t, schedule_id)
    if schedule is None:
        return not_found(request)
    return schedule


@app.patch("/v1/schedules/{schedule_id}")
async def patch_schedule_ep(request: Request, schedule_id: str,
                            body: _SchedulePatch):
    """Rename, re-cadence or enable/disable a schedule. Changing the
    cadence or re-enabling re-anchors next_run_at to the first
    occurrence after now — a schedule never fires retroactively."""
    ident = guard(request, "member")
    t = ident.tenant_id
    schedule = store.get_schedule(t, schedule_id)
    if schedule is None:
        return not_found(request)
    from datetime import datetime, timezone

    from .schedules import compute_next_run
    now = datetime.now(timezone.utc)
    reanchor = False
    if body.title is not None:
        schedule["title"] = body.title
    if body.cadence is not None:
        schedule["cadence"] = body.cadence.model_dump()
        reanchor = True
    if body.enabled is not None:
        if body.enabled and not schedule["enabled"]:
            reanchor = True
        schedule["enabled"] = body.enabled
    if reanchor:
        schedule["next_run_at"] = compute_next_run(
            schedule["cadence"], now).isoformat().replace("+00:00", "Z")
    from .domain import now_iso
    schedule["updated_at"] = now_iso()
    store.put_schedule(t, schedule)
    store.audit(t, ident.user_id, "schedule.updated", "schedule", schedule_id,
                {"enabled": schedule["enabled"]})
    return schedule


@app.delete("/v1/schedules/{schedule_id}")
async def delete_schedule_ep(request: Request, schedule_id: str):
    ident = guard(request, "member")
    t = ident.tenant_id
    if not store.delete_schedule(t, schedule_id):
        return not_found(request)
    store.audit(t, ident.user_id, "schedule.deleted", "schedule", schedule_id, {})
    return {"deleted": True, "schedule_id": schedule_id}


# ======================================================================
# v16 — Telegram front door (app/telegram.py + app/telegram_client.py).
# The channel is env-gated: with AGENTQON_TELEGRAM_BOT_TOKEN unset,
# every route below answers a clean 503 and nothing else changes.
# ======================================================================
def _telegram_cfg_or_503(request: Request):
    """(cfg, None) when the channel is configured, else (None, 503)."""
    from .telegram import channel_config, channel_enabled
    cfg = channel_config()
    if not channel_enabled(cfg):
        return None, JSONResponse(status_code=503, content=error_envelope(
            "TELEGRAM_NOT_CONFIGURED",
            "Telegram is not configured on this server "
            "(AGENTQON_TELEGRAM_BOT_TOKEN is unset)", corr(request)))
    return cfg, None


@app.post("/v1/channels/telegram/link-code", status_code=201)
async def telegram_link_code_ep(request: Request):
    """Mint a one-time link code (~10 min, single-use) binding a
    Telegram chat to THIS account. The user sends /start <code> to the
    bot (or taps the deep link); minting replaces older unused codes."""
    cfg, err = _telegram_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .telegram import new_link_code
    record = new_link_code(store, ident.tenant_id, ident.user_id,
                           ident.role, cfg)
    store.audit(ident.tenant_id, ident.user_id, "telegram.link_code",
                "telegram_link", ident.user_id, {})
    return {"code": record["code"], "expires_at": record["expires_at"],
            "bot_username": record["bot_username"],
            "deep_link": record["deep_link"],
            "note": "Send /start <code> to the bot (or tap the deep "
                    "link). One code works once, for ~10 minutes."}


@app.get("/v1/channels/telegram/status")
async def telegram_status_ep(request: Request):
    """Channel state for this account: configured? linked? as which
    Telegram user? The UI's Telegram panel renders from this."""
    cfg, err = _telegram_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .telegram import link_status
    return link_status(store, ident.tenant_id, ident.user_id, cfg)


@app.delete("/v1/channels/telegram/link")
async def telegram_unlink_ep(request: Request):
    """Detach this account's Telegram chat (the chat keeps its history;
    the bot stops recognising it until it links again)."""
    cfg, err = _telegram_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .telegram import unlink_account
    removed = unlink_account(store, ident.tenant_id, ident.user_id)
    if removed:
        store.audit(ident.tenant_id, ident.user_id, "telegram.unlinked",
                    "telegram_link", ident.user_id, {})
    return {"unlinked": removed}


@app.delete("/v1/channels/telegram/link/{target_user_id}")
async def telegram_admin_unlink_ep(request: Request, target_user_id: str):
    """Audit F8: an admin cuts ANOTHER user's Telegram link (e.g. a
    member who was removed from the workspace). The own-link endpoint
    above only ever acts on the caller's own link."""
    cfg, err = _telegram_cfg_or_503(request)
    if err is not None:
        return err
    ident = guard(request, "admin")
    from .telegram import unlink_account
    removed = unlink_account(store, ident.tenant_id, target_user_id)
    if removed:
        store.audit(ident.tenant_id, ident.user_id,
                    "telegram.unlinked_admin", "telegram_link",
                    target_user_id, {})
    return {"unlinked": removed, "user_id": target_user_id}


@app.post("/v1/channels/telegram/webhook")
async def telegram_webhook_ep(request: Request):
    """Telegram's update delivery. No AgentQon credential is involved:
    authenticity is the shared secret registered with setWebhook
    (X-Telegram-Bot-Api-Secret-Token), identity comes from the chat's
    link record, and updates are deduped by update_id. Valid updates
    always get a fast 200 — slow work (missions) is queued, and duel
    creation follows the web arena's synchronous path."""
    import secrets as _secrets

    from .telegram import channel_config, channel_enabled, handle_update
    cfg = channel_config()
    if not channel_enabled(cfg):
        return JSONResponse(status_code=503, content=error_envelope(
            "TELEGRAM_NOT_CONFIGURED",
            "Telegram is not configured on this server "
            "(AGENTQON_TELEGRAM_BOT_TOKEN is unset)", corr(request)))
    secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not cfg.telegram_webhook_secret or not _secrets.compare_digest(
            secret, cfg.telegram_webhook_secret):
        return JSONResponse(status_code=403, content=error_envelope(
            "FORBIDDEN", "bad or missing Telegram webhook secret",
            corr(request)))
    try:
        update = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "update body must be JSON", corr(request)))
    if not isinstance(update, dict):
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "update body must be a JSON object",
            corr(request)))
    result = await handle_update(store, orchestrator, update, cfg)
    return {"ok": True, **result}


# ======================================================================
# v17 — WhatsApp front door (app/whatsapp.py + app/whatsapp_client.py).
# The channel is env-gated: with AGENTQON_WHATSAPP_TOKEN or
# AGENTQON_WHATSAPP_PHONE_NUMBER_ID unset, every route below answers
# a clean 503 and nothing else changes.
# ======================================================================
def _whatsapp_cfg_or_503(request: Request):
    """(cfg, None) when the channel is configured, else (None, 503)."""
    from .whatsapp import channel_config, channel_enabled
    cfg = channel_config()
    if not channel_enabled(cfg):
        return None, JSONResponse(status_code=503, content=error_envelope(
            "WHATSAPP_NOT_CONFIGURED",
            "WhatsApp is not configured on this server "
            "(AGENTQON_WHATSAPP_TOKEN / AGENTQON_WHATSAPP_PHONE_NUMBER_ID "
            "are unset)", corr(request)))
    return cfg, None


@app.post("/v1/channels/whatsapp/link-code", status_code=201)
async def whatsapp_link_code_ep(request: Request):
    """Mint a one-time link code (~10 min, single-use) binding a
    WhatsApp number to THIS account. The user sends 'LINK <code>' to
    the business number (or taps the wa.me link); minting replaces
    older unused codes."""
    cfg, err = _whatsapp_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .whatsapp import new_link_code
    record = new_link_code(store, ident.tenant_id, ident.user_id,
                           ident.role, cfg)
    store.audit(ident.tenant_id, ident.user_id, "whatsapp.link_code",
                "whatsapp_link", ident.user_id, {})
    return {"code": record["code"], "expires_at": record["expires_at"],
            "business_number": record["business_number"],
            "wa_link": record["wa_link"],
            "note": "Send 'LINK <code>' to the WhatsApp business number "
                    "(or tap the wa.me link). One code works once, for "
                    "~10 minutes."}


@app.get("/v1/channels/whatsapp/status")
async def whatsapp_status_ep(request: Request):
    """Channel state for this account: configured? linked? as which
    WhatsApp number? The UI's WhatsApp panel renders from this."""
    cfg, err = _whatsapp_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .whatsapp import link_status
    return link_status(store, ident.tenant_id, ident.user_id, cfg)


@app.delete("/v1/channels/whatsapp/link")
async def whatsapp_unlink_ep(request: Request):
    """Detach this account's WhatsApp number (the number keeps its
    chat history; the bot stops recognising it until it links again)."""
    cfg, err = _whatsapp_cfg_or_503(request)
    if err is not None:
        return err
    ident = identity(request)
    from .whatsapp import unlink_account
    removed = unlink_account(store, ident.tenant_id, ident.user_id)
    if removed:
        store.audit(ident.tenant_id, ident.user_id, "whatsapp.unlinked",
                    "whatsapp_link", ident.user_id, {})
    return {"unlinked": removed}


@app.delete("/v1/channels/whatsapp/link/{target_user_id}")
async def whatsapp_admin_unlink_ep(request: Request, target_user_id: str):
    """Audit F8: an admin cuts ANOTHER user's WhatsApp link (e.g. a
    member who was removed from the workspace). The own-link endpoint
    above only ever acts on the caller's own link."""
    cfg, err = _whatsapp_cfg_or_503(request)
    if err is not None:
        return err
    ident = guard(request, "admin")
    from .whatsapp import unlink_account
    removed = unlink_account(store, ident.tenant_id, target_user_id)
    if removed:
        store.audit(ident.tenant_id, ident.user_id,
                    "whatsapp.unlinked_admin", "whatsapp_link",
                    target_user_id, {})
    return {"unlinked": removed, "user_id": target_user_id}


@app.get("/v1/channels/whatsapp/webhook")
async def whatsapp_webhook_verify_ep(request: Request):
    """Meta's subscription handshake: hub.mode=subscribe plus the
    verify token we chose earns the hub.challenge back as plain
    text; anything else is a 403."""
    cfg, err = _whatsapp_cfg_or_503(request)
    if err is not None:
        return err
    from .whatsapp import verify_handshake
    challenge = verify_handshake(dict(request.query_params),
                                 cfg.whatsapp_verify_token)
    if challenge is None:
        return JSONResponse(status_code=403, content=error_envelope(
            "FORBIDDEN", "bad WhatsApp webhook verification attempt",
            corr(request)))
    return PlainTextResponse(challenge)


@app.post("/v1/channels/whatsapp/webhook")
async def whatsapp_webhook_ep(request: Request):
    """Meta's event delivery. No AgentQon credential is involved:
    authenticity is the X-Hub-Signature-256 HMAC over the raw body.
    Shield H4: the app secret is MANDATORY whenever the channel is
    enabled — an enabled-but-unsigned channel refuses every delivery
    (fail closed, exactly like the Telegram door), identity comes
    from the number's link record, and messages are deduped by wamid.
    Valid deliveries always get a fast 200 — missions are queued,
    never run inside the request."""
    from .whatsapp import channel_config, channel_enabled, \
        handle_payload, verify_signature
    cfg = channel_config()
    if not channel_enabled(cfg):
        return JSONResponse(status_code=503, content=error_envelope(
            "WHATSAPP_NOT_CONFIGURED",
            "WhatsApp is not configured on this server "
            "(AGENTQON_WHATSAPP_TOKEN / AGENTQON_WHATSAPP_PHONE_NUMBER_ID "
            "are unset)", corr(request)))
    if not cfg.whatsapp_app_secret:
        security_log(settings, "shield.whatsapp_unsigned_refused")
        return JSONResponse(status_code=503, content=error_envelope(
            "WHATSAPP_APP_SECRET_REQUIRED",
            "WhatsApp deliveries are refused: AGENTQON_WHATSAPP_APP_SECRET "
            "is unset, so webhook authenticity cannot be verified "
            "(fail closed)", corr(request)))
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if not verify_signature(cfg.whatsapp_app_secret, raw, signature):
        security_log(settings, "shield.whatsapp_bad_signature")
        return JSONResponse(status_code=403, content=error_envelope(
            "FORBIDDEN", "bad or missing WhatsApp webhook signature",
            corr(request)))
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "webhook body must be JSON", corr(request)))
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "webhook body must be a JSON object",
            corr(request)))
    result = await handle_payload(store, orchestrator, payload, cfg,
                                  vault=vault)
    return {"ok": True, **result}


# ======================================================================
# v18 — WhatsApp self-serve onboarding (Embedded Signup,
# app/whatsapp_onboard.py). A tenant connects its OWN WhatsApp
# business number with one Facebook login in the browser; the
# exchange below turns the login code into a verified long-lived
# token stored encrypted in the tenant's BYOK vault. Independent of
# the v17 env channel above (which stays the fallback): these
# routes answer even when AGENTQON_WHATSAPP_TOKEN is unset.
# ======================================================================
@app.get("/v1/whatsapp/onboard/public-config")
async def whatsapp_onboard_public_config_ep(request: Request):
    """Is one-click connect enabled on this deployment, and with
    which (public) Meta app id + Login-for-Business configuration
    id? The browser needs both to run FB.login; neither is secret."""
    identity(request)
    from .whatsapp_onboard import public_config
    return public_config()


class WhatsAppExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=2000)
    waba_id: str = Field(min_length=1, max_length=64)
    phone_number_id: str = Field(min_length=1, max_length=64)


@app.post("/v1/whatsapp/onboard/exchange")
async def whatsapp_onboard_exchange_ep(request: Request,
                                       body: WhatsAppExchangeBody):
    """Complete an Embedded Signup for THIS tenant: code -> token ->
    long-lived -> scope-verified -> webhook-subscribed -> stored
    encrypted. Any failure stores nothing and explains itself."""
    # Shield H3: connecting the tenant's WhatsApp business identity
    # is an admin action (it replaces who the tenant is on WhatsApp).
    ident = guard(request, "admin")
    from .whatsapp_onboard import (OnboardError, exchange_configured,
                                    exchange_signup, onboard_settings)
    cfg = onboard_settings()
    if not exchange_configured(cfg):
        return JSONResponse(status_code=503, content=error_envelope(
            "WHATSAPP_ONBOARD_NOT_CONFIGURED",
            "One-click WhatsApp connect is not configured on this "
            "server (AGENTQON_META_APP_ID / AGENTQON_WHATSAPP_CONFIG_ID "
            "/ AGENTQON_WHATSAPP_APP_SECRET are unset)", corr(request)))
    if vault is None:
        return JSONResponse(status_code=503, content=error_envelope(
            "VAULT_UNAVAILABLE",
            "Credential vault is not configured on this deployment",
            corr(request)))
    try:
        return exchange_signup(store, vault, ident.tenant_id,
                               ident.user_id, body.code, body.waba_id,
                               body.phone_number_id, cfg)
    except OnboardError as exc:
        return JSONResponse(status_code=exc.status_code,
                            content=error_envelope(
                                "WHATSAPP_ONBOARD_FAILED", exc.message,
                                corr(request)))


@app.get("/v1/whatsapp/connection")
async def whatsapp_connection_ep(request: Request):
    """This tenant's self-serve WhatsApp connection: connected? as
    which business number? (The v17 env-channel link status lives
    at /v1/channels/whatsapp/status — different thing.)"""
    ident = identity(request)
    from .whatsapp_onboard import connection_status
    return connection_status(store, ident.tenant_id)


@app.delete("/v1/whatsapp/connection")
async def whatsapp_connection_delete_ep(request: Request):
    """Disconnect this tenant's WhatsApp business number: best-effort
    unsubscribe at Meta, then the stored token is crypto-shredded."""
    # Shield H3: disconnecting shreds the stored token — admin only.
    ident = guard(request, "admin")
    from .whatsapp_onboard import disconnect
    return disconnect(store, vault, ident.tenant_id, ident.user_id)


# ======================================================================
# V3 — BYOK connections (Master Build Part XXII)
# ======================================================================
class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(omniroute_gateway|direct_provider|local_endpoint)$")
    provider_key: str = Field(default="", max_length=80)
    label: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=4, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=2000)
    allowed_models: list[str] = Field(default_factory=list, max_length=200)
    blocked_models: list[str] = Field(default_factory=list, max_length=200)
    budget_limit_usd: Optional[float] = Field(default=None, gt=0)
    routing_mode: str = Field(default="balanced", max_length=40)
    data_policy: dict = Field(default_factory=dict)
    # Per-connection custom headers (item 5) — validated for real
    # in connections.validate_custom_headers; values are secrets.
    headers: dict = Field(default_factory=dict)


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Optional[str] = Field(default=None, max_length=120)
    allowed_models: Optional[list[str]] = Field(default=None, max_length=200)
    blocked_models: Optional[list[str]] = Field(default=None, max_length=200)
    budget_limit_usd: Optional[float] = Field(default=None, gt=0)
    routing_mode: Optional[str] = Field(default=None, max_length=40)
    data_policy: Optional[dict] = None


class RotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=2000)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


def _conn_or_404(request: Request, t: str, connection_id: str):
    conn = store.get_connection(t, connection_id)
    if conn is None:
        return None
    return conn


@app.post("/v1/connections", status_code=201)
async def create_connection(request: Request, body: ConnectionBody):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    if vault is None:
        return JSONResponse(status_code=503, content=error_envelope(
            "VAULT_UNAVAILABLE", "Credential vault is not configured on this deployment",
            corr(request)))
    from .connections import create_connection as _create
    from .netpolicy import UrlPolicyError
    try:
        return _create(store, vault, t, u, body.model_dump())
    except UrlPolicyError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "URL_POLICY", f"{exc.category}: {exc}", corr(request)))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))


@app.get("/v1/connections")
async def list_connections(request: Request):
    t = tenant(request)
    from .connections import public_view
    return {"connections": [public_view(c) for c in store.list_connections(t)]}


@app.get("/v1/connections/{connection_id}")
async def get_connection(request: Request, connection_id: str):
    t = tenant(request)
    conn = _conn_or_404(request, t, connection_id)
    if conn is None:
        return not_found(request)
    from .connections import public_view
    return public_view(conn)


@app.post("/v1/connections/{connection_id}/validate")
async def validate_connection_ep(request: Request, connection_id: str):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    rate_guard(f"validate:{t}", settings.rate_validate_per_min)
    if _conn_or_404(request, t, connection_id) is None:
        return not_found(request)
    from .connections import validate_connection, get_secret_for_call
    from .catalog import sync_connection_catalog
    result = await validate_connection(store, vault, t, u, connection_id)
    if result["state"] == "active":
        conn = store.get_connection(t, connection_id)
        secret = get_secret_for_call(store, vault, t, connection_id)
        sync = await sync_connection_catalog(store, t, conn, secret)
        result["catalog_sync"] = sync
    return result


@app.post("/v1/connections/{connection_id}/rotate")
async def rotate_connection_ep(request: Request, connection_id: str, body: RotateBody):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    if _conn_or_404(request, t, connection_id) is None:
        return not_found(request)
    from .connections import rotate_connection
    return rotate_connection(store, vault, t, u, connection_id, body.api_key)


@app.patch("/v1/connections/{connection_id}")
async def patch_connection(request: Request, connection_id: str, body: ConnectionPatch):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    conn = _conn_or_404(request, t, connection_id)
    if conn is None:
        return not_found(request)
    for field in ("label", "allowed_models", "blocked_models", "budget_limit_usd",
                  "routing_mode", "data_policy"):
        value = getattr(body, field)
        if value is not None:
            conn[field] = value
    from .store import now_iso
    conn["updated_at"] = now_iso()
    store.put_connection(t, conn)
    store.audit(t, u, "RoutingPolicyChanged", "connection", connection_id,
                {"fields": [f for f in body.model_dump() if getattr(body, f) is not None]})
    from .connections import public_view
    return public_view(conn)


@app.post("/v1/connections/{connection_id}/pause")
async def pause_connection(request: Request, connection_id: str):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    conn = _conn_or_404(request, t, connection_id)
    if conn is None:
        return not_found(request)
    from .connections import _set_state
    try:
        conn = _set_state(store, t, conn, "disabled")
    except ValueError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))
    store.audit(t, u, "GatewayConnectionPaused", "connection", connection_id, {})
    from .connections import public_view
    return public_view(conn)


@app.post("/v1/connections/{connection_id}/revoke")
async def revoke_connection_ep(request: Request, connection_id: str):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    if _conn_or_404(request, t, connection_id) is None:
        return not_found(request)
    from .connections import revoke_connection
    try:
        return revoke_connection(store, t, u, connection_id)
    except ValueError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))


@app.delete("/v1/connections/{connection_id}")
async def delete_connection_ep(request: Request, connection_id: str, body: ConfirmBody):
    guard(request, "admin")
    t, u = tenant(request), user(request)
    if _conn_or_404(request, t, connection_id) is None:
        return not_found(request)
    if not body.confirm:
        return JSONResponse(status_code=400, content=error_envelope(
            "CONFIRMATION_REQUIRED", "Deleting a connection destroys its stored credential; "
            "send {\"confirm\": true} to proceed", corr(request)))
    from .connections import delete_connection
    delete_connection(store, t, u, connection_id)
    return {"deleted": connection_id}


# ======================================================================
# V3 — catalog + routing (Master Build §105.1/§105.4)
# ======================================================================
@app.get("/v1/models")
async def list_models(request: Request, connection_id: Optional[str] = None):
    t = tenant(request)
    from .catalog import public_catalog_entry
    entries = store.list_catalog(t, connection_id)
    return {"models": [public_catalog_entry(e) for e in entries],
            "connections": [{"id": c["id"], "label": c["label"], "state": c["state"]}
                            for c in store.list_connections(t)]}


@app.post("/v1/models/sync")
async def sync_models(request: Request):
    guard(request, "admin")
    t = tenant(request)
    from .catalog import sync_connection_catalog
    from .connections import get_secret_for_call
    results = []
    for conn in store.list_connections(t):
        if conn["state"] != "active":
            continue
        if conn["kind"] == "whatsapp":
            continue  # v18: a messaging credential, not a model gateway
        if settings.auth_mode == "accounts":
            # Audit F7: sync inherits the hosted-mode refusal — a
            # stored local-target connection is never probed.
            from .connections import _url_is_local, \
                LOCAL_TARGET_HOSTED_CATEGORY
            if _url_is_local(conn["base_url"], conn["kind"]):
                results.append({"connection_id": conn["id"],
                                "status": "skipped",
                                "error": LOCAL_TARGET_HOSTED_CATEGORY,
                                "models": 0})
                continue
        secret = get_secret_for_call(store, vault, t, conn["id"])
        results.append({"connection_id": conn["id"], **await sync_connection_catalog(
            store, t, conn, secret)})
    return {"syncs": results}


@app.post("/v1/models/self-test")
async def start_model_self_test(request: Request):
    """Model self-test ("Test all", 2026-10-22): probe every model
    the calling tenant could use — their own connections plus the
    platform leg — with one real 1-token completion each, in the
    background (app/modeltest.py). Returns {run_id, total}; a run
    already in progress for the tenant is returned instead of
    starting a second one."""
    guard(request, "admin")
    t, u = tenant(request), user(request)
    run = await model_tester.start_run(t)
    if run.fresh:
        run.fresh = False
        store.audit(t, u, "ModelSelfTestStarted", "models", run.run_id,
                    {"total": run.total})
    return {"run_id": run.run_id, "total": run.total}


@app.get("/v1/models/self-test/{run_id}")
async def get_model_self_test(request: Request, run_id: str):
    """Poll a self-test run: state, progress, and the per-model
    verdicts accumulated so far. Runs are tenant-owned — another
    tenant's run_id is a 404, like every resource here."""
    t = tenant(request)
    run = model_tester.get_run(t, run_id)
    if run is None:
        return not_found(request)
    return run.view()


# ------------------------------------------------------------ societies (2026-10-22)
from .society import ConfirmationRequired as _SocietyConfirmationRequired
from .society import SocietyCreate as _SocietyCreate
from .society import SocietyNoModel as _SocietyNoModel
from .society import SocietyNotFound as _SocietyNotFound
from .society import SocietyRun as _SocietyRun
from .society import SocietyStateError as _SocietyStateError


@app.get("/v1/provider-presets")
async def provider_presets_ep(request: Request):
    """The BYOK form's provider picker (2026-10-23, items 5+6):
    the presets that honestly work as OpenAI-style API-key
    endpoints. Picking one fills the address (and, for AgentRouter,
    the identity headers it demands)."""
    guard(request, "viewer")
    from .presets import picker_presets
    return {"presets": picker_presets()}


# ------------------------------------------------------------ combos (2026-10-23, item 7)
import uuid as _uuid

from .store import now_iso as _now_iso


class ComboBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    strategy: str = Field(default="priority", max_length=40)
    targets: list[dict] = Field(default_factory=list, max_length=50)
    enabled: bool = True


class ComboMappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=200)
    combo_id: str = Field(min_length=1, max_length=80)
    priority: int = Field(default=100)
    enabled: bool = True


def _combo_view(combo: dict) -> dict:
    return {"combo_id": combo["combo_id"], "name": combo["name"],
            "strategy": combo["strategy"], "targets": combo["targets"],
            "enabled": combo.get("enabled", True),
            "state": combo.get("state", {})}


@app.get("/v1/combos/strategies")
async def combo_strategies_ep(request: Request):
    """The honest strategy list: what each picker strategy DOES,
    and the OmniRoute strategies this build deliberately does not
    offer, each with its reason (app/combos.py)."""
    guard(request, "viewer")
    from .combos import DEFERRED_STRATEGIES, REAL_STRATEGIES
    return {"strategies": [{"id": k, "description": v}
                           for k, v in REAL_STRATEGIES.items()],
            "deferred": [{"id": k, "reason": v}
                         for k, v in DEFERRED_STRATEGIES.items()]}


@app.get("/v1/combos")
async def list_combos_ep(request: Request):
    guard(request, "viewer")
    t = tenant(request)
    return {"combos": [_combo_view(c) for c in store.list_combos(t)],
            "mappings": store.list_combo_mappings(t)}


@app.post("/v1/combos", status_code=201)
async def create_combo_ep(request: Request, body: ComboBody):
    guard(request, "member")
    t, u = tenant(request), user(request)
    from .combos import new_combo_id, validate_combo
    try:
        clean = validate_combo(body.model_dump())
    except ValueError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    combo = {"combo_id": new_combo_id(), **clean,
             "state": {"rr": 0, "sticky": None, "last_good": None,
                       "calls": {}, "failures": {}, "bag": []},
             "created_at": _now_iso(), "updated_at": _now_iso()}
    store.put_combo(t, combo)
    store.audit(t, u, "ComboCreated", "combo", combo["combo_id"],
                {"name": combo["name"], "strategy": combo["strategy"],
                 "targets": len(combo["targets"])})
    return _combo_view(combo)


@app.put("/v1/combos/{combo_id}")
async def update_combo_ep(request: Request, combo_id: str, body: ComboBody):
    guard(request, "member")
    t, u = tenant(request), user(request)
    from .combos import validate_combo
    existing = store.get_combo(t, combo_id)
    if not existing:
        return not_found(request)
    try:
        clean = validate_combo(body.model_dump())
    except ValueError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    combo = {**existing, **clean, "updated_at": _now_iso()}
    store.put_combo(t, combo)
    store.audit(t, u, "ComboUpdated", "combo", combo_id,
                {"name": combo["name"], "strategy": combo["strategy"]})
    return _combo_view(combo)


@app.delete("/v1/combos/{combo_id}")
async def delete_combo_ep(request: Request, combo_id: str):
    guard(request, "member")
    t, u = tenant(request), user(request)
    if not store.get_combo(t, combo_id):
        return not_found(request)
    store.delete_combo(t, combo_id)
    store.audit(t, u, "ComboDeleted", "combo", combo_id, {})
    return {"deleted": combo_id}


@app.get("/v1/combos/auto/preview")
async def auto_combo_preview_ep(request: Request):
    """What the AUTO combo would try right now, in order — the
    live-scored chain over every eligible connected model."""
    guard(request, "viewer")
    t = tenant(request)
    from .combos import auto_targets
    connections = {c["id"]: c for c in store.list_connections(t)}
    entries = store.list_catalog(t)
    chain = auto_targets(entries, connections)
    return {"chain": [{"connection_id": e["connection_id"],
                       "model_id": e["model_id"]} for e, _c in chain]}


@app.post("/v1/combo-mappings", status_code=201)
async def create_mapping_ep(request: Request, body: ComboMappingBody):
    guard(request, "member")
    t, u = tenant(request), user(request)
    from .combos import validate_mapping
    try:
        clean = validate_mapping(body.model_dump())
    except ValueError as exc:
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", str(exc), corr(request)))
    if not store.get_combo(t, clean["combo_id"]):
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR", "the mapping points at a combo that "
            "does not exist", corr(request)))
    mapping = {"mapping_id": "cmap_" + _uuid.uuid4().hex[:12], **clean}
    store.put_combo_mapping(t, mapping)
    store.audit(t, u, "ComboMappingCreated", "combo_mapping",
                mapping["mapping_id"],
                {"pattern": mapping["pattern"],
                 "combo_id": mapping["combo_id"]})
    return mapping


@app.delete("/v1/combo-mappings/{mapping_id}")
async def delete_mapping_ep(request: Request, mapping_id: str):
    guard(request, "member")
    t, u = tenant(request), user(request)
    store.delete_combo_mapping(t, mapping_id)
    store.audit(t, u, "ComboMappingDeleted", "combo_mapping", mapping_id, {})
    return {"deleted": mapping_id}


@app.get("/v1/societies")
async def list_societies_ep(request: Request):
    """The Simulations section's runs list (2026-10-23): light rows
    for every world run in this workspace, newest first — the store
    method existed; this is the read path the section needed."""
    guard(request, "viewer")
    t = tenant(request)
    return {"societies": society_service.list_views(t)}


@app.post("/v1/societies", status_code=201)
async def create_society_ep(request: Request, body: _SocietyCreate):
    """Size + cast a society for a completed mission (or a standalone
    task text). This NEVER runs anything (the feature is opt-in): the
    record lands in state "sized" carrying the sizer's estimate and
    reason, and only an explicit run call spends a single model
    call."""
    guard(request, "member")
    t, u = tenant(request), user(request)
    if bool(body.mission_id) == bool(body.task_text and body.task_text.strip()):
        return JSONResponse(status_code=400, content=error_envelope(
            "VALIDATION_ERROR",
            "give exactly one of mission_id or task_text", corr(request)))
    try:
        record = society_service.create_society(
            t, u, mission_id=body.mission_id, task_text=body.task_text,
            seed=body.seed)
    except _SocietyNotFound:
        return not_found(request)
    except _SocietyStateError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))
    store.audit(t, u, "SocietyCreated", "society", record["society_id"],
                {"size": record["sizer"]["size"],
                 "mission_id": record.get("mission_id")})
    return society_service.view(record)


@app.post("/v1/societies/{society_id}/run")
async def run_society_ep(request: Request, society_id: str,
                         body: _SocietyRun):
    """Start the (background) society run. Rooms above 100 agents
    refuse without confirm=true — the estimate was shown at creation;
    this is the fence that makes the user own the spend."""
    guard(request, "member")
    t, u = tenant(request), user(request)
    if store.get_flag("kill_switch") == "on":
        return JSONResponse(status_code=503, content=error_envelope(
            "KILL_SWITCH_ACTIVE",
            "Execution is paused platform-wide by an administrator",
            corr(request)))
    try:
        record = society_service.start_run(t, society_id,
                                           confirm=body.confirm)
    except _SocietyNotFound:
        return not_found(request)
    except _SocietyConfirmationRequired as exc:
        return JSONResponse(status_code=422, content=error_envelope(
            "CONFIRMATION_REQUIRED", str(exc), corr(request)))
    except _SocietyStateError as exc:
        return JSONResponse(status_code=409, content=error_envelope(
            "STATE_CONFLICT", str(exc), corr(request)))
    except _SocietyNoModel as exc:
        return JSONResponse(status_code=503, content=error_envelope(
            "model_not_connected", str(exc), corr(request)))
    store.audit(t, u, "SocietyRunStarted", "society", society_id,
                {"size": record["sizer"]["size"],
                 "est_calls": record["sizer"]["est_calls"]})
    return society_service.view(record)


@app.get("/v1/societies/{society_id}")
async def get_society_ep(request: Request, society_id: str,
                         events: int = 0):
    """The society: state, the sizer's decision, the derived summary,
    the world output and the network graph — all computed from the
    ordered event record. The events themselves ride along only with
    ?events=1 (capped). Another tenant's society is a 404."""
    t = tenant(request)
    try:
        record = society_service.get_society(t, society_id)
    except _SocietyNotFound:
        return not_found(request)
    return society_service.view(record, include_events=bool(events))


@app.post("/v1/societies/{society_id}/cancel")
async def cancel_society_ep(request: Request, society_id: str):
    guard(request, "member")
    t = tenant(request)
    try:
        record = society_service.cancel(t, society_id)
    except _SocietyNotFound:
        return not_found(request)
    return society_service.view(record)


class RoutingPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="balanced", max_length=40)
    role: Optional[str] = None
    pinned_model_id: Optional[str] = None
    connection_id: Optional[str] = None
    data_classification: str = Field(default="internal", max_length=20)
    budget_remaining_usd: Optional[float] = None


@app.post("/v1/routing/preview")
async def routing_preview(request: Request, body: RoutingPreviewBody):
    """Explain a route decision without running a generation (§119.2)."""
    t = tenant(request)
    from .router import RouteRequest, route, NoEligibleModel
    connections = {c["id"]: c for c in store.list_connections(t)}
    entries = store.list_catalog(t)
    platform_conn, platform_entry = orchestrator._platform_connection()
    connections["platform"] = {**platform_conn, "tenant_id": t}
    entries = entries + [platform_entry]
    if body.connection_id:
        entries = [e for e in entries if e["connection_id"] == body.connection_id]
    req = RouteRequest(tenant_id=t, mode=body.mode, role=body.role,
                       pinned_model_id=body.pinned_model_id,
                       data_classification=body.data_classification,
                       budget_remaining_usd=body.budget_remaining_usd,
                       unknown_price_reserve_usd=settings.reserve_unknown_call_usd)
    try:
        decision = route(entries, connections, {}, req)
    except NoEligibleModel as exc:
        return JSONResponse(status_code=422, content={
            "error": {"code": "NO_ELIGIBLE_MODEL",
                      "message": "No model satisfies the hard constraints",
                      "filtered_counts": exc.filtered_counts},
            "correlation_id": corr(request)})
    decision.pop("id", None)
    return {"decision": decision}


@app.get("/v1/missions/{mission_id}/route-decisions")
async def mission_route_decisions(request: Request, mission_id: str):
    t = tenant(request)
    if store.get_mission(t, mission_id) is None:
        return not_found(request)
    return {"route_decisions": store.list_route_decisions(t, mission_id)}


@app.get("/v1/audit")
async def audit_log(request: Request):
    t = tenant(request)
    return {"audit": store.list_audit(t)}


# ======================================================================
# V3 — admin (kill switch + overview), gated by AGENTQON_ADMIN_KEY
# ======================================================================
def _require_admin(request: Request):
    import hmac
    if not settings.admin_key:
        return JSONResponse(status_code=403, content=error_envelope(
            "ADMIN_DISABLED", "Admin endpoints are disabled (AGENTQON_ADMIN_KEY unset)",
            corr(request)))
    provided = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(provided, settings.admin_key):
        return JSONResponse(status_code=403, content=error_envelope(
            "FORBIDDEN", "Invalid admin key", corr(request)))
    return None


class KillSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


@app.get("/v1/admin/overview")
async def admin_overview(request: Request):
    denied = _require_admin(request)
    if denied:
        return denied
    jobs = store._all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    dead = store._all("SELECT COUNT(*) AS n FROM jobs WHERE status='dead'")
    spend = store._all("SELECT tenant_id, ROUND(SUM(amount_usd),4) AS total FROM usage_records"
                       " GROUP BY tenant_id ORDER BY total DESC LIMIT 20")
    conns = store._all("SELECT state, COUNT(*) AS n FROM connections"
                       " WHERE deleted_at IS NULL GROUP BY state")
    return {"kill_switch": store.get_flag("kill_switch") or "off",
            "jobs_by_status": {r["status"]: r["n"] for r in jobs},
            "dead_jobs": dead[0]["n"] if dead else 0,
            "spend_by_tenant": spend,
            "connections_by_state": {r["state"]: r["n"] for r in conns},
            "backend": "postgres" if store.pg else "sqlite"}


@app.post("/v1/admin/kill-switch")
async def admin_kill_switch(request: Request, body: KillSwitchBody):
    denied = _require_admin(request)
    if denied:
        return denied
    store.set_flag("kill_switch", "on" if body.on else "off")
    store.audit("platform", "platform-admin", "KillSwitchChanged", "platform", "kill_switch",
                {"on": body.on})
    return {"kill_switch": "on" if body.on else "off"}


@app.post("/v1/admin/vault/rewrap")
async def admin_vault_rewrap(request: Request):
    """Re-encrypt all stored BYOK secrets onto the active vault key
    version (Phase 4 rotation completion). Admin-key gated."""
    denied = _require_admin(request)
    if denied:
        return denied
    if vault is None:
        return JSONResponse(status_code=503, content=error_envelope(
            "VAULT_UNAVAILABLE", "credential vault is not configured",
            corr(request), False))
    from .vault import rewrap_all_secrets
    result = rewrap_all_secrets(store, vault)
    store.audit("platform", "platform-admin", "VaultRewrap", "platform",
                "vault", result)
    return result


@app.get("/v1/usage")
async def usage(request: Request):
    t = tenant(request)
    return store.usage_summary(t)
