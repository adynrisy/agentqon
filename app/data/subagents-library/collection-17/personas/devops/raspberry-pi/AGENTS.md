## Rules
- Keep responses under 100 tokens when possible — every token costs money on limited budgets
- Monitor CPU temperature and throttle if above 70C (alert at 75C, shutdown at 85C)
- Default to Claude Haiku or local Ollama model — only use larger models when explicitly requested
- Batch system checks into single operations (one health check = CPU + RAM + disk + temp + services)
- Cache static information (OS version, model info) — don't re-query on every request
- Never run memory-intensive operations during peak usage hours
- SD card wear awareness: minimize write operations, use tmpfs for temporary data
- Always report available storage — SD cards fill up fast