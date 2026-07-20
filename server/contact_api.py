"""Public contact endpoint: send a message from the site instead of handing the
visitor off to their mail client.

Same safety posture as the waitlist (the app's other unauthenticated write): it is
rate-limited on the real client IP, guarded by a honeypot, and refuses to serve
without shared Redis in production so its limiter can never silently degrade to
per-process state. It stores nothing — the message is emailed to the support inbox
with the visitor's address as Reply-To, so a reply goes straight back to them.

A failed send is reported to the caller (502), not swallowed behind a cheerful
"thanks!": the visitor needs to know the message did not go through so they can
fall back to emailing support directly.
"""

import html as html_mod
import logging
import os

from fastapi import HTTPException, Request
from pydantic import BaseModel, field_validator

import waitlist
from waitlist import email as mailer
from server.waitlist_api import client_ip, _over_limit, _guard_available

log = logging.getLogger("saqua.contact.api")

# One person with a real question sends once or twice. Five an hour per network is
# generous for that and far below anything useful for spamming the inbox.
IP_LIMIT, IP_WINDOW = 5, 3600

MAX_SUBJECT_LEN = 160
MAX_MESSAGE_LEN = 4000


def support_email() -> str:
    """Where contact messages are delivered. Defaults to the published address."""
    return (os.environ.get("CONTACT_TO_EMAIL") or "support@saqua.io").strip()


class ContactRequest(BaseModel):
    email: str
    message: str
    subject: str = None
    # Honeypot. Hidden in the real form, so a person leaves it empty and a bot
    # fills it. A filled value is answered with success and dropped.
    company: str = None

    @field_validator("email")
    @classmethod
    def _trim_email(cls, v: str) -> str:
        return (v or "").strip()[:waitlist.MAX_EMAIL_LEN]

    @field_validator("subject")
    @classmethod
    def _trim_subject(cls, v):
        return (v or "").strip()[:MAX_SUBJECT_LEN] or None

    @field_validator("message")
    @classmethod
    def _trim_message(cls, v: str) -> str:
        return (v or "").strip()[:MAX_MESSAGE_LEN]


def _body(sender: str, subject: str, message: str) -> tuple:
    """(html, text) for the support notification. The message is escaped for the
    HTML part; the text part carries it verbatim."""
    safe_msg = html_mod.escape(message).replace("\n", "<br>")
    safe_sender = html_mod.escape(sender)
    safe_subject = html_mod.escape(subject)
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Inter,sans-serif;'
        'max-width:560px;color:#111;line-height:1.6">'
        f'<p style="margin:0 0 4px;color:#41424a">New message via the Saqua contact form</p>'
        f'<p style="margin:0 0 2px"><strong>From:</strong> {safe_sender}</p>'
        f'<p style="margin:0 0 16px"><strong>Subject:</strong> {safe_subject}</p>'
        f'<div style="border-top:1px solid #e5e5e5;padding-top:16px">{safe_msg}</div>'
        '</div>')
    text = f"New message via the Saqua contact form\n\nFrom: {sender}\nSubject: {subject}\n\n{message}"
    return html, text


def register(app) -> None:
    """Mount the public contact route."""

    @app.post("/api/contact")
    def contact(body: ContactRequest, request: Request):
        _guard_available()

        # Honeypot: answer as we would a real send, do nothing.
        if (body.company or "").strip():
            log.info("contact: honeypot tripped from %s", client_ip(request))
            return {"ok": True}

        ip = client_ip(request)
        if _over_limit(f"contact:ip:{ip}", IP_LIMIT, IP_WINDOW):
            raise HTTPException(
                status_code=429,
                detail="Too many messages from this network. Please try again later.")

        email = waitlist.normalize(body.email)
        if not waitlist.valid(email):
            raise HTTPException(status_code=400,
                                detail="Please enter a valid email address so we can reply.")
        if not body.message:
            raise HTTPException(status_code=400,
                                detail="Please include a message.")

        subject = body.subject or "Saqua — hello"
        html, text = _body(email, subject, body.message)
        sent, detail = mailer.send(
            support_email(),
            f"Contact form: {subject}",
            html,
            text=text,
            # A support reply goes straight to the visitor, not to the from-address.
            headers={"Reply-To": email},
        )
        if not sent:
            log.warning("contact: send failed from %s (%s)", email, detail)
            raise HTTPException(
                status_code=502,
                detail="We could not send your message just now. Please email "
                       f"{support_email()} directly.")
        return {"ok": True}
