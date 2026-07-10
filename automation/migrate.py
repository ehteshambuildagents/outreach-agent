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
    if verbose and not ran:
        print("  (no pending migrations)")
    return ran


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
