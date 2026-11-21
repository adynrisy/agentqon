# OmniRoute Compatibility Matrix

How OmniMind uses OmniRoute **v3.8.51** (pinned). Every row is marked from
live evidence gathered 2026-10-10; anything not probed is `unverified`,
never assumed.

Legend: **supported** = probed live and works · **partial** = works with a
documented gap/workaround · **unsupported** = probed, does not work ·
**not-used** = exists upstream, deliberately unused · **unverified** = not
probed yet.

| Capability | Status | Evidence / notes |
|---|---|---|
| OpenAI-compatible `GET /v1/models` | supported | 475 models returned with a gateway key |
| OpenAI-compatible `POST /v1/chat/completions` | supported | Real completion via `ollama-local/qwen2.5:1.5b` (fp_ollama, usage block) |
| Gateway API keys (create, scopes, hash validation) | supported | `POST /api/keys`; key required on the inference plane |
| Provider connection: local Ollama w/ custom base URL | supported | `POST /api/providers` → `ollama-local`; PATCH activates |
| Model discovery for local providers | **partial** | Chat models routable but NOT listed in `/v1/models`; provider registry empty; connection test "not supported". OmniMind workaround: user-declared catalog entries for allowlisted models |
| Pricing metadata for routing decisions | **partial** | No usable prices for local provider → OmniMind records `unknown`, reserves conservatively, shows estimate confidence honestly |
| `auto/*` combo routing | unverified | Catalog lists combos; one probe did not answer in 60s here. Tenants can allowlist/pin explicit models instead |
| Streaming (SSE) | unverified | OmniMind V3 uses non-streaming calls |
| Tool/function calling through gateway | unverified | Catalog marks tool-calling `unknown` accordingly |
| Embeddings endpoints | unverified | Embedding models are listed; not used by OmniMind V3 |
| OmniRoute failover / quota features | not-used | OmniMind owns routing + budget policy; OmniRoute is transport here |
| OmniRoute dashboard analytics | not-used | OmniMind surfaces its own cost ledger + route decisions |
| Management API dependence | avoided | OmniMind talks ONLY to the inference plane (`/v1/*`) with a gateway key; dashboard/management APIs were used once, manually, to provision the dev sidecar |

Re-probe rule: on any pin change, re-run the probes for every row marked
supported/partial and update this file with the date and result.
