"""Dry-run provider — deterministic, no network. Used for local runs and tests.

It behaves like a real provider (idempotent sends, stable message ids, a health
check) so the ENTIRE engine — scheduling, sending, waiting, reply-stop, retries,
recovery — is exercisable end-to-end without a live mailbox. A configurable
``fail_times`` lets tests drive the retry/backoff path.
"""

import hashlib
import threading

from automation.providers.base import EmailProvider, ProviderError, SendResult


class DryRunProvider(EmailProvider):
    name = "dryrun"

    # Class-level record so a test can inspect what "went out" across instances.
    sent = []
    _lock = threading.Lock()
    fail_times = 0                 # if >0, the next N sends raise a transient error
    _by_key = {}                   # idempotency_key -> SendResult (dedup)

    def _mid(self, idempotency_key: str) -> str:
        return "dry_" + hashlib.sha1(idempotency_key.encode()).hexdigest()[:16]

    def send(self, *, to, subject, body, idempotency_key, thread_id=None):
        with DryRunProvider._lock:
            # Idempotent: the same key never "sends" twice.
            if idempotency_key in DryRunProvider._by_key:
                return DryRunProvider._by_key[idempotency_key]
            if DryRunProvider.fail_times > 0:
                DryRunProvider.fail_times -= 1
                raise ProviderError("dryrun forced transient failure")
            res = SendResult(message_id=self._mid(idempotency_key),
                             thread_id=thread_id or self._mid(idempotency_key),
                             provider=self.name,
                             raw={"to": to, "subject": subject})
            DryRunProvider._by_key[idempotency_key] = res
            DryRunProvider.sent.append({"to": to, "subject": subject, "body": body,
                                        "message_id": res.message_id})
            return res

    def reply(self, *, to, subject, body, idempotency_key, thread_id):
        return self.send(to=to, subject=subject, body=body,
                         idempotency_key=idempotency_key, thread_id=thread_id)

    def watch(self, *, user_id):
        return {"history_id": "dry-history", "expiration": None}

    def stop(self, *, user_id):
        return None

    def health(self):
        return {"status": "ok", "detail": "dry-run (no network)"}

    # test helper
    @classmethod
    def reset(cls):
        with cls._lock:
            cls.sent = []
            cls._by_key = {}
            cls.fail_times = 0
