# Connecting models

AgentQon has **no built-in model and no mock mode**. A mission runs on a
model you connect — and if none is connected, the mission refuses with a
`model_not_connected` message instead of inventing output. There are four
ways to connect one, checked in this order:

## 1. Bring your own key (BYOK) — web UI

Open the web UI → **Connections** → add an OpenAI-compatible provider
(base URL, API key, model name). Keys are stored in AgentQon's
envelope-encrypted vault on your machine — never in plain text, never
sent anywhere except the provider you named.

Best for: hosted models (OpenAI-compatible APIs, gateways, your own
endpoints) with per-tenant budgets.

## 2. Environment endpoint

Set three variables before starting AgentQon:

```sh
export AGENTQON_LLM_BASE_URL="https://your-endpoint.example/v1"
export AGENTQON_LLM_API_KEY="…"
export AGENTQON_LLM_MODEL="your-model-name"
agentqon start
```

On Windows (PowerShell): `$env:AGENTQON_LLM_BASE_URL = "…"`, and so on.

Best for: servers, containers, and scripted setups.

## 3. OmniRoute sidecar

If an OmniRoute sidecar is
running on port **20128**, AgentQon detects and uses it automatically.
That routes missions through OmniRoute's own provider pool and pricing.

Best for: existing OmniRoute users.

## 4. Ollama (fully local)

Install [Ollama](https://ollama.com), pull any chat model, and AgentQon
detects it on port **11434** automatically:

```sh
ollama pull qwen2.5
agentqon doctor     # "model: <name> (ollama)" when detected
```

No key, no account, no data leaves your machine. Detection is a live
probe: a configured-but-unreachable endpoint counts as **not connected** —
AgentQon will never silently pretend a dead endpoint answered.

## Which should I choose?

| Route | Cost | Privacy | Setup |
|---|---|---|---|
| BYOK key | Provider pricing | Prompts go to your provider | 2 minutes in the UI |
| Env endpoint | Provider pricing | Same as BYOK | 3 variables |
| OmniRoute | OmniRoute pricing | Via your OmniRoute pool | Already running? Nothing |
| Ollama | Free | Fully local | Install + one `pull` |

## Checking the connection

- Web UI status chip: green `model: <name>` or amber **No model connected**
- Terminal: `agentqon doctor` or `agentqon status`
- Server: `GET /health` reports `model: { connected, name, source }`

## If a model call fails

A connected model whose call fails makes the mission fail **loudly** —
you'll see the failure and its reason, never a silently substituted
template answer. Check the endpoint, the key, and the model name, then
re-run.
