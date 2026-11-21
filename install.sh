#!/bin/sh
# AgentQon — installer
#
#   curl -fsSL https://raw.githubusercontent.com/adynrisy/agentqon/main/install.sh | sh
#
# ONE command installs EVERYTHING AgentQon needs:
#   1. Node.js >= 18 and Python 3.12 exactly — when missing (or the wrong
#      version), they
#      are installed through your system package manager (apt-get, dnf,
#      pacman, or Homebrew on macOS). Every system change is announced
#      BEFORE it runs; sudo is used only when you are not root.
#      On macOS without Homebrew, the installer prints Homebrew's
#      official install command and stops — it never runs it for you.
#   2. The `agentqon` terminal harness (npm).
#   3. All of AgentQon's Python libraries — the installer runs
#      `agentqon setup`, which builds a private venv in ~/.agentqon and
#      pip-installs the platform's requirements.txt into it, then
#      verifies every library imports.
# When it finishes, the machine is fully ready — just run:  agentqon
#
# Flags:
#   --no-setup   install only the harness; libraries install on first run
#   --dry-run    print every command that WOULD run; change nothing
#   --help       show this help
# Environment (AGENTQON_* preferred; the OMNIMIND_* spellings remain
# accepted as a legacy fallback):
#   AGENTQON_SKIP_SETUP=1              same as --no-setup
#   AGENTQON_CLI_TARBALL=<path-or-url> install from a packed .tgz instead
#   AGENTQON_SOURCE=<dir>              install from a local directory
set -eu
export DEBIAN_FRONTEND=noninteractive

DRY_RUN=0
SKIP_SETUP=0
[ "${AGENTQON_SKIP_SETUP:-${OMNIMIND_SKIP_SETUP:-0}}" = "1" ] && SKIP_SETUP=1
CLI_TARBALL="${AGENTQON_CLI_TARBALL:-${OMNIMIND_CLI_TARBALL:-}}"
SOURCE_DIR="${AGENTQON_SOURCE:-${OMNIMIND_SOURCE:-}}"
for arg in "$@"; do
  case "$arg" in
    --no-setup) SKIP_SETUP=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --help|-h)
      sed -n '2,31p' "$0" 2>/dev/null || echo "AgentQon installer — see the header of install.sh"
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then V='\033[38;5;141m'; C='\033[38;5;51m'; R='\033[0m'; else V=''; C=''; R=''; fi

# run CMD... — announce the command, then execute it. In --dry-run mode
# it only prints ("  + cmd") and pretends success, so detection probes
# still run but NOTHING on the system is changed.
run() {
  if [ "$DRY_RUN" = "1" ]; then printf '  + %s\n' "$*"; return 0; fi
  printf '  -> %s\n' "$*"
  "$@"
}

# ---- privilege + package manager detection ----
if [ "$(id -u)" = "0" ]; then SUDO=""; HAVE_SUDO=1
elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; HAVE_SUDO=1
else SUDO=""; HAVE_SUDO=0; fi

OS="$(uname -s 2>/dev/null || echo unknown)"
MANAGER=""
case "$OS" in
  Darwin)
    if command -v brew >/dev/null 2>&1; then MANAGER=brew; fi ;;
  Linux)
    if command -v apt-get >/dev/null 2>&1; then MANAGER=apt
    elif command -v dnf >/dev/null 2>&1; then MANAGER=dnf
    elif command -v pacman >/dev/null 2>&1; then MANAGER=pacman
    fi ;;
esac

apt_update_once() {
  if [ "${APT_UPDATED:-0}" != "1" ]; then
    say "Refreshing package lists (apt-get update)…"
    run $SUDO apt-get update
    APT_UPDATED=1
  fi
}

# Fail with clear guidance when we cannot install a prerequisite.
cannot_install() {  # $1 = what is missing
  say "✗ $1 is required, and this installer cannot install it automatically here."
  case "$OS" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        say "  Homebrew is the supported route on macOS, and it is not installed."
        say "  This installer will not run Homebrew's installer for you — run it yourself:"
        say "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        say "  …or install Node.js >= 18 and Python 3.12 from https://nodejs.org/ and https://www.python.org/downloads/ — then re-run this installer."
      else
        say "  Try:  brew install node python@3.12   — then re-run this installer."
      fi ;;
    Linux)
      if [ -n "$MANAGER" ] && [ "$HAVE_SUDO" != "1" ]; then
        say "  A package manager ($MANAGER) exists, but you are not root and sudo is not available."
      else
        say "  No supported package manager (apt-get / dnf / pacman) was found."
      fi
      say "  Install manually, then re-run this installer:"
      say "    Debian/Ubuntu: sudo apt install nodejs npm python3.12 python3.12-venv"
      say "    Fedora:        sudo dnf install nodejs python3"
      say "    Arch:          sudo pacman -S nodejs npm python"
      say "    (Node.js must be >= 18 and Python must be exactly 3.12 — the AgentQon payload is built for CPython 3.12; see https://nodejs.org/ if your distro's Node is older.)" ;;
    *)
      say "  Install Node.js >= 18 (https://nodejs.org/) and Python 3.12 (https://www.python.org/downloads/), then re-run this installer." ;;
  esac
  exit 1
}

