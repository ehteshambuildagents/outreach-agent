"""Jina AI provider — clean a messy webpage into LLM-ready markdown.

Responsibility: some pages (heavy JS, cluttered markup, search-result links from
Tavily/Exa that returned only a snippet) read poorly. Jina's Reader fetches a URL
and returns clean markdown, so the synthesis model sees content instead of noise.
The orchestrator uses it to enrich a source when the snippet on hand is too thin.

Key: JINA_API_KEY (optional — Reader also works unauthenticated at a lower rate
limit; with a key it is faster and higher-limit). Server-side only, never logged.
"""

import logging

from config.settings import JINA_PAGE_CHARS, PROVIDER_TIMEOUT_SECONDS
from research.providers_common import get_key, request_json

log = logging.getLogger("research.jina")

_READER = "https://r.jina.ai/"
_ENV = "JINA_API_KEY"


def available() -> bool:
    """A key is present. (Reader tolerates no key, but we only auto-use it when
    configured, to respect the anonymous rate limit.)"""
    return bool(get_key(_ENV))


def _headers() -> dict:
    h = {"Accept": "application/json", "X-Return-Format": "markdown"}
    key = get_key(_ENV)
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def clean_url(url: str, *, timeout: float = PROVIDER_TIMEOUT_SECONDS) -> str:
    """Return clean markdown for a URL via Jina Reader, or "" on failure."""
    if not url:
        return ""
    data = request_json("GET", _READER + url, provider="jina",
                        headers=_headers(), timeout=timeout)
    # Reader with Accept: application/json returns {"data": {"content": "..."}}.
    content = ((data or {}).get("data") or {}).get("content") or ""
    return content.strip()[:JINA_PAGE_CHARS]
