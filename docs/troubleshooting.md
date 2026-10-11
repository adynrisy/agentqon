# Troubleshooting & FAQ

## "Model not connected"

The most common first-run state — and a deliberate one. OmniMind has no
mock mode, so missions wait until a real model is connected. Fixes, in
order of ease:

1. **Ollama running?** `ollama pull qwen2.5`, then `omnimind doctor` —
   the model line should turn green.
2. **BYOK key added?** Web UI → Connections. Check the base URL and
   model name for typos.
3. **Env endpoint set?** The three `OMNIMIND_LLM_*` variables must be
   set in the shell that starts OmniMind (see
   [Connecting models](connecting-models.md)).
4. **Endpoint actually alive?** A configured-but-unreachable endpoint
   counts as not connected — by design.

## The server didn't start / port busy

OmniMind starts on port 8000 and **automatically takes the next free
port** if it's taken. `omnimind status` shows the real port, and
`omnimind open` always goes to the right place. `omnimind logs` shows
the server log if startup failed for another reason.

## Python errors on first run

OmniMind's current release needs **Python 3.12 exactly** and builds its
own environment in `~/.omnimind/venv`. The Shield-protected payload is
compiled for 3.12, so if your machine offers 3.11, 3.13 or no Python at
all, setup stops with a message naming the version it found and the one
it needs. Install 3.12 (Windows: `winget install Python.Python.3.12`;
macOS: `brew install python@3.12`), **close and reopen your terminal** so
the new Python is on the path, then run:

```sh
omnimind setup
```

If an earlier install already failed part-way (for example a Shield
integrity error), use `omnimind setup --repair` instead — it restores
the payload from the installed package and rotates the local credentials.

Setup verifies **every** required library imports cleanly in the
environment and says so; `omnimind doctor` includes a Libraries line if
you want to re-check later.

## "Shield: the bundled payload failed integrity verification"

That refusal is Shield working as designed — it will not run a bundle
whose files don't match the signed release manifest. Do not use that
copy; repair it from the current release instead:

```sh
npm install -g omnimind-ai@latest
omnimind setup --repair
omnimind
```

Known cause on Windows: releases up to 20.2.0 bundled 88 roster files
whose names contain characters Windows forbids (colons, a stray control
character), so those files could never be created on disk and the
manifest check correctly failed. **Fixed in 20.2.1** — the filenames are
sanitized and the manifest re-signed. If you still see this error on
20.2.1 or later, reinstall from npm as above and report the exact error
text with your version (`omnimind --version`).

## A mission failed — where's the answer?

There isn't one, and that's the guarantee working: a mission whose model
call fails ends **FAILED** with the reason attached (check
`omnimind missions` or the mission in the UI). OmniMind would rather show
you a failure than a fabricated success.

## Where is my data?

Everything lives in `~/.omnimind/`:

| Path | Contents |
|---|---|
| `~/.omnimind/data` | Missions, settings, encrypted vault |
| `~/.omnimind/venv` | The platform's Python environment |
| `~/.omnimind/server/<version>` | The installed platform |
| `~/.omnimind/config.json` | CLI config (owner-only permissions) |

Back up the `data` folder and you've backed up OmniMind.

## FAQ

**Is OmniMind free?**
The software is MIT-licensed and free. You bring the model: a local
Ollama model costs nothing; hosted providers charge their own rates, and
OmniMind shows every run's cost as a footnote before it can surprise you.

**Does my data leave my machine?**
Only to the model you connected. With Ollama, nothing leaves at all.

**How is this different from a chatbot wrapper?**
A wrapper gives you one model's first draft. OmniMind runs a
suitability-picked panel of specialist agents, attacks their drafts with
a critic, scores the survivors, tracks every agent's record, and hands
you the evidence trail (the Proof Pack) with the answer.

**Can multiple people use one deployment?**
Yes — hosted deployments support accounts with owner/admin/member/viewer
roles and per-tenant isolation of missions, keys and budgets.

**Why does the version list on npm show a `0.0.0-stage`?**
That was the placeholder created by npm's staged-publishing flow during
the first release. `latest` points at the real release; the placeholder
is harmless history.

**I found a bug / want a feature.**
Open an issue in this repository with your `omnimind doctor` output and
what you expected to happen.
