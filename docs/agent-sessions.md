# Agent sessions — agents that use tools

A chat answers. A mission deliberates. An **agent session** *acts*: an
agent works inside a folder you choose, using tools, until the job is
done — reading and writing files, running commands, checking the web,
with you approving the steps that change things.

## What a session can do

| Tool | What it does |
|---|---|
| `fs_list` / `fs_read` | Look around the workspace and read files |
| `fs_write` / `fs_edit` | Create files and make targeted edits |
| `fs_search` | Search file contents across the workspace |
| `shell_run` | Run a command (arguments only — no shell string tricks) |
| `web_read` | Read a web page's text |

Every tool is confined to the **workspace** — the folder you picked
when the session started. Paths that try to escape it are refused.

## Approvals: you hold the pen

By default, a session **stops and asks** before anything that changes
the world:

- writing or editing a file,
- any command that reaches the network.

Read-only inspection runs freely, so the agent can explore without
nagging you. You can switch approvals off for a session you trust
(**auto-approve**) — the session record still shows everything it did.
There is no mode in which actions happen invisibly: approved,
auto-approved, or refused, each tool call lands in the transcript.

## The two guards

An agent that *says* it did the work is not the same as an agent that
did it. Two guards check claims against tool evidence:

1. **Claim-over-failure guard.** If an agent claims success for
   something its own tool results show failed, the claim is rejected
   and the agent must deal with the failure.
2. **Verify-after-build guard.** If an agent builds something and
   declares it done without ever running or checking it, the session
   points at the gap and sends it back to verify — a small number of
   times, after which the session ends and says plainly that its
   result is **unverified**.

A session that ends `verified: false` is not a failed product — it is
an honest label. Treat its output as a draft you still owe a check.

## Step limits and control

Sessions run under a step cap (25 by default) so nothing wanders
forever. You can stop a session at any time; the transcript up to that
moment is kept, including any half-finished state — nothing pretends
the unfinished part finished.

## Sessions in hosted (accounts) mode

When AgentQon runs as a shared server, sessions are fenced harder:
workspaces must live inside the account's own area, web reads go
through the same channel rules as missions, and roles are re-checked
on every action — a person removed from a workspace loses their
session rights with it, not at some later sync.

## Good first sessions

- "Read this project's README and tell me what it promises — then
  check whether the code in `src/` keeps those promises."
- "Write a small script that renames these files by date, then run it
  on the copies in `test/` and show me the result."
- "Search this codebase for every place we handle passwords, and list
  them with file and line."

Start read-only. Graduate to writes when the transcripts have earned
your trust — they are written to be read.
