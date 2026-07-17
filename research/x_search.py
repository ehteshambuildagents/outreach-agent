"""X (Twitter) recent-search provider — OPTIONAL recent-social-signal source.

Read-only App-only Bearer auth against ``GET /2/tweets/search/recent``. This is
the ONE research source with no free tier — it costs real money PER POST READ —
so it is deliberately NOT on the always-on research path:

  * it runs only when a request implies a need for RECENT SOCIAL signal
    (``wants_recent_social`` gates the decision to call it);
  * results are CACHED by exact (query, max_results) in Redis (the existing
    ``automation.redis`` infra) for ``X_SEARCH_CACHE_TTL_SECONDS`` — a repeated
    query never re-hits the paid API;
  * ``max_results`` is capped well under the API max (100);
  * every REAL (non-cached) call logs its read count + estimated cost — the same
    observability convention as the Firecrawl/Tavily providers — and records a
    durable telemetry cost event, so spend is visible and traceable.

Never raises: like the other providers it returns a status dict and degrades to
``unavailable`` when ``X_BEARER_TOKEN`` isn't set. Posting/write is intentionally
NOT here — Bearer-only, read-only (posting is a later phase, needs Consumer keys).
"""

import hashlib
import json
import logging

from config.settings import (
    X_SEARCH_CACHE_TTL_SECONDS,
    X_SEARCH_COST_PER_READ_USD,
    X_SEARCH_MAX_RESULTS,
)
from research.providers_common import get_key, request_json

log = logging.getLogger("research.x_search")

_API = "https://api.twitter.com/2/tweets/search/recent"
_ENV = "X_BEARER_TOKEN"
_API_MIN_RESULTS = 10           # the endpoint's own minimum
_CACHE_PREFIX = "x_search:"

# Phrases in a USER's request that mark a need for recent social signal (vs.
# ordinary company-website research). Deterministic, so the "don't run by
# default" rule can be enforced in code — not only in the tool prompt.
_SOCIAL_SIGNAL_HINTS = (
    # platform mentions (natural phrasing: "on X", "in X", "on Twitter", "in Twitter")
    "on x", "in x", "on twitter", "in twitter", "twitter",
    # post/tweet phrasing
    "tweet", "tweeted", "posted recently", "recently posted", "recent post",
    "recent posts", "posting about", "posted about", "post about", "posts about",
    "tweet about",
    # discussion / opinion phrasing
    "complaining about", "complained about", "talking about", "saying about",
    "what people are saying", "what they're saying",
    # signal / sentiment vocabulary
    "sentiment", "buzz", "chatter", "trending", "going viral", "viral", "mentions",
    "social signal", "social media",
)


def available() -> bool:
    """True only when a Bearer token is configured (else the source is skipped)."""
    return bool(get_key(_ENV))


def wants_recent_social(text: str) -> bool:
    """True when a request explicitly implies recent social signal — the ONLY
    case X search should run (it costs money, so it is never on by default). Use
    this against the USER'S request, not against the X search query itself."""
    low = (text or "").lower()
    return any(hint in low for hint in _SOCIAL_SIGNAL_HINTS)


def search_recent_posts(query: str, max_results: int = X_SEARCH_MAX_RESULTS) -> dict:
    """Search recent public X posts matching ``query``. Never raises; returns:

        {"status": "ok"|"empty"|"unavailable"|"error", "query", "posts",
         "count", "estimated_cost_usd", "cached", "reason"?}

    Cost-controlled: cached by exact (query, max_results), ``max_results`` capped,
    and every real read logged with its count + estimated cost.
    """
    query = (query or "").strip()
    if not query:
        return _result("error", query, reason="Empty search query.")
    if not available():
        return _result("unavailable", query,
                       reason="X search isn't configured (set X_BEARER_TOKEN).")

    n = _cap(max_results)
    key = _cache_key(query, n)

    cached = _cache_get(key)
    if cached is not None:
        # A cache hit costs $0 and never touches the paid API — the whole point.
        log.info("x_search: cache hit (0 reads, $0.0000) query=%r", query)
        cached["cached"] = True
        return cached

    params = {
        "query": query,
        "max_results": n,
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    data = request_json("GET", _API, provider="x_search", headers=_headers(),
                        params=params)
    if data is None:
        return _result("error", query, reason="X search request failed (see logs).")

    posts = _parse(data)
    reads = int(((data.get("meta") or {}).get("result_count")) or len(posts))
    est_cost = round(reads * X_SEARCH_COST_PER_READ_USD, 4)
    # Observability: read count + estimated cost, same convention as Firecrawl.
    log.info("x_search: %d read(s), est $%.4f, query=%r (cache miss)",
             reads, est_cost, query)
    _record_cost(query, reads, est_cost)

    result = _result("ok" if posts else "empty", query, posts=posts,
                     estimated_cost_usd=est_cost)
    if not posts:
        result["reason"] = "No recent public posts matched this query."
    # Cache even an empty result — so a fruitless query isn't paid for twice.
    _cache_put(key, result)
    return result


# ── Helpers ────────────────────────────────────────────────────────────────
def _result(status, query, *, posts=None, estimated_cost_usd=0.0, cached=False,
            reason=None) -> dict:
    posts = posts or []
    out = {"status": status, "query": query, "posts": posts, "count": len(posts),
           "estimated_cost_usd": estimated_cost_usd, "cached": cached}
    if reason:
        out["reason"] = reason
    return out


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_key(_ENV)}"}


def _cap(max_results) -> int:
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = X_SEARCH_MAX_RESULTS
    return max(_API_MIN_RESULTS, min(n, X_SEARCH_MAX_RESULTS))


def _cache_key(query: str, max_results: int) -> str:
    raw = f"{query.strip().lower()}|{max_results}"
    return _CACHE_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse(data: dict) -> list:
    users = {u.get("id"): u
             for u in ((data.get("includes") or {}).get("users") or [])}
    out = []
    for t in (data.get("data") or []):
        author = users.get(t.get("author_id")) or {}
        metrics = t.get("public_metrics") or {}
        username = author.get("username")
        tid = t.get("id")
        out.append({
            "id": tid,
            "text": t.get("text") or "",
            "created_at": t.get("created_at"),
            "lang": t.get("lang"),
            "author_username": username,
            "author_name": author.get("name"),
            "url": (f"https://x.com/{username}/status/{tid}"
                    if username and tid else None),
            "like_count": metrics.get("like_count"),
            "reply_count": metrics.get("reply_count"),
            "repost_count": metrics.get("retweet_count"),
        })
    return out


# ── Redis cache (existing infra: automation.redis — Upstash or in-memory) ──
def _cache_get(key: str):
    try:
        from automation import redis
        raw = redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - a cache miss must never break the call
        return None


def _cache_put(key: str, value: dict) -> None:
    try:
        from automation import redis
        redis.set(key, json.dumps(value), ex=X_SEARCH_CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - failing to cache is not fatal
        pass


def _record_cost(query: str, reads: int, est_cost: float) -> None:
    """Durable cost event (beyond the log line), so spend is queryable later."""
    try:
        from telemetry import record_event
        record_event("research", "x_search", value=est_cost,
                     entity_id=_cache_key(query, 0), detail=f"{reads} reads")
    except Exception:  # noqa: BLE001 - observability must never break the call
        pass
