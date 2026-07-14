"""Per-user usage caps + account kill switch — public-signup cost safety.

Public signups remove the informal "I know how much I've used" bound, so every
paid provider call is metered per user and gated against hard caps. This module is
the single decision point; it's wired in at exactly two choke points so no call
site plumbs new parameters:

  * ``research/providers_common.request_json`` — Firecrawl / Tavily / Exa / Jina /
    Hunter / X, keyed by the ``provider`` name it already passes.
  * ``services/claude_client`` — Anthropic model calls (provider ``"anthropic"``).

Both read the ambient user from :mod:`telemetry.context`. The contract:

    allow(provider, user_id) -> Decision(allowed, reason)   # check BEFORE the call
    record(provider, user_id, cost=None, count=1)           # meter AFTER a real call

Guarantees:
  * NEVER raises into the product — any internal error fails OPEN (allowed=True), so
    a metering/DB hiccup can't lock users out. Only a deliberate cap/pause denies.
  * A call with no user context (``user_id`` falsy) is a system/internal call and is
    never capped or metered.
  * Deterministic per-provider daily CALL caps + combined daily/monthly USD ceilings.
  * Kill switch: an abnormal hourly spike pauses the account and flags it for review.
"""

import logging
import time

from config.settings import (
    LIMITS_ENFORCED,
    LIMIT_DAILY_CALLS,
    LIMIT_DAILY_USD_PER_USER,
    LIMIT_DEFAULT_COST_USD,
    LIMIT_DEFAULT_DAILY_CALLS,
    LIMIT_MONTHLY_USD_PER_USER,
    LIMIT_PROVIDER_COST_USD,
    LIMIT_SPIKE_ABS_CEILING,
    LIMIT_SPIKE_MIN_CALLS,
    LIMIT_SPIKE_MULTIPLE,
)
from limits import store

log = logging.getLogger("limits")

_DAY = 86400
_MONTH = 30 * 86400
_HOUR = 3600


class Decision:
    """Result of a cap check. Truthy when the call is allowed."""
    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self):
        return self.allowed

    def __repr__(self):
        return f"Decision(allowed={self.allowed}, reason={self.reason!r})"


def provider_cost(provider: str) -> float:
    return LIMIT_PROVIDER_COST_USD.get(provider, LIMIT_DEFAULT_COST_USD)


def provider_daily_cap(provider: str) -> int:
    return LIMIT_DAILY_CALLS.get(provider, LIMIT_DEFAULT_DAILY_CALLS)


# ── the gate ───────────────────────────────────────────────────────────
def allow(provider: str, user_id: str, *, db=None) -> Decision:
    """Should this user be allowed one more ``provider`` call right now?

    Fails OPEN on any internal error. Returns a Decision whose ``reason`` is a
    short, user-safe explanation when denied.
    """
    if not user_id:
        return Decision(True)                      # system/internal call — never capped
    if not LIMITS_ENFORCED:
        return Decision(True)
    try:
        # Kill switch: a paused account is blocked from all paid actions.
        if store.get_state(user_id, db=db).get("state") == "paused":
            return Decision(False, "This account is paused for review. "
                            "Please contact support.")
        now = time.time()

        cap = provider_daily_cap(provider)
        if cap > 0:
            used = store.provider_calls_since(user_id, provider, now - _DAY, db=db)
            if used >= cap:
                return Decision(False, f"Daily {provider} limit reached "
                                f"({used}/{cap}). It resets within 24 hours.")

        if LIMIT_DAILY_USD_PER_USER > 0:
            spend = store.spend_since(user_id, now - _DAY, db=db)
            if spend >= LIMIT_DAILY_USD_PER_USER:
                return Decision(False, "Daily usage limit reached. "
                                "It resets within 24 hours.")

        if LIMIT_MONTHLY_USD_PER_USER > 0:
            mspend = store.spend_since(user_id, now - _MONTH, db=db)
            if mspend >= LIMIT_MONTHLY_USD_PER_USER:
                return Decision(False, "Monthly usage limit reached.")

        return Decision(True)
    except Exception:  # noqa: BLE001 - availability first: never lock users out on error
        log.debug("cap check failed open for %s/%s", provider, user_id, exc_info=True)
        return Decision(True)


