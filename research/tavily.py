"""Tavily provider — recent, real-world signal about a company.

Responsibility: fresh external information a website won't tell you — news,
product launches, funding, announcements, partnerships. Used by the orchestrator
for "what's happening now" and by the company-name resolver (chat/resolver.py)
for name -> official-website lookup, so every Tavily call goes through here.

Key: TAVILY_API_KEY (server-side only, never logged).
"""

import logging

from config.settings import (
    PROVIDER_TIMEOUT_SECONDS,
    TAVILY_MAX_RESULTS,
    TAVILY_NEWS_DAYS,
)
from research.providers_common import get_key, request_json

log = logging.getLogger("research.tavily")

_API = "https://api.tavily.com/search"
_ENV = "TAVILY_API_KEY"


def available() -> bool:
    return bool(get_key(_ENV))


def _results(data) -> list:
    """Normalize Tavily results to {url, title, content, published_date}."""
    out = []
    for r in (data or {}).get("results") or []:
        if not r.get("url"):
            continue
        out.append({
            "url": r.get("url"),
            "title": r.get("title") or "",
            "content": r.get("content") or "",
            "published_date": r.get("published_date") or None,
        })
    return out


def search(query: str, *, max_results: int = TAVILY_MAX_RESULTS,
           topic: str = "general", days: int = None,
           timeout: float = PROVIDER_TIMEOUT_SECONDS) -> list:
    """Run one Tavily search. Returns a list of results (possibly empty).

    topic="news" + days=N restricts to recent items. Never raises.
    """
    if not available():
        return []
    body = {
        "api_key": get_key(_ENV),
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "topic": topic,
    }
    if topic == "news":
        body["days"] = days or TAVILY_NEWS_DAYS
    data = request_json("POST", _API, provider="tavily", json_body=body,
                        timeout=timeout)
    return _results(data)


def recent_news(company: str, *, max_results: int = TAVILY_MAX_RESULTS) -> list:
    """Recent news/launches/funding/announcements for a company."""
    return search(
        f"{company} funding OR launch OR announcement OR partnership news",
        max_results=max_results, topic="news",
    )
