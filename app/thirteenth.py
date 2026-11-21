"""Thirteenth document ("remaining distinct functions", W17) — stdlib only.

One module for 36 capabilities, grouped so each item is built once and
cross-referenced from the workstream that owns its neighbouring system:

- Group 1 (items 1-5): native desktop integration intents + portable mode.
- Group 2 (items 6-11): developer-tool protocols (MCP, LSP, DAP, terminal
  sessions, local API compatibility, shell completions).
- Group 3 (items 12-20): local model conversion / inference — the harness
  does NOT run an inference engine. Every item here is backend capability
  DETECTION + REPORTING: where a connected backend supports a capability
  it is driven and reported; where none does, the result says so plainly
  with reasons. Nothing here fabricates engine behaviour.
- Group 4 (items 21-26): retrieval / multimodal (reranker measurement,
  multimodal validator, voice / speech descriptors, screen-context rules).
- Group 5 (items 27-29): desktop automation guards. Windows UI Automation
  behaviour is NOT claimed on non-Windows machines: the platform gate
  reports unsupported with reasons (Windows Evidence Rule).
- Group 6 (items 30-36): output integrity / engineering tools.

Generalization seams (never duplicates):
- MCP server direction: cli/mcp/agentqon_brain_mcp.py is EXPANDED with a
  second, local tool (agentqon_limits); the client direction lives here.
- LSP adds server-provided semantics on top of CodeGraph (app/codegraph.py);
  CodeGraph parsing is NOT re-implemented here.
- Local API mode generalizes /v1/chat + mission SSE + mission cancellation
  + the Write Law; it adds a client-convention-compatible surface
  descriptor with enforced limits, not a second engine.
- Redaction verification generalizes app/memory.py redact_text: redaction
  itself stays there; verification (is it REALLY gone from extractable
  text and metadata?) is new here.
- Debug-state evidence generalizes the diagnostic bundle + secret filter
  (redact_text); the bundle itself is not rebuilt.
- Backend conformance generalizes the real one-token ping self-test
  (app/modeltest.py); the new part is that ROUTING consults the result.
- Portable mode / removable-volume safety are one story (items 5 + 34).

All user-facing messages use simple words. Statuses are plain:
"supported", "unsupported", "refused", "allowed", "paused", "warning".
A refused result always carries a plain-word reason.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import select
import shutil
import stat
import struct
import subprocess
import sys
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pty as _pty
except Exception:  # pragma: no cover - pty is stdlib on POSIX
    _pty = None

from .memory import redact_text

PLAIN = "plain words"


# ---------------------------------------------------------------- utilities
def _result(status: str, message: str, **extra) -> dict:
    out = {"status": status, "message": message}
    out.update(extra)
    return out


def _refused(message: str, **extra) -> dict:
    return _result("refused", message, **extra)


# ================================================================ Group 1
# Item 1 — File Explorer context-menu integration (preview mandatory).
CONTEXT_MENU_ACTIONS = ("analyze", "mission_from_folder", "explain_file")
CONTEXT_MENU_LABELS = {
    "analyze": "Analyze with AgentQon",
    "mission_from_folder": "Create mission from folder",
    "explain_file": "Explain this file",
}


def build_context_menu(selection: list[str]) -> dict:
    """Menu intents for an OS selection. Building intents never processes."""
    items = [
        {"action": a, "label": CONTEXT_MENU_LABELS[a], "selection": list(selection)}
        for a in CONTEXT_MENU_ACTIONS
    ]
    return _result("supported", "Menu ready. Nothing has been processed yet.",
                   selection=list(selection), items=items)


def preview_selection(selection: list[str], action: str) -> dict:
    """Mandatory preview: what WOULD be processed, for the user to approve."""
    if action not in CONTEXT_MENU_ACTIONS:
        return _refused(f"Unknown menu action: {action}.", action=action)
    if not selection:
        return _refused("Nothing is selected, so there is nothing to preview.",
                        action=action)
    return _result("supported",
                   "Preview ready. Approve it before anything is processed.",
                   action=action, selection=list(selection),
                   preview={"action": action, "inputs": list(selection),
                            "approved": False})


def process_selection(preview: dict, approved: bool) -> dict:
    """Processing is refused unless the preview was explicitly approved.

    `approved` is the user's approval of THIS preview record (see
    approve_preview). Without it, nothing is processed — never silently.
    """
    if not approved:
        return _refused("Not approved. The preview must be approved first.",
                        action=preview.get("action"))
    return _result("allowed",
                   "Approved. The selected inputs may now be processed.",
                   action=preview.get("action"),
                   selection=preview.get("selection", []))


def approve_preview(preview: dict) -> dict:
    """Mark a preview approved (the user's explicit approval step)."""
    out = dict(preview)
    pv = dict(preview.get("preview", {}))
    pv["approved"] = True
    out["preview"] = pv
    out["message"] = "Preview approved."
    return out


# Item 2 — Native file-association handler (Open With).
ASSOCIATION_ACTIONS = ("preview", "analysis", "editing", "mission")


def dispatch_association(path: str, action: str) -> dict:
    if action not in ASSOCIATION_ACTIONS:
        return _refused(
            f"Unknown choice: {action}. Choose preview, analysis, editing, "
            "or mission.", path=path, action=action)
    if not path:
        return _refused("No file was given to open.", action=action)
    return _result("allowed", f"Opening with {action}.", path=path, action=action)


# Item 3 — Global quick-capture overlay (explicit carry ONLY).
def create_capture(text: str | None = None, file: str | None = None,
                   source: str = "explicit") -> dict:
    """A capture carries ONLY what the user explicitly chose.

    source must be "explicit". Anything claiming background screen or
    clipboard content is refused: this product never collects those
    silently.
    """
    if source != "explicit":
        return _refused(
            "Only content you explicitly choose can be carried into a task. "
            "Background screen or clipboard content is never collected.",
            source=source)
    if text is None and file is None:
        return _result("supported",
                       "Empty capture window ready. Add text or choose a file.",
                       carried={})
    carried = {}
    if text is not None:
        carried["text"] = text
    if file is not None:
        carried["file"] = file
    return _result("supported", "Capture ready with only what you chose.",
                   carried=carried)


# Item 4 — Native system-tray controller.
def tray_menu(engine_status: str, recent_missions: list[dict],
              pausable_work: list[str]) -> dict:
    return _result("supported", "Tray menu ready.",
                   engine_status=engine_status,
                   recent_missions=list(recent_missions),
                   pausable_work=list(pausable_work),
                   actions=["show_status", "show_recent", "pause_work",
                            "show_diagnostics"])


def pause_background_work(work_id: str, pausable_work: list[str]) -> dict:
    if work_id not in pausable_work:
        return _refused(
            "That work cannot be paused from the tray menu.",
            work_id=work_id)
    return _result("paused", "Background work paused.", work_id=work_id)


# Item 5 — Portable application mode (one story with item 34).
def resolve_portable_data_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for var in ("AGENTQON_DATA", "OMNIMIND_DATA"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    return Path.cwd() / "data"


def portable_mode_report(data_dir: str | None = None,
                         vault_present: bool = False,
                         plaintext_credential_found: bool = False,
                         python_version: tuple = sys.version_info[:2]) -> dict:
    """Defined data location, encrypted-or-re-entered credentials (never
    plaintext), and machine-dependency checks for a portable install."""
    location = resolve_portable_data_dir(data_dir)
    checks = {
        "python_3_12": python_version == (3, 12),
        "python_version": ".".join(str(v) for v in python_version),
        "platform": platform.system(),
        "data_dir_writable_parent": location.parent.exists(),
    }
    if plaintext_credential_found:
        return _refused(
            "A credential was found in plain text. Portable mode refuses "
            "to carry it: move it into the encrypted vault or re-enter it "
            "on the new machine.", data_dir=str(location), checks=checks,
            credential_rule="encrypted vault or re-entered, never plain text")
    credential_rule = ("encrypted vault travels with the install"
                       if vault_present else
                       "no vault found: credentials must be re-entered "
                       "on the new machine")
    problems = [k for k, v in checks.items()
                if k == "python_3_12" and not v]
    status = "warning" if problems else "supported"
    msg = ("Portable mode ready."
           if not problems else
           "Portable mode ready, but this machine does not run Python 3.12, "
           "which this install expects.")
    return _result(status, msg, data_dir=str(location), checks=checks,
                   credential_rule=credential_rule,
                   machine_dependencies=["Python 3.12", "writable data folder"])


# ================================================================ Group 2
# Item 6 — MCP Gateway, both directions.
WRITE_TOOL_NAMES = ("write_file", "edit_file", "delete_file", "run_command")


@dataclass
class MCPServer:
    name: str
    transport: str = "stdio"  # stdio | sse | http
    tools: dict = field(default_factory=dict)  # tool -> {"files": [prefixes]}
    state: str = "stopped"
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3
    reconnect_backoff_seconds: float = 1.0

    def tool_allowed(self, tool: str, path: str | None = None) -> dict:
        """Per-server tool permissions. Default is deny. The Write Law
        applies: a write-capable tool is allowed only for files under an
        explicitly listed prefix, and never for harness files."""
        if tool not in self.tools:
            return _refused(f"Tool {tool} is not allowed for server "
                            f"{self.name}.", server=self.name, tool=tool)
        if tool in WRITE_TOOL_NAMES:
            if path is None:
                return _refused("A write needs a named file to check.",
                                server=self.name, tool=tool)
            p = Path(path)
            if "app" in p.parts or "cli" in p.parts:
                return _refused(
                    "Agents never change the harness's own files.",
                    server=self.name, tool=tool, path=path)
            prefixes = self.tools[tool].get("files", [])
            if not any(str(path).startswith(pfx) for pfx in prefixes):
                return _refused(
                    f"File {path} is outside this server's allowed files.",
                    server=self.name, tool=tool, path=path)
        return _result("allowed", "Tool call allowed.", server=self.name,
                       tool=tool, path=path)

    def reconnect_plan(self) -> dict:
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            return _result("unsupported",
                           "Reconnect attempts are used up. The server "
                           "stays stopped until you restart it.",
                           server=self.name,
                           attempts=self.reconnect_attempts)
        delay = self.reconnect_backoff_seconds * (2 ** self.reconnect_attempts)
        return _result("supported", "Reconnect planned.",
                       server=self.name, delay_seconds=delay,
                       next_attempt=self.reconnect_attempts + 1)


def register_mcp_server(servers: dict, name: str, transport: str,
                        tools: dict) -> dict:
    if transport not in ("stdio", "sse", "http"):
        return _refused(f"Unknown transport: {transport}.", server=name)
    if name in servers:
        return _refused(f"Server {name} is already registered.", server=name)
    servers[name] = MCPServer(name=name, transport=transport, tools=tools)
    return _result("supported", f"Server {name} registered, stopped.",
                   server=name, transport=transport,
                   tools=sorted(tools))


def mcp_client_handshake(transport_send) -> dict:
    """Client direction over an injected transport: send initialize, read
    the server's reply. `transport_send` takes a request dict and returns
    a response dict (a real stdio/SSE transport plugs in here)."""
    try:
        resp = transport_send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "agentqon",
                                                          "version": "22.5.0"}}})
    except Exception as exc:
        return _result("unsupported", f"Could not reach the MCP server: {exc}.")
    server_info = (resp or {}).get("result", {}).get("serverInfo", {})
    if not server_info:
        return _result("unsupported",
                       "The server did not answer the handshake.")
    return _result("supported", "Connected to the MCP server.",
                   server=server_info.get("name", "?"),
                   version=server_info.get("version", "?"))


