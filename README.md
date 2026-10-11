# OmniMind AI

**A collaborative, evidence-driven agent platform** — built from *AI Agent Ecosystem: A Practical Engineering Textbook* (Ed. 1.0, 10 Oct 2026).

OmniMind AI runs a mission through **two independent agents** with different strategies, has a **critic** attack their claims, **verifies evidence deterministically**, and presents **two candidates side-by-side** with visible dimension scores — instead of one agent's confident answer.

> North star: *build a trusted system that helps people finish difficult work better — not a spectacle that makes many agents talk.*

## Install — one command (v20 terminal harness)

**One command installs everything** — you don't need to prepare
anything. The installer checks for Node.js ≥ 18 and Python ≥ 3.11 and,
when either is missing or too old, installs it for you through your
system package manager (apt-get / dnf / pacman, Homebrew on macOS,
winget on Windows), announcing every system change *before* it runs and
using sudo only when needed. (One exception, by design: on macOS
without Homebrew it prints Homebrew's official install command and
stops rather than running it silently.) The same command then installs
the harness **and all of OmniMind's Python libraries** — it runs
`omnimind setup`, which builds a private Python environment for the
platform and pip-installs its `requirements.txt` into it, verifying
that every library imports (your system Python is never modified).
When it finishes, the machine is fully ready.

```sh
npm install -g omnimind-ai
```

```sh
curl -fsSL https://raw.githubusercontent.com/adynrisy/omnimind-ai/main/install.sh | sh
```

(Windows: `irm https://raw.githubusercontent.com/adynrisy/omnimind-ai/main/install.ps1 | iex` — the scripts are `install.sh` / `install.ps1` in this repo.)

Then just run:

```sh
omnimind
```

The npm package bundles the **complete product** — the terminal harness
*and* the platform server (FastAPI app, web UI, the 280-agent roster).
The installer copies the platform into `~/.omnimind/server/<version>`,
builds `~/.omnimind/venv` and installs the platform's Python
dependencies as part of the install; `omnimind` then starts the local
server on http://127.0.0.1:8000 (or the next free port), opens the web
UI and drops you into the chat. All user data — missions, SQLite DB,
vault keys, connections — lives in `~/.omnimind/data` and survives
updates; only code is ever replaced.

Installer options: `--no-setup` (or `OMNIMIND_SKIP_SETUP=1`) installs
only the harness and defers the library install to first run;
`--dry-run` prints every command the installer *would* run on your
machine and changes nothing (`-NoSetup` is the install.ps1 equivalent).
`omnimind doctor` includes a libraries check (venv present, installed
distribution count, all required modules import).

> One honest line: the npm name `omnimind-ai` and the installer URLs
> above activate when the package is published / the repo is hosted.
> Everything already works today from this repo:
> `OMNIMIND_SOURCE=$PWD/cli ./install.sh` installs from the local
> directory, and `OMNIMIND_CLI_TARBALL=<file.tgz>` from a packed tarball
> (`cd cli && npm pack`).

### Harness commands

| command | what it does |
|---|---|
| `omnimind` | set up (first run), start the local server, open the UI, chat REPL |
| `omnimind run <mission…> [--live] [--budget N]` | run one mission, phases narrated live, result panel + proof summary |
| `omnimind setup` | install/refresh the bundled platform in ~/.omnimind |
| `omnimind start` / `stop` / `status` | manage the local server (pid, port, health) |
| `omnimind serve` | run the local server in the foreground |
| `omnimind open` / `logs` | open the web UI / tail the server log |
| `omnimind agents [--top N] [--type T]` | suitability-ranked agents with score bars |
| `omnimind stats` | agent performance ledger + arena leaderboard |
| `omnimind missions [--limit N]` | recent missions |
| `omnimind proof <mission-id>` | computed claim labels + save `proof-<id>.md` |
| `omnimind arena <task…>` | blind duel between two agents — you vote, identities reveal |
| `omnimind schedules` / `schedules add` | recurring missions |
| `omnimind simulate <mission-id>` | run a mission's strategies through a seeded model world |
| `omnimind login [--server URL]` | point the CLI at a hosted OmniMind instead (remote mode) |
| `omnimind logout` / `whoami` / `use-local` | identity and mode |
| `omnimind doctor` | styled health checklist |
| `omnimind update` | the update commands |

