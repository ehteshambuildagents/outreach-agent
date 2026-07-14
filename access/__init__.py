"""Request-access gating for the soft launch.

New signups don't get instant full access: the first time a user hits a gated
endpoint they're recorded as ``pending`` and held until someone approves them.
Approved users get normal, full access immediately — the gate is about pace
control, not a permanent restriction.

Enforcement lives at one API dependency (``require_approved_user`` in server/api.py),
which calls :func:`check_access` after the identity is verified. This module owns
the policy; :mod:`access.store` owns persistence.

Gating is active only when it should be:
  * ``ACCESS_GATING`` = ``auto`` (default) turns it on exactly when Clerk auth is
    configured (i.e. a real deployment). Set ``1`` to force on, ``0`` to force off.
  * local/dev with no Clerk (user ``anonymous``) is never gated, so dev just runs.
  * ids/emails in ``ACCESS_AUTO_APPROVE`` (comma-separated) are auto-approved on
    first sight — the bootstrap so the founder isn't locked out of their own app.
"""

import logging
import os

from access import store

log = logging.getLogger("saqua.access")

PENDING, APPROVED, DENIED = "pending", "approved", "denied"


def _clerk_configured() -> bool:
    return bool((os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip())


def gating_enabled() -> bool:
    mode = (os.environ.get("ACCESS_GATING") or "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    return _clerk_configured()      # "auto"


def _auto_approve_set() -> set:
    raw = os.environ.get("ACCESS_AUTO_APPROVE") or ""
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _is_auto_approved(user_id: str, email: str = None) -> bool:
    s = _auto_approve_set()
    if not s:
        return False
    return (str(user_id).lower() in s) or (bool(email) and email.lower() in s)


def check_access(user_id: str, email: str = None, *, db=None) -> tuple:
    """Decide access for a verified user, recording first-seen users as pending.

    Returns ``(allowed: bool, status: str)``. Fails OPEN (allowed) on any internal
    error so a storage hiccup never locks out legitimate users; the deliberate
    ``pending``/``denied`` states are the only things that deny.
    """
    if not gating_enabled():
        return True, APPROVED
    if not user_id or user_id == "anonymous":
        return True, APPROVED
    try:
        existing = store.get(user_id, db=db)
        if existing:
            status = existing.get("status")
            return (status == APPROVED), (status or PENDING)
        # First time we've seen this user.
        if _is_auto_approved(user_id, email):
            store.create_pending(user_id, email, status=APPROVED, db=db)
            log.info("access: auto-approved %s", user_id)
            return True, APPROVED
        created = store.create_pending(user_id, email, db=db)
        if created:
            log.info("access: new pending user %s", user_id)
            _notify_pending(user_id, email)
        return False, PENDING
    except Exception:  # noqa: BLE001 - availability first
        log.debug("access check failed open for %s", user_id, exc_info=True)
        return True, APPROVED


def is_approved(user_id: str, *, db=None) -> bool:
    allowed, _ = check_access(user_id, db=db)
    return allowed


# ── admin operations ───────────────────────────────────────────────────
def approve(user_id: str, note: str = None, *, db=None) -> None:
    store.set_status(user_id, APPROVED, note or "approved by admin", db=db)
    log.info("access: approved %s", user_id)


def deny(user_id: str, note: str = None, *, db=None) -> None:
    store.set_status(user_id, DENIED, note or "denied by admin", db=db)
    log.info("access: denied %s", user_id)


def list_pending(*, db=None) -> list:
    return store.list_by_status(PENDING, db=db)


def list_all(*, db=None) -> list:
    return store.list_by_status(None, db=db)


def _notify_pending(user_id: str, email: str = None) -> None:
    """Best-effort signal that a new user is waiting (telemetry + optional webhook)."""
    try:
        from telemetry import record_event
        record_event("access", "pending", entity_id=user_id, detail=email or "")
    except Exception:  # noqa: BLE001
        pass
