"""Outlook / Microsoft Graph provider — real Graph REST API (no SDK dependency).

Sends via ``/me/sendMail``, reads via ``/me/messages``, and watches inbound mail
via a Graph change ``/subscriptions``. Needs a per-user OAuth **access token**.
Without one, operations raise ``ProviderNotConfigured`` and ``health()`` says so.
The app registration is valid (a client-credentials token mints successfully),
but per-user mailbox access needs delegated consent + a redirect URI on our
backend — the currently-provided redirect points at Clerk, not our callback.
"""

import os

import requests

from automation.providers.base import (
    EmailProvider,
    ProviderError,
    ProviderNotConfigured,
    SendResult,
)

_GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 30


class OutlookProvider(EmailProvider):
    name = "outlook"

    def _token(self) -> str:
        if isinstance(self.credentials, str):
            return self.credentials
        if isinstance(self.credentials, dict):
            return self.credentials.get("access_token") or ""
        return ""

    def _headers(self):
        tok = self._token()
        if not tok:
            raise ProviderNotConfigured(
                "Outlook is not connected: no per-user OAuth access token. "
                "Complete the Microsoft delegated consent to mint one.")
        return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    def _message(self, to, subject, body):
        return {"message": {"subject": subject,
                            "body": {"contentType": "Text", "content": body},
                            "toRecipients": [{"emailAddress": {"address": to}}]},
                "saveToSentItems": True}

    def send(self, *, to, subject, body, idempotency_key, thread_id=None):
        try:
            r = requests.post(f"{_GRAPH}/me/sendMail", headers=self._headers(),
                              json=self._message(to, subject, body), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"graph network error: {type(exc).__name__}")
        if r.status_code in (200, 202):
            # Graph sendMail returns 202 with no id; use the idempotency key as the
            # stable local id (the durable ledger dedupes anyway).
            return SendResult(message_id=f"graph_{idempotency_key}",
                              thread_id=thread_id, provider=self.name)
        if r.status_code in (401, 403):
            raise ProviderNotConfigured(f"graph auth error HTTP {r.status_code}")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError(f"graph transient HTTP {r.status_code}")
        raise ProviderError(f"graph HTTP {r.status_code}: {r.text[:120]}",
                            retryable=False)

    def reply(self, *, to, subject, body, idempotency_key, thread_id):
        if thread_id:
            try:
                r = requests.post(f"{_GRAPH}/me/messages/{thread_id}/reply",
                                  headers=self._headers(),
                                  json={"comment": body}, timeout=_TIMEOUT)
                if r.status_code in (200, 202):
                    return SendResult(message_id=f"graph_{idempotency_key}",
                                      thread_id=thread_id, provider=self.name)
            except requests.RequestException:
                pass
        return self.send(to=to, subject=subject, body=body,
                         idempotency_key=idempotency_key)

    def watch(self, *, user_id):
        notify = os.environ.get("GRAPH_WEBHOOK_URL")
        if not notify:
            raise ProviderNotConfigured(
                "Graph watch() needs a public webhook (GRAPH_WEBHOOK_URL) for the "
                "subscription notificationUrl; not configured.")
        r = requests.post(f"{_GRAPH}/subscriptions", headers=self._headers(),
                          json={"changeType": "created",
                                "notificationUrl": notify,
                                "resource": "me/mailFolders('Inbox')/messages",
                                "expirationDateTime": "2099-01-01T00:00:00Z"},
                          timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            return r.json()
        raise ProviderError(f"graph subscribe HTTP {r.status_code}")

    def stop(self, *, user_id):
        return None

    def get_message(self, message_id):
        """Fetch a single message to resolve its thread + sender (change
        notifications carry only the message id)."""
        r = requests.get(f"{_GRAPH}/me/messages/{message_id}",
                         headers=self._headers(),
                         params={"$select": "id,conversationId,from,internetMessageId"},
                         timeout=_TIMEOUT)
        if r.status_code != 200:
            raise ProviderError(f"graph message HTTP {r.status_code}")
        d = r.json()
        sender = (((d.get("from") or {}).get("emailAddress") or {}).get("address") or "")
        return {"message_id": d.get("id"), "thread_id": d.get("conversationId"),
                "from": sender.lower()}

    def health(self):
        if not (os.environ.get("MICROSOFT_CLIENT_ID")
                and os.environ.get("MICROSOFT_CLIENT_SECRET")):
            return {"status": "unconfigured",
                    "detail": "MICROSOFT_CLIENT_ID/SECRET not set"}
        if not self._token():
            return {"status": "unconfigured",
                    "detail": "app registration valid, but no per-user token "
                              "(delegated consent + backend redirect URI required)"}
        try:
            r = requests.get(f"{_GRAPH}/me", headers=self._headers(), timeout=_TIMEOUT)
            return ({"status": "ok", "detail": r.json().get("userPrincipalName", "")}
                    if r.status_code == 200
                    else {"status": "error", "detail": f"HTTP {r.status_code}"})
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
