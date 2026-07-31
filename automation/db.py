"""Storage backend selection — Postgres (production) or SQLite (local fallback).

The backend is chosen by ``DATABASE_URL``: when it is set (e.g. a Supabase
Postgres URL) all durable automation state lives in Postgres; otherwise a local
SQLite file is used so dev and tests need no server. The workflow + OAuth-token
stores talk ONLY to this module, so every dialect difference (placeholder style,
transactions, connection setup, schema types) is contained here.

Design goals:
  * identical semantics on both backends (per-user isolation, transactions and
    the durable idempotency ledger behave the same);
  * one connection per thread (neither sqlite3 nor a single psycopg connection is
    safe to share across threads);
  * SQL is written once with ``?`` placeholders and portable ``CREATE TABLE IF
    NOT EXISTS`` DDL; the ``?`` is rewritten to ``%s`` for Postgres.
"""

import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQLITE_DEFAULT = os.path.join(_ROOT, "automation.db")


def _env_true(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def _sqlite_retry(fn):
    """Run a SQLite write, retrying briefly on a transient ``database is locked``.

    ``busy_timeout`` serialises most WAL writers, but a few contention edges
    (e.g. a write-lock upgrade racing a checkpoint) still surface an immediate
    ``SQLITE_BUSY`` the busy handler never sees. A concurrent double-submit must
    not turn that into a 500, so we retry with a short backoff before giving up.
    Postgres never takes this path. Deterministic and bounded (~1.5s worst case).
    """
    delay = 0.02
    for attempt in range(8):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.4)


def database_url() -> str:
    # Ensure .env.local/.env are loaded before we read DATABASE_URL, so every
    # entrypoint (server, worker, migrate, verifier) selects the SAME backend
    # regardless of who remembered to load dotenv. Idempotent + cheap.
    from config.env import load_env
    load_env()
    return (os.environ.get("DATABASE_URL") or "").strip()


def is_postgres() -> bool:
    # AUTOMATION_FORCE_SQLITE lets the offline test suite stay on SQLite even when a
    # real DATABASE_URL is present in the environment/.env.
    if _env_true("AUTOMATION_FORCE_SQLITE"):
        return False
    return bool(database_url())


