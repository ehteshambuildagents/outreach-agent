"""Orchestrator: the single entry point for multi-source company research.

The rest of the application talks ONLY to this module — never to a provider
directly. It decides which providers a request actually needs, runs the
independent ones concurrently, then hands everything to the synthesis step
(Anthropic) which de-duplicates, ranks, and grounds the result with citations.

    intents  ->  gather (concurrent, cached)  ->  synthesize (Anthropic)  ->  result

Design guarantees:
  * Intent-driven: a request only calls the providers it needs, so normal
    conversation triggers nothing and "what launched recently" hits search first.
  * Concurrent: website / news / long-form gathers run in parallel.
  * Cached: a company's gathered evidence is reused for INTEL_CACHE_TTL_SECONDS
    so repeated asks in one session don't re-hit the providers.
  * Graceful: a provider that is unconfigured or fails is skipped; the others
    still produce a result. Only if EVERY source yields nothing do we report empty.
  * Secret-safe: keys live in provider modules (env only); nothing here logs them.
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from config.settings import (
    INTEL_CACHE_TTL_SECONDS,
    INTEL_MAX_EVIDENCE_CHARS,
    PROVIDER_MAX_WORKERS,
)
from research import exa, firecrawl, jina, tavily
from research.fetcher import fetch_static, validate_url
from research.cleaner import clean_html_text
from research.synthesis import synthesize
from services import claude_client

log = logging.getLogger("research.orchestrator")

# Intents — what KIND of information a request needs. The agent (or research())
# maps a user request onto a set of these; only the chosen providers run.
WEBSITE = "website"      # the company's own site (Firecrawl / fetch + Jina)
NEWS = "news"            # recent launches / funding / announcements (Tavily)
DEEP = "deep"            # founder / long-form / technical content (Exa)
ALL_INTENTS = (WEBSITE, NEWS, DEEP)


def provider_status() -> dict:
    """Which providers are configured (keys present). Safe to expose — booleans
    only, never the keys themselves."""
    return {
        "anthropic": _anthropic_ready(),
        "firecrawl": firecrawl.available(),
        "tavily": tavily.available(),
        "exa": exa.available(),
        "jina": jina.available(),
    }


def _anthropic_ready() -> bool:
    try:
        from config.settings import get_api_key
        return bool(get_api_key())
    except Exception:
        return False


def intents_for(focus: str) -> tuple:
    """Map a short free-text focus onto the intents it needs.

    Mirrors the product's tool-choice examples:
      * generic "what is X" / "summarize"      -> website (+ light news)
      * "recent / launch / news / funding"     -> news first (+ website)
      * "founder / interview / technical / hook"-> deep (+ website)
      * "everything / unique hook / full"       -> all sources
    Website is almost always included because it grounds everything else.
    """
    f = (focus or "").lower()
    intents = {WEBSITE}
    if re.search(r"\b(news|recent|latest|launch|funding|raised|announce|update|"
                 r"partnership|acquir)\w*", f):
        intents.add(NEWS)
    if re.search(r"\b(founder|ceo|interview|podcast|engineer|technical|architecture|"
                 r"long.?form|deep|blog|hook|angle|unique|personal)\w*", f):
        intents.add(DEEP)
    if re.search(r"\b(everything|all|full|comprehensive|thorough|complete)\b", f):
        intents.update((NEWS, DEEP))
    # A bare "research X" with no qualifier still benefits from a recency check.
    if intents == {WEBSITE}:
        intents.add(NEWS)
    return tuple(i for i in ALL_INTENTS if i in intents)


# ── Cache (per-process, TTL) ───────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()


def _cache_key(company, url, intents):
    base = (url or company or "").strip().lower().rstrip("/")
    return base + "|" + ",".join(sorted(intents))


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry[0] < INTEL_CACHE_TTL_SECONDS:
            return entry[1]
        if entry:
            del _cache[key]
    return None


def _cache_put(key, value):
    with _cache_lock:
        _cache[key] = (time.time(), value)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ── Gathering (concurrent) ─────────────────────────────────────────────
def _gather_website(company, url):
    """Company's own site as clean text. Firecrawl preferred; else fetch + Jina."""
    items = []
    if url and firecrawl.available():
        for p in firecrawl.crawl_site(url):
            items.append(_ev("firecrawl", "website", p["url"], p.get("title"),
                             p["markdown"]))
    if not items and url:
        # Fallback path: static fetch, cleaned by Jina if available else our cleaner.
        cleaned = jina.clean_url(url) if jina.available() else ""
        if not cleaned:
            ok, html = fetch_static(url)
            cleaned = clean_html_text(html) if ok else ""
        if cleaned:
            items.append(_ev("jina" if jina.available() else "fetch",
                             "website", url, company, cleaned))
    return items


def _gather_news(company, url):
    items = []
    for r in tavily.recent_news(company):
        items.append(_ev("tavily", "news", r["url"], r.get("title"),
                         r.get("content"), r.get("published_date")))
    return items


