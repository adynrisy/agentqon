# Support

AgentQon is built and maintained by one maker. Support is honest
about that: no ticket queue theatre, no paid tier to jump the line —
the product is free, and so is the help.

## Where to get help

- **Bugs and feature requests** — open an issue in this repository.
  It is the front door for everything product-shaped.
- **Questions and how-do-I** — an issue marked as a question is fine,
  or check the [docs index](docs/README.md) first; the common ground
  is covered in [Troubleshooting & FAQ](docs/troubleshooting.md).
- **Security issues** — never a public issue. Use GitHub's **Report a
  vulnerability** button under this repository's Security tab, as
  described in [SECURITY.md](SECURITY.md).

## What to include (this is what makes help fast)

1. `agentqon doctor` output — it carries versions, runtimes, roster
   and model state, and **no keys by design**.
2. Your `agentqon --version` and your operating system.
3. What you did, what you expected, and what happened instead —
   the exact error words, copied, beat any summary.
4. If a model is involved: which connection kind (local server or API
   key), and your [model self-test](docs/model-self-test.md) result —
   WORKING/NOT WORKING stamps settle most model mysteries in one look.

Do not paste API keys, vault files, or tokens into any report, ever.

## Before you write

Three checks solve most reports before they're filed:

- **Is the running server the version you think it is?** Update in
  the exact order — `agentqon stop`, install, `agentqon setup`,
  `agentqon` — an old server left running keeps serving the old code.
- **Does the model actually answer?** A connection is not proof; the
  self-test is.
- **Is the provider throttling you?** A 429 means wait, not broken —
  the mission's status line says so in plain words.

## Business, press, and creators

Reviewing AgentQon, featuring it, or writing about it? Everything you
need is public: this repository, the npm page, and the guides above.
For direct contact, open an issue marked "business" and it will reach
the maker. There is no paid placement and no review embargo game —
the product is free; try it the way your audience would.
