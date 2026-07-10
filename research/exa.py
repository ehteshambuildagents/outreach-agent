"""Exa provider — long-form and technical depth about a company or its people.

Responsibility: the content search engines bury — founder interviews, podcasts,
engineering blog posts, technical deep-dives, long-form writing. Exa's neural
search finds documents by meaning, and returns their text, which is exactly the
raw material for a genuinely unique personalization hook.

Key: EXA_API_KEY (server-side only, never logged).
"""

import logging

from config.settings import EXA_MAX_RESULTS, EXA_TEXT_CHARS
from research.providers_common import get_key, request_json

log = logging.getLogger("research.exa")

_API = "https://api.exa.ai/search"
_ENV = "EXA_API_KEY"


def available() -> bool:
    return bool(get_key(_ENV))


def _headers() -> dict:
    return {"x-api-key": get_key(_ENV), "Content-Type": "application/json"}


def _results(data) -> list:
    """Normalize Exa results to {url, title, content, published_date, author}."""
    out = []
    for r in (data or {}).get("results") or []:
        if not r.get("url"):
            continue
        text = (r.get("text") or "").strip()[:EXA_TEXT_CHARS]
        out.append({
            "url": r.get("url"),
            "title": r.get("title") or "",
            "content": text,
            "published_date": r.get("publishedDate") or None,
            "author": r.get("author") or None,
        })
    return out


def search(query: str, *, max_results: int = EXA_MAX_RESULTS,
           include_text: bool = True) -> list:
    """Run one Exa neural search. Returns a list of results (possibly empty).

    Uses type="auto" so Exa picks neural vs keyword per query. Never raises.
    """
    if not available():
        return []
    body = {
        "query": query,
        "numResults": max_results,
        "type": "auto",
    }
    if include_text:
        body["contents"] = {"text": {"maxCharacters": EXA_TEXT_CHARS}}
    data = request_json("POST", _API, provider="exa", headers=_headers(),
                        json_body=body)
    return _results(data)


def deep_content(company: str, *, max_results: int = EXA_MAX_RESULTS) -> list:
    """Long-form / founder / technical content about a company."""
    return search(
        f"{company} founder interview, engineering deep-dive, or long-form article",
        max_results=max_results,
    )
