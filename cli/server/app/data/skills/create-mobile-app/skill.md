<!--
Source: microsoft/power-platform-skills
Path: plugins/mobile-apps/skills/create-mobile-app/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/mobile-apps/skills/create-mobile-app/SKILL.md
-->

> **Plugin check**: Run `node "${PLUGIN_ROOT}/scripts/check-version.js"` - if it outputs a message, show it to the user before proceeding.

**📋 Shared instructions: [shared-instructions.md](${PLUGIN_ROOT}/shared/shared-instructions.md)** — read first. Covers safety guardrails, memory bank usage, preferred-environment policy, connector-first rule, Windows CLI compat, command-failure handling.

# Create Power Apps Code App (Native)

Top-level orchestrator. Owns the user-visible flow; delegates planning to the `native-app-planner` agent and per-domain mutation to dedicated `/add-*` skills.

For every child skill handoff, pass the invocation-scoped
[app-edit-routing.md](../../shared/references/app-edit-routing.md) context:
`MOBILE_APP_ORCHESTRATING=1`, `orchestrator: create-mobile-app`, the absolute
`working_dir`, current `phase`, and the exact approved scope/answers for
implementation. Forward it through routers and native helpers; do not persist it
or rely on an earlier shell export. The plan exists before the app is fully built,
so children must use this context rather than mistake creation for a standalone
edit. Design/configuration children still own any approval explicitly delegated
to their phase; this marker never skips those gates.

## Workflow

Intent and creation-state checks → prerequisites → requirements (iOS + Android fixed) →
rough plan preview → Gate 1 architecture (native capabilities, connectors,
then data platform) → Gate 2 Dataverse model when selected → Gates 3 and 4
screen graph/specifications → environment and template preparation → app
initialization → design system → conditional Dataverse materialization and
sample data → Mobile Offline Profile offer for Dataverse apps → native
capabilities and connectors → navigation and screens → validation, preview,
and summary.

---

## Fresh-template working-directory mode

This skill assumes the user already has a **fresh** `microsoft/power-platform-skills/plugins/mobile-apps/template#main` template materialized with `degit` in the target working directory and has already run `npm install` there. The skill turns that fresh template into an app; it does not clone, degit, or copy a template itself.

**Fresh template required for creation.** If the working directory is not a fresh
template, STOP creation and follow the state-specific routing below. Do not send
an existing-app edit request to a new folder. Missing generated markers alone do
not prove freshness: if the template is customized or its state is unclear, ask
before proceeding. Only a request for a new app requires a fresh template folder.

Resolve the original request before classifying project state. Apply this
state-routing table once in Step 0, in the order below; file markers are
supporting evidence, not user intent or approval.

| State | Detection | Action |
|---|---|---|
| Creation continuation | Explicit creation-continuation intent or a valid current create handoff for this app, not a request to edit an existing app | Use Step 0's continuation path. Require matching unfinished creation progress and confirm the next phase before continuing; missing or conflicting progress requires clarification, not fresh scaffolding. |
| Already-created app | Existing-app intent or confirmed app customizations/materialized state, even when generated markers are missing. `memory-bank.md`, `.datamodel-manifest.json`, and `src/generated/services/*.ts` can corroborate state, but do not select creation resume | STOP creation: never re-scaffold it. If the request changes this app, preserve the original request and execute the shared existing-app entry routing; invoke `/edit-app` only after **Full app integration**. If the user wants a separate new app, ask for a fresh template folder. |
| Fresh template | Confirmed new-app intent and a confirmed uncustomized starter; `package.json`, `app.config.js`, `auth.config.json`, and `tamagui.config.ts` exist; `node_modules/expo` exists; no materialized app bindings, `memory-bank.md`, `.datamodel-manifest.json`, or generated Dataverse services. An approved creation plan and its planning artifacts are allowed | Proceed to Step 1 only when every condition is satisfied. Missing generated markers alone do not establish freshness. |
| Template not installed | Confirmed new-app intent and a confirmed uncustomized starter with the required template files, but `node_modules/expo` is absent | STOP: ask user to run `npm install` in the template folder, then rerun. Do not provision ADO npm tokens here. |
| Not template | Confirmed new-app intent in a new/unsupported directory, after excluding existing-app work and creation continuation | STOP: ask user to materialize `microsoft/power-platform-skills/plugins/mobile-apps/template#main` into a new or empty folder with `degit` and run `npm install`; never overwrite existing work. |
| Unclear state or intent | New-app versus edit intent, customization, or creation progress cannot be established | Ask before selecting a workflow. Preserve the directory, plan, and history; do not default to fresh creation or resume. |

An approved `native-app-plan.md` alone is not a created-app marker. Creation
writes it before template preparation, and `assertFreshTemplate` intentionally
allows it. A plan alone also does not identify a safe resume phase: use current
caller context and saved creation progress, or ask. Do not treat an unrelated or
stale plan as approved for this request.

`/create-mobile-app` creates from a confirmed fresh starter or continues an
explicitly requested unfinished creation. It does not adopt, repair, overwrite,
or resume a completed app just because a bank or plan exists.

An already-created app is a routing boundary, not a reason to discard the
user's edit request. Do not merely suggest `/edit-app` or ask the user to repeat
the request. For a current-app feature request, execute
[app-edit-routing.md](../../shared/references/app-edit-routing.md), then invoke
`/edit-app` with the original request, absolute `working_dir`, and
`entry_choice: full-integration` only when that choice is approved. Reported
problems still route to `/debug-app`; dependency/template upgrades route to
`/check-updates`.

---

## TypeScript Gate Policy — no quality compromise

`tsc` is a **phase gate**, not a reflex after every tiny edit. The app may not advance past a gate until TypeScript is clean.

**Required gates:**
- **Scaffold gate:** Step 6.6 after existing-template preparation, `pa app init`, and dependency verification.
- **Dataverse/generated-services gate:** immediately after Step 8 returns and generated services/models are refreshed.
- **Navigation/skeleton gate:** after Step 10b layouts and Step 10.8 shared code/skeletons are written, before Step 11 builders launch.
- **Screen-wave gate:** after each Step 11 screen-builder wave returns, before launching the next wave.
- **Final gate:** before Step 12 starts the dev server.

**When a gate fails:**
1. Capture the full `tsc --noEmit` output once.
2. Classify errors by root cause (for example: generated model names, service option shapes, invalid UI props, typed percentage values, create/update payload typing, missing imports).
3. Repair in a batch.
4. Re-run the same gate once after the batch.
5. Continue only when the gate is clean, or stop/block according to the retry policy.

**Do not run full-app `tsc` after every microscopic local edit inside the same repair pass.** That is slower and encourages line-by-line patching. Batch root-cause fixes, then re-run the gate. This is a speed improvement only; it does **not** lower the quality bar.

**Hard stops:**
- Do not launch data-source work from a broken scaffold gate.
- Do not launch screen-builders from broken generated services, layouts, shared code, or skeletons.
- Do not launch wave N+1 until wave N passes its `tsc` gate.
- Do not start the dev server until the final gate is clean.
- Do not hide approved capability failures behind mocks or TODOs just to satisfy `tsc`.

---

## Build documentation protocol

This protocol applies after Step 0 selects fresh creation or an approved
creation continuation. Routing an existing-app request elsewhere, asking for
unclear intent, or declining creation does not create, reopen, or reset the
creation plan or change its phase status.

The run also writes a plan the user can read while it happens and keep afterwards:
`<working_dir>/docs/create-app-plan.html`, rendered by
[`scripts/app-docs.js`](${PLUGIN_ROOT}/scripts/app-docs.js) in the same Fluent format the
Power Pages `/create-site` plan uses. It is the record of *why* the app looks the way it does,
so a teammate opening the repo months later can see the environment, the approved data model,
and the screen plan without re-reading a transcript.

