# Use AgentQon as your agent's brain

You built an agent somewhere else — a Python script, a bot, an
MCP-native tool, a cron job with ambitions. Give it a brain: one API
key and one POST, and AgentQon's panel (rival specialist agents, a
critic, evaluation, long-term memory) does the thinking behind your
agent's calls. Five minutes, three steps.

## 1. Install and start AgentQon

```sh
npm install -g agentqon
agentqon            # first run sets everything up and starts the server
```

Connect a model if you haven't (Ollama is auto-detected, or add a
provider key under **Advanced → Connections**). The brain endpoint
runs the same pipeline as a mission — with no model connected it
answers `503 model_not_connected`, honestly, exactly like the rest
of the product. It never fakes an answer.

## 2. Create a brain key

```sh
agentqon brain key
```

The key (`aqb_…`) is shown once and saved into your local CLI config
(owner-only permissions). The server stores only a hash of it. One
active key per workspace: creating a new one rotates (the old key
dies), and `agentqon brain revoke` kills it outright. Run
`agentqon brain` any time for status and fresh copy-paste snippets.

The key is *scoped*: it authorises `POST /v1/brain` and nothing
else. It is not a login, it cannot read missions or change settings,
and Shield's local protections (loopback host allowlist, per-install
token everywhere else) are unchanged.

## 3. Paste the snippet

Python, standard library only — nothing to install:

```python
import json, urllib.request

KEY = "aqb_..."                       # from `agentqon brain key`
URL = "http://127.0.0.1:8000/v1/brain"  # your server's /v1/brain

def ask(prompt, session=None):
    body = {"prompt": prompt}
    if session:
        body["session"] = session
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["answer"]

print(ask("What should we do first?"))
```

`agentqon brain` prints this same snippet with your real URL and
key already filled in, plus curl and Node versions.

## The call

```
POST /v1/brain
Authorization: Bearer aqb_…
Content-Type: application/json

{
  "prompt":  "What should we do first?",          // required
  "context": "Facts the calling agent already has", // optional
  "session": "any-id-you-choose"                    // optional
}
```

Response:

```json
{
  "answer": "…the winning candidate's deliverable…",
  "mission_id": "mis_…",
  "status": "completed",
  "session": "any-id-you-choose",
  "agent": "…the winning agent…",
  "candidates": 3,
  "usage": { }
}
```

Every brain call is an ordinary mission under the hood — it shows up
in `agentqon missions`, carries a proof pack, and records its usage
like any mission. Pass a stable `session` id and the exchange is
written to long-term memory (session scope) and used to ground the
next call with the same id.

Errors are the platform's standard envelope: `401` for a missing or
revoked key, `503 model_not_connected` when no model is connected,
`429` when the per-workspace run rate is exceeded.

## Hosted AgentQon

Against a hosted deployment the flow is identical: `agentqon login`
first, then `agentqon brain key` — the key is created in your
workspace on that server, and the snippet's URL is the hosted one.

## MCP hosts

The npm package ships a one-file, stdlib-only stdio MCP server that
exposes a single `ask_agentqon` tool:

```
mcp/agentqon_brain_mcp.py
```

Point your MCP host at it with two environment variables —
`AGENTQON_BRAIN_KEY` and `AGENTQON_BASE_URL` — and your agent gets
AgentQon's panel as a native tool.
