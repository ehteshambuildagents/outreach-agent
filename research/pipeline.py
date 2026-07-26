"""Pipeline: adaptive, goal-driven research of one company.

Research toward THREE INDEPENDENT goals — completing one never satisfies another
(a previous benchmark proved a single conflated "sufficient" gate let a homepage
stop research before a human was ever found):
  * Goal 1 — understand the company (what/who/positioning/business model),
  * Goal 2 — find a recent buying signal (recent focus, traction, named customer),
  * Goal 3 — find a HUMAN (a named founder / decision-maker).

Deterministic stopping (``_stop_decision``): the crawler stops ONLY when
  CASE 1  all three goals are met, or
  CASE 2  the company is understood + a recent signal exists AND every
          person-source page (about/team/leadership/press/…) has been
          exhausted — then it concludes, explicitly, "No suitable public
          decision maker found." (page budget is the hard safety cap.)

Pages are visited in small BATCHES, highest-value first; once only the human
goal is missing, the crawler hunts person-source pages EXCLUSIVELY so it never
spends budget on product/pricing/docs that can't name anyone. A dedicated
name-only pass runs over the crawled person pages if ordinary extraction found
nobody (``find_founder`` forces it even when no person page was reachable).

This only changes the CRAWL STRATEGY. Every reliability component is preserved:
retries, sitemap/robots discovery, evidence grounding, confidence scoring,
structured extraction, inference fields, and logging.

A model call has a real fixed latency floor no matter how small its input is,
so two things keep that floor from being paid once per page:
  * each extraction call covers only the CURRENT page(s) — never the whole
    growing corpus — so the model never has to re-read/re-emit facts from
    pages it already saw on an earlier call;
  * after the homepage (checked alone, so a confident site can stop after one
    page and one call), remaining pages are fetched in parallel and extracted
    together in small batches (PAGE_BATCH_SIZE), trading a little stopping
    granularity for far fewer sequential model round-trips.
Scoring itself (verifier + hooks) is pure Python — cheap to re-run after every
batch, since it makes no model call at all.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from config.settings import (
    DIMINISHING_DELTA,
    DIMINISHING_STALLS,
    JS_RENDER_TEXT_THRESHOLD,
    MAX_PARALLEL_FETCHES,
    MAX_TEXT_CHARS,
    MIN_USABLE_TEXT_CHARS,
    NAME_SEARCH_RETRIES,
    PAGE_BATCH_SIZE,
    PAGE_BUDGET_LARGE,
    PAGE_BUDGET_MEDIUM,
    PAGE_BUDGET_SMALL,
    RESEARCH_SCORE_THRESHOLD,
    SITE_MEDIUM_MAX_CANDIDATES,
    SITE_SMALL_MAX_CANDIDATES,
)
from research.classifier import classify_page
from research.cleaner import clean_html_text
from research.crawler import (
    discover_from_sitemap,
    is_people_page,
    is_person_source_page,
    ordered_candidates,
)
from research.extractor import extract_evidence, extract_names_only
from research.fetcher import (
    RenderFetcher,
    fetch_static,
    fetch_with_fallbacks,
    validate_url,
)
from research.hooks import backfill_facts_from_hooks, rank_hooks, research_score
from research import exa, firecrawl, gaps, jina, source_planner, tavily
from research.verifier import select_primary_contact, verify
from services.claude_client import ClaudeClientError

log = logging.getLogger("research.pipeline")


def research_company(url: str, *, find_founder: bool = False) -> dict:
    """Research a company end-to-end. Never raises for normal failures.

    find_founder: when True, run the focused founder/team name-hunt (extra
    Claude calls) if the ordinary extraction found no company person. Off by
    default because it is a real latency cost on every founder-less site; a
    founder plainly stated on a page is still surfaced without it.
    """
    ok, reason = validate_url(url)
    if not ok:
        log.info("invalid url %s: %s", url, reason)
        return {"status": "error", "error": reason}

    log.info("research start: %s (find_founder=%s)", url, find_founder)
    sitemap_subs = discover_from_sitemap(url, fetch_static)
    if sitemap_subs:
        log.info("sitemap: %d relevant urls for %s", len(sitemap_subs), url)

    render_fetcher = RenderFetcher()
    try:
        return _adaptive_research(url, render_fetcher, sitemap_subs, find_founder)
    finally:
        render_fetcher.close()


# ──────────────────────────────────────────────────────────────────────
#  Adaptive crawl loop
# ──────────────────────────────────────────────────────────────────────
def _adaptive_research(url, render_fetcher, sitemap_subs, find_founder=False) -> dict:
    # The homepage is worth the paid tail of the fetch chain: without it there is
    # no run at all.
    ok, html, method = _fetch_page(url, render_fetcher, prefer_render=False,
                                   allow_paid=True)
    blocked_note = ""
    if not ok:
        # The site refuses automated traffic. That is NOT the end of the research:
        # what a company says about itself is only one source, and the public web
        # still has plenty. Fall back to it and SAY SO, rather than reporting a
        # bare failure the user cannot act on.
        log.info("homepage unreachable: %s (%s) - falling back to public sources", url, html)
        public_pages = _public_source_pages(url)
        if not public_pages:
            return {"status": "error",
                    "error": _unreachable_reason(url, html),
                    "site_unreachable": True}
        host = (urlparse(url).hostname or url).lstrip("www.")
        blocked_note = (f"{host} blocked automated crawling, so this is based on "
                        "public sources and recent coverage rather than their own site.")
        log.info("public-source fallback for %s: %d pages", url, len(public_pages))
        return _research_from_public_sources(url, public_pages, blocked_note)

    pages = [(url, clean_html_text(html))]
    contact_routes = _extract_contact_routes(html, url)
    used_render = method == "rendered"
    candidates = ordered_candidates(url, html, sitemap_subs)
    budget = _page_budget(len(candidates))
    log.info("crawl %s: %d candidate pages, budget %d", url, len(candidates), budget)

    try:
        raw_accum = _extract_pages_raw(pages)
        graph, hooks, score, breakdown = _score_from_raw(raw_accum, pages)
    except (ClaudeClientError, RuntimeError) as exc:
        return {"status": "error", "error": str(exc)}
    except Exception:
        return {"status": "error", "error": "Unexpected error during analysis."}

    remaining = list(candidates)
    # Facts the evidence ledger cannot read off the graph (hiring/funding come
    # from Apollo/Tavily, not the site). Empty here; the discovery side supplies
    # them when it already knows.
    ledger_signals = {}

    def _person_sources_left():
        # PERF: only GENUINE people pages (about/team/leadership/people) can name a
        # decision-maker — blog/news/careers/customers rarely do and cost a full
        # LLM extraction each. Restricting the person goal to people pages makes a
        # site that exposes none abandon the hunt immediately (CASE 2) instead of
        # exhausting the page budget — the dominant cost in the prior benchmark.
        return len(pages) < budget and any(is_person_source_page(u) for u in remaining)

    stop_reason = _stop_decision(graph, _person_sources_left())
    if stop_reason is None:
        stall, prev = 0, score
        while remaining:
            if len(pages) >= budget:
                stop_reason = f"page budget of {budget} reached (score {score})"
                break
            understood, recent, person = _goals(graph)
            # If the company is understood + has a recent signal and the ONLY
            # missing goal is a HUMAN, hunt genuine people pages exclusively — do
            # not spend budget on product/pricing/blog/careers that never name one.
            person_hunt = understood and recent and not person
            pool = ([u for u in remaining if is_person_source_page(u)]
                    if person_hunt else remaining)
            if person_hunt and not pool:
                stop_reason = ("company understood + recent signal; person sources "
                               "exhausted — No suitable public decision maker found.")
                break
            # EVIDENCE FIRST: crawl the page that closes the biggest remaining gap,
            # rather than whatever the static keyword priority happened to rank
            # highest. On a nav-heavy homepage (apple.com) the static order spends
            # the budget on whatever was linked; this aims at what is missing.
            if not person_hunt:
                pool = _gap_ordered(pool, graph, ledger_signals)
            batch_urls = pool[:min(PAGE_BATCH_SIZE, budget - len(pages))]
            for u in batch_urls:
                remaining.remove(u)

            fast_results = _fetch_static_batch(batch_urls)
            batch_pages, used_urls = [], []
            for sub in batch_urls:
                ok, sub_html, m = _fetch_page(
                    sub, render_fetcher, prefer_render=is_people_page(sub),
                    pre_fetched=fast_results.get(sub))
                if not ok:
                    log.info("skip %s (%s)", sub, sub_html)
                    continue
                _merge_contact_routes(contact_routes, _extract_contact_routes(sub_html, sub))
                text = clean_html_text(sub_html)
                if not text.strip():
                    log.info("skip %s (empty)", sub)
                    continue
                used_render = used_render or (m == "rendered")
                batch_pages.append((sub, text))
                used_urls.append(sub)
            if not batch_pages:
                continue                       # whole batch failed/empty -> try the next

            pages.extend(batch_pages)
            try:
                _merge_raw(raw_accum, _extract_pages_raw(batch_pages))
                graph, hooks, score, breakdown = _score_from_raw(raw_accum, pages)
            except Exception:
                log.info("extract failed adding %s; keeping prior result", used_urls)
                del pages[-len(batch_pages):]
                break
            delta = score - prev
            prev = score
            log.info("visited %s -> score %d (%+d)", ", ".join(used_urls), score, delta)

            stop_reason = _stop_decision(graph, _person_sources_left())
            if stop_reason:
                break
            # Diminishing-returns applies ONLY while still establishing the
            # company/recent goals — NEVER while actively hunting a person (score
            # may not climb as we scan people pages, but the goal isn't resolved).
            understood, recent, person = _goals(graph)
            if not (understood and recent and not person):
                if delta < DIMINISHING_DELTA:
                    stall += 1
                    if stall >= DIMINISHING_STALLS:
                        # ...unless the evidence ledger says we are still short AND
                        # an uncrawled page targets a specific gap. A flat score
                        # across two generic pages is not proof that the pricing
                        # page would have added nothing.
                        if _worth_continuing(graph, ledger_signals, remaining):
                            stall = 0
                            log.info("stall ignored: confidence still low and a "
                                     "gap-filling page remains")
                            continue
                        stop_reason = (f"no new evidence (score {score}; last {stall} "
                                       f"checkpoint(s) added < {DIMINISHING_DELTA})")
                        break
                else:
                    stall = 0
        if stop_reason is None:
            stop_reason = f"crawled all {len(pages)} relevant pages (score {score})"

    # ── Dedicated person-discovery pass (Goal 3 is first-class). If we crawled
    #    a genuine people page but ordinary extraction named nobody, run ONE
    #    focused name-only pass over those pages. Evidence-grounded — never
    #    invents. find_founder still forces it even without a people page.
    person_pages = [(u, t) for u, t in pages if is_person_source_page(u)]
    if not _person_found(graph) and (person_pages or find_founder):
        hunt_pages = person_pages or pages
        try:
            members = extract_names_only(_combine(hunt_pages),
                                         retries=NAME_SEARCH_RETRIES)
            if members:
                raw_accum.setdefault("team_members", []).extend(members)
                graph, hooks, score, breakdown = _score_from_raw(raw_accum, pages)
        except Exception:
            pass

    if not _person_found(graph):
        provider_pages = _provider_person_pages(url, graph.value("company_name"))
        if provider_pages:
            try:
                members = extract_names_only(_combine(provider_pages),
                                             retries=NAME_SEARCH_RETRIES)
                if members:
                    raw_accum.setdefault("team_members", []).extend(members)
                    pages.extend(provider_pages)
                    graph, hooks, score, breakdown = _score_from_raw(raw_accum, pages)
            except Exception:
                pass

    log.info("STOP %s: %s", url, stop_reason)
    return _finalize(url, pages, graph, hooks, score, breakdown,
                     used_render, stop_reason, contact_routes)


def _finalize(url, pages, graph, hooks, score, breakdown, used_render, stop_reason,
              contact_routes=None):
    pages_urls = [u for u, _ in pages]
    page_types = {u: classify_page(u, t, is_home=(u == url)) for u, t in pages}
    output = _build_output(graph, hooks, score, breakdown, pages_urls, page_types)
    output["fetch_method"] = "rendered" if used_render else "fast"
    output["stop_reason"] = stop_reason
    # The evidence LEDGER behind the run: how confident, and what is still
    # missing. Deliberately NOT "evidence", which is already the per-field
    # provenance map that consumers read facts out of.
    try:
        output["evidence_ledger"] = gaps.assess(graph).public()
    except Exception:  # noqa: BLE001 - reporting must never break a finished run
        output["evidence_ledger"] = {}

    # ── Person-discovery report (Goal 3): the person phase always runs, so this
    #    is always completed; when nobody is found, say WHY and WHERE we looked.
    data = output["data"]
    _attach_contact_routes(data, pages_urls, contact_routes or {})
    # Post-crawl source planner: decide, from what we now know about THIS prospect,
    # whether Apollo / news / X would genuinely help — and run only those.
    output["source_plan"] = source_planner.run(data, url)
    person_sources = [u for u in pages_urls if is_person_source_page(u)]
    person_found = bool(data.get("founder_name") or data.get("team_members"))
    data["person_found"] = person_found
    data["person_search_completed"] = True
    data["person_sources_checked"] = person_sources
    data["person_not_found_reason"] = (
        None if person_found else _person_not_found_reason(person_sources))

    total_text = sum(len(t.strip()) for _, t in pages)
    if total_text < MIN_USABLE_TEXT_CHARS:
        output["status"] = "skip"
        output["reason"] = "The page(s) had too little readable text to research."
    elif score < RESEARCH_SCORE_THRESHOLD:
        output["status"] = "skip"
        output["reason"] = _low_confidence_reason(score, breakdown, len(pages_urls))
    else:
        output["status"] = "ok"
    log.info("research done: %s status=%s score=%s method=%s pages=%d",
             url, output["status"], score, output["fetch_method"], len(pages_urls))
    return output


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_HREF_RE = re.compile(r"""(?is)<a\b[^>]*\bhref=["']([^"']+)["']""")
_BAD_EMAIL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _extract_contact_routes(html: str, page_url: str) -> dict:
    """Deterministic public contact routes from fetched page HTML."""
    routes = {"emails": [], "linkedin_urls": []}
    for email in _EMAIL_RE.findall(html or ""):
        email = email.strip().strip(".,;:()[]{}<>").lower()
        if email.endswith(_BAD_EMAIL_EXTENSIONS):
            continue
        if email not in routes["emails"]:
            routes["emails"].append(email)
    for href in _HREF_RE.findall(html or ""):
        href = href.strip()
        if "linkedin.com/" not in href.lower():
            continue
        if href not in routes["linkedin_urls"]:
            routes["linkedin_urls"].append(href)
    if "contact" in (urlparse(page_url).path or "").lower():
        routes["contact_page_url"] = page_url
    return routes


def _merge_contact_routes(accum: dict, new: dict) -> dict:
    for key in ("emails", "linkedin_urls"):
        accum.setdefault(key, [])
        for value in new.get(key) or []:
            if value not in accum[key]:
                accum[key].append(value)
    if new.get("contact_page_url") and not accum.get("contact_page_url"):
        accum["contact_page_url"] = new["contact_page_url"]
    return accum


def _attach_contact_routes(data: dict, pages_urls: list, routes: dict) -> None:
    emails = [e for e in (routes.get("emails") or []) if e]
    linkedins = [u for u in (routes.get("linkedin_urls") or []) if u]
    contact_page = routes.get("contact_page_url") or next(
        (u for u in pages_urls if "contact" in (urlparse(u).path or "").lower()),
        None,
    )
    data["public_contact_email"] = emails[0] if emails else None
    data["public_contact_emails"] = emails[:5]
    data["linkedin_url"] = linkedins[0] if linkedins else None
    data["linkedin_urls"] = linkedins[:5]
    data["contact_page_url"] = contact_page
    data["recipient_route"] = (
        data.get("primary_contact_name")
        or data.get("founder_name")
        or data.get("public_contact_email")
        or data.get("linkedin_url")
        or data.get("contact_page_url")
    )


def _public_source_pages(url: str, limit: int = 8) -> list:
    """What the public web says about this company, as (url, text) pages.

    Used when the company's own site cannot be fetched. Tavily brings recent
    coverage and funding/launch news, Exa brings semantically related pages; Jina
    is then used to read the most promising result properly, because a search
    snippet alone is usually too thin to extract anything from.
    """
    parsed = urlparse(url)
    domain = (parsed.hostname or "").lower().lstrip("www.")
    company = domain.split(".")[0] if domain else ""
    if not company:
        return []

    results = []
    for query in (f"{company} company what they do product",
                  f"{company} news funding launch announcement"):
        try:
            results.extend(tavily.search(query, max_results=4))
        except Exception:  # noqa: BLE001 - one provider must never end the fallback
            log.info("tavily public-source lookup failed for %s", company, exc_info=True)
    try:
        results.extend(exa.search(f"{company} company overview product customers",
                                  max_results=5, include_text=True))
    except Exception:  # noqa: BLE001
        log.info("exa public-source lookup failed for %s", company, exc_info=True)

    pages, seen = [], set()
    for item in results:
        source = str((item or {}).get("url") or "").strip()
        valid, _ = validate_url(source)
        if not valid:
            continue
        key = source.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        text = " ".join(str((item or {}).get(k) or "").strip()
                        for k in ("title", "content") if (item or {}).get(k))
        if len(text.strip()) < 80:
            continue
        pages.append((source, text[:4000]))
        if len(pages) >= limit:
            break
    return pages


def _research_from_public_sources(url: str, pages: list, note: str) -> dict:
    """Run the normal extraction/scoring over public sources instead of the
    company's own pages. Same output shape, with the caveat carried through so
    every consumer can see the evidence is second-hand."""
    try:
        raw_accum = _extract_pages_raw(pages)
        graph, hooks, score, breakdown = _score_from_raw(raw_accum, pages)
    except (ClaudeClientError, RuntimeError) as exc:
        return {"status": "error", "error": str(exc), "site_unreachable": True}
    except Exception:  # noqa: BLE001
        return {"status": "error", "error": "Unexpected error during analysis.",
                "site_unreachable": True}

    out = _finalize(url, pages, graph, hooks, score, breakdown, False,
                    "site unreachable; researched from public sources", [])
    out["site_unreachable"] = True
    out["evidence_note"] = note
    # Never let the generic low-confidence copy hide WHY the evidence is thin.
    if out.get("status") == "skip":
        out["reason"] = note + " " + str(out.get("reason") or "")
    return out


def _unreachable_reason(url: str, error: str) -> str:
    """A failure the user can act on: what was tried, and what it means."""
    host = (urlparse(url).hostname or url).lstrip("www.")
    detail = str(error or "").strip().rstrip(".")
    tried = "direct fetch, a headless browser"
    if firecrawl.available():
        tried += ", Firecrawl"
    if jina.available():
        tried += ", Jina Reader"
    return (f"{host} could not be read ({detail}). Tried {tried}, and no public "
            "sources had enough about them either. If the site is behind a login "
            "or blocks automation, paste a page's text and I can work from that.")


def _provider_person_pages(url: str, company_name: str = None) -> list:
    """Existing-provider fallback for missing people only."""
    parsed = urlparse(url)
    domain = (parsed.hostname or "").lower().lstrip("www.")
    company = (company_name or domain.split(".")[0]).strip()
    if not company:
        return []
    queries = [
        f'"{company}" founder OR CEO OR owner OR "leadership"',
        f'"{domain}" founder CEO team leadership contact LinkedIn',
    ]
    results = []
    try:
        for query in queries:
            results.extend(tavily.search(query, max_results=4))
    except Exception:
        pass
    try:
        results.extend(exa.search(
            f"{company} founder CEO team leadership",
            max_results=4,
            include_text=True,
        ))
    except Exception:
        pass
    pages, seen = [], set()
    for item in results:
        source = str((item or {}).get("url") or "").strip()
        ok, _ = validate_url(source)
        if not ok:
            continue
        key = source.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        text = " ".join(str((item or {}).get(k) or "").strip()
                        for k in ("title", "content", "author") if (item or {}).get(k))
        if len(text.strip()) < 20:
            continue
        pages.append((source, text[:3000]))
        if len(pages) >= 6:
            break
    return pages


# ──────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────
def _fetch_page(url, render_fetcher, prefer_render, pre_fetched=None,
                allow_paid=False):
    """Fetch one page fast (requests); escalate to a browser only if the page
    is a people page or looks JS-thin. Returns (ok, html_or_error, method).

    `pre_fetched` lets a batch pass in an already-fetched (ok, html) pair (the
    fast/requests step ran in parallel across the batch); only the render
    escalation — rare, and Playwright is not thread-safe — happens here.

    `allow_paid` adds the Firecrawl/Jina tail of the chain, used for the homepage
    where the alternative is abandoning the entire run."""
    # fetch_static is passed explicitly (not looked up inside the fetcher) so this
    # module stays the single seam the crawl tests substitute.
    return fetch_with_fallbacks(
        url, render_fetcher=render_fetcher, prefer_render=prefer_render,
        pre_fetched=pre_fetched, allow_paid=allow_paid, fetch_fn=fetch_static)


def _fetch_static_batch(urls):
    """Fetch several URLs' fast (requests) response in parallel. url -> (ok, html)."""
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(urls), MAX_PARALLEL_FETCHES)) as pool:
        results = list(pool.map(fetch_static, urls))
    return dict(zip(urls, results))