In the REPL (`omnimind ❯`), type a mission in plain words or use
`/agents`, `/missions`, `/proof <id>`, `/arena`, `/schedules`,
`/simulate <id>`, `/help`, `/exit`. The harness is zero-dependency
Node (hand-rolled ANSI styling: violet→cyan banner, box panels, phase
spinner, score bars) and honours `NO_COLOR` and non-TTY output — piped
output stays plain. `omnimind login` stores the session token in
`~/.omnimind/config.json` with `0600` permissions; no command ever
prints it. Set `OMNIMIND_NO_OPEN=1` to never auto-open the browser.
(The from-source developer path — `uvicorn` on port 8077 — is under
"Run it" below; the harness manages its own server on port 8000.)

## Status: Phase 3 (V3) — BYOK connections, OmniRoute gateway, cost-aware routing

Phase 3 adds the trust layer: **bring-your-own-key connections** with an
envelope-encrypted credential vault, a **live model catalog** synced from
your connections (never hardcoded), a **deterministic cost-aware router**
(six modes, hard filters first, every decision persisted with reasons), and
**race-safe budget reservations** so parallel role calls can't overspend a
mission cap. Missions can be **paused, resumed, revised** (bounded rounds,
new candidate versions) and **exported**; projects group missions; an
admin surface carries a **kill switch**. The optional **OmniRoute**
gateway (MIT, pinned v3.8.51) runs as an isolated sidecar — OmniMind
talks to it only through its OpenAI-compatible API, and a full mission
has been run through it into a local model. The golden rule: a mission
pinned to your connection uses **only your key** — if it fails, the
mission fails loudly; a platform key is never silently substituted.
Details: `docs/PROJECT_STATUS.md`, `docs/integrations/`.

Phase 1 built and verified 2026-10-10 (24/24 tests, live HTTP mission run).
Phase 2 adds the three things that make it a product instead of a demo:

1. **PostgreSQL** — the production store, behind the same repository interface as SQLite (which remains as zero-setup dev mode). Switch with one env var.
2. **A durable queue** — mission runs are jobs in the database: claimed by workers with a lease, retried with exponential backoff, dead-lettered after max attempts, replayable from the API. Cancellation propagates. The API can drain jobs inline (dev) or a separate worker process can (production).
3. **Real AI agents** — OpenAI-compatible LLM adapters for the research and critic agents. The same code path runs a local **Ollama** model (no key needed) or any cloud provider (OpenAI, OpenRouter, Groq, …) — selected purely by environment variables. If the provider fails mid-mission, the run fails loudly with the error on record (v20: there is no substitute engine to fall back to).

What the platform does, exactly as the textbook specifies (Ch.22):
1 mission → 3-task DAG → 2 independent agents → 1 critic round → schema + citation validation → 2 distinct candidates → user comparison & selection.

## Run it

```bash
cd omnimind-ai
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077
```

Then open in your browser:

http://127.0.0.1:8077

Click **"Load example: Startup Research (Ch.23)"**, then **"Create & Run Mission"**.

Run the tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

### Phase 2 modes (all environment-driven — see `.env.example`)

**PostgreSQL** (dev mode without it still works, on SQLite):
```bash
export OMNIMIND_DATABASE_URL=postgresql://omnimind:change-me@127.0.0.1:5432/omnimind
```

**A model connection is required for generation — see "Connect a
model" below.** There is no built-in substitute engine: with no model
connected, missions refuse to run (HTTP 503 `model_not_connected`)
rather than completing with canned output.

**External worker** (production shape: API only enqueues, workers execute):
```bash
export OMNIMIND_WORKER_MODE=external
.venv/bin/uvicorn app.main:app --port 8077 &
.venv/bin/python -m app.worker        # add more workers freely — leases prevent double-claims
```

## Connect a model — required for generation

OmniMind generates **only** through a real model. There is no offline
demo mode and no placeholder output: if nothing is connected, the
server still boots and everything non-generative works (registry,
usage, schedules, channels), but mission runs, revisions and arena
duels are refused with HTTP 503 and error code `model_not_connected`,
and any mission that somehow starts ends **FAILED** with that reason
on its record. The web header shows the state at all times — *model:
<name>* when connected, an amber **No model connected** chip (click
it to jump to Connections) when not; `omnimind doctor` reports the
same.

