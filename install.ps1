# AgentQon — installer (Windows PowerShell)
#
#   irm https://raw.githubusercontent.com/adynrisy/agentqon/main/install.ps1 | iex
#
# ONE command installs EVERYTHING AgentQon needs:
#   1. Node.js >= 18 and Python 3.12 exactly — when missing (or the wrong
#      version), they
#      are installed via winget (when available), with every system
#      change announced before it runs, and the session PATH refreshed
#      afterwards so the new tools are usable immediately.
#   2. The `agentqon` terminal harness (npm).
#   3. All of AgentQon's Python libraries — the installer runs
#      `agentqon setup`, which builds a private venv in ~/.agentqon and
#      pip-installs the platform's requirements.txt into it, then
#      verifies every library imports.
# When it finishes, the machine is fully ready — just run:  agentqon
#
# Switches:
#   -NoSetup     install only the harness; libraries install on first run
# Environment:
#   AGENTQON_SKIP_SETUP=1              same as -NoSetup (OMNIMIND_* also works)
#   AGENTQON_CLI_TARBALL=<path-or-url> install from a packed .tgz instead
#   AGENTQON_SOURCE=<dir>              install from a local directory
param(
    [switch]$NoSetup
)
$ErrorActionPreference = 'Stop'

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-NodeOk {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return $false }
    $major = 0
    try { $major = [int](node -p "parseInt(process.versions.node.split('.')[0], 10)") } catch { $major = 0 }
    return $major -ge 18
}

function Find-Python {
    # Returns the name of the first Python that is exactly 3.12, or $null.
    # The packaged payload is compiled for CPython 3.12 and setup refuses
    # every other version, so this installer hunts 3.12 ONLY.
    # (The Microsoft Store stub python.exe fails the version probe, as it should.)
    foreach ($py in @('python3.12', 'python', 'python3')) {
        if (Get-Command $py -ErrorAction SilentlyContinue) {
            & $py -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $py }
        }
    }
    return $null
}

function Fail-Guidance([string]$What) {
    Write-Host "x $What is required, and this installer cannot install it automatically here." -ForegroundColor Red
    Write-Host '  Install it manually, then re-run this installer:'
    Write-Host '    Node.js >= 18 : winget install OpenJS.NodeJS.LTS   (or https://nodejs.org/)'
    Write-Host '    Python 3.12: winget install Python.Python.3.12  (tick "Add python to PATH", or https://www.python.org/downloads/)'
    exit 1
}

$skipSetup = $NoSetup -or ($env:AGENTQON_SKIP_SETUP -eq '1') -or ($env:OMNIMIND_SKIP_SETUP -eq '1')
$cliTarball = if ($env:AGENTQON_CLI_TARBALL) { $env:AGENTQON_CLI_TARBALL } else { $env:OMNIMIND_CLI_TARBALL }
$sourceDir = if ($env:AGENTQON_SOURCE) { $env:AGENTQON_SOURCE } else { $env:OMNIMIND_SOURCE }
$winget = Get-Command winget -ErrorAction SilentlyContinue

Write-Host 'AgentQon — installer' -ForegroundColor Magenta
Write-Host ''

# ---- 1. runtimes ----
if (Test-NodeOk) {
    Write-Host "OK Node.js $(node --version)" -ForegroundColor Green
} else {
    Write-Host 'x Node.js >= 18 not found (or too old).' -ForegroundColor Red
    if (-not $winget) { Fail-Guidance 'Node.js >= 18' }
    Write-Host 'Installing Node.js LTS via winget (OpenJS.NodeJS.LTS)…' -ForegroundColor Cyan
    winget install --id OpenJS.NodeJS.LTS --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Host 'x winget failed to install Node.js.' -ForegroundColor Red; Fail-Guidance 'Node.js >= 18' }
    Refresh-Path
    if (Test-NodeOk) { Write-Host "OK Node.js $(node --version)" -ForegroundColor Green }
    else { Write-Host 'x Node.js is still not available after the winget install.' -ForegroundColor Red; Fail-Guidance 'Node.js >= 18' }
}

$pyBin = Find-Python
if ($pyBin) {
    Write-Host "OK Python $(& $pyBin -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') ($pyBin)" -ForegroundColor Green
} else {
    Write-Host 'x Python 3.12 not found.' -ForegroundColor Red
    if (-not $winget) { Fail-Guidance 'Python 3.12' }
    Write-Host 'Installing Python 3.12 via winget (Python.Python.3.12)…' -ForegroundColor Cyan
    winget install --id Python.Python.3.12 --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Host 'x winget failed to install Python.' -ForegroundColor Red; Fail-Guidance 'Python 3.12' }
    Refresh-Path
    $pyBin = Find-Python
    if ($pyBin) { Write-Host "OK Python $(& $pyBin -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') ($pyBin)" -ForegroundColor Green }
    else { Write-Host 'x Python 3.12 is still not available after the winget install.' -ForegroundColor Red; Fail-Guidance 'Python 3.12' }
}
if ($pyBin) { $env:AGENTQON_PYTHON = $pyBin; $env:OMNIMIND_PYTHON = $pyBin }

# ---- 2. the harness (npm) ----
if ($cliTarball) {
    Write-Host "Installing agentqon from tarball: $cliTarball"
    npm install -g $cliTarball
} elseif ($sourceDir) {
    Write-Host "Installing agentqon from source: $sourceDir"
    npm install -g $sourceDir
} else {
    Write-Host 'Installing agentqon from npm…'
    npm install -g agentqon
}
if ($LASTEXITCODE -ne 0) { Write-Host 'x npm install failed.' -ForegroundColor Red; exit 1 }

# ---- 3. AgentQon's Python libraries (agentqon setup) ----
if ($skipSetup) {
    Write-Host ''
    Write-Host 'Skipping the library install (-NoSetup). It will happen on first'
    Write-Host 'run instead — or any time with:  agentqon setup'
    Write-Host ''
    Write-Host 'OK AgentQon harness installed.' -ForegroundColor Cyan
    Write-Host '  Run agentqon — first run installs the libraries and starts your server.' -ForegroundColor Magenta
} else {
    Write-Host ''
    Write-Host 'Installing AgentQon''s Python libraries — this runs `agentqon setup`:'
    Write-Host 'copies the platform into ~/.agentqon, builds a private Python venv and'
    Write-Host 'pip-installs the platform''s requirements.txt into it, then verifies'
    Write-Host 'that every library imports.'
    agentqon setup
    if ($LASTEXITCODE -ne 0) { Write-Host 'x `agentqon setup` failed — see the output above.' -ForegroundColor Red; exit 1 }
    Write-Host ''
    Write-Host 'OK AgentQon installed — all required libraries are in place.' -ForegroundColor Cyan
    Write-Host '  Now just run:'
    Write-Host ''
    Write-Host '    agentqon' -ForegroundColor Magenta
    Write-Host ''
    Write-Host '  It starts your local server and opens the workspace.'
    Write-Host '  (agentqon doctor verifies runtimes, libraries and server health.)'
}
