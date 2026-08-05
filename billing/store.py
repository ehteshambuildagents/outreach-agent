"""Durable billing state — the only module that talks to the billing_* tables.

Provider-neutral by design: every row carries a ``provider`` and a
``provider_*_id``, so the same tables backed Stripe before and back Lemon Squeezy
now — only the default provider name and the entitling-status set are LS-specific.
Sits on the shared portable DB layer (``automation.db.Database``), so it behaves
identically on Postgres (production) and SQLite (local/tests). The tables come from
``automation/migrations/0002_billing.sql`` + ``0004_billing_events.sql`` +
``0005_billing_portal_url.sql``; this module reads and writes them.

Every write here is IDEMPOTENT on its provider key (the subscription / invoice /
event id), because Lemon Squeezy delivers webhooks at-least-once and will redeliver
on any non-2xx or a manual replay. Reapplying the same event must never
double-apply a plan change or double-count an invoice — see ``billing.webhook``.
"""

import time
import uuid

from automation.db import Database

# The default (and, today, only) payment provider these rows describe.
DEFAULT_PROVIDER = "lemonsqueezy"

# Subscription statuses that unconditionally entitle a user to their plan's
# allowance. Lemon Squeezy's other states (past_due, unpaid, paused, expired) fall
# back to Free. ``cancelled`` is handled specially in ``active_subscription``: it
# still entitles until ``ends_at`` (the paid-through grace window LS grants), then
# drops to Free.
ACTIVE_STATUSES = ("active", "on_trial")

# Statuses that mean the user still has a LIVE Lemon Squeezy subscription that
# could bill again — so starting a second checkout would create a duplicate and
# double-charge them. A *cancelled* subscription is deliberately NOT here: it will
# not renew, so re-subscribing is safe; ``expired`` is gone entirely. Plan changes
# for an open subscription must go through the customer portal, never a new checkout.
OPEN_STATUSES = ("active", "on_trial", "past_due", "unpaid", "paused")


def _db() -> Database:
    return Database()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ── Subscriptions ──────────────────────────────────────────────────────
def active_subscription(user_id: str, *, now: float = None, db: Database = None,
                        strict: bool = False):
    """The user's current entitling subscription (newest), or None.

    Entitling = an active/on_trial subscription, OR a *cancelled* one still inside
    its paid period (``status='cancelled'`` with ``current_period_end`` (= LS
    ``ends_at``) in the future). Lemon Squeezy keeps a cancelled subscription usable
    until it ends, so we honour that grace window here; once ``ends_at`` passes (or
    LS sends ``subscription_expired``), the row no longer qualifies and the user
    falls back to Free.

    Tolerant by design: if the billing tables do not exist yet (a dev DB that has
    not been migrated) or any read fails, callers get None and fall back to the
    Free plan rather than erroring in a hot path. ``now`` is injectable for tests.

    ``strict=True`` disables that tolerance and RE-RAISES on a read failure — used by
    the quota-enforcement path, which must fail closed (block) rather than silently
    resolve a paid user to the Free window during a database outage."""
    if not user_id:
        return None
    db = db or _db()
    now = time.time() if now is None else now
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    try:
        rows = db.query(
            f"SELECT * FROM billing_subscriptions "
            f"WHERE user_id=? AND ("
            f"  status IN ({placeholders}) "
            f"  OR (status='cancelled' AND current_period_end IS NOT NULL "
            f"      AND current_period_end > ?)"
            f") ORDER BY updated_at DESC LIMIT 1",
            (user_id, *ACTIVE_STATUSES, now))
    except Exception:  # noqa: BLE001 - unmigrated/unavailable DB => treat as Free
        if strict:
            raise
        return None
    return dict(rows[0]) if rows else None


def open_subscription(user_id: str, *, db: Database = None):
    """The user's newest LIVE subscription (any :data:`OPEN_STATUSES`), or None.

    Unlike :func:`active_subscription` (which gates entitlement and honours the
    cancelled grace window), this answers a different question: "does a second
    checkout risk creating a duplicate that double-charges?" A cancelled or expired
    subscription returns None here, because it will not bill again, so the user may
    safely start a fresh checkout; anything still live routes plan changes to the
    portal instead. Tolerant of an unmigrated/unavailable DB (returns None)."""
    if not user_id:
        return None
    db = db or _db()
    placeholders = ",".join("?" for _ in OPEN_STATUSES)
    try:
        rows = db.query(
            f"SELECT * FROM billing_subscriptions WHERE user_id=? "
            f"AND status IN ({placeholders}) ORDER BY updated_at DESC LIMIT 1",
            (user_id, *OPEN_STATUSES))
    except Exception:  # noqa: BLE001 - unmigrated/unavailable => treat as none
        return None
    return dict(rows[0]) if rows else None