# ---- Node.js >= 18 ----
node_major() { node -p "parseInt(process.versions.node.split('.')[0], 10)" 2>/dev/null || echo 0; }
node_ok() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && [ "$(node_major)" -ge 18 ]
}
apt_node_major() {  # major version apt would install for nodejs; 0 if none/unknown
  v="$(apt-cache policy nodejs 2>/dev/null | awk '/Candidate:/ {print $2}' \
    | sed -e 's/^[0-9]*://' -e 's/[^0-9].*//' | head -n 1)"
  case "$v" in ''|*[!0-9]*) echo 0 ;; *) echo "$v" ;; esac
}

install_node() {
  case "$MANAGER" in
    apt)
      if [ "$(apt_node_major)" -ge 18 ]; then
        say "Installing Node.js via apt-get (nodejs + npm)…"
        apt_update_once
        run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm
      else
        say "apt's Node.js is older than 18 (or unknown) — using the OFFICIAL NodeSource"
        say "setup script, which adds the NodeSource apt repository, then installing"
        say "Node.js 20 LTS via apt-get. The script being run is:"
        say "  https://deb.nodesource.com/setup_20.x"
        say "It is downloaded to a file and run from disk — never piped"
        say "straight into a shell (the Shield H2 rule the CLI follows)."
        if [ "$DRY_RUN" = "1" ]; then
          printf '  + NS_SETUP=$(mktemp)\n'
          printf '  + curl -fsSL https://deb.nodesource.com/setup_20.x -o "$NS_SETUP"\n'
          if [ -n "$SUDO" ]; then printf '  + %s -E bash "$NS_SETUP"\n' "$SUDO"
          else printf '  + bash "$NS_SETUP"\n'; fi
          printf '  + rm -f "$NS_SETUP"\n'
          printf '  + %s apt-get install -y nodejs\n' "$SUDO"
        else
          NS_SETUP="$(mktemp)"
          curl -fsSL https://deb.nodesource.com/setup_20.x -o "$NS_SETUP"
          if [ -n "$SUDO" ]; then run $SUDO -E bash "$NS_SETUP"
          else run bash "$NS_SETUP"; fi
          rm -f "$NS_SETUP"
          run $SUDO apt-get install -y nodejs
        fi
      fi ;;
    dnf)    say "Installing Node.js via dnf (nodejs)…";    run $SUDO dnf install -y nodejs ;;
    pacman) say "Installing Node.js via pacman (nodejs + npm)…"; run $SUDO pacman -S --noconfirm --needed nodejs npm ;;
    brew)   say "Installing Node.js via Homebrew (node)…"; run brew install node ;;
    *)      cannot_install "Node.js >= 18" ;;
  esac
  hash -r 2>/dev/null || true
}

# ---- Python exactly 3.12 ----
# The packaged payload is compiled for CPython 3.12 and setup refuses
# every other version, so this installer must hunt 3.12 ONLY — accepting
# 3.11/3.13 here used to strand users at the setup step.
PYBIN=""
find_python() {  # sets PYBIN to the first python that is exactly 3.12
  PYBIN=""
  for PY in python3.12 python3 python; do
    if command -v "$PY" >/dev/null 2>&1 && \
       "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
      PYBIN="$PY"; return 0
    fi
  done
  return 1
}

install_python() {
  case "$MANAGER" in
    apt)
      say "Installing Python via apt-get (python3, python3-venv, python3-pip)…"
      apt_update_once
      run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip
      if [ "$DRY_RUN" != "1" ] && ! find_python; then
        # Distro python3 is not 3.12 (e.g. Ubuntu 22.04 ships 3.10):
        # install the explicitly-versioned 3.12 package when apt offers it.
        for cand in python3.12; do
          if apt-cache policy "$cand" 2>/dev/null | grep -q 'Candidate: [0-9]'; then
            say "Distro python3 is not 3.12 — installing $cand (+ $cand-venv) via apt-get…"
            run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "$cand" "$cand-venv"
            break
          fi
        done
      fi ;;
    dnf)    say "Installing Python via dnf (python3)…";    run $SUDO dnf install -y python3 ;;
    pacman) say "Installing Python via pacman (python)…"; run $SUDO pacman -S --noconfirm --needed python ;;
    brew)   say "Installing Python via Homebrew (python@3.12)…"; run brew install python@3.12 ;;
    *)      cannot_install "Python 3.12" ;;
  esac
  hash -r 2>/dev/null || true
}