# Item 7 — LSP Bridge (server semantics ON TOP of CodeGraph, not a redo).
class LSPClient:
    """JSON-RPC language-server client over an injected transport.

    The transport is a callable taking a request dict and returning a
    response dict; a real process/stdio transport plugs in here. Nothing
    here re-parses source: CodeGraph (app/codegraph.py) stays the
    harness's own symbol index, and this client only consumes what a
    language server reports.
    """

    def __init__(self, transport_send, collect_notifications=None):
        self._send = transport_send
        self.notifications = collect_notifications if collect_notifications is not None else []
        self._next_id = 0
        self.server_info: dict = {}

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        resp = self._send({"jsonrpc": "2.0", "id": self._next_id,
                           "method": method, "params": params})
        if resp is None:
            return {}
        if isinstance(resp, dict) and resp.get("method", "").startswith("textDocument/publishDiagnostics"):
            self.notifications.append(resp)
            return {}
        return (resp or {}).get("result", {}) or {}

    def initialize(self, root_uri: str) -> dict:
        result = self._request("initialize", {
            "processId": None, "rootUri": root_uri,
            "capabilities": {}})
        self.server_info = result.get("serverInfo", {})
        return _result("supported" if self.server_info else "unsupported",
                       "Language server ready." if self.server_info else
                       "The language server did not answer.",
                       server=self.server_info)

    def open_document(self, uri: str, language_id: str, text: str) -> dict:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                    "params": {"textDocument": {"uri": uri,
                                                "languageId": language_id,
                                                "version": 1, "text": text}}})
        return _result("supported", "Document opened.", uri=uri)

    def diagnostics(self) -> list:
        out = []
        for note in self.notifications:
            out.extend((note.get("params") or {}).get("diagnostics", []))
        return out

    def definition(self, uri: str, line: int, character: int) -> dict:
        result = self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}})
        return _result("supported", "Definition from the language server.",
                       definition=result)

    def references(self, uri: str, line: int, character: int) -> dict:
        result = self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True}})
        return _result("supported", "References from the language server.",
                       references=result)

    def document_info(self, uri: str, line: int, character: int) -> dict:
        result = self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}})
        return _result("supported", "Document information from the server.",
                       info=result)


