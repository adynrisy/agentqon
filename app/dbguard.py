"""AgentQon — database guards (gap-security).

Shared database rules, implemented as constraints, state-machine
invariants and small helpers — not as hundreds of one-off checks:

  - optimistic concurrency: guarded updates carry the version the
    caller read; a stale writer loses, loudly, instead of overwriting;
  - atomic state transitions: mission-complete and artifact-available
    change together, or the state explicitly reads
    partial-needing-recovery ("needs_recovery") — never "completed
    with no artifact". A recovery sweep converts a mission left in
    the in-between state by a crash;
  - migration rollback planning: every registered migration carries
    its down-step, and the plan is inspectable before it runs;
  - connection-pool limits, query timeouts, index-usage reporting,
    keyset pagination (stable under inserts), soft deletes, and
    orphan cleanup.

Postgres note, stated honestly: the store runs Postgres in autocommit
mode, so multi-statement atomicity there is per-statement unless the
caller holds a transaction. ``complete_mission_with_artifact`` holds
the store lock and orders artifact-before-status on both backends, and
the recovery sweep repairs the in-between state on either backend —
so the INVARIANT (never completed-without-artifact) holds on both,
even though the mechanism differs.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .store import now_iso

NEEDS_RECOVERY = "needs_recovery"
COMPLETING = "completing"


# ------------------------------------------------------------ columns + versions
def _columns(store, table: str) -> list[str]:
    if store.pg:
        rows = store._all(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            (table,))
        return [r["column_name"] for r in rows]
    rows = store._all(f"PRAGMA table_info({table})")
    # _all returns dicts only when row_factory gives dicts; PRAGMA via
    # _raw returns Rows — _all converts via dict(row). Keys: cid,name,...
    return [r["name"] for r in rows if "name" in r]


def ensure_version_column(store, table: str = "missions") -> bool:
    """Add a ``version`` column when missing (idempotent). Returns
    True when the column exists afterwards."""
    try:
        if "version" in _columns(store, table):
            return True
        store._exec(f"ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        return True
    except Exception:
        return False


def guarded_update(store, table: str, id_value: str, expected_version: int,
                   sets: dict, id_column: str = "id") -> dict:
    """Optimistic concurrency checking: the UPDATE carries
    ``version = expected`` in its WHERE clause, so a stale writer
    updates zero rows and learns it lost — instead of silently
    overwriting a newer write. Returns (ok, new_version)."""
    ensure_version_column(store, table)
    set_clause = ", ".join(f"{key}=?" for key in sets) + ", version=version+1"
    # WHERE order is id first, then version — params must match it.
    params = list(sets.values()) + [id_value, expected_version]
    cur = store._exec(
        f"UPDATE {table} SET {set_clause} WHERE {id_column}=? AND version=?",
        tuple(params))
    if (cur.rowcount or 0) == 0:
        current = store._one(f"SELECT version FROM {table} WHERE {id_column}=?",
                             (id_value,))
        return {"ok": False, "conflict": True,
                "current_version": current["version"] if current else None,
                "plain": "someone else changed this record first — "
                         "reload it and try again"}
    return {"ok": True, "conflict": False, "new_version": expected_version + 1,
            "plain": "saved"}


# ------------------------------------------------------------ atomic mission completion
def complete_mission_with_artifact(store, tenant_id: str, mission_id: str,
                                   artifact_kind: str, content,
                                   metadata: dict | None = None,
                                   fail_after: str | None = None) -> dict:
    """Complete a mission AND publish its artifact together.

    Order: artifact first, then status, under the store lock. If
    anything fails after the artifact lands (``fail_after='artifact'``
    simulates a crash there), the mission is marked NEEDS_RECOVERY —
    it never reads 'completed' while its artifact is missing, and it
    never silently pretends the crash did not happen. A mission that
    dies mid-transition without even that (hard crash) sits in
    COMPLETING until ``recover_partial_missions`` converts it.
    ``fail_after`` is a test seam only."""
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None:
        return {"ok": False, "plain": "that mission no longer exists"}
    try:
        with store._lock:
            store.set_mission_status(tenant_id, mission_id, COMPLETING)
            artifact = store.put_artifact(tenant_id, mission_id, artifact_kind,
                                          content, dict(metadata or {}))
            if fail_after == "artifact":
                raise RuntimeError("simulated crash between artifact and completion")
            store.set_mission_status(tenant_id, mission_id, "completed")
        return {"ok": True, "artifact_id": artifact["artifact_id"],
                "plain": "mission completed and its result is available"}
    except Exception as exc:
        try:
            store.set_mission_status(tenant_id, mission_id, NEEDS_RECOVERY)
        except Exception:
            pass
        return {"ok": False, "state": NEEDS_RECOVERY,
                "plain": ("the mission finished its work but the final step "
                          "did not complete — it is marked as needing "
                          "recovery, not as completed"),
                "error": f"{type(exc).__name__}"}


def recover_partial_missions(store) -> dict:
    """Convert missions stranded in COMPLETING (crashed mid-transition)
    to NEEDS_RECOVERY, with a report. Idempotent."""
    try:
        rows = store._all("SELECT id, tenant_id FROM missions WHERE status=?",
                          (COMPLETING,))
    except Exception:
        return {"recovered": 0, "plain": "the mission list could not be read"}
    recovered = []
    for row in rows:
        try:
            artifacts = store.list_artifacts(row["tenant_id"], row["id"])
            store.set_mission_status(row["tenant_id"], row["id"], NEEDS_RECOVERY)
            recovered.append({"mission_id": row["id"],
                              "artifact_present": bool(artifacts)})
        except Exception:
            continue
    return {"recovered": len(recovered), "details": recovered,
            "plain": (f"{len(recovered)} mission(s) left half-finished by an "
                      "interruption were marked as needing recovery"
                      if recovered else "no half-finished missions were found")}


def mission_completion_state(store, tenant_id: str, mission_id: str) -> dict:
    """Read a mission's completion state honestly: completed implies
    an artifact exists, or the state says needs_recovery."""
    mission = store.get_mission(tenant_id, mission_id)
    if mission is None:
        return {"state": "missing", "plain": "that mission no longer exists"}
    artifacts = store.list_artifacts(tenant_id, mission_id)
    status = mission["status"]
    if status == "completed" and not artifacts:
        return {"state": "inconsistent",
                "plain": "this mission says completed but has no result — "
                         "it needs recovery (this should never happen)"}
    if status == NEEDS_RECOVERY:
        return {"state": NEEDS_RECOVERY, "artifact_present": bool(artifacts),
                "plain": ("this mission's work is done but its final step "
                          "did not complete — it needs recovery")}
    return {"state": status, "artifact_present": bool(artifacts),
            "plain": f"mission is {status}"}


# ------------------------------------------------------------ connection recovery
def recover_connection(store, fn=None) -> dict:
    """Database connection recovery: if the store's connection is dead
    (a ping fails), reopen it from the remembered path and retry once
    — a dropped connection must not crash the mission. ``fn`` is an
    optional callable run after recovery (its result is returned).
    Postgres recovery is left to the driver's pool: without a stored
    DSN the function reports honestly instead of pretending."""
    try:
        store._one("SELECT 1 AS x")
        alive = True
    except Exception:
        alive = False
    if alive:
        result = fn() if fn else None
        return {"recovered": False, "plain": "connection was already alive",
                "result": result}
    try:
        if store.pg:
            return {"recovered": False,
                    "plain": ("the database connection dropped and this "
                              "harness cannot reopen it on its own — "
                              "the mission was not crashed, but it needs "
                              "a restart of the database connection")}
        import sqlite3 as _sqlite3
        try:
            store._conn.close()
        except Exception:
            pass
        store._conn = _sqlite3.connect(store.db_path, check_same_thread=False)
        store._conn.row_factory = _sqlite3.Row
        store._one("SELECT 1 AS x")
        result = fn() if fn else None
        return {"recovered": True,
                "plain": "the database connection dropped and was reopened — the mission continues",
                "result": result}
    except Exception as exc:
        return {"recovered": False,
                "plain": f"the database connection could not be reopened: {exc}"}


# ------------------------------------------------------------ migrations
@dataclass
class Migration:
    version: int
    description: str
    up_sql: str
    down_sql: str  # the rollback step — a migration without one is refused


class MigrationRegistry:
    """Migration rollback planning: register up AND down together;
    ``rollback_plan`` returns the down-steps for a version range
    before anything runs."""

    def __init__(self):
        self._migrations: dict[int, Migration] = {}

    def register(self, migration: Migration) -> None:
        if not migration.down_sql.strip():
            raise ValueError("a migration without a rollback step is refused")
        self._migrations[migration.version] = migration

    def rollback_plan(self, from_version: int, to_version: int) -> list[dict]:
        steps = [m for v, m in sorted(self._migrations.items(), reverse=True)
                 if to_version < v <= from_version]
        return [{"version": m.version, "description": m.description,
                 "down_sql": m.down_sql} for m in steps]

    def check_applied(self, store, version: int) -> dict:
        from .reliability import check_schema_version
        result = check_schema_version(store, version)
        return result


# ------------------------------------------------------------ pool + timeout
class BoundedPool:
    """Connection-pool limits: at most ``max_connections`` checkouts,
    each waiting at most ``wait_timeout`` seconds. Exhaustion is a
    plain refusal, never an unbounded queue."""

    def __init__(self, factory, max_connections: int = 5,
                 wait_timeout: float = 5.0):
        self._factory = factory
        self.max_connections = max_connections
        self.wait_timeout = wait_timeout
        self._semaphore = threading.Semaphore(max_connections)
        self._idle: list = []
        self._lock = threading.Lock()

    def acquire(self):
        if not self._semaphore.acquire(timeout=self.wait_timeout):
            raise TimeoutError("no database connection became free in time — "
                               "too many callers at once; try again shortly")
        with self._lock:
            if self._idle:
                return self._idle.pop()
        try:
            return self._factory()
        except Exception:
            self._semaphore.release()
            raise

    def release(self, conn) -> None:
        with self._lock:
            self._idle.append(conn)
        self._semaphore.release()

    def __enter__(self):
        self._conn = self.acquire()
        return self._conn

    def __exit__(self, *exc):
        self.release(self._conn)
        return False


def run_query_with_timeout(store, sql: str, params: tuple = (),
                           timeout_ms: int = 5000) -> list[dict]:
    """Query-timeout enforcement (SQLite path): a progress handler
    aborts the query past ``timeout_ms``. Raises TimeoutError with a
    plain message; other backends raise the same error when the
    driver reports it."""
    if store.pg:
        # psycopg autocommit path has no progress hook; the timeout is
        # enforced by the caller-supplied statement timeout instead.
        try:
            return store._all(sql, params)
        except Exception as exc:
            raise TimeoutError(f"that query took too long: {exc}") from exc
    conn = store._conn
    deadline = time.monotonic() + timeout_ms / 1000.0
    calls = {"n": 0}

    def _progress():
        calls["n"] += 1
        if time.monotonic() > deadline:
            return 1
        return 0

    # sqlite3 offers no get_progress_handler: the handler is cleared
    # afterwards (nothing else in this codebase installs one).
    try:
        conn.set_progress_handler(_progress, 1000)
        cur = conn.execute(store._sql(sql), params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise TimeoutError(
                f"that query took longer than {timeout_ms}ms and was stopped") from exc
        raise
    finally:
        try:
            conn.set_progress_handler(None, 0)
        except Exception:
            pass


def index_usage_report(store) -> dict:
    """Index-usage monitoring — honestly scoped. SQLite keeps no
    per-index use counters, so this reports what IS knowable: which
    indexes exist, whether ANALYZE statistics exist, and what the
    query planner says about the key queries. It never invents a
    counter. On Postgres, real ``pg_stat_user_indexes`` counters are
    used when available."""
    if store.pg:
        try:
            rows = store._all(
                "SELECT indexrelname AS name, idx_scan AS scans"
                " FROM pg_stat_user_indexes")
            return {"backend": "postgres",
                    "indexes": [{"name": r["name"], "scans": r["scans"]} for r in rows],
                    "plain": "index use counted from the database's own statistics"}
        except Exception:
            pass
    indexes = store._all(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    stats = store._one("SELECT COUNT(*) AS n FROM sqlite_master WHERE name='sqlite_stat1'")
    plans = {}
    for sql in ("SELECT * FROM missions WHERE tenant_id=? ORDER BY created_at DESC",
                "SELECT * FROM jobs WHERE status=? AND available_at<=?"):
        try:
            rows = store._all("EXPLAIN QUERY PLAN " + sql, ("x",) if "missions" in sql else ("queued", "2000-01-01"))
            plans[sql.split("FROM")[1].split("WHERE")[0].strip()] = [r.get("detail", "") for r in rows]
        except Exception:
            continue
    return {"backend": "sqlite",
            "indexes": [{"name": r["name"], "table": r["tbl_name"]} for r in indexes],
            "analyze_stats_present": bool(stats and stats["n"]),
            "planner_notes": plans,
            "plain": ("SQLite does not count index use, so this lists the "
                      "indexes and the planner's choices — run ANALYZE for "
                      "fresh statistics")}


# ------------------------------------------------------------ pagination
def paginate(store, table: str, cursor: str | None, limit: int = 50,
             tenant_id: str | None = None) -> dict:
    """Keyset pagination, consistent under changing datasets: the
    cursor is the last (created_at, id) seen, so rows inserted mid-walk
    never duplicate or skip a page (offset pagination cannot promise
    that). Returns rows + the next cursor."""
    limit = max(1, min(limit, 200))
    if cursor:
        try:
            created_at, last_id = cursor.rsplit("|", 1)
        except ValueError:
            created_at, last_id = "", ""
        where = "((created_at < ?) OR (created_at = ? AND id > ?))"
        params: list = [created_at, created_at, last_id]
    else:
        where = "1=1"
        params = []
    if tenant_id is not None:
        where += " AND tenant_id=?"
        params.append(tenant_id)
    rows = store._all(
        f"SELECT * FROM {table} WHERE {where} ORDER BY created_at DESC, id ASC LIMIT ?",
        tuple(params + [limit + 1]))
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last.get('created_at', '')}|{last.get('id', '')}"
    return {"rows": page, "next_cursor": next_cursor, "has_more": has_more}


# ------------------------------------------------------------ soft delete + orphans
def ensure_deleted_at(store, table: str) -> bool:
    try:
        if "deleted_at" in _columns(store, table):
            return True
        store._exec(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
        return True
    except Exception:
        return False


def soft_delete(store, table: str, row_id: str, id_column: str = "id") -> dict:
    """Soft-delete handling: the row is marked deleted (with a time),
    not erased — and every active listing must filter it out."""
    if not ensure_deleted_at(store, table):
        return {"ok": False, "plain": "that kind of record cannot be marked deleted"}
    cur = store._exec(f"UPDATE {table} SET deleted_at=? WHERE {id_column}=?",
                      (now_iso(), row_id))
    if (cur.rowcount or 0) == 0:
        return {"ok": False, "plain": "that record no longer exists"}
    return {"ok": True, "plain": "marked deleted — it no longer appears in lists, but it is not erased"}


def list_active(store, table: str, tenant_id: str | None = None,
                limit: int = 50) -> list[dict]:
    """List rows excluding soft-deleted ones."""
    ensure_deleted_at(store, table)
    if tenant_id is not None:
        return store._all(f"SELECT * FROM {table} WHERE tenant_id=? AND deleted_at IS NULL"
                          f" ORDER BY created_at DESC LIMIT ?", (tenant_id, limit))
    return store._all(f"SELECT * FROM {table} WHERE deleted_at IS NULL"
                      f" ORDER BY created_at DESC LIMIT ?", (limit,))


def cleanup_orphans(store) -> dict:
    """Orphan-record cleanup: tasks and artifacts whose mission no
    longer exists are removed, with a counted report. Nothing else is
    touched."""
    report: dict = {}
    for table, column in (("tasks", "mission_id"), ("artifacts", "mission_id")):
        try:
            orphans = store._all(
                f"SELECT id FROM {table} WHERE {column} IS NOT NULL AND {column} NOT IN"
                f" (SELECT id FROM missions)")
            for row in orphans:
                store._exec(f"DELETE FROM {table} WHERE id=?", (row["id"],))
            report[table] = len(orphans)
        except Exception:
            report[table] = "could not be checked"
    total = sum(v for v in report.values() if isinstance(v, int))
    return {"removed": report, "total": total,
            "plain": (f"{total} orphan record(s) were cleaned up"
                      if total else "no orphan records were found")}
