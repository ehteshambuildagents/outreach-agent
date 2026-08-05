"""Lemon Squeezy webhook -> durable billing state.

Kept separate from the HTTP layer so it can be unit tested with a plain event
dict (no signature, no request). ``handle_event`` is the whole contract:

  * it is IDEMPOTENT — a redelivered event is detected by id (the LS signature,
    which for an identical body is byte-for-byte identical) and skipped, and every
    underlying write is itself keyed on an LS id (subscription id / invoice id), so
    even a redelivery that slips past the id check cannot double-apply;
  * it is FAIL-QUIET on unhandled event types — LS sends more than we act on, and
    anything else is acknowledged (200) so LS marks it delivered.

Lemon Squeezy differs from a card-processor webhook in two ways we rely on:
  * the internal ``user_id`` (+ ``plan``) we set at checkout come back on every
    related event under ``meta.custom_data`` — never trust the browser;
  * the customer PORTAL is a URL LS mints per subscription (``urls.customer_portal``),
    not an API-created session, so we persist it and hand it back from /portal.

The events we act on (LS names):
  subscription_created / _updated / _resumed / _unpaused -> activate + record
  subscription_cancelled / _expired / _paused            -> drop the entitlement
  subscription_payment_success / _recovered              -> record invoice (paid)
  subscription_payment_failed                            -> record invoice + past_due
  order_created                                          -> remember the customer map
"""

import logging
from datetime import datetime, timezone

from billing import normalize_plan, store
from config import settings

log = logging.getLogger("saqua.billing.webhook")

PROVIDER = "lemonsqueezy"

# Subscription events that (re)assert an active/known subscription row.
_SUB_UPSERT = {
    "subscription_created", "subscription_updated",
    "subscription_resumed", "subscription_unpaused",
}
# Subscription events that only flip status (entitlement on/off), plan unchanged.
_SUB_STATUS = {
    "subscription_cancelled": "cancelled",
    "subscription_expired": "expired",
    "subscription_paused": "paused",
}
_PAY_EVENTS = {
    "subscription_payment_success",
    "subscription_payment_recovered",
    "subscription_payment_failed",
}
_HANDLED = _SUB_UPSERT | set(_SUB_STATUS) | _PAY_EVENTS | {"order_created"}


def _iso_to_epoch(value):
    """LS timestamps are ISO-8601 (``2026-08-01T00:00:00.000000Z``). Return epoch
    seconds as a float, or None if absent/unparseable."""
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _plan_from_attrs(custom: dict, attrs: dict) -> str:
    """Prefer the plan we stamped at checkout (custom_data.plan); otherwise derive
    it from the subscription's variant id via the configured variant map."""
    if custom.get("plan"):
        return normalize_plan(custom["plan"])
    return normalize_plan(settings.variant_to_plan(attrs.get("variant_id")))


def _grant_access(user_id: str, *, db=None) -> None:
    """Approve a paying customer in the soft-launch access store, so an active
    subscription clears ``require_approved_user`` immediately. Imported lazily and
    fully swallowed on error — access is a soft gate and must never fail a paid
    billing event."""
    try:
        import access
        access.approve(user_id, note="active subscription", db=db)
    except Exception:  # noqa: BLE001 - access is best-effort; never break billing
        log.debug("could not auto-approve paid user %s", (user_id or "?")[:12],
                  exc_info=True)


def _resolve_user(custom: dict, attrs: dict, *, db=None) -> str:
    """Internal user id for an event: prefer custom_data, else the customer map
    recorded from the first event that carried it."""
    if custom.get("user_id"):
        return custom["user_id"]
    customer = attrs.get("customer_id")
    return store.user_id_for_customer(
        str(customer) if customer is not None else None,
        provider=PROVIDER, db=db) or ""