say "${V}AgentQon${R} — installer"
[ "$DRY_RUN" = "1" ] && say "(dry run — printing every command that WOULD run; changing nothing)"
say ""

# ---- 1. runtimes ----
needs_sudo() { [ "$MANAGER" = "apt" ] || [ "$MANAGER" = "dnf" ] || [ "$MANAGER" = "pacman" ]; }

if node_ok; then
  say "✓ Node.js $(node --version)"
else
  say "✗ Node.js >= 18 not found (or too old) — installing it…"
  if [ -z "$MANAGER" ]; then cannot_install "Node.js >= 18"; fi
  if needs_sudo && [ "$HAVE_SUDO" != "1" ]; then cannot_install "Node.js >= 18"; fi
  install_node
  if [ "$DRY_RUN" = "1" ]; then say "  (dry run — Node.js check skipped after install)"
  elif node_ok; then say "✓ Node.js $(node --version)"
  else say "✗ Node.js is still not >= 18 after the install attempt."; cannot_install "Node.js >= 18"; fi
fi

if find_python; then
  say "✓ Python $("$PYBIN" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') ($PYBIN)"
else
  say "✗ Python 3.12 not found — installing it…"
  if [ -z "$MANAGER" ]; then cannot_install "Python 3.12"; fi
  if needs_sudo && [ "$HAVE_SUDO" != "1" ]; then cannot_install "Python 3.12"; fi
  install_python
  if [ "$DRY_RUN" = "1" ]; then say "  (dry run — Python check skipped after install)"
  elif find_python; then say "✓ Python $("$PYBIN" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') ($PYBIN)"
  else say "✗ Python 3.12 is still not available after the install attempt."; cannot_install "Python 3.12"; fi
fi
# Tell `agentqon setup` exactly which interpreter to use (matters when the
# distro python3 is older but a python3.12 sits next to it).
if [ -n "$PYBIN" ]; then
  export AGENTQON_PYTHON="${AGENTQON_PYTHON:-${OMNIMIND_PYTHON:-$PYBIN}}"
  export OMNIMIND_PYTHON="$AGENTQON_PYTHON"
fi

# ---- 2. the harness (npm) ----
if [ -n "$CLI_TARBALL" ]; then
  say "Installing agentqon from tarball: $CLI_TARBALL"
  run npm install -g "$CLI_TARBALL"
elif [ -n "$SOURCE_DIR" ]; then
  say "Installing agentqon from source: $SOURCE_DIR"
  run npm install -g "$SOURCE_DIR"
else
  say "Installing agentqon from npm…"
  run npm install -g agentqon
fi

# ---- 3. AgentQon's Python libraries (agentqon setup) ----
if [ "$SKIP_SETUP" = "1" ]; then
  say ""
  say "Skipping the library install (--no-setup). It will happen on first"
  say "run instead — or any time with:  agentqon setup"
else
  say ""
  say "Installing AgentQon's Python libraries — this runs \`agentqon setup\`:"
  say "copies the platform into ~/.agentqon, builds a private Python venv and"
  say "pip-installs the platform's requirements.txt into it, then verifies"
  say "that every library imports."
  if [ "$DRY_RUN" = "1" ]; then
    run agentqon setup
  else
    OMNI="$(command -v agentqon 2>/dev/null || true)"
    if [ -z "$OMNI" ]; then OMNI="$(npm prefix -g 2>/dev/null)/bin/agentqon"; fi
    run "$OMNI" setup
  fi
fi

say ""
if [ "$DRY_RUN" = "1" ]; then
  say "${C}✓ Dry run complete — nothing was changed.${R}"
elif [ "$SKIP_SETUP" = "1" ]; then
  say "${C}✓ AgentQon harness installed.${R}"
  say "  Run ${V}agentqon${R} — first run installs the libraries and starts your server."
else
  say "${C}✓ AgentQon installed — all required libraries are in place.${R}"
  say "  Now just run:"
  say ""
  say "    ${V}agentqon${R}"
  say ""
  say "  It starts your local server and opens the workspace."
  say "  (${C}agentqon doctor${R} verifies runtimes, libraries and server health.)"
fi