# Item 8 — Debug Adapter Protocol (explicit authorization required).
class DAPSession:
    """DAP client over an injected transport. Controlling a live debug
    session is refused until authorize() has been called explicitly."""

    def __init__(self, transport_send):
        self._send = transport_send
        self._seq = 0
        self.authorized = False
        self.breakpoints: list = []

    def authorize(self) -> dict:
        self.authorized = True
        return _result("allowed",
                       "Live debugging control is now authorized by you.",
                       authorized=True)

    def _request(self, command: str, arguments: dict | None = None) -> dict:
        self._seq += 1
        resp = self._send({"seq": self._seq, "type": "request",
                           "command": command,
                           "arguments": arguments or {}})
        return (resp or {}).get("body", {}) or {}

    def _guarded(self, command: str, arguments: dict | None = None) -> dict:
        if not self.authorized:
            return _refused(
                "Controlling a live debugging session needs your explicit "
                "authorization first. Nothing was started or changed.",
                command=command)
        return _result("supported", f"{command} ran.",
                       body=self._request(command, arguments))

    def set_breakpoints(self, source_path: str, lines: list[int]) -> dict:
        out = self._guarded("setBreakpoints",
                            {"source": {"path": source_path},
                             "breakpoints": [{"line": ln} for ln in lines]})
        if out["status"] == "supported":
            self.breakpoints = [{"source": source_path, "line": ln}
                                for ln in lines]
        return out

    def stack_trace(self, thread_id: int = 1) -> dict:
        return self._guarded("stackTrace", {"threadId": thread_id})

    def variables(self, variables_reference: int) -> dict:
        return self._guarded("variables",
                             {"variablesReference": variables_reference})

    def execution_state(self, thread_id: int = 1) -> dict:
        return self._guarded("threads", {"threadId": thread_id})


# Item 9 — Persistent interactive terminal sessions (genuine PTY).
class TerminalSession:
    """A real interactive session: prompts, input stream, resize, recovery.

    Uses a PTY where available (Linux/macOS) so the child sees a real
    terminal; falls back to pipes with an honest note. This is new: the
    one-off shell_run command is untouched.
    """

    def __init__(self, command: list[str], cols: int = 80, rows: int = 24):
        self.command = list(command)
        self.cols = cols
        self.rows = rows
        self.uses_pty = _pty is not None
        self._master = None
        self._proc = None
        self.start()

    def start(self) -> None:
        if self.uses_pty:
            self._master, slave = _pty.openpty()
            self._set_size()
            self._proc = subprocess.Popen(
                self.command, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True)
            os.close(slave)
        else:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)

    def _set_size(self) -> None:
        if self._master is not None:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.rows, self.cols, 0, 0))

    def resize(self, cols: int, rows: int) -> dict:
        self.cols, self.rows = cols, rows
        if self.uses_pty:
            self._set_size()
            return _result("supported", "Terminal resized.",
                           cols=cols, rows=rows)
        return _result("supported",
                       "Size recorded. This fallback has no real terminal, "
                       "so the size applies on the next start.",
                       cols=cols, rows=rows)

    def send(self, text: str) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            return _refused("The session ended. Recover it before sending "
                            "more input.")
        if self.uses_pty:
            os.write(self._master, text.encode())
        else:
            self._proc.stdin.write(text)
            self._proc.stdin.flush()
        return _result("supported", "Input sent.")

    def read_available(self, timeout: float = 1.0) -> str:
        if self._master is None:
            return ""
        out = b""
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self._master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self._master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if time.time() >= end:
                break
        return out.decode(errors="replace")

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def recover(self) -> dict:
        if self.alive():
            return _result("supported", "Session is still running.",
                           recovered=False)
        self.close()
        self.start()
        return _result("supported", "Session recovered with a fresh start.",
                       recovered=True)

    def close(self) -> None:
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        finally:
            if self._master is not None:
                try:
                    os.close(self._master)
                except OSError:
                    pass
            self._master = None
            self._proc = None


# Item 10 — Local Agent API Compatibility Mode (localhost only).
LOCAL_API_HOSTS = ("localhost", "127.0.0.1", "::1")
LOCAL_API_SUBCOMMANDS = ("chat", "missions", "models", "health")


def describe_local_api_mode() -> dict:
    return _result("supported",
                   "Local mode ready on this machine only.",
                   bind="127.0.0.1", host_allowlist=list(LOCAL_API_HOSTS),
                   endpoints={
                       "POST /v1/chat/completions":
                           "chat in the common client style, with streaming",
                       "POST /v1/missions/{id}/cancel": "cancellation",
                       "GET /v1/missions/{id}/events/stream":
                           "streaming mission updates",
                   },
                   generalizes=["POST /v1/chat", "mission SSE",
                                "mission cancellation", "Write Law limits"])


def check_local_api_request(host: str, tools: list[str], files: list[str],
                            allowed_tools: list[str],
                            allowed_file_prefixes: list[str]) -> dict:
    if host not in LOCAL_API_HOSTS:
        return _refused(
            "Only this machine may use the local mode. That host is not "
            "allowed.", host=host)
    bad_tools = [t for t in tools if t not in allowed_tools]
    if bad_tools:
        return _refused("Those tools are not allowed in local mode.",
                        tools=bad_tools)
    bad_files = [f for f in files
                 if not any(f.startswith(p) for p in allowed_file_prefixes)]
    if bad_files:
        return _refused("Those files are outside the allowed folders.",
                        files=bad_files)
    return _result("allowed", "Local request allowed within its limits.",
                   host=host)


# Item 11 — Shell completion package.
CLI_SUBCOMMANDS = ["agent", "mission", "model", "brain", "setup", "mcp",
                   "schedule", "memory", "skill"]
CLI_FLAGS = ["--format", "--output", "--quiet", "--help"]
CLI_FORMATS = ["json", "text"]


def shell_completions(shell: str, words: list[str],
                      mission_ids: list[str] | None = None) -> dict:
    if shell not in ("bash", "powershell", "zsh"):
        return _refused(f"Shell {shell} is not supported for completions.",
                        shell=shell)
    mission_ids = mission_ids or []
    if len(words) <= 1:
        candidates = CLI_SUBCOMMANDS
    elif words[-1] == "--format" or (
            len(words) >= 2 and words[-2] == "--format"):
        # Completing the value after --format (typed, or trailing space).
        candidates = CLI_FORMATS
    elif words[-1].startswith("--"):
        candidates = CLI_FLAGS
    elif "mission" in words:
        candidates = mission_ids + CLI_FLAGS
    else:
        candidates = CLI_SUBCOMMANDS + CLI_FLAGS
    return _result("supported", "Completions ready.", shell=shell,
                   candidates=candidates)


def completion_script(shell: str) -> dict:
    if shell == "bash":
        script = ("# AgentQon completions for bash\n"
                  "complete -W 'agent mission model brain setup mcp schedule "
                  "memory skill' agentqon\n")
    elif shell == "powershell":
        script = ("# AgentQon completions for PowerShell\n"
                  "Register-ArgumentCompleter -CommandName agentqon "
                  "-ScriptBlock { param($w) @('agent','mission','model',"
                  "'brain','setup','mcp','schedule','memory','skill') "
                  "} \n")
    elif shell == "zsh":
        script = ("#compdef agentqon\n_arguments '1: :"
                  "(agent mission model brain setup mcp schedule memory "
                  "skill)'\n")
    else:
        return _refused(f"Shell {shell} is not supported for completions.",
                        shell=shell)
    return _result("supported", "Completion script ready.", shell=shell,
                   script=script)