def handle_event(event: dict, *, event_name: str = None, event_id: str = None,
                 db=None) -> dict:
    """Apply a Lemon Squeezy event to billing state. Returns a small result dict.

    ``event_name``/``event_id`` are what the HTTP layer read from the request (the
    ``X-Event-Name`` header and the ``X-Signature`` used as an idempotency key);
    both fall back to the body so a test can pass just the event. ``db`` is
    injectable so a request handler (or a test) can thread one connection through
    every store call."""
    meta = event.get("meta") or {}
    etype = event_name or meta.get("event_name") or ""
    data = event.get("data") or {}
    attrs = data.get("attributes") or {}
    custom = meta.get("custom_data") or {}
    resource_id = data.get("id")
    # Idempotency key: the signature (identical for an identical redelivery). Fall
    # back to a synthetic key so a test without one still records something.
    eid = event_id or (f"{etype}:{resource_id}" if resource_id else "")

    if eid and store.event_processed(eid, db=db):
        return {"status": "duplicate", "type": etype, "event_id": eid}

    if etype not in _HANDLED:
        store.mark_event_processed(eid, etype, db=db)
        return {"status": "ignored", "type": etype}

    result = {"status": "processed", "type": etype}
    # When a subscription event can't be attributed to a user, we do NOT record it
    # in the idempotency ledger, so a manual Resend (after the customer map exists)
    # can reprocess it rather than being deduped away and the paid plan lost.
    applied = True

    if etype == "order_created":
        # An order carries the customer + our custom data but no subscription id.
        # Record the customer<->user map so a later customer-only event resolves;
        # the subscription_created event (arriving alongside) does the entitlement.
        user_id = custom.get("user_id") or ""
        customer = attrs.get("customer_id")
        if user_id and customer is not None:
            store.upsert_customer(user_id, str(customer), provider=PROVIDER, db=db)
        result.update(user_id=user_id, order=resource_id)

    elif etype in _SUB_UPSERT:
        user_id = _resolve_user(custom, attrs, db=db)
        sub_id = str(resource_id) if resource_id is not None else None
        status = attrs.get("status") or "active"
        plan = _plan_from_attrs(custom, attrs)
        period_end = _iso_to_epoch(attrs.get("renews_at") or attrs.get("ends_at"))
        customer = attrs.get("customer_id")
        portal_url = ((attrs.get("urls") or {}).get("customer_portal")) or None
        if user_id and customer is not None:
            store.upsert_customer(user_id, str(customer), provider=PROVIDER, db=db)
        if status in store.ACTIVE_STATUSES and plan == "free":
            # An entitling subscription we can't name means the buyer paid but would
            # land on Free: it happens only if BOTH custom_data.plan is missing AND
            # the subscription's variant_id isn't in LEMONSQUEEZY_VARIANT_IDS. Our
            # own checkout always sets custom_data.plan, so this flags either a
            # misconfigured variant map or an out-of-band subscription — make it
            # loud rather than silently under-entitle.
            log.warning("billing webhook %s: active subscription %s for user %s has "
                        "no resolvable plan (variant_id=%s); check LEMONSQUEEZY_VARIANT_*",
                        etype, sub_id, (user_id or "?")[:12], attrs.get("variant_id"))
        if user_id and sub_id:
            store.upsert_subscription(
                user_id, plan, status, provider_subscription_id=sub_id,
                current_period_end=period_end, provider=PROVIDER,
                portal_url=portal_url, db=db)
            # A paying customer must not stay stuck behind the soft-launch access
            # gate: the moment their subscription is active, grant product access.
            # Best-effort — never let an access-store hiccup fail the billing event.
            if status in store.ACTIVE_STATUSES:
                _grant_access(user_id, db=db)
        elif sub_id and not user_id:
            # Could not attribute the subscription to an internal user (no
            # custom_data.user_id and no customer map yet). Loud, because it means
            # a paid subscription is not being applied — and left OUT of the ledger
            # so a Resend can recover it.
            applied = False
            result["status"] = "deferred"
            log.warning("billing webhook %s: unattributed subscription %s "
                        "(customer=%s) — no user_id resolved; not marking processed",
                        etype, sub_id, attrs.get("customer_id"))
        # Use a distinct key so we never clobber the top-level processing status.
        result.update(user_id=user_id, plan=plan, sub_status=status)

    elif etype in _SUB_STATUS:
        sub_id = str(resource_id) if resource_id is not None else None
        # Trust LS's own status string when present; else the event's implied one.
        status = attrs.get("status") or _SUB_STATUS[etype]
        if status in store.ACTIVE_STATUSES:  # defensive: a terminal event => not entitling
            status = _SUB_STATUS[etype]
        period_end = _iso_to_epoch(attrs.get("ends_at"))
        if sub_id:
            store.set_subscription_status(sub_id, status, current_period_end=period_end, db=db)
        result.update(subscription=sub_id, sub_status=status)

    elif etype in _PAY_EVENTS:
        user_id = _resolve_user(custom, attrs, db=db)
        failed = etype == "subscription_payment_failed"
        inv_status = "open" if failed else "paid"
        sub_id = attrs.get("subscription_id")
        sub_id = str(sub_id) if sub_id is not None else None
        if user_id:
            store.record_invoice(
                user_id,
                amount_cents=(attrs.get("total") or attrs.get("total_usd") or 0),
                currency=attrs.get("currency") or "usd", status=inv_status,
                provider_invoice_id=str(resource_id) if resource_id is not None else None,
                subscription_id=sub_id, db=db)
        if sub_id:
            if failed:
                # Also drop the entitlement here in case the subscription_updated
                # (past_due) event is delayed or lost.
                store.set_subscription_status(sub_id, "past_due", db=db)
            elif etype == "subscription_payment_recovered":
                store.set_subscription_status(sub_id, "active", db=db)
        result.update(user_id=user_id, invoice=resource_id, invoice_status=inv_status)

    if applied:
        store.mark_event_processed(eid, etype, db=db)
    log.info("billing webhook %s -> %s", etype, result.get("status"))
    return result
