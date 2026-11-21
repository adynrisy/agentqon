"""Model self-test (2026-10-22, Zohaib's order): a "Test all" for the
Models section — the harness checks every model the tenant could use
and reports, per model, whether it actually answers.

A model being LISTED proves nothing (see catalog.py's capability
honesty note), so each probe is the smallest REAL check the gateway
can make: one chat completion — "ping", max_tokens=1 — through
GatewayClient. The verdict therefore means "this model answers", and
failures land in a small stable vocabulary the UI can word plainly:

    auth_failed     the provider refused the credential (401/403)
    model_not_found the provider says no such model (404)
    rate_limited    429, after the gateway's own retry budget
    timeout         no answer inside the probe timeout
    provider_down   the provider answered 5xx
    unreachable     connection refused / DNS / network failure
    unknown         anything else

Scope (contract M1): the tenant's own connections in the states a
validated connection can be in — active, degraded, rate_limited,
auth_failed (draft was never validated, disabled is user-off, and
revoked/deleted credentials are shredded; the whatsapp kind is a
messaging credential, not a model gateway, and is excluded exactly
as /v1/models/sync excludes it) — PLUS the platform leg exactly as
platform model state sees it (env endpoint / OmniRoute / Ollama
auto-detect) under the synthetic connection id "platform".

Execution: POST discovers the model set, then probes run in the
background on a dedicated thread with its own event loop, at most
MAX_CONCURRENCY at once, results accumulating on the run record
for polling. Runs live in an in-memory registry (the results only
need to survive a few minutes of polling); finished runs are
evicted after RUN_RETENTION_S.

Probes ride the gateway's pacing + 429 retry machinery — there is no
parallel retry loop here. The one deliberate deviation: probe clients
are built with max_retries=0, so a dead or slow endpoint gets ONE
attempt per probe (a self-test wants a fast, honest verdict; the
client's 429 budget is a separate counter and still applies in
full, which is what "rate_limited (after retries)" means).
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from .catalog import normalize_model
from .gateway_client import GatewayClient, GatewayError
from .llm_adapter import _is_local_endpoint
from .modelstate import platform_state, probe_models

PROBE_TIMEOUT_S = 20.0
MAX_CONCURRENCY = 3
MAX_MODELS_PER_CONNECTION = 40
CAP_NOTE = "Only the first 40 models per connection were tested"
RUN_RETENTION_S = 600.0
TESTABLE_STATES = ("active", "degraded", "rate_limited", "auth_failed")
PLATFORM_CONNECTION_ID = "platform"
PLATFORM_LABEL = "Platform"

ERROR_CODES = ("auth_failed", "model_not_found", "rate_limited", "timeout",
               "provider_down", "unreachable", "unknown")


def error_code_for(exc: BaseException) -> str:
    """Map a gateway failure onto the self-test vocabulary. Driven by
    GatewayError's category + HTTP status: provider_unavailable with
    a 5xx status is the provider answering badly (provider_down);
    without a status it is a transport failure (unreachable)."""
    if isinstance(exc, GatewayError):
        if exc.category == "auth_failed":
            return "auth_failed"
        if exc.category == "rate_limited":
            return "rate_limited"
        if exc.category == "timeout":
            return "timeout"
        if exc.status == 404:
            return "model_not_found"
        if exc.category == "provider_unavailable":
            return "provider_down" if (exc.status or 0) >= 500 else "unreachable"
    return "unknown"


@dataclass
class _Run:
    run_id: str
    tenant_id: str
    state: str = "running"          # running | done
    total: int = 0
    capped: bool = False
    results: list = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    fresh: bool = False             # True only on the creating start_run
    thread: Optional[threading.Thread] = field(default=None, repr=False)

    def view(self) -> dict:
        return {"run_id": self.run_id, "state": self.state,
                "total": self.total, "done_count": len(self.results),
                "results": list(self.results),
                "capped": self.capped,
                "cap_note": CAP_NOTE if self.capped else None}


@dataclass
class _Target:
    """One connection leg of a run: where its models live and how to
    reach them. `client` is built lazily at discovery time; a build
    failure is kept and reported against every model on the leg."""
    connection_id: str
    connection_label: str
    source: str                     # tenant | platform
    conn: Optional[dict] = None     # the dict the client is built from
    client: Optional[GatewayClient] = None
    build_error: Optional[BaseException] = None
    declared: list = field(default_factory=list)   # allowed_models
    blocked: set = field(default_factory=set)
    local: bool = False
    catalog_models: list = field(default_factory=list)


class ModelSelfTester:
    """Registry + runner for model self-test runs. Constructed once
    with the app singletons (see app/main.py); `client_factory` is
    the test seam — the validate_connection precedent — receiving
    (conn, tenant_id) and returning a GatewayClient for that leg
    (the platform leg's conn is the orchestrator's synthetic
    platform connection)."""

    def __init__(self, store, settings, orchestrator, *,
                 client_factory: Optional[Callable] = None,
                 probe_timeout_s: float = PROBE_TIMEOUT_S,
                 max_models_per_connection: int = MAX_MODELS_PER_CONNECTION,
                 concurrency: int = MAX_CONCURRENCY):
        self.store = store
        self.settings = settings
        self.orchestrator = orchestrator
        self.client_factory = client_factory
        self.probe_timeout_s = probe_timeout_s
        self.max_models_per_connection = max_models_per_connection
        self.concurrency = concurrency
        self._runs: dict[str, _Run] = {}
        self._by_tenant: dict[str, str] = {}

    # ------------------------------------------------------------ runs
    def _retire(self, run: _Run) -> None:
        """End a run that will never probe (discovery or thread-start
        failure, a probe thread that died silently): mark it done so
        pollers see a terminal state, and free the tenant's slot so
        the next start_run creates a fresh run instead of returning
        this corpse (audit B6)."""
        run.state = "done"
        if self._by_tenant.get(run.tenant_id) == run.run_id:
            del self._by_tenant[run.tenant_id]

    def _thread_main(self, run: _Run, jobs: list) -> None:
        # The coroutine is built INSIDE the thread: building it in
        # start_run and passing it to asyncio.run would orphan it
        # (never awaited) if thread.start() itself raises.
        asyncio.run(self._execute(run, jobs))

    async def start_run(self, tenant_id: str) -> _Run:
        """Discover the tenant's model set and start probing in the
        background. If a run is already in progress for this tenant,
        return it instead of starting a second one."""
        self._evict()
        current = self._runs.get(self._by_tenant.get(tenant_id, ""))
        if current is not None and current.state == "running":
            if current.thread is not None and not current.thread.is_alive():
                # B6 residual: the probe thread died without its
                # finally flipping the state (a hard thread-level
                # failure). Retire the corpse; fall through and start
                # a genuinely fresh run below.
                self._retire(current)
            else:
                return current
        run = _Run(run_id="mtest_" + uuid.uuid4().hex[:12],
                   tenant_id=tenant_id, fresh=True)
        # Audit B5: register the run BEFORE the discovery await. The
        # check above and this registration form one synchronous
        # segment, so a concurrent second start_run observes the
        # registered run and returns it — previously registration
        # happened after `await self._discover(...)`, and two
        # concurrent callers both passed the check and started two
        # runs (double probe traffic, the first orphaned from
        # _by_tenant).
        self._runs[run.run_id] = run
        self._by_tenant[tenant_id] = run.run_id
        try:
            jobs = await self._discover(tenant_id, run)
        except BaseException:
            # A failed discovery must not leave the tenant pointing
            # at a run that will never probe (B6's wedge shape).
            self._retire(run)
            raise
        run.total = len(jobs)
        # Discovery clients were built — and their httpx pools bound —
        # on this request's event loop; probes run on the run's own
        # thread loop, where a foreign pool reads as a dead
        # connection ("Server disconnected without sending a
        # response"). Drop them: _execute rebuilds each leg's client
        # on its own loop before probing.
        for target in {id(t): t for t, _model in jobs}.values():
            target.client = None
        # Probes run on a dedicated thread with its own event loop —
        # the worker module's pattern (asyncio.run per job), NOT a
        # task on the request's loop: a task spawned inside a request
        # can be cancelled when the request scope closes (observed
        # under the test client: the semaphore-queued probe died with
        # the POST), and a self-test must never lose results that way.
        run.thread = threading.Thread(
            target=self._thread_main, args=(run, jobs),
            name=f"model-self-test-{run.run_id}", daemon=True)
        try:
            run.thread.start()
        except BaseException:
            # B6: thread.start() failing (resource exhaustion) used
            # to leave the run registered as "running" forever —
            # every later start returned the zombie and _evict only
            # removes done runs, so the tenant's self-test stayed
            # broken until a process restart.
            self._retire(run)
            raise
        return run

    def get_run(self, tenant_id: str, run_id: str) -> Optional[_Run]:
        run = self._runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return None
        return run

    def _evict(self) -> None:
        now = time.monotonic()
        stale = [rid for rid, run in self._runs.items()
                 if run.state == "done"
                 and now - run.created_at > RUN_RETENTION_S]
        for rid in stale:
            run = self._runs.pop(rid)
            if self._by_tenant.get(run.tenant_id) == rid:
                del self._by_tenant[run.tenant_id]

    # -------------------------------------------------------- discovery
    def _client_for(self, conn: dict, tenant_id: str) -> GatewayClient:
        if self.client_factory is not None:
            return self.client_factory(conn, tenant_id)
        client = self.orchestrator._gateway_for(conn)
        # One fast, honest verdict per probe (module docstring): the
        # timeout becomes the probe timeout and transient retries are
        # off — pacing and the 429 budget inside the client still
        # apply in full.
        client.timeout = self.probe_timeout_s
        client.max_retries = 0
        return client

    def _targets(self, tenant_id: str) -> list[_Target]:
        targets: list[_Target] = []
        hosted = self.settings.auth_mode == "accounts"
        for conn in self.store.list_connections(tenant_id):
            if conn.get("kind") == "whatsapp":
                continue  # messaging credential, not a model gateway
            if conn.get("state") not in TESTABLE_STATES:
                continue
            if hosted and (conn.get("kind") == "local_endpoint"
                           or _is_local_endpoint(conn.get("base_url") or "")):
                # Audit F7: hosted mode refuses loopback/LAN targets
                # at connection create/validate; a stored local
                # connection (predating the policy) is not probed
                # either — the probe would be an internal-service
                # oracle. The platform leg below is the operator's
                # own endpoint and is unaffected.
                continue
            target = _Target(
                connection_id=conn["id"],
                connection_label=conn.get("label") or conn["id"],
                source="tenant",
                conn=conn,
                declared=list(conn.get("allowed_models") or []),
                blocked=set(conn.get("blocked_models") or []),
                local=conn.get("kind") == "local_endpoint"
                      or _is_local_endpoint(conn.get("base_url") or ""),
                catalog_models=[e.get("model_id") for e in
                                self.store.list_catalog(tenant_id, conn["id"])
                                if e.get("model_id")])
            try:
                target.client = self._client_for(conn, tenant_id)
            except Exception as exc:  # e.g. shredded secret, vault down
                target.build_error = exc
            targets.append(target)
        pstate = platform_state(self.settings)
        if pstate.connected and pstate.base_url:
            conn, _entry = self.orchestrator._platform_connection(
                base_url=pstate.base_url, model=pstate.model)
            target = _Target(connection_id=PLATFORM_CONNECTION_ID,
                             connection_label=PLATFORM_LABEL,
                             source="platform",
                             conn=conn,
                             local=_is_local_endpoint(pstate.base_url))
            try:
                target.client = self._client_for(conn, tenant_id)
            except Exception as exc:
                target.build_error = exc
            targets.append(target)
        return targets

    async def _discover(self, tenant_id: str,
                        run: _Run) -> list[tuple[_Target, str]]:
        """Resolve every leg's model set (live list first, catalog
        fallback), apply declared/blocked + the per-connection cap,
        and return the flat probe job list."""
        # Audit B8: _targets() resolves platform state, which probes
        # endpoints with a blocking httpx call (up to its timeout on
        # a dead endpoint). Discovery runs on the POST request's
        # event loop, so the sync target resolution goes to a thread
        # — the loop must never stall on a probe.
        targets = await asyncio.to_thread(self._targets, tenant_id)
        listed = await asyncio.gather(
            *(self._list_models(t) for t in targets),
            return_exceptions=True)
        jobs: list[tuple[_Target, str]] = []
        for target, models in zip(targets, listed):
            if isinstance(models, BaseException) or models is None:
                models = list(target.catalog_models)
            seen: list[str] = []
            for model_id in list(models) + target.declared:
                if model_id and model_id not in target.blocked \
                        and model_id not in seen:
                    seen.append(model_id)
            if len(seen) > self.max_models_per_connection:
                run.capped = True
                seen = seen[:self.max_models_per_connection]
            jobs.extend((target, m) for m in seen)
        return jobs

    async def _list_models(self, target: _Target) -> Optional[list[str]]:
        if target.source == "platform":
            # Exactly what model-state sees: its own probe of the
            # resolved platform endpoint. A trusted cloud env endpoint
            # is never probed by model-state (it is believed until a
            # call proves otherwise); probe_models returning None
            # there means "use the one configured model".
            if target.client is None:
                return []
            # B8: probe_models / platform_state block on httpx (see
            # _discover) — keep them off the request's event loop.
            models = await asyncio.to_thread(
                probe_models, target.client.base_url,
                self.settings.llm_api_key)
            if models is None:
                state = await asyncio.to_thread(platform_state,
                                                self.settings)
                return [state.model] if state.model else []
            return models
        if target.client is None:
            return None
        raw = await target.client.list_models()
        if not raw and target.local:
            raw = await target.client.list_models_ollama_native()
        ids = []
        for item in raw:
            entry = normalize_model(item, kind="direct_provider")
            if entry:
                ids.append(entry["model_id"])
        return ids

    # ---------------------------------------------------------- probing
    async def _execute(self, run: _Run, jobs: list) -> None:
        # Rebuild each leg's client on THIS loop (see start_run) via
        # the same sync factory path discovery used.
        for target in {id(t): t for t, _model in jobs}.values():
            if target.build_error is None and target.conn is not None:
                try:
                    target.client = self._client_for(target.conn,
                                                     run.tenant_id)
                except Exception as exc:  # reported per model in _probe
                    target.build_error = exc
                    target.client = None
        semaphore = asyncio.Semaphore(self.concurrency)
        try:
            # return_exceptions: one probe's escape can never end the
            # run early — every queued model must land a verdict.
            await asyncio.gather(
                *(self._probe(run, semaphore, target, model)
                  for target, model in jobs),
                return_exceptions=True)
        finally:
            run.state = "done"

    async def _probe(self, run: _Run, semaphore: asyncio.Semaphore,
                     target: _Target, model: str) -> None:
        async with semaphore:
            started = time.monotonic()
            ok, error_code = False, None
            if target.build_error is not None:
                error_code = error_code_for(target.build_error)
            elif target.client is None:
                error_code = "unknown"
            else:
                try:
                    await target.client._request(
                        "POST", "/chat/completions", json={
                            "model": model,
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 1})
                    ok = True
                except Exception as exc:  # a failed probe marks, never crashes
                    error_code = error_code_for(exc)
            run.results.append({
                "connection_id": target.connection_id,
                "connection_label": target.connection_label,
                "model": model, "ok": ok,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": error_code})
