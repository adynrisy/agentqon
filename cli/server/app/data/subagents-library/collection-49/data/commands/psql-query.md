---
name: psql-query
description: Run ad-hoc PostgreSQL analytics queries against dev/test database
---

# PostgreSQL Analytics

Run analytics queries against the local database using psql or `mix run`.

## Rules

- NEVER run against production
- Use READ-ONLY queries (SELECT only)
- For complex analysis, use `Repo.query/2` in a Mix task
- Format results as ASCII tables or pipe to `column -t`

## Tidewave Integration

If Tidewave MCP is available, prefer it over psql/mix run:

- `mcp__tidewave__execute_sql_query` for direct SQL queries
- `mcp__tidewave__project_eval` for Ecto-based queries via `Repo.query!/2` or `Repo.all/1`

Fall back to psql/mix run only if Tidewave is not detected.

## Workflow

1. Check if Tidewave MCP is available — if yes, use `mcp__tidewave__execute_sql_query`
2. Otherwise, detect database URL from `config/dev.exs` or `DATABASE_URL`
3. Run query via: `psql $DATABASE_URL -c "YOUR QUERY" --expanded`
4. For Ecto: `mix run -e 'MyApp.Repo.query!("SELECT ...") |> IO.inspect()'`
5. Summarize results

## Example prompts

- "How many users signed up this week?"
- "Show me the slowest queries from pg_stat_statements"
- "What's the table size distribution?"
- "Show index usage stats"
