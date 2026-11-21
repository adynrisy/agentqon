<!--
Source: davidondrej/skills
Path: skills/agent-orchestration/self-archive/SKILL.md
License: MIT
URL: https://github.com/davidondrej/skills/tree/HEAD/skills/agent-orchestration/self-archive/SKILL.md
-->

# Self-archive

Act only on an explicit request to archive this thread. Never delete the thread or target unrelated threads.

1. Send a brief closing summary before running commands.
2. Archive the current thread and its children: `room-cli thread archive --self --json` (Local and Cloud).
3. If the command fails, report the error and keep the runtime running. Do not claim the thread was archived.
4. After success, run nothing else. Archiving stops the thread's runtime. In Cloud, the request is recorded durably and the app applies it when it next connects.

To restore the thread, the user can use Cloudroom's UI or run `room-cli thread unarchive <id> --json` on their computer.
