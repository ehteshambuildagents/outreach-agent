"""Thin Lemon Squeezy client over the REST (JSON:API) API — no SDK dependency.

Lemon Squeezy is a Merchant of Record: it hosts checkout, handles tax, and owns
the customer portal, so our surface is small — create a checkout, and verify the
one signed webhook it sends back. We talk to it with ``requests`` (already a
dependency) against the documented JSON:API endpoints, and verify webhook
signatures ourselves with HMAC-SHA256. That keeps the dependency surface flat and,
crucially, makes the whole flow unit testable offline: signatures are built and
checked with the standard library, and the single network call (checkout) is the
only thing that needs mocking.

Unlike Stripe, LS signs the *raw request body* (no timestamp), so the signature is
a pure content HMAC in the ``X-Signature`` header, hex-encoded. The event name
arrives in the ``X-Event-Name`` header and again in ``meta.event_name``. Anything
we pass in ``checkout_data.custom`` (our internal ``user_id`` + ``plan``) comes
back on every related event as ``meta.custom_data`` — that is how the webhook
attributes a subscription to the right account without trusting the browser.

All ids and amounts come straight from LS; this module does no business logic.
Plan <-> variant mapping lives in ``config.settings`` and the effect of each event
lives in ``billing.webhook``.
"""

import hashlib
import hmac
import json

import requests

from config import settings

_JSON_API = "application/vnd.api+json"


class LemonSqueezyError(RuntimeError):
    """An LS API call failed (network, auth, or a 4xx/5xx from Lemon Squeezy)."""


def enabled() -> bool:
    return settings.lemonsqueezy_enabled()


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.LEMONSQUEEZY_API_KEY}",
            "Accept": _JSON_API, "Content-Type": _JSON_API}


def _post(path: str, body: dict) -> dict:
    if not enabled():
        raise LemonSqueezyError(
            "Lemon Squeezy is not configured (LEMONSQUEEZY_API_KEY/STORE_ID unset).")
    url = f"{settings.LEMONSQUEEZY_API_BASE}/v1/{path.lstrip('/')}"
    try:
        resp = requests.post(url, headers=_headers(), json=body,
                             timeout=settings.REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise LemonSqueezyError(f"could not reach Lemon Squeezy: {exc}") from exc
    if resp.status_code >= 400:
        # Surface LS's own error message (operator-facing, not secret).
        detail = ""
        try:
            errors = resp.json().get("errors") or []
            detail = "; ".join(e.get("detail", "") for e in errors) or resp.text[:200]
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise LemonSqueezyError(f"Lemon Squeezy {resp.status_code}: {detail}")
    return resp.json()


def create_checkout(*, user_id: str, plan: str, variant_id: str,
                    success_url: str, customer_email: str = None) -> dict:
    """Create a hosted Checkout for ``variant_id`` and return {url, id}.

    ``checkout_data.custom`` carries our internal ``user_id`` + ``plan`` so the
    webhook can attribute the resulting subscription to the right account. Custom
    values must be strings (LS echoes them verbatim in ``meta.custom_data``)."""
    attributes = {
        "checkout_data": {
            # LS requires custom values to be strings; it returns them unchanged.
            "custom": {"user_id": str(user_id), "plan": str(plan)},
        },
        "product_options": {
            # Where LS returns the browser after a successful purchase.
            "redirect_url": success_url,
        },
    }
    if customer_email:
        attributes["checkout_data"]["email"] = customer_email
    body = {
        "data": {
            "type": "checkouts",
            "attributes": attributes,
            "relationships": {
                "store": {"data": {"type": "stores",
                                   "id": str(settings.LEMONSQUEEZY_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }
    resp = _post("checkouts", body)
    data = resp.get("data") or {}
    attrs = data.get("attributes") or {}
    return {"url": attrs.get("url"), "id": data.get("id")}


# ── Webhook signature verification (no SDK) ────────────────────────────
def verify_signature(payload: bytes, signature_header: str, *,
                     secret: str = None) -> dict:
    """Verify an LS webhook signature and return the parsed event dict.

    LS signs the raw body with HMAC-SHA256 under the webhook's signing secret and
    sends the hex digest in ``X-Signature`` (no timestamp — the signature is a pure
    content hash, which is what makes an identical redelivery a byte-for-byte
    duplicate). Raises ValueError on a missing/bad signature or unparseable JSON.
    """
    secret = secret if secret is not None else settings.LEMONSQUEEZY_WEBHOOK_SECRET
    if not secret:
        raise ValueError("webhook secret is not configured")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    provided = (signature_header or "").strip()
    if not provided:
        raise ValueError("missing signature")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise ValueError("no matching signature")
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON payload: {exc}") from exc


def sign_payload(payload: bytes, secret: str) -> str:
    """Build a valid ``X-Signature`` header value for ``payload``.

    Used by the test suite (LS does the equivalent server-side) so webhook handling
    can be exercised end to end without a live Lemon Squeezy."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
