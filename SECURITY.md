# Security policy

## Reporting a vulnerability — privately, please

If you find a security problem in AgentQon, **do not open a public
issue.** Report it privately instead:

- **Email:** bussiness.agentqon@gmail.com — this is the direct line to
  the maker for security reports.
- Or use GitHub's **Report a vulnerability** button under this
  repository's Security tab, if you prefer to stay inside GitHub.

**What to include** (as much as you have):

- What the problem is, in plain words.
- The AgentQon version (`agentqon --version`, or the version line in
  your `agentqon doctor` output) and your operating system.
- Steps to reproduce it — the exact commands or clicks.
- Your `agentqon doctor` output. It contains versions and connection
  state only — no keys, by design.
- **Never include real API keys, tokens, or vault contents in a
  report.** If a key of yours was exposed, rotate it at your provider
  first, then write.

## What happens next

You can expect an acknowledgement within a few days. Confirmed issues
are fixed in the next npm release and credited in its notes, unless
you prefer to stay anonymous.

## Scope notes

- AgentQon stores provider keys in an envelope-encrypted vault in
  `~/.agentqon/data`, and its payload is protected by AgentQon Shield
  (a signed integrity manifest over an encrypted, compiled payload).
  Reports about the vault's or Shield's design or implementation are
  especially welcome.
- AgentQon has no mock mode and ships no default credentials; anything
  that looks like a baked-in key, or a silent network call to an
  unexpected host, is a bug — please report it.
- An honest limit, stated up front: a local install cannot be made
  literally untouchable by its own machine's owner. Shield guarantees
  package integrity, encrypted data at rest, and resistance to outside
  attackers. Reports are judged against those guarantees.