def _extract_pages_raw(pages_subset):
    """Extract evidence from a SMALL batch of pages (often just one — always
    the homepage alone) in a single call. Bounded and roughly constant-cost no
    matter how many pages have already been crawled overall, since only the
    current batch — never the whole growing corpus — is sent to the model.
    name_retries=0: the focused name hunt runs once at the very end over
    people-pages only, not on every batch."""
    return extract_evidence(_combine(pages_subset), name_retries=0)


def _merge_raw(accum, new_raw):
    """Merge one page's raw evidence dict into the accumulated raw evidence."""
    for key, items in new_raw.items():
        accum.setdefault(key, []).extend(items or [])
    return accum


def _score_from_raw(raw, pages):
    """Ground + rank + score the evidence ACCUMULATED so far. Pure Python (no
    model call), so re-running it after every page costs nothing extra."""
    pages_dict = {u: t for u, t in pages}
    graph, raw_hooks = verify(raw, pages_dict)
    hooks = rank_hooks(graph, raw_hooks, pages_dict)
    backfill_facts_from_hooks(graph, hooks)
    score, breakdown = research_score(graph)
    return graph, hooks, score, breakdown


def _page_budget(num_candidates: int) -> int:
    if num_candidates <= SITE_SMALL_MAX_CANDIDATES:
        return PAGE_BUDGET_SMALL
    if num_candidates <= SITE_MEDIUM_MAX_CANDIDATES:
        return PAGE_BUDGET_MEDIUM
    return PAGE_BUDGET_LARGE


