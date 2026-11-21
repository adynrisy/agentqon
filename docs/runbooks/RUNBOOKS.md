# Runbooks

## Run the stack locally
1. PostgreSQL up; `OMNIMIND_DATABASE_URL` set (or omit for SQLite dev).
2. API: `.venv/bin/uvicorn app.main:app --port 8080`
3. Worker (when `OMNIMIND_WORKER_MODE=external`): `.venv/bin/python -m app.worker`
4. Optional local model: Ollama serving on 127.0.0.1:11434.
5. Optional OmniRoute sidecar: `node omniroute.mjs` with its own
   DATA_DIR on :20128; create a gateway key in its dashboard; add it in
   OmniMind as an `omniroute_gateway` connection and Validate.

## A tenant says "my mission failed: connection …"
1. Open the mission: `ConnectionUnavailable`/`ConnectionCallFailed`
   events name the category (auth_failed, rate_limited, …) — never a
   provider body.
2. Check the connection state; re-Validate (model list only, no billable
   call). auth_failed → ask the tenant to Rotate the key.
3. Budget failures show `BudgetExceeded`: reservations already released;
   partial candidates are preserved for review.

## Kill switch drill
`POST /v1/admin/kill-switch {"on": true}` with the admin key stops new
runs (503 `KILL_SWITCH_ACTIVE`) and makes workers requeue without
consuming attempts. Flip off to resume; both flips are audited.

## Dead-letter replay
`GET /v1/jobs?status=dead`, fix the cause, `POST /v1/jobs/{id}/replay`.
Never edit job rows by hand.

## Vault key rotation
Set `OMNIMIND_VAULT_KEY_V2`, set `OMNIMIND_VAULT_KEY` to the same value,
restart; new writes use v2, old values still decrypt. Re-encrypt lazily
on next rotate per connection. Losing the master key = credentials are
unrecoverable by design; tenants re-enter keys.
