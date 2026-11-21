# Platform behavior — plain-words rules for AgentQon

This document states, in plain words, how the harness behaves in the
cases users actually hit. Where code enforces a rule, the module is
named. Nothing here is aspirational: if a behavior is not implemented,
it says so.

## Settings

### Deterministic precedence (enforced by app/platform_config.py)

Highest to lowest:

1. **Admin hard limits** — set by the operator. Nothing below may
   loosen them.
2. **Project settings** — for one project folder.
3. **User settings** — for one signed-in user.
4. **Built-in defaults** — the fallback.

Rule: a lower level may only be MORE restrictive than the level above
it, never looser. A value that tries to loosen an admin limit is
refused with a field-specific message naming the field and the limit —
never silently clamped, because a silent clamp would hide the fact
that the operator's intent was not honoured.

### Validation, previews, snapshots, rollback, reset, import/export

- A bad value names the field and the valid range, in plain words
  (for example: "research_max_fetches must be between 0 and 50").
- Before a change is applied, a preview shows exactly which fields
  change and which surfaces they affect.
- Every change creates a new configuration version; the previous
  version is kept.
- Every mission records the configuration version it ran under
  (per-mission snapshot). Later setting changes never rewrite it.
- Rollback restores a previous version, requires a reason, and is
  written to the audit log. Rollback is never silent.
- Reset to defaults changes ONLY the configuration file. Project
  files, mission records and snapshots are NOT deleted or changed.
  The reset states this scope before it runs.
- Import/export uses a validated JSON format. Exports redact secret
  values (shown as hidden markers); importing a redacted marker is
  refused — the real value must be entered again.
- Sensitive settings are redacted wherever settings are shown.
- Configuration migration from older formats is preserved as-is;
  this build does not change migration behaviour.

### Setting changes and running missions

**Setting changes affect NEW missions only.** A mission already
running keeps the configuration it started with (its per-mission
snapshot). This is stated in the change preview and in the mission
record. A mission is never reconfigured mid-run by a settings edit.

## Product behavior (each case, stated plainly)

- **Closing the app mid-mission.** The mission is durable: it lives
  in the queue and the mission record, not in the browser tab. Closing
  the tab does not cancel it. Reopening the app shows the mission in
  its current state.
- **Background continuation.** Work continues on the server/worker
  after the UI closes, because execution is server-side. The browser
  tab is a viewer, not the engine. (If the server itself stops, queued
  work waits until it restarts; nothing is silently dropped.)
- **Expired provider key.** The user sees a plain explanation — which
  provider, that its key no longer works, and what to do next
  (reconnect the provider with a fresh key) — never a raw error, code
  or JSON.
- **Changing model mid-mission.** Not silently: a mission runs under
  the model it started with. Switching models starts new work under
  the new model; the record shows which model produced which part.
  (Model-switch notification wording follows the configured fallback
  policy and is never silent.)
- **Cancel during a tool run.** The cancel stops the mission from
  starting further steps. A tool call already sent to a provider may
  still complete on the provider's side; the harness does not pretend
  otherwise, and the record says what stopped and what may have
  completed.
- **Resume with the same configuration.** A resumed mission reuses its
  per-mission snapshot where one exists. If the snapshot is missing
  (missions created before snapshots existed), the record says so
  instead of claiming the same configuration.
- **Failed / partial deliverable.** A partial result is labelled
  PARTIAL wherever it is shown, with what is missing. It is never
  presented as complete.
- **Why this model was selected.** Wherever a model is shown, the
  routing reason is shown with it (the routing preview): which model
  was chosen and why, in plain words.
- **Why cost exceeded the estimate.** Cost overruns are explained
  from the estimator's records: estimated vs actual cost, and which
  part grew (more calls, longer outputs, retries). The estimate
  itself is labelled estimated, never presented as a bill.
- **Optimization made an answer worse — restoring original behavior.**
  Roll back the configuration version that carried the optimization
  (rollback requires a reason and is recorded), or restore the
  template/skill version from before. The previous version is kept
  precisely so this is possible.
- **Data stored after a mission ends.** The mission record, its
  verdict, its usage totals and its per-mission snapshot are kept as
  the mission's record. Provider secrets are never stored in mission
  records. Retention and deletion follow the mission-deletion rule
  below.
- **Mission deletion.** Before confirmation, deletion states exactly
  what it removes (the mission record, its tasks, its outputs) and
  what it does not remove (usage history totals, configuration
  snapshots that other missions reference). Deletion is confirmed,
  never silent.
- **Communicating uncertainty.** An uncertain result says so in plain
  words, with the uncertainty visible next to the result — not buried
  in a detail panel. Model confidence is never presented as a
  calibrated probability.

## Cost dashboard

Per-agent, per-provider and cache-savings breakdowns come from
app/platform_usage.py. Verified billing figures and estimated figures
are labelled separately on every surface. Cache savings are estimated
against a stated baseline: what the same requests would have cost with
no cache at all at the same prices. Savings are not a bill reduction
unless the provider bills that way, and the record says so.

## Model evaluation history

Per-model success rate, latency and cost over time come from
app/platform_history.py, fed by self-test and run records. A model
with fewer than five runs is labelled "too few to judge" rather than
given a confident percentage.

## Workflow templates