def _person_found(graph) -> bool:
    """Goal 3: a named human (founder or any grounded team member)."""
    return bool(graph.value("founder_name")) or bool(graph.team)


def _company_understood(graph) -> bool:
    """Goal 1: what they do + who they serve + positioning + business model.
    (Independent of a person or a recent signal — company info alone.)"""
    return bool(
        graph.value("what_they_do")
        and (graph.value("target_customer") or graph.values("industries_served"))
        and (graph.value("product_category")
             or graph.value("competitive_positioning")
             or graph.values("product_differentiators"))
        and graph.value("business_model"))


def _recent_signal(graph) -> bool:
    """Goal 2: a recent buying signal — recent focus/launch, traction metric, or
    a named customer. (Independent of understanding the company or a person.)"""
    return bool(graph.value("recent_focus")
                or graph.value("metrics_or_traction")
                or graph.values("notable_customers"))


def _gap_ordered(pool, graph, signals):
    """Move pages that serve a MISSING evidence slot ahead of ones that serve a
    slot already filled.

    Deliberately STABLE: within the promoted group and the rest, the existing
    crawl order is preserved. That ordering is tuned (people and proof pages
    first, bulk sections capped) and re-sorting it wholesale would trade one
    known-good heuristic for an untested one. This only demotes pages whose
    evidence we already have, which is the part the static order cannot know.
    """
    try:
        ledger = gaps.assess(graph, signals)
        if not ledger.missing:
            return pool
        hints = {h for name in ledger.missing
                 for h in (gaps.slot_hints(name) or ())}
    except Exception:  # noqa: BLE001 - planning must never break the crawl
        return pool
    if not hints:
        return pool
    serves_gap = [u for u in pool if any(h in str(u).lower() for h in hints)]
    if not serves_gap or len(serves_gap) == len(pool):
        return pool
    rest = [u for u in pool if u not in serves_gap]
    return serves_gap + rest