```bash
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' <command>
```

| Command | Use |
|---|---|
| `init --json-file <path>` | Once, at Step 2b, with `{"appName","dataPlatform"}`. Safe to re-run: a resume keeps recorded progress. The display name is user text, so it travels in the file, never as an argument. |
| `step --id <phase> --status <pending\|active\|done\|skipped\|failed> [--note "<text>"]` | At every phase boundary. Marking a phase `active` closes any previous one. |
| `set --section <name> --json '<object>'` or `--json-file <path>` | When a decision is made. Merges, so a later step can add to a section. |
| `step --id <phase> --status failed --note "<what stopped it>"` | Before surfacing any unrecoverable STOP. Best-effort like the rest: if it fails, still stop. |
| `phone --stage building\|screens\|qr` | Moves the plan's phone through its three stages: the building animation, the screen carousel at Step 6.75, and the Metro QR at Step 12. |

**Open the plan in the user's browser as soon as `init` creates it**, with the host's file opener
or `open` / `xdg-open` / `cmd /c start ""` on the quoted path. A plan the user never sees is a plan
that only ever existed as terminal text, and terminal text scrolls away during a long run. Opening
it once is enough: the page reloads itself as each phase lands.

**Phases** (`--id`): `requirements`, `architecture`, `data-model`, `screen-plan`, `scaffold`,
`design`, `auth`, `dataverse`, `capabilities`, `screens`, `run`. Any other id is rejected by name.

**Write each gated section into the plan *before* asking for approval**, with `--state proposed`,
then flip it to `--state approved` once the user says yes. The plan is already open in their
browser, so a rendered ER diagram or screen graph is what they answer the gate against. Recording
the decision afterwards means approving from terminal text and seeing the picture only once it is
too late to change it.

Step 3's Architecture gate lists every gate with its phase and section. The Gate 2 calls below are
the pattern each one follows:

```bash
DOCS="${PLUGIN_ROOT}/scripts/app-docs.js"
# 1. propose - the tab shows "Proposed - review this, then answer the prompt in your terminal"
node "$DOCS" --working-dir '<working_dir>' set --section dataModel --json-file <tmp>/data-model.json --state proposed
node "$DOCS" --working-dir '<working_dir>' step --id data-model --status active --note "Gate 2 — awaiting your approval"
# 2. point the user at that tab, then ask the gate question
# 3. on approval
node "$DOCS" --working-dir '<working_dir>' set --section dataModel --json-file <tmp>/data-model.json --state approved
node "$DOCS" --working-dir '<working_dir>' step --id data-model --status done --note "Gate 2 — approved"
```

Pass a fresh `--note` when you close a gate. A note belongs to the status it was written with, so
changing status without one clears it rather than leaving "awaiting your approval" under a step the
user has already approved.

If the user asks for changes, rewrite the section with `--state proposed` again and re-ask. The
page reloads itself, so they see the revision without touching anything.

**Record each other section as soon as it is known** — not in a batch at the end, or an abort
leaves a plan that explains nothing:

