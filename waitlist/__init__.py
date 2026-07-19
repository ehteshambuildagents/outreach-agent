"""Pre-launch waitlist: double opt-in capture, and the launch announcement.

Flow:
  1. A visitor submits an address  -> row created ``unconfirmed``, confirm email sent.
  2. They click the confirm link   -> ``subscribed``. Only these are ever broadcast to.
  3. Launch day                    -> :mod:`waitlist.broadcast` emails every
     confirmed address exactly once, stamping ``notified_at`` as it goes.
  4. Any email carries an unsubscribe link -> ``unsubscribed``, permanently skipped.

Double opt-in is the point, not ceremony: an unconfirmed address is never mailed
again beyond its single confirmation, so a typo'd or maliciously-submitted address
costs one message and then goes quiet. It also keeps the sending domain's
reputation intact, which matters more here than most places — deliverability is
the product's own pitch.

This module owns policy; :mod:`waitlist.store` owns persistence and
:mod:`waitlist.email` owns delivery.
"""

import logging
import os
import re

from waitlist import email as mailer
from waitlist import store

log = logging.getLogger("saqua.waitlist")

# Deliberately permissive but bounded: one @, no whitespace, a dotted domain, and
# a total length cap. Strict RFC 5322 validation rejects addresses that really do
# work; the confirm step is what actually proves an address is real.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
MAX_EMAIL_LEN = 254

# Outcome codes returned by join(). The public endpoint maps every one of these to
# the SAME response, so the form can never be used to test whether an address is
# already on the list.
OK, ALREADY, INVALID, ERROR = "ok", "already", "invalid", "error"


def normalize(email: str) -> str:
    """Lowercase + trim. Deliberately no gmail dot/plus folding: two addresses that
    look equivalent may genuinely be different mailboxes, and silently merging them
    would drop a real signup."""
    return (email or "").strip().lower()


def valid(email: str) -> bool:
    return bool(email) and len(email) <= MAX_EMAIL_LEN and bool(_EMAIL_RE.match(email))


def site_url() -> str:
    """Public origin used to build confirm/unsubscribe links."""
    return (os.environ.get("SAQUA_SITE_URL") or "https://saqua.io").rstrip("/")


def confirm_url(token: str) -> str:
    return f"{site_url()}/api/waitlist/confirm?t={token}"


def unsubscribe_url(token: str) -> str:
    return f"{site_url()}/api/waitlist/unsubscribe?t={token}"


# ── join ───────────────────────────────────────────────────────────────
def join(email: str, source: str = None, *, db=None) -> str:
    """Record a signup and send the confirmation email.

    Returns one of OK / ALREADY / INVALID / ERROR. The caller is expected to give
    the same user-facing answer for all of them except INVALID.
    """
    email = normalize(email)
    if not valid(email):
        return INVALID
    try:
        existing = store.get(email, db=db)
        row = store.create_unconfirmed(email, source=source, db=db)
        if row is None:
            return ERROR

        # Already confirmed, or opted out: do nothing and send nothing. Re-mailing
        # an unsubscribed address because they hit the form again would be exactly
        # the behaviour that gets a domain blocklisted.
        if existing and existing.get("status") in (store.SUBSCRIBED, store.UNSUBSCRIBED):
            return ALREADY

        sent, detail = _send_confirmation(row)
        if not sent:
            log.warning("waitlist: confirmation not sent to %s (%s)", email, detail)
            # The row stands. They can re-submit, which re-sends against the same
            # token, so a transient mail outage costs nothing permanent.
            return ERROR
        _record("joined", email, source)
        return OK
    except Exception:  # noqa: BLE001
        log.debug("waitlist join failed for %s", email, exc_info=True)
        return ERROR