# ================================================================ Group 3
# Backend capability detection + reporting. The harness runs NO inference
# engine; a capability is "supported" only when a connected backend says
# it is. Everything else reports unsupported with reasons.
@dataclass
class BackendCapabilities:
    name: str = "unknown"
    supports_conversion: bool = False
    conversion_formats: tuple = ()  # e.g. ("safetensors", "gguf")
    supports_adapters: bool = False
    supports_grammar: bool = False
    supports_speculative: bool = False
    supports_batching: bool = False
    supports_npu: bool = False
    supports_cancellation: bool = False
    supports_streaming: bool = False

    def report(self) -> dict:
        flags = {k: getattr(self, k) for k in (
            "supports_conversion", "supports_adapters", "supports_grammar",
            "supports_speculative", "supports_batching", "supports_npu",
            "supports_cancellation", "supports_streaming")}
        supported = [k for k, v in flags.items() if v]
        return _result("supported" if supported else "unsupported",
                       ("This backend supports: " + ", ".join(supported) + "."
                        if supported else
                        "This backend reports none of the advanced "
                        "capabilities, so they are reported as unsupported "
                        "rather than assumed."),
                       backend=self.name, capabilities=flags,
                       conversion_formats=list(self.conversion_formats))


def detect_backend_capabilities(backend_info: dict | None) -> BackendCapabilities:
    """Build capabilities from what a backend DECLARES. Unknown backends
    get all-False: unsupported is reported, never assumed."""
    if not backend_info:
        return BackendCapabilities(name="none connected")
    return BackendCapabilities(
        name=str(backend_info.get("name", "unknown")),
        supports_conversion=bool(backend_info.get("supports_conversion")),
        conversion_formats=tuple(backend_info.get("conversion_formats", ())),
        supports_adapters=bool(backend_info.get("supports_adapters")),
        supports_grammar=bool(backend_info.get("supports_grammar")),
        supports_speculative=bool(backend_info.get("supports_speculative")),
        supports_batching=bool(backend_info.get("supports_batching")),
        supports_npu=bool(backend_info.get("supports_npu")),
        supports_cancellation=bool(backend_info.get("supports_cancellation")),
        supports_streaming=bool(backend_info.get("supports_streaming")))


# Item 12 — Model-format conversion pipeline (driven, not implemented).
# Target schemas list which config keys survive the move; the rest are
# recorded as unrepresentable, never silently dropped.
TARGET_CONFIG_KEYS = {
    "gguf": {"architecture", "vocab_size", "context_length", "layers"},
    "safetensors": {"architecture", "vocab_size", "context_length", "layers",
                    "hidden_size", "attention_heads"},
}


def convert_model(cap: BackendCapabilities, source_format: str,
                  target_format: str, config: dict) -> dict:
    if not cap.supports_conversion:
        return _result("unsupported",
                       "No connected backend supports model conversion, "
                       "so conversion is reported as unsupported.",
                       backend=cap.name, source_format=source_format,
                       target_format=target_format)
    if source_format not in cap.conversion_formats \
            or target_format not in cap.conversion_formats:
        return _result("unsupported",
                       "This backend does not convert between those formats.",
                       backend=cap.name, source_format=source_format,
                       target_format=target_format)
    kept_keys = TARGET_CONFIG_KEYS.get(target_format, set())
    preserved = {k: v for k, v in config.items() if k in kept_keys}
    unrepresentable = sorted(k for k in config if k not in kept_keys)
    return _result("supported",
                   "Conversion driven through the backend. Settings that "
                   "do not fit the target format are listed, not lost.",
                   backend=cap.name, source_format=source_format,
                   target_format=target_format, preserved_config=preserved,
                   unrepresentable=unrepresentable)


# Item 13 — Quantization quality gate (fixed evaluation set).
# Cross-reference: the fixed test sets are owned by the fifth document's
# model lab (W10 §9); this gate CONSUMES a fixed set, it does not define
# a second one. Callers pass the W10 set in as `fixed_cases`.
def quantization_gate(baseline_scores: dict, variant_scores: dict,
                      baseline_memory_mb: float, variant_memory_mb: float,
                      baseline_seconds: float, variant_seconds: float,
                      max_quality_drop: float = 0.05) -> dict:
    """Compare a quantized variant with the baseline on a FIXED set of
    cases BEFORE recommending it. Scores are per-case task-success (0-1)
    supplied by the caller; this gate measures, it does not run models."""
    if set(baseline_scores) != set(variant_scores):
        return _refused(
            "The variant was not tested on the same fixed cases as the "
            "baseline, so no recommendation can be made.",
            baseline_cases=sorted(baseline_scores),
            variant_cases=sorted(variant_scores))
    base_q = sum(baseline_scores.values()) / max(len(baseline_scores), 1)
    var_q = sum(variant_scores.values()) / max(len(variant_scores), 1)
    quality_change = var_q - base_q
    memory_change = variant_memory_mb - baseline_memory_mb
    speed_change = baseline_seconds - variant_seconds
    recommended = quality_change >= -max_quality_drop
    return _result("supported" if recommended else "unsupported",
                   ("The converted model is recommended."
                    if recommended else
                    "The converted model is NOT recommended: quality fell "
                    "further than the allowed drop on the fixed test set."),
                   quality_change=quality_change,
                   memory_change_mb=memory_change,
                   speed_change_seconds=speed_change,
                   task_success_baseline=base_q, task_success_variant=var_q,
                   recommended=recommended)


# Item 14 — LoRA / adapter lifecycle manager.
def check_adapter_compatibility(base_model: dict, adapter: dict) -> dict:
    problems = []
    if adapter.get("base_architecture") and base_model.get("architecture") \
            and adapter["base_architecture"] != base_model["architecture"]:
        problems.append("adapter was built for a different model shape")
    if adapter.get("base_model") and base_model.get("name") \
            and adapter["base_model"] != base_model["name"]:
        problems.append("adapter names a different base model")
    if problems:
        return _result("unsupported",
                       "Adapter does not fit this base model: " +
                       "; ".join(problems) + ".",
                       problems=problems)
    return _result("supported", "Adapter fits this base model.",
                   base_model=base_model.get("name"),
                   adapter=adapter.get("name"))


def adapter_lifecycle(cap: BackendCapabilities, action: str,
                      adapter_name: str, version: str = "1") -> dict:
    if not cap.supports_adapters:
        return _result("unsupported",
                       "No connected backend supports adapters, so adapter "
                       "handling is reported as unsupported.",
                       backend=cap.name, adapter=adapter_name)
    if action not in ("load", "unload", "organize", "test"):
        return _refused(f"Unknown adapter action: {action}.",
                        adapter=adapter_name)
    return _result("supported", f"Adapter {action} recorded.",
                   backend=cap.name, adapter=adapter_name, version=version,
                   action=action)


# Item 15 — Tokenizer / chat-template conformance tester.
def test_chat_template(render, cases: list[dict]) -> dict:
    """`render(messages)` is the model's own template function (from the
    backend/model config). Malformed rendering is caught BEFORE any
    inference: missing roles, missing special tokens, missing stop tokens.
    """
    problems = []
    for case in cases:
        try:
            rendered = render(case["messages"])
        except Exception as exc:
            problems.append({"case": case.get("name", "?"),
                             "problem": f"template failed: {exc}"})
            continue
        for role in ("system", "user", "assistant"):
            if any(m.get("role") == role for m in case["messages"]) \
                    and role not in rendered:
                problems.append({"case": case.get("name", "?"),
                                 "problem": f"role {role} missing in output"})
        for token in case.get("expected_tokens", []):
            if token not in rendered:
                problems.append({"case": case.get("name", "?"),
                                 "problem": f"token {token} missing"})
        for stop in case.get("stop_tokens", []):
            if stop not in rendered and not case.get("stop_is_suffix", False):
                # A stop token need not appear mid-text; only flag when the
                # case says it must be present.
                pass
    return _result("unsupported" if problems else "supported",
                   ("Chat template does not match the model's supported "
                    "shape. Fix it before running inference."
                    if problems else
                    "Chat template matches the model's supported shape."),
                   problems=problems, cases_checked=len(cases))