| Section | Written at | Contents |
|---|---|---|
| `environment` | Step 1, confirmed Step 4 | `displayName`, `environmentId`, `environmentUrl`, `tenantId`, `publisherPrefix` |
| `requirements` | Steps 2 and 2b | `appName`, `slug`, `platforms`, `aesthetic`, `industry`, `brief`, `features[]` |
| `architecture` | Gate 1 | `dataPlatform`, `nativeCapabilities[]` and `connectors[]` as `{ name, reason }` — a bare name cannot be approved or rejected, the reason is the decision |
| `dataModel` | Gate 2 | `connectorOnly` when there are no tables; otherwise `tables[]` with `logicalName`, `displayName`, `status` (`new`/`reused`/`modified`, or the plan's `create`/`reuse`/`extend`), `purpose` (what it holds), **`reason` (why it is created, reused or extended)**, `columns[]` (`logicalName`, `displayName`, `type`, `key: PK\|FK`), `relationships[]` (`relatedTable`, `name`, `cardinality`) |
| `offline` | Gate 2 | `enabled`, `mode`, `tables[]`, `rationale` |
| `screens` | Gates 3 and 4 | `navigation`, `list[]` with `name`, `route`, `purpose`, `data`, `capabilities[]` |
| `design` | Step 6.75 | `direction`, `headingFont`, `bodyFont`, `darkMode`, `palette[]` (`name`, `value` as `#rrggbb`). Use the real family names from [`typography-and-tone.md`](${PLUGIN_ROOT}/shared/references/typography-and-tone.md) (`Inter`, `JetBrains Mono`, …) — the plan renders each name in the typeface it names, so a label that is not a real family shows as the fallback. |
| `auth` | Step 7 | `status` |
| `trust` | Step 10, after capabilities and connectors are wired | `permissions[]` (`name`, `status` as one of the acquisition patterns below, **`detail`: when access is requested and why**), `handles[]`, `notCollected[]`, `battery[]` (`name`, `detail`) |

The ER diagram is **built from `dataModel.tables`**, so populate the structured fields rather
than only a Mermaid string — that is what makes the diagram, the column tables, and the
new/reused/extended colouring agree with each other.

**Every reuse, create and extend decision needs a `reason`.** "Reusing `account`" is not
reviewable; "Reusing `account` — sites are already maintained there by the service desk, and a
second Site table would drift" is. Write the reason the architect actually used, including what
was rejected, since that is what the user is being asked to approve at Gate 2.

**Use `--json-file` for anything the user wrote.** Size is not the test — an app called
`Dave's Rounds` is enough. A value interpolated into a single-quoted shell argument ends that
quoting at the first apostrophe, which at best corrupts the JSON and at worst leaves whatever
follows to the shell. App names, briefs, feature lists, table reasons and design directions are
all user-authored, so they are written to a file with the `Write` tool and passed by path.

`--json` inline is for values this skill controls end to end, such as a fixed status string.

**Every one of these calls is best-effort.** If `app-docs.js` fails, say nothing and carry on:
the documentation must never gate, retry, or fail a build.

**Mark the active creation phase `failed` before stopping on a creation error.**
Step 0 routing exits leave existing creation history untouched. Once creation is
underway, a STOP is a terminal state the user has to act on, and the plan is
probably still open in their browser. Without this the last
phase stays `active`, so the page keeps reloading and keeps reporting work in progress on a run
that ended. A failed phase settles the page, so the final thing it shows names what stopped.

**No argument switches this protocol off.** `--no-design` suppresses design previews and
`--no-preview` does not exist (Step 2c). The plan is how an abandoned or failed run explains
itself, so it is written on every creation path after Step 0's routing decision.

---

### Step 0 — Intent routing + creation-state gate

**Telemetry checkpoint: `validate_fresh_template`**

Before project reads or commands, bind the absolute `working_dir` through
[app-working-directory.md](../../shared/references/app-working-directory.md).
Apply the state-routing table above once. Classify the original request before
considering a creation resume or testing freshness; do not derive the requested
operation from the presence or absence of `memory-bank.md`.

**Existing-app changes leave this skill before resume handling.** Follow the
table's existing-app handoff, preserving the original request and root, then
return without entering Step 1 or modifying creation progress. Reported problems
and dependency/template upgrades retain their matching workflows. If intent or
customization is unclear, ask and wait; do not offer a creation resume merely
because a bank exists.

Only a confirmed **Fresh template** or explicit **Creation continuation**
enters the creation workflow below. All other table outcomes stop or hand off
without re-scaffolding, overwriting history, or running template preparation.

#### Fresh run

Require every condition in the Fresh-template row, including confirmed new-app
intent and an uncustomized starter. The template/dependency failure rows apply
only after that intent is established. An approved plan from the current
creation is allowed; preparation stays in Step 5, not this read-only entry gate.
Resolve the CLI below, then continue to Step 1.

#### Creation continuation

For an explicit request to continue this app's unfinished creation, inspect the
matching `memory-bank.md` when present, `docs/.run-plan.json` when present, the
approved creation plan, and any valid current create caller context. Establish
the unfinished creation phase and corroborate it against current app artifacts.
Do not infer the next phase from arbitrary completed entries belonging to edits,
deployment, or another workflow.

- **Missing or conflicting progress** — ask which creation phase is being
  continued or ask to restore its records, then stop. Do not guess a phase,
  restart creation, or classify the app as fresh.
- **Completed creation** — report that there is no unfinished creation to resume.
  Clarify whether the user wants an app edit or a separate new app and use the
  table's matching route.
- **Corrupt records** — report the error and ask to repair/restore them. Do not
  delete or overwrite history to make the directory pass a freshness check.
- **Verified unfinished phase** — show the phase, remaining scope, and target
  app, then obtain continuation approval unless that exact continuation already
  has current approval. Reuse only decisions approved for the same current
  scope; pending or changed decisions keep their owning approval gates.

Pre-initialization creation may have an approved plan and saved planning
progress without a memory bank. It stays on this continuation path when the
phase can be established; the plan's presence does not redirect it to editing.
For a post-initialization continuation, resume the verified phase directly; do
not rerun the fresh preparation or `pa app init` over the materialized app.

**Reopen the build plan only after continuation approval.** When
`<working_dir>/docs/create-app-plan.html` exists, reopen it. Inspect the existing
plan with the read-only
`node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' show`
command. If it reports a `failedPhase` matching the approved continuation,
reopen that phase before continuing:

```bash
cd -- '<working_dir>' || { echo "BLOCKED: cannot enter working_dir" >&2; exit 1; }
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id <failedPhase> --status active
```

Use only a validated phase ID from the recorded creation state. Resolve the CLI
below before entering the verified phase; do not skip unresolved approval or
validation gates merely because an older plan exists.

**Do not silently copy a bundled template over the user's folder.** A fresh `plugins/mobile-apps/template` template may contain placeholder `power.config.json` with an empty `environmentId`; Step 5 removes that placeholder immediately before Step 6 runs `pa app init`.

**Resolve the Power Apps CLI** (fresh run or resume) before any CLI command — see [cli-binary.md](${PLUGIN_ROOT}/shared/cli-binary.md). Run every command as `$PA …` (`npx --no-install pa …`), never a bare `pa`; on `power-apps`-only projects, translate per its mapping tables. If `PA_KIND=none`, STOP and ask the user to run `npm install` in the template folder. Record the result in the memory bank's `CLI Binary` row once the bank exists (Step 6.7).

### Step 1 — Prerequisites

**Telemetry checkpoint: `validate_development_toolchain`**

Run all checks first — no point gathering requirements if the toolchain isn't ready.

**Important: npm auth and Power Platform app auth are separate.** The account used for `npm install` can be different from the account used by the Power Apps CLI (`$PA`):

| What | Uses | Typical account |
|---|---|---|
| `npm install` private feed access | npm/Azure Artifacts auth configured outside this skill | Account with feed Reader access |
| `pa app init`, Dataverse, deploy | Power Apps CLI browser auth + `az login --tenant <env-tenant>` for Dataverse helper scripts | Power Platform environment account, often a test-tenant/admin account |

Renewing npm feed auth does not sign the user into the Power Apps CLI. If the Power Apps CLI prompts for browser auth later, that is expected and unrelated to the npm/ADO feed token.

Then run the checks:

```bash
node --version                                      # v22+
npm  --version                                      # v10+
az account show --query "user.name" -o tsv          # Azure CLI logged in (needed for Dataverse helper scripts)
git --version                                       # optional
```

**Do NOT probe Xcode, Java, Android Studio, or CocoaPods here.** This plugin's flow is plan → scaffold → code → local Expo dev server. Build + deploy (`npm run build` / `pa app push`) is a separate user-driven step via the `/deploy` skill. Local native compile is the user's choice and lives outside this skill (run the platform-specific native command directly when needed). See [`shared/version-check.md`](${PLUGIN_ROOT}/shared/version-check.md) — only the **Always required** tier matters here.

| Missing | Action |
|---|---|
| Node < 22 | STOP — instruct `nvm install 22 && nvm use 22` |
| `az` | STOP — instruct `az login` |

Template-only rule: this skill no longer provisions npm feed tokens, PAT fallbacks, vendor fallbacks, or registry rewrites. The user must run `npm install` in the fresh template folder before invoking `/create-mobile-app`.

Capture target Power Platform environment for the remaining flow.

**Source of truth for env selection: the generated `power.config.json` first, explicit environment ID second.** In the normal template-folder flow, `pa app init` runs first and writes the selected environment ID into `power.config.json`; read that ID and pass it to `scripts/resolve-environment.js` to resolve the Dataverse URL and tenant. If `power.config.json` is missing or has an empty placeholder `environmentId`, ask for an environment ID. A Dataverse URL is useful as a resolver fallback for existing apps, but it is not enough for `pa app init` because init needs `--environment-id`.

| Step | Source | When user is asked |
|---|---|---|
| 0. `power.config.json` has `environmentId` | `scripts/resolve-environment.js <environment-id>` | Never — automatic after `pa app init` |
| 1. User supplies env ID | `scripts/resolve-environment.js <environment-id>` | Ask only if `power.config.json` is missing/empty or user wants a different env |
| 2. User wants a different account | Follow shared-instructions standalone CLI auth handling | Only if resolution/token acquisition fails or user asks |
| 3. User wants different env | Ask for another env ID and re-run resolver | Only if user selects "use a different environment" at Step 2 |
| 4. `$PA app init -t MobileApp --display-name "$DISPLAY_NAME" --environment-id $ACTIVE_ENV_ID --non-interactive` | Persists choice into `power.config.json` | Only when this skill owns the initial init path |

```bash
TARGET_ENV="<environment-id-or-empty>"
if [ -z "$TARGET_ENV" ] && [ -f power.config.json ]; then
  TARGET_ENV=$(node -e "try { const id=require('./power.config.json').environmentId || ''; console.log(id); } catch { console.log(''); }")
fi
test -n "$TARGET_ENV" || { echo "✗ Environment missing. Provide an environment ID."; exit 2; }
ENV_JSON=$(node "${PLUGIN_ROOT}/scripts/resolve-environment.js" "$TARGET_ENV")
printf '%s\n' "$ENV_JSON" > .resolved-environment.json
ACTIVE_ENV_ID=$(node -e "const j=JSON.parse(process.argv[1]); console.log(j.environmentId || '')" "$ENV_JSON")
ACTIVE_ENV_NAME=$(node -e "const j=JSON.parse(process.argv[1]); console.log(j.displayName || j.environmentUrl || '')" "$ENV_JSON")
ACTIVE_ENV_URL=$(node -e "const j=JSON.parse(process.argv[1]); console.log(j.environmentUrl || '')" "$ENV_JSON")
ACTIVE_TENANT_ID=$(node -e "const j=JSON.parse(process.argv[1]); console.log(j.tenantId || '')" "$ENV_JSON")
test -n "$ACTIVE_ENV_ID" || { echo "✗ Environment ID missing. Provide the environment ID directly."; exit 2; }
echo "✓ Target env: $ACTIVE_ENV_NAME ($ACTIVE_ENV_ID)"
echo "✓ Target env URL: $ACTIVE_ENV_URL"
echo "✓ Target tenant: ${ACTIVE_TENANT_ID:-unknown}"
```

**Orchestrator handling for `exit 2`:** ask the user for their environment ID directly, then re-run the capture block above. Do not run `pa app init` here; Step 6 owns initialization after the user confirms the target environment.

Stash `$ACTIVE_ENV_ID`, `$ACTIVE_ENV_NAME`, `$ACTIVE_ENV_URL`, and `$ACTIVE_TENANT_ID` for Step 2 (env confirmation), Step 6 (`pa app init`), and Step 7 (`auth.config.json` tenant/environment cache). If parsing fails, ask for an environment ID again.

If `resolve-environment.js` cannot get tokens, run `az login --tenant <env-tenant>` in the foreground. If `pa app init` later uses the wrong account, follow shared-instructions standalone CLI auth handling and retry once.

### Step 1.7 — Detect publisher prefix

Detect the publisher prefix for the env's Default solution so the planner uses the correct prefix rather than assuming `cr_`.

**Deferred execution:** do not run the query at this point. The architecture
gate first selects `required` or `connector-only`; only `required` runs execute
the block below. Connector-only runs set
`$DETECTED_PUBLISHER_PREFIX = ""` and make no Dataverse prefix query.

```bash
PUBLISHER_PREFIX_JSON=$(node "${PLUGIN_ROOT}/scripts/detect-publisher-prefix.js" \
  "$ACTIVE_ENV_URL" --tenant-id "$ACTIVE_TENANT_ID")
echo "$PUBLISHER_PREFIX_JSON"
```

Output is one line of JSON, e.g.:

```json
{"prefix": "cr8142a", "source": "detected"}
{"prefix": null, "reason": "no token (run `az login --tenant <env-tenant>`)"}
```

The script queries the Default solution's publisher via:
`/api/data/v9.2/solutions?$select=uniquename&$expand=publisherid($select=customizationprefix)&$filter=uniquename eq 'Default'`

A second solution name can be passed as a second argument if the env uses a different solution (defaults to `'Default'`).

**Token tenant note:** the script's `getAuthToken` discovers the env's tenant ID from the Dataverse HTTPS auth challenge and passes `--tenant <env-tenant>` to `az`, so detection works even when the active az identity is on a DIFFERENT tenant. If the user has not run `az login --tenant <env-tenant>` at any point, detection may return null.

**Stash the result for Step 3 (planner spawn):**

| Output | Stash as | Behavior at Step 3 |
|---|---|---|
| `{"prefix": "cr8142a", ...}` | `$DETECTED_PUBLISHER_PREFIX = "cr8142a"` | Pass to planner prompt as a fact: *"Publisher prefix (detected from env): `cr8142a_`"* |
| `{"prefix": null, ...}` | `$DETECTED_PUBLISHER_PREFIX = ""` (empty) | Pass to planner as: *"Publisher prefix: NOT DETECTED — use placeholder `cr_` and warn the user that Dataverse will normalize the actual prefix at create time."* |

Do NOT block on null detection — the user can still proceed; the Power Apps CLI normalizes prefixes when `pa app add data-source` runs. The detection step is purely to make the plan output accurate.

If the script exits non-zero (rare — should always exit 0 with `prefix: null`), treat it as the null case and continue.

### Step 2 — Gather requirements

**Telemetry checkpoint: `gather_app_requirements`**

Skip questions the user already answered in `$ARGUMENTS`.

If the user gave no description, ask one open-ended question first:

> "What would you like to build? Describe it in your own words — what it does, who uses it, and what problem it solves."

Then collect with `AskUserQuestion` (batch where possible):

| Question | Default |
|---|---|
| App display name | derived from description |
| Aesthetic | minimal / playful / professional / matches existing brand |
| Target environment | Confirm `<ACTIVE_ENV_URL>` / `<ACTIVE_ENV_ID>` from Step 1.6, or choose "use a different environment" and provide another environment ID |

Set `<target_platforms> = "ios, android"` for every new app. Do not ask the
user to choose between iOS and Android; the mobile template targets both and
downstream planning must retain platform-specific behavior for each.

**App slug is auto-derived** from the display name (`slugify(displayName)` — kebab-case, ASCII-only, strip non-alphanumerics). Do NOT ask the user; the derived slug is correct >95% of the time. Show the resolved slug as part of Step 2c's plan preview so the user can override via `edit` if needed.

**Environment override branch:** If the user picks "use a different environment", ask for the Power Platform environment ID via `AskUserQuestion`, then run `scripts/resolve-environment.js <id> --no-cache` again and refresh `$ACTIVE_ENV_ID` / `$ACTIVE_ENV_URL` / `$ACTIVE_TENANT_ID`. Do not persist the selection before Step 2c approval.

**App-name collision pre-flight.** Once `<displayName>` is fixed, check the chosen env for a name collision:

```bash
$PA app list --environment-id "$ACTIVE_ENV_ID" --json 2>/dev/null | grep -F "<displayName>" >/dev/null && \
  echo "COLLISION" || echo "OK"
```

If `COLLISION`, ask the user via `AskUserQuestion`:
> "An app named `<displayName>` already exists in `<ACTIVE_ENV_NAME>`. Choose:
>  1. Pick a different name (recommended)
>  2. Delete the existing app in Maker portal — DESTRUCTIVE, asks confirmation outside this skill
>  3. Continue anyway (bg `pa app init` will fail; you'll have to rename later — NOT recommended)"

Re-prompt for name if (1). If (2), send the user to Maker portal to delete the existing app, then re-run the collision check. Only proceed once collision is resolved.

If `pa app list` is unavailable in the installed CLI version, skip the pre-flight silently and continue.

Don't enter plan mode here — that's the planner agent's job in Step 3.

**Application Insights requirement normalization:** If the user's description asks for Application Insights, telemetry, app analytics, diagnostics, traces, or monitoring of this generated app, note it for a post-creation `/setup-app-insights` run (Application Insights is no longer configured during creation) and explicitly tell the planner that it is host/runtime configuration, not a data connector or planning constraint. The planner must not propose the Azure Application Insights connector, a custom telemetry connector, telemetry tables, or telemetry screens unless the user separately asked to build an in-app analytics dashboard. If the user names specific custom events (for example `OrderSubmitted` or `InspectionCompleted`), preserve those event names and their approved scalar properties in the corresponding per-screen specs so screen builders can emit them through `getCustomEventsLogger()`; do not convert them into connectors or data-model artifacts.

### Step 2b — Requirements discovery

Create the plan now that the display name is known, and open the first phase. Write
`<tmp>/plan-init.json` with the `Write` tool first — the display name is the user's own text, and
a `"`, a backtick or `$(…)` in it would break out of a shell argument before Node saw it:

```json
{"appName":"<displayName>","dataPlatform":"unknown"}
```


```bash
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' init --json-file <tmp>/plan-init.json
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id requirements --status active --note "Waiting for your answers to the setup questions"
```

The note is what raises the waiting-for-input banner on the plan, so the user can tell from the page
that the run is blocked on them rather than still working. **Set one whenever you are about to ask a
question and the answer gates the run.** It clears itself on the next status change, so there is
nothing to remember to take down.

> **Goal:** Turn the user's thin prompt into a confirmed feature brief before the planner runs. The planner agent receives this brief verbatim — richer input means better data model inference, accurate connector detection, and correct screen specs.

#### Step 2b.0 — Prompt richness scoring (decides which path to take)

Before asking anything, score the description on four signals. The score decides whether we ask a multi-select feature picker, a single confirmation, or skip the discovery question entirely.

Run this scorer mentally on `<description>` (the prompt the user gave with `/create-mobile-app`, plus any clarifying text from Step 2). Count how many of the four trip:

| Signal | Trips when |
|---|---|
| **Word count** | description has ≥ 60 words |
| **Distinct nouns** | description names ≥ 5 distinct domain nouns (people, things, documents, places — e.g. "inspector", "aircraft", "gate", "defect", "evidence") |
| **Action verbs** | description uses ≥ 3 workflow verbs from this set: log, track, submit, assign, notify, scan, upload, approve, verify, complete, capture, override, dispatch, review, sign |
| **Domain phrase** | description names a known industry domain — match against the industry table in [`shared/references/universal-patterns.md`](../../shared/references/universal-patterns.md) (airline, hospital, retail, manufacturing, field-service, finance, logistics, …) OR explicitly says "field operations" / "ground operations" / "site visit" / similar |

Tier the result:

| Score | Tier | What to do |
|---|---|---|
| **4 / 4** | `auto-plan` | **Skip both questions.** Extract the brief silently from `<description>`, write `native-app-plan.md` placeholder, fall through to Step 2c. The user's next interaction is the cost-estimate gate. |
| **3 / 4** | `one-tap` | **Skip the multi-select.** Extract the brief, show it once, ask only "Look right? (yes / adjust)". On `yes` → Step 2c. On `adjust` → fall through to walk-through. |
| **≤ 2 / 4** | `walk-through` | **Current behaviour.** Run the multi-select feature picker described in Step 2b.1, then the brief confirmation. |

Print the chosen tier so the user knows which path is running:

> "→ Prompt richness: 4/4 — skipping discovery questions, extracting brief and going straight to the plan-cost preview." *(or 3/4 / ≤2/4 with the matching path name)*

`--full-discovery` escape hatch: if `$ARGUMENTS` contains `--full-discovery`, force `walk-through` regardless of score. Use this in dogfood runs where you want to exercise the multi-select path.

`--no-discovery` escape hatch: if `$ARGUMENTS` contains `--no-discovery`, force `auto-plan` regardless of score. Use this for fully-headless runs from the wrapper templates repo.

#### Step 2b.1 — Walk-through path (only when tier = `walk-through`)

Read [`references/requirements-discovery.md`](${PLUGIN_ROOT}/skills/create-mobile-app/references/requirements-discovery.md). Infer context-aware options from the user's description, ask exactly one structured `AskUserQuestion`, and never use markdown checkboxes in the question text.

Wait for the user's response. Summarize their answers into a **requirements brief** — 4–8 bullet points covering what users can do, what data is tracked, and integrations.

Confirm once:
> "Here's the brief I'll use for planning:
> • *(bullet 1)*
> • *(bullet 2)*
> ...
> Look right? (yes / adjust)"

Store the confirmed brief as `<requirements_brief>`. This replaces the thin `$ARGUMENTS` as the primary input to the planner.

#### Step 2b.2 — One-tap path (tier = `one-tap`)

Skip the multi-select question. Extract a 6–10 bullet brief directly from `<description>` covering: user roles, key entities, primary workflow, severity / status enums if present, integrations / connectors, native capabilities, and any explicit constraints. Show it with a single confirm:

> "Your description is detailed enough to skip the feature picker. Here's the brief I extracted:
> • *(bullet 1)*
> • *(bullet 2)*
> ...
> Look right? (yes / adjust / start over)"

- `yes` → store as `<requirements_brief>`, fall through to Step 2c.
- `adjust` → drop to Step 2b.1 (walk-through) so the user can edit via the multi-select.
- `start over` → return to Step 2 and re-prompt for the description.

#### Step 2b.3 — Auto-plan path (tier = `auto-plan`)

Skip both the multi-select AND the brief confirmation. Extract the brief silently and store it as `<requirements_brief>`. Print it as a transparency log only:

> "→ Auto-plan tier (4/4). Extracted brief from your description:
> • *(bullet 1)*
> • *(bullet 2)*
> ...
> → Going straight to the plan-cost preview (Step 2c). The brief above is locked in unless you abort there."

Do not ask for confirmation here — the user agreed to this when their prompt scored 4/4. The plan-preview gate at Step 2c remains in force as the last cheap exit before any side effects.

#### Step 2b.4 — Common to all paths

**Auto-proceed after `yes` (or after auto-plan transparency log).** Fall through directly to Step 2c (plan preview). Do NOT add a separate "Proceed to planning?" prompt — the brief confirmation IS the planning go-ahead. The only abort gate after this is Step 2c's `proceed/edit/abort` block, which is intentionally distinct because it shows the rough cost estimate.

Infer a provisional Dataverse planning mode before Step 2c:

- `connector-only` only when every record source and write target is an
  explicit non-Dataverse connector/system of record, and the app needs no
  app-owned persistent rows, retained File/Image
  artifact, existing Dataverse table, or Dataverse-backed native capability.
- `required` for every other case, including ambiguity. Do not infer
  connector-only merely because the brief names a connector.

Apply
[`shared/references/connectivity-intent-ownership.md`](${PLUGIN_ROOT}/shared/references/connectivity-intent-ownership.md)
while classifying the brief.

Store the recommendation as `<recommended_dataverse_planning_mode>`. The first
user-visible data-platform choice happens in the planner's architecture gate
after native capabilities and connectors have been presented. Dataverse
discovery does not run before that gate.

Also stash `<exact_target_facts_required> = yes` when planning depends on any
existing/standard/managed table, reuse or extension decision, proposed-name
collision decision, relationship target, computed column, or target
customizability fact. Otherwise set it to `no`. This flag controls only safe
planning degradation; it never relaxes `/add-dataverse` reconciliation.

**Design decisions are deferred to Step 6.75** — `/design-system` (ships with this plugin) handles brand inputs, the style picker, and visual companion preference in one flow after the project is scaffolded. Do NOT ask design questions here.

Set tentative defaults (the preview preference applies at Step 6.75):

- `<visual_companion> = yes` — render `_plan_preview.html` at Step 6.75, after the design choice. **Nothing is opened in a browser during a create run**, whatever this is set to: the build plan shows the same screens in its phone frame and links out to the file, and every opener in `/design-system` and its style picker is gated on `CODE_APPS_NATIVE_ORCHESTRATING=1`, which this skill passes. The flag governs `/preview-screens` wherever it runs — including the Step 11.4 offer, which is a browser window the user asked for by picking (a) or (b), not one that appears on its own — and a later `/edit-app` re-plan. Gate 4 remains markdown-only regardless. `/design-system` may change it to `no`; persist the final value to memory-bank for future runs.
- `<design_vibe_opt_in> = deferred` — Step 6.75 sets the real value. While `deferred`, the planner does NOT prompt for a direction; it writes a placeholder `## Design Direction: <deferred — set by /design-system>` block so screen-planner can still run.

**`--no-design` escape hatch.** For headless / token-constrained runs, set `--no-design` in `$ARGUMENTS`. It forces `<visual_companion> = no`, skips the style-picker handoff at Step 3a entirely, and short-circuits Step 6.75 to a no-op (placeholder block stays in `native-app-plan.md`; screen-builders fall back to industry-inferred defaults).

**Record the confirmed brief.** The phase stays open: Step 2c still asks the user to approve the
plan preview, and a closed phase cannot report that the run is waiting on them.

Write `<tmp>/requirements.json` with the `Write` tool first — every value here came from the user,
and none of it may go through the shell:

```json
{"appName":"<displayName>","slug":"<slug>","platforms":"iOS and Android","aesthetic":"<aesthetic>","industry":"<industry>","brief":"<confirmed brief>","features":["<feature>"]}
```

```bash
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' set --section requirements --json-file <tmp>/requirements.json
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id requirements --status active --note "Brief confirmed"
```

### Step 2c — Plan preview (rough, always shown)

> **Goal:** Give the user a cheap exit before any mutation happens. This is the **last point** before the run touches the app itself — no `pa app init`, no Dataverse writes, no agent tokens spent on planning. The one thing already written is the build plan under `docs/`, which Step 2b created so the user has something to read while answering; an abort here leaves that folder and nothing else. After Step 3 starts, every abort gets more expensive (half-written `native-app-plan.md`, partial `_screens_section.md`, architect tokens already burnt).

**Always runs. There is no `--no-preview` flag in v0** — we need calibration data (~10+ runs with recorded estimate-vs-actual) before we can trust the rough estimates enough to let users skip them. Once the data shows estimates are reliably within ±50%, evaluate adding a skip flag for repeat-user workflows.

**Compute the estimates from inputs already in hand** (no agent spawn — pure heuristics on the confirmed brief and the wizard answers):

| Output | Input proxy | Computation | Confidence |
|---|---|---|---|
| Tables | Distinct nouns in confirmed brief | `count(unique_nouns) × [0.7, 1.3]` rounded | low — architect may merge or split |
| Connectors | Connector keywords in the confirmed brief | `len(candidates)` | low — Gate 1 confirms the actual list |
| Screens | Confirmed features in brief | `count(features) × [2, 3]` | low — depends on navigation choice |
| Planning min | Tables + screens | lower bound `max(10, tables × 0.3 + screens × 0.4 + 2)`; upper bound `max(15, computed upper)` | low — protects the quality-first Gate 2 data-model budget |
| Scaffold min | Fixed | `1-2` (template preparation + npm install already happened before skill invocation) | high |
| Build min | Screens, parallel cap of 5 | `ceil(screens / 5) × 0.6` | medium |
| Extra prompts | `<industry_confidence>` + `<design_vibe_opt_in>` | `+1 if low-confidence industry; +1 if vibe-opt-in == yes` | high |

Print the block once, exactly in this format (substitute computed values; ranges as `low-high`):

```
─── Plan preview (rough) ─────────────────────────────────
Based on your confirmed brief, before any agent runs:

Scope (proxy estimates — actual numbers come from architects):
  Tables       ~<low>-<high>      ← from <N> nouns in brief; architect may merge/split
  Connectors    <N> inferred      ← <comma-separated names>  (confirm at Gate 1)
  Screens     ~<low>-<high>       ← from <N> features × ~2-3 screens each
  Approval gates  4               ← architecture, data model when applicable, screen graph, screen specs

Time (rough — agent time only, excludes your approval latency at gates):
  Planning      ~<low>-<high> min ← includes the quality-first 10–15 min data-model target; approvals add latency
  Scaffolding   ~1-2 min          ← validates prepared template + runs power-apps init
  Screen build  ~<low>-<high> min ← parallel, capped at 5 concurrent

Token tier: Opus everywhere in v0 (model routing not yet shipped).

⚠ These are proxies, not measurements:
  • Table count is "noun count in brief" — architect may collapse or split
  • Time excludes your approval latency at up to 4 applicable gates
  • If industry inference is low-confidence, +1 picker prompt
  • If you opted into the design vibe picker, +1 prompt + planner re-spawn
  • If any gate is rejected, that section regenerates (~2-3 min each)

Proceed, edit brief, or abort? [proceed/edit/abort]
─────────────────────────────────────────────────────────
```

**Three-option exit:**

| User answer | Action |
|---|---|
| `proceed` (or empty / Enter) | Continue to Step 3. Default. |
| `edit` | Jump back to Step 2b. Re-confirm the brief with the user's changes. After 2b re-confirms, return here for a fresh preview. **No working dir mutations** — Step 2c runs before `mkdir -p <working_dir>` in Step 3. |
| `abort` | Print `"Aborted at Step 2c. No files created. Re-run /create-mobile-app when ready."` and exit cleanly. No working dir, no memory bank, no scaffold. |

**Why "always show" is correct in v0** (do not skip without explicit user request):
- Cost when user proceeds: ~30s (read + decide). Token cost ~500/run = ~$0.008.
- Cost when user aborts late (after Step 3 starts): 5-10 min + dirty working dir + frustration.
- Asymmetry: bounded 30s vs unbounded 30 min. Always show the bounded cost.
- Forced calibration: every run produces the `<estimate, actual>` data we need for v0.x model routing decisions. Skipping drops calibration data.

**Set expectations before handing off to the planner:**
Raise the wait before showing the estimate, and close the phase once the user approves:

```bash
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id requirements --status active --note "Waiting for you to approve the plan preview"
# ... after they approve ...
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id requirements --status done
```

> "Brief locked in. Planning surfaces up to 4 approval prompts (data platform + native capabilities + connectors → data model when Dataverse is selected → screen graph → screen specs). Data-model readiness is quality-first, with a 10–15 minute target:
>  • Gate 1 (architecture) — confirm Dataverse choice, native capabilities, and connectors before schema work
>  • Gate 2 (data model, Dataverse only) — budget 10–15 min for verified reuse/extend/create decisions, ER columns, relationships, tiers, and risks; auto-skipped for no-Dataverse apps
>  • Gate 3 (screen graph) — approve destinations, navigation, and shared conventions
>  • Gate 4 (screen specs) — **3–8 minutes** for detailed screen contracts
>
> For Dataverse-required apps, factual foreground milestones will show
> environment, inventory, candidate, detail, and timing counts within 30
> seconds of starting metadata work after Gate 1 approval. Connector-only apps
> skip those metadata milestones. While the
> architect runs, new milestone IDs from
> `.tmp/data-model-planning-status.json` are rendered without inventing
> percentages. If Gate 2 has not surfaced after 15 minutes of data-model planning, inspect the last
> applicable milestone before interrupting."

### Step 2d — Template-only mode

No background scaffold pipeline is used. The template is already present in `<working_dir>` and dependencies are expected to be installed before this skill starts (`npm install`). Continue directly to Step 3.

### Step 3 — Plan (planner agent + up to 4 applicable approval gates)

**Telemetry checkpoint: `plan_app_architecture`**

First, create the working and planning-artifact directories:

```bash
mkdir -p <working_dir> <working_dir>/.tmp
```


### Architecture gate

Planning is now the live phase. Open it with what the run is doing rather than with a question:
between gates the agents work for minutes, and the plan must not tell the user the run is waiting
on them while it is not.

```bash
node "${PLUGIN_ROOT}/scripts/app-docs.js" --working-dir '<working_dir>' step --id architecture --status active --note "Planning the architecture"
```

**Raise every gate in the plan as you present it, and take it down as soon as it is answered** -
here, in the inline fallback below, and in every revision loop. The plan is open in the user's
browser, and its banner is how they can tell the run is waiting on them rather than still working.

| Gate | `--id` | Section written first, `--state proposed` | Once approved, open next |
|---|---|---|---|
| 1 - architecture | `architecture` | `architecture` | `data-model`, note "Designing the data model" (`connector-only`: `screen-plan`, note "Planning the screens") |
| 2 - data model, `required` only | `data-model` | `dataModel`, and `offline` when the plan has one | `screen-plan`, note "Planning the screens" |
| 3 - screen graph | `screen-plan` | `screens` | `screen-plan`, note "Writing the screen specs" |
| 4 - screen specs | `screen-plan` | `screens` | nothing - Step 3.9 closes the phase |

```bash
DOCS="${PLUGIN_ROOT}/scripts/app-docs.js"
# Just before presenting gate <N>:
node "$DOCS" --working-dir '<working_dir>' set --section <section> --json-file <tmp>/<section>.json --state proposed
node "$DOCS" --working-dir '<working_dir>' step --id <id> --status active --note "Gate <N> — awaiting your approval"
# As soon as the user approves it:
node "$DOCS" --working-dir '<working_dir>' set --section <section> --json-file <tmp>/<section>.json --state approved
node "$DOCS" --working-dir '<working_dir>' step --id <next id> --status active --note "<note from the table>"
```

Flip each section to `approved` when its gate is answered, not later in the Step 3.9 batch. A
section left `proposed` while the next phase works reads as a question nobody is asking. The
architecture-only planner pass does nothing but present Gate 1, so raise Gate 1 before dispatching
it. When the planner's completion pass presents Gates 2-4 itself, the plan cannot see inside it and
keeps its progress note until the result comes back.

Run the planner once in architecture-only mode before any Dataverse discovery:

```
Spawn agent: mobile-app:native-app-planner

Prompt:
  Plan and approve architecture inputs for a Power Apps mobile app.
  Architecture phase: gate-only
  Requirements brief: <requirements_brief>
  Original prompt: <full $ARGUMENTS verbatim>
  Wizard answers: <Step 2 answers>
  Target platforms: iOS and Android
  Recommended Dataverse planning mode: <recommended_dataverse_planning_mode>
  Working directory: <absolute path>
  Plugin root: ${PLUGIN_ROOT}
  Dataverse planning snapshot: NOT SUPPLIED
  Dataverse planning evidence: NOT SUPPLIED
```

The planner presents native capabilities, then connectors, then the
data-platform choice in Gate 1. On approval it writes
`<working_dir>/.tmp/approved-architecture.md` and returns exactly
`NEEDS_CONTEXT: dataverse-planning-mode:<required|connector-only>`.

Treat that signal as the successful architecture result, set
`<dataverse_planning_mode>` only after the architecture-result checks in the
canonical status switch below pass, and continue. Any other terminal status
follows that switch. If the nested planner is unavailable, prepare and approve
all three architecture decisions inline: native capabilities, connectors, and
the data-platform choice. Use the planner's Gate 1 dependency checks, write
the same approved artifact, and continue without adding another approval.

Now execute the deferred Step 1.7 publisher-prefix detection for `required`.
For `connector-only`, set `$DETECTED_PUBLISHER_PREFIX = ""`, print
`↷ Publisher-prefix discovery skipped — connector-only planning.`, and do not
call `detect-publisher-prefix.js`.

### Foreground Dataverse planning snapshot and evidence

This read-only helper recipe is also used by
[scoped Dataverse change planning](../../shared/references/dataverse-change-planning.md).
Those callers supply their own request scope and approvals; they do not run
the create wizard or dispatch its native-app planner.

Apply [app-working-directory.md](../../shared/references/app-working-directory.md)
before this recipe. Each shell block below and in Dataverse planning recovery
re-enters the owner's absolute root. Re-supply the selected identity and artifact
paths on every call; fresh-shell retries re-execute the complete guarded
attempt block, including its variable assignments and function definition.

Planning stays read-only. The resolver's `--no-cache` mode may read existing
identity metadata, but must not persist the environment cache, auth settings,
or telemetry cluster, and must not replay pending telemetry. Normal resolution
in Step 4 retains those post-approval behaviors.

Branch on the Gate 1-approved
`<dataverse_planning_mode>`:

- `connector-only` — skip every command in this section. Set `SNAPSHOT_PATH`
  and `ARCHITECT_EVIDENCE_PATH` to empty/not supplied, print
  `↷ Foreground planning snapshot skipped — the confirmed brief is connector-only.`, and
  continue to planner dispatch. Connector-only planning does not perform
  Dataverse metadata reads; the skill's existing global prerequisites remain
  unchanged.
- `required` — resolve the already selected environment again in the
  foreground and create one normalized foreground planning snapshot as below. Do not make the
  nested planner or architect rediscover the tenant.

```bash
cd -- '<working_dir>' || { echo "BLOCKED: cannot enter working_dir" >&2; exit 1; }
node "${PLUGIN_ROOT}/scripts/resolve-environment.js" "$ACTIVE_ENV_ID" --no-cache --require-tenant
```

The command validates both required fields before returning JSON. On exit `0`,
use its `environmentUrl` as `$ACTIVE_ENV_URL` and `tenantId` as
`$ACTIVE_TENANT_ID` in the following commands, replacing the previous values.
Read these fields directly from the structured command result; do not run
separate JSON-extraction commands. On a nonzero exit, follow foreground
recovery below and do not create a snapshot or reuse stale environment values.

Build `<working_dir>/.tmp/dataverse-concepts.json` as a JSON array of typed
concepts from the confirmed brief and Gate 1-approved architecture. Account for
approved native capture/storage targets. Discover only app-owned Dataverse
records or explicitly approved Dataverse projections; exclude connector-owned
records from Dataverse candidate selection. Each item has `phrase`, `kind`,
`discoverTable`, and a short `evidence` quote. Use `kind: entity` and
`discoverTable: true` only for a plausible persistent business record with an
independent lifecycle; classify people/actors as `role`, fields as `attribute`,
workflow verbs as `action`, enum values as `status`, and operating limits as
`constraint`, all with `discoverTable: false`. Preserve multiword/header-child
families such as `medical assessments`, `care activities`, `release events`,
`custody transfers`, `test results`, and `evidence attachments`. Do not turn
every noun into an entity merely to increase recall. Add known standard or
required-existing logical names to `<EXPLICIT_TABLES>`. Build
`<PROPOSED_TABLES>` from the detected publisher prefix for every clearly
proposed custom table so collisions and missing names are explicit; leave a
name out rather than inventing it when the concept is not yet stable.

Detailed advisory discovery is quality-bounded:

- Keep the complete customizable-table inventory and ranking.
- Required exact-name tables are always detailed and do not consume advisory
  capacity.
- A concept credibly covered by an exact table does not receive speculative
  advisory alternatives.
- Every typed entity concept receives its primary candidate and at most one
  ambiguity candidate. Roles, attributes, actions, statuses, and constraints
  never trigger table discovery.
- A lower-ranked proposed-name collision is promoted only when its display
  phrase strongly matches a multiword entity concept.
- Strong exact/suffix/contains matches and explicit/collision candidates load
  full details. Weak advisory candidates load `core` details and cannot
  authorize Reuse, Extend, or Adapt until bounded expansion upgrades them.
- Inventory-only alternatives remain available for bounded exact-name
  expansion, at most once per newly selected logical name.

```bash
cd -- '<working_dir>' || { echo "BLOCKED: cannot enter working_dir" >&2; exit 1; }
SNAPSHOT_PATH='<working_dir>/.tmp/dataverse-foreground-planning-snapshot.json'
CONCEPTS_PATH='<working_dir>/.tmp/dataverse-concepts.json'
ARCHITECT_EVIDENCE_PATH='<working_dir>/.tmp/dataverse-architect-evidence.json'
INVENTORY_CACHE_PATH='<working_dir>/.tmp/dataverse-inventory-cache.json'

run_dataverse_planning_attempt() {
if ! node "${PLUGIN_ROOT}/scripts/create-dataverse-snapshot.js" \
  --env-url "$ACTIVE_ENV_URL" \
  --tenant-id "$ACTIVE_TENANT_ID" \
  --output "$SNAPSHOT_PATH" \
  --concepts-file "$CONCEPTS_PATH" \
  --tables "<EXPLICIT_TABLES>" \
  --proposed-tables "<PROPOSED_TABLES>" \
  --progressive-detail \
  --combined-base-read \
  --read-concurrency 1 \
  --inventory-cache "$INVENTORY_CACHE_PATH"; then
  printf 'NEEDS_RECOVERY: dataverse-snapshot\n' >&2
  return 2
fi

if ! node "${PLUGIN_ROOT}/scripts/render-dataverse-architect-evidence.js" \
  --snapshot "$SNAPSHOT_PATH" \
  --output "$ARCHITECT_EVIDENCE_PATH" \
  || ! node "${PLUGIN_ROOT}/scripts/render-dataverse-architect-evidence.js" \
    --snapshot "$SNAPSHOT_PATH" --output "$ARCHITECT_EVIDENCE_PATH" --validate-only; then
  printf 'NEEDS_RECOVERY: dataverse-evidence\n' >&2
  return 2
fi

node -e '
  const s=require(process.argv[1]);
  const t=s.timings;
  const d=s.detailLoadSummary;
  console.log(`✓ Dataverse inventory: ${s.inventoryFacts.customizableTables} customizable + ${s.inventoryFacts.exactNameTables} bounded exact-name discoveries (${s.inventoryFacts.requiredExactNameTables} required, ${s.inventoryFacts.proposedCollisionTables} proposed collisions) (${t.inventoryRetrievalMs} ms)`);
  console.log(`✓ Candidate selection: ${s.candidateRanking.length} concepts → ${d.attemptedCandidates} detailed (${d.primaryCandidates || 0} primary, ${d.ambiguityCandidates || 0} ambiguity, ${d.strongCollisionCandidates || 0} strong collision, ${d.deferredCandidates || 0} deferred; ${t.candidateSelectionMs} ms)`);
  console.log(`✓ Detail loading: ${d.loadedCandidates} loaded (${d.coreCandidates || 0} core, ${d.fullCandidates || 0} full), ${d.failedCandidates} failed; ${s.tables.reduce((n,x)=>n+x.facts.columnCount,0)} columns, ${s.tables.reduce((n,x)=>n+x.facts.relationshipCount,0)} relationships, ${s.tables.reduce((n,x)=>n+x.facts.keyCount,0)} keys (${t.detailLoadingMs} ms)`);
  console.log(`✓ Exact names: requested [${s.exactNameResolution.requestedTables.join(", ")}], loaded [${s.exactNameResolution.loadedTables.join(", ")}], unavailable [${s.exactNameResolution.unavailableTables.join(", ")}]`);
  console.log(`✓ Proposed names: ${s.proposedNameChecks.collisions.length} collisions, ${s.proposedNameChecks.missing.length} missing; foreground planning snapshot total ${t.totalDurationMs} ms`);
' "$SNAPSHOT_PATH" || return 2
echo "✓ Compact architect evidence: $ARCHITECT_EVIDENCE_PATH"
}
run_dataverse_planning_attempt
```

`--combined-base-read` loads attributes, three relationship collections, and
alternate keys through one entity-definition GET per selected table, following
any nested continuation links before normalization. Typed constraints, choices,
lookup targets, and computed metadata remain separate full-detail GETs.
`--read-concurrency 1` is the production default. Concurrency `2` through `8`
is an explicit operator choice only; metadata writes are never sent
through this read worker pool. The inventory cache stores inventory-level facts
only, has a five-minute TTL, fails open on corruption or identity mismatch, and
is never read by `--reconcile-exact`. After any metadata publish, invalidate it
with `dataverse-inventory-cache.js --file "$INVENTORY_CACHE_PATH" --invalidate`.

**Foreground recovery, not agent termination.** A nonzero command result or
`NEEDS_RECOVERY` returns control to the foreground agent. Keep working on the
failure; only dependent summaries, architect dispatch, approval, and mutation
wait for trustworthy evidence. This also applies to environment resolution,
token acquisition, transport access, broad inventory, exact-name identity
resolution, parsing, and evidence validation failures in this step.

- Diagnose the actual error, then repair recoverable project-local command,
  path, or concept-input mistakes. Let the request executor handle its bounded
  transient retries; do not launch parallel retry loops or repeat an unchanged
  permanent error. Invalid cached or old-format metadata is regenerated from
  live reads, never edited into apparent validity.
- After a targeted repair, rerun `run_dataverse_planning_attempt` (or
  rerun the block with that invocation if the shell function is unavailable).
  Allow at most two repair-and-retry attempts per failing stage. Re-resolve the
  selected environment first if resolution failed.
- Resume automatically only after the current attempt succeeds and its fresh
  snapshot and matching evidence validate. File existence is not success:
  prior artifacts can survive a failed atomic write. Never substitute stale
  evidence, invent metadata, change the selected account/environment, relax
  validation, or downgrade a requirement to make the check pass.
- Ask the user only when recovery needs interactive sign-in, missing privileges,
  an explicit scope decision, or the bounded repairs are exhausted. Preserve
  the working context and resume this stage after that blocker is resolved;
  a recoverable command failure alone is not terminal `BLOCKED`.
- Individual table-detail failures do not stop planning. Keep the snapshot,
  record each failure in `detailLoadFailures`, list it in the evidence
  appendix, and classify the affected exact or advisory table as `Defer` at
  Gate 2 unless the user chooses a materially different business design.
- `Defer` is non-executable. Step 8 still requires fresh complete evidence for
  every approved `Reuse`, `Extend`, `Create`, or `Adapt` operation; unresolved
  metadata never authorizes a write.
- `--proposed-tables` performs collision checks only. Missing proposed names
  are not required-table failures and must never be selected for detail loading
  solely because they were proposed.

The snapshot script emits factual `DATAVERSE_SNAPSHOT_PROGRESS` lines after
inventory, candidate selection, and detail loading. Print them immediately;
never replace them with estimates or percentages. Once the architect starts,
watch `<working_dir>/.tmp/data-model-planning-status.json` and print each new
milestone ID once with its counts and elapsed time. The first environment or
snapshot milestone must be visible within 30 seconds. The foreground
orchestrator owns this rendering; the architect only owns the status artifact.

For `required`, pass `SNAPSHOT_PATH` and `ARCHITECT_EVIDENCE_PATH` verbatim to
the planner prompt and every direct `data-model-architect` fallback/revision.
The model reads the compact sidecar, not the full snapshot; deterministic tools
retain the full snapshot for hash binding and validation. A supplied matching
pair activates the architect's `snapshot-only` path: no Bash discovery and no
live Dataverse calls inside the agent. For `connector-only`, pass the mode
explicitly and state that both paths are not supplied; never provide placeholder
file paths.

#### Planner completion dispatch

**Hard rule — planner writes are restricted during Step 3.** The planner (and any sub-agents it spawns) is permitted to write to **only**:

- `<working_dir>/native-app-plan.md`
- `<working_dir>/_screens_section.md`
- `<working_dir>/.tmp/*`

All other paths in `<working_dir>/` (notably `app/`, `src/`, `package.json`, `power.config.json`, `tamagui.config.ts`, `tsconfig.json`, `node_modules/`, `memory-bank.md`) are owned by the foreground setup phases. Do not mutate them during planning.

If the planner needs to record a `DONE_WITH_CONCERNS` from a sub-agent (data-model architect, scree