def _gather_deep(company, url):
    items = []
    for r in exa.deep_content(company):
        items.append(_ev("exa", "deep", r["url"], r.get("title"),
                         r.get("content"), r.get("published_date")))
    return items


# intent -> gatherer function NAME. Resolved via module globals at call time so
# tests can patch the individual _gather_* functions and gather() honours it.
_GATHERERS = {WEBSITE: "_gather_website", NEWS: "_gather_news", DEEP: "_gather_deep"}


def _ev(provider, kind, url, title, text, published_date=None):
    return {"provider": provider, "kind": kind, "url": url,
            "title": (title or "").strip(), "text": (text or "").strip(),
            "published_date": published_date}


def gather(company, url=None, intents=ALL_INTENTS):
    """Run the chosen providers CONCURRENTLY and return a list of evidence items.

    Never raises; a failing/unconfigured provider simply contributes nothing.
    """
    chosen = [i for i in intents if i in _GATHERERS]
    results = []
    with ThreadPoolExecutor(max_workers=min(len(chosen) or 1,
                                            PROVIDER_MAX_WORKERS)) as pool:
        futures = {pool.submit(globals()[_GATHERERS[i]], company, url): i
                   for i in chosen}
        for fut in futures:
            try:
                results.extend(fut.result())
            except Exception:  # noqa: BLE001 - one provider failing must not abort
                log.info("gather: %s provider raised; skipping", futures[fut])
    return results


# ── Public: research (gather + synthesize) ─────────────────────────────
def research(company, url=None, focus="", *, intents=None):
    """Multi-source research for one company. Single entry point for the app.

    Returns:
        {status: "ok"|"empty"|"error", company, summary, findings[], hooks[],
         sources[], providers_used[], providers_missing[]}
    Never raises for normal failures.
    """
    company = (company or _company_from_url(url) or "").strip()
    if not company and not url:
        return {"status": "error", "error": "No company or URL provided."}
    if url:
        ok, reason = validate_url(url)
        if not ok:
            url = None                     # bad/unsafe URL -> search-only research

    chosen = tuple(intents) if intents else intents_for(focus)
    key = _cache_key(company, url, chosen)
    cached = _cache_get(key)
    if cached is not None:
        log.info("intel cache hit: %s", key)
        return cached

    evidence = gather(company, url, chosen)
    providers_used = sorted({e["provider"] for e in evidence})
    status = provider_status()
    missing = [p for p in ("firecrawl", "tavily", "exa", "jina")
               if not status[p]]

    if not evidence:
        result = {"status": "empty", "company": company, "summary": "",
                  "findings": [], "hooks": [], "sources": [],
                  "providers_used": [], "providers_missing": missing}
        _cache_put(key, result)
        return result

    try:
        synth = synthesize(company, _evidence_blocks(evidence), focus)
    except claude_client.ClaudeClientError as exc:
        return {"status": "error", "error": str(exc), "company": company}
    except Exception:  # noqa: BLE001
        return {"status": "error", "company": company,
                "error": "Could not analyse the gathered research just now."}

    result = {
        "status": "ok",
        "company": company,
        "summary": synth.get("summary", ""),
        "findings": synth.get("findings", []),
        "hooks": synth.get("hooks", []),
        "sources": _sources(evidence),
        "providers_used": providers_used,
        "providers_missing": missing,
        "intents": list(chosen),
    }
    _cache_put(key, result)
    log.info("intel ok: %s (%d findings, providers=%s)",
             company, len(result["findings"]), providers_used)
    return result


# ── Helpers ────────────────────────────────────────────────────────────
def _company_from_url(url):
    host = (urlparse(url or "").hostname or "").lower().lstrip("www.")
    return host.split(".")[0] if host else ""


def _evidence_blocks(evidence) -> str:
    """Combine evidence into labelled blocks for the synthesis model, capped."""
    blocks, total = [], 0
    for e in evidence:
        header = (f"[SOURCE: {e['url']} | via {e['provider']} | {e['kind']}"
                  + (f" | {e['published_date']}" if e.get("published_date") else "")
                  + "]")
        body = e["title"] + ("\n" if e["title"] else "") + e["text"]
        block = f"{header}\n{body}"
        if total + len(block) > INTEL_MAX_EVIDENCE_CHARS:
            block = block[: max(0, INTEL_MAX_EVIDENCE_CHARS - total)]
        blocks.append(block)
        total += len(block)
        if total >= INTEL_MAX_EVIDENCE_CHARS:
            break
    return "\n\n".join(blocks)


def _sources(evidence) -> list:
    """Distinct sources, de-duplicated by URL, preserving order."""
    seen, out = set(), []
    for e in evidence:
        u = e["url"]
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "provider": e["provider"],
                    "title": e["title"], "kind": e["kind"]})
    return out
