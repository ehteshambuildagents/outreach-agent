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


def entitlements(user_id: str, prospects_used: int) -> dict:
    """The full billing view for /api/billing: plan, allowance, usage, status."""
    sub = store.active_subscription(user_id)
    plan = normalize_plan(sub["plan"]) if sub else "free"
    limit = plan_limit(plan)
    used = int(prospects_used or 0)
    return {
        "plan": plan,
        "prospect_limit": limit,
        "prospects_used": used,
        # None communicates "unlimited" to the client (limit 0), matching the
        # existing Settings card contract.
        "prospects_remaining": (max(0, limit - used) if limit > 0 else None),
        "status": (sub["status"] if sub else "none"),
        "current_period_end": (sub.get("current_period_end") if sub else None),
    }
