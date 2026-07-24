"""Sandboxed demo sessions — the identity layer of the in-app live demo.

An anonymous visitor who passes the demo email gate becomes a first-class
principal with id ``demo_<32hex>``:

  * the namespace is DISJOINT from Clerk ids (which all start ``user_``), and the
    underscore form survives every store's user-id sanitisation, so demo data can
    never collide with — or address — a real user's data;
  * the id travels in a stateless, HMAC-signed token ``<id>.<exp>.<sig>`` set as
    an HttpOnly cookie; the Next proxy forwards it as ``X-Saqua-Demo-Session``;
  * expiry is enforced here (a dead token is simply invalid), and
    ``limits.is_paused(demo_id)`` still applies, so one abusive session can be
    kill-switched exactly like a real account.

Verification is constant-time on the signature and needs no storage; isolation
between visitors is 128 bits of server-generated randomness inside the
signature. Forging an id requires the secret.
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


def mint_token(demo_id: str, *, ttl_seconds: int = None) -> tuple[str, int]:
    """Return ``(token, expires_at_epoch)`` for a demo principal."""
    exp = int(time.time()) + (ttl_seconds or settings.DEMO_SESSION_TTL_SECONDS)
    payload = f"{demo_id}.{exp}"
    return f"{payload}.{_sign(payload)}", exp


def verify_token(token: str) -> str | None:
    """The demo id if ``token`` is authentic and unexpired, else None. Never raises."""
    try:
        demo_id, exp_s, sig = (token or "").strip().split(".")
        if not is_demo_id(demo_id):
            return None
        payload = f"{demo_id}.{exp_s}"
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        if int(exp_s) < time.time():
            return None
        return demo_id
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


def token_expiry(token: str) -> int | None:
    try:
        return int((token or "").split(".")[1])
    except Exception:  # noqa: BLE001
        return None
