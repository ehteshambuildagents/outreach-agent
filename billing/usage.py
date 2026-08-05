"""Durable, billing-period-scoped prospect usage — the quota source of truth.

This module replaces the old ``conversations/<user_id>/_usage.json`` file (local
disk, wiped on every Railway redeploy, and counted for a user's *lifetime* rather
than the paid billing period). Usage now lives in the ``prospect_usage`` table
(``automation/migrations/0006_prospect_usage.sql``) on the shared portable DB
layer, so it behaves identically on Postgres (production) and SQLite (local/tests)
and survives restarts and redeploys.

What a "prospect" costs: a prospect is consumed the first time its company is
researched (research gates write + send, so capping research caps the funnel). The
count is keyed on a normalized domain/company key, so re-researching the SAME
company inside a period never consumes a second slot.

Periods: paid usage is scoped to the active subscription's billing period. The
"period anchor" is the subscription's ``current_period_start`` (epoch seconds),
which the Lemon Squeezy webhooks advance on every renewal — so a new period
automatically begins at zero used, and past rows stay as an audit trail. A user
with no active subscription is on the Free lifetime trial, anchored at 0.

Atomicity: :func:`record_prospect_use` performs the check-and-record as one atomic
operation, so two simultaneous requests can never push usage past the cap. On
SQLite the surrounding ``BEGIN IMMEDIATE`` transaction serializes writers; on
Postgres a per-user transaction-scoped advisory lock does the same without
blocking other users.

Fail-safe, like the rest of billing: any DB hiccup or unmigrated table degrades to
"no durable usage" rather than erroring in the chat hot path (see the try/excepts).
"""

import logging
import re
import time
import uuid
from urllib.parse import urlparse

from automation.db import Database
from billing import store

log = logging.getLogger("saqua.billing.usage")


def _db(db: Database = None) -> Database:
    return db or Database()


def _log_unavailable(op: str, user_id: str, key: str, exc: Exception) -> None:
    """Record a durable-store failure with just enough request context to debug —
    the operation, a truncated user id, a truncated prospect key, and the exception
    TYPE. Never logs secrets, the DSN, or full row data."""
    log.warning("prospect_usage %s unavailable (user=%s key=%s): %s",
                op, (user_id or "?")[:12], (key or "")[:80], type(exc).__name__)


# ── Normalized prospect key (canonical dedup identity) ─────────────────────
def prospect_key(company: str = None, url: str = None) -> str:
    """The normalized identity a prospect is deduped on: its bare domain when a URL
    is known, else the company name reduced to lowercase alphanumerics. Matches the
    key chat/tools.py historically used so pre-existing counts line up."""
    if url:
        host = urlparse(url).hostname or url or ""
        host = host[4:] if host.startswith("www.") else host
        if host:
            return host.lower().strip()
    return re.sub(r"[^a-z0-9]", "", (company or "").lower())


# ── Billing period resolution ──────────────────────────────────────────────
def usage_period(user_id: str, *, db: Database = None, strict: bool = False) -> dict:
    """The user's current usage period as ``{period_start, period_end, anchor,
    paid}``.

    Paid: scoped to the active subscription's billing period (start/end from the
    persisted Lemon Squeezy timestamps). The ``anchor`` — the value usage rows are
    keyed on — is ``current_period_start`` when known, else ``current_period_end``
    (still per-cycle-stable, since a renewal moves it), else 0.

    Free (no active subscription, incl. expired/cancelled-and-past-grace): a single
    lifetime window anchored at 0, preserving the existing Free-trial behavior.

    Tolerant of an unmigrated/unavailable billing DB (falls back to the Free window)
    by default; ``strict=True`` re-raises so the enforcement path can fail closed."""
    try:
        sub = store.active_subscription(user_id, db=db, strict=strict)
    except Exception:  # noqa: BLE001 - never let billing resolution break the caller
        if strict:
            raise
        sub = None
    if not sub:
        return {"period_start": None, "period_end": None, "anchor": 0.0, "paid": False}
    pe = sub.get("current_period_end")
    ps = sub.get("current_period_start")
    anchor = ps if ps is not None else (pe if pe is not None else 0.0)
    return {"period_start": (ps if ps is not None else anchor),
            "period_end": pe, "anchor": float(anchor), "paid": True}


