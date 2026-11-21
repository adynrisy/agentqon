<!--
Source: microsoft/power-platform-skills
Path: plugins/power-pages/skills/setup-auth/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/power-pages/skills/setup-auth/SKILL.md
-->

> **Plugin check**: Run `node "${PLUGIN_ROOT}/scripts/check-version.js"` — if it outputs a message, show it to the user before proceeding.

# Set Up Authentication & Authorization

Configure authentication (login/logout) and role-based authorization for a Power Pages code site. This skill supports multiple identity providers -- Microsoft Entra ID, Entra External ID (for customer-facing apps with self-service sign-up), OpenID Connect (Okta, Auth0, etc.), SAML2, WS-Federation, local authentication (username/password), Microsoft Account, Facebook, and Google. It also supports optional features including invitation-based registration and Terms & Conditions acceptance. Power Pages built-in 2FA is intentionally not scaffolded because the SendCode/VerifyCode pages are server-rendered and cannot be integrated into a SPA experience — use IdP-level MFA instead. It creates an auth service, type declarations, authorization utilities, auth UI components, and role-based access control patterns appropriate to the site's framework and chosen identity provider(s).

## Core Principles

- **Client-side auth is UX only** — Power Pages authentication is server-side (session cookies). Client-side role checks control what users see, not what they can access. Server-side table permissions enforce actual security.
- **Framework-appropriate patterns** — Every auth artifact (hooks, composables, services, directives, guards) must match the detected framework's idioms and conventions.
- **Development parity** — Include mock data for local development so developers can test auth flows and role-based UI without deploying to Power Pages.
- **Preserve site integrity** — Before changing navigation, auth pages, dialogs, or role-based UI, read `${PLUGIN_ROOT}/references/site-modification-integrity.md`. Synchronize all visible auth states across configured locale resources and keep them bidirectional and expansion-safe.

**Initial request:** $ARGUMENTS

> **Prerequisites:**
>
> - An existing Power Pages code site created via `/create-site`
> - The site must be deployed at least once (`.powerpages-site` folder must exist)
> - Web roles must be created via `/create-webroles`

## Workflow

