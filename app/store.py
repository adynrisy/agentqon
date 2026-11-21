"""AgentQon — persistence layer (textbook Ch.17).

Phase 2: PostgreSQL is the production store; SQLite remains the zero-setup
dev mode. Both sit behind the same repository interface, chosen by
AGENTQON_DATABASE_URL. Every read is tenant-scoped. Artifacts are
immutable: content is hash-addressed on disk and a new version is a new
record, never an overwrite. The jobs table is the durable queue (Ch.19):
missions are executed by workers claiming jobs with a lease, with bounded
retries, backoff, and a dead-letter state.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .domain import StateConflict, content_hash, new_id, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT,
  created_by TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
  contract TEXT NOT NULL, budget_limit REAL NOT NULL, spent REAL NOT NULL DEFAULT 0,
  selected_candidate_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS missions_tenant_idx ON missions(tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  contract TEXT NOT NULL, status TEXT NOT NULL, assigned_agent TEXT,
  output_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (
  agent_version_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  manifest TEXT NOT NULL, lifecycle TEXT NOT NULL,
  PRIMARY KEY (tenant_id, agent_version_id));
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, task_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL, agent_version_id TEXT NOT NULL, adapter_version TEXT NOT NULL,
  status TEXT NOT NULL, usage TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT,
  kind TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
  metadata TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluations (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, mission_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS critiques (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS worlds (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS societies (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS combos (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS combo_mappings (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS personas (
  tenant_id TEXT NOT NULL DEFAULT '', id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS agent_stats (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS memories (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS agent_sessions (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS skill_pins (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS duels (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS telegram_links (
  chat_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_codes (
  code TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_updates (
  update_id INTEGER PRIMARY KEY, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_outbox (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS telegram_duels (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS whatsapp_links (
  wa_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS whatsapp_codes (
  code TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS whatsapp_updates (
  wamid TEXT PRIMARY KEY, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS whatsapp_outbox (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS whatsapp_duels (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, aggregate_id TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_invocations (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  tool TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  invocation_id TEXT NOT NULL, preview TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_records (
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  category TEXT NOT NULL, amount_usd REAL NOT NULL, detail TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cost_ledger (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', phase TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'inference',
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER, success INTEGER NOT NULL DEFAULT 1,
  cost_usd REAL, cost_status TEXT NOT NULL DEFAULT 'known',
  price_source TEXT, price_effective_date TEXT, price_version INTEGER,
  attempt INTEGER NOT NULL DEFAULT 1, retry_of TEXT,
  partial INTEGER NOT NULL DEFAULT 0, fallback INTEGER NOT NULL DEFAULT 0,
  cached_hit INTEGER NOT NULL DEFAULT 0, reused_from TEXT,
  provider_request_id TEXT, event_key TEXT UNIQUE,
  currency TEXT NOT NULL DEFAULT 'USD', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS price_history (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, model TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', price_in REAL, price_out REAL,
  price_cached REAL, price_source TEXT NOT NULL DEFAULT '',
  effective_date TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget_events (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT NOT NULL, tenant_id TEXT NOT NULL, request_hash TEXT NOT NULL,
  mission_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, key));
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
  kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  available_at TEXT NOT NULL, lease_owner TEXT, lease_expires TEXT,
  last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs(status, available_at);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS connections (
  id TEXT NOT NULL, tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
  kind TEXT NOT NULL, provider_key TEXT NOT NULL, label TEXT NOT NULL,
  base_url TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft',
  masked_key TEXT NOT NULL DEFAULT '', allowed_models TEXT NOT NULL DEFAULT '[]',
  blocked_models TEXT NOT NULL DEFAULT '[]', budget_limit_usd REAL,
  routing_mode TEXT NOT NULL DEFAULT 'balanced', data_policy TEXT NOT NULL DEFAULT '{}',
  last_validated_at TEXT, last_error_category TEXT, models_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  headers TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, id));
CREATE TABLE IF NOT EXISTS credential_secrets (
  connection_id TEXT NOT NULL, tenant_id TEXT NOT NULL, key_version TEXT NOT NULL,
  ciphertext TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, connection_id));
CREATE TABLE IF NOT EXISTS catalog_models (
  tenant_id TEXT NOT NULL, connection_id TEXT NOT NULL, model_id TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '',
  capabilities TEXT NOT NULL DEFAULT '{}', price_in REAL, price_out REAL,
  price_source TEXT, price_status TEXT NOT NULL DEFAULT 'unknown',
  health TEXT NOT NULL DEFAULT 'unknown', first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL, deprecated INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, connection_id, model_id));
CREATE TABLE IF NOT EXISTS model_capability_results (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
  connection_id TEXT NOT NULL, model_id TEXT NOT NULL,
  capability TEXT NOT NULL, status TEXT NOT NULL,
  task_class TEXT, latency_ms INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS token_reconciliations (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
  session_id TEXT NOT NULL, model_id TEXT NOT NULL,
  estimated_input_tokens INTEGER, reported_input_tokens INTEGER,
  input_error INTEGER, input_error_pct REAL,
  estimated_output_tokens INTEGER, reported_output_tokens INTEGER,
  output_error INTEGER, output_error_pct REAL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_sync_runs (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, connection_id TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  models_count INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS route_decisions (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT, role TEXT,
  connection_id TEXT, model_id TEXT, mode TEXT NOT NULL,
  reason_codes TEXT NOT NULL DEFAULT '[]', filtered_counts TEXT NOT NULL DEFAULT '{}',
  snapshot_id TEXT, estimated_cost REAL, estimate_confidence TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget_reservations (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
  amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'reserved',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS platform_flags (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memberships (
  tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, user_id));
CREATE TABLE IF NOT EXISTS brain_keys (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '', key_hash TEXT UNIQUE NOT NULL,
  key_prefix TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  last_used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rate_counters (
  key TEXT NOT NULL, window_start INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key, window_start));
"""

# Columns added after the Phase 1/2 schema shipped; applied idempotently
# to pre-existing databases (CREATE TABLE IF NOT EXISTS does not alter).
_COLUMN_MIGRATIONS = [
    ("missions", "project_id", "TEXT"),
    ("missions", "reserved_usd", "REAL NOT NULL DEFAULT 0"),
    # Audit B9: monotonic insertion sequence for events — the
    # tiebreaker that makes list_events ordering (and its `after`
    # cursor) exact when several events share a created_at tick.
    ("events", "seq", "INTEGER"),
    # Custom headers (2026-10-26): persisted on the connection row so
    # headers sent at creation survive reload, validate, patch, and
    # restart — previously put_connection dropped them entirely.
    # Appended last, matching the CREATE TABLE column order, so the
    # positional put_connection tuple stays valid on both dialects.
    ("connections", "headers", "TEXT NOT NULL DEFAULT '{}'"),
]


def _future_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def error_summary(error: str | None, limit: int = 300) -> str:
    """One-line, client-safe error summary (v21.4 error hygiene).

    jobs.last_error is returned by GET /v1/jobs to any tenant member,
    so a full traceback there leaks file paths and internals. Store
    (and serve) only the summary: for a traceback, its final line —
    "ExceptionType: message"; for anything else, the text itself,
    truncated. Full tracebacks belong in the server log only."""
    text = (error or "").strip()
    if not text:
        return ""
    if "\n" in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        text = lines[-1] if lines else ""
    return text[:limit]


