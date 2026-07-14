"""Gmail provider — real Gmail REST API (no SDK dependency).

Sends via ``users.messages.send`` with a base64url MIME payload, reads/watches
via the REST endpoints. It needs a per-user OAuth **access token** (minted from a
stored refresh token after the user consents). Without one, every operation
raises ``ProviderNotConfigured`` and ``health()`` says so — honestly, rather than
pretending. The account-level OAuth *client* is configured
(GOOGLE_CLIENT_ID/SECRET); the missing piece is the per-user consent + token and
an authorized redirect URI on our backend + a Pub/Sub topic for ``watch()``.
"""

import base64
import os
from email.message import EmailMessage

import requests

from automation.providers.base import (
    EmailProvider,
    ProviderError,
    ProviderNotConfigured,
    SendResult,
)

_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TIMEOUT = 30


class GmailProvider(EmailProvider):
    name = "gmail"

    def _token(self) -> str:
        # credentials may be a raw access token or {"access_token": ...}.
        if isinstance(self.credentials, str):
            return self.credentials
        if isinstance(self.credentials, dict):
            return self.credentials.get("access_token") or ""
        return ""

    def _headers(self):
        tok = self._token()
        if not tok:
            raise ProviderNotConfigured(
                "Gmail is not connected: no per-user OAuth access token. Complete "
                "the Google OAuth consent to mint one.")
        return {"Authorization": f"Bearer {tok}"}

    @staticmethod
    def _raw(to, subject, body, thread_headers=None):
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        for k, v in (thread_headers or {}).items():
            msg[k] = v
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _post_send(self, payload):
        try:
            r = requests.post(f"{_API}/messages/send", headers=self._headers(),
                              json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"gmail network error: {type(exc).__name__}")
        if r.status_code == 200:
            d = r.json()
            return SendResult(message_id=d.get("id", ""),
                              thread_id=d.get("threadId"), provider=self.name, raw=d)
        if r.status_code in (401, 403):
            raise ProviderNotConfigured(f"gmail auth error HTTP {r.status_code}")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError(f"gmail transient HTTP {r.status_code}")
        raise ProviderError(f"gmail HTTP {r.status_code}: {r.text[:120]}",
                            retryable=False)

    def send(self, *, to, subject, body, idempotency_key, thread_id=None):
        payload = {"raw": self._raw(to, subject, body)}
        if thread_id:
            payload["threadId"] = thread_id
        return self._post_send(payload)

    def reply(self, *, to, subject, body, idempotency_key, thread_id):
        subj = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        payload = {"raw": self._raw(to, subj, body), "threadId": thread_id}
        return self._post_send(payload)

    def watch(self, *, user_id):
        topic = os.environ.get("GMAIL_PUBSUB_TOPIC")
        if not topic:
            raise ProviderNotConfigured(
                "Gmail watch() needs a Cloud Pub/Sub topic (GMAIL_PUBSUB_TOPIC) "
                "with gmail-api-push publish rights; not configured.")
        r = requests.post(f"{_API}/watch", headers=self._headers(),
                          json={"topicName": topic, "labelIds": ["INBOX"]},
                          timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        raise ProviderError(f"gmail watch HTTP {r.status_code}")

    def stop(self, *, user_id):
        requests.post(f"{_API}/stop", headers=self._headers(), timeout=_TIMEOUT)

    def list_history(self, start_history_id):
        """Changed inbox messages since ``start_history_id`` (Gmail push tells us
        only that *something* changed; this resolves it to messages/threads).
        Returns {'history_id': latest, 'messages': [{message_id, thread_id, labels}]}."""
        r = requests.get(f"{_API}/history", headers=self._headers(),
                         params={"startHistoryId": start_history_id,
                                 "historyTypes": "messageAdded"}, timeout=_TIMEOUT)
        if r.status_code != 200:
            raise ProviderError(f"gmail history HTTP {r.status_code}")
        d = r.json()
        messages = []
        for h in d.get("history", []):
            for m in h.get("messagesAdded", []):
                mm = m.get("message", {})
                messages.append({"message_id": mm.get("id"),
                                 "thread_id": mm.get("threadId"),
                                 "labels": mm.get("labelIds", [])})
        return {"history_id": d.get("historyId"), "messages": messages}

    def history_raw(self, start_history_id, history_types=None, max_pages=10):
        """DIAGNOSTIC: the RAW history.list response(s) since ``start_history_id``,
        following ``nextPageToken``. ``history_types=None`` requests ALL change
        types, so label/read/delete changes that a ``messageAdded``-only query
        hides become visible — this is how you tell 'no new message' apart from
        'a message we dropped'. Read-only. Returns the concatenated raw records
        plus a per-type tally; ``truncated`` flags if we hit ``max_pages``."""
        params = {"startHistoryId": start_history_id}
        if history_types:                     # omit -> Gmail returns every type
            params["historyTypes"] = history_types
        records, latest, token, pages = [], None, None, 0
        while True:
            p = dict(params)
            if token:
                p["pageToken"] = token
            r = requests.get(f"{_API}/history", headers=self._headers(),
                             params=p, timeout=_TIMEOUT)
            if r.status_code != 200:
                raise ProviderError(f"gmail history HTTP {r.status_code}: {r.text[:200]}")
            d = r.json()
            records.extend(d.get("history", []))
            latest = d.get("historyId") or latest
            pages += 1
            token = d.get("nextPageToken")
            if not token or pages >= max_pages:
                break
        tally = {}
        for h in records:
            for k in ("messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
                if h.get(k):
                    tally[k] = tally.get(k, 0) + len(h[k])
        return {"history_id": latest, "pages_fetched": pages,
                "truncated": bool(token), "record_count": len(records),
                "type_counts": tally, "records": records}

    def health(self):
        if not (os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET")):
            return {"status": "unconfigured",
                    "detail": "GOOGLE_CLIENT_ID/SECRET not set"}
        if not self._token():
            return {"status": "unconfigured",
                    "detail": "OAuth client set, but no per-user token "
                              "(consent + authorized redirect URI required)"}
        try:
            r = requests.get(f"{_API}/profile", headers=self._headers(),
                             timeout=_TIMEOUT)
            return ({"status": "ok", "detail": r.json().get("emailAddress", "")}
                    if r.status_code == 200
                    else {"status": "error", "detail": f"HTTP {r.status_code}"})
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": type(exc).__name__}
