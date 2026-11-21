#!/usr/bin/env python3
"""AgentQon brain — stdio MCP bridge (stdlib only, no dependencies).

Exposes TWO tools to any MCP host (Claude Code, Cursor, a home-grown
agent…): `ask_agentqon` — the host's agent asks a question and
AgentQon's panel — rival agents, critic, evaluation, long-term
memory — does the thinking behind a single call to POST /v1/brain;
and `agentqon_limits` — a local, no-network tool that states plainly
what the brain can and cannot do, so hosts do not assume capabilities
that are not there. (W17 expansion: the server direction grows here,
in this file, never as a second copy.)

Configuration (environment):
  AGENTQON_BRAIN_KEY   the brain key from `agentqon brain key` (req.)
  AGENTQON_BASE_URL    default http://127.0.0.1:8000

Run:  AGENTQON_BRAIN_KEY=aqb_… python3 agentqon_brain_mcp.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("AGENTQON_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
BRAIN_KEY = os.environ.get("AGENTQON_BRAIN_KEY", "")

TOOL = {
    "name": "ask_agentqon",
    "description": (
        "Ask AgentQon a question and get the panel's answer. AgentQon "
        "convenes specialist agents who draft rival answers, critiques "
        "them and evaluates the survivors; long-term memory grounds "
        "the result. Pass `session` to keep memory continuity across "
        "calls, and `context` for anything the caller already knows."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "The question or task."},
            "context": {"type": "string",
                        "description": "Optional context from the caller."},
            "session": {"type": "string",
                        "description": "Optional session id for memory "
                                       "continuity."},
        },
        "required": ["prompt"],
    },
}


LIMITS_TOOL = {
    "name": "agentqon_limits",
    "description": (
        "State plainly what AgentQon can and cannot do, so a host never "
        "assumes a capability that is not there. Local only: no network "
        "call, no brain key needed."),
    "inputSchema": {"type": "object", "properties": {}},
}


def agentqon_limits() -> tuple[str, bool]:
    """Local limits statement (W17). Kept honest: AgentQon connects to
    models, it does not run its own inference engine."""
    return (("AgentQon can: answer through its panel of agents, run "
             "missions with approval steps, and remember facts you save.\n"
             "AgentQon cannot: run its own inference engine, change its "
             "own files, read your screen or clipboard in the background, "
             "or control a live debugging session without your explicit "
             "go-ahead. Anything it cannot do is reported as unsupported, "
             "never pretended."), False)


def ask_agentqon(args: dict) -> tuple[str, bool]:
    """Returns (text, is_error). Errors are reported, never hidden."""
    if not BRAIN_KEY:
        return ("AGENTQON_BRAIN_KEY is not set — create a key with "
                "`agentqon brain key` and pass it in the environment.",
                True)
    body = {"prompt": str(args.get("prompt", ""))}
    if args.get("context"):
        body["context"] = str(args["context"])
    if args.get("session"):
        body["session"] = str(args["session"])
    req = urllib.request.Request(
        BASE_URL + "/v1/brain",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {BRAIN_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read())["error"]["message"]
        except Exception:
            msg = f"HTTP {e.code}"
        return (f"AgentQon refused the call ({e.code}): {msg}", True)
    except Exception as e:  # connection refused, timeout, bad JSON…
        return (f"Could not reach AgentQon at {BASE_URL}: {e}. "
                "Is the server running (`agentqon`)?", True)
    answer = out.get("answer") or "(the panel returned no answer text)"
    return (f"{answer}\n\n[mission {out.get('mission_id', '?')} · "
            f"agent {out.get('agent') or 'panel'}]", False)


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        params = msg.get("params") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agentqon-brain", "version": "21.2.0"}}}
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL, LIMITS_TOOL]}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") == LIMITS_TOOL["name"]:
            text, is_error = agentqon_limits()
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_error}}
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": f"unknown tool: {params.get('name')}"}],
                "isError": True}}
        text, is_error = ask_agentqon(params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"unknown method: {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