class Store:
    """One repository interface, two dialects.

    SQLite:  Store("/path/to.db", "/path/to/artifacts")
    Postgres: Store(pg_dsn="postgresql://...", artifacts_dir="/path/to/artifacts")
    """

    def __init__(self, db_path: Optional[str] = None, artifacts_dir: Optional[str] = None,
                 *, pg_dsn: Optional[str] = None):
        # Gap-security: remember where this store lives so connection
        # recovery can reopen it after a drop (dbguard.recover_connection).
        self.db_path = db_path
        self.pg_dsn = pg_dsn
        self.pg = bool(pg_dsn)
        self.artifacts_dir = Path(artifacts_dir or "data/artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        # RLock (not Lock) so Store.bulk() can hold the lock across a
        # whole multi-statement import while the importer's own _exec
        # calls re-enter it on the same thread.
        self._lock = threading.RLock()
        self._bulk_depth = 0
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self._conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        with self._lock:
            for stmt in SCHEMA.split(";\n"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    self._raw(stmt)
                except Exception as exc:
                    # Concurrent first boot (API + worker) races CREATE
                    # TABLE IF NOT EXISTS on Postgres: the catalog insert
                    # is not atomic, so the loser sees a unique violation
                    # on pg_type. That outcome is benign — the table now
                    # exists, which is all this statement wanted.
                    if self.pg and ("42P07" in str(exc) or "pg_type" in str(exc)
                                    or "already exists" in str(exc)):
                        continue
                    raise
            self._commit()
            self._apply_column_migrations()
            # Audit B9: the events(seq) index can only be created
            # once the migration above has added the column.
            try:
                self._raw("CREATE INDEX IF NOT EXISTS events_seq_idx"
                          " ON events(seq)")
                self._commit()
            except Exception:
                pass  # put_event's MAX(seq) stays correct, just slower
            self._migrate_legacy_personas()
            self._apply_brain_key_guard()
            self._apply_jobs_run_guard()

    def _apply_column_migrations(self):
        """Idempotently add columns introduced after a database was created."""
        for table, column, decl in _COLUMN_MIGRATIONS:
            if self.pg:
                # NOTE: called under self._lock from __init__ — use _raw
                # directly; _one/_all would try to re-acquire the lock.
                cur = self._raw(
                    "SELECT 1 AS x FROM information_schema.columns"
                    " WHERE table_name=? AND column_name=?", (table, column))
                if cur.fetchone() is None:
                    self._raw(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            else:
                cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()]
                if column not in cols:
                    self._raw(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            self._commit()

    def _apply_jobs_run_guard(self):
        """Audit B3: enforce 'at most one active mission_run job per
        mission' at the database level, mirroring the brain-key
        guard. Rows that already violate it are the pre-fix
        double-enqueue's product — two executions of one mission
        cancelling each other's tasks — so they are repaired first:
        the live run (a running job, else the oldest) is kept and
        younger duplicates are failed with the reason on the row.
        Then a partial unique index makes the invariant structural."""
        rows = self._all(
            "SELECT id, tenant_id, mission_id, status FROM jobs"
            " WHERE kind='mission_run' AND status IN ('queued','running')"
            " ORDER BY tenant_id, mission_id,"
            " CASE WHEN status='running' THEN 0 ELSE 1 END, created_at, id")
        seen: set = set()
        for r in rows:
            key = (r["tenant_id"], r["mission_id"])
            if key in seen:
                self._exec(
                    "UPDATE jobs SET status='failed', last_error=?,"
                    " updated_at=? WHERE id=?",
                    ("superseded duplicate mission_run job (audit B3 repair)",
                     now_iso(), r["id"]))
            else:
                seen.add(key)
        try:
            self._raw("CREATE UNIQUE INDEX IF NOT EXISTS"
                      " jobs_one_active_run_idx"
                      " ON jobs(tenant_id, mission_id, kind)"
                      " WHERE kind='mission_run'"
                      " AND status IN ('queued','running')")
            self._commit()
        except Exception:
            pass  # backstop only — enqueue_job's locked dedupe is primary

    def _table_exists(self, table: str) -> bool:
        if self.pg:
            row = self._one(
                "SELECT 1 AS x FROM information_schema.tables"
                " WHERE table_name=?", (table,))
        else:
            row = self._one(
                "SELECT 1 AS x FROM sqlite_master WHERE type='table'"
                " AND name=?", (table,))
        return row is not None

    def _migrate_legacy_personas(self):
        """v21.4: move persona briefs from the global `agency_personas`
        table (keyed by agent_id alone) into the tenant-scoped
        `personas` table. Roster/pack briefs stay global (tenant '').
        A legacy `custom.*` brief is attributed to the one tenant that
        holds the agent when exactly one does; when ownership is
        ambiguous it stays global, where the tenant fallback read still
        finds it — no brief is ever lost or silently reassigned.
        Idempotent: the legacy table is dropped at the end."""
        if not self._table_exists("agency_personas"):
            return
        rows = self._all("SELECT id, data FROM agency_personas")
        for r in rows:
            tenant = ""
            if r["id"].startswith("custom."):
                holders = self.agent_tenants(r["id"])
                if len(holders) == 1:
                    tenant = holders[0]
            self._upsert(
                "INSERT OR REPLACE INTO personas VALUES (?,?,?)",
                (tenant, r["id"], r["data"]),
                "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")
        self._exec("DROP TABLE IF EXISTS agency_personas")

    def _apply_brain_key_guard(self):
        """v21.4: enforce 'at most one active brain key per tenant' at
        the database level. Rows that already violate it (a past
        non-atomic rotation) are repaired first — all but the newest
        active key per tenant are revoked — then a partial unique
        index makes the invariant structural."""
        rows = self._all(
            "SELECT id, tenant_id FROM brain_keys WHERE revoked=0"
            " ORDER BY tenant_id, created_at DESC, id DESC")
        seen: set[str] = set()
        for r in rows:
            if r["tenant_id"] in seen:
                self._exec("UPDATE brain_keys SET revoked=1 WHERE id=?",
                           (r["id"],))
            else:
                seen.add(r["tenant_id"])
        try:
            self._raw("CREATE UNIQUE INDEX IF NOT EXISTS"
                      " brain_keys_one_active_idx ON brain_keys(tenant_id)"
                      " WHERE revoked=0")
            self._commit()
        except Exception:
            pass  # backstop only — create_brain_key's lock is primary

    # ------------------------------------------------------------- low-level
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def _raw(self, sql: str, params: tuple = ()):
        return self._conn.execute(self._sql(sql), params)

    def _commit(self):
        if self.pg:
            return  # psycopg runs with autocommit=True
        if self._bulk_depth:
            return  # deferred: the enclosing bulk() block commits once
        self._conn.commit()

    @contextmanager
    def bulk(self):
        """Run a block of writes as ONE commit (v20.1).

        _exec commits after every statement — right for single writes,
        ruinous for bulk imports: the 6,476-agent library roster is
        ~13k persona+manifest writes, i.e. ~13k fsyncs, which measured
        ~19 minutes on a real disk against 10 seconds in memory. Inside
        this block _commit defers and the block commits exactly once on
        success (and rolls back on error), so the final state is
        identical to the unbatched run — only commit granularity
        changes. The store lock is held for the whole block so no other
        thread's writes can interleave into the open transaction.
        Postgres (autocommit) gains nothing and is unaffected.
        """
        with self._lock:
            self._bulk_depth += 1
            try:
                yield self
            except Exception:
                self._bulk_depth -= 1
                if self._bulk_depth == 0 and not self.pg:
                    self._conn.rollback()
                raise
            self._bulk_depth -= 1
            if self._bulk_depth == 0:
                self._commit()

    def _exec(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._raw(sql, params)
            self._commit()
            return cur

    def _one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with self._lock:
            cur = self._raw(sql, params)
            row = cur.fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._raw(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _upsert(self, insert_sql: str, params: tuple, pg_conflict_sql: str) -> None:
        """insert_sql uses SQLite 'INSERT OR REPLACE'. For Postgres the caller
        supplies the ON CONFLICT tail appended to a plain INSERT."""
        if self.pg:
            plain = insert_sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            self._exec(plain + " " + pg_conflict_sql, params)
        else:
            self._exec(insert_sql, params)

    # ------------------------------------------------------------- missions
    def create_mission(self, tenant_id: str, created_by: str, title: str,
                       contract: dict, budget_limit: float,
                       project_id: Optional[str] = None) -> dict:
        # v21.3: budgets are not user-facing. The stored limit is always
        # the internal runaway ceiling, whatever the caller passed —
        # the argument is accepted for signature compatibility only.
        from .domain import MISSION_BUDGET_CEILING_USD
        budget_limit = MISSION_BUDGET_CEILING_USD
        mid = new_id("mis")
        ts = now_iso()
        self._exec(
            "INSERT INTO missions (id, tenant_id, created_by, title, status, contract,"
            " budget_limit, spent, created_at, updated_at, project_id)"
            " VALUES (?,?,?,?, 'draft', ?, ?, 0, ?, ?, ?)",
            (mid, tenant_id, created_by, title, json.dumps(contract), budget_limit, ts, ts,
             project_id))
        return self.get_mission(tenant_id, mid)

    def get_mission(self, tenant_id: str, mission_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM missions WHERE id=? AND tenant_id=?", (mission_id, tenant_id))
        if not row:
            return None
        row["contract"] = json.loads(row["contract"])
        return row

    def list_missions(self, tenant_id: str, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT * FROM missions WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                         (tenant_id, limit))
        for r in rows:
            r["contract"] = json.loads(r["contract"])
        return rows

    def set_mission_status(self, tenant_id: str, mission_id: str, status: str) -> None:
        self._exec("UPDATE missions SET status=?, updated_at=? WHERE id=? AND tenant_id=?",
                   (status, now_iso(), mission_id, tenant_id))

    def set_budget_limit(self, tenant_id: str, mission_id: str, limit_usd: float) -> None:
        """Change a mission's spending cap. Called ONLY from an explicit
        budget-approval decision (cost_engine.BudgetManager) — never
        automatically, never silently."""
        self._exec("UPDATE missions SET budget_limit=?, updated_at=? WHERE id=? AND tenant_id=?",
                   (limit_usd, now_iso(), mission_id, tenant_id))

    def set_mission_selected(self, tenant_id: str, mission_id: str, candidate_id: str) -> None:
        self._exec("UPDATE missions SET selected_candidate_id=?, updated_at=? WHERE id=? AND tenant_id=?",
                   (candidate_id, now_iso(), mission_id, tenant_id))

    # W5 mission management (E1/E8): user-settable naming, and deletion
    # including retained data. rename_mission only changes the title
    # (every view already shows the stored title); delete_mission
    # removes the mission row plus every mission-owned row listed in
    # mission_mgmt.DELETION_SCOPE_TABLES and returns per-table counts.
    # Agent records, memories, and other missions are never touched.
    def rename_mission(self, tenant_id: str, mission_id: str, title: str) -> None:
        self._exec("UPDATE missions SET title=?, updated_at=? WHERE id=? AND tenant_id=?",
                   (title, now_iso(), mission_id, tenant_id))

    def delete_mission(self, tenant_id: str, mission_id: str) -> dict:
        tables = [
            ("tasks", "mission_id"), ("runs", "mission_id"),
            ("artifacts", "mission_id"), ("candidates", "mission_id"),
            ("evaluations", "mission_id"), ("critiques", "mission_id"),
            ("tool_invocations", "mission_id"), ("approvals", "mission_id"),
            ("usage_records", "mission_id"),
            ("idempotency_keys", "mission_id"),
        ]
        counts: dict[str, int] = {}
        for table, column in tables:
            cur = self._exec(
                f"DELETE FROM {table} WHERE tenant_id=? AND {column}=?",
                (tenant_id, mission_id))
            counts[table] = getattr(cur, "rowcount", 0) or 0
        # Events carry the mission as aggregate_id, not mission_id.
        cur = self._exec(
            "DELETE FROM events WHERE tenant_id=? AND aggregate_id=?",
            (tenant_id, mission_id))
        counts["events"] = getattr(cur, "rowcount", 0) or 0
        cur = self._exec("DELETE FROM missions WHERE id=? AND tenant_id=?",
                         (mission_id, tenant_id))
        counts["missions"] = getattr(cur, "rowcount", 0) or 0
        return counts

    def add_spend(self, tenant_id: str, mission_id: str, amount: float) -> None:
        self._exec("UPDATE missions SET spent = spent + ?, updated_at=? WHERE id=? AND tenant_id=?",
                   (amount, now_iso(), mission_id, tenant_id))

    # ------------------------------------------------------------- idempotency
    def find_idempotent(self, tenant_id: str, key: str) -> Optional[dict]:
        return self._one("SELECT * FROM idempotency_keys WHERE key=? AND tenant_id=?", (key, tenant_id))

    def save_idempotent(self, tenant_id: str, key: str, request_hash: str, mission_id: str) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO idempotency_keys VALUES (?,?,?,?,?)",
            (key, tenant_id, request_hash, mission_id, now_iso()),
            "ON CONFLICT (tenant_id, key) DO UPDATE SET request_hash=EXCLUDED.request_hash,"
            " mission_id=EXCLUDED.mission_id, created_at=EXCLUDED.created_at")

    # ------------------------------------------------------------- agents
    def upsert_agent(self, tenant_id: str, manifest: dict) -> str:
        avid = f"{manifest['agent_id']}@{manifest['version']}"
        self._upsert(
            "INSERT OR REPLACE INTO agents VALUES (?,?,?,?)",
            (avid, tenant_id, json.dumps(manifest), manifest.get("lifecycle", "active")),
            "ON CONFLICT (tenant_id, agent_version_id) DO UPDATE SET"
            " manifest=EXCLUDED.manifest, lifecycle=EXCLUDED.lifecycle")
        return avid

    def list_agents(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM agents WHERE tenant_id=?", (tenant_id,))
        out = []
        for r in rows:
            m = json.loads(r["manifest"])
            m["agent_version_id"] = r["agent_version_id"]
            m["lifecycle"] = r["lifecycle"]
            out.append(m)
        return out

    def agents_version(self, tenant_id: str) -> dict:
        """v21.4.2: a cheap roster version token for the UI's device
        cache. Derived entirely from the stored rows — total count,
        custom-agent count (custom agent_version_ids carry the
        `custom.` prefix by construction) and a SHA-256 over every
        row's id + lifecycle + raw manifest text, in id order. ANY
        registry mutation (reseed, import, custom create/edit/delete,
        lifecycle change) changes the token; an unchanged registry
        always yields the same one. The probe returns ~100 bytes so a
        warm client cache costs no roster download at all."""
        rows = self._all(
            "SELECT agent_version_id, lifecycle, manifest FROM agents"
            " WHERE tenant_id=? ORDER BY agent_version_id", (tenant_id,))
        h = hashlib.sha256()
        custom = 0
        for r in rows:
            if r["agent_version_id"].startswith("custom."):
                custom += 1
            h.update(r["agent_version_id"].encode("utf-8"))
            h.update(b"|")
            h.update((r["lifecycle"] or "").encode("utf-8"))
            h.update(b"|")
            h.update((r["manifest"] or "").encode("utf-8"))
            h.update(b"\n")
        return {"version": f"v{len(rows)}-{custom}-{h.hexdigest()[:20]}",
                "total": len(rows), "custom": custom}

    def set_agent_lifecycle(self, tenant_id: str, agent_version_id: str, lifecycle: str) -> None:
        self._exec("UPDATE agents SET lifecycle=? WHERE agent_version_id=? AND tenant_id=?",
                   (lifecycle, agent_version_id, tenant_id))

    def get_agent(self, tenant_id: str, agent_id: str) -> Optional[dict]:
        """One agent manifest by bare agent_id within the tenant."""
        for m in self.list_agents(tenant_id):
            if m.get("agent_id") == agent_id:
                return m
        return None

    def delete_agent(self, tenant_id: str, agent_version_id: str) -> bool:
        row = self._one(
            "SELECT agent_version_id FROM agents"
            " WHERE agent_version_id=? AND tenant_id=?",
            (agent_version_id, tenant_id))
        if row is None:
            return False
        self._exec("DELETE FROM agents WHERE agent_version_id=? AND tenant_id=?",
                   (agent_version_id, tenant_id))
        return True

    def agent_tenants(self, agent_id: str) -> list[str]:
        """Tenants holding any version of this agent_id (the persona
        migration and the legacy shared-brief cleanup use this to know
        whether any workspace still references the agent)."""
        rows = self._all("SELECT manifest, tenant_id FROM agents")
        return sorted({r["tenant_id"] for r in rows
                       if json.loads(r["manifest"]).get("agent_id") == agent_id})

    # ------------------------------------------------------------- tasks / runs
    def create_task(self, tenant_id: str, contract: dict) -> None:
        ts = now_iso()
        self._upsert(
            "INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
            (contract["task_id"], contract["mission_id"], tenant_id,
             json.dumps(contract), contract.get("status", "queued"), None, None, ts, ts),
            "ON CONFLICT (id) DO UPDATE SET contract=EXCLUDED.contract, status=EXCLUDED.status")

    def update_task(self, tenant_id: str, task_id: str, status: str,
                    assigned_agent: Optional[str] = None, output_artifact_id: Optional[str] = None) -> None:
        self._exec("UPDATE tasks SET status=?, assigned_agent=COALESCE(?, assigned_agent),"
                   " output_artifact_id=COALESCE(?, output_artifact_id), updated_at=?"
                   " WHERE id=? AND tenant_id=?",
                   (status, assigned_agent, output_artifact_id, now_iso(), task_id, tenant_id))

    def close_open_tasks(self, tenant_id: str, mission_id: str,
                         status: str = "failed") -> int:
        """Close every still-open task of a mission with the mission's
        own terminal status. Audit R (bug #3): when a run died mid-stage
        (adapter error, provider down), the mission row was marked
        failed but its tasks were left 'running'/'queued' forever —
        the detail view showed a dead mission with a live-looking
        research task and two queued successors. Terminal mission
        transitions must take their open tasks with them."""
        cur = self._exec(
            "UPDATE tasks SET status=?, updated_at=? WHERE mission_id=?"
            " AND tenant_id=? AND status IN ('queued', 'running')",
            (status, now_iso(), mission_id, tenant_id))
        return cur.rowcount if cur is not None else 0

    def list_tasks(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM tasks WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))
        for r in rows:
            r["contract"] = json.loads(r["contract"])
            r["kind"] = r["contract"].get("kind", "")
            r["description"] = r["contract"].get("description", "")
        return rows

    def create_run(self, tenant_id: str, mission_id: str, task_id: str,
                   agent_version_id: str, adapter_version: str, usage: dict) -> str:
        rid = new_id("run")
        self._exec("INSERT INTO runs VALUES (?,?,?,?,?,?, 'completed', ?, ?)",
                   (rid, mission_id, task_id, tenant_id, agent_version_id,
                    adapter_version, json.dumps(usage), now_iso()))
        return rid

    # ------------------------------------------------------------- artifacts (immutable)
    def put_artifact(self, tenant_id: str, mission_id: Optional[str], kind: str,
                     content: Any, metadata: dict) -> dict:
        h = content_hash(content)
        if self.pg:
            existing = self._all("SELECT * FROM artifacts WHERE tenant_id=? AND mission_id IS NOT DISTINCT FROM ?"
                                 " AND kind=? ORDER BY version DESC", (tenant_id, mission_id, kind))
        else:
            existing = self._all("SELECT * FROM artifacts WHERE tenant_id=? AND mission_id IS ? AND kind=?"
                                 " ORDER BY version DESC", (tenant_id, mission_id, kind))
        version = (existing[0]["version"] + 1) if existing else 1
        aid = new_id("art")
        path = self.artifacts_dir / f"{h}.json"
        if not path.exists():
            path.write_text(json.dumps(content, indent=2, default=str))
        rec_meta = dict(metadata)
        rec_meta["version"] = version
        self._exec("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                   (aid, tenant_id, mission_id, kind, version, h, json.dumps(rec_meta), now_iso()))
        return {"artifact_id": aid, "version": version, "content_hash": h, "kind": kind}

    def get_artifact(self, tenant_id: str, artifact_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM artifacts WHERE id=? AND tenant_id=?", (artifact_id, tenant_id))
        if not row:
            return None
        path = self.artifacts_dir / f"{row['content_hash']}.json"
        row["content"] = json.loads(path.read_text()) if path.exists() else None
        row["metadata"] = json.loads(row["metadata"])
        return row

    def list_artifacts(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT id, kind, version, content_hash, metadata, created_at FROM artifacts"
                         " WHERE tenant_id=? AND mission_id=? ORDER BY created_at", (tenant_id, mission_id))
        for r in rows:
            r["metadata"] = json.loads(r["metadata"])
        return rows

    # ------------------------------------------------------------- candidates / evaluations / critiques / sources
    def put_candidate(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?)",
            (data["candidate_id"], data["mission_id"], tenant_id, json.dumps(data), now_iso()),
            "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data")

    def get_candidate(self, tenant_id: str, mission_id: str, candidate_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM candidates WHERE id=? AND mission_id=? AND tenant_id=?",
                        (candidate_id, mission_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_candidates(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM candidates WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))
        return [json.loads(r["data"]) for r in rows]

    def put_evaluation(self, tenant_id: str, mission_id: str, data: dict) -> None:
        self._exec("INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
                   (data["evaluation_id"], data["candidate_id"], mission_id, tenant_id,
                    json.dumps(data), now_iso()))

    def list_evaluations(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM evaluations WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))
        return [json.loads(r["data"]) for r in rows]

    def put_critique(self, tenant_id: str, mission_id: str, data: dict) -> None:
        self._exec("INSERT INTO critiques VALUES (?,?,?,?,?)",
                   (data["critique_id"], mission_id, tenant_id, json.dumps(data), now_iso()))

    def list_critiques(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM critiques WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))
        return [json.loads(r["data"]) for r in rows]

    def put_source(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO sources VALUES (?,?,?)",
            (data["source_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    # ------------------------------------------------------------- worlds (Phase 7)
    # ------------------------------------------------- agent stats (v13)
    def put_agent_stat(self, tenant_id: str, data: dict) -> None:
        key = f"{data['agent_version_id']}|{data['task_type']}"
        self._upsert(
            "INSERT OR REPLACE INTO agent_stats VALUES (?,?,?)",
            (key, tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def list_agent_stats(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM agent_stats WHERE tenant_id=?",
                         (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    def delete_agent_stats(self, tenant_id: str, agent_version_id: str) -> int:
        rows = self._all("SELECT id FROM agent_stats WHERE tenant_id=?",
                         (tenant_id,))
        doomed = [r["id"] for r in rows
                  if r["id"].startswith(agent_version_id + "|")]
        for key in doomed:
            self._exec("DELETE FROM agent_stats WHERE id=? AND tenant_id=?",
                       (key, tenant_id))
        return len(doomed)

    # ------------------------------------------------- memories (v21)
    def put_memory(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO memories VALUES (?,?,?)",
            (data["memory_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_memory(self, tenant_id: str, memory_id: str) -> dict | None:
        row = self._one("SELECT data FROM memories WHERE tenant_id=? AND id=?",
                        (tenant_id, memory_id))
        return json.loads(row["data"]) if row else None

    def list_memories(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM memories WHERE tenant_id=?",
                         (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    def delete_memory(self, tenant_id: str, memory_id: str) -> bool:
        cur = self._exec("DELETE FROM memories WHERE tenant_id=? AND id=?",
                         (tenant_id, memory_id))
        return (cur.rowcount or 0) > 0

    # ------------------------------------------------- agent sessions (v22)
    def put_agent_session(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO agent_sessions VALUES (?,?,?)",
            (data["id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_agent_session(self, tenant_id: str, session_id: str) -> dict | None:
        row = self._one("SELECT data FROM agent_sessions WHERE tenant_id=? AND id=?",
                        (tenant_id, session_id))
        return json.loads(row["data"]) if row else None

    def list_agent_sessions(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM agent_sessions WHERE tenant_id=?",
                         (tenant_id,))
        out = [json.loads(r["data"]) for r in rows]
        out.sort(key=lambda s: s.get("created_at", ""))
        return out

    # ------------------------------------------------- skill pins (v22.1)
    def set_skill_pin(self, tenant_id: str, skill_id: str, pinned: bool,
                      user_id: str = "") -> None:
        """A tenant's pinned (favourite) skills for the skill library
        (v22.1 S2). Pins are strictly per-tenant: one workspace's
        favourites never surface in another's browse/search views or
        retrieval ordering."""
        if pinned:
            self._upsert(
                "INSERT OR REPLACE INTO skill_pins VALUES (?,?,?)",
                (skill_id, tenant_id,
                 json.dumps({"skill_id": skill_id, "user_id": user_id,
                             "pinned_at": now_iso()})),
                "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")
        else:
            self._exec("DELETE FROM skill_pins WHERE tenant_id=? AND id=?",
                       (tenant_id, skill_id))

    def list_skill_pins(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM skill_pins WHERE tenant_id=?",
                         (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    def pinned_skill_ids(self, tenant_id: str) -> set[str]:
        return {p["skill_id"] for p in self.list_skill_pins(tenant_id)}

    # ------------------------------------------------- duels (v15 arena)
    def put_duel(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO duels VALUES (?,?,?)",
            (data["duel_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_duel(self, tenant_id: str, duel_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM duels WHERE id=? AND tenant_id=?",
                        (duel_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_duels(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM duels WHERE tenant_id=?",
                         (tenant_id,))
        duels = [json.loads(r["data"]) for r in rows]
        return sorted(duels, key=lambda d: d.get("created_at", ""),
                      reverse=True)

    # ------------------------------------------------- schedules (v15)
    def put_schedule(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO schedules VALUES (?,?,?)",
            (data["schedule_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_schedule(self, tenant_id: str, schedule_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM schedules WHERE id=? AND tenant_id=?",
                        (schedule_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_schedules(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM schedules WHERE tenant_id=?",
                         (tenant_id,))
        schedules = [json.loads(r["data"]) for r in rows]
        return sorted(schedules, key=lambda s: s.get("created_at", ""))

    def list_all_schedules(self) -> list[dict]:
        """Cross-tenant scan — worker-side only (the scheduler tick is a
        platform process, never exposed through the tenant API)."""
        rows = self._all("SELECT data FROM schedules", ())
        return [json.loads(r["data"]) for r in rows]

    def delete_schedule(self, tenant_id: str, schedule_id: str) -> bool:
        cur = self._exec("DELETE FROM schedules WHERE id=? AND tenant_id=?",
                         (schedule_id, tenant_id))
        return (cur.rowcount or 0) > 0

    def claim_schedule_advance(self, tenant_id: str, schedule_id: str,
                               expected_next_run_at: str, claimed: dict) -> bool:
        """Audit B10: atomically replace a schedule row with its
        advanced form ONLY while the stored row's next_run_at still
        equals the value the caller read when it decided the
        schedule was due. Two worker processes ticking at once both
        read the due row; exactly one claim wins and fires — the
        loser gets False. next_run_at is compared inside the JSON
        blob with each dialect's own extraction."""
        if self.pg:
            cond = "data::jsonb ->> 'next_run_at' = ?"
        else:
            cond = "json_extract(data, '$.next_run_at') = ?"
        with self._lock:
            cur = self._raw(
                f"UPDATE schedules SET data=? WHERE tenant_id=? AND id=? AND {cond}",
                (json.dumps(claimed), tenant_id, schedule_id,
                 expected_next_run_at))
            self._commit()
            return (cur.rowcount or 0) == 1

    # ------------------------------------------------- telegram (v16)
    # The front door's state: chat->account links (keyed by chat id —
    # a chat belongs to exactly one account), one-time link codes,
    # webhook update dedupe, the result-push outbox, and the per-duel
    # message bookkeeping the vote reveal edits. All JSON-blob rows in
    # the style of schedules/duels above.
    def put_telegram_link(self, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO telegram_links VALUES (?,?)",
            (str(data["chat_id"]), json.dumps(data)),
            "ON CONFLICT (chat_id) DO UPDATE SET data=EXCLUDED.data")

    def get_telegram_link(self, chat_id) -> Optional[dict]:
        row = self._one("SELECT * FROM telegram_links WHERE chat_id=?",
                        (str(chat_id),))
        return json.loads(row["data"]) if row else None

    def get_telegram_link_for_user(self, tenant_id: str,
                                   user_id: str) -> Optional[dict]:
        rows = self._all("SELECT data FROM telegram_links", ())
        for r in rows:
            link = json.loads(r["data"])
            if link.get("tenant_id") == tenant_id \
                    and link.get("user_id") == user_id:
                return link
        return None

    def delete_telegram_link(self, chat_id) -> bool:
        cur = self._exec("DELETE FROM telegram_links WHERE chat_id=?",
                         (str(chat_id),))
        return (cur.rowcount or 0) > 0

    def put_telegram_code(self, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO telegram_codes VALUES (?,?,?)",
            (data["code"], data["tenant_id"], json.dumps(data)),
            "ON CONFLICT (code) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,"
            " data=EXCLUDED.data")

    def get_telegram_code(self, code: str) -> Optional[dict]:
        row = self._one("SELECT * FROM telegram_codes WHERE code=?", (code,))
        return json.loads(row["data"]) if row else None

    def delete_telegram_code(self, code: str) -> None:
        self._exec("DELETE FROM telegram_codes WHERE code=?", (code,))

    def delete_telegram_codes_for(self, tenant_id: str, user_id: str) -> int:
        rows = self._all(
            "SELECT data FROM telegram_codes WHERE tenant_id=?", (tenant_id,))
        n = 0
        for r in rows:
            rec = json.loads(r["data"])
            if rec.get("user_id") == user_id:
                self.delete_telegram_code(rec["code"])
                n += 1
        return n

    def mark_telegram_update(self, update_id: int) -> bool:
        """Record a webhook update id. True = first time seen (process
        it); False = Telegram retry of an update already handled."""
        existing = self._one(
            "SELECT update_id FROM telegram_updates WHERE update_id=?",
            (int(update_id),))
        if existing:
            return False
        self._exec("INSERT INTO telegram_updates VALUES (?,?)",
                   (int(update_id), now_iso()))
        return True

    def put_telegram_outbox(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO telegram_outbox VALUES (?,?,?)",
            (data["outbox_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_telegram_outbox(self, tenant_id: str,
                            outbox_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM telegram_outbox WHERE id=? AND tenant_id=?",
                        (outbox_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_telegram_outbox(self, status: Optional[str] = None) -> list[dict]:
        """Cross-tenant scan — worker-side only (the outbox tick is a
        platform process, like list_all_schedules)."""
        rows = self._all("SELECT data FROM telegram_outbox", ())
        out = [json.loads(r["data"]) for r in rows]
        if status is not None:
            out = [o for o in out if o.get("status") == status]
        return sorted(out, key=lambda o: o.get("created_at", ""))

    def put_telegram_duel(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO telegram_duels VALUES (?,?,?)",
            (data["duel_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_telegram_duel(self, tenant_id: str, duel_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM telegram_duels WHERE id=? AND tenant_id=?",
                        (duel_id, tenant_id))
        return json.loads(row["data"]) if row else None

    # ------------------------------------------------- whatsapp (v17)
    # The WhatsApp front door's state, mirroring the telegram block:
    # wa_id->account links (a WhatsApp number belongs to exactly one
    # account), one-time link codes, webhook dedupe by Meta's wamid,
    # the window-aware result-push outbox, and per-duel bookkeeping.
    def put_whatsapp_link(self, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO whatsapp_links VALUES (?,?)",
            (str(data["wa_id"]), json.dumps(data)),
            "ON CONFLICT (wa_id) DO UPDATE SET data=EXCLUDED.data")

    def get_whatsapp_link(self, wa_id) -> Optional[dict]:
        row = self._one("SELECT * FROM whatsapp_links WHERE wa_id=?",
                        (str(wa_id),))
        return json.loads(row["data"]) if row else None

    def get_whatsapp_link_for_user(self, tenant_id: str,
                                   user_id: str) -> Optional[dict]:
        rows = self._all("SELECT data FROM whatsapp_links", ())
        for r in rows:
            link = json.loads(r["data"])
            if link.get("tenant_id") == tenant_id \
                    and link.get("user_id") == user_id:
                return link
        return None

    def delete_whatsapp_link(self, wa_id) -> bool:
        cur = self._exec("DELETE FROM whatsapp_links WHERE wa_id=?",
                         (str(wa_id),))
        return (cur.rowcount or 0) > 0

    def put_whatsapp_code(self, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO whatsapp_codes VALUES (?,?,?)",
            (data["code"], data["tenant_id"], json.dumps(data)),
            "ON CONFLICT (code) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,"
            " data=EXCLUDED.data")

    def get_whatsapp_code(self, code: str) -> Optional[dict]:
        row = self._one("SELECT * FROM whatsapp_codes WHERE code=?", (code,))
        return json.loads(row["data"]) if row else None

    def delete_whatsapp_code(self, code: str) -> None:
        self._exec("DELETE FROM whatsapp_codes WHERE code=?", (code,))

    def delete_whatsapp_codes_for(self, tenant_id: str, user_id: str) -> int:
        rows = self._all(
            "SELECT data FROM whatsapp_codes WHERE tenant_id=?", (tenant_id,))
        n = 0
        for r in rows:
            rec = json.loads(r["data"])
            if rec.get("user_id") == user_id:
                self.delete_whatsapp_code(rec["code"])
                n += 1
        return n

    def mark_whatsapp_update(self, wamid: str) -> bool:
        """Record a webhook message id (Meta's wamid). True = first
        time seen (process it); False = Meta redelivery of a message
        already handled."""
        existing = self._one(
            "SELECT wamid FROM whatsapp_updates WHERE wamid=?", (str(wamid),))
        if existing:
            return False
        self._exec("INSERT INTO whatsapp_updates VALUES (?,?)",
                   (str(wamid), now_iso()))
        return True

    def put_whatsapp_outbox(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO whatsapp_outbox VALUES (?,?,?)",
            (data["outbox_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_whatsapp_outbox(self, tenant_id: str,
                            outbox_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM whatsapp_outbox WHERE id=? AND tenant_id=?",
                        (outbox_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_whatsapp_outbox(self, status: Optional[str] = None) -> list[dict]:
        """Cross-tenant scan — worker-side only (the outbox tick is a
        platform process, like list_telegram_outbox)."""
        rows = self._all("SELECT data FROM whatsapp_outbox", ())
        out = [json.loads(r["data"]) for r in rows]
        if status is not None:
            out = [o for o in out if o.get("status") == status]
        return sorted(out, key=lambda o: o.get("created_at", ""))

    def put_whatsapp_duel(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO whatsapp_duels VALUES (?,?,?)",
            (data["duel_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_whatsapp_duel(self, tenant_id: str, duel_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM whatsapp_duels WHERE id=? AND tenant_id=?",
                        (duel_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def put_world(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO worlds VALUES (?,?,?)",
            (data["world_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_world(self, tenant_id: str, world_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM worlds WHERE id=? AND tenant_id=?",
                        (world_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_worlds(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM worlds WHERE tenant_id=?", (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------- societies (2026-10-22)
    def put_society(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO societies VALUES (?,?,?)",
            (data["society_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_society(self, tenant_id: str, society_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM societies WHERE id=? AND tenant_id=?",
                        (society_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_societies(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM societies WHERE tenant_id=?", (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------- combos (2026-10-23)
    def put_combo(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO combos VALUES (?,?,?)",
            (data["combo_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_combo(self, tenant_id: str, combo_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM combos WHERE id=? AND tenant_id=?",
                        (combo_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_combos(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM combos WHERE tenant_id=?", (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    def delete_combo(self, tenant_id: str, combo_id: str) -> None:
        self._exec("DELETE FROM combos WHERE id=? AND tenant_id=?",
                   (combo_id, tenant_id))
        self._commit()

    def put_combo_mapping(self, tenant_id: str, data: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO combo_mappings VALUES (?,?,?)",
            (data["mapping_id"], tenant_id, json.dumps(data)),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def list_combo_mappings(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM combo_mappings WHERE tenant_id=?",
                         (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    def delete_combo_mapping(self, tenant_id: str, mapping_id: str) -> None:
        self._exec("DELETE FROM combo_mappings WHERE id=? AND tenant_id=?",
                   (mapping_id, tenant_id))
        self._commit()

    # ------------------------------------------------- personas (v21.4: tenant-scoped)
    def put_persona(self, agent_id: str, persona: str, tenant_id: str = "") -> None:
        """Store a persona brief. tenant_id='' is the shared scope —
        bundled rosters and framework packs, whose briefs are identical
        for every workspace by design. Workspace-authored agents
        (custom.*) must pass their tenant: two workspaces may own
        same-named custom agents and their briefs must never collide."""
        self._upsert(
            "INSERT OR REPLACE INTO personas VALUES (?,?,?)",
            (tenant_id, agent_id, persona),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET data=EXCLUDED.data")

    def get_persona(self, agent_id: str, tenant_id: str = "") -> Optional[str]:
        """A tenant's own brief wins; the shared brief is the fallback
        (roster briefs live in the shared scope)."""
        if tenant_id:
            row = self._one("SELECT data FROM personas WHERE tenant_id=? AND id=?",
                            (tenant_id, agent_id))
            if row:
                return row["data"]
        row = self._one("SELECT data FROM personas WHERE tenant_id='' AND id=?",
                        (agent_id,))
        return row["data"] if row else None

    def delete_persona(self, agent_id: str, tenant_id: str = "") -> bool:
        """Delete one scope's brief. Deleting a tenant's brief can never
        remove the shared brief or another tenant's brief."""
        cur = self._exec("DELETE FROM personas WHERE tenant_id=? AND id=?",
                         (tenant_id, agent_id))
        return (cur.rowcount or 0) > 0

    def get_source(self, tenant_id: str, source_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM sources WHERE id=? AND tenant_id=?", (source_id, tenant_id))
        return json.loads(row["data"]) if row else None

    def list_sources(self, tenant_id: str) -> list[dict]:
        rows = self._all("SELECT data FROM sources WHERE tenant_id=?", (tenant_id,))
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------------------- events / tools / approvals / usage
    def put_event(self, tenant_id: str, data: dict) -> None:
        # Audit B9: assign the next insertion sequence under the
        # store lock, in the same write as the row itself. Legacy
        # rows (seq NULL) sort as 0 within their created_at tick;
        # sequenced rows order strictly by insertion after them.
        with self._lock:
            cur = self._raw("SELECT COALESCE(MAX(seq), 0) AS m FROM events")
            row = cur.fetchone()
            seq = (row["m"] if row else 0) + 1
            self._raw("INSERT INTO events VALUES (?,?,?,?,?,?)",
                      (data["event_id"], tenant_id, data["aggregate_id"],
                       json.dumps(data), data["occurred_at"], seq))
            self._commit()

    def list_events(self, tenant_id: str, aggregate_id: str, after: Optional[str] = None) -> list[dict]:
        rows = self._all("SELECT data FROM events WHERE tenant_id=? AND aggregate_id=?"
                         " ORDER BY created_at, COALESCE(seq, 0), id",
                         (tenant_id, aggregate_id))
        events = [json.loads(r["data"]) for r in rows]
        if after:
            idx = next((i for i, e in enumerate(events) if e["event_id"] == after), -1)
            events = events[idx + 1:]
        return events

    def put_invocation(self, tenant_id: str, mission_id: str, tool: str, risk: str,
                       status: str, data: dict) -> str:
        iid = new_id("inv")
        self._exec("INSERT INTO tool_invocations VALUES (?,?,?,?,?,?,?,?)",
                   (iid, mission_id, tenant_id, tool, risk, status, json.dumps(data), now_iso()))
        return iid

    def list_invocations(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM tool_invocations WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def put_approval(self, tenant_id: str, mission_id: str, invocation_id: str, preview: str) -> str:
        aid = new_id("apr")
        self._exec("INSERT INTO approvals VALUES (?,?,?,?,?, 'pending', ?)",
                   (aid, mission_id, tenant_id, invocation_id, preview, now_iso()))
        return aid

    def list_approvals(self, tenant_id: str, mission_id: str) -> list[dict]:
        return self._all("SELECT * FROM approvals WHERE mission_id=? AND tenant_id=? ORDER BY created_at",
                         (mission_id, tenant_id))

    def decide_approval(self, tenant_id: str, mission_id: str, approval_id: str, decision: str) -> Optional[dict]:
        """Decide a PENDING approval. A decided approval is immutable
        history (v21.4): re-deciding raises StateConflict (the API maps
        it to 409) — it never flips approved<->denied after the fact.
        The conditional UPDATE is the race guard: two concurrent
        deciders cannot both land."""
        row = self._one("SELECT * FROM approvals WHERE id=? AND mission_id=? AND tenant_id=?",
                        (approval_id, mission_id, tenant_id))
        if not row:
            return None
        if row["status"] != "pending":
            raise StateConflict(
                f"approval {approval_id} was already decided ({row['status']})")
        cur = self._exec("UPDATE approvals SET status=? WHERE id=? AND status='pending'",
                         (decision, approval_id))
        if (cur.rowcount or 0) != 1:
            raise StateConflict(
                f"approval {approval_id} was already decided")
        inv_status = "approved" if decision == "approved" else "denied"
        self._exec("UPDATE tool_invocations SET status=? WHERE id=?", (inv_status, row["invocation_id"]))
        return self._one("SELECT * FROM approvals WHERE id=?", (approval_id,))

    def add_usage(self, tenant_id: str, mission_id: str, category: str, amount: float, detail: str) -> None:
        # W1/W3 §14 rounding rule: EVERY cost is rounded (6dp) BEFORE it
        # is stored, so every total is a sum of already-rounded costs —
        # never a sum of raw floats rounded only at display time. The
        # ledger path (record_cost) already normalized; this closes the
        # legacy path, the second place W3's routed finding named.
        amount = round(float(amount), 6)
        self._exec("INSERT INTO usage_records VALUES (?,?,?,?,?,?,?)",
                   (new_id("use"), mission_id, tenant_id, category, amount, detail, now_iso()))
        self.add_spend(tenant_id, mission_id, amount)

    def usage_summary(self, tenant_id: str, mission_id: Optional[str] = None) -> dict:
        if mission_id:
            rows = self._all("SELECT category, SUM(amount_usd) AS total FROM usage_records"
                             " WHERE tenant_id=? AND mission_id=? GROUP BY category", (tenant_id, mission_id))
        else:
            rows = self._all("SELECT category, SUM(amount_usd) AS total FROM usage_records"
                             " WHERE tenant_id=? GROUP BY category", (tenant_id,))
        by_cat = {r["category"]: round(float(r["total"]), 4) for r in rows}
        # W1 cost engine: merge the detailed ledger into the SAME
        # existing surface (no new screen). No double-counting: a call
        # is recorded in exactly one of usage_records / cost_ledger —
        # the inference/chat/agent recorders now write the ledger only,
        # tool costs still write usage_records — so the two tables hold
        # disjoint calls and their sums may be added.
        ledger = self.ledger_summary(tenant_id, mission_id) if hasattr(self, "ledger_summary") else None
        result = {"by_category": by_cat, "total_usd": round(sum(by_cat.values()), 4)}
        if ledger is not None:
            for cat, amount in ledger["by_category"].items():
                by_cat[cat] = round(by_cat.get(cat, 0.0) + amount, 4)
            result["total_usd"] = round(sum(by_cat.values()), 4)
            result["by_agent"] = ledger["by_agent"]
            result["by_provider"] = ledger["by_provider"]
            result["by_phase"] = ledger["by_phase"]
            result["currency"] = "USD"
            result["cost_per_success_usd"] = ledger["cost_per_success_usd"]
            result["ledger_calls"] = ledger["calls"]
            result["calls_with_unknown_cost"] = ledger["rows_with_unknown_cost"]
        return result

    # ------------------------------------------------- W1 cost ledger (gap-fix)
    def record_cost(self, record: dict) -> Optional[str]:
        """Append one detailed ledger row (cost_engine.build_cost_record
        output). Usage-event dedup (123): a repeated event_key returns
        the existing row id and writes nothing — a retried recording
        of the same provider call never counts twice. Known cost also
        advances the mission's spent total (the same single add_spend
        the legacy path used); unknown cost advances nothing, never $0
        disguised as spend. Returns the row id, or None on dedup."""
        from .cost_engine import build_cost_record as _build
        rec = _build(**{k: v for k, v in record.items()
                        if k in ("tenant_id", "mission_id", "agent", "provider",
                                 "model", "phase", "category", "input_tokens",
                                 "output_tokens", "cached_tokens", "latency_ms",
                                 "success", "cost_usd", "cost_status",
                                 "price_source", "price_effective_date",
                                 "price_version", "attempt", "retry_of",
                                 "partial", "fallback", "cached_hit",
                                 "reused_from", "provider_request_id",
                                 "event_key", "currency")})
        if rec["event_key"]:
            existing = self._one("SELECT id FROM cost_ledger WHERE event_key=?",
                                 (rec["event_key"],))
            if existing:
                return None
        rid = new_id("cst")
        self._exec(
            "INSERT INTO cost_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rec["tenant_id"], rec["mission_id"], rec["agent"], rec["provider"],
             rec["model"], rec["phase"], rec["category"], rec["input_tokens"],
             rec["output_tokens"], rec["cached_tokens"], rec["latency_ms"],
             1 if rec["success"] else 0, rec["cost_usd"], rec["cost_status"],
             rec["price_source"], rec["price_effective_date"], rec["price_version"],
             rec["attempt"], rec["retry_of"], 1 if rec["partial"] else 0,
             1 if rec["fallback"] else 0, 1 if rec["cached_hit"] else 0,
             rec["reused_from"], rec["provider_request_id"], rec["event_key"],
             rec["currency"], now_iso()))
        if rec["cost_status"] == "known" and rec["cost_usd"]:
            self.add_spend(rec["tenant_id"], rec["mission_id"], rec["cost_usd"])
        return rid

    def list_cost_ledger(self, tenant_id: str, mission_id: Optional[str] = None) -> list[dict]:
        if mission_id:
            rows = self._all("SELECT * FROM cost_ledger WHERE tenant_id=? AND mission_id=?"
                             " ORDER BY created_at", (tenant_id, mission_id))
        else:
            rows = self._all("SELECT * FROM cost_ledger WHERE tenant_id=? ORDER BY created_at",
                             (tenant_id,))
        for r in rows:
            r["success"] = bool(r["success"])
            r["partial"] = bool(r["partial"])
            r["fallback"] = bool(r["fallback"])
            r["cached_hit"] = bool(r["cached_hit"])
            r["fresh_input_tokens"] = max(0, r["input_tokens"] - r["cached_tokens"])
        return rows

    def ledger_summary(self, tenant_id: str, mission_id: Optional[str] = None) -> dict:
        """Attribution summary over the detailed ledger, plus the
        headline cost-per-success: known cost of missions whose status
        is 'completed' divided by the number of completed missions.
        (Verified-completion is the mission status here; a mission
        that failed or is still running is not a success.)"""
        from .cost_engine import summarize_ledger as _summarize
        rows = self.list_cost_ledger(tenant_id, mission_id)
        summary = _summarize(rows)
        if mission_id:
            mission = self.get_mission(tenant_id, mission_id)
            outcomes = {mission_id: bool(mission and mission.get("status") == "completed")}
            from .cost_engine import cost_per_success as _cps
            summary["cost_per_success_usd"] = _cps(rows, outcomes)["cost_per_success_usd"]
        else:
            missions = self.list_missions(tenant_id, limit=10000)
            outcomes = {m["id"]: m.get("status") == "completed" for m in missions}
            from .cost_engine import cost_per_success as _cps
            summary["cost_per_success_usd"] = _cps(rows, outcomes)["cost_per_success_usd"]
        return summary

    def project_cost_summary(self, tenant_id: str, project_id: str) -> dict:
        """Per-project cost totals from the ledger (W9 seam: per-project
        budgets read this, they do not recompute cost). Missions carry
        the project_id; ledger rows carry the mission_id, so project
        totals are a sum over the project's missions — one computation,
        owned here."""
        missions = [m for m in self.list_missions(tenant_id, limit=10000)
                    if m.get("project_id") == project_id]
        ids = [m["id"] for m in missions]
        if not ids:
            return {"project_id": project_id, "missions": 0, "calls": 0,
                    "known_cost_usd": 0.0, "currency": "USD",
                    "cost_per_success_usd": None}
        placeholders = ",".join("?" for _ in ids)
        rows = self._all(f"SELECT * FROM cost_ledger WHERE tenant_id=? AND mission_id IN ({placeholders})",
                         (tenant_id, *ids))
        for r in rows:
            r["success"] = bool(r["success"])
            r["partial"] = bool(r["partial"])
            r["fallback"] = bool(r["fallback"])
            r["cached_hit"] = bool(r["cached_hit"])
            r["fresh_input_tokens"] = max(0, r["input_tokens"] - r["cached_tokens"])
        from .cost_engine import summarize_ledger as _summarize, cost_per_success as _cps
        summary = _summarize(rows)
        outcomes = {m["id"]: m.get("status") == "completed" for m in missions}
        return {"project_id": project_id, "missions": len(ids),
                "calls": summary["calls"],
                "known_cost_usd": summary["total_known_cost_usd"],
                "currency": "USD",
                "cost_per_success_usd": _cps(rows, outcomes)["cost_per_success_usd"],
                "by_agent": summary["by_agent"],
                "by_provider": summary["by_provider"]}

    def record_price_change(self, tenant_id: str, model: str, *,
                            provider: str = "", price_in: Optional[float] = None,
                            price_out: Optional[float] = None,
                            price_cached: Optional[float] = None,
                            price_source: str = "", effective_date: Optional[str] = None) -> dict:
        """Pricing-version tracking (122/137): each price change is a
        new versioned row with source + effective date; old prices are
        kept, never overwritten, so any past ledger row can be traced
        to the price that was current when it was recorded."""
        rows = self._all("SELECT MAX(version) AS v FROM price_history"
                         " WHERE tenant_id=? AND model=?", (tenant_id, model))
        version = int(rows[0]["v"] or 0) + 1 if rows else 1
        rid = new_id("prc")
        eff = effective_date or now_iso()
        self._exec("INSERT INTO price_history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, tenant_id, model, provider, price_in, price_out,
                    price_cached, price_source, eff, version, now_iso()))
        return {"id": rid, "model": model, "provider": provider,
                "price_in": price_in, "price_out": price_out,
                "price_cached": price_cached, "price_source": price_source,
                "effective_date": eff, "version": version}

    def latest_price(self, tenant_id: str, model: str) -> Optional[dict]:
        rows = self._all("SELECT * FROM price_history WHERE tenant_id=? AND model=?"
                         " ORDER BY effective_date DESC, version DESC LIMIT 1",
                         (tenant_id, model))
        return rows[0] if rows else None

    def record_budget_event(self, tenant_id: str, mission_id: str, kind: str,
                            detail_text: str, detail: Optional[dict] = None) -> dict:
        eid = new_id("bge")
        payload = {"text": detail_text, **(detail or {})}
        self._exec("INSERT INTO budget_events VALUES (?,?,?,?,?,?,?,?)",
                   (eid, tenant_id, mission_id, kind, "pending",
                    json.dumps(payload), now_iso(), now_iso()))
        return {"id": eid, "kind": kind, "status": "pending", "detail": payload}

    def decide_budget_event(self, tenant_id: str, mission_id: str,
                            event_id: str, decision: str) -> Optional[dict]:
        row = self._one("SELECT * FROM budget_events WHERE id=? AND tenant_id=? AND mission_id=?",
                        (event_id, tenant_id, mission_id))
        if not row or row["status"] != "pending":
            return None
        self._exec("UPDATE budget_events SET status=?, updated_at=? WHERE id=?",
                   (decision, now_iso(), event_id))
        row["status"] = decision
        row["detail"] = json.loads(row["detail"])
        return row

    def list_budget_events(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM budget_events WHERE tenant_id=? AND mission_id=?"
                         " ORDER BY created_at", (tenant_id, mission_id))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    # ------------------------------------------------------------- durable queue (jobs)
    def get_active_job(self, tenant_id: str, mission_id: str, kind: str = "mission_run",
                       exclude_job_id: Optional[str] = None) -> Optional[dict]:
        """The queued/running job of this kind for this mission, if
        one exists — the run-ownership lookup behind enqueue dedupe
        (audit B3) and the orchestrator's foreign-run refusal."""
        sql = ("SELECT id FROM jobs WHERE tenant_id=? AND mission_id=? AND kind=?"
               " AND status IN ('queued','running')")
        params: list = [tenant_id, mission_id, kind]
        if exclude_job_id:
            sql += " AND id<>?"
            params.append(exclude_job_id)
        sql += " ORDER BY created_at LIMIT 1"
        row = self._one(sql, tuple(params))
        return self.get_job(tenant_id, row["id"]) if row else None

    def enqueue_job(self, tenant_id: str, mission_id: str, kind: str = "mission_run",
                    payload: Optional[dict] = None, max_attempts: int = 3,
                    dedupe_active: bool = False) -> dict:
        """Insert a queued job. With dedupe_active, an already
        queued/running job of the same kind for the same mission is
        returned instead (marked "existing") — audit B3: a duplicate
        Run request used to enqueue a second mission_run job, and
        the two executions cancelled each other's tasks. The check
        and the insert hold the store lock together, and the
        jobs_one_active_run_idx partial unique index backstops the
        same invariant across processes."""
        with self._lock:
            if dedupe_active:
                existing = self.get_active_job(tenant_id, mission_id, kind)
                if existing is not None:
                    existing["existing"] = True
                    return existing
            jid = new_id("job")
            ts = now_iso()
            try:
                self._exec("INSERT INTO jobs (id, tenant_id, mission_id, kind, payload, status, attempts,"
                           " max_attempts, available_at, created_at, updated_at)"
                           " VALUES (?,?,?,?,?, 'queued', 0, ?, ?, ?, ?)",
                           (jid, tenant_id, mission_id, kind, json.dumps(payload or {}),
                            max_attempts, ts, ts, ts))
            except Exception as exc:
                if dedupe_active and type(exc).__name__ in (
                        "IntegrityError", "UniqueViolation"):
                    if not self.pg:
                        self._conn.rollback()
                    existing = self.get_active_job(tenant_id, mission_id, kind)
                    if existing is not None:
                        existing["existing"] = True
                        return existing
                raise
            return self.get_job(tenant_id, jid)

    def get_job(self, tenant_id: str, job_id: str) -> Optional[dict]:
        row = self._one("SELECT * FROM jobs WHERE id=? AND tenant_id=?", (job_id, tenant_id))
        if row:
            row["payload"] = json.loads(row["payload"])
            if row.get("last_error"):
                # Scrub on read too: rows written before v21.4 may hold
                # full tracebacks, and this row is API-visible.
                row["last_error"] = error_summary(row["last_error"])
        return row

    def list_jobs(self, tenant_id: str, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self._all("SELECT * FROM jobs WHERE tenant_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
                             (tenant_id, status, limit))
        else:
            rows = self._all("SELECT * FROM jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                             (tenant_id, limit))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            if r.get("last_error"):
                r["last_error"] = error_summary(r["last_error"])
        return rows

    # A job is claimable when it is queued and due, or running under a
    # lease that has expired (its worker died). Used verbatim in both
    # the picker subquery and the outer guard of the claim UPDATE.
    _JOB_DUE = ("((status='queued' AND available_at<=?)"
                " OR (status='running' AND lease_expires<?))")

    def claim_job(self, owner: str, lease_seconds: int = 120) -> Optional[dict]:
        """Claim the oldest due job — atomically, in ONE guarded UPDATE
        (v21.4). The old SELECT-then-UPDATE shape released its Postgres
        row lock when the SELECT ended (autocommit) and serialised
        SQLite only per-process, so two processes could claim the same
        job. Here the UPDATE itself carries the due-condition twice
        (picker subquery + outer guard): a concurrent winner flips the
        row out of the due set, the loser's UPDATE matches nothing, and
        it moves on to the next due job. Expired leases are reclaimable."""
        now = now_iso()
        with self._lock:
            for _ in range(3):
                cur = self._raw(
                    "UPDATE jobs SET status='running', lease_owner=?,"
                    " lease_expires=?, attempts=attempts+1, updated_at=?"
                    f" WHERE id=(SELECT id FROM jobs WHERE {self._JOB_DUE}"
                    " ORDER BY created_at LIMIT 1)"
                    f" AND {self._JOB_DUE} RETURNING *",
                    (owner, _future_iso(lease_seconds), now,
                     now, now, now, now))
                row = cur.fetchone()
                self._commit()
                if row is not None:
                    row = dict(row)
                    row["payload"] = json.loads(row["payload"])
                    return row
                if self._one(f"SELECT id FROM jobs WHERE {self._JOB_DUE} LIMIT 1",
                             (now, now)) is None:
                    return None  # genuinely nothing due
            return None

    def claim_job_by_id(self, owner: str, job_id: str,
                        lease_seconds: int = 120) -> Optional[dict]:
        """Claim ONE specific job if it is claimable (v21.4) — the
        inline runner must execute the job it was handed, never the
        oldest job in the queue (the old behaviour stranded the
        requested job and could run another tenant's backlog first)."""
        now = now_iso()
        with self._lock:
            cur = self._raw(
                "UPDATE jobs SET status='running', lease_owner=?,"
                " lease_expires=?, attempts=attempts+1, updated_at=?"
                f" WHERE id=? AND {self._JOB_DUE} RETURNING *",
                (owner, _future_iso(lease_seconds), now, job_id, now, now))
            row = cur.fetchone()
            self._commit()
            if row is None:
                return None
            row = dict(row)
            row["payload"] = json.loads(row["payload"])
            return row

    def heartbeat_job(self, job_id: str, lease_seconds: int = 120,
                      owner: Optional[str] = None) -> bool:
        """Extend a running job's lease. Fenced on lease_owner when the
        caller passes one: a worker whose lease was reclaimed must not
        resurrect it. Returns True when the lease was extended."""
        if owner is not None:
            cur = self._exec(
                "UPDATE jobs SET lease_expires=?, updated_at=? WHERE id=?"
                " AND status='running' AND lease_owner=?",
                (_future_iso(lease_seconds), now_iso(), job_id, owner))
        else:
            cur = self._exec(
                "UPDATE jobs SET lease_expires=?, updated_at=? WHERE id=?"
                " AND status='running'",
                (_future_iso(lease_seconds), now_iso(), job_id))
        return (cur.rowcount or 0) > 0

    def complete_job(self, job_id: str, status: str = "completed",
                     owner: Optional[str] = None) -> None:
        """Mark a job finished. With owner given, the write is fenced:
        it lands only while that owner still holds the lease — a stale
        owner cannot complete (or cancel) a job it lost."""
        if owner is not None:
            self._exec("UPDATE jobs SET status=?, lease_owner=NULL, lease_expires=NULL,"
                       " updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
                       (status, now_iso(), job_id, owner))
        else:
            self._exec("UPDATE jobs SET status=?, lease_owner=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
                       (status, now_iso(), job_id))

    def _fenced_out(self, row: Optional[dict], owner: Optional[str]) -> bool:
        """True when an owner-fenced write must NOT land: the job is no
        longer running under that owner's lease."""
        return owner is not None and (
            row is None or row["status"] != "running"
            or row["lease_owner"] != owner)

    def fail_job(self, job_id: str, error: str, base_backoff_seconds: float = 2.0,
                 owner: Optional[str] = None) -> dict:
        """Bounded retry with exponential backoff + a dead-letter end state.
        With owner given, fenced on the lease (see complete_job): a stale
        owner can neither requeue nor dead-letter a job it no longer owns.
        last_error stores a one-line summary, never a traceback."""
        row = self._one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise KeyError("job not found")
        if self._fenced_out(row, owner):
            return {"status": row["status"], "attempts": row["attempts"],
                    "fenced": True}
        summary = error_summary(error)
        guard = " AND status='running' AND lease_owner=?" if owner is not None else ""
        tail = (owner,) if owner is not None else ()
        if row["attempts"] >= row["max_attempts"]:
            self._exec("UPDATE jobs SET status='dead', last_error=?, lease_owner=NULL,"
                       f" lease_expires=NULL, updated_at=? WHERE id=?{guard}",
                       (summary, now_iso(), job_id, *tail))
            return {"status": "dead", "attempts": row["attempts"]}
        backoff = base_backoff_seconds * (2 ** row["attempts"])
        self._exec("UPDATE jobs SET status='queued', last_error=?, available_at=?,"
                   f" lease_owner=NULL, lease_expires=NULL, updated_at=? WHERE id=?{guard}",
                   (summary, _future_iso(backoff), now_iso(), job_id, *tail))
        return {"status": "queued", "attempts": row["attempts"], "retry_in_seconds": backoff}

    def fail_job_terminal(self, job_id: str, error: str,
                          owner: Optional[str] = None) -> None:
        """Terminal failure with no retry — used by inline (synchronous) mode,
        where the caller needs the failure now, not after backoff. Fenced
        on the lease when owner is given; stores an error summary only."""
        if owner is not None:
            self._exec("UPDATE jobs SET status='failed', last_error=?, lease_owner=NULL,"
                       " lease_expires=NULL, updated_at=? WHERE id=?"
                       " AND status='running' AND lease_owner=?",
                       (error_summary(error), now_iso(), job_id, owner))
        else:
            self._exec("UPDATE jobs SET status='failed', last_error=?, lease_owner=NULL,"
                       " lease_expires=NULL, updated_at=? WHERE id=?",
                       (error_summary(error), now_iso(), job_id))

    def replay_job(self, tenant_id: str, job_id: str) -> Optional[dict]:
        row = self.get_job(tenant_id, job_id)
        if not row or row["status"] not in ("dead", "failed"):
            return None
        try:
            self._exec("UPDATE jobs SET status='queued', attempts=0, available_at=?, last_error=NULL,"
                       " updated_at=? WHERE id=? AND tenant_id=?",
                       (now_iso(), now_iso(), job_id, tenant_id))
        except Exception as exc:
            # Audit B3: requeueing a mission_run job while another
            # run of the same mission is active trips the
            # jobs_one_active_run_idx invariant — that is a state
            # conflict (the endpoint answers 409), not a 500.
            if type(exc).__name__ in ("IntegrityError", "UniqueViolation"):
                if not self.pg:
                    self._conn.rollback()
                return None
            raise
        return self.get_job(tenant_id, job_id)

    def requeue_job(self, job_id: str, delay_seconds: float, *, refund_attempt: bool = False,
                    owner: Optional[str] = None) -> None:
        """Return a running job to the queue without treating it as a
        failure — used for pause and for the admin kill switch. Fenced
        on lease_owner when owner is given."""
        attempts_sql = ", attempts=attempts-1" if refund_attempt else ""
        guard = " AND lease_owner=?" if owner is not None else ""
        tail = (owner,) if owner is not None else ()
        self._exec(
            f"UPDATE jobs SET status='queued', available_at=?, lease_owner=NULL,"
            f" lease_expires=NULL, updated_at=?{attempts_sql} WHERE id=? AND status='running'{guard}",
            (_future_iso(delay_seconds), now_iso(), job_id, *tail))

    # -------------------------------------------------- projects (V3)
    def put_project(self, tenant_id: str, project: dict) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO projects VALUES (?,?,?,?)",
            (project["id"], tenant_id, project["name"], project["created_at"]),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET name=EXCLUDED.name")

    def list_projects(self, tenant_id: str) -> list[dict]:
        return self._all("SELECT * FROM projects WHERE tenant_id=? ORDER BY created_at",
                         (tenant_id,))

    def get_project(self, tenant_id: str, project_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM projects WHERE tenant_id=? AND id=?",
                         (tenant_id, project_id))

    # -------------------------------------------------- connections (V3)
    def put_connection(self, tenant_id: str, conn: dict, secret: Optional[dict] = None) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO connections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (conn["id"], tenant_id, conn["owner_user_id"], conn["kind"], conn["provider_key"],
             conn["label"], conn["base_url"], conn["state"], conn.get("masked_key", ""),
             json.dumps(conn.get("allowed_models", [])), json.dumps(conn.get("blocked_models", [])),
             conn.get("budget_limit_usd"), conn.get("routing_mode", "balanced"),
             json.dumps(conn.get("data_policy", {})), conn.get("last_validated_at"),
             conn.get("last_error_category"), conn.get("models_count", 0),
             conn["created_at"], conn["updated_at"], conn.get("deleted_at"),
             json.dumps(conn.get("headers") or {})),
            "ON CONFLICT (tenant_id, id) DO UPDATE SET owner_user_id=EXCLUDED.owner_user_id,"
            " kind=EXCLUDED.kind, provider_key=EXCLUDED.provider_key, label=EXCLUDED.label,"
            " base_url=EXCLUDED.base_url, state=EXCLUDED.state, masked_key=EXCLUDED.masked_key,"
            " allowed_models=EXCLUDED.allowed_models, blocked_models=EXCLUDED.blocked_models,"
            " budget_limit_usd=EXCLUDED.budget_limit_usd, routing_mode=EXCLUDED.routing_mode,"
            " data_policy=EXCLUDED.data_policy, last_validated_at=EXCLUDED.last_validated_at,"
            " last_error_category=EXCLUDED.last_error_category, models_count=EXCLUDED.models_count,"
            " updated_at=EXCLUDED.updated_at, deleted_at=EXCLUDED.deleted_at,"
            " headers=EXCLUDED.headers")
        if secret is not None:
            self.put_secret(tenant_id, conn["id"], secret["key_version"], secret["ciphertext"])

    def _conn_row(self, row: Optional[dict]) -> Optional[dict]:
        if not row:
            return None
        row = dict(row)
        row["allowed_models"] = json.loads(row["allowed_models"])
        row["blocked_models"] = json.loads(row["blocked_models"])
        row["data_policy"] = json.loads(row["data_policy"])
        raw_headers = row.get("headers")
        if raw_headers is None:
            row["headers"] = {}
        elif isinstance(raw_headers, dict):
            row["headers"] = raw_headers
        else:
            try:
                row["headers"] = json.loads(raw_headers) or {}
            except (ValueError, TypeError):
                row["headers"] = {}
        return row

    def get_connection(self, tenant_id: str, connection_id: str) -> Optional[dict]:
        return self._conn_row(self._one(
            "SELECT * FROM connections WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
            (tenant_id, connection_id)))

    def list_connections(self, tenant_id: str) -> list[dict]:
        return [self._conn_row(r) for r in self._all(
            "SELECT * FROM connections WHERE tenant_id=? AND deleted_at IS NULL ORDER BY created_at",
            (tenant_id,))]

    def list_connections_all(self) -> list[dict]:
        """Cross-tenant scan — platform-process only, like
        list_whatsapp_outbox: the WhatsApp webhook router (v18) maps
        an inbound phone_number_id to its owning tenant through this.
        Callers must treat the rows as read-only routing data."""
        return [self._conn_row(r) for r in self._all(
            "SELECT * FROM connections WHERE deleted_at IS NULL ORDER BY created_at",
            ())]

    def put_secret(self, tenant_id: str, connection_id: str, key_version: str, ciphertext: str) -> None:
        self._upsert(
            "INSERT OR REPLACE INTO credential_secrets VALUES (?,?,?,?,?)",
            (connection_id, tenant_id, key_version, ciphertext, now_iso()),
            "ON CONFLICT (tenant_id, connection_id) DO UPDATE SET key_version=EXCLUDED.key_version,"
            " ciphertext=EXCLUDED.ciphertext, updated_at=EXCLUDED.updated_at")

    def get_secret(self, tenant_id: str, connection_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM credential_secrets WHERE tenant_id=? AND connection_id=?",
                         (tenant_id, connection_id))

    def list_secret_rows(self) -> list[dict]:
        return self._all("SELECT * FROM credential_secrets WHERE key_version != 'shredded'")

    def update_secret_row(self, tenant_id: str, connection_id: str,
                          ciphertext: str, key_version: str) -> None:
        self._exec("UPDATE credential_secrets SET ciphertext=?, key_version=?,"
                   " updated_at=? WHERE tenant_id=? AND connection_id=?",
                   (ciphertext, key_version, now_iso(), tenant_id, connection_id))

    def shred_secret(self, tenant_id: str, connection_id: str) -> None:
        """Crypto-shred: the ciphertext is destroyed, making the secret
        unrecoverable even from backups of this table."""
        self._exec("UPDATE credential_secrets SET ciphertext='', key_version='shredded',"
                   " updated_at=? WHERE tenant_id=? AND connection_id=?",
                   (now_iso(), tenant_id, connection_id))

    # -------------------------------------------------- catalog (V3)
    def start_sync_run(self, tenant_id: str, connection_id: str) -> str:
        run_id = new_id("sync")
        self._exec("INSERT INTO catalog_sync_runs VALUES (?,?,?,?,NULL,'running',0,NULL)",
                   (run_id, tenant_id, connection_id, now_iso()))
        return run_id

    def finish_sync_run(self, run_id: str, status: str, models_count: int,
                        error: Optional[str] = None) -> None:
        self._exec("UPDATE catalog_sync_runs SET finished_at=?, status=?, models_count=?, error=?"
                   " WHERE id=?", (now_iso(), status, models_count, error, run_id))

    def upsert_catalog_model(self, tenant_id: str, connection_id: str, entry: dict) -> None:
        now = now_iso()
        existing = self._one(
            "SELECT first_seen FROM catalog_models WHERE tenant_id=? AND connection_id=? AND model_id=?",
            (tenant_id, connection_id, entry["model_id"]))
        self._upsert(
            "INSERT OR REPLACE INTO catalog_models VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, connection_id, entry["model_id"], entry.get("provider", ""),
             entry.get("display_name", entry["model_id"]), json.dumps(entry.get("capabilities", {})),
             entry.get("price_in"), entry.get("price_out"), entry.get("price_source"),
             entry.get("price_status", "unknown"), entry.get("health", "unknown"),
             existing["first_seen"] if existing else now, now, 0),
            "ON CONFLICT (tenant_id, connection_id, model_id) DO UPDATE SET"
            " provider=EXCLUDED.provider, display_name=EXCLUDED.display_name,"
            " capabilities=EXCLUDED.capabilities, price_in=EXCLUDED.price_in,"
            " price_out=EXCLUDED.price_out, price_source=EXCLUDED.price_source,"
            " price_status=EXCLUDED.price_status, health=EXCLUDED.health,"
            " last_seen=EXCLUDED.last_seen, deprecated=0")

    def deprecate_missing_models(self, tenant_id: str, connection_id: str,
                                 seen_model_ids: set) -> None:
        rows = self._all("SELECT model_id FROM catalog_models WHERE tenant_id=? AND connection_id=?"
                         " AND deprecated=0", (tenant_id, connection_id))
        for r in rows:
            if r["model_id"] not in seen_model_ids:
                self._exec("UPDATE catalog_models SET deprecated=1 WHERE tenant_id=?"
                           " AND connection_id=? AND model_id=?",
                           (tenant_id, connection_id, r["model_id"]))

    def delete_catalog_for_connection(self, tenant_id: str, connection_id: str) -> None:
        """Remove every catalog row owned by one connection.

        Called only from connection deletion: a deleted connection's
        models must not outlive it in /v1/models. (Deprecating instead
        would NOT fix the display — list_catalog returns deprecated
        rows and the UI renders them — so deletion, not deprecation,
        is the correct cleanup here.)
        """
        self._exec("DELETE FROM catalog_models WHERE tenant_id=? AND connection_id=?",
                   (tenant_id, connection_id))

    def list_catalog(self, tenant_id: str, connection_id: Optional[str] = None) -> list[dict]:
        if connection_id:
            rows = self._all("SELECT * FROM catalog_models WHERE tenant_id=? AND connection_id=?"
                             " ORDER BY model_id", (tenant_id, connection_id))
        else:
            rows = self._all("SELECT * FROM catalog_models WHERE tenant_id=? ORDER BY model_id",
                             (tenant_id,))
        for r in rows:
            r["capabilities"] = json.loads(r["capabilities"])
            r["deprecated"] = bool(r["deprecated"])
        return rows

    def last_sync_run(self, tenant_id: str, connection_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM catalog_sync_runs WHERE tenant_id=? AND connection_id=?"
                         " ORDER BY started_at DESC LIMIT 1", (tenant_id, connection_id))

    # ----------------------- model capability history + token reconciliation (W6)
    def record_capability_result(self, tenant_id: str, connection_id: str,
                                 model_id: str, capability: str, status: str,
                                 task_class: str | None = None,
                                 latency_ms: int = 0, detail: dict | None = None) -> None:
        """Append-only probe/task history (W6 A6): one row per stamp.
        Append-only on purpose — history is evidence, never rewritten."""
        self._exec("INSERT INTO model_capability_results VALUES "
                   "(?,?,?,?,?,?,?,?,?,?)",
                   (new_id("cap"), tenant_id, connection_id, model_id, capability, status,
                    task_class, int(latency_ms), json.dumps(detail or {}), now_iso()))

    def latest_capability_badges(self, tenant_id: str) -> dict:
        """Latest probe stamp per (connection_id, model_id, capability).
        Returns {(connection_id, model_id): {capability: status}}."""
        rows = self._all("SELECT * FROM model_capability_results WHERE tenant_id=?"
                         " ORDER BY created_at DESC, id DESC", (tenant_id,))
        latest: dict = {}
        for r in rows:
            key = (r["connection_id"], r["model_id"])
            bucket = latest.setdefault(key, {})
            if r["capability"] not in bucket:
                bucket[r["capability"]] = r["status"]
        return latest

    def capability_history(self, tenant_id: str, model_id: str | None = None,
                           task_class: str | None = None) -> list[dict]:
        """Capability history rows, optionally filtered by model and
        task class, oldest first (so profiles read them in order)."""
        sql = "SELECT * FROM model_capability_results WHERE tenant_id=?"
        args: list = [tenant_id]
        if model_id:
            sql += " AND model_id=?"
            args.append(model_id)
        if task_class:
            sql += " AND task_class=?"
            args.append(task_class)
        sql += " ORDER BY created_at ASC, id ASC"
        rows = self._all(sql, tuple(args))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
        return rows

    def record_token_reconciliation(self, tenant_id: str, session_id: str,
                                    model_id: str, reconciliation: dict) -> None:
        self._exec("INSERT INTO token_reconciliations VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (new_id("trec"), tenant_id, session_id, model_id,
                    reconciliation.get("estimated_input_tokens"),
                    reconciliation.get("reported_input_tokens"),
                    reconciliation.get("input_error"),
                    reconciliation.get("input_error_pct"),
                    reconciliation.get("estimated_output_tokens"),
                    reconciliation.get("reported_output_tokens"),
                    reconciliation.get("output_error"),
                    reconciliation.get("output_error_pct"), now_iso()))

    def list_token_reconciliations(self, tenant_id: str,
                                   session_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM token_reconciliations WHERE tenant_id=?"
        args: list = [tenant_id]
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        sql += " ORDER BY created_at ASC, id ASC"
        return self._all(sql, tuple(args))

    # -------------------------------------------------- routing + budget (V3)
    def put_route_decision(self, decision: dict) -> None:
        self._exec(
            "INSERT INTO route_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision["id"], decision["tenant_id"], decision.get("mission_id"),
             decision.get("role"), decision.get("connection_id"), decision.get("model_id"),
             decision["mode"], json.dumps(decision.get("reason_codes", [])),
             json.dumps(decision.get("filtered_counts", {})), decision.get("snapshot_id"),
             decision.get("estimated_cost"), decision.get("estimate_confidence", "unknown"),
             decision["created_at"]))

    def list_route_decisions(self, tenant_id: str, mission_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM route_decisions WHERE tenant_id=? AND mission_id=?"
                         " ORDER BY created_at", (tenant_id, mission_id))
        for r in rows:
            r["reason_codes"] = json.loads(r["reason_codes"])
            r["filtered_counts"] = json.loads(r["filtered_counts"])
        return rows

    def reserve_budget(self, tenant_id: str, mission_id: str, amount: float,
                       cap: float) -> Optional[str]:
        """Atomic reservation: succeeds only if spent + reserved + amount
        stays within the mission cap. The conditional UPDATE is the race
        guard — concurrent workers cannot over-reserve (invariant §114)."""
        if amount <= 0:
            return new_id("rsv")
        with self._lock:
            cur = self._raw(
                "UPDATE missions SET reserved_usd=reserved_usd+?"
                " WHERE id=? AND tenant_id=? AND spent+reserved_usd+?<=?",
                (amount, mission_id, tenant_id, amount, cap))
            self._commit()
            # sqlite cursor.rowcount / psycopg rowcount both reflect the update.
            if getattr(cur, "rowcount", 0) != 1:
                return None
            rid = new_id("rsv")
            self._raw("INSERT INTO budget_reservations VALUES (?,?,?,?,'reserved',?,?)",
                      (rid, tenant_id, mission_id, amount, now_iso(), now_iso()))
            self._commit()
            return rid

    def settle_reservation(self, reservation_id: str, outcome: str) -> None:
        """outcome: 'reconciled' (call finished; spend recorded separately in
        the ledger) or 'released' (call failed/never ran). Either way the
        reserved amount leaves the mission's reserved total."""
        row = self._one("SELECT * FROM budget_reservations WHERE id=?", (reservation_id,))
        if not row or row["status"] != "reserved":
            return
        self._exec("UPDATE budget_reservations SET status=?, updated_at=? WHERE id=?",
                   (outcome, now_iso(), reservation_id))
        # Postgres MAX() is an aggregate; GREATEST is its scalar form.
        fn = "GREATEST" if self.pg else "MAX"
        self._exec(f"UPDATE missions SET reserved_usd={fn}(0, reserved_usd-?) WHERE id=?",
                   (row["amount"], row["mission_id"]))

    # -------------------------------------------------- flags + audit (V3)
    def get_flag(self, key: str) -> Optional[str]:
        row = self._one("SELECT value FROM platform_flags WHERE key=?", (key,))
        return row["value"] if row else None

    def set_flag(self, key: str, value: str) -> None:
        self._upsert("INSERT OR REPLACE INTO platform_flags VALUES (?,?,?)",
                     (key, value, now_iso()),
                     "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,"
                     " updated_at=EXCLUDED.updated_at")

    def audit(self, tenant_id: str, actor: str, action: str, entity: str,
              entity_id: str, detail: Optional[dict] = None) -> None:
        self._exec("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?)",
                   (new_id("aud"), tenant_id, actor, action, entity, entity_id,
                    json.dumps(detail or {}), now_iso()))

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[dict]:
        rows = self._all("SELECT * FROM audit_log WHERE tenant_id=? ORDER BY created_at DESC"
                         " LIMIT ?", (tenant_id, limit))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    # ---- Phase 4: users, sessions, memberships, rate counters ------------
    def create_user(self, email: str, password_hash: str,
                    display_name: str = "") -> Optional[dict]:
        uid = new_id("usr")
        try:
            self._exec("INSERT INTO users VALUES (?,?,?,?,?)",
                       (uid, email, password_hash, display_name, now_iso()))
        except Exception:
            return None  # duplicate email (unique constraint)
        return self.get_user(uid)

    def get_user(self, user_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM users WHERE id=?", (user_id,))

    def get_user_by_email(self, email: str) -> Optional[dict]:
        return self._one("SELECT * FROM users WHERE email=?", (email,))

    def create_session(self, user_id: str, token_hash: str,
                       expires_at: str) -> dict:
        sid = new_id("ses")
        self._exec("INSERT INTO sessions VALUES (?,?,?,?,?,0)",
                   (sid, user_id, token_hash, expires_at, now_iso()))
        return {"id": sid, "user_id": user_id, "expires_at": expires_at}

    def get_session(self, token_hash: str) -> Optional[dict]:
        return self._one("SELECT * FROM sessions WHERE token_hash=? AND revoked=0",
                         (token_hash,))

    def revoke_session(self, token_hash: str) -> None:
        self._exec("UPDATE sessions SET revoked=1 WHERE token_hash=?", (token_hash,))

    # ---- brain keys (v21.2): one active scoped key per tenant ----
    def create_brain_key(self, tenant_id: str, user_id: str, label: str,
                         key_hash: str, key_prefix: str) -> dict:
        """Insert a new brain key, revoking the tenant's previous ones
        (create == rotate: at most one active key per tenant). v21.4:
        revoke + insert run as ONE locked unit (previously two
        separately-committed statements, whose interleave could leave
        two active keys), backed by the brain_keys_one_active_idx
        unique index; if a cross-process creator still wins the index
        race, revoke-and-retry once so create stays a rotation."""
        kid = new_id("bkey")
        params = (kid, tenant_id, user_id, label, key_hash, key_prefix,
                  now_iso())
        with self._lock:
            self._raw("UPDATE brain_keys SET revoked=1"
                      " WHERE tenant_id=? AND revoked=0", (tenant_id,))
            try:
                self._raw("INSERT INTO brain_keys VALUES (?,?,?,?,?,?,?,NULL,0)",
                          params)
            except Exception:
                self._raw("UPDATE brain_keys SET revoked=1"
                          " WHERE tenant_id=? AND revoked=0 AND id!=?",
                          (tenant_id, kid))
                self._raw("INSERT INTO brain_keys VALUES (?,?,?,?,?,?,?,NULL,0)",
                          params)
            self._commit()
        return self.get_brain_key(kid)

    def get_brain_key(self, key_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM brain_keys WHERE id=?", (key_id,))

    def get_brain_key_by_hash(self, key_hash: str) -> Optional[dict]:
        return self._one("SELECT * FROM brain_keys WHERE key_hash=? AND revoked=0",
                         (key_hash,))

    def get_active_brain_key(self, tenant_id: str) -> Optional[dict]:
        return self._one(
            "SELECT * FROM brain_keys WHERE tenant_id=? AND revoked=0"
            " ORDER BY created_at DESC", (tenant_id,))

    def revoke_brain_keys(self, tenant_id: str) -> int:
        cur = self._exec("UPDATE brain_keys SET revoked=1 WHERE tenant_id=? AND revoked=0",
                         (tenant_id,))
        return cur.rowcount or 0

    def touch_brain_key(self, key_id: str) -> None:
        self._exec("UPDATE brain_keys SET last_used_at=? WHERE id=?",
                   (now_iso(), key_id))

    def upsert_membership(self, tenant_id: str, user_id: str, role: str) -> dict:
        existing = self.get_membership(tenant_id, user_id)
        if existing:
            self._exec("UPDATE memberships SET role=? WHERE tenant_id=? AND user_id=?",
                       (role, tenant_id, user_id))
        else:
            self._exec("INSERT INTO memberships VALUES (?,?,?,?)",
                       (tenant_id, user_id, role, now_iso()))
        return self.get_membership(tenant_id, user_id)

    def get_membership(self, tenant_id: str, user_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM memberships WHERE tenant_id=? AND user_id=?",
                         (tenant_id, user_id))

    def list_memberships(self, tenant_id: str) -> list[dict]:
        return self._all(
            "SELECT m.tenant_id, m.user_id, m.role, m.created_at, u.email,"
            " u.display_name FROM memberships m JOIN users u ON u.id=m.user_id"
            " WHERE m.tenant_id=? ORDER BY m.created_at", (tenant_id,))

    def remove_membership(self, tenant_id: str, user_id: str) -> bool:
        cur = self._exec("DELETE FROM memberships WHERE tenant_id=? AND user_id=?",
                         (tenant_id, user_id))
        return (cur.rowcount or 0) > 0

    def rate_hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Fixed-window rate counter. Returns (allowed, retry_after_s).

        The conditional increment is the race guard: the UPDATE only lands
        while count < limit, so parallel callers cannot slip past the cap.
        """
        import time as _time
        now_s = int(_time.time())
        window = (now_s // window_seconds) * window_seconds
        with self._lock:
            self._raw("INSERT INTO rate_counters (key, window_start, count)"
                      " VALUES (?,?,0) ON CONFLICT DO NOTHING"
                      if self.pg else
                      "INSERT OR IGNORE INTO rate_counters (key, window_start, count)"
                      " VALUES (?,?,0)", (key, window))
            cur = self._raw("UPDATE rate_counters SET count=count+1"
                            " WHERE key=? AND window_start=? AND count<?",
                            (key, window, limit))
            allowed = (cur.rowcount or 0) > 0
            self._commit()
        if allowed:
            return True, 0
        return False, max(1, window + window_seconds - now_s)


def default_store() -> Store:
    from .config import load_settings
    s = load_settings()
    if s.use_postgres:
        return Store(pg_dsn=s.database_url, artifacts_dir=str(s.artifacts_dir))
    return Store(str(s.sqlite_path), str(s.artifacts_dir))
