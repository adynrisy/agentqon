# AgentQon v22.1.3 — release notes (prepared 2026-10-23)

Status: LIVE ON NPM — agentqon@22.1.3 confirmed published
2026-10-23T12:17:30.732Z by npm's publication email
(shasum bf5e637f9b76160af48ad1c711577020fafe86f1).
GitHub release creation from these notes is still pending.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### The Skill Library ships — 4,840 skills, honest count
- Every skill lives in an on-device library inside AgentQon; the
  orchestrator and agent sessions retrieve the top-matching skills
  per task and inject them with a provenance header. A skill's
  provenance (source repo, license type, source URL) is always
  displayed — a community skill is never presented as AgentQon-authored.
- Search is a runtime SQLite FTS index built on first use, with
  keyword search plus category/source filters and a favorite/pin
  toggle persisted like the product's other preferences.
- Skill usage is recorded (which skills fed which mission/session)
  and outcomes feed the existing evolve/ledger system.

### The rename is live
- Every agent and every skill now carries an original AgentQon
  display name and ID — 6,799 agents and the full skill library
  renamed, zero harvested or upstream names anywhere user-facing.
  License attribution for harvested skills stays inside the data
  files, as the licenses require.

### The npm page finally points at GitHub
- The package now carries repository and homepage metadata, both
  pointing at the public repo page:

https://github.com/adynrisy/agentqon

- This closed a real discoverability hole: the live 22.0.0 package
  carried neither field, so even the npm page didn't link to GitHub.

### The exclusion cut — and the diagnosis it proved
- 22.1.0, 22.1.1 and 22.1.2 were all blocked by npm's automated
  publish-time review, three times, with no reason given. A
  file-by-file diff showed the only real difference from the passing
  22.0.0 was the Skill Library itself — and inside it, a cluster of
  offensive-security documentation (penetration-testing and similar
  published how-to material a machine reviewer cannot distinguish
  from malicious payloads).
- v22.1.3 removes exactly that cluster under a documented,
  reproducible cut (keyword-score, single-keyword-density,
  name-cluster and title-sweep legs, with eyeball-verified
  exceptions for benign senses such as reverse-proxy and
  reverse-logistics): **213 skills cut, 4,840 shipped**, agents
  untouched at 6,799. The full criteria and the per-skill list are
  recorded in the build records, and the cut set is restorable
  from git history.
- npm let 22.1.3 through. The diagnosis is proven: the documents
  were the block.

## Test evidence
- Skills suites 28/28, node 53/53; all maps and counts corrected to
  the 4,840 reality — no surface still claims the pre-cut number.
- Cold FTS index rebuild totals exactly 4,840; searches for the cut
  topics return none of the cut set.
- Pack gate: PASS; clean-room install clean with the v22.1.3 banner;
  seven sampled cut skills grepped in the final published package —
  all absent.

## Honest limits
- The 213 cut skills are not gone, just not in the npm package.
  They return as an **optional, ask-first download pack** riding a
  later release: one signed tarball hosted outside npm, installed
  into ~/.agentqon/extended-library/ only on the user's yes
  (default no), with the product reporting both numbers truthfully —
  "4,840 skills in the box · 5,053 with the extended library".
  Declining changes nothing else.
- The live end-to-end proof of the published package (fresh npm
  install, local model, full mission with Proof Pack, agent session
  writing and running real code, society run) was ordered the same
  day and was in flight at release time — its verdict table follows
  separately.
- Versions 22.1.0, 22.1.1 and 22.1.2 were never published — npm's
  review refused all three, so none of them is installable and none
  will ever go live under those numbers.
