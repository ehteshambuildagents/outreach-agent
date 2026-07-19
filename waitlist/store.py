"""Persistence for the pre-launch waitlist (double opt-in).

One table, provisioned idempotently at first use (same self-provision pattern as
access/limits/telemetry), on the shared ``automation.db.Database``:

  * ``waitlist`` — one row per email address, carrying the opt-in ``status``
    (unconfirmed | subscribed | unsubscribed | bounced) and the timestamps that
    make the launch broadcast resumable and non-repeating.

Keyed by the NORMALIZED email, deliberately not by a user id. A waitlist signup
happens before any account exists, so there is no Clerk user to key on — which is
exactly why this cannot reuse ``access.pending_users`` (that table is keyed by
``user_id`` and only ever populated after signup).

``token`` is a per-row random secret carried in the confirm and unsubscribe links.
It means neither URL has to contain the email address, and it needs no server-side
signing key to verify: possession of the token is the proof.

Every function is defensive: a read returns a safe default and a write reports
failure rather than raising, so a storage hiccup can never 500 a public endpoint.
"""

import secrets
import time

from automation.db import Database

UNCONFIRMED, SUBSCRIBED, UNSUBSCRIBED, BOUNCED = (
    "unconfirmed", "subscribed", "unsubscribed", "bounced")

DDL = """
CREATE TABLE IF NOT EXISTS waitlist (
    email           TEXT PRIMARY KEY,
    token           TEXT NOT NULL,
    status          TEXT NOT NULL,          -- unconfirmed | subscribed | unsubscribed | bounced
    source          TEXT,
    created_at      DOUBLE PRECISION NOT NULL,
    confirmed_at    DOUBLE PRECISION,
    notified_at     DOUBLE PRECISION,
    unsubscribed_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_waitlist_status ON waitlist(status, created_at);
CREATE INDEX IF NOT EXISTS ix_waitlist_token ON waitlist(token);
"""

_COLS = ("email, token, status, source, created_at, confirmed_at, notified_at, "
         "unsubscribed_at")

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


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _row(row) -> dict:
    g = (lambda k, i: row[k] if hasattr(row, "keys") else row[i])
    return {"email": g("email", 0), "token": g("token", 1),
            "status": g("status", 2), "source": g("source", 3),
            "created_at": g("created_at", 4), "confirmed_at": g("confirmed_at", 5),
            "notified_at": g("notified_at", 6),
            "unsubscribed_at": g("unsubscribed_at", 7)}


def get(email: str, *, db: Database = None) -> dict:
    d = _db(db); ensure(d)
    try:
        row = d.query_one(f"SELECT {_COLS} FROM waitlist WHERE email=?", (email,))
    except Exception:  # noqa: BLE001
        return None
    return _row(row) if row else None


def get_by_token(token: str, *, db: Database = None) -> dict:
    """Look a row up by its link token. Returns None for an empty/unknown token."""
    if not token:
        return None
    d = _db(db); ensure(d)
    try:
        row = d.query_one(f"SELECT {_COLS} FROM waitlist WHERE token=?", (token,))
    except Exception:  # noqa: BLE001
        return None
    return _row(row) if row else None


def create_unconfirmed(email: str, source: str = None, *, db: Database = None) -> dict:
    """Insert a first-seen address as ``unconfirmed``. Idempotent: an address that
    already exists is returned untouched, so a re-submit never resets someone's
    status or rotates the token out from under a confirm link already in flight.

    Returns the row (existing or new), or None if the write failed.
    """
    d = _db(db); ensure(d)
    try:
        existing = get(email, db=d)
        if existing:
            return existing
        token = new_token()
        d.execute(
            "INSERT INTO waitlist (email, token, status, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, token, UNCONFIRMED, source, time.time()))
        return get(email, db=d)
    except Exception:  # noqa: BLE001
        return None


def confirm(email: str, *, db: Database = None) -> bool:
    """Move unconfirmed -> subscribed. Never resurrects an unsubscribed address:
    someone who opted out stays out until they deliberately join again."""
    d = _db(db); ensure(d)
    try:
        now = time.time()
        return bool(d.execute(
            "UPDATE waitlist SET status=?, confirmed_at=? "
            "WHERE email=? AND status=?",
            (SUBSCRIBED, now, email, UNCONFIRMED)))
    except Exception:  # noqa: BLE001
        return False


def unsubscribe(email: str, *, db: Database = None) -> bool:
    d = _db(db); ensure(d)
    try:
        now = time.time()
        return bool(d.execute(
            "UPDATE waitlist SET status=?, unsubscribed_at=? WHERE email=?",
            (UNSUBSCRIBED, now, email)))
    except Exception:  # noqa: BLE001
        return False


def mark_notified(email: str, *, db: Database = None) -> bool:
    """Stamp the launch send. The broadcast selects on ``notified_at IS NULL``, so
    this is what makes a resumed run skip everyone already emailed."""
    d = _db(db); ensure(d)
    try:
        return bool(d.execute(
            "UPDATE waitlist SET notified_at=? WHERE email=? AND notified_at IS NULL",
            (time.time(), email)))
    except Exception:  # noqa: BLE001
        return False


def mark_bounced(email: str, *, db: Database = None) -> bool:
    d = _db(db); ensure(d)
    try:
        return bool(d.execute(
            "UPDATE waitlist SET status=? WHERE email=?", (BOUNCED, email)))
    except Exception:  # noqa: BLE001
        return False


def pending_broadcast(*, db: Database = None, limit: int = 1000) -> list:
    """Confirmed subscribers who have not yet been sent the launch email."""
    d = _db(db); ensure(d)
    try:
        rows = d.query(
            f"SELECT {_COLS} FROM waitlist WHERE status=? AND notified_at IS NULL "
            "ORDER BY created_at ASC LIMIT ?", (SUBSCRIBED, limit))
    except Exception:  # noqa: BLE001
        return []
    return [_row(r) for r in rows or []]


def list_by_status(status: str = None, *, db: Database = None, limit: int = 500) -> list:
    d = _db(db); ensure(d)
    try:
        if status:
            rows = d.query(
                f"SELECT {_COLS} FROM waitlist WHERE status=? "
                "ORDER BY created_at ASC LIMIT ?", (status, limit))
        else:
            rows = d.query(
                f"SELECT {_COLS} FROM waitlist ORDER BY created_at ASC LIMIT ?",
                (limit,))
    except Exception:  # noqa: BLE001
        return []
    return [_row(r) for r in rows or []]


def counts(*, db: Database = None) -> dict:
    """Row counts per status — the number the admin view and the broadcast
    pre-flight both report."""
    d = _db(db); ensure(d)
    try:
        rows = d.query("SELECT status, COUNT(*) AS n FROM waitlist GROUP BY status")
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows or []:
        g = (lambda k, i, rr=r: rr[k] if hasattr(rr, "keys") else rr[i])
        out[g("status", 0)] = int(g("n", 1))
    return out