Templates (app/platform_templates.py) save the plan shape, agent roles
and verification steps of a successful pattern. They never store a
previous run's outputs: instantiating a template starts fresh work.

## Disaster recovery — backup and restore process

1. Export: produce a state backup (app/platform_backup.py) for one
   tenant. It contains configuration versions, templates, model
   history, plugin records and experiment summaries.
2. Keep the export somewhere safe. It contains NO provider secrets
   and NO live queue rows — that is deliberate.
3. Restore: validate the bundle first (format, version, checksum).
   Show the restore preview — what will be replaced — and only then
   apply it. A bundle that fails validation is refused outright;
   there is no partial restore.
4. After a restore, reconnect providers with fresh credentials.
5. A backup is only trusted once a restore has been tried: an
   untested backup is never described as safe. (Restore-testing is
   the operator's step; the module refuses corrupt bundles so a bad
   restore cannot masquerade as success.)

## Plugin framework

Extensions (app/platform_plugins.py) install without modifying core
code. They declare their permissions and tools; undeclared use is
refused. Core security permissions can never be granted, even if
declared — the whole install is refused. Extensions are scoped to one
tenant and can never reach another tenant's data. Note the honest
limit: extensions run in the host process behind permission checks;
this is bounded, auditable trust in operator-chosen code, not process
isolation, and it is not claimed as such.

## Distributed execution

Multiple workers run over the existing durable queue
(app/platform_workers.py over app/store.py). Claims carry the worker
id as owner, so the queue's lease, heartbeat and fencing apply
unchanged. Workers check mission state, tenant and budget from shared
records, so all workers agree. With one worker, behaviour is identical
to before: app/worker.py is unmodified.

## Multi-modal pass-through

Image/audio inputs reach the agent only where the seated model's
capability has been verified by probe (app/platform_multimodal.py).
Declared-but-unprobed support does not open the gate. Where the model
does not support the input, the harness says so plainly and produces
no result — it never invents a description of an image it did not see
or audio it did not hear.

## API

- **Rate limits** (app/platform_api.py): fixed windows per key.
  Breach errors are plain words with a wait time, never a bare code.
- **Webhook verification**: HMAC-SHA256 signature verification plus
  delivery dedup, as a tested module. Wiring to a live webhook
  endpoint is NOT_TESTED until a webhook system ships; no webhook
  system is faked here.
- **Deprecation**: a deprecated endpoint's response names its
  replacement and says it is deprecated.
- **Secret rotation**: rotate a credential while keeping the previous
  one valid for a grace period, then it stops. Rotation is recorded.
- **Graceful degradation**: an optional integration's failure
  (live research, chat-channel delivery, extended library, catalog
  refresh) degrades the mission with a plain-words explanation; the
  mission continues where safe. Core failures still fail honestly.

## Automatic improvement guardrails

Failures name their configuration version. Skill regressions, routing
comparisons, drift and cost+quality summaries come from
app/platform_improve.py. Cost and quality are always shown together.
A proposed configuration change is evaluated on reference tasks
before applying — evaluation recommends, it never applies. A model is
promoted for a category only on verified, representative evidence
(at least 5 verified runs across at least 3 distinct tasks); one
success is anecdote, not evidence, and the code refuses it as such.

## Society details

Participant state is versioned, never overwritten
(app/platform_society.py): received information, generated reactions,
confidence changes and stance changes are four separate histories
(the separation rule — the graph cannot exaggerate persuasion by
counting activity as stance change). Exposure is tracked per
participant; duplicate information is recognised as one exposure.
Rounds reference prior state instead of re-sending full history.
Population totals are checked for consistency. Replay records seed +
configuration + simulation mode (individual, batched, deterministic
or hybrid); a replay involving a live model is labelled as not exactly
reproducible, honestly. Output is shown in three labelled sections —
assumptions, generated reactions, calculated statistics — never
blended into one confident story.

## UI polish

The W7 block in apps/web/index.html implements the §8 rules and the
UI polish list (shortcuts with a documented help panel, focus
management, disabled-state explanations, field-edit undo, searchable
dropdowns, persistent resizable panels, accessible labels on new
controls, empty states, tooltips with delay and viewport clamping,
inline errors near affected controls, relative + absolute timestamps,
consistent money/number formatting, truncation indicators, expandable
outputs, copyable identifiers with clipboard-failure handling, stable
sorting, pagination helpers, estimated-vs-verified badges, keyboard
navigation, screen-reader announcements, visible focus indicators,
reduced-motion support, no color-only status, text alternatives for
visualizations, and the society graph legend, filters, why-changed
inspection, simplified-mode warning, assumptions explanation and
dense-graph clustering). Contrast bar: text uses the existing Verdict
Room bone-on-carbon palette; the focus indicator is the verdict-lime
outline, which is also a shape/position signal, never colour alone.
Live-browser verification of the UI was not available in this build
role; UI items are verified by static + unit evidence (see the report).

## What this build does NOT do

- No npm staging (owner's staging hold, 2026-10-24 — nothing stages
  without Zohaib's explicit word naming exactly what stages).
- No new runtime dependencies.
- No changes to the Shield, tenant confinement, the free-roam Write
  Law, display names or content-derived IDs.
- Webhook end-to-end wiring: NOT_TESTED (verifier module only).
- File-operations micro-layer: reserved for the dedicated audit
  builder's report; nothing is built from it here until ordered.