def _worth_continuing(graph, signals, remaining) -> bool:
    """True when confidence is below target AND some uncrawled page is aimed at a
    real gap. Stopping on a flat score while the pricing page sits uncrawled is
    how a run ends up 'understood' but unable to say anything specific."""
    try:
        ledger = gaps.assess(graph, signals)
        if ledger.is_confident():
            return False
        return any(a.kind == "crawl"
                   for a in gaps.plan(ledger, candidate_urls=remaining, limit=2))
    except Exception:  # noqa: BLE001
        return False


def _goals(graph):
    """The three INDEPENDENT research goals as a (understood, recent, person)
    triple. Completing one never satisfies another — each is checked on its own
    grounded evidence."""
    return _company_understood(graph), _recent_signal(graph), _person_found(graph)


def _stop_decision(graph, person_sources_left: bool):
    """DETERMINISTIC stop rule (replaces the old sufficiency gate that let a
    homepage stop research). Returns a stop-reason string, or None to keep going.

      CASE 1  understood AND recent AND person found            -> stop, all met.
      CASE 2  understood AND recent AND no person source left   -> stop, and
              conclude "No suitable public decision maker found."
      else    keep crawling — in particular NEVER stop just because the company
              is understood and a recent signal exists while a person-source page
              (about/team/leadership/…) is still uncrawled.
    """
    understood, recent, person = _goals(graph)
    if understood and recent and person:
        return "all goals met (company understood, recent signal, named person)"
    if understood and recent and not person_sources_left:
        return ("company understood + recent signal; person sources exhausted — "
                "No suitable public decision maker found.")
    return None


