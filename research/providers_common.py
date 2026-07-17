"""Shared plumbing for the external research providers.

Every provider (Firecrawl, Tavily, Exa, Jina) is a thin, self-contained client
with the SAME production behaviour, centralised here so it is written once:

  * key read from the environment (never hard-coded, never logged);
  * a bounded HTTP request with a timeout;
  * retries for TRANSIENT failures only (timeouts, connection drops, 429, 5xx)
    with exponential backoff + jitter — never for 4xx client errors;
  * graceful failure: a provider returns ``None``/``[]`` and logs a short reason
    instead of raising, so one provider failing never breaks the orchestrator.

Only the orchestrator calls the providers; nothing here talks to the browser.
"""

import logging
import os
import random
import time

import requests

from config.settings import (
    PROVIDER_BACKOFF_BASE_SECONDS,
    PROVIDER_MAX_RETRIES,
    PROVIDER_TIMEOUT_SECONDS,
)

log = logging.getLogger("research.providers")

# Status codes worth retrying: rate-limit + server faults. 4xx (auth, bad
# request) are permanent and fail fast.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def get_key(env_name: str) -> str:
    """Read a provider key from the environment (empty string if unset)."""
    return (os.environ.get(env_name) or "").strip()


# ── Per-user usage caps (best-effort; never break a provider call) ─────
def _cap_user():
    """The ambient user this call is serving, or None for a system call."""
    try:
        from telemetry import context
        return context.get("user_id")
    except Exception:  # noqa: BLE001
        return None


def _cap_allow(provider: str, user_id: str):
    try:
        import limits
        return limits.allow(provider, user_id)
    except Exception:  # noqa: BLE001 - caps must never break a research call
        return None


def _cap_record(provider: str, user_id: str) -> None:
    try:
        import limits
        limits.record(provider, user_id)
    except Exception:  # noqa: BLE001
        pass


def request_json(method: str, url: str, *, provider: str,
                 headers=None, json_body=None, params=None,
                 timeout: float = PROVIDER_TIMEOUT_SECONDS):
    """One provider HTTP call returning parsed JSON, or ``None`` on any failure.

    Retries transient failures (timeout / connection / 429 / 5xx) with bounded
    exponential backoff + jitter. Never raises — callers get ``None`` and the
    orchestrator simply continues with the other providers. The key/headers are
    never logged; only the provider name, status, and a short reason are.
    """
    # Per-user spend cap (public-signup safety). The ambient telemetry user_id is
    # set by the chat/campaign layers around this call; a system call has none and
    # is never capped. A capped user gets None — the same graceful-degradation
    # signal every provider already handles — instead of a paid API hit.
    user_id = _cap_user()
    if user_id:
        decision = _cap_allow(provider, user_id)
        if decision is not None and not decision.allowed:
            log.info("%s: usage cap reached for user — skipping paid call (%s)",
                     provider, decision.reason)
            return None

    last_reason = "request failed"
    for attempt in range(PROVIDER_MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, params=params,
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            last_reason = "timed out"
        except requests.exceptions.ConnectionError:
            last_reason = "connection error"
        except requests.exceptions.RequestException:
            log.info("%s: request error", provider)
            return None                                   # malformed/permanent
        else:
            if resp.status_code == 200:
                try:
                    parsed = resp.json()
                except ValueError:
                    log.info("%s: non-JSON response", provider)
                    return None
                if user_id:
                    _cap_record(provider, user_id)   # meter the real, paid read
                return parsed
            if resp.status_code not in _RETRYABLE_STATUS:
                log.info("%s: HTTP %s (not retrying)", provider, resp.status_code)
                return None                               # 4xx -> permanent
            last_reason = f"HTTP {resp.status_code}"

        if attempt == PROVIDER_MAX_RETRIES:
            break
        delay = PROVIDER_BACKOFF_BASE_SECONDS * (2 ** attempt)
        delay += random.uniform(0, delay * 0.25)          # jitter
        log.info("%s: %s — retry %d/%d", provider, last_reason,
                 attempt + 1, PROVIDER_MAX_RETRIES)
        time.sleep(delay)

    log.info("%s: giving up (%s)", provider, last_reason)
    return None