# A sane default period length when the very first subscription event carries no
# explicit start (a monthly cycle). Only ever used for the first period's *display*
# start if the webhook omitted created_at; the anchor still resets correctly on
# every renewal regardless, because the end timestamp advances.
_DEFAULT_PERIOD_SECONDS = 30 * 24 * 3600


def upsert_subscription(user_id: str, plan: str, status: str, *,
                        provider_subscription_id: str,
                        current_period_end: float = None,
                        current_period_start: float = None,
                        provider: str = DEFAULT_PROVIDER, portal_url: str = None,
                        db: Database = None) -> dict:
    """Insert or update the subscription identified by its provider id.

    Keyed on ``provider_subscription_id`` so a ``subscription_updated`` event
    updates the same row rather than creating a duplicate — which is what makes
    replaying an event a no-op. ``portal_url`` (LS's per-subscription customer
    portal link) is only written when provided, so a later event that omits it
    never clobbers a good link.

    ``current_period_start`` scopes prospect usage to the billing period. It is
    resolved with a carry-forward rule so only Lemon Squeezy timestamps are used:
    an explicit start wins; otherwise, when the end advances past the stored end the
    cycle has rolled, so the previous end becomes the new start (a fresh usage
    window); otherwise the stored start is kept. On the first insert with no explicit
    start it defaults to one month before the end."""
    db = db or _db()
    now = time.time()
    existing = db.query(
        "SELECT id, current_period_end, current_period_start "
        "FROM billing_subscriptions WHERE provider_subscription_id=?",
        (provider_subscription_id,))
    if existing:
        row = existing[0]
        sub_id = row["id"]
        prev_start = row["current_period_start"]
        prev_end = row["current_period_end"]
        if current_period_start is not None:
            period_start = current_period_start
        elif (current_period_end is not None and prev_end is not None
              and current_period_end > prev_end):
            # The renewal advanced the period end -> the old end starts the new cycle.
            period_start = prev_end
        else:
            period_start = prev_start
        if portal_url:
            db.execute(
                "UPDATE billing_subscriptions SET user_id=?, plan=?, status=?, "
                "provider=?, current_period_end=?, current_period_start=?, "
                "portal_url=?, updated_at=? WHERE id=?",
                (user_id, plan, status, provider, current_period_end, period_start,
                 portal_url, now, sub_id))
        else:
            db.execute(
                "UPDATE billing_subscriptions SET user_id=?, plan=?, status=?, "
                "provider=?, current_period_end=?, current_period_start=?, "
                "updated_at=? WHERE id=?",
                (user_id, plan, status, provider, current_period_end, period_start,
                 now, sub_id))
    else:
        sub_id = _new_id("bsub")
        if current_period_start is not None:
            period_start = current_period_start
        elif current_period_end is not None:
            period_start = current_period_end - _DEFAULT_PERIOD_SECONDS
        else:
            period_start = now
        db.execute(
            "INSERT INTO billing_subscriptions "
            "(id, user_id, plan, status, provider, provider_subscription_id, "
            " current_period_end, current_period_start, portal_url, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sub_id, user_id, plan, status, provider, provider_subscription_id,
             current_period_end, period_start, portal_url, now, now))
    return {"id": sub_id, "user_id": user_id, "plan": plan, "status": status,
            "current_period_end": current_period_end,
            "current_period_start": period_start}


def set_subscription_status(provider_subscription_id: str, status: str, *,
                            current_period_end: float = None,
                            db: Database = None) -> bool:
    """Update just the status (+ optional period end) of a known subscription.

    Used by subscription.updated / .deleted, where the plan is unchanged but the
    entitlement flips on/off. Returns True if a row was updated."""
    db = db or _db()
    now = time.time()
    if current_period_end is not None:
        n = db.execute(
            "UPDATE billing_subscriptions SET status=?, current_period_end=?, "
            "updated_at=? WHERE provider_subscription_id=?",
            (status, current_period_end, now, provider_subscription_id))
    else:
        n = db.execute(
            "UPDATE billing_subscriptions SET status=?, updated_at=? "
            "WHERE provider_subscription_id=?",
            (status, now, provider_subscription_id))
    return bool(n)