def _combine(pages) -> str:
    parts = [f"===== PAGE: {u} =====\n{t}" for u, t in pages if t and t.strip()]
    return "\n\n".join(parts)[:MAX_TEXT_CHARS]


def _build_output(graph, hooks, score, breakdown, pages_crawled, page_types) -> dict:
    team = [{"name": m.name, "role": m.role} for m in graph.team]
    contact_name, contact_role = select_primary_contact(graph)
    data = {
        "company_name": graph.value("company_name"),
        "founder_name": graph.value("founder_name"),
        "founder_role": graph.value("founder_role"),
        "primary_contact_name": contact_name,
        "primary_contact_role": contact_role,
        "team_members": team,
        "what_they_do": graph.value("what_they_do"),
        "target_customer": graph.value("target_customer"),
        "recent_focus": graph.value("recent_focus"),
        "unique_hook": hooks[0].text if hooks else None,
        "additional_hooks": [h.text for h in hooks[1:6]],
        "their_mission_or_why": graph.value("their_mission_or_why"),
        "tone_style": graph.value("tone_style"),
        "metrics_or_traction": graph.value("metrics_or_traction"),
        "pricing_model": graph.value("pricing_model"),
        "notable_customers": graph.values("notable_customers"),
        "tech_stack": graph.values("tech_stack"),
        "product_category": graph.value("product_category"),
        "business_model": graph.value("business_model"),
        "company_stage": graph.value("company_stage"),
        "competitive_positioning": graph.value("competitive_positioning"),
        "industries_served": graph.values("industries_served"),
        "product_differentiators": graph.values("product_differentiators"),
        "pain_points": graph.values("pain_points"),
        "integrations": graph.values("integrations"),
        "has_enough_detail": score >= RESEARCH_SCORE_THRESHOLD,
    }
    # Explicit "not found" — never leave downstream guessing whether a null means
    # "absent from the site" vs "not looked for". True = we located it; the
    # not_found list names the outreach-critical items we searched for and didn't.
    data["founder_found"] = bool(data["founder_name"] or team)
    data["decision_maker_found"] = bool(contact_name)
    _anchor_checks = (
        ("named_decision_maker", contact_name),
        ("founder_or_leadership", data["founder_name"] or team),
        ("named_customers", data["notable_customers"]),
        ("recent_event", data["recent_focus"]),
        ("metrics_or_traction", data["metrics_or_traction"]),
    )
    data["not_found"] = [label for label, value in _anchor_checks if not value]
    # ── Person discovery (Goal 3): a decision-maker candidate with rationale +
    #    grounded evidence, and the three goals reported independently.
    data["decision_maker"] = _decision_maker(graph, contact_name, contact_role)
    understood, recent, person = _goals(graph)
    data["goals"] = {"company_understood": understood,
                     "recent_signal": recent, "person_found": person}
    evidence = {node: [e.as_dict() for e in items]
                for node, items in graph.nodes.items() if items}
    if graph.team:
        evidence["team_members"] = [m.as_dict() for m in graph.team]

    return {
        "data": data,
        "evidence": evidence,
        "hooks": [h.as_dict() for h in hooks],
        "research_score": score,
        "score_breakdown": breakdown,
        "pages_crawled": pages_crawled,
        "page_types": page_types,
    }