def confirm(token: str, *, db=None) -> dict:
    """Confirm via link token. Returns the row, or None if the token is unknown.

    Idempotent: clicking twice is a success both times (the second is already
    ``subscribed``), because bouncing a user to an error page for double-clicking
    their own confirm link is worse than useless.
    """
    row = store.get_by_token(token, db=db)
    if not row:
        return None
    if row["status"] == store.UNCONFIRMED:
        store.confirm(row["email"], db=db)
        _record("confirmed", row["email"], row.get("source"))
        return store.get(row["email"], db=db)
    return row


def unsubscribe(token: str, *, db=None) -> dict:
    row = store.get_by_token(token, db=db)
    if not row:
        return None
    if row["status"] != store.UNSUBSCRIBED:
        store.unsubscribe(row["email"], db=db)
        _record("unsubscribed", row["email"], row.get("source"))
        return store.get(row["email"], db=db)
    return row


def counts(*, db=None) -> dict:
    return store.counts(db=db)


# ── messages ───────────────────────────────────────────────────────────
def _shell(body_html: str, unsub: str = None) -> str:
    foot = ""
    if unsub:
        foot = (f'<p style="margin:28px 0 0;font-size:12px;color:#6c6d76">'
                f'You received this because you joined the Saqua waitlist. '
                f'<a href="{unsub}" style="color:#6c6d76">Unsubscribe</a>.</p>')
    return (
        '<div style="font-family:-apple-system,Segoe UI,Inter,sans-serif;'
        'max-width:520px;margin:0 auto;padding:32px 24px;color:#111111;'
        'line-height:1.6">'
        f'{body_html}{foot}'
        '</div>')


def _send_confirmation(row: dict) -> tuple:
    url = confirm_url(row["token"])
    html = _shell(
        '<h1 style="font-size:20px;margin:0 0 16px">Confirm your spot</h1>'
        '<p style="margin:0 0 20px">You asked to hear when Saqua opens up. '
        'Tap below to confirm, and we will email you the moment it does.</p>'
        f'<p style="margin:0 0 24px"><a href="{url}" '
        'style="background:#4f5af7;color:#ffffff;text-decoration:none;'
        'padding:12px 20px;border-radius:6px;display:inline-block;'
        'font-weight:600">Confirm my email</a></p>'
        '<p style="margin:0;font-size:13px;color:#6c6d76">If you did not sign up, '
        'ignore this and you will not hear from us again.</p>')
    text = ("Confirm your spot on the Saqua waitlist:\n\n" + url +
            "\n\nIf you did not sign up, ignore this email.")
    return mailer.send(row["email"], "Confirm your spot on the Saqua waitlist",
                       html, text=text)


def launch_message(row: dict) -> tuple:
    """(subject, html, text, headers) for the launch announcement."""
    unsub = unsubscribe_url(row["token"])
    html = _shell(
        '<h1 style="font-size:20px;margin:0 0 16px">Saqua is open</h1>'
        '<p style="margin:0 0 20px">You joined the waitlist early, so you get in '
        'first. Your founding pricing is locked in.</p>'
        f'<p style="margin:0 0 24px"><a href="{site_url()}" '
        'style="background:#4f5af7;color:#ffffff;text-decoration:none;'
        'padding:12px 20px;border-radius:6px;display:inline-block;'
        'font-weight:600">Get started</a></p>', unsub=unsub)
    text = ("Saqua is open. You joined the waitlist early, so you get in first.\n\n"
            f"{site_url()}\n\nUnsubscribe: {unsub}")
    # RFC 8058: lets a mail client offer a real one-click unsubscribe, which keeps
    # people from reaching for "mark as spam" instead.
    headers = {"List-Unsubscribe": f"<{unsub}>",
               "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return "Saqua is open", html, text, headers


def _record(event: str, email: str, source: str = None) -> None:
    """Best-effort telemetry; never affects the caller."""
    try:
        from telemetry import record_event
        record_event("waitlist", event, entity_id=email, detail=source or "")
    except Exception:  # noqa: BLE001
        pass
