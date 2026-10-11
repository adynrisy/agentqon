# Security policy

## Reporting a vulnerability

If you find a security issue in AgentQon, please report it privately
rather than opening a public issue: use GitHub's **Report a
vulnerability** button under this repository's Security tab, and include
reproduction steps and your `agentqon doctor` output (it contains no
keys by design).

You can expect an acknowledgement within a few days. Confirmed issues
are fixed in the next npm release and credited in its notes unless you
prefer otherwise.

## Scope notes

- AgentQon stores provider keys in an envelope-encrypted vault in
  `~/.agentqon/data`. Reports about that vault's design or
  implementation are especially welcome.
- AgentQon has no mock mode and ships no default credentials; anything
  that looks like a baked-in key or a silent network call to an
  unexpected host is a bug — please report it.
- Never include real API keys or tokens in a report.
