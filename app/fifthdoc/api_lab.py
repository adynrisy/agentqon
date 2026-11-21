"""§7 API testing lab.

Requests that change real data need explicit permission, and production
credentials are kept separate from test credentials: a saved example is
always stored with secrets redacted, and an environment declares which
credential set it uses. Sending uses only the stdlib (urllib) with a
timeout; tests exercise mocks, comparison and redaction, never a live host.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_METHODS = ("none", "bearer", "basic", "api_key_header", "api_key_query")


def redact_headers(headers: dict) -> dict:
    out = {}
    for key, value in headers.items():
        if key.lower() in ("authorization", "x-api-key", "api-key", "cookie"):
            out[key] = "[redacted]"
        else:
            out[key] = value
    return out


def redact_body(body: str) -> str:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(data, dict):
        return json.dumps({k: ("[redacted]" if k.lower() in
                               ("password", "token", "secret", "api_key", "apiKey")
                               else v) for k, v in data.items()})
    return body


@dataclass
class ApiRequest:
    method: str
    url: str
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: str = ""
    auth_method: str = "none"
    timeout_seconds: float = 30.0

    def validate(self) -> list[str]:
        problems = []
        if self.method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            problems.append(f"Unknown method: {self.method}")
        if not self.url.startswith(("http://", "https://")):
            problems.append("URL must start with http:// or https://.")
        if self.auth_method not in AUTH_METHODS:
            problems.append(f"Unknown auth method: {self.auth_method}")
        if self.timeout_seconds <= 0:
            problems.append("Timeout must be positive.")
        return problems


@dataclass
class ApiResponse:
    status: int
    headers: dict
    body: str
    elapsed_ms: float


@dataclass
class Environment:
    name: str
    variables: dict = field(default_factory=dict)
    credential_set: str = "test"  # "test" or "production" — never mixed silently


@dataclass
class Collection:
    name: str
    requests: dict[str, ApiRequest] = field(default_factory=dict)
    environment: Environment = field(
        default_factory=lambda: Environment(name="default"))
    saved_examples: list[dict] = field(default_factory=list)


def validate_json_schema(body: str, required: list[str], types: dict | None = None) -> dict:
    try:
        data = json.loads(body)
    except ValueError as exc:
        return {"ok": False, "problems": [f"Body is not valid JSON: {exc}"]}
    problems = []
    if not isinstance(data, dict):
        return {"ok": False, "problems": ["Expected a JSON object."]}
    for key in required:
        if key not in data:
            problems.append(f"Missing field: {key}")
    for key, expected in (types or {}).items():
        if key in data and type(data[key]).__name__ != expected:
            problems.append(f"Field {key} should be {expected}.")
    return {"ok": not problems, "problems": problems}


def compare_runs(a: ApiResponse, b: ApiResponse) -> dict:
    diffs = []
    if a.status != b.status:
        diffs.append(f"Status changed: {a.status} -> {b.status}.")
    if a.body != b.body:
        diffs.append("Body changed.")
    return {"same": not diffs, "diffs": diffs,
            "elapsed_delta_ms": round(b.elapsed_ms - a.elapsed_ms, 2)}


def send(request: ApiRequest, permission_granted: bool = False,
         transport=None) -> dict:
    """Send a request. Mutating methods need permission first."""
    problems = request.validate()
    if problems:
        return {"ok": False, "problems": problems}
    if request.method.upper() in MUTATING_METHODS and not permission_granted:
        return {"ok": False, "reason": "This request changes real data and "
                "needs permission before it is sent."}
    if transport is not None:
        return {"ok": True, **transport(request)}
    query = "&".join(f"{k}={v}" for k, v in request.query.items())
    url = request.url + ("?" + query if query else "")
    req = urllib.request.Request(url, data=request.body.encode() or None,
                                 headers=request.headers, method=request.method.upper())
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status,
                    "headers": dict(resp.headers), "body": body,
                    "elapsed_ms": round((time.time() - started) * 1000, 2)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code,
                "body": exc.read().decode("utf-8", "replace"),
                "reason": f"Server answered with status {exc.code}."}
    except Exception as exc:  # timeouts, DNS, connection failures
        return {"ok": False, "reason": f"Request failed: {exc}"}


def save_example(collection: Collection, request: ApiRequest, response: ApiResponse) -> dict:
    example = {"method": request.method, "url": request.url,
               "headers": redact_headers(request.headers),
               "body": redact_body(request.body),
               "response_status": response.status,
               "response_body": redact_body(response.body)}
    collection.saved_examples.append(example)
    return example


def spec_examples(spec: dict) -> list[ApiRequest]:
    """Generate example requests from a minimal OpenAPI-style spec dict."""
    out = []
    for path, methods in (spec.get("paths") or {}).items():
        for method, _ in methods.items():
            out.append(ApiRequest(method=method.upper(),
                                 url=spec.get("base_url", "") + path))
    return out


def import_collection(data: dict) -> Collection:
    coll = Collection(name=data.get("name", "imported"))
    for raw in data.get("requests", []):
        coll.requests[raw["name"]] = ApiRequest(
            method=raw.get("method", "GET"), url=raw.get("url", ""),
            headers=raw.get("headers", {}), query=raw.get("query", {}),
            body=raw.get("body", ""), auth_method=raw.get("auth_method", "none"))
    return coll


def client_codegen(request: ApiRequest, language: str = "python") -> str:
    if language == "python":
        return (f"import urllib.request\n"
                f"req = urllib.request.Request({request.url!r}, "
                f"method={request.method.upper()!r})\n"
                f"with urllib.request.urlopen(req) as resp:\n"
                f"    print(resp.read().decode())\n")
    if language == "curl":
        return f"curl -X {request.method.upper()} {request.url!r}\n"
    raise ValueError(f"Unsupported codegen language: {language}")


def mock_response(status: int, body: str, headers: dict | None = None) -> ApiResponse:
    return ApiResponse(status=status, headers=headers or {}, body=body, elapsed_ms=0.0)


def validate_webhook(payload: str, required_fields: list[str]) -> dict:
    return validate_json_schema(payload, required_fields)


def sanitized_report(collection: Collection) -> dict:
    return {"collection": collection.name,
            "environment": collection.environment.name,
            "credential_set": collection.environment.credential_set,
            "saved_examples": collection.saved_examples,
            "note": "Secrets are redacted in every saved example."}