def _why_relevant(role: str) -> str:
    """Deterministic rationale for why a named person is a plausible outbound
    decision-maker, from their role (no LLM call; never invented)."""
    r = (role or "").lower()
    if any(k in r for k in ("founder", "co-founder", "cofounder", "ceo",
                            "chief executive")):
        return ("Founder/CEO — at an early-stage company typically owns or signs "
                "off on outbound.")
    if any(k in r for k in ("sales", "revenue", "cro", "business development",
                            "account exec")):
        return "Owns the sales/revenue function — direct owner of outbound."
    if any(k in r for k in ("growth", "demand", "marketing", "cmo")):
        return "Owns growth/marketing — a likely outbound stakeholder or buyer."
    if any(k in r for k in ("partnership", "alliances", "bd ")):
        return "Owns partnerships/BD — plausible outbound decision-maker."
    if role:
        return "Named company leader; plausible outbound decision-maker."
    return "Named contact on the site; outbound relevance unconfirmed (no title)."


def _decision_maker(graph, name, role):
    """A single best decision-maker candidate with grounded evidence + rationale,
    or None. Reuses evidence already on the graph — invents nothing."""
    if not name:
        return None
    src = conf = quote = None
    best = graph.best("founder_name")
    if best and best.value == name:
        src, conf, quote = best.source_url, best.confidence, best.quote
    else:
        for m in graph.team:
            if m.name == name:
                src, conf, quote = m.source_url, m.confidence, m.quote
                break
    return {"name": name, "role": role, "source_url": src, "confidence": conf,
            "evidence": quote, "why_relevant": _why_relevant(role)}