# ── Reads ───────────────────────────────────────────────────────────────────
def prospects_used(user_id: str, *, db: Database = None, anchor: float = None,
                   strict: bool = False) -> int:
    """Distinct prospects the user has consumed in the current billing period.

    Tolerant by default (returns 0 on an unmigrated/unavailable DB, for display).
    ``strict=True`` re-raises on failure so the enforcement pre-check can fail closed
    instead of reading a real outage as "0 used" (which would open the quota)."""
    if not user_id:
        return 0
    db = _db(db)
    try:
        if anchor is None:
            anchor = usage_period(user_id, db=db, strict=strict)["anchor"]
        rows = db.query(
            "SELECT COUNT(*) AS n FROM prospect_usage "
            "WHERE user_id=? AND period_anchor=?", (user_id, float(anchor)))
    except Exception as exc:  # noqa: BLE001 - unmigrated/unavailable
        _log_unavailable("read", user_id, "", exc)
        if strict:
            raise
        return 0
    return int(rows[0]["n"]) if rows else 0


def _key_recorded(db: Database, user_id: str, anchor: float, key: str) -> bool:
    rows = db.query(
        "SELECT 1 FROM prospect_usage "
        "WHERE user_id=? AND period_anchor=? AND prospect_key=? LIMIT 1",
        (user_id, float(anchor), key))
    return bool(rows)


def would_block(user_id: str, key: str, *, limit: int, db: Database = None) -> bool:
    """True if researching a NEW prospect ``key`` right now would exceed ``limit``.

    A best-effort pre-check used to avoid paying for research the user cannot keep;
    the authoritative gate is the atomic :func:`record_prospect_use`. An already-
    recorded key (a duplicate) never blocks. ``limit<=0`` means unlimited."""
    if limit is None or limit <= 0 or not key or not user_id:
        return False
    db = _db(db)
    try:
        anchor = usage_period(user_id, db=db)["anchor"]
        if _key_recorded(db, user_id, anchor, key):
            return False
        return prospects_used(user_id, db=db, anchor=anchor) >= limit
    except Exception:  # noqa: BLE001 - never block on an infra hiccup
        return False


# ── Atomic check-and-record ─────────────────────────────────────────────────
def _unavailable(op: str, user_id: str, key: str, exc: Exception, limit: int) -> dict:
    """The result when the durable store cannot be reached during a check-and-record.

    Fails CLOSED in production (``allowed=False, error=True`` — the caller must NOT
    research and must not consume quota, and a duplicate cannot bypass it either,
    because we never confirmed the store). Off production (``is_production()`` false)
    ONLY, an explicit dev fallback allows the action so local work without a database
    is not blocked; it is flagged ``fallback=True`` so callers can tell it apart."""
    _log_unavailable(op, user_id, key, exc)
    from config import settings
    if settings.is_production():
        return {"allowed": False, "duplicate": False, "used": None, "limit": limit,
                "anchor": None, "error": True}
    return {"allowed": True, "duplicate": False, "used": None, "limit": limit,
            "anchor": None, "error": True, "fallback": True}