Connect any ONE of these (first match wins):

1. **Your own key (BYOK)** — in the web UI: ⚙ Advanced → Connections →
   add an OpenAI-compatible provider key. Per-tenant, encrypted at
   rest; a mission can also be *pinned* to one connection.
2. **Server environment** — any OpenAI-compatible endpoint:
   ```bash
   export OMNIMIND_LLM_BASE_URL=https://openrouter.ai/api/v1
   export OMNIMIND_LLM_API_KEY=<your key>
   export OMNIMIND_LLM_MODEL=openai/gpt-4o-mini
   export OMNIMIND_LLM_PRICE_IN=0.15 OMNIMIND_LLM_PRICE_OUT=0.60   # USD per 1M tokens, for the cost ledger
   ```
3. **OmniRoute sidecar** — if an OmniRoute gateway answers on
   `http://127.0.0.1:20128/v1`, it is used automatically.
4. **Local Ollama** — zero configuration: if Ollama is running on its
   default port (`http://127.0.0.1:11434`), OmniMind detects it and
   uses the pulled model — no key, nothing leaves the machine:
   ```bash
   ollama serve &
   ollama pull qwen2.5:3b
   ```

Local endpoints (Ollama/OmniRoute/explicit local URL) are *probed*
before use — a configured endpoint that isn't answering counts as
**not connected**, not as a silent fallback.

## What's real in this slice

- **Mission contracts (Ch.4)** — validated server-side; unknown tool names are *rejected*, budgets are hard caps enforced during the run.
- **State machines in code (Ch.17/30/31)** — a mission can never jump from `awaiting_approval` to `completed` on an agent's say-so; illegal transitions return 409.
- **Agent registry & lifecycle (Ch.6)** — curated agents with immutable versions; quarantine an agent in the UI and it immediately gets **zero** new assignments (recovery path: testing → approved → active).
- **Scheduler (Ch.30)** — hard eligibility filters first, then transparent ranking by measured quality / reliability / latency / cost.
- **Independent first passes (Ch.9/10)** — each research agent receives only the mission contract, never the other's output.
- **Tool Gateway (Ch.12)** — agents never touch tools directly; every call is policy-, classification- and budget-checked and audited. High-risk tools (`email.send`, `purchase.create`, …) create an approval request bound to an exact action preview. The textbook's own budget test is implemented verbatim: spent $0.99 of a $1.00 cap → the next $0.05 call is blocked with `BUDGET_EXCEEDED`.
- **Evidence engine (Ch.11)** — every claim carries sources; citation checks verify the source exists, has an excerpt, and its *kind* can support the claim (an opinion piece cannot back an unqualified factual claim). Candidate B's partnership claim is deliberately contested — watch the critic flag it and the evaluator score it down.
- **Evaluation (Ch.14)** — dimension scores (evidence, completeness, robustness, cost efficiency) stay visible with notes; rubric versioned (`research-v1`); deterministic test results shown per candidate.
- **Tenant isolation (Ch.16/17)** — every read is tenant-scoped; cross-tenant access returns 404 (existence hidden). Switch the Tenant field in the UI header to prove it.
- **Immutable artifacts (Ch.17)** — plans, candidates and comparisons are hash-addressed and versioned, never overwritten.
- **Idempotency (Ch.18)** — replaying a mission create with the same key returns the same mission; reusing the key with a different body returns 409.
- **Cost ledger (Ch.15)** — per-mission spend by category (inference / tools / evaluation), with 20% of the budget reserved for final evaluation.

## What is still deliberately simple (and where it goes next)

- **Sources** are still a clearly labelled seed library inside the product (`app/sources.py`). LLM agents may only cite from it — invented source ids are stripped in code and the claim is marked *unsupported* for the evaluator. Live web research arrives through the Gateway in Phase 3 — the claim/citation model doesn't change.
- **No canned generation (v20)** — the deterministic stand-in agents are gone from the product. Generation always comes from a connected real model (see *Connect a model*); without one, runs fail fast and honestly. The test suite's single stub model lives under `tests/` only and can never be enabled from product configuration.
- **Auth** is still dev headers (`X-Tenant-Id` / `X-User-Id`) — replace with real auth before any public deployment (Ch.16). The queue is database-backed by design (ADR-002); if job volume outgrows it, the worker interface is the seam where a broker would slot in.

