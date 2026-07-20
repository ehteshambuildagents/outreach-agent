"""Public waitlist endpoints — the only UNAUTHENTICATED write surface in the app.

Everything else under ``/api`` sits behind a verified Clerk session. This cannot:
a visitor joining a pre-launch waitlist has no account by definition. That makes
this the app's main abuse target, so it does not reuse the shared ``_rate_limit``
helper in ``server.api``, which is unsuitable here for two reasons:

  1. it counts in a per-process ``dict``, so limits multiply by instance count and
     reset on every deploy; and
  2. it keys on ``request.client.host``, which behind a proxy is the PROXY's
     address — every visitor would share one bucket.

So this module uses the shared Upstash counter (``automation.redis``) keyed on a
proxy-aware client IP, and it fails CLOSED: if the limiter cannot be consulted, the
request is rejected rather than waved through. In production it also refuses to
serve at all unless shared Redis is actually configured, because a public write
endpoint whose rate limiting silently degrades to per-process state is not
something that should be reachable from the internet.

Abuse controls, in order:
  * hard refusal when shared coordination is missing (production);
  * per-IP and per-address fixed-window limits, fail-closed;
  * a honeypot field that bots fill and humans never see;
  * uniform responses, so the form cannot be used to test whether an address is
    already on the list.
"""

import hmac
import html as html_mod
import logging
import os

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

import waitlist
from automation import redis
from config import settings

log = logging.getLogger("saqua.waitlist.api")

# Per-IP: a person signs up once. Five an hour is generous for shared offices and
# NAT while still being far below anything useful for filling a table.
IP_LIMIT, IP_WINDOW = 5, 3600
# Per-address: caps how many confirmation emails one address can be made to
# receive, so the form cannot be turned into a mail-bomb aimed at a third party.
EMAIL_LIMIT, EMAIL_WINDOW = 3, 86400


def _env_true(name: str, default: str = "") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes", "on")


def require_shared_redis() -> bool:
    """Whether to refuse service without shared Redis. On by default in production.
    ``WAITLIST_REQUIRE_SHARED_REDIS=0`` opts out (and you own the consequences)."""
    raw = (os.environ.get("WAITLIST_REQUIRE_SHARED_REDIS") or "").strip()
    if raw:
        return raw.lower() in ("1", "true", "yes", "on")
    return settings.is_production()


def proxy_secret() -> str:
    """Shared secret proving a request genuinely came through our own frontend
    proxy. Empty (the default) means no request is ever treated as proxied."""
    return (os.environ.get("SAQUA_PROXY_SECRET") or "").strip()


def _peer(request: Request) -> str:
    """The address that actually opened the connection to our edge.

    Railway's edge OVERWRITES ``X-Forwarded-For`` with the peer it saw and appends
    one internal hop, rather than appending to whatever arrived. Measured against
    production on 2026-07-19: a request carrying a forged
    ``X-Forwarded-For: 203.0.113.99`` produced a bucket keyed on Railway's own edge
    address and no bucket for the forged value. So the LEFTMOST entry (equivalently
    ``X-Real-IP``) is set by our infrastructure and cannot be influenced by the
    caller, and the rightmost is always Railway's internal hop.
    """
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    xff = request.headers.get("x-forwarded-for") or ""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[0]
    return request.client.host if request.client else "?"


def client_ip(request: Request) -> str:
    """The caller's address, for rate-limit bucketing.

    Two paths reach this app and they need different treatment, because the peer
    our edge sees is only the real client on one of them:

      direct  api.saqua.io   peer == the caller                 -> use the peer
      proxied www.saqua.io   peer == our frontend's egress      -> ask the proxy

    On the proxied path the browser's address never survives the hop: Vercel knows
    it, but Railway's edge rewrites the forwarding headers on ingress, so it cannot
    be recovered from ``X-Forwarded-For`` at any offset. The proxy therefore passes
    it explicitly in ``X-Saqua-Client-IP``.

    That header is only honoured when ``X-Saqua-Proxy-Secret`` matches, because
    api.saqua.io is publicly reachable: without the check, anyone could send a
    client IP of their choosing straight to the backend and mint an unlimited
    supply of fresh rate-limit buckets — the exact hole the header exists to close.
    With no secret configured, nothing is ever treated as proxied and every request
    buckets on the peer: over-restrictive for proxied traffic, never permissive.

    (This replaces a TRUSTED_PROXY_HOPS scheme that could not work here at any
    value: hop-counting assumes each proxy appends, and Railway overwrites.)
    """
    secret = proxy_secret()
    if secret:
        provided = (request.headers.get("x-saqua-proxy-secret") or "").strip()
        if provided and hmac.compare_digest(provided, secret):
            forwarded = (request.headers.get("x-saqua-client-ip") or "").strip()
            if forwarded:
                return forwarded
    return _peer(request)


def _over_limit(bucket: str, limit: int, window: int) -> bool:
    """True if this bucket is over its limit. Fails CLOSED.

    ``redis.rate_limited`` swallows errors and returns False (fail-open), which is
    the right call for an authenticated send path where blocking a paying user is
    worse than a missed count. It is the wrong call for an anonymous public write,
    so this deliberately does not use it.
    """
    try:
        return redis.incr_expiring(f"rl:{bucket}", window) > limit
    except Exception as exc:  # noqa: BLE001
        log.warning("waitlist rate check failed (%s) — refusing", type(exc).__name__)
        return True