def record_prospect_use(user_id: str, key: str, *, limit: int,
                        db: Database = None) -> dict:
    """Atomically record that ``user_id`` researched prospect ``key``, enforcing the
    quota. Returns ``{allowed, duplicate, used, limit, anchor, error}``.

    The whole check-and-record runs in one serialized transaction so simultaneous
    requests can never exceed ``limit``:
      * a key already recorded this period is a DUPLICATE — allowed, no new slot;
      * otherwise, if ``used >= limit`` (and limit>0) the request is BLOCKED and
        nothing is written;
      * otherwise the row is inserted and ``used`` reflects the new total.
    ``limit<=0`` means unlimited (always allowed).

    Fails CLOSED on infra errors: if the durable store cannot be reached, the result
    is ``allowed=False, error=True`` in production (see :func:`_unavailable`), so the
    caller does not research and no unmetered quota is granted — a duplicate request
    cannot bypass the store either, because the store was never consulted. Only off
    production is there an explicit dev fallback."""
    if not user_id or not key:
        return {"allowed": True, "duplicate": False, "used": 0, "limit": limit,
                "anchor": 0.0, "error": False}
    db = _db(db)
    unlimited = (limit is None or limit <= 0)
    try:
        # strict: a failure resolving the period must fail closed, not silently
        # resolve a paid user to the Free window.
        anchor = usage_period(user_id, db=db, strict=True)["anchor"]
        with db.tx() as t:
            # Serialize concurrent check-and-record for THIS user. SQLite's
            # BEGIN IMMEDIATE (inside db.tx) already serializes all writers; Postgres
            # needs an explicit per-user lock so two requests can't both read
            # used=limit-1 and both insert.
            if db.backend == "postgres":
                t.execute("SELECT pg_advisory_xact_lock(hashtext(?))",
                          (f"prospect_usage:{user_id}",))
            dup = t.execute(
                "SELECT 1 FROM prospect_usage "
                "WHERE user_id=? AND period_anchor=? AND prospect_key=? LIMIT 1",
                (user_id, anchor, key)).fetchone()
            used = int(t.execute(
                "SELECT COUNT(*) AS n FROM prospect_usage "
                "WHERE user_id=? AND period_anchor=?",
                (user_id, anchor)).fetchone()["n"])
            if dup:
                return {"allowed": True, "duplicate": True, "used": used,
                        "limit": limit, "anchor": anchor, "error": False}
            if not unlimited and used >= limit:
                return {"allowed": False, "duplicate": False, "used": used,
                        "limit": limit, "anchor": anchor, "error": False}
            t.execute(
                "INSERT INTO prospect_usage "
                "(id, user_id, period_anchor, prospect_key, created_at) "
                "VALUES (?,?,?,?,?)",
                (f"pu_{uuid.uuid4().hex}", user_id, anchor, key, time.time()))
            return {"allowed": True, "duplicate": False, "used": used + 1,
                    "limit": limit, "anchor": anchor, "error": False}
    except Exception as exc:  # noqa: BLE001 - durable store unreachable => fail closed
        return _unavailable("record", user_id, key, exc, limit)


def release_prospect_use(user_id: str, key: str, *, db: Database = None,
                         anchor: float = None) -> None:
    """Undo a slot claimed by :func:`record_prospect_use` when the research it was
    guarding did not actually produce a usable result — so a failed/too-thin research
    never permanently consumes quota. Best-effort and never raises; a lingering row
    would only slightly UNDER-serve the user, never over-grant."""
    if not user_id or not key:
        return
    db = _db(db)
    try:
        if anchor is None:
            anchor = usage_period(user_id, db=db)["anchor"]
        db.execute(
            "DELETE FROM prospect_usage "
            "WHERE user_id=? AND period_anchor=? AND prospect_key=?",
            (user_id, float(anchor), key))
    except Exception as exc:  # noqa: BLE001 - release is best-effort
        _log_unavailable("release", user_id, key, exc)


# ── Legacy migration (one-time import of surviving _usage.json) ────────────
# Legacy usage is ALWAYS imported at this anchor — the Free lifetime window — never
# into a paid billing period. The old _usage.json counted a user's LIFETIME research
# (paid billing launched with this durable store), so it is free/historical usage by
# definition; importing it here keeps a newly active Pro/Max period starting at zero
# paid-period usage while preserving the historical count and audit trail.
LEGACY_ANCHOR = 0.0


def import_legacy_keys(user_id: str, keys, *, db: Database = None) -> int:
    """Best-effort one-time import of pre-existing ``_usage.json`` prospect keys, so
    migrating off the JSON file does not silently zero a user's historical usage.

    Imported into :data:`LEGACY_ANCHOR` (0 = the Free lifetime window) REGARDLESS of
    the user's current plan, so a legacy count can never consume a user's active paid
    billing period — a fresh Pro/Max period always begins at zero. Idempotent per key
    (the UNIQUE constraint dedupes a re-run), never raises, never deletes anything.
    Returns the number newly inserted."""
    if not user_id or not keys:
        return 0
    db = _db(db)
    inserted = 0
    now = time.time()
    for raw in keys:
        key = str(raw or "").strip()
        if not key:
            continue
        try:
            n = db.execute(
                "INSERT INTO prospect_usage "
                "(id, user_id, period_anchor, prospect_key, created_at) "
                "VALUES (?,?,?,?,?)",
                (f"pu_{uuid.uuid4().hex}", user_id, LEGACY_ANCHOR, key, now))
            inserted += int(n or 0)
        except Exception:  # noqa: BLE001 - a duplicate/locked row is fine; skip it
            continue
    return inserted