def record(provider: str, user_id: str, cost: float = None, count: int = 1,
           *, db=None) -> None:
    """Meter one real (non-cached) paid call, then run the spike kill switch."""
    if not user_id:
        return
    try:
        c = provider_cost(provider) if cost is None else float(cost)
        store.add_usage(user_id, provider, c, count, db=db)
        _maybe_flag_spike(user_id, db=db)
    except Exception:  # noqa: BLE001
        pass


# ── kill switch (spike detection) ──────────────────────────────────────
def _maybe_flag_spike(user_id: str, *, db=None) -> bool:
    """Pause the account if its last-hour volume is an abnormal spike.

    Gated cheaply: the expensive baseline query only runs once a user has already
    crossed the absolute floor within the hour, so normal users never pay for it.
    Returns True if it paused the account this call.
    """
    try:
        now = time.time()
        hour_calls = store.calls_since(user_id, now - _HOUR, db=db)
        if hour_calls < LIMIT_SPIKE_MIN_CALLS:
            return False
        if store.get_state(user_id, db=db).get("state") == "paused":
            return False                           # already paused
        baseline = store.median_hourly_calls(
            now - 24 * _HOUR, now, exclude_user=user_id, db=db)
        # Relative anomaly when peers exist; a high absolute ceiling at cold start
        # so the first legitimate heavy user isn't paused, only a runaway bot.
        if baseline > 0:
            threshold = max(float(LIMIT_SPIKE_MIN_CALLS),
                            LIMIT_SPIKE_MULTIPLE * baseline)
        else:
            threshold = float(LIMIT_SPIKE_ABS_CEILING)
        if hour_calls >= threshold:
            reason = (f"Auto-paused: {hour_calls} calls in the last hour "
                      f"(threshold {threshold:.0f}, baseline {baseline:.0f}).")
            pause(user_id, reason, db=db)
            log.warning("account kill switch tripped user=%s %s", user_id, reason)
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def pause(user_id: str, reason: str = "flagged for review", *, db=None) -> None:
    store.set_state(user_id, "paused", reason, db=db)
    try:
        from telemetry import record_event
        record_event("account", "paused", entity_id=user_id, detail=reason[:200],
                     user_id=user_id)
    except Exception:  # noqa: BLE001
        pass


def resume(user_id: str, *, db=None) -> None:
    store.set_state(user_id, "active", "resumed by admin", db=db)
    try:
        from telemetry import record_event
        record_event("account", "resumed", entity_id=user_id, user_id=user_id)
    except Exception:  # noqa: BLE001
        pass


def is_paused(user_id: str, *, db=None) -> bool:
    if not user_id:
        return False
    try:
        return store.get_state(user_id, db=db).get("state") == "paused"
    except Exception:  # noqa: BLE001
        return False


# ── admin / internal view ──────────────────────────────────────────────
def usage_snapshot(user_id: str, *, db=None) -> dict:
    """Per-user usage-vs-cap for the internal admin view."""
    now = time.time()
    daily_spend = round(store.spend_since(user_id, now - _DAY, db=db), 5)
    monthly_spend = round(store.spend_since(user_id, now - _MONTH, db=db), 5)
    by_provider = store.provider_breakdown_since(user_id, now - _DAY, db=db)
    providers = {}
    for prov, cap in LIMIT_DAILY_CALLS.items():
        used = by_provider.get(prov, {}).get("calls", 0)
        providers[prov] = {"calls_today": used, "daily_cap": cap,
                           "cost_today": by_provider.get(prov, {}).get("cost", 0.0)}
    return {
        "user_id": user_id,
        "state": store.get_state(user_id, db=db).get("state"),
        "daily_spend": daily_spend,
        "daily_cap": LIMIT_DAILY_USD_PER_USER,
        "monthly_spend": monthly_spend,
        "monthly_cap": LIMIT_MONTHLY_USD_PER_USER,
        "hour_calls": store.calls_since(user_id, now - _HOUR, db=db),
        "providers": providers,
    }


def all_usage(*, db=None) -> list:
    """Usage snapshots for every user active in the last 24h (admin overview)."""
    now = time.time()
    ids = set(store.active_user_ids(now - _DAY, db=db))
    return [usage_snapshot(uid, db=db) for uid in sorted(ids)]
