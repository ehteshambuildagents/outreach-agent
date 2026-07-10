"""Clerk authentication for the API — JWT verification only.

The browser authenticates with Clerk (using the PUBLISHABLE key) and sends the
resulting short-lived session JWT as ``Authorization: Bearer <token>``. This
module verifies that token server-side against Clerk's public JWKS, so:

  * The Clerk SECRET key is never needed for verification and is never exposed
    to the client (only the publishable key is, via /api/public-config).
  * Verification uses Clerk's rotating public keys (RS256) fetched from the
    instance's JWKS endpoint and cached — no per-request round-trip to Clerk.

The Clerk instance (JWKS URL + issuer) is derived from the publishable key,
which base64-encodes the instance's Frontend API host. That means the same code
works for any Clerk app/environment without extra configuration.

If no Clerk keys are configured, auth is DISABLED (``require_user`` becomes a
pass-through) so the app still runs locally without Clerk — but with keys
present it is enforced on every protected endpoint.
"""

import base64
import logging
import os

import jwt
from fastapi import HTTPException, Request

log = logging.getLogger("saqua.auth")

_LEEWAY_SECONDS = 30           # tolerate small clock skew on exp/nbf
_JWT_ALGORITHMS = ["RS256"]    # Clerk session tokens are RS256


def _publishable_key() -> str:
    return (os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip()


def _secret_key() -> str:
    # Read here so it stays server-side; never returned or logged.
    return (os.environ.get("CLERK_SECRET_KEY") or "").strip()


def auth_enabled() -> bool:
    """True when Clerk is configured (a publishable key is present)."""
    return bool(_publishable_key())


def publishable_key() -> str:
    """The publishable key — safe to send to the browser. Never the secret."""
    return _publishable_key()


def _frontend_api_host(pk: str) -> str:
    """Derive the Clerk Frontend API host from the publishable key.

    ``pk_test_<b64>`` / ``pk_live_<b64>`` where the base64 payload decodes to
    ``<frontend-api-host>$`` (e.g. ``clever-cat-42.clerk.accounts.dev$``).
    """
    body = pk.split("_", 2)[-1]
    padded = body + "=" * (-len(body) % 4)      # restore base64 padding
    host = base64.b64decode(padded).decode("utf-8").rstrip("$").strip()
    if not host:
        raise ValueError("publishable key did not decode to a host")
    return host


# Lazily-built, cached JWKS client + issuer (built once per process).
_jwks_client = None
_issuer = None


def _clerk_verifier():
    global _jwks_client, _issuer
    if _jwks_client is not None:
        return _jwks_client, _issuer
    host = _frontend_api_host(_publishable_key())
    _issuer = f"https://{host}"
    # PyJWKClient fetches the signing keys once and caches them (with its own
    # TTL) — verification does not hit the network on every request.
    _jwks_client = jwt.PyJWKClient(f"https://{host}/.well-known/jwks.json")
    return _jwks_client, _issuer


def verify_token(token: str) -> dict:
    """Verify a Clerk session JWT; return its claims or raise ValueError.

    Checks the RS256 signature against Clerk's JWKS, the issuer, and expiry
    (exp/nbf, with a little leeway). Clerk session tokens have no ``aud``, so
    audience verification is disabled deliberately.
    """
    jwks_client, issuer = _clerk_verifier()
    signing_key = jwks_client.get_signing_key_from_jwt(token)  # raises on bad kid/format
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=_JWT_ALGORITHMS,
        issuer=issuer,
        leeway=_LEEWAY_SECONDS,
        options={"verify_aud": False, "require": ["exp", "iat"]},
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authentication required.")
    return token.strip()


def require_user(request: Request) -> str:
    """FastAPI dependency: verify the caller's Clerk JWT, return the user id.

    Raises 401 for a missing/invalid/expired token. When Clerk isn't configured
    at all, auth is disabled and this returns ``"anonymous"`` so local dev runs.
    """
    if not auth_enabled():
        return "anonymous"
    token = _bearer(request)
    try:
        claims = verify_token(token)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - any verification failure is a 401
        log.info("token rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session.")
    return user_id
