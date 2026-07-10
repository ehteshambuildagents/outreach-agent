"""Company resolution: a company NAME -> its official website, via a search API.

Kept deliberately separate from the research engine (which still takes URLs
exactly as before). The provider is chosen from whichever API key is present:
TAVILY_API_KEY (preferred) or BRAVE_API_KEY. With neither set, ``search`` returns
None and the caller degrades gracefully (best-effort guess + ask the user).

``resolve_company_name`` returns one of:
    {"status": "resolved",    "url": str, "match": {...}}
    {"status": "choices",     "choices": [{"url","domain","title","description"}]}
    {"status": "none"}                       # searched, found no official site
    {"status": "no_provider"}                # no search key configured
    {"status": "error"}                      # the search request failed
It never raises for normal failures.
"""

import os
import re
from urllib.parse import urlparse

import requests

from config.settings import (
    COMPANY_SEARCH_MAX_RESULTS,
    COMPANY_SEARCH_TIMEOUT,
    EXCLUDED_RESOLUTION_DOMAINS,
    USER_AGENT,
)


# ── Provider dispatch ──────────────────────────────────────────────────
def provider() -> str:
    """Which search provider is configured (env keys), or None."""
    if os.environ.get("TAVILY_API_KEY", "").strip():
        return "tavily"
    if os.environ.get("BRAVE_API_KEY", "").strip():
        return "brave"
    return None


def search(query: str, max_results: int = COMPANY_SEARCH_MAX_RESULTS):
    """Run one web search. Returns a list of {url,title,description}, or None if
    no provider is configured. Raises requests exceptions on transport failure
    (the caller translates them)."""
    which = provider()
    if which == "tavily":
        return _search_tavily(query, max_results)
    if which == "brave":
        return _search_brave(query, max_results)
    return None


def _search_tavily(query: str, max_results: int) -> list:
    # Route through the centralized Tavily provider so every Tavily call lives in
    # one module (research/tavily.py) rather than being duplicated here.
    from research import tavily
    return [{"url": r["url"], "title": r.get("title", ""),
             "description": r.get("content", "")}
            for r in tavily.search(query, max_results=max_results,
                                   timeout=COMPANY_SEARCH_TIMEOUT)]


def _search_brave(query: str, max_results: int) -> list:
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"],
                 "Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=COMPANY_SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    results = ((data.get("web") or {}).get("results")) or []
    return [{"url": r.get("url"), "title": r.get("title", ""),
             "description": r.get("description", "")}
            for r in results if r.get("url")]


# ── Official-domain extraction ─────────────────────────────────────────
def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _registrable(host: str) -> str:
    """Crude registrable domain: the last two labels (good enough to dedupe
    stripe.com vs stripe.com/pricing and to compare candidates)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _excluded(host: str) -> bool:
    return any(host == d or host.endswith("." + d)
               for d in EXCLUDED_RESOLUTION_DOMAINS)


def _candidates_from_results(results: list) -> list:
    """Distinct, non-excluded official-site candidates, preserving search rank."""
    seen, candidates = set(), []
    for item in results or []:
        host = _host(item.get("url") or "")
        if not host or _excluded(host):
            continue
        reg = _registrable(host)
        if reg in seen:
            continue
        seen.add(reg)
        candidates.append({
            "url": f"https://{host}", "domain": reg,
            "title": item.get("title", ""), "description": item.get("description", ""),
        })
    return candidates


def resolve_company_name(name: str) -> dict:
    """Resolve a company name to its official website (see module docstring)."""
    name = (name or "").strip()
    if not name:
        return {"status": "none"}
    try:
        results = search(f"{name} official website")
    except Exception:  # noqa: BLE001 - transport/timeout/HTTP error
        return {"status": "error"}
    if results is None:
        return {"status": "no_provider"}

    candidates = _candidates_from_results(results)
    if not candidates:
        return {"status": "none"}

    # A candidate whose domain core (label before the TLD) contains the company
    # slug is a STRONG match (stripe -> stripe.com, notion -> notion.so). One
    # strong match => confident. Several strong OR several plausible => ask.
    slug = _norm(name)
    strong = [c for c in candidates
              if slug and slug in _norm(c["domain"].split(".")[0])]
    if len(strong) == 1:
        return {"status": "resolved", "url": strong[0]["url"], "match": strong[0]}
    pool = strong if len(strong) > 1 else candidates
    if len(pool) == 1:
        return {"status": "resolved", "url": pool[0]["url"], "match": pool[0]}
    return {"status": "choices", "choices": pool[:3]}