def _looks_like_test() -> bool:
    """True when we are almost certainly running the test suite — under ANY runner
    (pytest, ``python -m unittest``, or ``python tests/test_x.py``). The deployed
    app never imports pytest or a ``test_*`` module, so this stays False in prod.

    This backs the fail-closed guard in ``_open``: a test must never silently open
    a connection to the real database just because DATABASE_URL happens to be set
    and someone forgot ``AUTOMATION_FORCE_SQLITE=1``."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    main = sys.modules.get("__main__")
    main_file = (getattr(main, "__file__", "") or "").replace("\\", "/").lower()
    if "/tests/" in main_file or os.path.basename(main_file).startswith("test_"):
        return True                          # python tests/test_x.py
    if getattr(main, "__package__", "") == "unittest" or main_file.endswith("unittest/__main__.py"):
        return True                          # python -m unittest ...
    # A test module is on the import graph (pytest/unittest discovery).
    for name in list(sys.modules):
        base = name.rsplit(".", 1)[-1]
        if name == "tests" or name.startswith("tests.") or base.startswith("test_"):
            return True
    return False


def _assert_not_test_hitting_prod() -> None:
    """Fail closed: refuse to open a Postgres connection from the test suite.

    Backend selection (``is_postgres``) is deliberately NOT changed — a test may
    assert it *selects* Postgres from a fake URL without connecting. This guard
    fires only at real connect time, and only in a test context, so production is
    never affected. Escape hatch: ``AUTOMATION_ALLOW_PROD_DB=1`` for the rare test
    that genuinely means to touch a real database (e.g. a live-integration check)."""
    if _looks_like_test() and not _env_true("AUTOMATION_ALLOW_PROD_DB"):
        raise RuntimeError(
            "Refusing to open a Postgres connection from the test suite: "
            "DATABASE_URL points at a real database and AUTOMATION_FORCE_SQLITE is "
            "not forcing SQLite, so this run would read/write PRODUCTION. Set "
            "AUTOMATION_FORCE_SQLITE=1 (normal for tests), or — only if you truly "
            "intend to hit the real database — AUTOMATION_ALLOW_PROD_DB=1."
        )


def backend() -> str:
    return "postgres" if is_postgres() else "sqlite"


def sqlite_path() -> str:
    return os.environ.get("AUTOMATION_DB_PATH") or _SQLITE_DEFAULT


def _pg_dsn() -> str:
    """The Postgres DSN, defaulting to sslmode=require (Supabase needs TLS)."""
    dsn = database_url()
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    return dsn


def _is_pooler(dsn: str = None) -> bool:
    """Supabase's transaction pooler (pgbouncer) runs on port 6543 and its host
    contains 'pooler'. In transaction pooling mode, server-side PREPARED statements
    are unsafe (a connection is not pinned across statements), so psycopg's implicit
    prepared statements must be disabled — otherwise you get intermittent
    'prepared statement "_pg3_N" already exists' errors under load."""
    dsn = dsn or database_url()
    return ":6543" in dsn or "pooler.supabase.com" in dsn


# ── Per-thread connection + uniform API ────────────────────────────────
class Database:
    """A thin, thread-safe handle over either Postgres or SQLite.

    ``sqlite_path`` only matters for the SQLite backend; when DATABASE_URL is set
    it is ignored and Postgres is used. Tests pass a temp path to isolate state.
    """

    def __init__(self, sqlite_path: str = None):
        self.backend = backend()
        self._sqlite_path = sqlite_path or globals()["sqlite_path"]()
        self._local = threading.local()

    # -- connection (lazy, per thread) ----------------------------------
    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None or not self._alive(conn):
            conn = self._open()
            self._local.conn = conn
        return conn

    def _alive(self, conn) -> bool:
        if self.backend == "postgres":
            return not getattr(conn, "closed", False)
        return True

    def _open(self):
        if self.backend == "postgres":
            _assert_not_test_hitting_prod()   # fail closed: tests never reach prod
            import psycopg
            from psycopg.rows import dict_row
            # autocommit=True: standalone statements commit immediately (matching
            # the SQLite isolation_level=None behaviour); explicit blocks use tx().
            # prepare_threshold=None disables implicit prepared statements so the
            # Supabase transaction pooler (pgbouncer) works correctly.
            kwargs = {"autocommit": True, "row_factory": dict_row}
            if _is_pooler():
                kwargs["prepare_threshold"] = None
            return psycopg.connect(_pg_dsn(), **kwargs)
        conn = sqlite3.connect(self._sqlite_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _adapt(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    # -- reads ----------------------------------------------------------
    def query(self, sql: str, params=()) -> list:
        cur = self._conn().execute(self._adapt(sql), tuple(params))
        return list(cur.fetchall())

    def query_one(self, sql: str, params=()):
        cur = self._conn().execute(self._adapt(sql), tuple(params))
        return cur.fetchone()

    # -- single autocommit write ---------------------------------------
    def execute(self, sql: str, params=()) -> int:
        adapted = self._adapt(sql)
        if self.backend == "sqlite":
            return _sqlite_retry(lambda: self._conn().execute(adapted, tuple(params)).rowcount)
        cur = self._conn().execute(adapted, tuple(params))
        return cur.rowcount

    # -- transactional block (dialect-neutral) -------------------------
    @contextmanager
    def tx(self):
        """A single atomic unit of work. ``with db.tx() as t: t.execute(...)``.
        Commits on clean exit, rolls back on any exception."""
        conn = self._conn()
        if self.backend == "postgres":
            with conn.transaction():
                yield _Tx(conn, self._adapt)
        else:
            _sqlite_retry(lambda: conn.execute("BEGIN IMMEDIATE"))
            try:
                yield _Tx(conn, self._adapt)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def executescript(self, script: str) -> None:
        """Run a multi-statement DDL script (used by schema + migrations)."""
        if self.backend == "postgres":
            conn = self._conn()
            for stmt in _split_statements(script):
                conn.execute(stmt)
        else:
            self._conn().executescript(script)

    # -- schema the stores rely on (idempotent; safe on both backends) --
    def ensure_core_schema(self) -> None:
        self.executescript(CORE_SCHEMA % {"serial_pk": _SERIAL_PK[self.backend]})


class _Tx:
    def __init__(self, conn, adapt):
        self._conn, self._adapt = conn, adapt

    def execute(self, sql: str, params=()):
        return self._conn.execute(self._adapt(sql), tuple(params))


def _split_statements(script: str) -> list:
    """Split a DDL script into individual statements (Postgres has no
    executescript). Naive ';' split is fine for our simple, comment-light DDL."""
    out, buf = [], []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            out.append("\n".join(buf).rstrip().rstrip(";"))
            buf = []
    if buf:
        tail = "\n".join(buf).strip().rstrip(";")
        if tail:
            out.append(tail)
    return [s for s in out if s.strip()]


# ── Portable core schema (the tables the running code uses) ────────────
# Types are chosen to be valid on BOTH engines: TEXT everywhere, DOUBLE PRECISION
# for epoch timestamps (SQLite gives it REAL affinity), and a per-backend serial
# PK for the append-only events table.
_SERIAL_PK = {
    "sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "postgres": "BIGSERIAL PRIMARY KEY",
}

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    state       TEXT NOT NULL,
    next_run_at DOUBLE PRECISION,
    updated_at  DOUBLE PRECISION NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wf_user ON workflows(user_id);
CREATE INDEX IF NOT EXISTS ix_wf_due  ON workflows(state, next_run_at);

CREATE TABLE IF NOT EXISTS events (
    id          %(serial_pk)s,
    workflow_id TEXT NOT NULL,
    ts          DOUBLE PRECISION NOT NULL,
    type        TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_ev_wf ON events(workflow_id, id);

CREATE TABLE IF NOT EXISTS processed (
    key TEXT PRIMARY KEY,
    ts  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    account_email  TEXT NOT NULL,
    access_enc     TEXT,
    refresh_enc    TEXT,
    expires_at     DOUBLE PRECISION,
    scopes         TEXT,
    status         TEXT NOT NULL,
    watch_state    TEXT,
    created_at     DOUBLE PRECISION NOT NULL,
    updated_at     DOUBLE PRECISION NOT NULL,
    UNIQUE(user_id, provider, account_email)
);
CREATE INDEX IF NOT EXISTS ix_tok_user ON oauth_accounts(user_id);
CREATE INDEX IF NOT EXISTS ix_tok_prov ON oauth_accounts(provider, status);
"""


# A process-wide default handle (stores can share one).
_default = None


def default() -> "Database":
    global _default
    if _default is None:
        _default = Database()
    return _default


def reset_default() -> None:
    """Test hook: drop the cached handle so the next call re-reads the backend."""
    global _default
    _default = None
