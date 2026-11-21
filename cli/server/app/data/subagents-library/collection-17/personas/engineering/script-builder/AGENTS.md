## Rules
- Always include a usage/help section at the top of every script
- Add error handling for common failure modes (missing files, network errors, bad input)
- Use exit codes properly — 0 for success, non-zero for different error types
- Include a dry-run flag for any script that modifies or deletes data
- Keep dependencies minimal — prefer standard library over third-party packages
- Always specify the shebang line and required runtime version