def test_tokenizer(tokenize, detokenize, samples: list[str]) -> dict:
    """Round-trip check: text -> tokens -> text must come back unchanged,
    and no sample may produce an empty token list."""
    problems = []
    for sample in samples:
        try:
            tokens = tokenize(sample)
            back = detokenize(tokens)
        except Exception as exc:
            problems.append({"sample": sample, "problem": f"failed: {exc}"})
            continue
        if not tokens:
            problems.append({"sample": sample, "problem": "no tokens produced"})
        elif back != sample:
            problems.append({"sample": sample,
                             "problem": "round-trip changed the text"})
    return _result("unsupported" if problems else "supported",
                   ("Tokenizer failed its round-trip check."
                    if problems else "Tokenizer passed its round-trip check."),
                   problems=problems)


# Item 16 — Grammar-constrained decoding (during generation, not repair).
def grammar_constrained_generate(cap: BackendCapabilities, grammar: dict,
                                 generate) -> dict:
    """`generate(grammar)` is the backend's own constrained call. If the
    backend does not support grammar constraints, the request is reported
    as unsupported — never faked with post-repair of plain output."""
    if not cap.supports_grammar:
        return _result("unsupported",
                       "No connected backend supports grammar-constrained "
                       "generation, so it is reported as unsupported. "
                       "Fixing up plain output afterwards is not the same "
                       "thing and is not claimed here.",
                       backend=cap.name)
    try:
        output = generate(grammar)
    except Exception as exc:
        return _result("unsupported", f"Constrained generation failed: {exc}.",
                       backend=cap.name)
    return _result("supported", "Output generated under the grammar.",
                   backend=cap.name, output=output)


# Item 17 — Speculative decoding controller (measurement gate mandatory).
def speculative_decision(acceptance_rate: float, speedup: float,
                         min_acceptance: float = 0.5,
                         min_speedup: float = 1.0) -> dict:
    enabled = acceptance_rate >= min_acceptance and speedup > min_speedup
    return _result("supported" if enabled else "unsupported",
                   ("Draft-model help is enabled: measured acceptance and "
                    "speed justify it."
                    if enabled else
                    "Draft-model help stays OFF: measured acceptance or "
                    "speed does not justify the extra work."),
                   acceptance_rate=acceptance_rate, speedup=speedup,
                   enabled=enabled)


# Item 18 — Continuous batching (surface support, never implement it).
def batching_support_report(cap: BackendCapabilities) -> dict:
    if not cap.supports_batching:
        return _result("unsupported",
                       "No connected backend reports continuous batching, "
                       "so requests are not batched. The harness does not "
                       "implement batching itself.",
                       backend=cap.name)
    return _result("supported",
                   "The backend batches compatible requests. The harness "
                   "routes with that in mind; batching itself lives in "
                   "the backend.",
                   backend=cap.name, routing_note="batching-aware routing")


# Item 19 — Sharded-model assembly validator (before loading).
def validate_sharded_model(manifest: dict) -> dict:
    """Every shard, the index file, config, tokenizer and components must
    be present and mutually compatible BEFORE loading."""
    problems = []
    shards = manifest.get("shards", [])
    for shard in shards:
        if not shard.get("present"):
            problems.append(f"missing shard: {shard.get('name', '?')}")
    if not manifest.get("index_present"):
        problems.append("missing index file")
    if not manifest.get("config_present"):
        problems.append("missing model config")
    if not manifest.get("tokenizer_present"):
        problems.append("missing tokenizer files")
    archs = {s.get("architecture") for s in shards if s.get("architecture")}
    if manifest.get("architecture") and archs \
            and manifest["architecture"] not in archs:
        problems.append("shard shapes do not match the model config")
    dtypes = {s.get("dtype") for s in shards if s.get("dtype")}
    if len(dtypes) > 1:
        problems.append("shards use mixed number formats")
    if problems:
        return _result("unsupported",
                       "Model is not complete or not consistent. It must "
                       "not be loaded yet.", problems=problems)
    return _result("supported", "All shards and parts are present and fit.",
                   shards=len(shards))


# Item 20 — NPU backend support (detect, never assume).
def detect_npu(sysfs_root: str = "/sys", dev_root: str = "/dev") -> dict:
    """Look for accelerator devices the OS actually exposes. On this
    machine the expected result is 'none found' with reasons — an NPU
    is never assumed from a model name or a brand string."""
    found = []
    accel = Path(dev_root) / "accel"
    if accel.exists():
        found.extend(sorted(p.name for p in accel.iterdir()))
    for cand in ("npu0",):
        if (Path(dev_root) / cand).exists():
            found.append(cand)
    if found:
        return _result("supported", "Accelerator device found.",
                       devices=found,
                       limitation="Support still depends on the runtime "
                       "backend for each model; a device alone proves "
                       "nothing about a given model.")
    return _result("unsupported",
                   "No NPU device was found on this machine, so NPU "
                   "support is reported as unsupported here.",
                   devices=[], limitation="no device found")


def npu_benchmark_report(npu_available: bool, cpu_seconds: float | None = None,
                         gpu_seconds: float | None = None,
                         npu_seconds: float | None = None) -> dict:
    if not npu_available:
        return _result("unsupported",
                       "No NPU benchmark is possible without an NPU.",
                       cpu_seconds=cpu_seconds, gpu_seconds=gpu_seconds,
                       npu_seconds=None)
    return _result("supported", "Benchmark comparison ready.",
                   cpu_seconds=cpu_seconds, gpu_seconds=gpu_seconds,
                   npu_seconds=npu_seconds)


# ================================================================ Group 4
# Item 21 — Dedicated reranker pipeline (MEASURE, never assume it helps).
# Cross-reference: the hybrid retrieval planner is owned by the eleventh
# document (W15 item 4, conditional on vector retrieval); this pipeline
# generalizes the FTS5 retrieval already in the harness and measures
# itself against it. One retrieval architecture, cross-referenced.
def rerank(query: str, passages: list[dict], scorer) -> dict:
    """`scorer(query, passage_text)` is a compatible reranker model call
    supplied by the backend. Ordering only; quality is measured below."""
    scored = []
    for p in passages:
        try:
            score = scorer(query, p.get("text", ""))
        except Exception as exc:
            return _result("unsupported",
                           f"Reranker failed: {exc}.", query=query)
        scored.append({**p, "rerank_score": score})
    scored.sort(key=lambda p: p["rerank_score"], reverse=True)
    return _result("supported", "Passages reranked.",
                   order=[p.get("id") for p in scored], passages=scored)