1. **Phase 1: Check Prerequisites** — Verify site exists, detect framework, check web roles
2. **Phase 2: Plan** — Gather auth requirements (optionally set up the IDP app registration knowledge-first — Guided by default, or configure-for-you via the provider's own CLI where supported: Okta / Auth0 / Entra External ID) and present plan for approval
3. **Phase 3: Create Auth Service** — Auth service with login/logout and type declarations
4. **Phase 4: Create Authorization Utils** — Role-checking functions and wrapper components
5. **Phase 5: Create Auth UI** — Login/logout button integrated into navigation
6. **Phase 6: Implement Role-Based UI** — Apply role-based patterns to site components
7. **Phase 7: Verify Auth Setup** — Validate all auth files exist, build succeeds, auth UI renders
8. **Phase 8: Review & Deploy** — Summary and deployment prompt

---

## Phase 1: Check Prerequisites

**Goal:** Confirm the project exists, identify the framework, verify deployment status and web roles, and check for existing auth code.

### Actions

#### 1.1 Locate Project

Look for `powerpages.config.json` in the current directory or immediate subdirectories:

```text
**/powerpages.config.json
```

**If not found**: Tell the user to create a site first with `/create-site`.

#### 1.2 Detect Framework

Read `package.json` to determine the framework (React, Vue, Angular, or Astro). See `${PLUGIN_ROOT}/references/framework-conventions.md` for the full framework detection mapping.

#### 1.3 Check Deployment Status

Look for the `.powerpages-site` folder:

```text
**/.powerpages-site
```

**If not found**: Tell the user the site must be deployed first:

> "The `.powerpages-site` folder was not found. The site needs to be deployed at least once before authentication can be configured."

<!-- gate: setup-auth:1.3.deploy-first | category=plan | cancel-leaves=nothing -->

> 🚦 **Gate (plan · setup-auth:1.3.deploy-first):** `.powerpages-site` missing — auth setup writes site settings inside that folder. Deploy first or stop.
>
> **Trigger:** Phase 1.3 detected no `.powerpages-site` folder.
> **Why we ask:** Auto-deploy picks the wrong env; skipping leaves auth wiring broken.
> **Cancel leaves:** Nothing — no auth files written yet.

Use `AskUserQuestion`:

| Question | Options |
|----------|---------|
| Your site needs to be deployed first. Would you like to deploy now? | Yes, deploy now (Recommended), No, I'll do it later |

**If "Yes, deploy now"**: Invoke `/deploy-site`, then resume.

**If "No"**: Stop — the site must be deployed first.

#### 1.4 Check Web Roles

Look for web role YAML files in `.powerpages-site/web-roles/`:

```text
**/.powerpages-site/web-roles/*.yml
```

Read each file and compile a list of existing web roles (name, id, flags).

<!-- gate: setup-auth:1.4.create-webroles | category=plan | cancel-leaves=nothing -->

> 🚦 **Gate (plan · setup-auth:1.4.create-webroles):** No web roles found — role-based authorization needs at least one role. Create roles first or skip and add later.
>
> **Trigger:** Phase 1.4 found no YAML files in `.powerpages-site/web-roles/`.
> **Why we ask:** Auto-invoking `/create-webroles` runs another full skill; auto-skipping leaves RBAC checks against an empty role set.
> **Cancel leaves:** Nothing — no auth files written yet.

**If no web roles exist**: Warn the user that web roles are needed for authorization. Ask via `AskUserQuestion` whether to create them first:

| Question | Options |
|----------|---------|
| No web roles were found. Web roles are required for role-based authorization. Would you like to create them now? | Yes, create web roles first (Recommended), Skip — I'll add roles later |

**If "Yes"**: Invoke `/create-webroles`, then resume.

**If "Skip"**: Continue — auth service and login/logout will still work, but role-based authorization will need roles created later.

#### 1.5 Discover Existing Auth Configuration

**Always run this discovery step, even on a first invocation** — the site may have site settings from a prior run, or from hand-editing the YAML files, even if no SPA auth code exists yet. The goal is to make sure we never silently drop a provider that's already configured server-side.

**Step 1 — Scan `.powerpages-site/site-settings/` for already-configured providers.**

Detect existing providers by matching site-setting filenames against these patterns:

| Pattern | Maps to provider type |
|---|---|
| `Authentication-OpenIdConnect-{Name}-AuthenticationType.sitesetting.yml` | OIDC (Entra External ID, Okta, Auth0, generic OIDC, B2C — all share the OIDC path) |
| `Authentication-SAML2-{Name}-AuthenticationType.sitesetting.yml` | SAML2 |
| `Authentication-WsFederation-{Name}-AuthenticationType.sitesetting.yml` | WS-Federation |
| `Authentication-OpenAuth-{Microsoft\|Facebook\|Google}-{ClientId\|AppId}.sitesetting.yml` | Social OAuth |
| `Authentication-Registration-LocalLoginEnabled.sitesetting.yml` with value `true` | Local Authentication |

For each detected provider, read its full set of `.sitesetting.yml` files to extract: `Authority` / `MetadataAddress`, `ClientId` / `AppId`, `AuthenticationType` (the providerIdentifier), `Caption` or display name (if present), and the `{Name}` slug used in the keys (e.g., `OpenIdConnect_1`, `EntraExternalId`).

**Distinguishing Entra ID variants from OIDC** — by Authority URL pattern:

| Authority pattern | Provider type | Notes |
|---|---|---|
| `https://login.windows.net/{guid}/` (no `/v2.0/`) — site's parent tenant | **Microsoft Entra ID (workforce)** — `type: 'entra-id'` | Auto-populated by Power Pages on site creation. The `{Name}` slug is usually `AzureAD`. **Set `providerIdentifier` to undefined in AUTH_PROVIDERS** — runtime resolver derives it from `Portal.tenant`. |
| `https://{subdomain}.ciamlogin.com/{tenantId}` (no trailing `/v2.0/`) | **Entra External ID** — `type: 'oidc'` | Customer tenant. Must include explicit `providerIdentifier` matching the Authority. |
| `https://{tenant}.b2clogin.com/{tenant}.onmicrosoft.com/v2.0/{policy}` | **Azure AD B2C** (legacy) — `type: 'oidc'` | Older B2C product. Must include explicit `providerIdentifier`. |
| Any other OIDC authority (Okta, Auth0, etc.) | **OIDC** — `type: 'oidc'` | Must include explicit `providerIdentifier`. |

**The Entra ID (workforce) case is special** — when Phase 1.5 discovery detects `Authentication/OpenIdConnect/AzureAD/*` settings on the site (which Power Pages auto-creates for the parent tenant), add a single entry to `EXISTING_PROVIDERS`:

```typescript
{
  id: 'entra-id',
  type: 'entra-id',
  displayName: existingCaption || 'Sign in with Microsoft',
  // NO providerIdentifier — resolveProviderIdentifier() derives it from Portal.tenant
}
```

Do NOT extract the tenant ID from the existing Authority site setting just to hardcode it back into AUTH_PROVIDERS — the runtime resolver handles it. This keeps the SPA code portable if the site is ever cloned to a different tenant.

**Step 2 — Scan for existing SPA auth code.**

Check for these files and read their key markers:

- `src/services/authService.ts` or `.js` — look for `AUTH_PROVIDERS` array (current pattern) vs single `AUTH_PROVIDER` constant (legacy)
- `src/types/powerPages.d.ts` — exists or not
- `src/utils/authorization.ts` — exists or not
- Auth components (`AuthButton.*`, `Login.*`, `Registration.*`, `RedeemInvitation.*`, etc.) — list which exist
- `src/pages/Login.tsx` — extract which providers it currently renders (via `AUTH_PROVIDERS` import or inline)

**Step 3 — Present findings to the user.**

If providers were detected from site settings, present them with their config:

```
I found these existing auth providers on your site:

  ✓ Entra External ID
    - ProviderName: OpenIdConnect_1
    - Tenant: ba275000-98c8-404d-a6f0-c5450f2aa668
    - ClientId: e728d63e-1190-495a-ae29-663e9cc10877
    - Configured in site settings: yes
    - Surfaced in SPA UI: NO (authService.ts has no entry for this provider)

  ✓ Local Authentication
    - LoginByEmail: true
    - Surfaced in SPA UI: yes
```

Use `AskUserQuestion`:

| Question | Header | Options |
|----------|--------|---------|
| I found existing auth providers on your site. What would you like to do? | Existing auth | Keep all existing providers and add a new one (Recommended) — preserves what's there, adds what you ask for next, Keep all existing providers (no new provider this run) — re-generates SPA code to surface what's already in site settings, Replace everything with a new configuration — wipes existing site settings and SPA code, starts fresh |

**"Keep all existing providers and add a new one"** (default path):
- Store the discovered providers as `EXISTING_PROVIDERS` — these will be merged into the `AUTH_PROVIDERS` array generated in Phase 3.2
- Phase 2.1 will prompt for the NEW provider being added; the existing ones are kept untouched
- For **local auth specifically** — if `Local Authentication` is in `EXISTING_PROVIDERS`, **always regenerate the local auth SPA code** (login flow, registration page, forgot/reset password, redeem invitation) from the user's Phase 2.1 answers. Don't try to preserve hand-edited local-auth code — the local flows are complex enough that partial updates introduce more bugs than they avoid.

**"Keep all existing providers (no new provider this run)"**:
- Skip the Phase 2.1 provider selection question entirely
- Re-derive `AUTH_PROVIDERS` from `EXISTING_PROVIDERS` only
- Useful for: fixing a site where the SPA UI is missing a provider that's already in site settings (the exact bug this branch was created to fix)

**"Replace everything with a new configuration"**:
- Set `EXISTING_PROVIDERS = []`
- Delete existing OIDC/SAML2/WsFed/OpenAuth site-setting YAMLs as part of Phase 8.1
- Run Phase 2.1 as if no providers existed

> **DO NOT** offer a "skip / no changes" option. If the user invokes setup-auth, they want auth set up — silently doing nothing is worse than asking.

### Output

- Project root path confirmed
- Framework identified (React, Vue, Angular, or Astro)
- Deployment status verified
- Web roles inventory compiled
- **`EXISTING_PROVIDERS` list compiled from site settings, with provider type, ProviderName slug, ClientId/Authority/etc. for each**
- **`MERGE_MODE` chosen: `keep-and-add` (default) | `keep-only` | `replace-all`**
- SPA auth file inventory recorded (which files exist, whether they use `AUTH_PROVIDERS` array or legacy single-provider pattern)

---

## Phase 2: Plan

**Goal:** Gather authentication requirements from the user and present the implementation plan for approval.

### Actions

#### 2.0 Smart Auth Inference (Before Asking)

Before asking the user which providers they want, analyze the site context from Phase 1 (site name, purpose, audience type) and try to infer appropriate auth settings automatically:

**Inference rules:**

| Site Type | Inferred Auth Settings | Rationale |
|-----------|----------------------|-----------|
| Internal/employee portal (HR, dashboard, admin) | Entra ID + invitation-only registration (`OpenRegistrationEnabled=false`, `InvitationEnabled=true`) | Internal sites should restrict access to invited employees only |
| Customer-facing portal (support, self-service) | Entra External ID + open registration | Customer portals need self-service sign-up for customers |
| Partner portal (B2B, vendor) | Entra ID + invitation-only registration | Partners are pre-vetted; open registration is a security risk |
| Public site with protected features (e-commerce, community) | Entra External ID + open registration + optional Google/Facebook | Public sites benefit from social login for frictionless sign-up |
| Loan/financial/banking portal | Entra External ID + invitation-only registration | Financial sites require controlled access for compliance |

**If you can infer with confidence**, present the recommendation with rationale:

> "Based on your site purpose ({purpose}), I recommend:
> - **{provider}** for authentication
> - **{registration mode}** because {rationale}
>
> Would you like to proceed with this configuration, or choose different providers?"

| Question | Options |
|----------|---------|
| Would you like to proceed with this recommended configuration? | Yes, proceed with recommendation, No, let me choose providers |

**If "Yes"**: Skip Phase 2.1 provider selection and proceed directly to collecting provider-specific details (ClientId, tenant name, etc.) for the recommended provider(s).

**If "No"** or **if you cannot infer with confidence**: Fall back to Phase 2.1 below.

#### 2.1 Gather Requirements

<!-- gate: setup-auth:2.1.requirements | category=plan | cancel-leaves=nothing -->

> 🚦 **Gate (plan · setup-auth:2.1.requirements):** Pick which auth features to build (login+logout / RBAC / both). Covers the conditional follow-up "which roles get access" sub-prompt in the same step.
>
> **Trigger:** Phase 2.1 entry.
> **Why we ask:** Wrong feature set gets generated — e.g. building RBAC files when the user only wanted login.
> **Cancel leaves:** Nothing — no auth files written yet.

**Re-run handling — when Phase 1.5 detected existing providers:**

The behavior depends on the `MERGE_MODE` chosen in Phase 1.5:

- **`keep-only`** (user chose "keep all existing, no new provider this run") → Skip the new-provider selection question entirely. Proceed to the "Local Authentication" follow-ups only if local was detected. Phase 3.2 will generate `AUTH_PROVIDERS` from `EXISTING_PROVIDERS` only.
- **`keep-and-add`** (default — user wants to add one more) → Ask the user what to add. The provider selection question below should still be multi-select (the user could be adding multiple new providers in one go), but the existing providers are NOT in the list (they're already configured — the question is asking what's *new*). Common patterns:
  - User has Entra External ID, wants to add Local Auth → user selects "Local Authentication" → ask local follow-ups → Phase 3.2 merges
  - User has Entra External ID + Local, wants to add a *second* Entra External ID tenant → user selects "Entra External ID" → after collecting Authority/ClientId, ask: `"You already have an Entra External ID provider configured for tenant {existing-tenant}. This new one is a separate instance — give it a distinct ProviderName slug (used in site setting keys like Authentication/OpenIdConnect/{ProviderName}/* and in code as the provider id)."` Let the user pick a slug (default to the next incrementing number, e.g., `OpenIdConnect_2`) or pick a custom name (e.g., `EntraExternalId_Employee`).
- **`replace-all`** (user chose to wipe everything) → Run the provider selection question as on a first invocation.

**Do NOT proactively ask "do you want to configure multiple instances?"** at the start. Walk the user through configuring ONE provider at a time. When they finish configuring one and want another, they can re-run setup-auth → Phase 1.5 detects what's there → Phase 2.1 in `keep-and-add` mode asks "what do you want to add now?". This keeps the question count low for the common case (configure one provider) while still supporting the advanced case (multiple tenants).

**IMPORTANT: Multiple providers are supported.** The user may want more than one identity provider (e.g., Entra External ID + Google). If the user's initial prompt mentions specific providers, skip the provider selection question and proceed directly to collecting details for each mentioned provider.

> **IMPORTANT — Local Authentication:** NEVER set up local authentication by default. Do NOT include it in the provider selection list, do NOT recommend it in smart inference, and do NOT configure it unless the user explicitly and specifically asks for it (e.g., "I want username/password login", "set up local login", "add local auth"). External identity providers (Entra External ID, Entra ID, OIDC, etc.) are always preferred. If the user says something ambiguous like "add login", default to an external provider — never to local auth.

If the user has NOT specified which provider(s) they want, use `AskUserQuestion` to determine the identity provider(s). **This is a multi-select question** — the user can choose one or more:

| Question | Options |
|----------|---------|
| Which identity provider(s) do you want to use? (select all that apply) | Entra External ID (Recommended) — Customer identity with self-service sign-up (CIAM), Microsoft Entra ID — Azure AD / Entra ID for internal/employee sites, OpenID Connect — Okta, Auth0, or any OIDC-compliant provider, SAML2 — SAML 2.0 identity provider (ADFS, Shibboleth, Login.gov, etc.), WS-Federation — WS-Federation identity provider, Microsoft Account — Sign in with Microsoft personal/work account, Facebook — Sign in with Facebook, Google — Sign in with Google |

**Then, for EACH selected provider, ask the mandatory follow-up questions below.** Do not skip any provider — every selected provider needs its configuration collected before proceeding.

For each provider, also share the relevant Microsoft Learn documentation link so the user knows where to get the values:

**For "Microsoft Account"**:

| Question | Options |
|----------|---------|
| What is the Client ID from your Microsoft app registration? (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`) | *(free text)* |

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/openid-settings

**For "Facebook"**:

| Question | Options |
|----------|---------|
| What is the App ID from the Facebook Developer Console? (e.g., `1234567890123456`) | *(free text)* |

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/facebook-settings

**For "Google"**:

| Question | Options |
|----------|---------|
| What is the Client ID from the Google Cloud Console? (e.g., `123456789-abc.apps.googleusercontent.com`) | *(free text)* |

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/oauth2-google

**For "OpenID Connect"** (Okta, Auth0, etc.):

**First, identify the specific OIDC provider** — setup steps and the docs to read differ per vendor:

| Question | Options |
|----------|---------|
| Which OpenID Connect provider are you using? | Okta, Auth0, Other (any OIDC-compliant provider) |

Use the answer as `{provider}` in the questions below and for Phase 2.1.2 Step A (reading the provider's docs). For **Other**, capture the provider's name from the user.

**Orient the user first — don't jump straight to inputs.** Now that they've picked {provider}, set expectations: you'll set up an app registration at {provider} for this site. The **first thing** you'll do is **read {provider}'s own documentation** — how it's set up depends on the provider. Then, depending on what {provider} supports, you'll either walk the user through the steps in the {provider} console, or — when {provider} supports it — configure it for the user. Either way you derive the metadata, Redirect URI, and claims automatically, and the app uses the no-secret `code id_token` flow — no client secret to manage.

Ask for the {provider} tenant — the sign-in domain (Okta: `your-tenant.okta.com`, Auth0: `your-tenant.us.auth0.com`):

| Question | Options |
|----------|---------|
| What's your {provider} tenant? | *(free text)* |
| What display name should the login button show? (default: `Sign in`) | *(free text, defaulted)* |
| Do you already have an app registration for this site at {provider}? | No — set one up in Phase 2.1.2 (Recommended), Yes — I already have one |

If the user answered **"Yes — I already have one"**, ask for the existing **Client ID** and **Metadata Address** now (Phase 8.1 needs them) and skip the Phase 2.1.2 app-registration setup. Otherwise the Client ID is **not** asked upfront — it comes from **Phase 2.1.2**, where you set up the app (guided, or configured for the user when the provider supports it) and derive the metadata from the provider's discovery document.

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/openid-settings

**For "Entra External ID"** — use the 4-step walkthrough below. Do NOT just ask the user for Authority/ClientId/Metadata upfront — those values come from a tenant + app registration + user flow that the user may not have set up yet. Walk them through each prerequisite before asking for the corresponding value.

> Reference doc: https://learn.microsoft.com/en-us/power-pages/security/authentication/entra-external-id
> See also `${PLUGIN_ROOT}/skills/setup-auth/references/authentication-reference.md` for the full Entra External ID prerequisites section the steps below cross-reference.

**Pre-computed values for THIS site** — before starting the walkthrough, compute:
- `SITE_URL` = the deployed site URL (e.g., `https://site-597pv.powerappsportals.com`). Read from `pac env who` + the site name, or from the site's existing settings.
- `PROVIDER_NAME` = if this is a fresh add, default to `OpenIdConnect_1` (or the next free `OpenIdConnect_N` slug per the CallbackPath uniqueness logic in Phase 8.1). The user can override to a custom slug like `EntraExternalId_Customer` for multi-instance setups.
- `REDIRECT_URI` = `{SITE_URL}/signin-{PROVIDER_NAME-lowercased}` — e.g., `https://site-597pv.powerappsportals.com/signin-openidconnect_1`. The user pastes this verbatim into the Entra app registration.
- `APP_NAME_SUGGESTION` = `power-pages-{site-shortname}` — e.g., `power-pages-savoria`
- `USER_FLOW_NAME_SUGGESTION` = `{site-shortname}-signupsignin` — e.g., `savoria-signupsignin`

Display these to the user before Step 1 so they have them handy.

##### Step 1 — Tenant

| Question | Header | Options |
|----------|--------|---------|
| Do you already have a Microsoft Entra External ID tenant? (This is a separate tenant type from a regular workforce Entra ID tenant — sometimes called CIAM.) | Tenant | Yes — I have an External ID tenant, No — help me create one (free 30-day trial), I'm not sure |

**If "No"**, show:

> Steps to create an Entra External ID tenant:
> 1. Open https://entra.microsoft.com/
> 2. Sign in with the account that should own the tenant
> 3. From the top, click **Manage tenants → Create**
> 4. Choose **External (for customers)** — NOT Workforce
> 5. Pick a domain prefix (the **tenant subdomain**) — e.g., `contoso` becomes `contoso.ciamlogin.com`. This appears in every login URL.
> 6. Free 30-day trial: no credit card required. You can attach a paid Azure subscription later.
>
> Detailed guide: https://learn.microsoft.com/en-us/entra/external-id/customers/quickstart-tenant-setup
>
> When you've created the tenant, switch to it (top-right tenant picker in entra.microsoft.com), then come back here.

**If "I'm not sure"**, show: "At https://entra.microsoft.com/ → top-right tenant picker. Tenants for customers are labeled **External**. Workforce tenants won't work — that's a different product."

Then collect the tenant identifiers:

| Question | Options |
|----------|---------|
| What is the tenant **subdomain**? (the part before `.ciamlogin.com` — e.g., `contoso`. Find it in the External ID tenant's Overview page under "Primary domain", removing `.onmicrosoft.com`.) | *(free text)* |
| What is the tenant **ID** (GUID)? (Find it in the External ID tenant's Overview page under "Tenant ID" — looks like `a1b2c3d4-e5f6-7890-abcd-ef1234567890`.) | *(free text)* |

**Validate**: subdomain matches `^[a-z0-9-]+$` (no dots, no uppercase, no `.ciamlogin.com` suffix); tenant ID matches the UUID regex. If either fails, show the expected format and re-prompt.

Store as `EXTERNAL_ID_TENANT_SUBDOMAIN` and `EXTERNAL_ID_TENANT_ID`.

##### Step 2 — App registration

**Confirm the Redirect URI first.** The skill pre-computes a default based on the site URL and `PROVIDER_NAME`, but the user may prefer a different URI:

> The Power Pages site needs a Redirect URI registered in your app registration. Based on the site URL and provider name, the default is:
>
> **`{REDIRECT_URI}`**
>
> You can keep this default, or use a different URI — for example, `{SITE_URL}/signin-entra-customer` or `{SITE_URL}/auth/external-id`. The host must be your Power Pages site; only the path can change.

| Question | Header | Options |
|----------|--------|---------|
| Use this Redirect URI? | Redirect URI | Use the default (Recommended) — `{REDIRECT_URI}`, Use a different URI |

**If "Use a different URI"**, ask:

| Question | Options |
|----------|---------|
| Enter the Redirect URI (must be on `{SITE_URL}`, must start with `{SITE_URL}/`, no spaces, no query string). Example: `{SITE_URL}/signin-entra-customer`. | *(free text)* |

**Validate** the custom URI:
- Must start with `{SITE_URL}/`
- Path portion must match `^/[a-zA-Z0-9_\-/]+$` (alphanumeric, hyphen, underscore, additional slashes allowed)
- Path must NOT collide with any `Authentication/OpenIdConnect/*/CallbackPath` already in `.powerpages-site/site-settings/` (from Phase 1.5 discovery)
- Path must NOT be a reserved Power Pages server path (`/Account/...`, `/SignIn`, `/Register`, `/_layout/...`, `/api/...`)

Re-prompt on invalid input. Then store the value as `REDIRECT_URI` for the rest of the walkthrough and Phase 8.1.

> **Note**: The skill writes two site settings derived from this single `REDIRECT_URI`: the user-facing `RedirectUri` (the full URI, sent to the IdP) and the internal `CallbackPath` (just the path portion, used by the OWIN middleware to know which incoming request to handle). The maker doesn't need to think about `CallbackPath` separately — the skill derives it automatically from `REDIRECT_URI` by extracting the path portion.

| Question | Header | Options |
|----------|--------|---------|
| Have you registered an app in your Entra External ID tenant for this Power Pages site? | App reg | No — walk me through it (Recommended for first time), Yes — I have the Application (client) ID |

**If "No"**, show step-by-step with the confirmed Redirect URI verbatim:

> Steps to register the app:
> 1. At https://entra.microsoft.com/, make sure you're in your External ID tenant (top-right picker)
> 2. **Applications → App registrations → New registration**
> 3. **Name**: `{APP_NAME_SUGGESTION}` (or your own name)
> 4. **Supported account types**: select **Accounts in this organizational directory only (single tenant)** — recommended for Power Pages. Multi-tenant configurations forcibly disable contact mapping by email for security.
> 5. **Redirect URI**: select **Web**, paste exactly:
>
>    ```
>    {REDIRECT_URI}
>    ```
>
>    (Copy this verbatim. Any mismatch between this value and the `RedirectUri` site setting causes sign-in to fail with `AADSTS50011: The reply URL specified in the request does not match`.)
> 6. Click **Register**
> 7. Open the **Authentication** tab → under "Implicit grant and hybrid flows" check **Access tokens** AND **ID tokens** → **Save**
> 8. Open the **API permissions** tab → click **Grant admin consent for {your tenant}** → confirm
> 9. Go back to the **Overview** tab and copy the **Application (client) ID** (it's a GUID)
>
> Detailed guide: https://learn.microsoft.com/en-us/entra/external-id/customers/quickstart-register-app

**If "Yes" (existing app)**, before asking for the Client ID, also confirm the user has the matching Redirect URI registered:

> Before continuing, please verify that your existing app registration has the following Redirect URI registered (under **Authentication → Web** in the Entra admin center):
>
> **`{REDIRECT_URI}`**
>
> If it's missing or different, add it now. An app registration can have multiple Web Redirect URIs registered — adding ours doesn't break any existing integrations. Sign-in will fail if the value in Power Pages doesn't match a registered URI exactly.

Then ask for the value:

| Question | Options |
|----------|---------|
| Paste the **Application (client) ID** from the Overview tab. | *(free text)* |

**Validate**: must match UUID v4 format (`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`). Re-prompt on mismatch.

Store as `EXTERNAL_ID_CLIENT_ID`.

**Do NOT ask about client secret.** Entra External ID app registrations are public clients using PKCE — no secret needed. The skill will create site settings without `ClientSecret` and skip Phase 8.1.1 (Key Vault) for this provider. If the user has a confidential-client scenario that requires a secret, they can add it manually via the Power Pages admin center after deploy — document this as an advanced override in Phase 8.5 post-deploy notes.

##### Step 3 — User flow

User flows define what attributes are collected from users and what claims appear in the ID token. Without one, sign-in fails after the IdP redirect.

| Question | Header | Options |
|----------|--------|---------|
| Have you created a sign-up/sign-in user flow in your Entra External ID tenant and attached it to your app? | User flow | No — walk me through it (Recommended for first time), Yes — I have the user flow name |

**If "No"**, the walkthrough's user-flow-attribute selection must match the `PROFILE_MAPPING_CHOICE` collected later (Track B's profile mapping question). Since this step runs BEFORE that question, ask it now (just for Entra External ID):

> The user flow needs to be told which attributes to collect from users and which claims to return in the token. The skill maps claims → Dataverse contact fields automatically — the attributes you select here determine what's available.

| Question | Header | Options |
|----------|--------|---------|
| What user profile info should the sign-up form collect and return as claims? | Profile attributes | Standard (Recommended) — Email, Given Name, Surname, Standard + phone — also Phone Number, Email only — minimal sign-up form |

Store as `PROFILE_ATTRIBUTES_CHOICE` (this also drives `PROFILE_MAPPING_CHOICE` in Track B — they should be consistent; default both to "Standard" unless the user explicitly differs).

Then show:

> Steps to create the user flow:
> 1. At https://entra.microsoft.com/, in your External ID tenant
> 2. **External Identities → User flows → New user flow**
> 3. **Name**: `{USER_FLOW_NAME_SUGGESTION}` (or your own — letters, digits, hyphens, underscores only)
> 4. **Identity providers** for sign-in: choose **Email with password** (Recommended — most familiar to customers) or **Email one-time passcode** (passwordless)
> 5. **User attributes to collect** (the sign-up form fields): based on your choice above, select:
>    - **Standard / Standard + phone**: ☑ Email Address, ☑ Given Name, ☑ Surname{`, ☑ Phone Number` if Standard + phone}
>    - **Email only**: ☑ Email Address
> 6. **User attributes to return as claims** (in the ID token): same selections as above — these power profile mapping into Dataverse contact fields
> 7. Click **Create**
> 8. Open the user flow you just created → **Applications** tab → **Add application** → select the app you registered in Step 2 → **Select**
>
> Detailed guide: https://learn.microsoft.com/en-us/entra/external-id/customers/how-to-user-flow-sign-up-sign-in-customers

Then ask:

| Question | Options |
|----------|---------|
| Paste the **user flow name** you created (e.g., `{USER_FLOW_NAME_SUGGESTION}`). | *(free text)* |

**Validate**: matches `^[a-zA-Z0-9_-]+$` (letters, digits, hyphens, underscores). Re-prompt on mismatch.

Store as `EXTERNAL_ID_USER_FLOW`.

##### Step 4 — Display name + Confirmation

| Question | Options |
|----------|---------|
| What should the login button label say? Default: **`Sign in with Entra External ID`** (shortened from "Sign in with Microsoft Entra External ID" so it fits on one line in the horizontal-row Login page layout — see note below). Do NOT use "Sign in with Microsoft" — that conflicts with the Microsoft Account social provider. | *(free text, defaulted)* |

> **Display name length guidance**: keep labels around **28 characters or less** to display on a single line in the horizontal-row Login page layout (which is the default). Longer labels still work — buttons grow vertically to wrap text to two lines — but single-line buttons look more polished. For reference:
> - "Sign in with Entra External ID" — 30 chars (wraps on narrow cards, fits on wider)
> - "Sign in with Microsoft Entra External ID" — 40 chars (wraps to two lines in the default horizontal layout)
> - "Customer Sign In" — 16 chars (always single line, but less descriptive)
>
> If the user has multiple external providers configured (e.g., Entra External ID + Google), shorter labels matter more because each button gets less width. For a single-provider site, longer labels are fine (the button spans the full row width).

Store as `EXTERNAL_ID_DISPLAY_NAME`.

Now derive the configuration and present a summary for confirmation:

- **Authority**: `https://{EXTERNAL_ID_TENANT_SUBDOMAIN}.ciamlogin.com/{EXTERNAL_ID_TENANT_ID}` (NO trailing `/v2.0/` — Entra External ID uses the bare tenant path, NOT the B2C-style URL)
- **MetadataAddress**: `https://{EXTERNAL_ID_TENANT_SUBDOMAIN}.ciamlogin.com/{EXTERNAL_ID_TENANT_ID}/v2.0/.well-known/openid-configuration`
- **AuthenticationType** (provider identifier in `AUTH_PROVIDERS` array and ExternalLogin POST): same value as Authority
- **RedirectUri**: `{REDIRECT_URI}` (computed earlier)
- **ClientId**: `{EXTERNAL_ID_CLIENT_ID}`

Present this summary inline:

> About to configure:
>
> | Field | Value |
> |---|---|
> | Provider | Microsoft Entra External ID |
> | Tenant | `{subdomain}.ciamlogin.com` (`{tenantId}`) |
> | App (Client) ID | `{clientId}` |
> | User flow | `{userFlowName}` |
> | Redirect URI | `{REDIRECT_URI}` (must already be registered in your app) |
> | Authority | `{authority}` (derived) |
> | Metadata | `{metadataAddress}` (derived) |
> | Display name | `{displayName}` |
> | Login button | "{displayName}" |
> | Client secret | None (public client / PKCE) |
>
> Continue to write these site settings?

| Question | Options |
|----------|---------|
| Continue? | Yes — write the site settings, No — let me adjust |

If "No", re-prompt for the specific value the user wants to change.

> **Implementation note:** Power Pages server treats Entra External ID as a generic OpenID Connect provider (no special CIAM handling). All settings go under `Authentication/OpenIdConnect/{ProviderName}/`. The `provider` value posted to `/Account/Login/ExternalLogin` must match the `AuthenticationType` site setting, which by default equals the authority URL.

**For "SAML2"**:

| Question | Options |
|----------|---------|
| What is the metadata endpoint URL for your SAML2 identity provider? (e.g., `https://adfs.contoso.com/FederationMetadata/2007-06/FederationMetadata.xml`) | *(free text)* |
| What display name should the login button show? (e.g., `Sign in with ADFS`, `Sign in with Login.gov`) | *(free text)* |

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/saml2-settings
> Login.gov (US government SAML IdP) — get its metadata endpoint from the SAML developer guide: https://developers.login.gov/saml/getting-started/

**For "WS-Federation"**:

| Question | Options |
|----------|---------|
| What is the metadata endpoint URL for your WS-Federation provider? (e.g., `https://adfs.contoso.com/federationmetadata/2007-06/federationmetadata.xml`) | *(free text)* |
| What is the provider realm or identifier? (e.g., `https://adfs.contoso.com/adfs/services/trust`) | *(free text)* |
| What display name should the login button show? (e.g., `Sign in with ADFS`) | *(free text)* |

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/ws-federation-settings

**Profile mapping (for every external provider — OIDC, Entra External ID, SAML2, WS-Federation, social)**

After collecting the provider's basic details, ask what user profile info should flow from the IdP to the Dataverse contact. **Don't skip this** — without it, contact records have empty `firstname`/`lastname` and the SPA falls back to displaying the email or username everywhere.

| Question | Header | Options |
|----------|--------|---------|
| What profile info should be copied from your identity provider into the Dataverse contact record? | Profile mapping | Standard (Recommended) — copy first name, last name, and email on first sign-in, Standard + phone — also copy mobile phone, Custom — let me pick which contact fields and claims to map, None — leave contact fields empty (the server will still populate emailaddress1 from the email claim) |

Store as `PROFILE_MAPPING_CHOICE`. Then ask:

| Question | Header | Options |
|----------|--------|---------|
| Should profile info be updated on every login, or only once at first sign-in? | Sync frequency | First sign-in only (Recommended) — copy claims once when the contact is created; let users edit their own profile afterwards without it being overwritten, Both — sync on first sign-in AND every login (use only when the IdP is the authoritative source of truth and you don't want users editing their profile in Power Pages) |

Store as `PROFILE_SYNC_FREQUENCY`. This determines whether to write `LoginClaimsMapping` (every login) in addition to `RegistrationClaimsMapping` (first sign-in only).

> **Why "First sign-in only" is now the default**: this skill optionally scaffolds a SPA profile page (`/user-profile`) where signed-in users can edit their own contact info. If `LoginClaimsMapping` is set, the server overwrites the user's edits with IdP claims on the very next login — which is confusing and silently undoes the user's work. "First sign-in only" lets the user own their profile after the contact is created. Switch to "Both" only when the IdP is the sole authoritative source for these fields (e.g., HR-managed workforce directory) and end-user edits should NOT persist.

**Claim type values** — the mapping format is comma-separated `contactfield=claimtype` (NOT JSON). For OIDC providers like Entra External ID, use OIDC short names:

| Choice | Generated mapping |
|---|---|
| Standard | `firstname=given_name,lastname=family_name,emailaddress1=email` |
| Standard + phone | `firstname=given_name,lastname=family_name,emailaddress1=email,mobilephone=phone_number` |
| Custom | Loop: ask the user for each `contactfield=claimtype` pair until they say done. Suggest OIDC short names (`given_name`, `family_name`, `email`, `phone_number`, `preferred_username`, custom claim names). Validate that `contactfield` is a known Dataverse contact column. |
| None | Don't write `RegistrationClaimsMapping` or `LoginClaimsMapping` settings. |

For **SAML2 / WS-Federation**, the claim types are URIs (e.g., `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname`). Adjust the "Standard" generated mapping accordingly. For **social** providers, the claim types are provider-specific (Google: `given_name`, Facebook: `name`).

**Contact linking (for every external provider)**

Ask whether to auto-link external sign-ins to existing contacts by email.

| Question | Header | Options |
|----------|--------|---------|
| If a user signs in with an external provider and their email matches an existing Dataverse contact, what should happen? | Contact linking | Link to the existing contact (Recommended) — auto-link by email match so users don't end up with duplicate contacts when admins pre-create records (single-tenant providers only — see warning below), Create a new contact — always create a fresh contact, never auto-link (safer choice when the IdP doesn't verify emails) |

Store as `CONTACT_LINKING_CHOICE`. This drives `AllowContactMappingWithEmail` (`true` for "link", `false` for "create new").

> **Why "Link to the existing contact" is the default**: the common flow is that admins pre-create contact records in Dataverse (often via invitation or import) and then expect those exact contacts to be picked up when the user signs in for the first time via the configured IdP. Without linking, the server creates a brand-new contact and the pre-created record sits orphaned — confusing for users and easy to misdiagnose. Linking by verified email is the well-known pattern for joining IdP identity to an existing CRM record.
>
> **⚠ Auto-link is for migration / pre-provisioned contacts**: turn linking on when contacts already exist in Dataverse (data migration, admin import, invitations) and must be matched by email. On a greenfield site with no pre-existing contacts, normal first-sign-in contact creation works the same — linking simply has nothing to match.
>
> **⚠ Multi-tenant safety**: For **multi-tenant Entra External ID** (Authority uses `/organizations/` or `/common/`, or `IssuerFilter` is a wildcard), the Power Pages server **forcibly disables** `AllowContactMappingWithEmail` regardless of the site setting (`BlockContactMappingSettingForMultitenantApp` feature flag in `LoginController.cs:2578-2587`). Reason: email claims can't be trusted across tenants. If the user selects "Link to the existing contact" but the Authority is multi-tenant, warn them that linking won't work and recommend single-tenant Authority.
>
> **⚠ Security**: When `AllowContactMappingWithEmail = true`, an attacker who can sign into the configured IdP using a victim's email can take over the victim's contact. Enable only when the IdP verifies emails (Entra External ID with single tenant verifies; arbitrary OIDC may not). Switch to "Create a new contact" if you're configuring an IdP whose email-verification stance you don't control (e.g., a generic OIDC endpoint).

**For "Local Authentication"** (only if user explicitly requested it): Ask the user how they want users to identify themselves when logging in:

| Question | Options |
|----------|---------|
| How should users log in with their local account? | Login by email (Recommended) — Users sign in with their email address, Login by username — Users sign in with a chosen username |

This choice determines the `Authentication/Registration/LocalLoginByEmail` site setting (`true` for email, `false` for username) and affects every form field in the login, registration, and auth service code. When **email** is chosen, the login and registration forms show an `Email` field (type `email`). When **username** is chosen, the forms show a `Username` field (type `text`) and `Email` becomes a separate required field on the registration form (the server needs it for the contact record). Store this choice — it will be used in Phase 3 (auth service), Phase 5 (sign-in and registration pages), and Phase 8.1 (site settings).

**For "Local Authentication"** — also ask which registration mode the site should use:

| Question | Options |
|----------|---------|
| How should users be able to register on your site? | Open registration only (Recommended) — Anyone can sign up freely with a username/password, Invitation-only — Only users with a valid invitation code can register; direct registration is blocked, Both — Users can self-register OR redeem an invitation link, Registration disabled — No new accounts can be created (only existing users can log in) |

**Why this matters** — the server enforces the following gating rules in `RegistrationManager` (see `crm.solutions.portal/Samples/MasterPortal/Areas/Account/Models/RegistrationManager.cs`):

| Mode | `Enabled` | `OpenRegistrationEnabled` | `InvitationEnabled` | Behavior |
|---|---|---|---|---|
| Open registration only | `true` | `true` | `false` | Direct `/registration` works. Invitation links return 404. |
| Invitation-only | `true` | `false` | `true` | Direct `/registration` returns 404. Users must arrive via invitation link → `/redeem-invitation` → `/registration?invitationCode=...`. |
| Both | `true` | `true` | `true` | Both paths work. Invitation pre-fills email; direct registration is fully open. |
| Registration disabled | `false` | (moot) | (moot) | All registration endpoints return 404. Existing users can still log in. |

> **Note:** The `Authentication/Registration/RequireInvitationCode` setting is NOT a real server setting — the server doesn't read it. The "require invitation" behavior is enforced solely by `OpenRegistrationEnabled = false` + `InvitationEnabled = true`. Do not create that setting.

Store this choice as `REGISTRATION_MODE` — it drives:
- Whether to create the `/registration` page (always, unless `Registration disabled`)
- Whether to create the `/redeem-invitation` page (only when `InvitationEnabled` is true, i.e., `Invitation-only` or `Both`)
- Whether the `/registration` page calls `fetchInvitationDetails()` to pre-fill the email (only when `InvitationEnabled` is true)
- The deterministic set of site settings written in Phase 8.1
- Whether to default CAPTCHA on (open / both) or off (invitation-only — invitations already filter users)

**For "Microsoft Entra ID"** (workforce / employee tenant): No tenant or client info needed. Power Pages auto-configures the OIDC site settings (`Authentication/OpenIdConnect/AzureAD/*`) for the site's parent tenant when the site is created. The SPA derives the `providerIdentifier` (`https://login.windows.net/{tenantId}/`) at runtime from `window.Microsoft.Dynamic365.Portal.tenant` — no hardcoded values.

**Claims mapping is also auto-configured silently** — Phase 8.1 always writes `Authentication/OpenIdConnect/AzureAD/RegistrationClaimsMapping` and `LoginClaimsMapping` with the value `firstname=given_name,lastname=family_name,emailaddress1=upn` for the workforce Entra provider. **No question is asked for this** — the answer is deterministic. Workforce Entra ID issues v1.0 tokens by default (issuer `sts.windows.net/{tid}/`) which omit the `email` claim, so `upn` is the only reliable claim to populate `emailaddress1`. Without this mapping, contacts created on first sign-in have `oid` linked but firstname/lastname/email all empty (the User object renders with `contactId` but blank profile fields).

Only ask one optional question — the button display name. Provide a sensible default the user can accept:

| Question | Options |
|----------|---------|
| What should the login button label say? (default: `Sign in with Microsoft`) | *(free text, defaulted)* |

Store as `ENTRA_ID_DISPLAY_NAME` (default `"Sign in with Microsoft"`). Phase 3.2 adds an entry to `AUTH_PROVIDERS` with `type: 'entra-id'`, this display name, and **no `providerIdentifier`** (runtime-resolved).

> **Why no tenant ID?** The tenant ID is essentially for SPA-button wiring (the value the SPA POSTs to `/Account/Login/ExternalLogin`). Power Pages exposes the site's parent tenant ID at runtime via `window.Microsoft.Dynamic365.Portal.tenant`, so the SPA can construct the providerIdentifier (`https://login.windows.net/{tenantId}/`) without asking the user. The server-side OIDC settings are already in place from site creation. Compare this to **Entra External ID**, where the tenant is a SEPARATE customer tenant unrelated to the site's parent — there we DO need the user to provide the tenant ID + subdomain because they can't be derived from `Portal.tenant`.

> Docs: https://learn.microsoft.com/en-us/power-pages/security/authentication/openid-settings

**Login page layout** — when more than one auth provider is configured (including local + 1 external, or 2+ providers), the Login page renders all of them. Ask the user how they want providers laid out:

| Question | Header | Options |
|----------|--------|---------|
| How should sign-in options be laid out on the Login page? | Layout | Horizontal row (Recommended) — provider buttons side-by-side in a wrapping row, local form below a divider, Vertical stack — provider buttons stacked full-width, local form below a divider, Primary spotlight — one provider featured as the primary CTA, others under a "More sign-in options" toggle, local form below, Tabbed — tabs to switch between provider modes (good for 3+ providers, feels heavy for 2) |

Store this choice as `LOGIN_LAYOUT` — Phase 5.1.1 renders the Login page based on it. If only one provider is configured (e.g., `Entra External ID` only with no local), `LOGIN_LAYOUT` is moot: the AuthButton's "Sign In" calls `login()` directly, no Login page is needed.

For the **Primary spotlight** layout, ask a follow-up:

| Question | Header | Options |
|----------|--------|---------|
| Which provider should be featured as the primary sign-in option? | Primary provider | *(List the configured providers as options. The first external provider is a sensible default.)* |

Store as `PRIMARY_PROVIDER_ID`.

Then determine the scope:

| Question | Options |
|----------|---------|
| Which authentication features do you need? | Login & Logout + Role-based access control (Recommended), Login & Logout only, Role-based access control only (auth service already exists) |

Then ask about optional features:

| Question | Options |
|----------|---------|
| Would you like to enable any of these optional features? | None (Recommended), Terms and Conditions — require users to accept terms before accessing the site |

> **Note:** If they select Terms and Conditions, follow the Terms flow below.
>
> **Invitation-based registration is NOT in this list** — it's controlled by the registration mode question above. Setting registration mode to `Invitation-only` or `Both` is what enables invitations.
>
> **Two-factor authentication (2FA) is intentionally NOT offered.** Power Pages' built-in 2FA flow is server-rendered (`/Account/Login/SendCode` → `/Account/Login/VerifyCode`) and cannot be intercepted from the SPA — there's no SPA-equivalent UI for the code entry step, and bouncing the user out to a server page mid-login breaks the SPA experience. If the user explicitly asks for 2FA, tell them: "Power Pages built-in 2FA requires the legacy server-rendered SendCode/VerifyCode pages, which we don't support in SPA-based code sites yet. For external providers (Entra ID, Entra External ID, OIDC), enable MFA at the identity provider instead — it's transparent to Power Pages and stays inside the IdP's branded experience. For local accounts, 2FA on SPA sites is not currently supported." Do NOT create `TwoFactorEnabled`, `RememberMeEnabled`, or `RememberBrowserEnabled` site settings.

**Profile page** — ask whether to scaffold a SPA profile page that lets signed-in users edit their own contact info via the Power Pages Web API. This is a standalone question because it has its own infrastructure implications (Web API site settings on the `contact` entity + Self-scope table permission).

| Question | Header | Options |
|----------|--------|---------|
| Add a profile page that lets signed-in users edit their contact info (name, mobile phone, address) via the Power Pages Web API? Email is shown read-only. | Profile page | No (default) — no profile page; users can't edit their info from the SPA, Yes — create a /user-profile SPA page with edit form, accessible from the header user menu |

Store as `INCLUDE_PROFILE_PAGE` (boolean). Default `false`.

> **⚠ MANDATORY route name: `/user-profile` (NOT `/profile`)**. The path `/profile` is **reserved by the Power Pages server** for the legacy server-rendered profile page — using it as a SPA route creates a conflict that breaks the page. **Always use `/user-profile`** for the SPA route. The skill executor MUST NOT rename this route. Same for the file: **`src/pages/UserProfile.tsx`** (NOT `Profile.tsx`).

When `true`, Phase 5.1.9 generates `src/pages/UserProfile.tsx` (file name mandatory), extends `authService.ts` with `getMyProfile` / `updateMyProfile` (function names mandatory), evolves the `AuthButton` from inline `[Avatar Name Sign Out]` to a dropdown menu with "My Profile" and "Sign Out", and adds the `/user-profile` route (path mandatory) to `App.tsx`. Phase 8.1 writes the Web API site settings (`Webapi/contact/enabled = true` and `Webapi/contact/fields` with the COMPLETE default field list documented in Phase 8.1 — not a subset) and a Self-scope table permission on `contact` for the Authenticated Users role. The dropdown shape becomes the new default for `AuthButton` regardless of `INCLUDE_PROFILE_PAGE` so the component is ready for future menu items.

**Profile page design — intentionally simple:**

The page has two sections:

1. **Account Details** (read-only display at the top) — shows just the user's **full name** (firstname + lastname combined) and **email**. **DO NOT** display contactId, roles, sign-in method, provider name, last-login timestamp, or any other account metadata. Keep this section minimal.
2. **Edit form** (below) — only these editable fields. Email is intentionally **NOT** editable (changing email via Web API conflicts with auth provider claim mapping behavior and is surprising for external-auth users).

**Default EDITABLE field set** (the form MUST include exactly these 8 fields — do not add email, do not add middlename, do not add a Change Password link):

- First name (`firstname`)
- Last name (`lastname`)
- Mobile phone (`mobilephone`)
- Address line 1 (`address1_line1`)
- City (`address1_city`)
- State / Province (`address1_stateorprovince`)
- Postal code (`address1_postalcode`)
- Country (`address1_country`)

All fields are optional on submit. **DO NOT** include:

- ❌ `emailaddress1` as an editable field (read-only in Account Details only)
- ❌ `middlename` (keep the form simple)
- ❌ A "Change password" link or button (password reset is handled by the existing `/forgot-password` flow, not the profile page)
- ❌ A "Sign out" button on the profile page (sign-out lives in the header AuthButton dropdown only)
- ❌ Display of contactId, userRoles, or any account metadata beyond name + email

The Web API `fields` site setting MUST include `contactid` plus the 8 editable fields above (9 total entries — lowercase). Phase 8.1 specifies the exact value verbatim.

> **Prerequisite for `Yes`**: an "Authenticated Users" web role must exist (or any role with `authenticatedusersrole: true` flag). Phase 1.4 inventoried web roles — if none qualifies, warn the user that profile editing won't work until a role is assigned and offer to invoke `/create-webroles` first.

> **Cross-provider compatibility**: the profile page works the same regardless of auth provider (local, Entra ID, Entra External ID, OIDC, social) because it operates on the contact record after sign-in — not on IdP-specific session state. Email is read-only on the page, so there's no provider-specific caveat to surface (the IdP remains the source of truth for email).

**If "Terms and Conditions" is selected**, first surface the GDPR prerequisite **before** collecting content — terms only function if the underlying solution is installed:

> **GDPR prerequisite**: Terms require ALL THREE of these to be in place for the server to actually enforce them:
> 1. `Authentication/Registration/TermsAgreementEnabled = true` (site setting we will create)
> 2. The `msdynce_PortalPrivacyExtensions` solution must be installed in your Dataverse environment (`IsGdprEnabled` is portal-level)
> 3. The `Account/Signin/TermsAndConditionsCopy` content snippet must have non-empty text (we will create this)
>
> Without the Privacy Extensions solution, the server silently ignores `TermsAgreementEnabled`. The setup-auth skill will still write all three pieces — but unless the solution is installed in Dataverse, the terms gate won't be enforced server-side.

**Auto-detect the Privacy Extensions solution before asking.** Run:

```powershell
node "${PLUGIN_ROOT}/scripts/check-solution-installed.js" --solutionName "msdynce_PortalPrivacyExtensions"
```

The script prints JSON to stdout: `{ "installed": true, "version": "..." }` if found, or `{ "installed": false }` if the solution isn't in the environment. On infrastructure failure (no PAC environment, expired Azure CLI token, missing Read permission on the solutions table, network error), it exits non-zero and writes a human-readable reason to stderr.

Branch on the result:

| Script result | What to do |
|---|---|
| `installed: true` | Skip the prereq question entirely and proceed to collecting terms content (next section). Briefly tell the user: *"Confirmed `msdynce_PortalPrivacyExtensions` v{version} is installed in your environment — terms enforcement will work."* |
| `installed: false` | **Tell the user clearly that terms and conditions will NOT work**: *"The `msdynce_PortalPrivacyExtensions` solution is NOT installed in your Dataverse environment. The Terms and Conditions feature will NOT be enforced by the server until that solution is installed — `Authentication/Registration/TermsAgreementEnabled` is silently ignored without it. The site setting, Terms page, and content snippets will still be scaffolded, but the gate is a no-op until the solution is in place."* Then ask via `AskUserQuestion`: **header** "Privacy solution", **question** "Would you still like to set up Terms and Conditions now (it can be enabled later once you install the solution), or skip it?", **options**: "Continue anyway — scaffold the Terms infrastructure; I'll install the solution later", "Cancel — skip Terms and Conditions for this site". |
| Script exited non-zero (infrastructure failure) | Tell the user we couldn't auto-detect the solution (include the stderr message succinctly so they understand why — e.g., "couldn't reach Dataverse", "missing permissions on the solutions table"). Fall back to the manual prompt: **question** "We couldn't auto-detect whether `msdynce_PortalPrivacyExtensions` is installed. Do you have the GDPR/Privacy Extensions solution installed?", **options**: "Yes — solution is installed (or I'll install it)", "Continue anyway — set up terms; I understand they won't be enforced until I install the solution", "Cancel — I don't want terms". |

**If the user picks "Cancel" (in either the not-installed or fallback path)**: skip the Terms branch entirely, do not set `TermsAgreementEnabled`, do not create the Terms page or snippets.

Otherwise, collect the terms content. The server uses 4 content snippets — the skill hardcodes these values into the SPA Terms page component. Ask the user:

| Question | Header | Options |
|----------|--------|---------|
| What terms text should be shown to users? You can provide HTML or plain text. | Terms Content | Use default terms (Recommended) — Generic terms covering data use, account responsibility, and acceptable use, I'll provide my own terms text |

If the user provides custom text, use it. Otherwise use the default terms template (see `authentication-reference.md` for the default content).

Also collect optional customizations:

| Question | Header | Options |
|----------|--------|---------|
| Would you like to customize the terms page labels? | Labels | Use defaults (Recommended) — heading: "Terms and Conditions", checkbox: "I agree to these terms and conditions.", button: "Confirm", I'll customize the labels |

Store these 4 values — they'll be hardcoded into the Terms page component in Phase 5 and created as content snippets in Phase 8.1:
- `TERMS_HEADING` (default: "Terms and Conditions")
- `TERMS_CONTENT` (default: generic terms HTML)
- `TERMS_AGREEMENT_TEXT` (default: "I agree to these terms and conditions.")
- `TERMS_BUTTON_TEXT` (default: "Confirm")

Optionally ask about `TermsPublicationDate`:

| Question | Header | Options |
|----------|--------|---------|
| When should users be re-prompted to accept terms? | Re-consent | Every login (no publication date) — users accept terms every time they sign in, Set a publication date — users re-accept only when terms are updated past this date |

If "Set a publication date", collect the date. The format should be ISO: `YYYY-MM-DD` (e.g., `2026-01-01`). If "Every login", leave `TermsPublicationDate` unset.

If web roles were found in Phase 1.4, also ask:

| Question | Options |
|----------|---------|
| Which web roles should have access to protected areas of the site? | *(List discovered web role names as options)* |

#### 2.1.1 Optional Advanced Settings

After collecting the required provider details, ask if the user wants to configure advanced settings:

| Question | Options |
|----------|---------|
| Would you like to configure advanced authentication settings? (logout mode, claims mapping, session timeout, scopes, etc.) | No, use defaults (Recommended), Yes, show me the options |

**If "Yes, show me the options"**, present the optional settings table relevant to the selected provider. Only show settings that apply to their provider type. For each setting the user wants to configure, collect the value.

##### Logout mode (external providers only — OIDC, Entra External ID, SAML2, WS-Fed, social)

**Always offer this question to the user** when an external provider is being configured (it's the most common advanced setting and has visible UX consequences). For local-auth-only sites, skip this question.

> **Two logout modes:**
> - **Local logout** (server default, simpler) — Power Pages clears its session cookie and redirects the user to `returnUrl` (defaults to `/`). The user **remains signed in at the IdP**. Next time they click the external provider button, the IdP's SSO cookie is still warm and they re-sign-in silently with no credential re-entry. This is the default UX for most consumer / customer-facing sites.
> - **Federated logout** (RP-initiated) — Power Pages additionally calls the IdP's `end_session_endpoint` with `id_token_hint` and `post_logout_redirect_uri`. The IdP signs the user out of THEIR session too, then redirects the browser back to the site. The user is fully signed out across systems. Required for: shared-device scenarios, regulated industries with hard logout requirements, sites that explicitly want users to re-enter credentials each time.

| Question | Header | Options |
|----------|--------|---------|
| What should happen at the IdP when a user signs out? | Logout mode | Local logout only (Recommended, server default) — clear Power Pages session; user stays signed in at the IdP, Federated logout — also sign user out at the IdP so they have to re-authenticate |

**If "Local logout only"**: do nothing further. Skip writing `RPInitiatedLogout` and `PostLogoutRedirectUri` site settings in Phase 8.1 — the server defaults handle it correctly (both default to `false`/unset).

**If "Federated logout"**: this requires TWO pieces of configuration that MUST go together. Setting only `RPInitiatedLogout=true` without `PostLogoutRedirectUri` leaves users stranded on the IdP's "you have been signed out" page — confirmed via HAR analysis on a live Entra External ID site.

Step 1 — collect the post-logout redirect URI (default to the site root):

| Question | Options |
|----------|---------|
| Where should users land after they sign out at the IdP? (Defaults to the site home page. Use a different URL if you have a dedicated "signed out" page.) | *(free text, defaulted to `{SITE_URL}/`)* |

Validate the value: must be a fully-qualified URL on the same host as `SITE_URL`. St