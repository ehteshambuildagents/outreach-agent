"""Sandboxed demo sessions — the identity layer of the in-app live demo.

An anonymous visitor who passes the demo email gate becomes a first-class
principal with id ``demo_<32hex>``:

  * the namespace is DISJOINT from Clerk ids (which all start ``user_``), and the
    underscore form survives every store's user-id sanitisation, so demo data can
    never collide with — or address — a real user's data;
  * the id travels in a stateless, HMAC-signed token ``<id>.<email_hmac>.<exp>.<sig>``
    set as an HttpOnly cookie; the Next proxy forwards it as ``X-Saqua-Demo-Session``;
  * expiry is enforced here (a dead token is simply invalid), and
    ``limits.is_paused(demo_id)`` still applies, so one abusive session can be
    kill-switched exactly like a real account.

Two identities travel in one token, on purpose:

  * ``demo_id`` — BROWSER-BOUND. It rides in the persistent cookie and is the
    principal every per-user store scopes on (conversations, campaigns, …). A
    returning visitor in the SAME browser presents the same cookie, so their
    workspace restores; a visitor on a different browser/device gets a fresh
    ``demo_id`` and therefore never sees another person's history from an email
    alone.
  * ``email_hmac`` — a keyed hash of the normalised gate email (never the raw
    email). It keys the per-session TURN QUOTA, so the "five messages" allowance
    is tied to the email and CANNOT be reset by clearing the cookie or re-entering
    the demo. It is an HMAC, so it discloses nothing and is safe in a cookie.

Verification is constant-time on the signature and needs no storage; isolation
between visitors is 128 bits of server-generated randomness inside the
signature. Forging an id requires the secret. A short legacy ``<id>.<exp>.<sig>``
token (no email dimension) is still accepted so sessions minted before this
change are not logged out; they simply fall back to demo_id-keyed quota until the
next mint upgrades them.
"""

import hashlib
import hmac
import logging
import secrets
import time

from config import settings

log = logging.getLogger("saqua.demo.session")

COOKIE_NAME = "saqua_demo"          # HttpOnly — the signed token; JS can never read it
EXP_COOKIE = "saqua_demo_exp"       # readable — expiry epoch only, for the client's countdown
HEADER_NAME = "x-saqua-demo-session"
_PREFIX = "demo_"

# With no configured secret, sign with a per-process random one: dev works out of
# the box; in prod it only means sessions don't survive a restart (and the mint
# endpoint refuses to run without shared Redis anyway, so this is never the only
# line of defence).
_FALLBACK_SECRET = secrets.token_hex(32)


def _secret() -> bytes:
    return (settings.DEMO_SESSION_SECRET or _FALLBACK_SECRET).encode()


def is_demo_id(user_id: str) -> bool:
    return bool(user_id) and user_id.startswith(_PREFIX)


def new_demo_id() -> str:
    return _PREFIX + secrets.token_hex(16)


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def email_hmac(normalized_email: str) -> str:
    """A keyed, non-reversible tag for an already-normalised email. Keys the
    email-bound turn quota. Prefixed and truncated to 32 hex chars — enough
    collision resistance for a quota bucket, and it never contains a '.', so it
    can't confuse the dotted token grammar. Empty in, empty out."""
    if not normalized_email:
        return ""
    mac = hmac.new(_secret(), b"demo-email:" + normalized_email.encode(),
                   hashlib.sha256).hexdigest()
    return mac[:32]


def mint_token(demo_id: str, email_tag: str = "", *,
               ttl_seconds: int = None) -> tuple[str, int]:
    """Return ``(token, expires_at_epoch)`` for a demo principal.

    ``email_tag`` is an :func:`email_hmac` (or "" when unknown). It is signed into
    the token so the quota subject can't be tampered with."""
    exp = int(time.time()) + (ttl_seconds or settings.DEMO_SESSION_TTL_SECONDS)
    payload = f"{demo_id}.{email_tag}.{exp}"
    return f"{payload}.{_sign(payload)}", exp


def _parse(token: str) -> tuple[str, str | None, int] | None:
    """Return ``(demo_id, email_hmac_or_None, exp)`` for an authentic, unexpired
    token, else None. Accepts the current 4-part grammar and the legacy 3-part
    one. Never raises."""
    try:
        parts = (token or "").strip().split(".")
        if len(parts) == 4:
            demo_id, email_tag, exp_s, sig = parts
            payload = f"{demo_id}.{email_tag}.{exp_s}"
        elif len(parts) == 3:                       # legacy: no email dimension
            demo_id, exp_s, sig = parts
            email_tag = ""
            payload = f"{demo_id}.{exp_s}"
        else:
            return None
        if not is_demo_id(demo_id):
            return None
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        exp = int(exp_s)
        if exp < time.time():
            return None
        return demo_id, (email_tag or None), exp
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


def verify_token(token: str) -> str | None:
    """The demo id if ``token`` is authentic and unexpired, else None."""
    parsed = _parse(token)
    return parsed[0] if parsed else None


def token_email_hmac(token: str) -> str | None:
    """The signed email tag from an authentic token (None for legacy tokens or
    when the token is invalid)."""
    parsed = _parse(token)
    return parsed[1] if parsed else None


def quota_subject(token: str) -> str | None:
    """The identity the turn quota is charged against: the email tag when present
    (so the allowance survives cookie clears / re-entry), else the browser-bound
    demo id for legacy tokens. None when the token is invalid."""
    parsed = _parse(token)
    if not parsed:
        return None
    demo_id, tag, _exp = parsed
    return tag or demo_id


def token_expiry(token: str) -> int | None:
    parsed = _parse(token)
    return parsed[2] if parsed else None