def _guard_available() -> None:
    if require_shared_redis() and not redis.configured():
        log.error(
            "WAITLIST DISABLED — shared Redis is not configured, so rate limiting "
            "for this public unauthenticated endpoint would run on per-process "
            "state. Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN on the "
            "web service (or WAITLIST_REQUIRE_SHARED_REDIS=0 to override).")
        raise HTTPException(
            status_code=503,
            detail="The waitlist is temporarily unavailable. Please try again shortly.")


class JoinRequest(BaseModel):
    email: str
    source: str = None
    # Honeypot. Rendered hidden and left empty by a real browser; bots fill every
    # field they find. A filled value is answered with success and dropped.
    company: str = None

    @field_validator("email")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "").strip()[:waitlist.MAX_EMAIL_LEN]

    @field_validator("source")
    @classmethod
    def _trim_source(cls, v):
        return (v or "").strip()[:40] or None


# ── pages returned to email clicks ─────────────────────────────────────
def _page(title: str, body: str, *, form_action: str = None, token: str = None) -> str:
    action = ""
    if form_action and token:
        action = (
            f'<form method="post" action="{html_mod.escape(form_action)}">'
            f'<input type="hidden" name="t" value="{html_mod.escape(token)}">'
            '<button type="submit" style="background:#4f5af7;color:#fff;border:0;'
            'padding:12px 20px;border-radius:6px;font-weight:600;font-size:14px;'
            'cursor:pointer">Confirm unsubscribe</button></form>')
    return (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<title>{html_mod.escape(title)}</title>'
        '<div style="font-family:-apple-system,Segoe UI,Inter,sans-serif;'
        'max-width:480px;margin:12vh auto;padding:0 24px;color:#111;'
        'line-height:1.6;text-align:center">'
        f'<h1 style="font-size:22px;margin:0 0 12px">{html_mod.escape(title)}</h1>'
        f'<p style="color:#41424a;margin:0 0 24px">{body}</p>{action}'
        '</div>')


def register(app) -> None:
    """Mount the public waitlist routes."""

    @app.post("/api/waitlist")
    def join(body: JoinRequest, request: Request):
        _guard_available()

        # Honeypot: answer exactly as we would a real signup, store nothing.
        if (body.company or "").strip():
            log.info("waitlist: honeypot tripped from %s", client_ip(request))
            return {"ok": True}

        ip = client_ip(request)
        if _over_limit(f"wl:ip:{ip}", IP_LIMIT, IP_WINDOW):
            raise HTTPException(
                status_code=429,
                detail="Too many attempts from this network. Please try again later.")

        email = waitlist.normalize(body.email)
        if not waitlist.valid(email):
            raise HTTPException(status_code=400,
                                detail="That does not look like a valid email address.")

        if _over_limit(f"wl:em:{email}", EMAIL_LIMIT, EMAIL_WINDOW):
            # Silent success: telling the caller they hit a per-address cap would
            # itself confirm the address is on the list.
            return {"ok": True}

        result = waitlist.join(email, source=body.source)
        if result == waitlist.INVALID:
            raise HTTPException(status_code=400,
                                detail="That does not look like a valid email address.")
        # ok / already / error all answer identically: no enumeration, and a
        # transient mail failure is not the visitor's problem to interpret.
        return {"ok": True}

    @app.get("/api/waitlist/confirm", response_class=HTMLResponse)
    def confirm(t: str = ""):
        try:
            row = waitlist.confirm(t)
        except waitlist.ConfirmWriteError:
            # The token was good but the status did not persist. Saying "you are on
            # the list" here is the one answer we must not give: it is false, and it
            # stops the visitor from retrying the only action that could fix it.
            return HTMLResponse(_page(
                "We could not confirm you just now",
                "Your link is valid, but we could not save the change. Please open "
                "the link again in a moment — nothing is lost."),
                status_code=503)
        if not row:
            return HTMLResponse(_page(
                "Link not recognised",
                "That confirmation link is not valid. Try joining the waitlist again."),
                status_code=404)
        return HTMLResponse(_page(
            "You are on the list",
            "We will email you the moment Saqua opens up. Nothing else, no noise."))

    @app.get("/api/waitlist/unsubscribe", response_class=HTMLResponse)
    def unsubscribe_page(t: str = ""):
        """Shows a button rather than acting on GET. Mail clients and security
        scanners prefetch links, which would silently unsubscribe people who never
        clicked anything."""
        if not t:
            return HTMLResponse(_page("Link not recognised",
                                      "That unsubscribe link is not valid."),
                                status_code=404)
        return HTMLResponse(_page(
            "Unsubscribe?",
            "You will stop receiving Saqua waitlist email.",
            form_action="/api/waitlist/unsubscribe", token=t))

    @app.post("/api/waitlist/unsubscribe", response_class=HTMLResponse)
    async def unsubscribe(request: Request):
        # Token may arrive as a form post (our page) or a query param (RFC 8058
        # one-click from a mail client).
        token = request.query_params.get("t") or ""
        if not token:
            try:
                form = await request.form()
                token = (form.get("t") or "").strip()
            except Exception:  # noqa: BLE001
                token = ""
        row = waitlist.unsubscribe(token)
        if not row:
            return HTMLResponse(_page("Link not recognised",
                                      "That unsubscribe link is not valid."),
                                status_code=404)
        return HTMLResponse(_page("Unsubscribed",
                                  "You will not hear from us again."))
