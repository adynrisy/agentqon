<!--
Source: microsoft/power-platform-skills
Path: plugins/code-apps/skills/deploy/SKILL.md
License: MIT
URL: https://github.com/microsoft/power-platform-skills/tree/HEAD/plugins/code-apps/skills/deploy/SKILL.md
-->

**📋 Shared Instructions: [shared-instructions.md](${PLUGIN_ROOT}/shared/shared-instructions.md)** - Cross-cutting concerns.

# Deploy

Builds and deploys the app in the current directory to Power Platform.

## Workflow

1. Check Memory Bank → 2. Build → 3. Deploy → 4. Update Memory Bank

---

### Step 1: Check Memory Bank

Check for `memory-bank.md` in the project root. If found, read it for the project name and environment. If not found, proceed — the project may have been created without the plugin.

### Step 2: Build

```bash
npm run build
```

If the build fails:

- **TS6133 (unused import)**: Remove the unused import and retry.
- **Other TypeScript errors**: Report the error with the file and line number and stop. Do not deploy a broken build.

Verify `dist/` exists with `index.html` before continuing.

### Step 3: Deploy

Ask the user: _"Ready to deploy to [environment name]? This will update the live app."_ Wait for explicit confirmation before proceeding.

> Resolve the CLI first (see [cli-binary.md](${PLUGIN_ROOT}/shared/cli-binary.md)): run as `$PA app push` (`npx --no-install pa …`), never a bare `pa`. On `power-apps`-only projects, translate to `power-apps push`.

```bash
pa app push
```

Capture the app URL from the output if present.

If deploy fails, report the error and stop — do not retry silently. Common fixes:

- Auth error / token expired → `pa auth logout` (or `power-apps logout` on `power-apps`-only projects), then retry — the CLI will re-prompt browser login.
- Environment mismatch → update `environmentId` in `power.config.json` to the correct value and retry.

### Step 4: Update Memory Bank

If `memory-bank.md` exists, update:

- Last deployed timestamp
- App URL (if captured)
