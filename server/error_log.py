"""Durable capture of unhandled server errors — so a real 500 is noticed same-day.

Without this, bugs are found by staring at a terminal and a public user just leaves.
This records every unhandled exception to a small ``error_log`` table (queryable via
the admin endpoint / CLI) and, if ``ERROR_WEBHOOK_URL`` is set, fires a compact
alert to Slack/Discord so you actually see it the same day.

Best-effort and self-contained: recording an error must never raise (that would
mask the original failure), and the webhook is fired on a background daemon thread
so it never delays the error response.
"""

import logging
import os
import threading
import time

from automation.db import Database

log = logging.getLogger("saqua.errors")

DDL = """
CREATE TABLE IF NOT EXISTS error_log (
    id         %(serial_pk)s,
    ts         DOUBLE PRECISION NOT NULL,
    path       TEXT,
    method     TEXT,
    status     INTEGER,
    user_id    TEXT,
    error_type TEXT,
    message    TEXT,
    traceback  TEXT
);
CREATE INDEX IF NOT EXISTS ix_errlog_ts ON error_log(ts);
"""

_SERIAL_PK = {
    "sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "postgres": "BIGSERIAL PRIMARY KEY",
}

_ensured = False
_MAX_TB = 8000
_MAX_MSG = 1000


def _db(db=None) -> Database:
    return db or Database()


def ensure(db: Database = None, *, force: bool = False) -> None:
    global _ensured
    if _ensured and not force:
        return
    try:
        d = _db(db)
        d.executescript(DDL % {"serial_pk": _SERIAL_PK[d.backend]})
        _ensured = True
    except Exception:  # noqa: BLE001
        pass


def reset_ensured() -> None:      # test hook
    global _ensured
    _ensured = False


def record_error(*, path=None, method=None, status=500, user_id=None,
                 error_type=None, message=None, tb=None, db: Database = None,
                 notify: bool = True) -> None:
    """Persist one unhandled error and (best-effort) fire the alert webhook."""
    try:
        d = _db(db)
        ensure(d)
        d.execute(
            "INSERT INTO error_log (ts, path, method, status, user_id, error_type, "
            "message, traceback) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), path, method, int(status or 500), user_id,
             (error_type or "")[:200], (message or "")[:_MAX_MSG],
             (tb or "")[:_MAX_TB]))
    except Exception:  # noqa: BLE001 - never mask the original error
        pass
    # A loud, greppable server-log line even when nobody is watching the webhook.
    log.error("unhandled_error status=%s method=%s path=%s type=%s msg=%s",
              status, method, path, error_type, (message or "")[:200])
    if notify:
        _fire_webhook(path, method, status, error_type, message)


def recent(limit: int = 50, *, db: Database = None) -> list:
    d = _db(db); ensure(d)
    try:
        rows = d.query(
            "SELECT ts, path, method, status, user_id, error_type, message "
            "FROM error_log ORDER BY ts DESC LIMIT ?", (int(limit),))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows or []:
        g = (lambda k, i, rr=r: rr[k] if hasattr(rr, "keys") else rr[i])
        out.append({"ts": g("ts", 0), "path": g("path", 1), "method": g("method", 2),
                    "status": g("status", 3), "user_id": g("user_id", 4),
                    "error_type": g("error_type", 5), "message": g("message", 6)})
    return out


def count_since(since: float, *, db: Database = None) -> int:
    d = _db(db); ensure(d)
    try:
        row = d.query_one("SELECT COUNT(*) FROM error_log WHERE ts>=?", (since,))
        if not row:
            return 0
        return int(list(row.values())[0] if hasattr(row, "values") else row[0])
    except Exception:  # noqa: BLE001
        return 0


# ── alerting (optional; ERROR_WEBHOOK_URL) ─────────────────────────────
def _fire_webhook(path, method, status, error_type, message) -> None:
    url = (os.environ.get("ERROR_WEBHOOK_URL") or "").strip()
    if not url:
        return
    text = (f"🔴 Saqua {status} on {method} {path}\n"
            f"{error_type}: {(message or '')[:300]}")
    # Slack expects {"text"}, Discord expects {"content"} — send both keys so one
    # webhook config works for either. Fire on a daemon thread; never block/raise.
    payload = {"text": text, "content": text}

    def _send():
        try:
            import requests
            requests.post(url, json=payload, timeout=5)
        except Exception:  # noqa: BLE001 - alerting must never break the app
            pass

    try:
        threading.Thread(target=_send, name="error-webhook", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
