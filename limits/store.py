"""Persistence for per-user usage metering and the account kill switch.

Two small tables, provisioned idempotently at first use via :func:`ensure` (the
same self-provision pattern telemetry/automation use), on the SAME database
(``automation.db.Database``) as everything else — no new connection layer:

  * ``usage_ledger``   — one append-only row per paid provider call (user, provider,
    estimated cost, count, ts). The single source of truth for the caps.
  * ``account_status`` — per-user active/paused state for the kill switch.

Every function is defensive: on any DB error a read returns a safe zero/empty and
a write is a no-op, so metering can never take down a request.
"""

import time

from automation.db import Database

DDL = """
CREATE TABLE IF NOT EXISTS usage_ledger (
    id        %(serial_pk)s,
    ts        DOUBLE PRECISION NOT NULL,
    user_id   TEXT NOT NULL,
    provider  TEXT NOT NULL,
    cost      DOUBLE PRECISION NOT NULL,
    cnt       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_user_ts      ON usage_ledger(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_usage_user_prov_ts ON usage_ledger(user_id, provider, ts);
CREATE INDEX IF NOT EXISTS ix_usage_ts           ON usage_ledger(ts);

CREATE TABLE IF NOT EXISTS account_status (
    user_id    TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    reason     TEXT,
    updated_at DOUBLE PRECISION NOT NULL
);
"""

_SERIAL_PK = {
    "sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "postgres": "BIGSERIAL PRIMARY KEY",
}

_ensured = False


def _db(db=None) -> Database:
    return db or Database()


def ensure(db: Database = None, *, force: bool = False) -> None:
    """Create the metering tables if absent. Idempotent, cheap, never raises."""
    global _ensured
    if _ensured and not force:
        return
    try:
        d = _db(db)
        d.executescript(DDL % {"serial_pk": _SERIAL_PK[d.backend]})
        _ensured = True
    except Exception:  # noqa: BLE001 - provisioning must never break the product
        pass


def reset_ensured() -> None:      # test hook
    global _ensured
    _ensured = False


# ── writes ─────────────────────────────────────────────────────────────
def add_usage(user_id: str, provider: str, cost: float, cnt: int = 1,
              ts: float = None, *, db: Database = None) -> None:
    try:
        d = _db(db)
        ensure(d)
        d.execute(
            "INSERT INTO usage_ledger (ts, user_id, provider, cost, cnt) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts if ts is not None else time.time(), user_id, provider,
             float(cost or 0.0), int(cnt or 1)))
    except Exception:  # noqa: BLE001
        pass


def set_state(user_id: str, state: str, reason: str = None,
              *, db: Database = None) -> None:
    """Upsert the account state (active | paused). Portable across both backends."""
    try:
        d = _db(db)
        ensure(d)
        now = time.time()
        # UPDATE-then-INSERT keeps the DDL identical on SQLite and Postgres (no
        # ON CONFLICT dialect differences); account_status is tiny and low-traffic.
        updated = d.execute(
            "UPDATE account_status SET state=?, reason=?, updated_at=? WHERE user_id=?",
            (state, reason, now, user_id))
        if not updated:
            d.execute(
                "INSERT INTO account_status (user_id, state, reason, updated_at) "
                "VALUES (?, ?, ?, ?)", (user_id, state, reason, now))
    except Exception:  # noqa: BLE001
        pass


# ── reads ──────────────────────────────────────────────────────────────
def _scalar(d, sql, params, default=0):
    try:
        row = d.query_one(sql, params)
        if not row:
            return default
        val = list(row.values())[0] if hasattr(row, "values") else row[0]
        return val if val is not None else default
    except Exception:  # noqa: BLE001
        return default


def provider_calls_since(user_id: str, provider: str, since: float,
                         *, db: Database = None) -> int:
    d = _db(db); ensure(d)
    return int(_scalar(
        d, "SELECT COALESCE(SUM(cnt),0) FROM usage_ledger "
           "WHERE user_id=? AND provider=? AND ts>=?",
        (user_id, provider, since)))


def spend_since(user_id: str, since: float, *, db: Database = None) -> float:
    d = _db(db); ensure(d)
    return float(_scalar(
        d, "SELECT COALESCE(SUM(cost),0) FROM usage_ledger WHERE user_id=? AND ts>=?",
        (user_id, since)))


def calls_since(user_id: str, since: float, *, db: Database = None) -> int:
    d = _db(db); ensure(d)
    return int(_scalar(
        d, "SELECT COALESCE(SUM(cnt),0) FROM usage_ledger WHERE user_id=? AND ts>=?",
        (user_id, since)))


def get_state(user_id: str, *, db: Database = None) -> dict:
    d = _db(db); ensure(d)
    try:
        row = d.query_one(
            "SELECT state, reason, updated_at FROM account_status WHERE user_id=?",
            (user_id,))
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return {"user_id": user_id, "state": "active", "reason": None,
                "updated_at": None}
    g = (lambda k, i: row[k] if hasattr(row, "keys") else row[i])
    return {"user_id": user_id, "state": g("state", 0), "reason": g("reason", 1),
            "updated_at": g("updated_at", 2)}


def median_hourly_calls(since: float, until: float, exclude_user: str = None,
                        *, db: Database = None) -> float:
    """Median of per-(user, hour) call volumes over a window — the kill switch's
    baseline for 'normal' hourly usage. Computed in Python from a small grouped
    result so it's identical on SQLite and Postgres."""
    d = _db(db); ensure(d)
    try:
        # Bucket each row into an integer hour and sum counts per (user, hour).
        sql = ("SELECT user_id, CAST(ts/3600 AS INTEGER) AS hr, SUM(cnt) AS c "
               "FROM usage_ledger WHERE ts>=? AND ts<?")
        params = [since, until]
        if exclude_user:
            sql += " AND user_id<>?"
            params.append(exclude_user)
        sql += " GROUP BY user_id, hr"
        rows = d.query(sql, tuple(params))
    except Exception:  # noqa: BLE001
        return 0.0
    vals = []
    for r in rows or []:
        v = r["c"] if hasattr(r, "keys") else r[2]
        if v:
            vals.append(float(v))
    if not vals:
        return 0.0
    vals.sort()
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def active_user_ids(since: float, *, db: Database = None) -> list:
    d = _db(db); ensure(d)
    try:
        rows = d.query(
            "SELECT DISTINCT user_id FROM usage_ledger WHERE ts>=?", (since,))
    except Exception:  # noqa: BLE001
        return []
    return [(r["user_id"] if hasattr(r, "keys") else r[0]) for r in rows or []]


def provider_breakdown_since(user_id: str, since: float, *, db: Database = None) -> dict:
    """{provider: {calls, cost}} for a user since ``since`` — for the admin view."""
    d = _db(db); ensure(d)
    try:
        rows = d.query(
            "SELECT provider, SUM(cnt) AS calls, SUM(cost) AS cost "
            "FROM usage_ledger WHERE user_id=? AND ts>=? GROUP BY provider",
            (user_id, since))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows or []:
        if hasattr(r, "keys"):
            out[r["provider"]] = {"calls": int(r["calls"] or 0),
                                  "cost": round(float(r["cost"] or 0.0), 5)}
        else:
            out[r[0]] = {"calls": int(r[1] or 0), "cost": round(float(r[2] or 0.0), 5)}
    return out
