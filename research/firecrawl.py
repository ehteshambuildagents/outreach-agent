"""Firecrawl provider — turn a company website into clean page content.

Responsibility: given a company URL, return the most useful pages of that site
as clean markdown (homepage, product, pricing, docs, blog, about, careers …).
Firecrawl handles JS rendering and returns markdown, so it is the preferred
website source for the orchestrator; when it is unavailable the orchestrator
falls back to the built-in fetcher.

Only the orchestrator imports this module. The key (FIRECRAWL_API_KEY) is read
from the environment and never logged.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from config.settings import (
    FIRECRAWL_MAP_LIMIT,
    FIRECRAWL_PAGE_CHARS,
    FIRECRAWL_SCRAPE_PAGES,
    PROVIDER_MAX_WORKERS,
)
from research.providers_common import get_key, request_json

log = logging.getLogger("research.firecrawl")

_API = "https://api.firecrawl.dev/v2"
_ENV = "FIRECRAWL_API_KEY"

# Path keywords that make a discovered URL worth scraping, roughly in priority
# order. A salesperson cares most about what they do, who they serve, how they
# price, and what's new.
_VALUABLE = (
    "product", "platform", "solution", "pricing", "about", "customer",
    "case-stud", "docs", "blog", "news", "team", "company", "features",
    "careers", "use-case",
)


def available() -> bool:
    return bool(get_key(_ENV))


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_key(_ENV)}",
            "Content-Type": "application/json"}


def _clean(text) -> str:
    return (text or "").strip()[:FIRECRAWL_PAGE_CHARS]


def scrape(url: str) -> dict:
    """Scrape ONE page to clean markdown. Returns {url, markdown, title} or None."""
    if not available():
        return None
    data = request_json(
        "POST", f"{_API}/scrape", provider="firecrawl", headers=_headers(),
        json_body={"url": url, "formats": ["markdown"], "onlyMainContent": True},
    )
    doc = (data or {}).get("data") or {}
    md = _clean(doc.get("markdown"))
    if not md:
        return None
    meta = doc.get("metadata") or {}
    return {"url": meta.get("sourceURL") or url,
            "markdown": md, "title": meta.get("title") or ""}


def map_site(url: str, limit: int = FIRECRAWL_MAP_LIMIT) -> list:
    """Discover the site's URLs (fast, no scraping). Returns a list of URLs."""
    if not available():
        return []
    data = request_json(
        "POST", f"{_API}/map", provider="firecrawl", headers=_headers(),
        json_body={"url": url, "limit": limit},
    )
    links = (data or {}).get("links") or []
    out = []
    for item in links:
        u = item.get("url") if isinstance(item, dict) else item
        if isinstance(u, str) and u.startswith("http"):
            out.append(u)
    return out


def _prioritize(home: str, urls: list, want: int) -> list:
    """Pick the most valuable same-site pages to scrape (homepage first)."""
    host = (urlparse(home).hostname or "").lower().lstrip("www.")
    same, seen = [], set()
    for u in urls:
        h = (urlparse(u).hostname or "").lower().lstrip("www.")
        if h != host or u in seen:
            continue
        seen.add(u)
        same.append(u)

    def rank(u: str) -> int:
        path = urlparse(u).path.lower()
        for i, kw in enumerate(_VALUABLE):
            if kw in path:
                return i
        return len(_VALUABLE) + path.count("/")   # shallow pages before deep ones

    ordered = sorted(same, key=rank)
    picked = [home] + [u for u in ordered if u.rstrip("/") != home.rstrip("/")]
    # de-dupe preserving order
    final, done = [], set()
    for u in picked:
        k = u.rstrip("/")
        if k not in done:
            done.add(k)
            final.append(u)
    return final[:want]


def crawl_site(url: str, max_pages: int = FIRECRAWL_SCRAPE_PAGES) -> list:
    """Return clean markdown for the most useful pages of a company's site.

    Maps the site, prioritizes valuable pages (product/pricing/about/blog/…),
    and scrapes up to ``max_pages`` of them. Falls back to scraping just the
    homepage if mapping returns nothing. List of {url, markdown, title}.
    """
    if not available():
        return []
    discovered = map_site(url)
    targets = _prioritize(url, discovered, max_pages) if discovered else [url]
    # Scrape the chosen pages concurrently (bounded) — the slow part is the
    # per-page render, and they're independent. The common layer retries any
    # transient 429/5xx, so a burst that trips a rate limit still recovers.
    with ThreadPoolExecutor(max_workers=min(len(targets) or 1,
                                            PROVIDER_MAX_WORKERS)) as pool:
        scraped = list(pool.map(scrape, targets))
    pages = [p for p in scraped if p and p.get("markdown")]
    log.info("firecrawl: scraped %d/%d page(s) for %s",
             len(pages), len(targets), url)
    return pages
