# AgentQon v22.2.0 — release notes (prepared 2026-10-24)

Status: LIVE ON NPM (status updated 2026-10-28 — approved by the
owner and published).

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### The Extended Library — the 213 cut skills return, outside npm
- v22.1.3 cut exactly 213 security-research skills from the npm
  package to pass npm's automated review (see those notes for the
  full diagnosis). This release brings them back the way that was
  promised there: **one optional, signed download** that npm never
  carries.
- The pack is a single tarball, **signed with Ed25519**; the
  signature and the per-file hashes are verified *before* anything
  installs. A tampered tarball or a wrong signature is refused in
  plain words and installs nothing.
- **Ask-first, always.** Interactive `agentqon setup` offers the
  pack once, in plain words, with the default answer **No**.
  Declining downloads nothing and changes nothing. The pack can be
  installed or removed later at any time:
  `agentqon skills extended [--install|--remove]`.
- It lands only in `~/.agentqon/extended-library` — inside your
  data home, never inside the npm package — and a running server
  picks an install or a removal up without a restart.
- **Honest two-number counts.** With the pack absent, the product
  reports **4,840 skills**; with it, **4,840 in the box · 5,053
  with the Extended Library**. Both numbers, both ways, on every
  surface — the boxed count is never quietly dressed up as the
  with-pack count.

## Honest limits
- Until the pack files are uploaded to their release host, the
  download address has nothing behind it and the setup offer says
  so and carries on — declining and failing-to-fetch both leave a
  fully working 4,840-skill install.
- The pack is a convenience, not a recommendation: the skills in it
  are security-research material, which is exactly why npm does not
  carry them and why the offer defaults to No.
