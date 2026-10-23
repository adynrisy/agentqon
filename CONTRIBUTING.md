# Contributing to AgentQon

## First, the honest part: the source code is private

AgentQon's source is not public, and this repository does not contain
it. This repo is the product's public home — **documentation, guides,
and issue tracking** — and the product itself ships as the
[`agentqon` npm package](https://www.npmjs.com/package/agentqon).

That means code pull requests can't be accepted here: there is no
public source tree to change. Please don't spend your time preparing
one. What genuinely helps is below.

## Ways to help

- **Report bugs** — open an issue with the Bug report template. The
  single most useful thing you can include is your `agentqon doctor`
  output (versions and connection state — no keys, by design).
- **Request features** — use the Feature request template; a concrete
  example mission beats an abstract wish every time.
- **Improve these docs** — pull requests against the guides in `docs/`
  and the README are welcome. Keep claims honest: if a behaviour is
  described here, it must be true of the shipped package.
- **Share Proof Packs** — a mission result, with its evidence trail,
  attached to an issue is the fastest way to show a quality problem —
  or a great result.
- **Security problems are different** — never file them as public
  issues. See [SECURITY.md](SECURITY.md) for the private route.

## Ground rules

- Be kind and specific; argue with drafts like our critic does —
  attack the work, never the person.
- No secrets in issues: never paste API keys, tokens, or vault
  contents. `agentqon doctor` output is safe by design.
