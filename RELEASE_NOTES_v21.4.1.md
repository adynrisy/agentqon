# AgentQon v21.4.1 — release notes (drafted 2026-10-13)

Status: READY TO PUBLISH — agentqon@21.4.1 confirmed live on npm
2026-10-12T16:45:11Z (npm publication email; shasum
f66328bd446f0c63cc8f932c100be650a7af1bf5). This is a single-fix Windows
hotfix for the library-verification failure in 21.4.0; it replaces
21.4.0 for install purposes and is itself superseded by 21.4.2.

## Install / update

Stop AgentQon first, then:

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

- Library verification no longer demands uvloop and exceptiongroup on
  Windows. Root cause: 21.4.0's checker read its rules from the
  hash-locked requirements file, and the file's security hashes bled
  into the extracted platform markers (e.g. `sys_platform != "win32"`
  arrived with hash text glued on). The strict parser fell back to
  "demand the module", so setup stopped on Windows — while correctly
  leaving the existing install untouched.
- The fix is verified against the real hash-locked file: markers now
  extract cleanly for all 30 packages, and a simulated-Windows run of
  the shipped checker correctly skips uvloop and exceptiongroup while
  still demanding them on Linux.

## Test evidence
- CLI (node) suite: 18/18 at build time.
