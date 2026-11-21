<!--
Source: microsoft/power-platform-skills
Path: plugins/power-pages/skills/create-site/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-pages/skills/create-site/SKILL.md
-->

> **Plugin check**: Run `node "${PLUGIN_ROOT}/scripts/check-version.js"` — if it outputs a message, show it to the user before proceeding.

# Create Power Pages Code Site

Guide the user through creating a complete, production-quality Power Pages code site from initial concept to deployed site. Follow a systematic approach: discover requirements, scaffold and launch immediately, plan components and design, implement with design applied, validate, review, and deploy.

## Core Principles

- **Design for the first impression**: The site must make the user say "wow" the moment the Home page replaces the scaffold, and keep earning it on every page. Once the user answers the aesthetic, mood, and brand questions, make confident decisions about fonts, colors, layouts, imagery, and motion yourself - the design references (`design-aesthetics.md`, `page-blueprints.md`, `design-critique.md` under `${PLUGIN_ROOT}/references/`) define the bar, and your own taste fills the rest.
- **Use TaskCreate/TaskUpdate**: Track all progress throughout all phases — create the path-agnostic upfront tasks first, then append branch-specific tasks after the creation path is selected.
- **Scaffold early, design with intention**: Get the dev server running immediately after discovery so the user has something to look at. Then plan the design and features while the scaffold is live — apply the chosen aesthetic during implementation.
- **Live preview feedback loop**: The dev server MUST be running before any customization begins. Browse the site via Playwright (`browser_navigate` + `browser_snapshot`) to verify the structure of every significant change. Visual design review captures every page with one script call and opens the screenshots in one turn (see [5.7](#57-design-critique-pass)); use Playwright MCP screenshots only for brand extraction in Phase 3.
- **Keep the scaffold loader in sync with reality**: The scaffold loader polls `public/scaffold-status.json`. Update this file before every `AskUserQuestion` (to raise the "waiting for your input" banner so the user doesn't miss a terminal prompt) and before each implementation step in Phase 5 (so the progress-bar label matches what you're actually doing while the decorative spinner continues its default cycle). See [Live Preview Status Protocol](#live-preview-status-protocol).
- **Use purposeful visuals**: Every image explains, orients, demonstrates, or reinforces identity. Prefer the site's own UI composed as a product moment, then bespoke inline SVG, then specific Unsplash photography with one art direction (see [5.3](#53-source-purposeful-visuals)). Never leave image placeholders or broken `<img>` tags pointing to nonexistent files.
- **Git checkpoints**: Commit after every individual page and component — each gets its own commit so breaking changes can be reverted.
- **Site content language is independent of Dataverse language**: Generate every user-visible SPA string in the approved content language. Dataverse and Power Pages platform-managed messages remain English (`en-US`, LCID `1033`) in this workflow.
- **Bidirectional by default**: Read `${PLUGIN_ROOT}/references/bidirectional-design.md`. Every site must use direction-neutral layout, mixed-content boundaries, and script-capable typography even when its initial language has only one direction. A later LTR/RTL locale must not require a general redesign.
- **Lifecycle-ready output**: Follow `${PLUGIN_ROOT}/references/site-modification-integrity.md` so later pages and components can preserve localization, direction, and content-expansion behavior through the shared post-modification validator.

**Constraint**: Only static SPA frameworks are supported (React, Vue, Angular, Astro). NOT supported: Next.js, Nuxt.js, Remix, SvelteKit, Liquid.

**Initial request:** $ARGUMENTS

---

## Live Preview Status Protocol

<!-- not-a-gate: prose-only section — mentions of `AskUserQuestion` here describe the live-status protocol that wraps every real prompt in Phases 3/4/8; the actual gates are catalogued in §6.13 of references/approval-gates.md and marked at their call sites below -->

While the scaffold loading screen is visible (from Phase 2.6 until the Home page itself is replaced in Phase 5), the loader polls `GET /scaffold-status.json` every 1.5 seconds. The `message` you write into `<PROJECT_ROOT>/public/scaffold-status.json` appears as the label under the progress bar, and `awaitingInput` controls the "waiting for your input" banner. The decorative spinner above the progress bar continues its built-in phrase cycle; keep the progress-bar label current so the loader still reflects what is actually happening.

**Why this matters**: When the browser with the loader takes over the user's screen, a prompt in the terminal can sit unanswered for a long time because the user doesn't realize anything is waiting. The banner makes it obvious.

**File shape** (all fields optional — omit any field you don't want to change):

```json
{
  "message": "Creating Contact page",
  "awaitingInput": false,
  "inputPrompt": "Please check your terminal to respond."
}
```

- `message` — one short present-participle phrase shown as the status line under the progress bar in the loader (replacing the default "Getting started…" / "Setting up infrastructure…" cycle). Include the grouping context inline when it helps (e.g., `"Creating Footer component (shared components)"`).
- `awaitingInput` — when `true`, a prominent pulsing banner appears at the top of the loader and stays visible until this field is cleared. Set this **before** every `AskUserQuestion` call and clear it (`false`) **immediately after** the user answers.
- `inputPrompt` — short context for the banner (e.g., `"Choose a framework"`). Optional.

**When to update the file**:

1. **After scaffold launches (end of Phase 2)**: write an initial status like `{ "message": "Planning your site", "awaitingInput": false }`.
2. **Before any `AskUserQuestion` that runs while the scaffold is visible** (Phases 3, 4, and any in-scaffold prompt in Phase 5): set `awaitingInput: true` with a short `inputPrompt`. After the user answers, write again with `awaitingInput: false`.
3. **Before each implementation step in Phase 5** — applying design tokens, creating each shared component, creating each page, updating the router, updating navigation — update `message` to the specific action. Examples: `"Applying design tokens"`, `"Creating Navbar component"`, `"Creating Contact page"`.
4. **At the end of Phase 5, after the Home page has been replaced**: delete `public/scaffold-status.json` so it isn't deployed with the site.

Write the file with the `Write` tool (atomic overwrite). You do not need to read it first.

---

## Phase 1: Discovery

**Goal**: Understand what site needs to be built and what problem it solves

**Actions**:

<!-- gate: create-site:1.purpose | category=plan | cancel-leaves=nothing -->

> 🚦 **Gate (plan · create-site:1.purpose):** Path-agnostic discovery prompt collecting site name, purpose, and audience. Determines what kind of site the user needs before the skill decides between a template-backed path and the from-scratch scaffold path. Fires only on the "site purpose unclear" branch (step 3 below).
>
> **Trigger:** Phase 1 when site name, purpose, or audience was not provided in `$ARGUMENTS`.
> **Why we ask:** Wrong purpose/audience context → wrong branch decision and wrong generated site plan; cleanup is annoying.
> **Cancel leaves:** Nothing — no scaffolding has started yet.

1. Create the minimal upfront todo list (see [Progress Tracking](#progress-tracking)):
   - Discover site requirements
   - Select template or choose from-scratch
2. If site name, purpose, and audience are clear from arguments:
   - Summarize understanding
   - Identify site type (portal, dashboard, landing page, blog, etc.)
3. If site name, purpose, or audience is unclear, use `AskUserQuestion`:

   | Question | Header | Options |
   |----------|--------|---------|
   | What should the site be called? (e.g., "Contoso Portal", "HR Dashboard") | Site Name | *(free text — use a single generic option so the user types a custom name via "Other")* |
   | What is the site's purpose? | Purpose | Company Portal, Blog/Content, Dashboard, Landing Page |
   | Who is the target audience? | Audience | Internal (employees, partners), External (public-facing customers) |

4. From the user's answers, derive:
   - `__SITE_NAME__` (Title Case, e.g., `Contoso Portal`)
   - A preliminary `__SITE_SLUG__` (lowercase ASCII kebab-case derived from
     the site name when possible)
5. Summarize the path-agnostic understanding and confirm with user before proceeding:
   - Site name
   - Site purpose/type
   - Target audience

   Do **not** ask for framework or project location in Phase 1. Each creation path asks for its location after Phase 1.5 selects that path.

**Audience influences site generation:**

- **Internal**: Prioritize task completion - status, the next action, data tables, dashboards, authentication, navigation depth. The wow comes from clarity, density done right, and polish in the details rather than marketing heroes.
- **External**: Prioritize a striking first impression, a persuasive page narrative with proof at the point of doubt, SEO-friendly structure, and contact forms.

**Output**: Clear statement of site purpose, audience, site name, and preliminary technical slug.

---

## Phase 1.5: Template Branch Decision

**Goal**: Route the user into the appropriate creation path after path-agnostic Discovery.

> **Current implementation state:** Template discovery, selection, supporting-solution import, packaged SPA cloning/upload, optional seed data, activation, live-site preview, and terminal telemetry are implemented for `kind: "spa"`. Traditional catalog entries are accepted but not shown until their solution-only provisioning flow is implemented. The user can always choose **Start from scratch** to continue into the existing scaffold flow.

**Actions**:

1. Mark **Select template or choose from-scratch** as `in_progress`.

2. Fetch the template catalog:

   ```bash
   node "${PLUGIN_ROOT}/scripts/fetch-template-catalog.js"
   ```
   Use the returned immutable commit SHA for every template artifact request in this run. Pass `--ref <tag-or-branch>` only for a deliberate test or rollback.

   Evaluate the JSON result:
   - **If `ok: false`**: tell the user templates are temporarily unavailable and continue with the from-scratch path. This is additive; a catalog failure must never block `create-site`.
   - **If `ok: true` but `selectableCatalog.templates` is empty**: tell the user no supported SPA templates are currently available and continue with the from-scratch path. Do not offer entries from `catalog.templates` whose `kind` is `traditional`.
   - **If supported SPA templates are available**: use `selectableCatalog.templates` for every matching, preview, browse, and selection step below. Keep `catalog.templates` only as the complete downloaded manifest.

3. Semantically match the template families against the Phase 1 context (`$ARGUMENTS`, site name, purpose, audience, and any framework mentioned by the user):
   - Use each family template's `displayName`, `description`, `keywords`, `audience`, available variant frameworks, and any variant-specific previews.
   - Do **not** compute a numeric score or invent a ranking script. Keywords guide agent judgement; they are not counted.
   - Treat a template family as the user-facing template and a framework variant as the installable package. A family can have multiple variants (`react`, `vue`, `angular`, `astro`).

<!-- not-a-gate: read-only route selection after semantic matching; no project directory, Dataverse write, or durable skill state exists yet -->

4. Ask the user how to proceed after semantic matching:

   | Match situation | Question | Header | Options |
   |-----------------|----------|--------|---------|
   | One or more clear matches | I found matching template(s) for your site. What would you like to do? | Creation Path | Show matching templates (Recommended), Browse all templates, Create from scratch |
   | No clear match | I couldn't find a matching template for your site. What would you like to do? | Creation Path | Browse all templates, Create from scratch (Recommended) |

   Branch on the answer:
   - **Show matching templates**: set `TEMPLATE_PREVIEW_FAMILIES` to the matched family or families and continue to browser preview.
   - **Browse all templates**: set `TEMPLATE_PREVIEW_FAMILIES` to all entries in `selectableCatalog.templates` and continue to browser preview.
   - **Create from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions.

5. Render `TEMPLATE_PREVIEW_FAMILIES` for browser preview:

   - Download each `previewImages` artifact into the SHA-keyed cache before rendering:
     ```bash
     node "${PLUGIN_ROOT}/scripts/fetch-template-artifact.js" --sha "<catalog-sha>" --artifactPath "<preview-image-path>"
     ```
     Replace each preview image path with the returned `localUrl`. If a preview download returns `ok: false`, omit that one image from the gallery and continue; a missing preview should not block using an otherwise-valid template.
   - Write a temporary JSON file containing a `TEMPLATES_JSON` array with the template families the user should preview and the cached preview image URLs. Include each family's available variants so the browser can show framework badges/tabs, but keep the browser read-only.
   - Run:
     ```bash
     node "${PLUGIN_ROOT}/scripts/render-template-browser.js" --templatesJsonPath "<temp-json>" --outputPath "<temp-html>" --open
     ```
     Evaluate the JSON result before telling the user to use the browser:
     - **`status: "ok"`**: continue; the browser preview was generated and opened.
     - **`status: "invalid"`**: do not continue with template selection. Surface the validation errors, rebuild the `TEMPLATES_JSON` file from the matched families, and rerun the render. If it is still invalid, fall back to from-scratch rather than showing an empty or incomplete template browser.
     - **`status: "ok"` with `opened: false`**: the HTML validated but the browser could not be opened automatically. Show the file path and continue with terminal selection only after the user has had a chance to open it manually.
   - The browser view is read-only and exists to browse template capabilities and available framework variants; the terminal `AskUserQuestion` remains the decision surface.

<!-- not-a-gate: read-only route selection after template preview; only disposable temp preview files exist, with no project directory, Dataverse write, or durable skill state -->

6. Ask one of the following `AskUserQuestion` prompts:

   | Match situation | Prompt options |
   |-----------------|----------------|
   | One strong family + framework match | Use `<displayName>` - `<framework>` (Recommended), Choose another framework, See all templates, Start from scratch |
   | One strong family but no framework match | One option per available framework, See all templates, Start from scratch |
   | Several plausible family matches | One option per shortlisted family, See all templates, Start from scratch |
   | Full catalog browse | One option per family, Start from scratch |

   When the user chooses **See all templates** from a matching-template branch, set `TEMPLATE_PREVIEW_FAMILIES` to all entries in `selectableCatalog.templates`, render the family-first SPA template gallery, and ask again with the **Full catalog browse** options. If the selected family has multiple variants, ask a second terminal question for the framework.

7. Branch on the user's selection:
   - **Template family and framework variant selected**:
     1. Set `SELECTED_TEMPLATE` to the family entry and `SELECTED_TEMPLATE_VARIANT` to the exact framework variant. Resolve the selected template assets once from the pinned catalog SHA. The result combines the family's supporting solutions with the selected variant's website code:
        ```bash
        node "${PLUGIN_ROOT}/scripts/fetch-template-variant.js" \
          --sha "<catalog-sha>" \
          --catalogPath "<catalogPath from fetch-template-catalog.js>" \
          --kind "<SELECTED_TEMPLATE.kind>" \
          --templateId "<SELECTED_TEMPLATE.id>" \
          --variant "<SELECTED_TEMPLATE_VARIANT.variantKey>" \
          --solutionSettingsJson '<JSON.stringify(SELECTED_TEMPLATE.solutions || [])>'
        ```
        Use the returned `websiteCodePath` and `solutions`. Process `solutions` in the returned order; do not rediscover, reorder, or revalidate the template assets in the skill.
     2. If the result is `ok: false`, tell the user the selected framework variant is unavailable or invalid. If the same family has other available framework variants, offer those first; otherwise offer **Start from scratch** or **Stop**. Do not emit `template_used` for a variant whose package did not validate. If the user falls back to from-scratch, recommend the framework they had selected.
     3. If the result is `ok: true`, set `CREATION_PATH = "template"`, `SELECTED_TEMPLATE_SOLUTIONS = <result.solutions>`, and `SELECTED_TEMPLATE_WEBSITE_CODE = <result.websiteCodePath>`. Run the `template_used` telemetry command silently (fail-closed), then append the template pre-install tasks now (see [Progress Tracking](#progress-tracking)); append the execution tasks after the reinstall policy is known. Do **not** proceed to Phase 2.
        Do not mention this telemetry command to the user and do not print its output.
        ```bash
        node "${PLUGIN_ROOT}/scripts/emit-create-site-template-outcome.js" \
          --eventName template_used \
          --templateId "<SELECTED_TEMPLATE.id>" \
          --templateKind "<SELECTED_TEMPLATE.kind>" \
          --framework "<SELECTED_TEMPLATE_VARIANT.framework>" \
          --audience "<internal|external from Phase 1 discovery>"
        ```
        `--audience` is the site audience captured in Phase 1 (`internal` or `external`), **not** the template's `audience` persona array from the catalog manifest. Do not include site name, URL, subdomain, free-text purpose, or any other user-identifying value.
     4. Mark **Choose local template directory** as `in_progress`.

<!-- not-a-gate: read-only location selection for the later template clone; no directory is created and no environment change has started -->

        Ask where to create the local template project:

        | Question | Header | Options |
        |----------|--------|---------|
        | Where should I create the template site's local files? | Project Location | Current directory, New folder in current directory (Recommended), Any other directory |

        Resolve the clone destination:
        - **Current directory**: use `<cwd>`.
        - **New folder in current directory**: use `<cwd>/<__SITE_SLUG__>/`.
        - **Any other directory**: ask for a full path and resolve it to an absolute path.

        The destination must not exist or must be empty. Check it without creating it. If it is non-empty, ask the user to choose another directory. Store the resolved path as `TEMPLATE_CLONE_OUTPUT_DIRECTORY`, confirm "The template project will be created under `<resolved path>`.", and mark **Choose local template directory** as `completed`.
   - **Start from scratch** or catalog unavailable: set `CREATION_PATH = "from-scratch"` and continue below.

8. For the template path only:

   1. Mark **Resolve target environment** as `in_progress`.
   2. Resolve the target environment via the shared auth helpers:
      ```bash
      node "${PLUGIN_ROOT}/scripts/resolve-template-import-context.js"
      ```
      Use the returned `environmentUrl` for the remaining template workflow. The PAC import wrapper uses the current PAC authentication with this explicit target URL; Dataverse preflight and seed helpers acquire Azure tokens internally so credentials are never returned in JSON or carried between tasks. If `ok: false`, surface the error and stop before import. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.

<!-- gate: create-site:1.5.confirm-environment | category=consent | cancel-leaves=template-cache -->

> 🚦 **Gate (consent · create-site:1.5.confirm-environment):** Confirm the resolved target environment before running template install preflights.
>
> **Trigger:** Phase 1.5 after `resolve-template-import-context.js` returns `environmentUrl` and before any CLI-tenant, `.js` unblock, language, solution import, seed, or activation step.
> **Why we ask:** PAC auth can point at a different Dataverse environment than the user intended. Even preflights can inspect or modify environment-level settings, so the skill must not continue silently.
> **Cancel leaves:** `template-cache` — template catalog assets, the discovered solution sources, and website code may already be cached locally; no org mutation has happened if cancelled here.

   3. Mark **Resolve target environment** as `completed` and **Confirm target environment** as `in_progress`, then ask the user to confirm the resolved target environment before any environment preflight:

      | Question | Header | Options |
      |----------|--------|---------|
      | Use **`<environmentUrl>`** as the target environment for this template install? | Confirm Environment | Yes, use this environment (Recommended), No, start from scratch, Cancel |

      - **Yes**: continue to the preflight checks below.
      - **No, start from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions. Do not run environment preflights or import the template.
      - **Cancel**: stop before environment preflights. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.

   4. Mark **Confirm target environment** as `completed` and **Validate CLI tenant alignment** as `in_progress`, then confirm PAC CLI and Azure CLI are authenticated to the same tenant before any import preflight that depends on both CLIs:
      ```bash
      node "${PLUGIN_ROOT}/scripts/validate-cli-tenant-alignment.js" --envUrl "<environmentUrl>"
      ```
      - **`ok: true`**: mark **Validate CLI tenant alignment** as `completed`.
      - **`ok: false`**: surface the error and the `pacTenantId`, `azTenantId`, and `tokenTenantId` fields when present. Stop before import and tell the user to switch either PAC auth or Azure CLI to the same tenant, then rerun the skill. Do not emit `template_import_failure` because no import was attempted.
   5. Mark both **Validate JavaScript unblock requirement** and **Validate Dataverse language requirements** as `in_progress`. Run the `.js` blocked-attachment dry run and the Dataverse language check in parallel because both are read-only preflights against the confirmed target environment:
      ```bash
      node "${PLUGIN_ROOT}/scripts/lib/fix-blocked-attachments.js" --envUrl "<environmentUrl>" --extensions js --dry-run --quiet
      node "${PLUGIN_ROOT}/scripts/check-available-languages.js" --envUrl "<environmentUrl>" --requiredLocaleIds "<SELECTED_TEMPLATE_VARIANT.requiredDataverseLanguages or SELECTED_TEMPLATE.requiredDataverseLanguages comma-separated>"
      ```
      Capture both JSON results before deciding what to do next. Do not mutate `blockedattachments` until the language check has also completed; if the language check blocks import, route to the language-requirement question without changing attachment settings.
      Evaluate the `.js` result:
      - **`wasBlocked` does not include `js`**: mark **Validate JavaScript unblock requirement** as `completed`; JavaScript attachments are allowed.
      - **`wasBlocked` includes `js`**: keep **Validate JavaScript unblock requirement** as `in_progress`; this may need the `.js` unblock consent gate below, but handle language failures first because they block import without any environment mutation.
      Evaluate the language result:
      - **`ok: true` and `hasRequiredLanguages: true`**: mark **Validate Dataverse language requirements** as `completed`; the target environment has every LCID required by the selected variant, falling back to the family requirements when the variant does not override them.
      - **`ok: false`**: tell the user the skill could not verify available Dataverse languages, surface the script error, then ask whether to switch to from-scratch or stop. Do not import the template and do not mutate `blockedattachments`.
      - **`ok: true` and `hasRequiredLanguages: false`**: explain that the selected template requires the missing Dataverse language LCIDs from `missingLocaleIds`. Block before any solution import mutation. Do not provide a "proceed anyway" branch and do not mutate `blockedattachments`.

<!-- gate: create-site:1.5.unblock-js | category=consent | cancel-leaves=attachment-block-modified -->

> 🚦 **Gate (consent · create-site:1.5.unblock-js):** Preflight unblock of `.js` from the target environment's `blockedattachments` setting before uploading website code.
>
> **Trigger:** Phase 1.5 when `fix-blocked-attachments.js --dry-run --extensions js` reports `.js` is blocked in the target environment.
> **Why we ask:** The packaged SPA is uploaded with `pac pages upload-code-site` after its supporting solutions are ready. That upload includes JavaScript files and fails when `.js` is blocked. Checking before solution import avoids leaving supporting artifacts behind when the site cannot be created.
> **Cancel leaves:** `attachment-block-modified` is possible only if the user approved and the update partially completed. Pure Cancel here leaves the original `blockedattachments` value untouched and no template installation has started.

      Use `AskUserQuestion`:

      | Question | Header | Options |
      |----------|--------|---------|
      | This environment currently blocks `.js` attachments. The template site's code upload will fail unless `.js` is unblocked. Remove only `js` from `blockedattachments` now? | Unblock JavaScript | Yes, unblock `.js` and continue (Recommended), No, start from scratch, Cancel |

      - **Yes**: run the helper without `--dry-run`, preserving every other blocked extension:
        ```bash
        node "${PLUGIN_ROOT}/scripts/lib/fix-blocked-attachments.js" --envUrl "<environmentUrl>" --extensions js --quiet
        ```
        Confirm the JSON result has `removed` containing `js` or `changed: true`, then rerun the dry-run check and continue only if `.js` is no longer blocked.
      - **No, start from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions. Do not import the template.
      - **Cancel**: stop before template installation. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.

<!-- not-a-gate: route selection after a blocking preflight; no org mutation has happened and there is no option to override missing template language requirements -->

    6. Use `AskUserQuestion` only when the language preflight cannot continue:

       | Question | Header | Options |
       |----------|--------|---------|
       | This template requires Dataverse language LCID(s) `<requiredLocaleIds>`, but the target environment is missing `<missingLocaleIds>` or the language check could not be completed. What would you like to do? | Template Language Requirement | Start from scratch (Recommended), Cancel |

       - **Start from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions. Do not import the template.
       - **Cancel**: stop before template installation. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.

<!-- gate: create-site:1.5.template-import | category=consent | cancel-leaves=template-cache -->

> 🚦 **Gate (consent · create-site:1.5.template-import):** Confirm installing the selected template in the current Power Platform environment.
>
> **Trigger:** Phase 1.5 after the template variant and its unpacked solutions are downloaded and the target environment is resolved.
> **Why we ask:** The install can import unmanaged supporting solutions and create a new code site. Choosing the wrong environment or template is disruptive and cannot be cleanly undone.
> **Cancel leaves:** `template-cache` — the discovered solution sources and preview images may remain in the private SHA-keyed template cache; no org mutation has occurred.

   7. If the language preflight passed but `.js` was blocked and the user approved/verification passed, mark **Validate JavaScript unblock requirement** as `completed`. Then mark **Confirm template install** as `in_progress`, present the template and environment, and ask:

      List any supporting solution whose manifest metadata has `publishChanges: true`.
      Explain that those solutions use `pac solution import --publish-changes` so imported forms, views, and other customizations become visible immediately after import.
      Disclose that PAC's publication step can also publish other pending customizations in the target environment.

      | Question | Header | Options |
      |----------|--------|---------|
      | Install **`<SELECTED_TEMPLATE.displayName>`** into **`<environmentUrl>`**? This imports any required unmanaged supporting solutions, runs PAC's publish-changes step for solutions that declare it in the template manifest, clones and builds the website code, and uploads the new code site. The publication step can include other pending customizations in this environment. Seed data is applied before activation when available. | Install Template | Yes, install this template (Recommended), No, start from scratch, Cancel |

      - **No, start from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions.
      - **Cancel**: stop; no org mutation has happened. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.
   8. Mark **Confirm template install** as `completed`. Initialize `TEMPLATE_SOLUTIONS_TO_IMPORT = []` and `TEMPLATE_SOLUTIONS_TO_SKIP = []`, then process every entry in `SELECTED_TEMPLATE_SOLUTIONS` in the returned order:
      ```bash
      node "${PLUGIN_ROOT}/scripts/inspect-template-solution.js" --solutionPath "<solution.solutionPath>"
      node "${PLUGIN_ROOT}/scripts/check-solution-installed.js" --solutionName "<solution.uniqueName>" --envUrl "<environmentUrl>"
      node "${PLUGIN_ROOT}/scripts/inspect-template-solution.js" --solutionPath "<solution.solutionPath>" --installed "<true|false>" --installedVersion "<version-or-empty>"
      ```
      Do not derive a website name from a solution or expect supporting-solution import to add a `pac pages list -v` row. If metadata inspection returns `ok: false` after variant validation, or `check-solution-installed.js` exits 1, treat that solution as `decision: "ask"` rather than assuming it is absent.
      - **`decision: "import"`**: append the solution to `TEMPLATE_SOLUTIONS_TO_IMPORT`.
      - **`decision: "confirm-update"`**: tell the user that this specific solution has a newer version available and confirm before adding it to `TEMPLATE_SOLUTIONS_TO_IMPORT`.

        <!-- gate: create-site:1.5.update-installed | category=consent | cancel-leaves=template-cache -->

        > 🚦 **Gate (consent · create-site:1.5.update-installed):** Confirm updating an already-installed unmanaged template solution. Repeat for each solution that needs an update.
        >
        > **Trigger:** Phase 1.5 when one of the selected template solutions is already installed and the downloaded source has a newer version.
        > **Loop behavior:** Fires once per matching entry in `SELECTED_TEMPLATE_SOLUTIONS`; three solutions needing updates require three confirmations.
        > **Why we ask:** Updating an unmanaged solution merges changes into the environment and cannot be cleanly rolled back.
        > **Cancel leaves:** `template-cache` — downloaded template artifacts remain in the private SHA-keyed template cache; no org mutation happens if cancelled.

        Use `AskUserQuestion`:

        | Question | Header | Options |
        |----------|--------|---------|
        | Solution `<solution.uniqueName>` is installed at version `<installedVersion>`, and the template contains `<availableVersion>`. Update it in this environment? | Update Solution | Yes, update this solution (Recommended), No, cancel |

        If the user declines or cancels, stop before import; no org mutation has happened. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.
        If the user confirms, append this solution to `TEMPLATE_SOLUTIONS_TO_IMPORT`.
      - **`decision: "offer-clone"`**: append the same-or-newer solution to `TEMPLATE_SOLUTIONS_TO_SKIP`.
      - **`decision: "ask"`** or detection failure: ask whether to import that solution anyway, start from scratch, or stop.

        <!-- gate: create-site:1.5.reinstall-unknown | category=consent | cancel-leaves=template-cache -->

        > 🚦 **Gate (consent · create-site:1.5.reinstall-unknown):** Confirm whether to import when installed-solution detection failed.
        >
        > **Trigger:** Phase 1.5 when `check-solution-installed.js` cannot determine whether one selected template solution already exists.
        > **Loop behavior:** Fires once per unknown entry in `SELECTED_TEMPLATE_SOLUTIONS`; an answer applies only to the named solution.
        > **Why we ask:** Importing an unmanaged solution that may already exist can merge components.
        > **Cancel leaves:** `template-cache` — downloaded template artifacts remain in the private SHA-keyed template cache; no org mutation happens if cancelled.

        Use `AskUserQuestion`:

        | Question | Header | Options |
        |----------|--------|---------|
        | I couldn't determine whether solution `<solution.uniqueName>` is installed. Importing it may merge unmanaged components. How would you like to proceed? | Solution Install Unknown | Import anyway (advanced), Start from scratch (Recommended), Stop |

        Branch on the answer:
        - **Import anyway**: append this solution to `TEMPLATE_SOLUTIONS_TO_IMPORT`.
        - **Start from scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions.
        - **Stop**: stop before import; no org mutation has happened. Do not emit `template_import_failure` because no import was attempted; `template_used` was already emitted when the template path was selected.

      Append the full site-install tasks.
      Include **Import template supporting solutions** only when `TEMPLATE_SOLUTIONS_TO_IMPORT` is non-empty.
      If every solution was skipped, ask once before creating the site:

      <!-- gate: create-site:1.5.clone-existing | category=consent | cancel-leaves=template-cache -->

      > 🚦 **Gate (consent · create-site:1.5.clone-existing):** Confirm creating a new site while reusing already-installed supporting solutions.
      >
      > **Trigger:** Phase 1.5 when every selected template solution is installed at the same or a newer version.
      > **Why we ask:** The skill will skip all solution imports but still clone and upload a new code site.
      > **Cancel leaves:** `template-cache` — downloaded template artifacts remain in the private SHA-keyed template cache; no site clone/upload happens if cancelled.

      | Question | Header | Options |
      |----------|--------|---------|
      | All supporting solutions are already installed at the same or newer versions. Create a new `<SELECTED_TEMPLATE_VARIANT.framework>` site from the website code? | Create Template Site | Yes, create the site (Recommended), No, cancel |

      If the user confirms, set `SKIP_TEMPLATE_SOLUTION_IMPORT = true`. If the user declines, stop. Do not emit an import result event because no solution import was attempted.

   6. Render and open a read-only status page:
      ```bash
      # Create <temp-import-status-dir>/ under the operating-system temporary directory,
      # then write this initial status JSON to <temp-import-status-dir>/status.json:
      # Import path:
      # Use "Installing solution" when TEMPLATE_SOLUTIONS_TO_IMPORT has one entry,
      # otherwise use "Installing solutions":
      # { "state": "running", "phase": "solution", "message": "<solution install label>" }
      # SKIP_TEMPLATE_SOLUTION_IMPORT path:
      # { "state": "running", "phase": "site", "message": "Preparing template site" }
      node "${PLUGIN_ROOT}/scripts/render-template-import-status.js" \
        --templateName "<SELECTED_TEMPLATE.displayName>" \
        --statusPath "<temp-import-status-dir>/status.json" \
        --previewImagesJson '<JSON array of SELECTED_TEMPLATE.previewImages localUrl values from the template browser step>' \
        --solutionCount "<TEMPLATE_SOLUTIONS_TO_IMPORT.length, or SELECTED_TEMPLATE_SOLUTIONS.length when imports are skipped>" \
        --outputPath "<temp-import-status-dir>/index.html"
      node "${PLUGIN_ROOT}/scripts/serve-static-dir.js" --root "<temp-import-status-dir>" --urlFile "<temp-import-status-dir>/url.txt" --cleanupRoot
      node "${PLUGIN_ROOT}/scripts/open-url.js" --url "<url from <temp-import-status-dir>/url.txt>"
      ```
      Reuse the already-downloaded local preview image URLs from the browser step; do not fetch preview images again for this page.
      The server command returns only after the listener is ready. It removes the temporary status directory after a successful redirect, after the browser stops polling, or when the maximum lifetime expires.
   7. Unless `SKIP_TEMPLATE_SOLUTION_IMPORT = true`, mark **Import template supporting solutions** as `in_progress`. Process `TEMPLATE_SOLUTIONS_TO_IMPORT` sequentially in its existing case-insensitive lexical unique-name order. For each entry, set `CURRENT_TEMPLATE_SOLUTION` and prepare its unmanaged solution for import:
      ```bash
      # Update the status JSON:
      # { "state": "running", "phase": "solution", "message": "Preparing solution <CURRENT_TEMPLATE_SOLUTION.uniqueName>" }
      node "${PLUGIN_ROOT}/scripts/pack-template-solution.js" \
        --solutionPath "<CURRENT_TEMPLATE_SOLUTION.solutionPath>"
      ```
      Set `PACKED_TEMPLATE_SOLUTION_ZIP = <result.zipPath>`, `PACKED_TEMPLATE_SOLUTION_WORK_DIRECTORY = <result.workDirectory>`, `PACKED_TEMPLATE_SOLUTION_CLEANUP_MARKER = <result.cleanupMarker>`, and `PACKED_TEMPLATE_SOLUTION_CLEANUP_TOKEN = <result.cleanupToken>` for the current solution. The packer creates the ZIP only in a token-owned OS temporary directory. Never write a packed ZIP into the downloaded template cache or another repository path.

      If packing fails, do not call Dataverse and do not emit `template_import_failure` because no import was attempted. The packer removes partial output automatically.

      <!-- gate: create-site:1.5.pack-failed | category=progress | cancel-leaves=template-cache -->

      > 🚦 **Gate (progress · create-site:1.5.pack-failed):** Choose how to proceed after preparing the local solution package fails.
      >
      > **Trigger:** Phase 1.5 when local validation or `pac solution pack` fails for one discovered solution.
      > **Loop behavior:** Fires per failed iteration of `TEMPLATE_SOLUTIONS_TO_IMPORT`; an answer applies only to `CURRENT_TEMPLATE_SOLUTION`.
      > **Why we ask:** No environment mutation has happened, but template installation cannot continue without a valid temporary solution ZIP.
      > **Cancel leaves:** `template-cache` — downloaded template source remains in the private SHA-keyed template cache; partial pack output has been removed.

      Use `AskUserQuestion`:

      | Question | Header | Options |
      |----------|--------|---------|
      | Solution `<CURRENT_TEMPLATE_SOLUTION.uniqueName>` could not be prepared for import. How would you like to proceed? | Template Pack Failed | Retry packing, Fall back to from-scratch (Recommended), Stop |

      - **Retry packing**: return to the pack command above.
      - **Fall back to from-scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions.
      - **Stop**: stop after showing the local pack error. Do not mark **Import template supporting solutions** as completed.

      After packing succeeds, import the temporary ZIP with the PAC CLI wrapper. Do not invoke `/import-solution` or write ALM artifacts.
      The solution-level `publishChanges` value comes from the pinned template manifest.
      The wrapper adds `pac solution import --publish-changes` only when that value is true:
      ```json
      { "state": "running", "phase": "solution", "message": "Importing solution <CURRENT_TEMPLATE_SOLUTION.uniqueName>" }
      ```
      Write that object to `<temp-import-status-dir>/status.json`, then run the import wrapper in a Task subagent with `run_in_background: true`:
      ```bash
      node "${PLUGIN_ROOT}/scripts/import-template-solution.js" \
        --zipPath "<PACKED_TEMPLATE_SOLUTION_ZIP>" \
        --envUrl "<environmentUrl>" \
        --publishChanges "<CURRENT_TEMPLATE_SOLUTION.publishChanges>"
      ```
      The Task must return the wrapper's complete JSON result.
      When it finishes, clean the owned temporary directory in the main conversation:
      ```bash
      node "${PLUGIN_ROOT}/scripts/pack-template-solution.js" \
        --cleanup \
        --workDirectory "<PACKED_TEMPLATE_SOLUTION_WORK_DIRECTORY>" \
        --cleanupMarker "<PACKED_TEMPLATE_SOLUTION_CLEANUP_MARKER>" \
        --cleanupToken "<PACKED_TEMPLATE_SOLUTION_CLEANUP_TOKEN>"
      ```
      Always run the owned temporary-directory cleanup after the import command returns.
      Treat a cleanup failure as a local warning and show the work-directory path so the user can remove it manually; do not hide the import result.
      Do not start the next solution import until the current command returns `ok: true`, and do not start site cloning until every solution in `TEMPLATE_SOLUTIONS_TO_IMPORT` succeeds.
      If the import wrapper returns `ok: false`, do **not** auto-clean up the unmanaged partial import.
      Run the `template_import_failure` telemetry command silently before asking the recovery question.
      Do not mention this telemetry command to the user and do not print its output:
      ```bash
      node "${PLUGIN_ROOT}/scripts/emit-create-site-template-outcome.js" \
        --eventName template_import_failure \
        --templateId "<SELECTED_TEMPLATE.id>" \
        --templateKind "<SELECTED_TEMPLATE.kind>" \
        --framework "<SELECTED_TEMPLATE_VARIANT.framework>" \
        --audience "<internal|external from Phase 1 discovery>" \
        --outcome failure \
        --errorClass "PacSolutionImport"
      ```
      `--audience` is the site audience captured in Phase 1 (`internal` or `external`), **not** the template's `audience` persona array from the catalog manifest. Do not add free-form error text to telemetry; it can contain paths, URLs, stack traces, or user data.

      <!-- gate: create-site:1.5.import-failed | category=progress | cancel-leaves=partial-unmanaged-template-import -->

      > 🚦 **Gate (progress · create-site:1.5.import-failed):** Choose how to proceed after template solution import fails.
      >
      > **Trigger:** Phase 1.5 when `pac solution import` fails or times out.
      > **Loop behavior:** Fires per failed iteration of `TEMPLATE_SOLUTIONS_TO_IMPORT`; an answer applies only to `CURRENT_TEMPLATE_SOLUTION`.
      > **Why we ask:** The environment may contain the current partial unmanaged import plus any earlier solutions imported during this run. A local timeout does not cancel an already-started server import, so retrying or switching paths should be an explicit choice.
      > **Cancel leaves:** `partial-unmanaged-template-import` — downloaded template artifacts remain in the private SHA-keyed template cache; the current partial import and any earlier successful solution imports remain in Dataverse and are explained in the error summary.

      Use `AskUserQuestion`:

      | Question | Header | Options |
      |----------|--------|---------|
      | Supporting solution `<CURRENT_TEMPLATE_SOLUTION.uniqueName>` failed or partially completed. How would you like to proceed? | Template Import Failed | Retry import, Fall back to from-scratch (Recommended), Stop |

      Branch on the answer:
      - **Retry import**: return to the pack-and-import command sequence above.
      - **Fall back to from-scratch**: set `CREATION_PATH = "from-scratch"` and continue to the deferred framework/location questions. Tell the user the unmanaged partial import may remain in Dataverse. The eventual from-scratch branch emits the single terminal telemetry event.
      - **Stop**: stop after showing the error summary. Do not mark **Import template supporting solutions** as completed and do not clone or upload the site. The `template_import_failure` event was already emitted when the import failure was detected.

      If the error is `AttachmentBlocked`, point to `/import-solution` Phase 5b remediation.
      Only continue to the next step when the import wrapper returns `ok: true`.
   8. When every required solution import succeeds, continue to seed planning and site provisioning.
      Mark **Import template supporting solutions** as `completed` and set `EMIT_TEMPLATE_IMPORT_SUCCESS = true` only after these checks.
      Defer that telemetry event until the seed-data workstream joins so `seedApplied` reflects this run.
      If every solution import was skipped, mark the task as skipped and set `EMIT_TEMPLATE_IMPORT_SUCCESS = false`.
   9. Prepare seed data in the main conversation before starting either mutating workstream.
      If seed data is present, fetch it:
      ```bash
      node "${PLUGIN_ROOT}/scripts/fetch-template-seed-data.js" --sha "<catalog-sha>" --seedDataPath "<SELECTED_TEMPLATE_VARIANT.seedDataPath or SELECTED_TEMPLATE.seedDataPath>"
      ```
      Save `localDir` and `seedFile` from a successful result as `TEMPLATE_SEED_DIRECTORY` and `TEMPLATE_SEED_FILE`.
      Inspect the source and target without inserting records:
      ```bash
      node "${PLUGIN_ROOT}/scripts/apply-seed-data.js" \
        --seedDir "<TEMPLATE_SEED_DIRECTORY>" \
        --seedFile "<TEMPLATE_SEED_FILE>" \
        --envUrl "<environmentUrl>" \
        --plan
      ```
      This reports table/record/file counts, financial entity sets, and active target currencies with `writes: 0`.
      <!-- not-a-gate: create-site:1.5.seed-currency — Read-only currency selection prepares already-approved optional seed records; it does not create currencies or mutate records. -->
      If `requiresCurrencySelection` is true, ask in the main conversation which existing active currency the financial sample rows should use.
      Show the available currency codes/names, explain that the numeric amounts are not converted, and offer **Skip sample data**.
      Never create a currency, copy a source-environment currency GUID, or silently assume USD.
      Save the selected ISO code as `TEMPLATE_SEED_CURRENCY_CODE`.
      If the user skips sample data, do not launch seed writes.
      If no financial rows are present, omit currency selection and the `--currencyCode` flag.
      Fetch/plan failures remain best-effort seed failures: show the explicit failure, retain it in the seed summary, and create the site without a seed task rather than pretending records were inserted.

      Start site provisioning and seed-data application concurrently only after successful imports and the main-conversation seed decisions:
      - Mark **Clone, build, and upload template site** as `in_progress`.
      - If seed data is ready and was not skipped, mark **Apply template seed data** as `in_progress` and launch the seed-data workstream with `Task` using `run_in_background: true`. The background task must only apply and verify the prepared seed data. It must not clone, build, upload, activate, update the shared status file, ask the user questions, or retry failed writes.
      - Run the site-provisioning wrapper in the main conversation while the seed-data task runs. These workstreams are independent after the supporting solution import creates the required Dataverse tables.

      When seed data is present, update the status page:
      ```json
      { "state": "running", "phase": "siteAndSeed", "message": "Creating template site and seeding data" }
      ```
      When seed data is absent, update it with:
      ```json
      { "state": "running", "phase": "site", "message": "Cloning, building, and uploading template site" }
      ```
      Clone the packaged SPA source into the directory selected earlier, then upload the clone:
      ```bash
      node "${PLUGIN_ROOT}/scripts/provision-template-site.js" \
        --sourcePath "<SELECTED_TEMPLATE_WEBSITE_CODE>" \
        --outputDirectory "<TEMPLATE_CLONE_OUTPUT_DIRECTORY>" \
        --siteName "<__SITE_NAME__>"
      ```
      Treat the wrapper as the sole template-site provisioning entry point. Do not rerun its underlying `pac`, `npm`, or build commands directly for diagnosis. Never pipe a mutating command such as `pac pages clone` or `pac pages upload-code-site` through `head`, `tail`, or another consumer that can close the output stream before the command finishes. The wrapper safely captures a bounded diagnostic tail without interrupting the command. On success, save the returned `clonedPath` as both `CLONED_TEMPLATE_SITE_PATH` and `PROJECT_ROOT`, `siteName` as `IMPORTED_SITE_NAME`, and `websiteRecordId` as `IMPORTED_WEBSITE_RECORD_ID`.
   10. In the seed-data background task, use the main conversation's prepared directory/file and currency decision:
       ```bash
       node "${PLUGIN_ROOT}/scripts/apply-seed-data.js" \
         --seedDir "<TEMPLATE_SEED_DIRECTORY>" \
         --seedFile "<TEMPLATE_SEED_FILE>" \
         --envUrl "<environmentUrl>" \
         --currencyCode "<TEMPLATE_SEED_CURRENCY_CODE, only when financial rows were planned>"
       ```
       Omit the entire currency flag when no financial rows were planned.
       The seeder resolves only an existing active target currency and its exact metadata navigation before any writes.
       It removes declared upload-column values and their generated file-name companions from create payloads; file-upload actions populate those fields.
       Return the complete JSON summary (`ok`, `inserted`, `failed`, `skipped`, `errors`) to the main conversation.
       A partial row, state, validation, or file-upload failure must be reported as `ok: false`; never rely solely on a successful CLI exit.
       Seed records must use the exact table entity-set names, column logical names, and scalar or collection `<NavigationProperty>@odata.bind` lookup names from the template solution metadata.
       Never derive lookup navigation properties from a primary key, entity set, display name, or app-style alias such as `categoryId`; the seeder rejects ambiguous aliases and malformed collection binds before its first Dataverse write.
       For verification, query the planned record IDs—not merely `$top=1`—and compare expected references and native collection memberships.
       Verify each uploaded attachment through its target file metadata/content, rather than counting only created attachment rows.
       Ordinary existing seed-record skips do not reconcile native N:N memberships; disclose that limit and do not silently update existing contacts, businesses, or assignments.
   11. Wait for both workstreams to finish before showing the inactive-site summary or starting activation. Record the seed summary, then mark **Apply template seed data** as `completed`; if seed data is absent, mark it skipped. Seed fetch and insertion remain best-effort: surface their result, but do not fail site creation or block activation.

       If `EMIT_TEMPLATE_IMPORT_SUCCESS = true`, emit the import result now:
       ```bash
       node "${PLUGIN_ROOT}/scripts/emit-create-site-template-outcome.js" \
         --eventName template_import_success \
         --templateId "<SELECTED_TEMPLATE.id>" \
         --templateKind "<SELECTED_TEMPLATE.kind>" \
         --framework "<SELECTED_TEMPLATE_VARIANT.framework>" \
         --audience "<internal|external from Phase 1 discovery>" \
         --seedApplied "<true only when the seed summary has inserted > 0 and failed = 0; otherwise false>"
       ```
       `--audience` is the site audience captured in Phase 1 (`internal` or `external`), **not** the template's `audience` persona array from the catalog manifest. Do not include site name, URL, subdomain, free-text purpose, or any other user-identifying value.
   12. If clone, cloned-identity inspection, dependency installation, build, build-output validation, or upload fails, run `template_clone_failure` telemetry silently. Map the returned `step` to `errorClass`: `clone`/`clone-output` → `PacPagesClone`, `install` → `NpmInstall`, `build` → `NpmBuild`, `build-output` → `CompiledOutput`, and `upload` → `PacPagesUploadCodeSite`:
       ```bash
       node "${PLUGIN_ROOT}/scripts/emit-create-site-template-outcome.js" \
         --eventName template_clone_failure \
         --templateId "<SELECTED_TEMPLATE.id>" \
         --templateKind "<SELECTED_TEMPLATE.kind>" \
         --framework "<SELECTED_TEMPLATE_VARIANT.framework>" \
         --audience "<internal|external from Phase 1 discovery>" \
         --outcome failure \
         --errorClass "<PacPagesClone|NpmInstall|NpmBuild|CompiledOutput|PacPagesUploadCodeSite>"
       ```
       Update the status page before firing the recovery gate:
       ```json
       { "state": "error", "phase": "site", "message": "Template site creation failed" }
       ```
       Diagnose the failure only from the wrapper's returned `step`, `error`, and preserved local path. Do not rerun the wrapper or any underlying command until the user chooses a recovery option.
       Then fire this gate:

      <!-- gate: create-site:1.5.clone-failed | category=progress | cancel-leaves=partial-template-clone -->

      > 🚦 **Gate (progress · create-site:1.5.clone-failed):** Choose how to proceed after cloning, building, or uploading the packaged template site fails.
      >
      > **Trigger:** Phase 1.5 when cloning, cloned identity inspection, dependency installation, the project build, compiled-output validation, or `pac pages upload-code-site` fails.
      > **Why we ask:** The selected project directory can contain partial clone or build output, the environment can contain a partial code-site upload, and supporting solutions may already be installed.
      > **Cancel leaves:** `partial-template-clone` — cached template artifacts and local files remain in the selected project directory; supporting solutions or a partial site upload may also remain in Dataverse.

      | Question | Header | Options |
      |----------|--------|---------|
      | The template site could not be cloned, built, or uploaded. How would you like to proceed? | Site Creation Failed | Retry site creation (Recommended), Fall back to from-scratch, Stop |

      Do not retry automatically. If the wrapper returned `outputDirectoryRemoved: true`, **Retry site creation** may reuse the same selected location. Otherwise, ask for a new empty directory using the same **Project Location** prompt, update `TEMPLATE_CLONE_OUTPUT_DIRECTORY`, and rerun the wrapper. If the user falls back to from-scratch, explain that local files, supporting solutions, or a partial site upload may remain and recommend `<SELECTED_TEMPLATE_VARIANT.framework>`.
   13. When clone, build, and upload succeed, mark **Clone, build, and upload template site** as `completed` and run `template_clone_success` telemetry silently:
       ```bash
       node "${PLUGIN_ROOT}/scripts/emit-create-site-template-outcome.js" \
         --eventName template_clone_success \
         --templateId "<SELECTED_TEMPLATE.id>" \
         --templateKind "<SELECTED_TEMPLATE.kind>" \
         --framework "<SELECTED_TEMPLATE_VARIANT.framework>" \
         --audience "<internal|external from Phase 1 discovery>"
       ```
       Do not include the site name, clone path, URL, or Website Record ID in telemetry.
   14. Mark **Show inactive template site** as `in_progress`. Tell the user: "Template `<displayName>` was created as `<IMPORTED_SITE_NAME>` (`<IMPORTED_WEBSITE_RECORD_ID>`). It is not activated yet. Seed-data processing is complete, and activation is next." If the template has no seed data, say that instead of implying records were inserted. Mark **Show inactive template site** as `completed`.
   15. Mark **Activate template site** as `in_progress`. Before invoking `/activate-site`, update the status page:
       ```json
       { "state": "running", "phase": "activation", "message": "Activating template site", "awaitingInput": false }
       ```
       Then invoke `/activate-site`, passing the resolved identity and status path in the request so it skips local-project discovery and can show a toast while waiting for activation input:
       ```text
       Activate cloned template site:
       - siteName: <IMPORTED_SITE_NAME>
       - websiteRecordId: <IMPORTED_WEBSITE_RECORD_ID>
       - environmentUrl: <environmentUrl>
       - statusPath: <temp-import-status-dir>/status.json
       - source: create-site template path
       ```
       The activate-site skill owns subdomain selection, the waiting-for-input toast, final activation confirmation, provisioning-status polling, and recovery. Its foreground activation script is the only poll required by this flow.
       If activation fails, tell the user the cloned site exists but is not live and can be activated later by rerunning `/activate-site` with this identity. Do not treat activation failure as a failed supporting-solution import or site upload.
   16. When `/activate-site` returns a `siteUrl`, mark **Activate template site** as `completed` and **Show live template site** as `in_progress`.
   17. Redirect the already-open status page to the live site:
       ```json
       {
         "state": "succeeded",
         "message": "Template site is live. Opening it now...",
         "redirectUrl": "<siteUrl>",
         "shutdownServer": true
       }
       ```
       Do not open a second browser page for the template path. The status page polls this file and redirects the same tab to `redirectUrl` when the URL is `http` or `https`. `shutdownServer: true` tells the local server to remove the temporary status directory after a short redirect grace period. If the user closed the status page, show the `siteUrl` for manual opening.

       Always surface the activate-site DNS propagation caveat: the site may take a few minutes to load even after activation succeeds. Do not start a separate background command or Task to wait for the live URL to return HTTP 200. The status-page redirect and DNS note handle that delay without leaving work that can resume the conversation after the completion summary. If a reachability poll was started accidentally, stop it before marking **Show live template site** as `completed`.
   18. Mark **Show live template site** as `completed`, then present the template-path summary:
       - Template name and framework
       - Cloned site name and Website Record ID
       - Live site URL
       - DNS propagation note: the site may take a few minutes to load everywhere
       - Local project path (`PROJECT_ROOT`)
       - "Your site is live. Want to keep customizing the local project?"

<!-- not-a-gate: optional post-live template customization branch; the local clone already exists and the site is live -->

   19. Use `AskUserQuestion`:

       | Question | Header | Options |
       |----------|--------|---------|
       | Your template site is live. Do you want to customize the local project now? | Customize Template Site | Yes, customize now (Recommended), No, finish here |

       - **No, finish here**: mark **Select template or choose from-scratch** as `completed`, then stop.
       - **Yes, customize now**: append the template customization tasks (see [Progress Tracking](#progress-tracking)), then continue below.

   20. Mark **Plan template customizations** as `in_progress`, then inspect the
       cloned project before asking what the user wants changed:

       ```bash
       node "${PLUGIN_ROOT}/scripts/lib/localization-config.js" inspect --projectRoot "<PROJECT_ROOT>"
       ```

       Establish the language context required by the Phase 4 plan in this order:
       - When `localization.detected=true` and `localization.valid=true`, use
         `localization.defaultLocale` as the authoritative locale. Resolve it
         with `resolve-locale`, set `SITE_LOCALE`, `SITE_DIRECTION`, and
         `SITE_LANGUAGE` from the returned `locale`, `direction`, and
         `languageName`, and retain `localeName` when regional or script detail
         needs to be displayed. Do not scan locale-specific document roots as
         though they described one single-language site.
       - When `localization.detected=true` and `localization.valid=false`, show
         the localization conflicts and stop before planning customization. The
         localization configuration must be repaired; do not fall back to
         static document attributes or report localized roots as a
         single-language conflict.
       - When no localization is detected and `siteLanguage.detected=true` with
         `siteLanguage.valid=true`, resolve `siteLanguage.locale` with
         `resolve-locale`. Use the returned `locale`, `direction`, and
         `languageName` to establish `SITE_LOCALE`, `SITE_DIRECTION`, and
         `SITE_LANGUAGE`.
       - When `siteLanguage.reason` is `document-not-found`,
         `html-root-not-found`, `document-ambiguous`, or
         `document-configuration-invalid`, show its conflicts and
         `expectedSources`, then stop before planning customization. Locate or
         repair the actual application root document before asking for a
         content language.
       - When the document exists but its static language attributes are
         missing or invalid, ask the **Content language** question below,
         resolve the answer with `resolve-locale`, and include repairing
         `siteLanguage.source` in the approved customization plan.

       After `SITE_LANGUAGE`, `SITE_LOCALE`, and `SITE_DIRECTION` are all
       established, ask what the user wants changed in `PROJECT_ROOT`. Use the
       existing Phase 3/4/5/6/7 implementation, verification, and review flow
       against the cloned project; do **not** run Phase 2 scaffold/copy-template.
       In Phase 3, skip the Brand sub-prompt and set `BRAND_SOURCE` to
       `template` - the template's existing theme tokens are the source of truth
       unless the user asks for a redesign. On this path Phase 5 edits the cloned
       project in place: keep the existing theme file, layout, and pages, change
       only what the approved plan names, and skip every `scaffold-status.json`
       step because no loader is running. Run the Phase 5.7 design critique on
       the pages you changed.
   21. After the customization plan is approved, mark **Plan template customizations** as `completed`, **Implement pages and components** as `in_progress`, and make the requested changes.
   22. Run the existing validation/review flow. Do not automatically deploy unless the user explicitly asks to run `/deploy-site`.

8. For the from-scratch path only, tell the user: "I'll scaffold this site from scratch."

<!-- not-a-gate: deferred data-gathering prompt for framework and directory before any scaffold files are written -->

9. Ask the from-scratch-only questions that were deferred from Phase 1:

   | Question | Header | Options |
   |----------|--------|---------|
   | Which frontend framework? | Framework | React (Recommended), Vue, Angular, Astro |
   | Where should the project be created? | Location | Current directory, New folder in current directory (Recommended), Any other directory |

10. Determine the site's single content language independently of whether the
   purpose was supplied in `$ARGUMENTS`.

   - If the request explicitly names the content language, resolve it without
     asking again and include it in the confirmation.
   - Do not infer the desired site language merely from the language used to
     converse with the maker.

   <!-- not-a-gate: the locale is validated before it can affect scaffolding -->

   When the content language was not explicit, get a best-effort suggestion:

   ```bash
   node "${PLUGIN_ROOT}/scripts/suggest-content-locale.js"
   ```

   The script returns the selected Dataverse environment's base language as a
   canonical BC