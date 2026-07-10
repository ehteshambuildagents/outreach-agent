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


def request_json(method: str, url: str, *, provider: str,
                 headers=None, json_body=None, params=None,
                 timeout: float = PROVIDER_TIMEOUT_SECONDS):
    """One provider HTTP call returning parsed JSON, or ``None`` on any failure.

    Retries transient failures (timeout / connection / 429 / 5xx) with bounded
    exponential backoff + jitter. Never raises — callers get ``None`` and the
    orchestrator simply continues with the other providers. The key/headers are
    never logged; only the provider name, status, and a short reason are.
    """
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
                    return resp.json()
                except ValueError:
                    log.info("%s: non-JSON response", provider)
                    return None
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