def _person_not_found_reason(person_sources) -> str:
    """Explain WHY no person was found (never a silent null)."""
    if not person_sources:
        return ("No public people page (about/team/leadership) was found or "
                "reachable on the site; no named person could be verified.")
    return (f"Checked {len(person_sources)} person-source page(s) "
            "(about/team/leadership/press/etc.) but no public decision-maker was "
            "named with grounded evidence.")


def _low_confidence_reason(score: int, breakdown: dict, page_count: int) -> str:
    """Explain EXACTLY why confidence is low: which signals were missing."""
    labels = {
        "what_they_do": "product description", "founder_name": "founder/team",
        "product_category": "product category", "business_model": "business model",
        "recent_focus": "recent activity", "notable_customers": "named customers",
        "metrics_or_traction": "metrics/traction",
        "their_mission_or_why": "mission", "industries_served": "industries",
        "pricing_model": "pricing", "tech_stack": "technology",
    }
    missing = [labels[k] for k, v in breakdown.items() if k in labels and not v]
    detail = ("; ".join(missing[:6]) if missing
              else "the pages contained little specific, verifiable detail")
    return (f"Research score {score}/100 (below {RESEARCH_SCORE_THRESHOLD}) after "
            f"reading {page_count} page(s). Missing/weak: {detail}. We return this "
            "rather than send a generic email.")
