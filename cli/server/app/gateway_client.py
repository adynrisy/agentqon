"""Typed client for any OpenAI-compatible model gateway — including a
pinned OmniRoute sidecar (Master Build V3 §104.1 `ModelGateway`).

One client, three uses: connection validation, catalog sync, and agent
inference. Errors are normalized into stable categories so callers can
distinguish auth failure from rate limiting from an outage (§105.5).

The client refuses to run without an explicit base URL, and it bypasses
environment proxies for loopback/private endpoints (see llm_adapter for
why: sandbox proxy variables break local gateways).
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from .llm_adapter import _is_local_endpoint

ERROR_CATEGORIES = ("auth_failed", "rate_limited", "timeout", "provider_unavailable",
                    "unsupported", "bad_request", "unknown")


class GatewayError(Exception):
    def __init__(self, category: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.category = category if category in ERROR_CATEGORIES else "unknown"
        self.status = status


def categorize_http(status: int) -> str:
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status in (400, 422):
        return "bad_request"
    if status == 404:
        return "unsupported"
    if status >= 500:
        return "provider_unavailable"
    return "unknown"


"""Transient-failure retry policy (v21.4.2): one flaky 503 from a
hosted provider used to kill a whole mission even when earlier calls
in the same run had succeeded. Retryable: HTTP 429/500/502/503/504,
connection errors and timeouts. Never retried: 400/401/403/404 and
other client errors — those are answers, not weather."""
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_CAP_S = 30.0

"""Rate-limit survival (v21.4.4): a 429 is not a flaky 503 — free-tier
providers cap requests PER MINUTE, so the v21.4.2 budget (4 attempts
over ~7s of backoff) could never outlast the quota window and panel
missions (dozens of calls) reliably died mid-run. For 429 the client
therefore retries up to 8 attempts total, honors Retry-After up to a
120s cap, AND paces subsequent calls to the same connection: once a
connection has answered 429, a minimum gap is enforced before the
next call to it — starting at PACE_BASE_S, doubling on repeated 429s
up to PACE_CAP_S, and forgotten after PACE_RESET_S with no 429 (the
provider's window has rolled over; a healthy connection is never
paced). Pacing state is in-memory and shared across client instances
via a module-level registry keyed by connection id, because clients
are constructed per run/call."""
RATE_LIMIT_MAX_RETRIES = 7  # retries after the first attempt → 8 total
RATE_LIMIT_RETRY_AFTER_CAP_S = 120.0
PACE_BASE_S = 5.0
PACE_CAP_S = 60.0
PACE_RESET_S = 120.0


def _now() -> float:
    """Monotonic clock for pacing math (tests monkeypatch this)."""
    return time.monotonic()


@dataclass
class _PaceState:
    gap_s: float = 0.0        # enforced minimum gap before the next call
    last_issue_at: float = 0.0  # _now() when the last attempt was issued
    last_429_at: float = 0.0  # _now() of the most recent 429 (0 = never)


# Shared across GatewayClient instances in this process: pacing must
# survive the per-run/per-call construction of clients, or every new
# mission would re-discover the limit by dying on it.
_PACE_STATES: dict[str, _PaceState] = {}


def _reset_pacing() -> None:
    """Drop all pacing state (test hygiene; harmless in production)."""
    _PACE_STATES.clear()


def pacing_snapshot() -> list[dict]:
    """Read-only view of the learned pacing state (v22 evolve
    report): which connections are currently paced, and by how much.
    Pure observability — reading it changes nothing."""
    return [{"connection_id": key, "gap_s": state.gap_s,
             "last_429_at": state.last_429_at}
            for key, state in sorted(_PACE_STATES.items())]


@dataclass
class GatewayClient:
    base_url: str
    api_key: str = ""
    timeout: float = 60.0
    transport: Optional[httpx.AsyncBaseTransport] = field(default=None, repr=False)
    # 3 retries after the first attempt (4 attempts max), backoff
    # ~1s → 2s → 4s at the default base; tests inject a tiny base.
    max_retries: int = 3
    backoff_s: float = 1.0
    # v21.4.4 rate-limit survival. connection_id keys the shared
    # pacing registry; when unset, the base URL is the pacing key.
    # on_rate_limited (when set) receives {wait_s, attempt, status,
    # reason, connection_id} whenever a call waits on rate limiting —
    # the orchestrator turns it into a mission event.
    connection_id: Optional[str] = None
    # Per-connection custom headers (2026-10-23, fix-list item 5):
    # stored on the connection, merged into every request this
    # client makes. AgentRouter is the motivating case — it turns
    # away any client that does not present a Claude Code identity.
    headers: dict = field(default_factory=dict)
    rate_limit_max_retries: int = RATE_LIMIT_MAX_RETRIES
    retry_after_cap_s: float = RETRY_AFTER_CAP_S
    rate_limit_retry_after_cap_s: float = RATE_LIMIT_RETRY_AFTER_CAP_S
    pace_base_s: float = PACE_BASE_S
    pace_cap_s: float = PACE_CAP_S
    pace_reset_s: float = PACE_RESET_S
    on_rate_limited: Optional[Callable[[dict], None]] = field(
        default=None, repr=False)

    def _client(self) -> httpx.AsyncClient:
        # Explicit egress proxy + trust_env=False: identical behaviour in
        # sane environments, immune to unparseable no_proxy values (the
        # bracketed-IPv6 crash) in restricted ones. Local endpoints never
        # go through a proxy.
        from .netpolicy import egress_proxy, tls_verify
        kwargs = {}
        if not _is_local_endpoint(self.base_url):
            proxy = egress_proxy()
            if proxy and self.transport is None:
                kwargs["proxy"] = proxy
            kwargs["verify"] = tls_verify()
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport,
            trust_env=False, **kwargs)

    def _headers(self) -> dict:
        # Custom connection headers go in first; the managed fields
        # then win their own names — Content-Type is always JSON,
        # and the stored API key (when there is one) owns
        # Authorization, so a stale pasted Authorization header can
        # never shadow the connection's real credential. A custom
        # Authorization survives only on keyless connections.
        headers = dict(self.headers or {})
        headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _attempt_suffix(attempts: int) -> str:
        # Exhausted retries must be visible in the surfaced error —
        # "gateway returned HTTP 503 (after 4 attempts)".
        return f" (after {attempts} attempts)" if attempts > 1 else ""

    def _may_retry(self, attempts: int, status: Optional[int]) -> bool:
        """Retry budget for the failure just seen. A 429 earns the
        long rate-limit budget (up to 8 attempts total — per-minute
        quotas need time, not luck); every other transient failure
        keeps the v21.4.2 budget (4 attempts total)."""
        if status == 429:
            return attempts <= self.rate_limit_max_retries
        return attempts <= self.max_retries

    def _retry_delay(self, attempts: int, resp: Optional[httpx.Response]) -> float:
        """Seconds to wait before the next attempt. A numeric
        Retry-After on 429/503 wins — capped at 120s for a 429 (a
        per-minute quota can legitimately ask for a full window) and
        at the 30s cap for a 503; otherwise exponential backoff —
        attempts 1,2,3 → 1s, 2s, 4s at the default base — plus
        small jitter."""
        if resp is not None and resp.status_code in (429, 503):
            raw = resp.headers.get("retry-after")
            if raw:
                try:
                    cap = (self.rate_limit_retry_after_cap_s
                           if resp.status_code == 429
                           else self.retry_after_cap_s)
                    return min(float(raw), cap)
                except ValueError:
                    pass  # HTTP-date form: keep the computed backoff
        delay = self.backoff_s * (2 ** (attempts - 1))
        return delay + random.uniform(0, 0.25 * delay)

    def _emit_rate_limited(self, info: dict) -> None:
        """Best-effort visibility hook: a broken observer must never
        break (or slow) the call it is observing."""
        if self.on_rate_limited is None:
            return
        try:
            self.on_rate_limited(info)
        except Exception:
            pass

    async def _sleep_before_retry(self, attempts: int, resp: Optional[httpx.Response]) -> None:
        delay = self._retry_delay(attempts, resp)
        if resp is not None and (resp.status_code == 429
                                  or resp.headers.get("retry-after")):
            # A wait imposed by rate limiting is mission-visible: the
            # run is not hung, it is waiting out the provider's quota.
            self._emit_rate_limited({
                "wait_s": round(delay, 3), "attempt": attempts,
                "status": resp.status_code,
                "reason": ("retry_after" if resp.headers.get("retry-after")
                           else "backoff"),
                "connection_id": self._pace_key()})
        if delay > 0:
            await asyncio.sleep(delay)

    # ------------------------------------------------ pacing (v21.4.4)
    def _pace_key(self) -> str:
        return self.connection_id or self.base_url

    def _pace_state(self) -> _PaceState:
        return _PACE_STATES.setdefault(self._pace_key(), _PaceState())

    async def _pace_before_attempt(self, attempts: int) -> None:
        """Enforce this connection's post-429 minimum gap before an
        attempt is issued. Healthy connections (gap 0) pass through
        untouched; the gap is forgotten once the connection has been
        quiet — no 429 — for pace_reset_s."""
        state = self._pace_state()
        now = _now()
        if (state.gap_s > 0 and state.last_429_at > 0
                and now - state.last_429_at > self.pace_reset_s):
            state.gap_s = 0.0
        if state.gap_s > 0 and state.last_issue_at > 0:
            wait = state.last_issue_at + state.gap_s - now
            if wait > 0:
                self._emit_rate_limited({
                    "wait_s": round(wait, 3), "attempt": attempts,
                    "status": 429, "reason": "pacing",
                    "connection_id": self._pace_key()})
                await asyncio.sleep(wait)
                now = _now()
        state.last_issue_at = now

    def _pace_note_rate_limited(self) -> None:
        """A 429 from this connection: engage (or double) its pacing
        gap. After a quiet period longer than pace_reset_s the gap
        restarts at the base instead of doubling a stale value."""
        state = self._pace_state()
        now = _now()
        if state.gap_s <= 0 or now - state.last_429_at > self.pace_reset_s:
            state.gap_s = self.pace_base_s
        else:
            state.gap_s = min(state.gap_s * 2, self.pace_cap_s)
        state.last_429_at = now

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        # Phase 4: request-time SSRF re-validation before every call.
        from .netpolicy import UrlPolicyError, assert_resolved_host_safe
        try:
            assert_resolved_host_safe(
                self.base_url,
                allow_local=_is_local_endpoint(self.base_url))
        except UrlPolicyError as exc:
            raise GatewayError("blocked", f"endpoint blocked by SSRF policy: {exc}") from exc
        url = self.base_url.rstrip("/") + path
        # Retry scope (audit): only re-issuable calls — reads and model
        # chat completions. A generation the provider rejected with a
        # transient status produced no result and no side effects to
        # duplicate; no tool or side-effecting call shares this client
        # (its only endpoints are GET /models and POST /chat/completions).
        retryable_call = method.upper() == "GET" or path == "/chat/completions"
        attempts = 0
        while True:
            attempts += 1
            # v21.4.4: a connection that recently answered 429 is
            # paced — every attempt (first tries and retries alike)
            # honors the gap before it is issued.
            await self._pace_before_attempt(attempts)
            try:
                async with self._client() as client:
                    resp = await client.request(method, url, headers=self._headers(), **kwargs)
            except httpx.TimeoutException as exc:
                if retryable_call and self._may_retry(attempts, None):
                    await self._sleep_before_retry(attempts, None)
                    continue
                raise GatewayError("timeout", f"gateway timed out: {exc.__class__.__name__}"
                                   + self._attempt_suffix(attempts)) from exc
            except httpx.HTTPError as exc:
                if retryable_call and self._may_retry(attempts, None):
                    await self._sleep_before_retry(attempts, None)
                    continue
                raise GatewayError("provider_unavailable", f"gateway unreachable: {exc}"
                                   + self._attempt_suffix(attempts)) from exc
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError:
                    return {}
            if resp.status_code == 429:
                self._pace_note_rate_limited()
            if (retryable_call and resp.status_code in RETRYABLE_STATUSES
                    and self._may_retry(attempts, resp.status_code)):
                await self._sleep_before_retry(attempts, resp)
                continue
            # Never echo provider bodies verbatim into errors shown to users;
            # bodies may contain sensitive data (§110). Keep it short + safe.
            raise GatewayError(categorize_http(resp.status_code),
                               f"gateway returned HTTP {resp.status_code}"
                               + self._attempt_suffix(attempts),
                               status=resp.status_code)

    # ------------------------------------------------------------- API
    async def health(self) -> dict:
        """Cheap liveness: model list doubles as an auth check."""
        models = await self.list_models()
        return {"ok": True, "models": len(models)}

    async def list_models(self) -> list[dict]:
        body = await self._request("GET", "/models")
        data = body.get("data") if isinstance(body, dict) else None
        if data is None and isinstance(body, dict) and "models" in body:
            data = body["models"]  # some gateways (incl. Ollama-style) differ
        return data or []

    async def list_models_ollama_native(self) -> list[dict]:
        """Ollama's native tag list, for local endpoints whose /v1/models
        may be disabled."""
        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        url = root + "/api/tags"
        from .netpolicy import UrlPolicyError, assert_resolved_host_safe
        try:
            assert_resolved_host_safe(root, allow_local=_is_local_endpoint(root))
        except UrlPolicyError as exc:
            raise GatewayError("blocked", f"endpoint blocked by SSRF policy: {exc}") from exc
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise GatewayError("timeout", "gateway timed out") from exc
        except httpx.HTTPError as exc:
            raise GatewayError("provider_unavailable", f"gateway unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise GatewayError(categorize_http(resp.status_code),
                               f"gateway returned HTTP {resp.status_code}",
                               status=resp.status_code)
        body = resp.json()
        return body.get("models", []) if isinstance(body, dict) else []

    async def chat_text(self, model: str, messages: list[dict],
                        temperature: float = 0.7) -> tuple[str, dict]:
        """One plain conversational completion (v21.4.3): the chat
        endpoint's whole job — no JSON mode, no panel, just the model's
        reply text plus token usage, through the same retrying request
        path as every other call."""
        payload = {"model": model, "messages": messages,
                   "temperature": temperature}
        body = await self._request("POST", "/chat/completions", json=payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("bad_request",
                               "gateway response had no chat content") from exc
        usage = body.get("usage") or {}
        return str(content), {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "model": body.get("model", model),
        }

    async def chat_json(self, model: str, system: str, user: str,
                        temperature: float = 0.4) -> tuple[dict, dict]:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            body = await self._request("POST", "/chat/completions", json=payload)
        except GatewayError as exc:
            if exc.status != 400:
                raise
            payload.pop("response_format")
            body = await self._request("POST", "/chat/completions", json=payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("bad_request", "gateway response had no chat content") from exc
        usage = body.get("usage") or {}
        return _extract_json(content), {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "model": body.get("model", model),
        }


def _extract_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise GatewayError("bad_request", "model did not return valid JSON")