## API surface (Ch.18)

```
POST /v1/missions                      (requires Idempotency-Key header)
GET  /v1/missions
GET  /v1/missions/{id}                 full detail: tasks, candidates, evaluations, critiques, usage, events
POST /v1/missions/{id}/run             enqueues a durable job (202 + job_id in external worker mode)
GET  /v1/jobs?status=dead              the queue, incl. the dead-letter list
GET  /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/replay          resurrect a dead/failed job
POST /v1/missions/{id}/cancel
POST /v1/missions/{id}/select          {candidate_id}
POST /v1/missions/{id}/approve         {approval_id, decision}
GET  /v1/missions/{id}/events?after={event_id}
GET  /v1/agents?capability=research.web
POST /v1/agents/import-agency         install the bundled agency-agents roster (admin)
POST /v1/agents/import-library         install the subagents mega-library roster (admin, v14)
POST /v1/agents/{agent_version_id}/lifecycle
GET  /v1/missions/{id}/proof-pack      evidence record w/ computed confidence labels (v15)
POST /v1/arena/duels                   blind head-to-head duel between two personas (v15)
GET  /v1/arena/duels                   the tenant's duels (blind until voted)
POST /v1/arena/duels/{id}/vote        {winner: "a"|"b"} — reveals agents, moves the ledger
GET  /v1/arena/leaderboard             arena standings merged with the mission record (v15)
POST /v1/schedules                     create a recurring mission schedule (v15)
GET  /v1/schedules                     list schedules (fired by the worker tick — no cron)
PATCH /v1/schedules/{id}               rename / re-cadence / enable / disable
DELETE /v1/schedules/{id}
POST /v1/channels/telegram/link-code   one-time code binding a chat to your account (v16)
GET  /v1/channels/telegram/status      channel state: configured? linked? as whom? (v16)
DELETE /v1/channels/telegram/link      detach this account's Telegram chat (v16)
POST /v1/channels/telegram/webhook     Telegram update delivery (secret-verified, deduped)
POST /v1/channels/whatsapp/link-code   one-time code binding a number to your account (v17)
GET  /v1/channels/whatsapp/status      channel state: configured? linked? as whom? (v17)
DELETE /v1/channels/whatsapp/link      detach this account's WhatsApp number (v17)
GET  /v1/channels/whatsapp/webhook     Meta subscription handshake (hub.challenge)
POST /v1/channels/whatsapp/webhook     Cloud API delivery (HMAC-signed, wamid-deduped)
GET  /v1/whatsapp/onboard/public-config  is one-click connect enabled? + public app/config ids (v18)
POST /v1/whatsapp/onboard/exchange     Embedded Signup: login code -> verified token, stored encrypted (v18)
GET  /v1/whatsapp/connection           this tenant's self-serve WhatsApp connection (v18)
DELETE /v1/whatsapp/connection         disconnect: unsubscribe + crypto-shred the token (v18)
GET  /v1/artifacts/{id}
GET  /v1/sources
GET  /v1/usage
GET  /health
```

Errors use one envelope everywhere: `{error: {code, message, correlation_id, retryable}}`.

## Layout

```
apps/web/          mission UI (single page, served by the API)
app/               the modular monolith:
  domain.py          contracts + state machines (frozen schemas, Ch.3-31)
  store.py           tenant-scoped persistence + immutable artifacts
  registry.py        curated agents + scheduler ranking
  planner.py         mission -> 3-task DAG (20% evaluation reserve)
  adapters.py        universal agent interface (protocol only — engines live in llm_adapter.py)
  llm_adapter.py     real LLM adapters (OpenAI-compatible: Ollama local, or any cloud provider)
  config.py          environment-driven settings (database, agent mode, worker mode)
  worker.py          durable queue worker: leases, backoff retries, dead-letter, replay
  gateway.py         tool gateway: policy, budget, approvals, audit
  evaluator.py       schema + citation checks, dimension scoring
  orchestrator.py    plan -> research -> critique -> evaluation -> comparison
  sources.py         seed source library (the citable evidence set)
  proofpack.py       (v15) the mission evidence record w/ computed confidence labels
  arena.py           (v15) blind persona duels feeding the performance ledger
  schedules.py       (v15) recurring missions, fired by the worker tick (no cron)
  telegram_client.py (v16) the one Bot API client — every outbound Telegram call
  telegram.py        (v16) Telegram front door: linking, chat missions, result push, arena votes
  whatsapp_client.py (v17) the one Graph API client — every outbound WhatsApp call
  whatsapp.py        (v17) WhatsApp front door: LINK codes, the 24h window, template alerts, arena votes
  whatsapp_onboard.py (v18) self-serve onboarding: Embedded Signup exchange, per-tenant connections, webhook routing
docs/ROADMAP.md    the full OmniMind AI implementation roadmap (Phases 0-7)
docs/adr/          architecture decision records (ADR-001 monolith, ADR-002 Postgres/queue/LLM)
tests/             unit + integration, incl. PostgreSQL tests when OMNIMIND_TEST_DATABASE_URL is set
```

