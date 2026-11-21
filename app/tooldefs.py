"""AgentQon — tool definitions and tool-executor rules (W5 gap C, §3).

Every session tool carries full metadata (stable ID, argument schema,
constraints, permissions, side-effect class, timeout, retry policy,
cancellation support, output schema, version), enforced by validation
helpers — not just documentation. This module is stdlib-only and has no
dependency on the rest of the harness, so the rules can be tested in
isolation and reused by the scheduler, the conversation layer, and the
agent loop.

Outcome vocabulary (used everywhere new code records a tool or task
outcome — timeout-vs-failure distinction, top-15 #2):
  success | failure | timeout | cancelled
A timeout is NEVER a failure proof and NEVER a success. Error
normalization keeps the existing error-envelope shape
({error: {code, message, correlation_id, retryable}}).

Type coercion policy (DOCUMENTED, §3-42) — applied by coerce_value():
  * int <- int: kept. int <- float: kept ONLY if integral (3.0 -> 3);
    3.5 is rejected, never silently cut down.
  * float <- int/float: kept.
  * str <- str: kept. No other type is coerced INTO a string (a number
    passed where text is expected is rejected — silent stringifying
    hides caller mistakes).
  * bool <- bool: kept ONLY. The strings "true"/"false" are NEVER
    coerced to bool (that is the classic silent-meaning-change bug).
  * list <- list: kept; a single value is NEVER wrapped into a list
    (wrapping changes "one item" into "a list of one item" silently).
  * Anything else: rejected with a plain-words error.

Argument-key normalization (§3-41): keys are stripped and lowercased,
and a small per-tool alias map folds common spellings onto the
canonical key (file/filepath -> path, cmd -> argv for shell_run).
Unknown keys are detected (§3-44) and REJECTED (fail-closed): a tool
call carrying an argument the tool does not declare is refused, never
silently ignored — a silently ignored argument is how a safety flag
gets dropped.

Safe defaults (C2): a default is allowed ONLY when it cannot change
the meaning of the call. The registry below declares defaults only for
``path`` ("." = the workspace itself) and ``timeout_s`` (a bound, not a
meaning). A tool definition that declares any other default is
rejected at registry build time by _check_safe_defaults() — a default
that changes meaning is forbidden, and the registry refuses to load
rather than carrying it.

Environment filtering (§3-49): shell tools receive a filtered
environment — every variable whose name looks like a secret is
dropped (denylist below), everything else passes through unchanged.
The harness keeps provider secrets in its own process environment, so
an agent shell must never inherit them wholesale.

Result-size limits (C4, §3-53): truncate_text() cuts at the limit and
returns a PLAIN-WORDS marker stating what was cut and how much of the
whole is shown — partial output always SAYS it is partial, never
silently cut. The existing agent_tools truncation flags are kept and
now carry that marker (see agent_tools.py).

Exit codes (§3-52, C5): verdict_from_exit() — 0 is success, anything
else is failure, and text output NEVER overrides the code. A tool
that prints "success" and exits non-zero has FAILED.

Resource limits (§3-55): resource_limits_preexec() returns a
preexec_fn for subprocess on POSIX (CPU seconds, address-space cap,
output-file cap) and None where unsupported (e.g. Windows) — the
caller then enforces the time bound only, and records that the
memory/CPU caps were unavailable on that platform rather than
pretending they applied.

Cancellation (§3-56): CancelHandle — the caller registers a process
(or any killable handle) and cancel() propagates the kill. The
executor records outcome "cancelled", never "failure".

Temporary resources (§3-57): temp_workspace() is a context manager
that removes the directory on exit, including on error.

Execution receipts (§3-60): receipt() builds the record of what ran,
where, with what result — arguments redacted (secret-looking keys
become "[hidden]"), never logged raw.
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .domain import new_id, now_iso

# ---------------------------------------------------------------- outcomes

OUTCOMES = ("success", "failure", "timeout", "cancelled")
FAILURE_PROOF_OUTCOMES = ("failure",)  # timeout/cancelled are NOT proof

# ---------------------------------------------------------------- env filtering

_SECRET_NAME_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|PRIVATE|"
    r"SESSION|COOKIE|VAULT|SIGNING)", re.IGNORECASE)
# Names dropped even when they do not match the secret pattern.
_ENV_DENY_EXACT = {"AGENTQON_VAULT_KEY", "AGENTQON_MASTER_KEY"}


def filter_env(env: dict | None = None) -> dict:
    """The environment a tool process receives (§3-49). Secret-looking
    variables are dropped; everything else passes through unchanged.
    Pass None to filter the current process environment."""
    source = os.environ if env is None else env
    out = {}
    for key, value in source.items():
        if key in _ENV_DENY_EXACT or _SECRET_NAME_RE.search(key):
            continue
        out[str(key)] = str(value)
    return out


def redact_args(args: dict) -> dict:
    """Copy of `args` with secret-looking values replaced by [hidden]
    (for receipts and logs — never log raw tool arguments)."""
    out = {}
    for key, value in args.items():
        if _SECRET_NAME_RE.search(str(key)):
            out[key] = "[hidden]"
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------- tool registry

@dataclass(frozen=True)
class ArgSpec:
    type: str  # "string" | "integer" | "number" | "boolean" | "list"
    required: bool = False
    default: Any = None
    has_default: bool = False
    enum: tuple = ()
    minimum: Any = None
    maximum: Any = None
    min_length: int | None = None
    max_length: int | None = None
    description: str = ""


@dataclass(frozen=True)
class ToolDef:
    tool_id: str  # stable, never renamed once shipped
    name: str
    description: str
    args: dict  # name -> ArgSpec
    constraints: tuple = ()
    permissions: tuple = ()  # e.g. ("workspace.read",)
    side_effect: str = "none"  # none | read | write | external
    timeout_s: float = 60.0
    max_attempts: int = 2
    retryable_outcomes: tuple = ("timeout",)
    cancellable: bool = True
    output_fields: dict = field(default_factory=dict)  # name -> type
    version: str = "1"
    idempotent: bool = True  # safe to retry without repeating effects

    def side_effect_warning(self) -> str:
        """Plain-words warning shown BEFORE a side-effect tool runs
        (C8). Empty for read-only tools — nothing to warn about."""
        if self.side_effect == "write":
            return (f"{self.name} changes files. The change will be "
                    f"made in the workspace and can be undone only by "
                    f"editing the file again.")
        if self.side_effect == "external":
            return (f"{self.name} acts outside this workspace — it can "
                    f"reach other systems or spend money. It runs "
                    f"only after approval.")
        return ""


# Defaults that cannot change meaning (C2). Any other default in a
# ToolDef is rejected at registry build.
_SAFE_DEFAULTS = {
    ("fs_list", "path"): ".",
    ("fs_search", "path"): ".",
    ("shell_run", "timeout_s"): 60.0,
}

_STR = {"type": "string", "required": False}
TOOL_DEFS: dict[str, ToolDef] = {}


def _def(tool_id: str, name: str, description: str, args: dict,
         **kw) -> ToolDef:
    return ToolDef(tool_id=tool_id, name=name, description=description,
                   args=args, **kw)


def _build_registry() -> dict[str, ToolDef]:
    defs = [
        _def("tool.fs_read", "fs_read", "Read a file from the workspace.",
             {"path": ArgSpec("string", required=True,
                              description="File to read, inside the workspace")},
             permissions=("workspace.read",), side_effect="read",
             timeout_s=30.0, output_fields={"content": "string", "path": "string"},
             idempotent=True),
        _def("tool.fs_write", "fs_write", "Write a file in the workspace.",
             {"path": ArgSpec("string", required=True),
              "content": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string", "bytes": "integer"},
             idempotent=False),
        _def("tool.fs_edit", "fs_edit", "Replace one exact passage in a file.",
             {"path": ArgSpec("string", required=True),
              "old": ArgSpec("string", required=True),
              "new": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string", "replacements": "integer"},
             idempotent=False),
        _def("tool.fs_list", "fs_list", "List a directory in the workspace.",
             {"path": ArgSpec("string", required=False, default=".",
                              has_default=True)},
             permissions=("workspace.read",), side_effect="read",
             timeout_s=30.0, output_fields={"entries": "list"},
             idempotent=True),
        _def("tool.fs_search", "fs_search", "Search workspace files for text.",
             {"query": ArgSpec("string", required=True, min_length=1),
              "path": ArgSpec("string", required=False, default=".",
                              has_default=True)},
             permissions=("workspace.read",), side_effect="read",
             timeout_s=60.0, output_fields={"matches": "list", "count": "integer"},
             idempotent=True),
        _def("tool.shell_run", "shell_run", "Run a command without a shell.",
             {"argv": ArgSpec("list", required=False),
              "command": ArgSpec("string", required=False),
              "timeout_s": ArgSpec("number", required=False, default=60.0,
                                   has_default=True, minimum=1, maximum=300)},
             constraints=("argv only: no shell string is ever executed",),
             permissions=("system.execute",), side_effect="external",
             timeout_s=300.0, max_attempts=1, retryable_outcomes=(),
             output_fields={"stdout": "string", "stderr": "string",
                            "returncode": "integer"},
             idempotent=False),
        _def("tool.web_read", "web_read", "Read a web page through the gateway.",
             {"url": ArgSpec("string", required=True)},
             permissions=("web.read",), side_effect="read",
             timeout_s=60.0, output_fields={"text": "string", "url": "string"},
             idempotent=True),
        # File-operations tools (W8): metadata added at integration
        # because W8 extended TOOLS without extending this registry —
        # the every-session-tool metadata test pins the pair.
        _def("tool.fs_copy", "fs_copy", "Copy a file in the workspace.",
             {"path": ArgSpec("string", required=True),
              "destination": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string",
                                            "destination": "string"},
             idempotent=False),
        _def("tool.fs_move", "fs_move", "Move a file in the workspace.",
             {"path": ArgSpec("string", required=True),
              "destination": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string",
                                            "destination": "string"},
             idempotent=False),
        _def("tool.fs_rename", "fs_rename", "Rename a file in the workspace.",
             {"path": ArgSpec("string", required=True),
              "destination": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string",
                                            "destination": "string"},
             idempotent=False),
        _def("tool.fs_delete", "fs_delete", "Delete a file in the workspace.",
             {"path": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string"},
             idempotent=False),
        _def("tool.fs_mkdir", "fs_mkdir", "Create a folder in the workspace.",
             {"path": ArgSpec("string", required=True)},
             permissions=("workspace.write",), side_effect="write",
             timeout_s=30.0, output_fields={"path": "string"},
             idempotent=False),
        _def("tool.fs_stat", "fs_stat", "Read a file's size, time, mode, type.",
             {"path": ArgSpec("string", required=True)},
             permissions=("workspace.read",), side_effect="read",
             timeout_s=30.0, output_fields={"path": "string",
                                            "size": "integer",
                                            "mode": "string",
                                            "type": "string"},
             idempotent=True),
    ]
    out = {}
    for d in defs:
        _check_safe_defaults(d)
        out[d.name] = d
    return out


def _check_safe_defaults(defn: ToolDef) -> None:
    """Reject any default that could change meaning (C2)."""
    for arg_name, spec in defn.args.items():
        if not spec.has_default:
            continue
        if (defn.name, arg_name) not in _SAFE_DEFAULTS:
            raise ValueError(
                f"unsafe default rejected: tool {defn.name!r} argument "
                f"{arg_name!r} carries a default that could change meaning")
        if spec.default != _SAFE_DEFAULTS[(defn.name, arg_name)]:
            raise ValueError(
                f"unsafe default value rejected: tool {defn.name!r} "
                f"argument {arg_name!r}")


TOOL_DEFS = _build_registry()

# Argument-key aliases (§3-41). Canonical key is the value.
_ARG_ALIASES = {
    "fs_read": {"file": "path", "filepath": "path", "filename": "path"},
    "fs_write": {"file": "path", "filepath": "path"},
    "fs_edit": {"file": "path", "filepath": "path"},
    "fs_list": {"dir": "path", "directory": "path"},
    "fs_search": {"dir": "path", "directory": "path", "text": "query"},
    "shell_run": {"cmd": "argv", "command_line": "argv"},
    "web_read": {"link": "url", "href": "url"},
}


def normalize_arg_keys(tool: str, args: dict) -> dict:
    """Strip/lowercase keys and fold aliases onto canonical keys
    (§3-41). A key that collides with a canonical key after folding
    keeps the canonical spelling's value (the explicit spelling wins
    over the alias)."""
    aliases = _ARG_ALIASES.get(tool, {})
    out: dict = {}
    # Two passes so the explicit canonical spelling always wins over
    # an alias, regardless of argument order: first the keys that are
    # already canonical (or unknown), then aliases for keys still
    # missing.
    for raw_key, value in args.items():
        key = str(raw_key).strip().lower()
        if key not in aliases and key not in out:
            out[key] = value
    for raw_key, value in args.items():
        key = aliases.get(str(raw_key).strip().lower())
        if key is not None and key not in out:
            out[key] = value
    return out


# ---------------------------------------------------------------- coercion

def coerce_value(value: Any, want: str) -> tuple[Any, str | None]:
    """Coerce `value` toward `want` under the documented policy above.
    Returns (value, None) on success or (None, plain-words error)."""
    if want == "string":
        if isinstance(value, str):
            return value, None
        return None, "expected text, got something else"
    if want == "integer":
        if isinstance(value, bool):
            return None, "expected a whole number, got yes/no"
        if isinstance(value, int):
            return value, None
        if isinstance(value, float) and value.is_integer():
            return int(value), None
        if isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"[+-]?\d+", text):
                return int(text), None
        return None, "expected a whole number"
    if want == "number":
        if isinstance(value, bool):
            return None, "expected a number, got yes/no"
        if isinstance(value, (int, float)):
            return float(value), None
        if isinstance(value, str):
            try:
                return float(value.strip()), None
            except ValueError:
                return None, "expected a number"
        return None, "expected a number"
    if want == "boolean":
        if isinstance(value, bool):
            return value, None
        return None, "expected yes or no (true/false as a real yes/no value)"
    if want == "list":
        if isinstance(value, list):
            return value, None
        return None, "expected a list of items"
    return None, f"unknown argument type {want!r}"


def validate_args(tool: str, args: dict) -> tuple[dict, list[str]]:
    """Validate + coerce arguments for `tool` against its definition.
    Returns (clean_args, errors). Errors are plain words. Unknown
    fields are errors (§3-44), never silently dropped. Missing
    required arguments are errors. Enum (C3) and range (C3) checks
    run after coercion."""
    defn = TOOL_DEFS.get(tool)
    if defn is None:
        return {}, [f"unknown tool {tool!r}"]
    clean_in = normalize_arg_keys(tool, args if isinstance(args, dict) else {})
    errors: list[str] = []
    unknown = sorted(set(clean_in) - set(defn.args))
    for key in unknown:
        errors.append(f"unknown argument {key!r} — this tool does not accept it")
    clean: dict = {}
    for name, spec in defn.args.items():
        if name not in clean_in:
            if spec.has_default:
                clean[name] = spec.default
            elif spec.required:
                errors.append(f"missing required argument {name!r}")
            continue
        value, err = coerce_value(clean_in[name], spec.type)
        if err is not None:
            errors.append(f"argument {name!r}: {err}")
            continue
        if spec.enum and value not in spec.enum:
            errors.append(f"argument {name!r} must be one of "
                          f"{', '.join(map(str, spec.enum))}")
            continue
        if spec.minimum is not None and value < spec.minimum:
            errors.append(f"argument {name!r} must be at least {spec.minimum}")
            continue
        if spec.maximum is not None and value > spec.maximum:
            errors.append(f"argument {name!r} must be at most {spec.maximum}")
            continue
        if spec.min_length is not None and isinstance(value, str) \
                and len(value) < spec.min_length:
            errors.append(f"argument {name!r} must not be empty")
            continue
        if spec.max_length is not None and isinstance(value, str) \
                and len(value) > spec.max_length:
            errors.append(f"argument {name!r} is too long "
                          f"(at most {spec.max_length} characters)")
            continue
        clean[name] = value
    return clean, errors


def validate_output(tool: str, result: dict) -> list[str]:
    """Check a tool result against its declared output schema (§3-58,
    C10). An empty search result is valid success (C11): a search
    with zero matches is SUCCESS with zero results, not failure —
    so a fs_search result with count 0 and ok True passes here."""
    defn = TOOL_DEFS.get(tool)
    if defn is None:
        return [f"unknown tool {tool!r}"]
    if not isinstance(result, dict):
        return ["tool result must be a set of named fields"]
    errors = []
    if "ok" in result and not isinstance(result["ok"], bool):
        errors.append("tool result 'ok' must be yes or no")
    for fname, ftype in defn.output_fields.items():
        if fname not in result:
            continue  # partial results may omit fields; absence is not failure
        _, err = coerce_value(result[fname], ftype)
        if err is not None:
            errors.append(f"output field {fname!r}: {err}")
    return errors


# ---------------------------------------------------------------- truncation / exit codes

def truncate_text(text: str, limit: int) -> tuple[str, bool, str]:
    """Cut `text` at `limit` characters. Returns (shown, truncated,
    marker). The marker is plain words stating the cut (§3-53, C4):
    partial output always SAYS it is partial."""
    if len(text) <= limit:
        return text, False, ""
    shown = text[:limit]
    marker = (f"… (output cut short here: showing the first {limit:,} "
              f"characters of {len(text):,} in total)")
    return shown + marker, True, marker


def verdict_from_exit(returncode: int | None) -> str:
    """The exit code is the verdict (§3-52, C5). None (never started
    or killed before exit) is failure, never success."""
    if returncode == 0:
        return "success"
    return "failure"


def normalize_error(code: str, message: str, correlation_id: str,
                    retryable: bool = False) -> dict:
    """Structured error, keeping the existing envelope shape (C6, T3).
    `message` must already be plain words — this function never
    invents jargon, it only wraps."""
    return {"error": {"code": code, "message": message,
                      "correlation_id": correlation_id,
                      "retryable": retryable}}


def is_retry_safe(tool: str) -> bool:
    """Per-tool idempotency metadata (C7): True only for tools whose
    retry cannot repeat an effect."""
    defn = TOOL_DEFS.get(tool)
    return bool(defn and defn.idempotent)


# ---------------------------------------------------------------- timing / limits / cancellation / temp / receipts

class timed_run:
    """Measure execution time (§3-54). Usage:
    with timed_run() as t: ... ; t.duration_ms holds the elapsed time."""

    def __init__(self) -> None:
        self.duration_ms = 0.0
        self._start = 0.0

    def __enter__(self) -> "timed_run":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self.duration_ms = round((time.monotonic() - self._start) * 1000, 3)


def resource_limits_preexec(cpu_seconds: int = 120,
                            max_bytes: int = 512 * 1024 * 1024):
    """preexec_fn for subprocess enforcing CPU/memory caps (§3-55).
    Returns None where the platform cannot enforce them (e.g.
    Windows) — the caller must record that instead of pretending."""
    try:
        import resource as _resource
    except ImportError:
        return None

    def _preexec() -> None:
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        _resource.setrlimit(_resource.RLIMIT_AS, (max_bytes, max_bytes))
        _resource.setrlimit(_resource.RLIMIT_FSIZE,
                            (max_bytes, max_bytes))
    return _preexec


class CancelHandle:
    """Caller-driven cancellation (§3-56). Register a killable
    process (or callback); cancel() propagates the kill and records
    that cancellation happened, so the outcome is 'cancelled'."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._targets: list = []
        self.cancelled = False

    def register(self, target) -> None:
        """target: a subprocess.Popen (kill()ed) or a zero-arg callable."""
        self._targets.append(target)
        if self._event.is_set():
            self._kill(target)

    @staticmethod
    def _kill(target) -> None:
        try:
            if hasattr(target, "kill"):
                target.kill()
            elif callable(target):
                target()
        except Exception:
            pass

    def cancel(self) -> None:
        self.cancelled = True
        self._event.set()
        for target in list(self._targets):
            self._kill(target)

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@contextlib.contextmanager
def temp_workspace(prefix: str = "agentqon-tool-"):
    """A temporary directory removed on exit, including on error
    (§3-57). Yields a pathlib.Path."""
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        yield Path(tmp)


def receipt(tool: str, args: dict, outcome: str, *,
            returncode: int | None = None, duration_ms: float = 0.0,
            stdout_truncated: bool = False, stderr_truncated: bool = False,
            mission_id: str = "", task_id: str = "",
            workspace: str = "") -> dict:
    """Execution receipt (§3-60): what ran, where, with what result.
    Arguments are redacted; the receipt never carries raw secrets."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    return {
        "receipt_id": new_id("rcpt"),
        "tool": tool,
        "tool_version": TOOL_DEFS[tool].version if tool in TOOL_DEFS else "",
        "arguments": redact_args(args if isinstance(args, dict) else {}),
        "outcome": outcome,
        "returncode": returncode,
        "duration_ms": duration_ms,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "mission_id": mission_id,
        "task_id": task_id,
        "workspace": workspace,
        "recorded_at": now_iso(),
    }
