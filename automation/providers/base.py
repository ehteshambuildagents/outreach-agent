"""The email-provider interface every backend implements.

Five operations, same shape for Gmail and Outlook:
    send()   — send one message (idempotent via the caller's key)
    reply()  — reply within an existing thread
    watch()  — start push notifications for inbound mail (reply detection)
    stop()   — stop those notifications
    health() — is this provider usable right now? (config + reachability)

Errors are normalised to two types so the engine's retry logic is provider-
agnostic: ``ProviderNotConfigured`` (permanent — missing token/consent, do NOT
retry) and ``ProviderError`` (transient — network/5xx/rate-limit, DO retry).
"""

import abc
from dataclasses import dataclass, field
from typing import Optional


class ProviderError(Exception):
    """A transient provider failure worth retrying (network, 5xx, rate-limit)."""
    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class ProviderNotConfigured(ProviderError):
    """Permanent: the provider can't run (no OAuth token / consent). Never retry."""
    def __init__(self, message):
        super().__init__(message, retryable=False)


@dataclass
class SendResult:
    message_id: str
    thread_id: Optional[str] = None
    provider: str = ""
    raw: dict = field(default_factory=dict)


class EmailProvider(abc.ABC):
    name = "base"

    def __init__(self, credentials=None):
        # credentials: a per-user OAuth access token (str) or a small dict.
        self.credentials = credentials

    @abc.abstractmethod
    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str, thread_id: str = None) -> SendResult:
        ...

    @abc.abstractmethod
    def reply(self, *, to: str, subject: str, body: str,
              idempotency_key: str, thread_id: str) -> SendResult:
        ...

    @abc.abstractmethod
    def watch(self, *, user_id: str) -> dict:
        """Begin push notifications for inbound mail. Returns a small config dict
        (e.g. {'history_id': ..., 'expiration': ...}) or {} if not applicable."""
        ...

    @abc.abstractmethod
    def stop(self, *, user_id: str) -> None:
        ...

    @abc.abstractmethod
    def health(self) -> dict:
        """{'status': 'ok'|'unconfigured'|'error', 'detail': str}. Never raises."""
        ...
