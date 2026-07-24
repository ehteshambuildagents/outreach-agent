"""Demo-aware authorization dependencies, shared across route modules.

These let a SINGLE real route serve both a signed-in member and an anonymous
sandboxed demo visitor:

  * a Bearer token means a real member and takes the module's EXISTING member
    path unchanged (identity via ``require_user``), so real users are unaffected;
  * with no bearer, a valid demo session yields its ``demo_*`` principal, which
    every per-user store scopes on automatically — real pages over demo-only
    data that can never address another principal's.

Kept in its own module (not ``server.api``) so ``campaign_api`` / ``oauth_api``
can import it without a circular dependency. The demo path never touches the
access store (so it can't pollute the approval queue) but IS subject to the
pause kill switch, exactly like a member.
"""

from fastapi import HTTPException, Request

import limits
from server import demo_session
from server.auth import require_user

_PAUSED_MESSAGE = "This demo session is paused."


def demo_principal(request: Request) -> str | None:
    """The demo principal id for a request carrying a valid, unexpired demo
    session (proxy-forwarded header, or the forwarded cookie), else None."""
    tok = (request.headers.get(demo_session.HEADER_NAME)
           or request.cookies.get(demo_session.COOKIE_NAME))
    return demo_session.verify_token(tok) if tok else None


def has_bearer(request: Request) -> bool:
    return (request.headers.get("authorization") or "").lower().startswith("bearer ")


def demo_or_401(request: Request) -> str:
    """Resolve the demo principal or raise. Enforces the pause kill switch; never
    the access store."""
    did = demo_principal(request)
    if not did:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if limits.is_paused(did):
        raise HTTPException(status_code=403, detail=_PAUSED_MESSAGE)
    return did


def require_identity_or_demo(request: Request) -> str:
    """Member IDENTITY (``require_user``) OR a demo principal. For routes that only
    need to know *who* the caller is (their own per-user data), not approval."""
    if has_bearer(request):
        return require_user(request)
    return demo_or_401(request)
