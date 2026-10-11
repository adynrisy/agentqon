# Installation

OmniMind AI ships as a single npm package that contains the terminal harness
**and** the full platform server. One install command sets up both.

## Requirements

| Requirement | Version | Why |
|---|---|---|
| Node.js | ≥ 18 | Runs the `omnimind` terminal harness |
| Python | ≥ 3.11 | Runs the local platform server (in OmniMind's own virtual environment — your system Python is never modified) |

You don't need to install these by hand on most systems: the standalone
installers (below) detect what's missing and offer to install it through your
package manager. With a plain `npm install -g`, OmniMind checks on first run
and prints exact, per-OS guidance if something is missing.

## The npm way (all platforms)

```sh
npm install -g omnimind-ai
omnimind
```

The first `omnimind` run:

1. Copies the platform into `~/.omnimind/server/<version>/`
2. Builds a private Python environment at `~/.omnimind/venv` and installs the
   platform's libraries into it (one-time, under a minute on most machines)
3. Starts the local server on http://127.0.0.1:8000 (or the next free port)
4. Opens the web UI and drops you into the chat

Your data — missions, connections, encrypted keys — lives in
`~/.omnimind/data` and survives updates.

## Windows

Open PowerShell:

```powershell
npm install -g omnimind-ai
omnimind
```

If Node.js is missing, install it first with:

```powershell
winget install OpenJS.NodeJS.LTS
```

…and Python with:

```powershell
winget install Python.Python.3.12
```

(Reopen PowerShell after installing so the new PATH is picked up.)

## macOS

```sh
npm install -g omnimind-ai
omnimind
```

Missing runtimes via Homebrew:

```sh
brew install node python@3.12
```

## Linux

```sh
npm install -g omnimind-ai
omnimind
```

Most distributions carry a new-enough Python; for Node.js ≥ 18 use your
distribution's packages or the NodeSource setup for your distro.

## Verify the install

```sh
omnimind doctor
```

Doctor prints a styled checklist: runtimes, installed libraries, server
state, agent roster, and — importantly — whether a **model is connected**.
OmniMind has no mock mode: missions need a real model. See
[Connecting models](connecting-models.md).

## Updating

```sh
npm install -g omnimind-ai@latest
omnimind setup
```

`omnimind update` prints the same instructions in the terminal. Your data
directory is untouched by updates.

## Uninstalling

```sh
npm uninstall -g omnimind-ai
```

To remove everything including your data, also delete the `~/.omnimind`
folder (on Windows: `%USERPROFILE%\.omnimind`).

## Environment switches

| Variable | Effect |
|---|---|
| `NO_COLOR=1` | Plain terminal output (also automatic when output is piped) |
| `OMNIMIND_NO_OPEN=1` | Never auto-open the browser |
| `OMNIMIND_SKIP_SETUP=1` | Skip the automatic setup step on first run |