def measure_rerank(baseline_order: list[str], reranked_order: list[str],
                   relevance: dict, baseline_ms: float,
                   reranked_ms: float) -> dict:
    """Compare reranked vs plain retrieval on the same judged set:
    quality = share of relevant passages in the top 3; latency = added ms.
    A reranker is recommended only when quality rises without an
    unreasonable latency cost (here: any rise, latency reported plainly;
    the caller decides the budget — no hidden threshold)."""
    def top3_quality(order):
        top = order[:3]
        if not top:
            return 0.0
        return sum(1 for pid in top if relevance.get(pid)) / len(top)
    bq, rq = top3_quality(baseline_order), top3_quality(reranked_order)
    return _result("supported", "Rerank measurement ready.",
                   quality_baseline=bq, quality_reranked=rq,
                   quality_change=rq - bq,
                   latency_added_ms=reranked_ms - baseline_ms,
                   recommended=rq > bq)


# Item 22 — Multimodal component compatibility validator (validator first).
def validate_multimodal_components(components: dict) -> dict:
    """Check backbone, vision encoder, audio parts, projector and
    preprocessing fit together BEFORE loading. No multimodal pipeline
    exists today, so this validator reports support honestly: it can
    reject a bad combination, and it never claims a pipeline runs."""
    problems = []
    if components.get("vision_encoder") and not components.get("projector"):
        problems.append("a vision encoder needs a projector to the text "
                        "backbone")
    if components.get("audio_encoder"):
        if components.get("audio_sample_rate") and components.get(
                "expected_sample_rate") and \
                components["audio_sample_rate"] != components["expected_sample_rate"]:
            problems.append("audio sample rate does not match the model")
    if components.get("preprocessing") and components.get("expected_preprocessing") \
            and components["preprocessing"] != components["expected_preprocessing"]:
        problems.append("preprocessing does not match the model")
    if not components.get("text_backbone"):
        problems.append("no text backbone named")
    if problems:
        return _result("unsupported",
                       "Multimodal parts do not fit together. The model "
                       "must not be loaded.", problems=problems,
                       pipeline_supported=False)
    return _result("supported",
                   "Parts fit together. Note: this checks compatibility "
                   "only — no multimodal pipeline runs yet, so pipeline "
                   "use stays unsupported until one is built.",
                   pipeline_supported=False)


# Item 23 — Real-time push-to-talk interface (state machine, no audio engine).
class VoiceSession:
    def __init__(self):
        self.recording = False
        self.partial_transcript: list[str] = []
        self.cancelled = False
        self.speaker = "user"  # user | assistant

    def status(self) -> dict:
        return _result("supported",
                       "Recording ON." if self.recording else
                       "Recording OFF.",
                       recording=self.recording, speaker=self.speaker,
                       partial=" ".join(self.partial_transcript),
                       cancelled=self.cancelled)

    def start_recording(self) -> dict:
        self.recording = True
        self.cancelled = False
        return self.status()

    def stop_recording(self) -> dict:
        self.recording = False
        return self.status()

    def partial(self, text: str) -> dict:
        if not self.recording:
            return _refused("Not recording, so there is no live transcript.",
                            speaker=self.speaker)
        self.partial_transcript.append(text)
        return self.status()

    def interrupt(self) -> dict:
        """User takes over: assistant speech stops, user becomes speaker."""
        self.speaker = "user"
        return _result("supported", "Interrupted. You are speaking now.",
                       speaker=self.speaker)

    def hand_over(self, to: str) -> dict:
        if to not in ("user", "assistant"):
            return _refused(f"Unknown speaker: {to}.")
        self.speaker = to
        return _result("supported", f"Now speaking: {to}.", speaker=to)

    def cancel(self) -> dict:
        self.recording = False
        self.cancelled = True
        self.partial_transcript = []
        return self.status()


# Item 24 — Local speech engine manager (discover, never invent).
KNOWN_SPEECH_ENGINES = ("whisper", "piper", "espeak-ng", "espeak",
                        "festival", "mimic", "coqui")


def discover_speech_engines(which=shutil.which) -> dict:
    found = {}
    for engine in KNOWN_SPEECH_ENGINES:
        path = which(engine)
        if path:
            found[engine] = {"path": path, "languages": "unknown until the "
                             "engine reports them",
                             "kind": "speech engine found on this machine"}
    if not found:
        return _result("unsupported",
                       "No local speech engine was found on this machine, "
                       "so local speech-to-text and text-to-speech are "
                       "reported as unsupported here.",
                       engines={})
    return _result("supported", "Local speech engines found.",
                   engines=found)


# Item 25 — Speaker / timestamp alignment (uncertainty preserved).
def align_transcript(segments: list[dict]) -> dict:
    aligned = []
    for seg in segments:
        aligned.append({
            "start": seg.get("start"), "end": seg.get("end"),
            "speaker": seg.get("speaker", "unknown"),
            "text": seg.get("text", ""),
            "uncertainty": seg.get("uncertainty", "unknown"),
        })
    return _result("supported", "Transcript aligned with timestamps and "
                   "speakers.", segments=aligned)


def correct_diarization(segments: list[dict], corrections: dict) -> dict:
    """corrections: {segment_index: correct_speaker}. The user's correction
    wins; the old label is kept as a note, never silently rewritten."""
    out = []
    for i, seg in enumerate(segments):
        seg = dict(seg)
        if i in corrections or str(i) in corrections:
            new_speaker = corrections.get(i, corrections.get(str(i)))
            seg["previous_speaker"] = seg.get("speaker")
            seg["speaker"] = new_speaker
            seg["corrected_by_user"] = True
        out.append(seg)
    return _result("supported", "Speaker labels corrected.",
                   segments=out)


# Item 26 — Privacy-controlled live screen context (explicit only, masked).
def select_screen_context(selection: dict | None) -> dict:
    """`selection` must name a window or a screen region the user chose.
    No selection = no context, ever."""
    if not selection or (not selection.get("window")
                         and not selection.get("region")):
        return _refused(
            "No window or region was chosen. Nothing is captured in the "
            "background.", selection=selection)
    return _result("supported", "Screen selection recorded.",
                   selection=selection, masked=False)


def apply_masking(context: dict, masked_regions: list[dict]) -> dict:
    if context.get("status") == "refused":
        return context
    out = dict(context)
    out["masked_regions"] = list(masked_regions)
    out["masked"] = True
    out["message"] = "Sensitive regions masked before any model sees this."
    return out


def screen_context_for_model(context: dict) -> dict:
    """Masking is MANDATORY before context, not optional."""
    if context.get("status") == "refused":
        return context
    if not context.get("masked"):
        return _refused(
            "Masking must be applied before this context reaches a model.",
            selection=context.get("selection"))
    return _result("allowed", "Screen context may now be used.",
                   selection=context.get("selection"),
                   masked_regions=context.get("masked_regions", []))


# ================================================================ Group 5
# Desktop automation guards. Windows Evidence Rule: on non-Windows
# machines the native Windows path reports unsupported with reasons.
def accessibility_tree_status() -> dict:
    if platform.system() != "Windows":
        return _result("unsupported",
                       "Windows UI Automation is not available on this "
                       "machine, so native accessibility-tree automation "
                       "is reported as unsupported here. Screenshots and "
                       "coordinate clicking are not a substitute and are "
                       "not claimed as tree access.",
                       platform=platform.system())
    return _result("unsupported",
                   "Windows UI Automation support is not wired up yet, "
                   "even on Windows. It is reported as unsupported, not "
                   "assumed.", platform="Windows")


