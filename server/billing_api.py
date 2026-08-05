"""Billing HTTP surface — plan status, Lemon Squeezy Checkout, portal, and webhook.

Mounted on the app by :func:`register` (called from ``server.api``), which injects
the shared rate limiters plus the already-defined auth + per-user-store helpers so
this module never imports back into ``server.api`` (no import cycle).

Design:
  * GET  /api/billing            member-or-demo — the user's plan + usage.
  * POST /api/billing/checkout   member only    — a Lemon Squeezy Checkout URL.
  * POST /api/billing/portal     member only    — the user's LS customer-portal URL.
  * POST /api/billing/webhook    public         — LS -> billing state, signature
                                                   verified, idempotent. No rate limit
                                                   and no session auth (LS authenticates
                                                   by signing the raw body).

Lemon Squeezy is a Merchant of Record, so there is no per-request portal API call
like Stripe's: LS mints a customer-portal URL per subscription and delivers it on
the subscription webhook; we persist it and hand it back here.

Entitlement enforcement does NOT live here: the chat research/write/send gate reads
the same ``billing.limit_for_user`` (via the conversation workspace), so a plan
bought here raises the cap everywhere the product actually spends money.
"""

import logging

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import billing
from billing import store as billing_store
from billing import lemonsqueezy_client as ls_client
from billing import webhook
from config import settings
from server import demo_session

log = logging.getLogger("saqua.billing_api")

# Only self-serve, checkout-able plans (canonical ids). Enterprise is sales-assisted
# (Talk to us), Free needs no purchase.
_CHECKOUT_PLANS = ("pro", "max")
_INTERVALS = ("monthly", "yearly")


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = "monthly"

    @field_validator("plan")
    @classmethod
    def _plan_ok(cls, v: str) -> str:
        # Accept the marketing aliases (starter/growth) too, but store canonical.
        v = billing.normalize_plan(v)
        if v not in _CHECKOUT_PLANS:
            raise ValueError(
                f"Unknown plan. Choose one of: {', '.join(_CHECKOUT_PLANS)}.")
        return v

    @field_validator("interval")
    @classmethod
    def _interval_ok(cls, v: str) -> str:
        v = (v or "monthly").strip().lower()
        return v if v in _INTERVALS else "monthly"


def register(app, *, rl_read=None, rl_write=None, store_for=None,
            member=None, member_or_demo=None):
    def _read(request: Request):
        return rl_read(request) if rl_read else None

    def _write(request: Request):
        return rl_write(request) if rl_write else None

    # ── Plan + usage (shared by members and demo visitors) ─────────────
    @app.get("/api/billing")
    def get_billing(request: Request, _=Depends(_read),
                    user: str = Depends(member_or_demo)):
        usage = store_for(user).load_usage()
        used = len(usage.get("prospects") or [])
        ent = billing.entitlements(user, used)
        # Whether a real Lemon Squeezy checkout can be started. A demo visitor can
        # never transact (no account), so the upgrade UI routes them to /pricing
        # instead of a dead checkout button.
        ent["checkout_enabled"] = bool(ls_client.enabled()
                                       and not demo_session.is_demo_id(user))
        return ent

    # ── Start a Checkout (members only; a demo visitor cannot pay) ─────
    @app.post("/api/billing/checkout")
    def start_checkout(body: CheckoutRequest, request: Request, _=Depends(_write),
                       user: str = Depends(member)):
        # Fail CLOSED on any ambiguous/incomplete billing configuration (an
        # unresolved or invalid mode, or missing credentials). We NEVER silently
        # transact against Live on a misconfiguration; a clear, non-secret config
        # error is returned instead. (Read-only /api/billing still works below.)
        config_error = settings.billing_config_error()
        if config_error:
            raise HTTPException(status_code=503, detail=config_error)
        # Duplicate-subscription guard. Lemon Squeezy does NOT swap plans on a new
        # checkout — it creates a SECOND subscription and bills both. So an
        # already-subscribed user is never allowed to start another checkout; plan
        # changes and cancellations go through the customer portal. This is the
        # deliberate "switching disabled, portal-only" launch posture: our flow is
        # structurally incapable of creating two subscriptions.
        if billing_store.open_subscription(user):
            raise HTTPException(
                status_code=409,
                detail="You already have an active subscription. Use Manage "
                       "Billing to change or cancel your plan.")
        variant_id = settings.lemonsqueezy_variant_id(body.plan, body.interval)
        if not variant_id:
            raise HTTPException(
                status_code=400,
                detail=f"The {body.plan.title()} plan isn't purchasable right now.")
        try:
            session = ls_client.create_checkout(
                user_id=user, plan=body.plan, variant_id=variant_id,
                success_url=settings.BILLING_SUCCESS_URL)
        except ls_client.LemonSqueezyError as exc:
            log.warning("checkout create failed for %s: %s", user[:12], exc)
            raise HTTPException(
                status_code=502,
                detail="Could not start checkout with our payment provider. "
                       "Please try again in a moment.") from exc
        return {"url": session.get("url"), "id": session.get("id")}

    # ── Manage/cancel an existing subscription (members only) ──────────
    # LS gives a customer-portal URL per subscription (delivered on the webhook and
    # persisted); we just return it. No provider API call here.
    @app.post("/api/billing/portal")
    def open_portal(request: Request, _=Depends(_write), user: str = Depends(member)):
        if not ls_client.enabled():
            raise HTTPException(status_code=503, detail="Billing isn't available yet.")
        portal_url = billing_store.portal_url_for_user(user)
        if not portal_url:
            raise HTTPException(
                status_code=400,
                detail="You don't have a billing account yet. Upgrade to a paid plan first.")
        return {"url": portal_url}

    # ── Lemon Squeezy webhook (public; authenticated by signature, not a session) ─
    # Deliberately NOT given _rl_read/_rl_write: those key on the caller IP, and
    # LS's delivery IPs are shared/rotating. The HMAC signature IS the auth.
    @app.post("/api/billing/webhook")
    async def ls_webhook(request: Request):
        payload = await request.body()
        signature = request.headers.get("x-signature", "")
        event_name = request.headers.get("x-event-name", "")
        if not settings.LEMONSQUEEZY_WEBHOOK_SECRET:
            # Nothing can be verified, so refuse rather than trust the body.
            return JSONResponse(status_code=503, content={"error": "billing not configured"})
        try:
            event = ls_client.verify_signature(payload, signature)
        except ValueError as exc:
            log.warning("rejected webhook: %s", exc)
            return JSONResponse(status_code=400, content={"error": "invalid signature"})
        try:
            # The signature (an HMAC of the exact body) is the idempotency key: an
            # identical redelivery has an identical signature.
            result = webhook.handle_event(event, event_name=event_name,
                                          event_id=signature)
        except Exception:  # noqa: BLE001 - never 500 LS into an endless retry storm
            log.exception("billing webhook handler failed for %s", event_name)
            # 200 so LS doesn't hammer us; the event key is unrecorded, so a manual
            # resend can reprocess once the bug is fixed.
            return {"received": True, "handled": False}
        return {"received": True, "status": result.get("status")}
