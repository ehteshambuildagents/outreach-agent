"""Persistence for request-access gating (soft launch).

One table, provisioned idempotently at first use (same self-provision pattern as
telemetry/limits), on the shared ``automation.db.Database``:

  * ``pending_users`` — one row per user who has ever hit the app, with an access
    ``status`` (pending | approved | denied) and light audit timestamps.

Every function is defensive: a read returns a safe default and a write is a no-op
on any DB error, so access gating can never take down a request (it fails toward
the caller's chosen default, decided in :mod:`access`).
"""

import time

from automation.db import Database

DDL = """
CREATE TABLE IF NOT EXISTS pending_users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT,
    status       TEXT NOT NULL,          -- pending | approved | denied
    requested_at DOUBLE PRECISION NOT NULL,
    decided_at   DOUBLE PRECISION,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_pending_status ON pending_users(status, requested_at);
"""

_ensured = False


def _db(db=None) -> Database:
    return db or Database()


def ensure(db: Database = None, *, force: bool = False) -> None:
    global _ensured
    if _ensured and not force:
        return
    try:
        _db(db).executescript(DDL)
        _ensured = True
    except Exception:  # noqa: BLE001
        pass


def reset_ensured() -> None:      # test hook
    global _ensured
    _ensured = False


def get(user_id: str, *, db: Database = None) -> dict:
    d = _db(db); ensure(d)
    try:
        row = d.query_one(
            "SELECT user_id, email, status, requested_at, decided_at, note "
            "FROM pending_users WHERE user_id=?", (user_id,))
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    g = (lambda k, i: row[k] if hasattr(row, "keys") else row[i])
    return {"user_id": g("user_id", 0), "email": g("email", 1),
            "status": g("status", 2), "requested_at": g("requested_at", 3),
            "decided_at": g("decided_at", 4), "note": g("note", 5)}


def create_pending(user_id: str, email: str = None, status: str = "pending",
                   *, db: Database = None) -> bool:
    """Insert a first-seen user. Returns True if a row was created (idempotent:
    an existing user is left untouched)."""
    d = _db(db); ensure(d)
    try:
        if get(user_id, db=d):
            return False
        d.execute(
            "INSERT INTO pending_users (user_id, email, status, requested_at) "
            "VALUES (?, ?, ?, ?)", (user_id, email, status, time.time()))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_status(user_id: str, status: str, note: str = None,
               *, db: Database = None) -> None:
    d = _db(db); ensure(d)
    try:
        now = time.time()
        updated = d.execute(
            "UPDATE pending_users SET status=?, decided_at=?, note=? WHERE user_id=?",
            (status, now, note, user_id))
        if not updated:
            d.execute(
                "INSERT INTO pending_users (user_id, email, status, requested_at, "
                "decided_at, note) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, None, status, now, now, note))
    except Exception:  # noqa: BLE001
        pass


def list_by_status(status: str = None, *, db: Database = None, limit: int = 500) -> list:
    d = _db(db); ensure(d)
    try:
        if status:
            rows = d.query(
                "SELECT user_id, email, status, requested_at, decided_at, note "
                "FROM pending_users WHERE status=? ORDER BY requested_at ASC LIMIT ?",
                (status, limit))
        else:
            rows = d.query(
                "SELECT user_id, email, status, requested_at, decided_at, note "
                "FROM pending_users ORDER BY requested_at ASC LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows or []:
        g = (lambda k, i, rr=r: rr[k] if hasattr(rr, "keys") else rr[i])
        out.append({"user_id": g("user_id", 0), "email": g("email", 1),
                    "status": g("status", 2), "requested_at": g("requested_at", 3),
                    "decided_at": g("decided_at", 4), "note": g("note", 5)})
    return out