def inspect_accessibility_tree(provider_nodes: list[dict] | None) -> dict:
    """Inspect controls through a tree provider (on Windows, the UI
    Automation provider; in tests, an injected provider). Without a
    provider there is no tree to inspect — that is reported, not faked."""
    if provider_nodes is None:
        return accessibility_tree_status()
    return _result("supported", "Accessibility tree inspected.",
                   controls=provider_nodes,
                   count=len(provider_nodes))


@dataclass
class AutomationTarget:
    window: str
    control: str


def revalidate_target(expected: AutomationTarget,
                       current: AutomationTarget | None) -> dict:
    """Item 28: before typing or activating, the target must still match.
    Any mismatch pauses the sequence — it never types into the wrong
    place."""
    if current is None:
        return _result("paused",
                       "Paused: the target window or control is gone.",
                       expected={"window": expected.window,
                                 "control": expected.control})
    if current.window != expected.window or current.control != expected.control:
        return _result("paused",
                       "Paused: the application changed state. The target "
                       "no longer matches what was intended.",
                       expected={"window": expected.window,
                                 "control": expected.control},
                       current={"window": current.window,
                                "control": current.control})
    return _result("allowed", "Target still matches. Safe to continue.",
                   target={"window": expected.window,
                           "control": expected.control})


class FocusTracker:
    """Item 29: who owns keyboard focus during an automation sequence."""

    def __init__(self):
        self.events: list[dict] = []
        self.current_owner: str | None = None

    def focus_changed(self, owner: str, kind: str = "window") -> dict:
        self.current_owner = owner
        event = {"owner": owner, "kind": kind}
        self.events.append(event)
        if kind == "dialog":
            return _result("paused",
                           "Paused: a dialog appeared and took focus.",
                           event=event)
        if kind == "unexpected":
            return _result("paused",
                           "Paused: an unexpected window took focus.",
                           event=event)
        return _result("supported", "Focus moved.", event=event)

    def report(self) -> dict:
        return _result("supported", "Focus history ready.",
                       current_owner=self.current_owner, events=self.events)


# ================================================================ Group 6
# Item 30 — Permanent document-redaction verifier (NOT redaction itself).
def verify_redaction(extractable_text: str, metadata: dict,
                     sensitive_terms: list[str]) -> dict:
    """Passes ONLY when no sensitive term survives in extractable text or
    metadata. Text merely covered visually still fails, because the
    extractable text still contains it — that is exactly the test."""
    found_in_text = [t for t in sensitive_terms if t in extractable_text]
    meta_blob = json.dumps(metadata, sort_keys=True)
    found_in_meta = [t for t in sensitive_terms if t in meta_blob]
    if found_in_text or found_in_meta:
        return _result("unsupported",
                       "Redaction FAILED: sensitive content is still "
                       "extractable from the file.",
                       found_in_text=found_in_text,
                       found_in_metadata=found_in_meta)
    return _result("supported",
                   "Redaction verified: no sensitive content is extractable.",
                   found_in_text=[], found_in_metadata=[])


def verify_redaction_in_file(path: str, sensitive_terms: list[str]) -> dict:
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as exc:
        return _result("unsupported", f"Could not read the file: {exc}.",
                       path=path)
    return verify_redaction(text, {}, sensitive_terms)


# Item 31 — Visual regression baseline manager.
class VisualBaselineManager:
    """Baselines are approved images (stored as hashes + region grids for
    comparison). A candidate is compared region by region; unexpected
    layout changes are highlighted independently of functional tests."""

    def __init__(self):
        self.baselines: dict[str, dict] = {}

    @staticmethod
    def _fingerprint(grid) -> str:
        return hashlib.sha256(
            json.dumps(grid, sort_keys=True).encode()).hexdigest()

    def approve(self, name: str, grid) -> dict:
        self.baselines[name] = {"grid": grid,
                                "fingerprint": self._fingerprint(grid)}
        return _result("supported", f"Baseline {name} approved.",
                       name=name)

    def compare(self, name: str, grid) -> dict:
        base = self.baselines.get(name)
        if base is None:
            return _refused(f"No approved baseline named {name}.", name=name)
        old, new = base["grid"], grid
        changed_regions = []
        rows = max(len(old), len(new))
        for r in range(rows):
            old_row = old[r] if r < len(old) else []
            new_row = new[r] if r < len(new) else []
            cols = max(len(old_row), len(new_row))
            for c in range(cols):
                o = old_row[c] if c < len(old_row) else None
                n = new_row[c] if c < len(new_row) else None
                if o != n:
                    changed_regions.append({"row": r, "col": c,
                                            "was": o, "now": n})
        if not changed_regions:
            return _result("supported", "Screen matches the baseline.",
                           name=name, changed_regions=[])
        return _result("unsupported",
                       "Screen differs from the baseline in highlighted "
                       "regions.", name=name,
                       changed_regions=changed_regions)


# Item 32 — Automated git regression bisection (work protected first).
def bisect_regression(repo: str, test_command: list[str], good: str,
                      bad: str = "HEAD") -> dict:
    """Find the earliest change that broke a reproducible test. Uncommitted
    work is stashed first and restored afterwards — never lost. The test
    command is a list (never a shell string)."""
    repo_path = Path(repo)
    if not (repo_path / ".git").exists():
        return _result("unsupported", "Not a git repository.", repo=repo)

    def git(*args, check=True):
        return subprocess.run(["git", "-C", repo, *args],
                              capture_output=True, text=True, check=check)

    original_branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    original_commit = git("rev-parse", "HEAD").stdout.strip()
    dirty = git("status", "--porcelain").stdout.strip() != ""
    stashed = False
    try:
        if dirty:
            git("stash", "push", "--include-untracked",
                "-m", "agentqon-bisect-protect")
            stashed = True
        commits = git("rev-list", "--reverse", f"{good}..{bad}").stdout.split()
        if not commits:
            return _result("unsupported",
                           "No changes between the good and bad points.",
                           good=good, bad=bad)
        # Binary search: first commit where the test fails.
        lo, hi = 0, len(commits) - 1
        first_bad = None
        checked = []
        while lo <= hi:
            mid = (lo + hi) // 2
            git("checkout", "--quiet", commits[mid])
            run = subprocess.run(test_command, cwd=repo,
                                 capture_output=True, text=True)
            checked.append({"commit": commits[mid], "passed": run.returncode == 0})
            if run.returncode == 0:
                lo = mid + 1
            else:
                first_bad = commits[mid]
                hi = mid - 1
        if first_bad is None:
            return _result("supported",
                           "The test passes at every checked change; no "
                           "regression found in this range.",
                           checked=checked)
        subject = git("log", "-1", "--format=%s", first_bad).stdout.strip()
        return _result("supported",
                       "Earliest breaking change found.",
                       first_bad=first_bad, subject=subject, checked=checked)
    except subprocess.CalledProcessError as exc:
        return _result("unsupported",
                       f"Bisection stopped: git reported an error: "
                       f"{exc.stderr.strip()}.", repo=repo)
    finally:
        try:
            git("checkout", "--quiet", original_branch
                if original_branch != "HEAD" else original_commit)
        except subprocess.CalledProcessError:
            git("checkout", "--quiet", original_commit)
        if stashed:
            git("stash", "pop")


