"""Migration runner — provisions the database schema, version-tracked.

Applies the operational core schema (:func:`automation.db.Database.ensure_core_schema`)
plus every numbered ``.sql`` file in ``automation/migrations/`` that hasn't run
yet, recording each in a ``schema_migrations`` table so re-running is a safe no-op.
Works against whichever backend ``DATABASE_URL`` selects (Postgres in production,
SQLite locally).

    python -m automation.migrate            # apply all pending migrations
    python -m automation.migrate --status   # show applied vs pending (no changes)

Design note: migrations are written in portable DDL (``CREATE TABLE IF NOT
EXISTS``, TEXT/DOUBLE PRECISION types) so the same files run on both engines.
"""

import os
import sys
import time

from automation.db import Database

_MIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def _files() -> list:
    if not os.path.isdir(_MIG_DIR):
        return []
    return sorted(f for f in os.listdir(_MIG_DIR) if f.endswith(".sql"))


def _ensure_ledger(db: Database) -> None:
    db.executescript(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL);")


# Columns added to pre-existing tables. Kept in code (not raw ALTER in a .sql file)
# so the add is idempotent on BOTH engines: SQLite has no "ADD COLUMN IF NOT EXISTS",
# and a portable .sql line cannot branch per dialect. Each entry is
# (table, column, type) and is applied only when the column is genuinely absent.
_ENSURE_COLUMNS = (
    ("billing_subscriptions", "current_period_start", "DOUBLE PRECISION"),
)


def _column_exists(db: Database, table: str, column: str) -> bool:
    if db.backend == "postgres":
        rows = db.query(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=? AND column_name=?", (table, column))
        return bool(rows)
    # SQLite: PRAGMA table_info returns one row per column.
    rows = db.query(f"PRAGMA table_info({table})")
    return any(r["name"] == column for r in rows)


def ensure_schema_columns(db: Database) -> list:
    """Idempotently add any missing columns declared in :data:`_ENSURE_COLUMNS`.

    A genuine add-if-missing on Postgres (which also supports native ``ADD COLUMN IF
    NOT EXISTS``) and SQLite (which does not) — we check the catalog first, so
    re-running is always a no-op. Returns the columns actually added."""
    added = []
    for table, column, coltype in _ENSURE_COLUMNS:
        if not _column_exists(db, table, column):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            added.append(f"{table}.{column}")
    return added


def applied(db: Database) -> set:
    _ensure_ledger(db)
    return {r["version"] for r in db.query("SELECT version FROM schema_migrations")}


def pending(db: Database) -> list:
    done = applied(db)
    return [f for f in _files() if f not in done]


def run(db: Database = None, *, verbose: bool = True) -> list:
    """Apply core schema + all pending migrations. Returns the versions applied."""
    db = db or Database()
    db.ensure_core_schema()
    _ensure_ledger(db)
    done = applied(db)
    ran = []
    for fname in _files():
        if fname in done:
            continue
        with open(os.path.join(_MIG_DIR, fname), encoding="utf-8") as fh:
            script = fh.read()
        db.executescript(script)
        db.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                   (fname, time.time()))
        ran.append(fname)
        if verbose:
            print(f"  applied {fname}")
    # Idempotent column adds (run every time; a no-op once present).
    added = ensure_schema_columns(db)
    for col in added:
        if verbose:
            print(f"  added column {col}")
    if verbose and not ran and not added:
        print("  (no pending migrations)")
    return ran


# Tables the running app MUST have before it can safely enforce quota. If any are
# missing the deployment did not migrate, and the app must not serve (it would
# otherwise fail-open or silently fall back to local state).
_REQUIRED_TABLES = ("prospect_usage", "billing_subscriptions")


def _table_exists(db: Database, table: str) -> bool:
    try:
        if db.backend == "postgres":
            rows = db.query(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?", (table,))
            return bool(rows)
        rows = db.query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(rows)
    except Exception:  # noqa: BLE001 - an unreachable DB counts as "schema missing"
        return False


def verify_schema(db: Database = None) -> dict:
    """Check the quota-critical schema is present. Returns
    ``{"ok": bool, "missing_tables": [...], "missing_columns": [...]}``.

    Used by the app's startup guard so a process that came up against an unmigrated
    database reports UNHEALTHY instead of silently failing open on the quota."""
    db = db or Database()
    missing_tables = [t for t in _REQUIRED_TABLES if not _table_exists(db, t)]
    missing_columns = []
    if "billing_subscriptions" not in missing_tables:
        for table, column, _type in _ENSURE_COLUMNS:
            try:
                if not _column_exists(db, table, column):
                    missing_columns.append(f"{table}.{column}")
            except Exception:  # noqa: BLE001 - treat an errored catalog read as missing
                missing_columns.append(f"{table}.{column}")
    return {"ok": not missing_tables and not missing_columns,
            "missing_tables": missing_tables, "missing_columns": missing_columns}


def status(db: Database = None) -> dict:
    db = db or Database()
    done = sorted(applied(db))
    return {"backend": db.backend, "applied": done, "pending": pending(db)}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    db = Database()
    print(f"Database backend: {db.backend}"
          + (" (DATABASE_URL set)" if db.backend == "postgres"
             else " (local fallback — set DATABASE_URL for Postgres)"))
    try:
        if "--status" in argv:
            st = status(db)
            print("Applied:", ", ".join(st["applied"]) or "(none)")
            print("Pending:", ", ".join(st["pending"]) or "(none)")
            return 0
        print("Running migrations…")
        run(db)
        print("Schema is up to date.")
        return 0
    except Exception as exc:  # noqa: BLE001 - turn driver errors into guidance
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        print(f"\nERROR: could not run migrations — {first}", file=sys.stderr)
        if "password authentication failed" in str(exc):
            print("The DB host is reachable but the password is wrong. Put the real "
                  "Supabase password in DATABASE_URL (replace [YOUR-PASSWORD]).",
                  file=sys.stderr)
        elif "could not translate host name" in str(exc) or "Name or service" in str(exc):
            print("Host did not resolve. Use the Supabase *pooler* connection string "
                  "(…pooler.supabase.com:6543), not the direct db.<ref> host.",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
