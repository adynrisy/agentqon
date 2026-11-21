### Auto-Remediation Rules
\```
auto_remediate:
  - trigger: "container_exited"
    action: "docker restart"
    max_retries: 3
    backoff: "exponential"  # 30s, 60s, 120s
  - trigger: "disk_above_90%"
    action: "cleanup_routine"
    targets: ["docker_images", "old_logs", "tmp_files"]
  - trigger: "process_zombie"
    action: "kill_and_restart"
  - trigger: "ssl_expiry_7d"
    action: "certbot_renew"
\```

### Schedule
\```
schedule:
  health_check: "*/5 * * * *"      # every 5 minutes
  disk_cleanup: "0 3 * * *"         # daily 3am
  weekly_report: "0 9 * * 1"        # Monday 9am
\```

## Rules
- NEVER delete user data — only logs, caches, temp files, and unused Docker images
- Always log what was done and why before taking action
- Stop auto-remediating after 3 failed attempts — escalate to human
- Disk cleanup must preserve last 7 days of logs
- Container restarts use exponential backoff: 30s -> 60s -> 120s
- If a service fails 3 times in 1 hour, mark it as "needs human" and stop retrying
- Include before/after metrics in every remediation report
- Weekly reports should include uptime percentage, incidents, and disk trend