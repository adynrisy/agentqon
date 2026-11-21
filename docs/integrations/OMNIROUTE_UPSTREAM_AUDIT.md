# OmniRoute — Upstream Audit

Audited: 2026-10-10. Auditor: OmniMind build (this repository).

## Identity and pin

| Fact | Value |
|---|---|
| Project | OmniRoute — open-source AI gateway / LLM router |
| Upstream | https://github.com/diegosouzapw/OmniRoute |
| Pinned version | **v3.8.51** (release tag, 2026-09-30) |
| Tag commit | `c1e30b7676975feb298b49eff6ff58923c04b89e` |
| Default branch at audit time | `release/v3.8.52` (in development — NOT used; the spec's version rule pins the latest stable tag, never a moving branch) |
| License | **MIT**, "Copyright (c) 2026 diegosouzapw" (LICENSE file at the tag) |
| Runtime | Node.js `>=22.22.2 <23 \|\| >=24 <27` (package.json engines); Next.js application |
| Local install used for verification | `npm install omniroute@3.8.51`, run as its own process on port **20128** with its own `DATA_DIR` |

Redistribution posture: MIT permits use, modification and redistribution with
the copyright and permission notice preserved. OmniRoute is integrated as an
**optional, separately-running sidecar** — OmniMind never vendors or modifies
its source. Attribution lives in `THIRD_PARTY_NOTICES.md`.

## What was verified live (evidence, not documentation claims)

- The pinned build installs and starts cleanly ("started in 7.7s",
  dashboard on :20128, API base `/v1`).
- Admin seeding via `INITIAL_PASSWORD`; dashboard session login via
  `POST /api/auth/login` works.
- Gateway API keys are created via `POST /api/keys` (scopes supported);
  inference-plane key validation is hash-based.
- A provider connection for a local Ollama instance was created via
  `POST /api/providers` (provider id resolves to `ollama-local`, custom
  `baseUrl` in providerSpecificData) and activated via PATCH.
- `GET /v1/models` with a gateway key returned **475 models** (provider
  catalogs + `auto/*` combos).
- `POST /v1/chat/completions` with model `ollama-local/qwen2.5:1.5b`
  returned a real completion from the local model (`system_fingerprint:
  fp_ollama`, usage reported).
- **Full OmniMind mission** ran through the sidecar: OmniMind tenant
  connection (kind `omniroute_gateway`) → OmniRoute → Ollama
  qwen2.5:1.5b → 2 candidates + 7 critiques, route decisions recorded.

## Findings that shape the integration

1. **Catalog gap (verified):** `ollama-local` chat models are routable by
   prefixed ID but are NOT listed by `GET /v1/models` (only its embedding
   models appear), its provider model registry stays empty, and its
   connection test returns "Provider test not supported". OmniMind handles
   this with user-declared catalog entries for allowlisted models
   (provenance-marked, price unknown) — see the compatibility matrix.
2. **No prices on this path:** the gateway's model list carries no usable
   per-token price for the local provider; OmniMind records the price as
   `unknown` and reserves budget conservatively. Unknown is never shown
   as $0.
3. **Auto combos are opaque:** `auto/*` combos route by OmniRoute's own
   policy; one probe (`auto/qwen`) did not answer within 60s in this
   environment. OmniMind treats combos as ordinary catalog entries and
   lets the tenant allowlist/pin control their use.
4. **Default bind warning:** the sidecar warns when listening on 0.0.0.0
   with no API-key requirement. OmniMind's guidance: run it on loopback
   or a private network, always behind its own gateway keys, and treat
   it as internal infrastructure (see SECURITY_THREAT_MODEL.md).
5. **Security model fit:** OmniRoute encrypts its stored provider keys at
   rest (it generates a `STORAGE_ENCRYPTION_KEY` on first run). OmniMind
   additionally keeps the *gateway* key in OmniMind's own envelope-
   encrypted vault; the two vaults are independent.

## Upgrade policy

Upgrades are deliberate: re-run this audit against the new tag, re-run the
compatibility matrix probes, bump the pin in deployment config, and record
the change in PROJECT_STATUS.md. Never track a moving branch in production.
