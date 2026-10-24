# AgentQon v22.5.0 — release notes (prepared 2026-10-24)

Status: STAGED ON NPM — built and verified, awaiting the owner's
approval to publish. It ships after v22.4.0 is approved; until then,
`agentqon@latest` remains v22.1.3.

## Install / update

agentqon stop

npm install -g agentqon@latest

agentqon setup

agentqon

## What changed

### Free-roam — a task helper for your whole machine, without harming
- Agent sessions used to work inside one locked folder. A session
  now starts with a **home**: open AgentQon inside a project folder
  and that folder becomes the session's home project — no setup,
  no paths to type. A session created with an explicit workspace
  keeps it; the home only applies when none is given.
- **Reads roam.** The agent can read files anywhere on your
  machine — notes in another folder, a file on the desktop, a log
  three folders away — because real tasks are never confined to
  one folder. Reading harms nothing; it is how the helper
  understands the job. It can never read AgentQon's own files:
  its data and its install are off-limits, and a link pointing
  into them is refused, not followed.
- **Writes stay under your law.** Inside the home project, the
  agent writes freely, as before. Outside it, it may change only
  one specific file you approve — and the approval names that
  exact file in plain words, *"write outside your project:
  /path/to/that/file"*. The approval rides the existing approval
  machinery, and the law outranks even auto-approve mode: an
  unapproved outside write is refused twice over — once at the
  approval gate, once at execution. Permission is per file, never
  per folder; a second outside file asks again. Denial feeds the
  agent a plain-words refusal, and the file is never touched.
- **AgentQon's own files are never modified**, even with a
  would-be approval naming them — the refusal is absolute. There
  is no delete tool for agents at all; deletion remains shell
  only, under the approval rule below.
- On a shared or hosted setup, nothing changes: the old
  workspace confinement stays exactly as it was. Free-roam is
  for your own machine.

### Mission Open, fixed
- Opening a mission used to scroll the page to the very top — a
  leftover from the old layout, where the detail card sat at the
  top of Advanced. Since the redesign, the detail card lives in
  the Missions view *below* the mission list, so on a long list
  the opened mission sat entirely below the screen: the button
  looked dead while the detail was rendered, invisible, beneath
  it. Measured on a 15-mission list, the detail's top edge sat
  903 pixels down with the page scrolled to zero.
- Opening a mission now scrolls the detail card itself into view.
  Every open path was click-tested on the fixed build — long and
  short lists, completed and draft missions, chat chips, and the
  "Run the world" link — with zero page errors. The Skills and
  Worlds detail views were checked too and are not bitten by the
  same pattern.

## Gates (the build's own record)
- Free-roam feature suite: 25/25. Agent-loop suite, unedited:
  24/24. Adjacent session-driving suites: 152/152. UI spot check:
  12/12. CLI suite: 59/59.
- Integration gate on the frozen tree: full unit suite 727/727,
  CLI suite 59/59, package gate passed (19,211 files, identity
  "agentqon", payload Python 3.12), and a clean-room install that
  boots and reports "terminal harness v22.5.0".

## Honest limits
- **The shell is approval-governed, not hard-jailed.** In local
  mode the shell runs commands as your own user, starting in the
  home project. Mutating and network commands park for your
  approval; a strict read-only allowlist runs free. But the
  classifier is a screen on the command's shape, not a
  filesystem jail: a command *you approve* — a remove, or an
  interpreter one-liner — can modify or delete files anywhere
  you can, including outside the home project and including
  AgentQon's own files. The Write Law is enforced by mechanism
  for file writes, and by your approval plus the agent's written
  instructions for the shell. That is the nature of an approved
  command, not a hidden loophole, and it is stated here rather
  than hidden.
- **The tool remembers its start folder until restart.** The
  server is a long-running daemon: it keeps the folder it was
  started from. Open it in a second project while the server
  runs and it keeps the first home until you stop and start
  again.
- The Windows fallback for granted writes (no file-descriptor
  walk, so the proven path is written directly) is code-mirrored
  from the existing confined writer but was only exercised on
  Linux in this build's gates; the packaged start-from-a-folder
  flow was covered by tests, not by a live packaged boot.
- A society run completing on a real external model remains the
  standing unproven item from earlier releases; this release does
  not claim it.
