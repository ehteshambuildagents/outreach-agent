"""Transactional email for the waitlist, via Resend.

This is deliberately separate from ``automation.providers`` (Gmail/Outlook). Those
send a USER's outreach from a USER's mailbox over per-user OAuth. This sends
Saqua's own mail from Saqua's own domain, so it needs its own sender identity and
its own credential — a customer's mailbox must never be used to send our product
announcements.

Configuration (both required before anything sends):
  * ``RESEND_API_KEY``       — Resend API key
  * ``WAITLIST_FROM_EMAIL``  — verified sender, e.g. ``Saqua <hello@saqua.io>``

Unconfigured is a first-class state: :func:`configured` is False and :func:`send`
refuses rather than pretending. Nothing here ever raises to a caller.
"""

import json
import logging
import os

import requests

log = logging.getLogger("saqua.waitlist.email")

_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT = 20


def api_key() -> str:
    return (os.environ.get("RESEND_API_KEY") or "").strip()


def from_email() -> str:
    return (os.environ.get("WAITLIST_FROM_EMAIL") or "").strip()


def configured() -> bool:
    return bool(api_key() and from_email())


def send(to: str, subject: str, html: str, text: str = None,
         headers: dict = None) -> tuple:
    """Send one email. Returns ``(ok: bool, detail: str)``.

    Never raises: a send failure is data the caller acts on (the broadcast leaves
    ``notified_at`` unset so the address is retried on the next run).
    """
    if not configured():
        return False, "resend not configured (RESEND_API_KEY / WAITLIST_FROM_EMAIL)"
    payload = {"from": from_email(), "to": [to], "subject": subject, "html": html}
    if text:
        payload["text"] = text
    if headers:
        payload["headers"] = headers
    try:
        resp = requests.post(
            _ENDPOINT,
            headers={"Authorization": f"Bearer {api_key()}",
                     "Content-Type": "application/json"},
            data=json.dumps(payload), timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("waitlist send failed (network): %s", type(exc).__name__)
        return False, f"network: {type(exc).__name__}"
    if resp.status_code >= 400:
        # Body can carry the real reason (unverified domain, invalid address).
        detail = (resp.text or "")[:200]
        log.warning("waitlist send failed: HTTP %s %s", resp.status_code, detail)
        return False, f"http {resp.status_code}: {detail}"
    return True, "sent"