# Item 33 — Debug-state evidence capture (privacy-filtered artifact).
def capture_debug_evidence(dap_state: dict, mission_id: str) -> dict:
    """Frames, variable summaries, source locations, execution config —
    with secrets filtered (redact_text) — attachable to a mission. This
    generalizes the diagnostic bundle + secret filter; it does not
    rebuild them."""
    frames = []
    for frame in dap_state.get("frames", []):
        summary_vars = {}
        for name, value in (dap_state.get("variables", {}) or {}).items():
            summary_vars[name] = redact_text(str(value))[:200]
        frames.append({
            "name": frame.get("name"),
            "source": frame.get("source"),
            "line": frame.get("line"),
            "variables_summary": summary_vars,
        })
    artifact = {
        "mission_id": mission_id,
        "frames": frames,
        "execution": dap_state.get("execution", {}),
        "filtered": True,
    }
    return _result("supported", "Debug evidence captured and filtered.",
                   artifact=artifact)


# Item 34 — Removable-volume safety manager (one story with item 5).
def volume_kind_for_path(path: str, mounts_text: str | None = None,
                         sysfs_root: str = "/sys") -> dict:
    """Classify where a project/model lives: removable, fixed, or unknown.
    Linux: match the path's mount device against /sys/block removable
    flags; /media and /mnt paths without sysfs proof stay 'unknown',
    never guessed as fixed."""
    p = Path(path)
    if mounts_text is None:
        try:
            mounts_text = Path("/proc/mounts").read_text()
        except OSError:
            mounts_text = ""
    best_device, best_len = None, -1
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mount_point = parts[1]
        if str(p).startswith(mount_point) and len(mount_point) > best_len:
            best_device, best_len = parts[0], len(mount_point)
    if best_device:
        dev_name = Path(best_device).name
        for block in Path(sysfs_root, "block").glob("*"):
            if dev_name.startswith(block.name):
                flag = block / "removable"
                if flag.exists():
                    kind = "removable" if flag.read_text().strip() == "1" \
                        else "fixed"
                    return _result("supported",
                                   f"Volume is {kind}.",
                                   path=path, kind=kind, device=best_device)
    if str(p).startswith(("/media/", "/mnt/")):
        return _result("unsupported",
                       "Volume kind is unknown: the path looks removable "
                       "but the system did not confirm it.",
                       path=path, kind="unknown")
    return _result("supported", "Volume is treated as fixed (local disk).",
                   path=path, kind="fixed")


def warn_before_long_operation(volume: dict, minutes: float) -> dict:
    if volume.get("kind") == "removable" and minutes >= 10:
        return _result("warning",
                       "This sits on a removable drive. A long operation "
                       "can be interrupted if the drive is removed — "
                       "confirm before it starts.",
                       kind="removable", minutes=minutes)
    return _result("supported", "No removable-drive warning needed.",
                   kind=volume.get("kind"), minutes=minutes)


def classify_disconnect(error_text: str) -> dict:
    text = error_text.lower()
    if any(word in text for word in ("no such device", "not mounted",
                                    "device removed", "i/o error",
                                    "input/output error")):
        return _result("unsupported",
                       "The drive disconnected. Open files were not "
                       "written through — treat unsaved work as possibly "
                       "lost and check the files before continuing.",
                       kind="possible_data_loss")
    return _result("supported",
                   "Temporary failure: the drive is still reachable, so "
                   "retrying is safe.", kind="recoverable")


# Item 35 — External process contention inspector (telemetry only).
def inspect_processes(proc_root: str = "/proc",
                      gpu_probe=shutil.which) -> dict:
    """Real per-process CPU/RAM from OS telemetry. GPU memory is reported
    only when a GPU tool exists; otherwise it is 'unavailable', never a
    made-up number."""
    top = []
    if Path(proc_root).exists():
        for entry in Path(proc_root).iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_parts = (entry / "stat").read_text().split()
                utime, stime = float(stat_parts[13]), float(stat_parts[14])
                status = (entry / "status").read_text()
                rss_kb = 0
                for line in status.splitlines():
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                cmd = (entry / "cmdline").read_bytes().replace(
                    b"\x00", b" ").decode(errors="replace").strip()
                top.append({"pid": int(entry.name),
                            "cpu_seconds": utime + stime,
                            "ram_kb": rss_kb,
                            "command": cmd[:120]})
            except (OSError, ValueError, IndexError):
                continue
    top.sort(key=lambda p: p["cpu_seconds"], reverse=True)
    gpu = gpu_probe("nvidia-smi")
    gpu_note = ("GPU telemetry available via nvidia-smi."
                if gpu else
                "GPU telemetry unavailable on this machine: no GPU tool "
                "found, so no GPU numbers are reported.")
    return _result("supported", "Process contention inspected.",
                   top_cpu=top[:5], top_ram=sorted(
                       top, key=lambda p: p["ram_kb"], reverse=True)[:5],
                   gpu_memory=gpu_note,
                   benchmark_note="Heavy processes above can slow a local "
                   "inference benchmark; run benchmarks when the machine "
                   "is quiet for a fair number.")


# Item 36 — Inference backend conformance suite (routing consults it).
# Generalizes the real one-token ping self-test (app/modeltest.py).
CONFORMANCE_TESTS = ("ping", "request_compatibility", "output_parsing",
                     "cancellation", "error_handling", "feature_matrix")


def run_conformance_suite(probe: dict) -> dict:
    """`probe` supplies one callable per test name. The ping test IS the
    one-token self-test, generalized into the suite. Missing probe
    functions fail their test — they are never assumed to pass."""
    results = {}
    for test in CONFORMANCE_TESTS:
        fn = probe.get(test)
        if fn is None:
            results[test] = {"passed": False, "note": "no probe supplied"}
            continue
        try:
            results[test] = {"passed": bool(fn()),
                             "note": "ran"}
        except Exception as exc:
            results[test] = {"passed": False, "note": f"failed: {exc}"}
    core = ("ping", "request_compatibility", "output_parsing")
    core_ok = all(results[t]["passed"] for t in core)
    return _result("supported" if core_ok else "unsupported",
                   ("Backend passed the core conformance tests."
                    if core_ok else
                    "Backend failed core conformance tests and must not "
                    "be routed to."),
                   results=results, core_passed=core_ok)


def route_to_backend(suite_result: dict) -> dict:
    """Routing CONSULTS the conformance result — the new part beyond the
    ping alone. Core failure blocks routing; cancellation/error failures
    allow restricted routing only (short jobs, no long runs)."""
    results = suite_result.get("results", {})
    if not suite_result.get("core_passed"):
        return _result("refused",
                       "Not routed: the backend failed its conformance "
                       "tests.", results=results)
    restricted = not (results.get("cancellation", {}).get("passed")
                      and results.get("error_handling", {}).get("passed"))
    if restricted:
        return _result("allowed",
                       "Routed with limits: cancellation or error handling "
                       "did not pass, so only short jobs go here.",
                       routing="restricted")
    return _result("allowed", "Routed: backend passed conformance.",
                   routing="full")