## Telegram front door (v16) — operator setup

Run OmniMind from Telegram: a linked chat can send missions as plain
messages, receive the result (with 🧾 Proof Pack and ⚔️ New Duel
buttons) when the worker finishes, vote in arena duels with inline
buttons, and list its schedules. Setup is four steps:

1. **Create the bot** — talk to [@BotFather](https://t.me/BotFather),
   `/newbot`, and keep the token it gives you. Never commit the token.
2. **Configure the server** — set the environment and restart:
   - `OMNIMIND_TELEGRAM_BOT_TOKEN` — the BotFather token (required;
     without it the channel is disabled and its endpoints answer 503)
   - `OMNIMIND_TELEGRAM_WEBHOOK_SECRET` — a random secret of your own
     choosing (required for the webhook to accept updates)
   - `OMNIMIND_TELEGRAM_BOT_USERNAME` — the bot's username, no `@`
     (only used to build the `t.me/<bot>?start=<code>` deep link)
   - `OMNIMIND_TELEGRAM_API_BASE` — optional, default
     `https://api.telegram.org` (tests point it at a stub)
3. **Register the webhook** with the same secret:

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     --data-urlencode "url=https://<your-host>/v1/channels/telegram/webhook" \
     --data-urlencode "secret_token=<SECRET>"
   ```

4. **Link users** — each user opens ⚙ Advanced → Telegram in the web
   UI, generates a one-time code (~10 min, single-use), and sends
   `/start <code>` to the bot (or taps the deep link). Missions need a
   worker running (`OMNIMIND_WORKER_MODE=external` +
   `python -m app.worker`), because results are pushed from the worker
   tick — the webhook itself only queues work.

## WhatsApp front door (v17) — operator setup

Run OmniMind from WhatsApp (Meta WhatsApp Business Cloud API): a
linked number can send missions as plain messages, receive the result
(with 🧾 Proof Pack and ⚔️ New Duel reply buttons) when the worker
finishes, vote in arena duels with reply buttons, and list its
schedules. One platform rule shapes everything: **Meta only allows
free-form messages within 24 hours of the user's last message.**
Inside the window, results push in full. Outside it, the worker
sends ONE approved template alert (if configured) inviting a reply —
the reply reopens the window and the held full result is delivered
immediately; with no template configured, the result is simply held
until the user's next message. Nothing is ever dropped for being out
of window.

Setup, in order:

1. **Create a Meta developer account** at
   [developers.facebook.com](https://developers.facebook.com) (the
   Facebook account you log in with becomes the developer account).
2. **Create an app** — My Apps → Create App → type **Business** —
   and name it (e.g. "OmniMind"). This app owns the WhatsApp setup,
   the access token, and the app secret.
3. **Add the WhatsApp product** to the app (App Dashboard → Add
   Product → WhatsApp → Set up). This creates a WhatsApp Business
   Account (WABA) for the app.
4. **Use the test number to start** — WhatsApp → API Setup shows a
   free Meta **test phone number** and its **Phone number ID**. Add
   your own phone number under "Add phone number" in the recipient
   list (the test number may only message numbers you add there, up
   to 5 — enough to run the whole front door for real). The ID in
   this step is `OMNIMIND_WHATSAPP_PHONE_NUMBER_ID` — the ID, not
   the number itself.
5. **Get a token** — API Setup shows a temporary access token (valid
   ~24 h; fine for first runs). For production, create a **System
   User** (Business Settings → Users → System Users), assign it the
   app with `whatsapp_business_messaging` (and
   `whatsapp_business_management`), and generate a **permanent
   token** from that system user instead. Either way the value is
   `OMNIMIND_WHATSAPP_TOKEN`. Never commit a token.
6. **Find the app secret** — App Dashboard → Settings → Basic →
   App Secret (Show). It becomes `OMNIMIND_WHATSAPP_APP_SECRET`;
   with it set, webhook deliveries MUST carry a valid
   `X-Hub-Signature-256` (HMAC-SHA256 of the raw body) or they are
   refused. **Without an app secret the webhook is unauthenticated —
   local development only, never production** (see the warning at
   the top of `app/whatsapp.py`).
7. **Configure the server** — set the environment and restart:
   - `OMNIMIND_WHATSAPP_TOKEN` — from step 5 (required)
   - `OMNIMIND_WHATSAPP_PHONE_NUMBER_ID` — from step 4 (required)
   - `OMNIMIND_WHATSAPP_APP_SECRET` — from step 6 (required in
     production; see the warning above)
   - `OMNIMIND_WHATSAPP_VERIFY_TOKEN` — a random secret of YOUR OWN
     choosing (used only for the step-8 handshake)
   - `OMNIMIND_WHATSAPP_BUSINESS_NUMBER` — the number users message,
     digits only with country code, no `+` or spaces (the test
     number to start; only builds the `wa.me` tap-to-link URL in
     the UI/API)
   - `OMNIMIND_WHATSAPP_TEMPLATE_NAME` — the template from step 9
     (optional; empty = out-of-window results are held, not alerted)
   - `OMNIMIND_WHATSAPP_TEMPLATE_LANG` — its language code
     (default `en`)
   - `OMNIMIND_WHATSAPP_API_BASE` / `OMNIMIND_WHATSAPP_API_VERSION` —
     optional, defaults `https://graph.facebook.com` / `v21.0`
     (tests point the base at a stub)
8. **Register the webhook** — App Dashboard → WhatsApp →
   Configuration → Webhook → Edit:
   - Callback URL: `https://<your-host>/v1/channels/whatsapp/webhook`
   - Verify token: the exact value of `OMNIMIND_WHATSAPP_VERIFY_TOKEN`
     (Meta calls the GET handshake with it; a match earns the
     challenge back and the webhook activates)
   - Then, in the same screen, **Subscribe** to the **`messages`**
     webhook field (without the subscription, Meta sends nothing).
9. **Create the out-of-window alert template** — WhatsApp → Message
   Templates → Create template:
   - Category: **Utility**; name: exactly your
     `OMNIMIND_WHATSAPP_TEMPLATE_NAME` (e.g. `mission_result_alert`);
     language: your `OMNIMIND_WHATSAPP_TEMPLATE_LANG`.
   - Body: one sentence with exactly ONE variable, `{{1}}`, which
     the platform fills with the mission title — e.g.
     `Your OmniMind mission "{{1}}" has finished. Reply to this
     message to receive the full result.`
   - Submit for approval (utility templates are usually approved in
     minutes). Until it is approved, leave
     `OMNIMIND_WHATSAPP_TEMPLATE_NAME` empty — held results still
     deliver on the user's next message.
10. **Link users** — each user opens ⚙ Advanced → WhatsApp in the
    web UI and generates a one-time code (~10 min, single-use),
    then sends `LINK <code>` to the business number — or taps the
    `wa.me` link the panel shows, which pre-fills exactly that text.
    Commands after linking: plain text = a mission, `status`,
    `arena <topic>`, `schedules`, `help`. As with Telegram, missions
    need a worker running (results are pushed from the worker tick;
    the webhook itself only queues work).
11. **Go to production** — add your OWN phone number (WhatsApp → API
    Setup → Add phone number; the number must not be registered to a
    personal WhatsApp account), complete **business verification**
    (Business Settings → Business Info) when Meta asks for it, and
    swap `OMNIMIND_WHATSAPP_PHONE_NUMBER_ID`,
    `OMNIMIND_WHATSAPP_BUSINESS_NUMBER`, and the token to the
    production number/system user. Everything else stays the same.

## Self-serve WhatsApp onboarding (v18) — Embedded Signup

Everything above is the OPERATOR's number, wired by hand. v18 lets
every tenant connect **its own** WhatsApp business number with one
click — "the user just has to sign up or log in on Facebook with
OmniMind": the ⚙ Advanced → WhatsApp panel shows a **Connect
WhatsApp with Facebook** button; the user logs into Facebook in the
popup, picks their business and number, and OmniMind automatically
receives a long-lived access token, the WhatsApp Business Account
ID, and the phone number ID, subscribes itself to that WABA's
webhooks, and stores the token **encrypted in the tenant's BYOK
vault** (a connection of kind `whatsapp`; the API only ever shows
it masked). From then on:

- the tenant's WhatsApp sends use **its number and its token**
  (the v17 env number remains the fallback for tenants without a
  connection);
- inbound webhook deliveries route by the payload's
  `metadata.phone_number_id` to the owning tenant — a delivery for
  a number nobody connected is ignored, silently, with a 200;
- users of that number still link with the v17 `LINK <code>` flow
  (on the tenant's own business number, only that tenant's codes
  bind);
- **Disconnect** (panel, or `DELETE /v1/whatsapp/connection`)
  unsubscribes the app from the WABA and crypto-shreds the token.

Owner setup (one time, in the Meta app from the v17 section):

1. **Add the Facebook Login product** to the app (App Dashboard →
   Add Product → Facebook Login → Set up), then open **Facebook
   Login for Business → Configurations → Create configuration**:
   - Name: e.g. "OmniMind WhatsApp connect".
   - Login variation / flow type: **WhatsApp embedded signup**
     ("Get WhatsApp Business accounts and phone numbers" — the
     configuration type Meta reserves for this flow).
   - Permissions: **`whatsapp_business_management`** and
     **`whatsapp_business_messaging`** — both. The server verifies
     both are present on the returned token (via `debug_token`)
     and refuses to store anything when one is missing.
   - Save, and copy the **Configuration ID** it shows.
2. **App domains + OAuth settings** — Settings → Basic → App
   Domains: add your OmniMind deployment's domain. Facebook Login →
   Settings → Valid OAuth Redirect URIs: the Embedded Signup popup
   flow returns the code to the page that opened it, so the
   deployment origin must be allowed — add
   `https://<your-host>/` there (and enable "Login with the
   JavaScript SDK" if the console offers it). The Facebook SDK is
   loaded from `connect.facebook.net` by the panel; no other
   redirect URI is involved.
3. **Configure the server** — set the environment and restart:
   - `OMNIMIND_META_APP_ID` — the app's ID (Settings → Basic).
     Public value; the panel ships it to the browser.
   - `OMNIMIND_WHATSAPP_CONFIG_ID` — the Configuration ID from
     step 1. Also public.
   - `OMNIMIND_WHATSAPP_APP_SECRET` — the same app secret as v17
     step 6; the exchange uses it to swap the login code for a
     token and to inspect that token. Without all three, the panel
     shows "one-click connect is not enabled by the owner yet" and
     the exchange endpoint answers 503.
   - The webhook from v17 step 8 stays as it is: one callback URL
     serves every connected number (each tenant's subscription is
     created automatically during their signup), and the app secret
     above keeps authenticating deliveries.
4. **The honest gate — App Review.** A fresh configuration works
   immediately for **people who hold a role on the app** (admins,
   developers, testers on the app's Roles page). For the PUBLIC to
   connect, Meta must grant **Advanced Access** to
   `whatsapp_business_management` and `whatsapp_business_messaging`
   via **App Review** (App Dashboard → App Review → Permissions and
   Features: request both, with a screencast of the connect flow
   and your business use case), and the business must complete
   **business verification** when Meta asks. Until then, Facebook
   itself refuses non-role users at the login step — that is Meta's
   gate, not OmniMind's, and the v17 manual path remains the
   fallback for anyone it blocks.

## Roadmap

`docs/ROADMAP.md` is the full plan: Phase 2 (durable execution, real tool gateway integrations, PostgreSQL), Phase 3 (real collaboration + baseline A/B), Phase 4 (simulation worlds), Phase 5 (ecosystem growth), Phase 6 (external onboarding), Phase 7 (enterprise). Each phase has an exit gate — and Phase 1's own rule still applies: if collaboration doesn't beat a single strong agent on your benchmark tasks, simplify before scaling.

---
Built for Zohaib Khan · OmniMind AI · Phase 1 delivered 2026-10-10
