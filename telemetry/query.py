"""Observability services — reusable, read-only queries over telemetry.

Backend only (no UI). Aggregates the raw telemetry tables (indexed) on read, so
there's no duplicated rollup storage, and REUSES the existing automation health /
metrics for worker + queue signals rather than re-deriving them. Every function is
defensive: on any error it returns a safe empty/zero value so a reporting call can
never take down a request.
"""

import time
from datetime import datetime, timezone

from automation.db import Database
from telemetry import schema


def _db(db=None) -> Database:
    db = db or Database()
    schema.ensure(db)
    return db


def _scalar(db, sql, params=(), default=0):
    try:
        row = db.query_one(sql, params)
        if not row:
            return default
        val = list(row.values())[0] if hasattr(row, "values") else row[0]
        return val if val is not None else default
    except Exception:  # noqa: BLE001
        return default


def _day_bounds(day=None):
    now = day if day is not None else time.time()
    start = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), start.timestamp() + 86400


def _month_bounds(when=None):
    now = when if when is not None else time.time()
    d = datetime.fromtimestamp(now, timezone.utc)
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc)
    nxt = datetime(d.year + (d.month == 12), (d.month % 12) + 1, 1, tzinfo=timezone.utc)
    return start.timestamp(), nxt.timestamp()


# ── cost / tokens ──────────────────────────────────────────────────────
def _spend(db, lo, hi, user_id):
    if user_id:
        return _scalar(db, "SELECT COALESCE(SUM(estimated_cost),0) FROM ai_requests "
                       "WHERE ts>=? AND ts<? AND user_id=?", (lo, hi, user_id))
    return _scalar(db, "SELECT COALESCE(SUM(estimated_cost),0) FROM ai_requests "
                   "WHERE ts>=? AND ts<?", (lo, hi))


def daily_spend(user_id=None, day=None, *, db=None) -> float:
    lo, hi = _day_bounds(day)
    return round(float(_spend(_db(db), lo, hi, user_id)), 6)


def monthly_spend(user_id=None, when=None, *, db=None) -> float:
    lo, hi = _month_bounds(when)
    return round(float(_spend(_db(db), lo, hi, user_id)), 6)


def total_tokens(user_id=None, since=None, *, db=None) -> int:
    db = _db(db)
    where, params = "1=1", []
    if user_id:
        where += " AND user_id=?"; params.append(user_id)
    if since is not None:
        where += " AND ts>=?"; params.append(since)
    return int(_scalar(db, f"SELECT COALESCE(SUM(total_tokens),0) FROM ai_requests WHERE {where}",
                       tuple(params)))


def campaign_cost(campaign_id, *, db=None) -> float:
    return round(float(_scalar(_db(db),
                 "SELECT COALESCE(SUM(estimated_cost),0) FROM ai_requests WHERE campaign_id=?",
                 (campaign_id,))), 6)


def agent_cost(agent, *, db=None) -> float:
    return round(float(_scalar(_db(db),
                 "SELECT COALESCE(SUM(estimated_cost),0) FROM ai_requests WHERE agent=?",
                 (agent,))), 6)


# ── performance / reliability ──────────────────────────────────────────
def avg_latency(agent=None, *, db=None) -> float:
    db = _db(db)
    if agent:
        return round(float(_scalar(db, "SELECT COALESCE(AVG(latency_ms),0) FROM ai_requests "
                                   "WHERE agent=? AND latency_ms IS NOT NULL", (agent,))), 2)
    return round(float(_scalar(db, "SELECT COALESCE(AVG(latency_ms),0) FROM ai_requests "
                               "WHERE latency_ms IS NOT NULL")), 2)


def failure_rate(*, db=None) -> float:
    db = _db(db)
    total = int(_scalar(db, "SELECT COUNT(*) FROM ai_requests"))
    if not total:
        return 0.0
    failed = int(_scalar(db, "SELECT COUNT(*) FROM ai_requests WHERE success=0"))
    return round(failed / total, 4)


def retry_rate(*, db=None) -> float:
    db = _db(db)
    total = int(_scalar(db, "SELECT COUNT(*) FROM ai_requests"))
    if not total:
        return 0.0
    retried = int(_scalar(db, "SELECT COUNT(*) FROM ai_requests WHERE retries>0"))
    return round(retried / total, 4)


# ── event-derived stats ────────────────────────────────────────────────
def _event_count(db, category, event, user_id=None):
    if user_id:
        return int(_scalar(db, "SELECT COUNT(*) FROM telemetry_events "
                           "WHERE category=? AND event=? AND user_id=?",
                           (category, event, user_id)))
    return int(_scalar(db, "SELECT COUNT(*) FROM telemetry_events WHERE category=? AND event=?",
                       (category, event)))


def guard_stats(user_id=None, *, db=None) -> dict:
    db = _db(db)
    return {
        "blocked": _event_count(db, "email", "guard_blocked", user_id),
        "duplicate_prevented": _event_count(db, "email", "duplicate_prevented", user_id),
        "sent": _event_count(db, "email", "sent", user_id),
    }


def reply_rate(user_id=None, *, db=None) -> float:
    db = _db(db)
    sent = _event_count(db, "email", "sent", user_id)
    if not sent:
        return 0.0
    return round(_event_count(db, "email", "replied", user_id) / sent, 4)


def bounce_rate(user_id=None, *, db=None) -> float:
    db = _db(db)
    sent = _event_count(db, "email", "sent", user_id)
    if not sent:
        return 0.0
    return round(_event_count(db, "email", "bounced", user_id) / sent, 4)


# ── automation / worker (REUSE existing health + metrics) ──────────────
def queue_health(*, store=None, worker=None) -> dict:
    try:
        from automation import health
        snap = health.snapshot(store=store, worker=worker)
        return {"status": snap["status"], "database": snap["checks"]["database"]["status"],
                "worker": snap["checks"]["worker"]["status"]}
    except Exception:  # noqa: BLE001
        return {"status": "unknown"}


def worker_health(*, worker=None) -> dict:
    try:
        from automation import health
        return health._worker_health(worker)
    except Exception:  # noqa: BLE001
        return {"status": "unknown"}


def automation_metrics() -> dict:
    try:
        from automation import metrics
        return metrics.snapshot()
    except Exception:  # noqa: BLE001
        return {}


def summary(user_id=None, *, db=None) -> dict:
    """A one-call operational snapshot (spend / tokens / reliability / guard)."""
    db = _db(db)
    return {
        "daily_spend": daily_spend(user_id, db=db),
        "monthly_spend": monthly_spend(user_id, db=db),
        "total_tokens": total_tokens(user_id, db=db),
        "avg_latency_ms": avg_latency(db=db),
        "failure_rate": failure_rate(db=db),
        "retry_rate": retry_rate(db=db),
        "guard": guard_stats(user_id, db=db),
        "reply_rate": reply_rate(user_id, db=db),
        "bounce_rate": bounce_rate(user_id, db=db),
    }