# ── Customers (user_id <-> Stripe customer id) ─────────────────────────
def upsert_customer(user_id: str, provider_customer_id: str, *,
                    provider: str = DEFAULT_PROVIDER, db: Database = None) -> None:
    """Remember the provider's customer id for a user (UNIQUE per user+provider).

    The mapping lets a webhook that only carries a customer id (e.g. a payment
    event) resolve the internal user."""
    db = db or _db()
    now = time.time()
    existing = db.query(
        "SELECT id FROM billing_customers WHERE user_id=? AND provider=?",
        (user_id, provider))
    if existing:
        db.execute(
            "UPDATE billing_customers SET provider_customer_id=?, updated_at=? "
            "WHERE id=?", (provider_customer_id, now, existing[0]["id"]))
    else:
        db.execute(
            "INSERT INTO billing_customers "
            "(id, user_id, provider, provider_customer_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (_new_id("bcus"), user_id, provider, provider_customer_id, now, now))


def user_id_for_customer(provider_customer_id: str, *,
                         provider: str = DEFAULT_PROVIDER, db: Database = None):
    if not provider_customer_id:
        return None
    db = db or _db()
    rows = db.query(
        "SELECT user_id FROM billing_customers "
        "WHERE provider_customer_id=? AND provider=? LIMIT 1",
        (provider_customer_id, provider))
    return rows[0]["user_id"] if rows else None


def customer_id_for_user(user_id: str, *, provider: str = DEFAULT_PROVIDER,
                         db: Database = None):
    if not user_id:
        return None
    db = db or _db()
    rows = db.query(
        "SELECT provider_customer_id FROM billing_customers "
        "WHERE user_id=? AND provider=? LIMIT 1", (user_id, provider))
    return rows[0]["provider_customer_id"] if rows else None


def portal_url_for_user(user_id: str, *, db: Database = None):
    """The customer-portal URL Lemon Squeezy minted for the user's current
    subscription (newest entitling row), or None. Lets /api/billing/portal hand
    back a manage/cancel link without an API round-trip."""
    sub = active_subscription(user_id, db=db)
    if sub:
        return sub.get("portal_url")
    # Fall back to the newest subscription of any status (a cancelled/past_due user
    # may still want the portal to fix their card or resubscribe).
    if not user_id:
        return None
    db = db or _db()
    try:
        rows = db.query(
            "SELECT portal_url FROM billing_subscriptions WHERE user_id=? "
            "ORDER BY updated_at DESC LIMIT 1", (user_id,))
    except Exception:  # noqa: BLE001 - unmigrated/unavailable => no portal
        return None
    return rows[0]["portal_url"] if rows else None


# ── Invoices ───────────────────────────────────────────────────────────
def record_invoice(user_id: str, *, amount_cents: int, currency: str, status: str,
                   provider_invoice_id: str, subscription_id: str = None,
                   db: Database = None) -> None:
    """Record an invoice, de-duped on the Stripe invoice id so a replayed
    invoice.paid / invoice.payment_failed never double-writes."""
    db = db or _db()
    if provider_invoice_id:
        existing = db.query(
            "SELECT id FROM billing_invoices WHERE provider_invoice_id=?",
            (provider_invoice_id,))
        if existing:
            db.execute("UPDATE billing_invoices SET status=? WHERE id=?",
                       (status, existing[0]["id"]))
            return
    db.execute(
        "INSERT INTO billing_invoices "
        "(id, user_id, subscription_id, amount_cents, currency, status, "
        " provider_invoice_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (_new_id("binv"), user_id, subscription_id, int(amount_cents or 0),
         currency or "usd", status, provider_invoice_id, time.time()))


# ── Webhook idempotency ────────────────────────────────────────────────
def event_processed(event_id: str, *, db: Database = None) -> bool:
    """True if this Stripe event id has already been handled to completion."""
    if not event_id:
        return False
    db = db or _db()
    rows = db.query("SELECT 1 FROM billing_events WHERE event_id=?", (event_id,))
    return bool(rows)


def mark_event_processed(event_id: str, event_type: str, *,
                         db: Database = None) -> None:
    if not event_id:
        return
    db = db or _db()
    try:
        db.execute(
            "INSERT INTO billing_events (event_id, type, received_at) "
            "VALUES (?,?,?)", (event_id, event_type, time.time()))
    except Exception:  # noqa: BLE001 - a racing duplicate insert is fine; it's marked
        pass
