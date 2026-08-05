"""Billing & entitlements — the single source of truth for plan -> allowance.

Plans mirror the public /pricing page: Free (self-serve trial), Starter, Growth,
and Enterprise. The only thing a plan grants today is a monthly *researched
prospect* allowance, which is the funnel bottleneck (research gates write + send),
so capping it caps everything downstream. The map lives here so the API, the chat
enforcement, and the tests all read the same numbers.

A user's plan is derived from their active Lemon Squeezy subscription
(``billing.store.active_subscription``); with no active subscription they are on
Free. Resolution is fail-safe: any DB hiccup or unmigrated table yields Free
rather than erroring, so billing can never take down the chat hot path.
"""

import os

from billing import store

# Free is env-driven (matches config.settings.FREE_PROSPECT_LIMIT) so local dev
# can lift the trial cap; the paid tiers are cost-based (see the /pricing page).
_FREE = int(os.getenv("FREE_PROSPECT_LIMIT", "3"))

# Canonical plan ids are `pro` (50) and `max` (100) — the brief's names, and what
# GET /api/billing reports. The public /pricing page still MARKETS these as
# "Starter" and "Growth", so those are accepted as aliases everywhere a plan name
# is read (see PLAN_ALIASES / normalize_plan). The limit env vars accept either
# name (PLAN_LIMIT_PRO or the legacy PLAN_LIMIT_STARTER, etc.).
PLAN_LIMITS = {
    "free": _FREE,
    "pro": int(os.getenv("PLAN_LIMIT_PRO", os.getenv("PLAN_LIMIT_STARTER", "50"))),
    "max": int(os.getenv("PLAN_LIMIT_MAX", os.getenv("PLAN_LIMIT_GROWTH", "100"))),
    "enterprise": int(os.getenv("PLAN_LIMIT_ENTERPRISE", "300")),
}

# Order low -> high, used only for display/upgrade hints.
PLAN_ORDER = ("free", "pro", "max", "enterprise")

# Marketing/legacy name -> canonical id. Keep these forever: old rows, old
# checkouts, and the public pricing copy all use starter/growth.
PLAN_ALIASES = {"starter": "pro", "growth": "max"}

# Human plan names for the UI (the canonical ids are terse). The public /pricing
# page still MARKETS pro/max as Starter/Growth, but the in-app upgrade surfaces use
# the canonical Pro/Max names the brief asks for.
PLAN_DISPLAY_NAMES = {"free": "Free", "pro": "Pro", "max": "Max",
                      "enterprise": "Enterprise"}

# Canonical PRICE catalog — the single source the upgrade UI reads so no price is
# ever hardcoded in a component. Numbers mirror the /pricing page ($65 Pro / $100
# Max monthly); yearly = 10x monthly (two months free). All env-overridable so a
# price change is a config edit, not a code change. Currency is display-only here;
# Lemon Squeezy is the source of truth for what is actually charged.
BILLING_CURRENCY = os.getenv("BILLING_CURRENCY", "USD")
_PLAN_PRICES = {
    "pro": {"monthly": int(os.getenv("PLAN_PRICE_PRO_MONTHLY", "65")),
            "yearly": int(os.getenv("PLAN_PRICE_PRO_YEARLY", "650"))},
    "max": {"monthly": int(os.getenv("PLAN_PRICE_MAX_MONTHLY", "100")),
            "yearly": int(os.getenv("PLAN_PRICE_MAX_YEARLY", "1000"))},
}

# The purchasable, self-serve plans in upgrade order (Free/Enterprise excluded:
# Free needs no purchase, Enterprise is sales-assisted).
CHECKOUT_PLANS = ("pro", "max")


def plan_catalog(interval: str = "monthly") -> list[dict]:
    """The purchasable plans with display name, price, interval and allowance — the
    canonical data an upgrade card/popup renders, so prices live here, not in the
    frontend. ``interval`` selects the monthly/yearly price."""
    interval = "yearly" if (interval or "").lower() == "yearly" else "monthly"
    return [{
        "plan": pid,
        "name": PLAN_DISPLAY_NAMES[pid],
        "price": _PLAN_PRICES[pid][interval],
        "currency": BILLING_CURRENCY,
        "interval": interval,
        "prospect_limit": PLAN_LIMITS[pid],
    } for pid in CHECKOUT_PLANS]


def normalize_plan(plan) -> str:
    p = (plan or "").strip().lower()
    p = PLAN_ALIASES.get(p, p)
    return p if p in PLAN_LIMITS else "free"


def plan_limit(plan) -> int:
    """Monthly researched-prospect allowance for a plan (0 = unlimited)."""
    return PLAN_LIMITS.get(normalize_plan(plan), _FREE)


def plan_for_user(user_id: str) -> str:
    """The user's current plan name from their active subscription, else 'free'."""
    sub = store.active_subscription(user_id)
    return normalize_plan(sub["plan"]) if sub else "free"


def limit_for_user(user_id: str) -> int:
    """The prospect allowance the server must enforce for this user."""
    return plan_limit(plan_for_user(user_id))


def prospects_used(user_id: str) -> int:
    """Distinct prospects the user has consumed in their current billing period,
    read from the durable usage store (Postgres/SQLite). Fail-safe: 0 on any DB
    hiccup, so /api/billing and the gate never error on a billing read."""
    try:
        from billing import usage
        return usage.prospects_used(user_id)
    except Exception:  # noqa: BLE001 - usage read must never break a billing view
        return 0


def entitlements(user_id: str, prospects_used: int = None) -> dict:
    """The full billing view for /api/billing: plan, allowance, usage, status,
    period. ``prospects_used`` is read from the durable store when not supplied
    (callers may still pass a value for tests or to avoid a second read)."""
    sub = store.active_subscription(user_id)
    plan = normalize_plan(sub["plan"]) if sub else "free"
    limit = plan_limit(plan)
    if prospects_used is None:
        used = globals()["prospects_used"](user_id)
    else:
        used = int(prospects_used or 0)
    # Billing-period window (paid = subscription period; free = lifetime trial).
    try:
        from billing import usage
        period = usage.usage_period(user_id)
    except Exception:  # noqa: BLE001
        period = {"period_start": None, "period_end": None}
    remaining = (max(0, limit - used) if limit > 0 else None)
    # The plan to pitch on an upgrade surface: the next paid tier up (free -> pro,
    # pro -> max). None once there's nothing self-serve left to sell (max/enterprise).
    nxt = {"free": "pro", "pro": "max"}.get(plan)
    return {
        "plan": plan,
        "plan_name": PLAN_DISPLAY_NAMES.get(plan, plan.title()),
        "prospect_limit": limit,
        "prospects_used": used,
        # None communicates "unlimited" to the client (limit 0), matching the
        # existing Settings card contract.
        "prospects_remaining": remaining,
        # Explicit key the brief requires (alias of prospects_remaining).
        "remaining": remaining,
        # The billing period this usage is scoped to (epoch seconds).
        "period_start": period.get("period_start"),
        "period_end": period.get("period_end"),
        "status": (sub["status"] if sub else "none"),
        "current_period_end": (sub.get("current_period_end") if sub else None),
        # Whether this user is on a paid tier (upgrade surfaces hide for them).
        "is_paid": plan != "free",
        # Canonical purchasable plans + the recommended upgrade, so the upgrade UI
        # renders name/price/limit without hardcoding anything.
        "catalog": plan_catalog(),
        "recommended_upgrade": nxt,
    }
