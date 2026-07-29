"""The Prospect Discovery Agent — the deterministic orchestrator.

    discover(owner, query) -> DiscoveryResult

It gathers candidates from Apollo's company database and the web providers over
bounded PASSES, drops anything the caller has already seen (per-user dedupe +
explicit excludes), applies exclude-keyword filters, merges duplicates across
sources as corroboration, verifies the hiring signal against Apollo's dated job
postings, ranks companies above demoted intermediaries, paginates, persists the
page, and returns structured JSON. No LLM, no deep research, no writing.

Stopping is QUALITY-based, not count-based (the old behaviour was "first pass,
whatever was valid", which is how a page of job boards got returned). A run ends
when one of these is true:

  * the page is full of STRONG candidates (>= DISCOVERY_STRONG_CONFIDENCE);
  * the last pass added fewer than DISCOVERY_PASS_MIN_GAIN new strong candidates,
    so more searching is unlikely to improve quality;
  * DISCOVERY_MAX_PASSES is reached.

``DiscoveryResult.quality`` reports which of those ended the run, so "why did it
stop" is an answer rather than a guess.
"""

import logging
from dataclasses import dataclass, field

from config.settings import (
    DISCOVERY_ALLOW_FALLBACK_TIER,
    DISCOVERY_MAX_PASSES,
    DISCOVERY_MIN_CONFIDENCE,
    DISCOVERY_PASS_MIN_GAIN,
    DISCOVERY_PROVIDER_POOL,
    DISCOVERY_STRONG_CONFIDENCE,
)
from discovery import aggregators, intent, narration, scoring, sources
from discovery.models import DiscoveryQuery, registrable_domain
from discovery.store import ProspectStore

log = logging.getLogger("discovery.engine")


class Narrator:
    """Streams the run as two kinds of line: a ``step`` (what is happening) and a
    ``thought`` (why, derived from real state by discovery/narration.py).

    Wraps the caller's ``progress`` callable, which is invoked as
    ``progress(text, kind)``. A one-argument sink (older callers, tests) still
    works: the kind is dropped rather than raising.
    """

    def __init__(self, progress=None):
        self._sink = progress if callable(progress) else None

    def _send(self, text, kind):
        if not self._sink or not text:
            return
        try:
            self._sink(text, kind)
        except TypeError:               # a legacy one-argument sink
            self._sink(text)
        except Exception:  # noqa: BLE001 - narration must never break discovery
            log.debug("progress sink raised; continuing")

    def step(self, text):
        self._send(text, "step")

    def thought(self, text):
        """One reasoning line, or a list of them (narration.* returns lists)."""
        for line in ([text] if isinstance(text, str) else list(text or [])):
            self._send(line, "thought")

    def __call__(self, text, kind="step"):
        """So a Narrator can be passed straight into anything expecting the raw
        ``progress`` callable."""
        self._send(text, kind)


@dataclass
class DiscoveryResult:
    status: str                       # ok | empty | error
    prospects: list = field(default_factory=list)     # list[Prospect]
    page: int = 0
    limit: int = 0
    returned: int = 0
    has_more: bool = False
    providers: dict = field(default_factory=dict)
    reason: str = ""
    quality: dict = field(default_factory=dict)       # passes/stop reason/counts

    def public(self) -> dict:
        return {
            "status": self.status,
            "page": self.page,
            "limit": self.limit,
            "returned": self.returned,
            "has_more": self.has_more,
            "providers": self.providers,
            "reason": self.reason,
            "quality": self.quality,
            "prospects": [p.public() for p in self.prospects],
        }


def discover(owner, query, *, store=None, exclude_domains=None,
             skip_seen=True, plan=None, progress=None) -> DiscoveryResult:
    """Find companies matching ``query`` for ``owner``.

    exclude_domains: extra domains to drop (e.g. the company being worked on).
    skip_seen: drop domains this owner has already discovered (durable dedupe).
    plan: a discovery.intent.SearchPlan. Omitted, one is parsed from the query,
    so callers that only have the user's words still get plan-driven search.
    progress: optional ``callable(str)`` invoked with human-readable stage labels
    ("Searching company databases", "Checking their live job postings") so a
    streaming caller narrates the real pipeline instead of faking steps on a timer.
    Never raises.
    """
    say = Narrator(progress)
    if isinstance(query, dict):
        query = DiscoveryQuery(**query)
    if query.is_empty():
        return DiscoveryResult("error", reason="No ICP filters given — say what "
                               "kind of companies to find.", limit=query.limit)

    avail = sources.providers_available()
    if not any(avail.values()):
        return DiscoveryResult("error", providers=avail,
                               reason="No search provider is configured.",
                               limit=query.limit)

    store = store or _safe_store()
    seen = set(registrable_domain(d) for d in (exclude_domains or []))
    if skip_seen and store is not None:
        try:
            seen |= store.seen_domains(owner)
        except Exception:  # noqa: BLE001 - dedupe is best-effort, never fatal
            log.info("seen_domains lookup failed; continuing without dedupe")

    search_query = query.search_string()
    pool = min(DISCOVERY_PROVIDER_POOL, max(query.limit * 2, DISCOVERY_PROVIDER_POOL))
    log.info("discovery_start query=%r limit=%s pool=%s", search_query, query.limit, pool)
    try:
        ranked, quality = _search_until_good(query, seen, pool, plan, say)
    except Exception:  # noqa: BLE001 - providers are wrapped, belt-and-braces
        log.exception("discovery search failed")
        return DiscoveryResult("error", providers=avail, limit=query.limit,
                               reason="The search providers couldn't be reached.")
    log.info("discovery_ranked query=%r ranked=%s seen=%s quality=%s",
             search_query, len(ranked), len(seen), quality)
    # When durable dedupe is on (the chat "find another 20" flow), already-returned
    # companies are excluded via ``seen``, so that IS the pagination cursor —
    # always take the top `limit` unseen. Only a stateless caller (skip_seen=False)
    # pages by an explicit offset over the full ranked pool.
    # Intermediaries are a FALLBACK, never a primary result: fill the page with
    # real companies first, and only top up with demoted results if the page
    # would otherwise be short (and the operator allows it at all).
    companies = [p for p in ranked if p.tier == "company"]
    fallback = [p for p in ranked if p.tier != "company"]
    start = 0 if skip_seen else query.page * query.limit
    page_items = companies[start:start + query.limit]
    if len(page_items) < query.limit and DISCOVERY_ALLOW_FALLBACK_TIER:
        room = query.limit - len(page_items)
        fb_start = 0 if skip_seen else max(0, start - len(companies))
        page_items = page_items + fallback[fb_start:fb_start + room]
    has_more = len(companies) > start + query.limit

    if store is not None and page_items:
        for p in page_items:
            p.owner = owner
        try:
            store.save_many(page_items)
        except Exception:  # noqa: BLE001 - persistence must not break discovery
            log.info("prospect save failed (returning results anyway)")

    if not page_items:
        log.info("discovery_result query=%r status=empty valid_company_prospects=0", search_query)
        say.thought(narration.empty(
            considered=quality.get("candidates_considered", 0),
            demoted=quality.get("demoted_intermediaries", 0)))
        return DiscoveryResult(
            "empty", page=query.page, limit=query.limit, providers=avail,
            quality=quality,
            reason=("No more new matches — try broadening the filters or a new query."
                    if query.page > 0 else
                    "No matching companies found for those filters."))
    quality["returned_companies"] = sum(1 for p in page_items if p.tier == "company")
    quality["returned_fallback"] = sum(1 for p in page_items if p.tier != "company")
    # How much of this page is actually worth acting on, said plainly.
    say.thought(narration.confidence(
        strong=sum(1 for p in page_items
                   if p.tier == "company" and p.confidence >= DISCOVERY_STRONG_CONFIDENCE),
        returned=len(page_items)))
    log.info("discovery_result query=%r status=ok returned=%s has_more=%s", search_query, len(page_items), has_more)
    return DiscoveryResult("ok", prospects=page_items, page=query.page,
                           limit=query.limit, returned=len(page_items),
                           has_more=has_more, providers=avail, quality=quality)


# ── multi-pass search with quality-based stopping ──────────────────────
def _search_until_good(query, seen, pool, plan=None, progress=None):
    """Execute the PLAN in bounded passes until the page is full of strong
    candidates or another pass stops being worth it.

    The plan (discovery/intent.py) decides the axes, not the chat model. When it
    is role-anchored, the primary Apollo search is by live JOB POSTING TITLE,
    which is industry-agnostic: that is what lets "hiring an AI video creator"
    return Clay or Ramp and not only AI-video vendors.

    ``progress`` (callable(str)) narrates the real stages for a streaming caller.
    Returns (ranked_prospects, quality).
    """
    say = progress if isinstance(progress, Narrator) else Narrator(progress)
    plan = plan or intent.parse(
        query.raw or query.search_string(), industry=query.industry,
        keywords=query.keywords, employee_range=query.employee_range,
        funding_stage=query.funding_stage, location=query.location,
        exclude_keywords=query.exclude_keywords, limit=query.limit)
    role = list(plan.roles or []) or sources.role_terms(query)
    # Verification matches EMPLOYER phrasings, not the user's: someone asks for an
    # "SDR", but the live posting says "Sales Development Representative". Verifying
    # against the user's term alone found nothing, so the whole page fell back to
    # "hiring, but not that role". The expanded titles are exactly what Apollo
    # searched, and the head-noun matcher keeps them from matching scattered words.
    verify_terms = list(plan.role_titles or []) or role
    passes = _pass_plans(query, plan)
    merged, passes_run, stop = {}, 0, "passes_exhausted"

    role_label = (plan.roles or [""])[0]
    searched_apollo = searched_web = False
    for index, step in enumerate(passes[:max(1, DISCOVERY_MAX_PASSES)], start=1):
        before = _strong_count(merged, plan)
        batch = []
        if step["apollo"]:
            first = not searched_apollo
            # Only claim the search when Apollo can actually run. Announcing a
            # source we never reached would be exactly the false narration the
            # grounding rule forbids; the gap line below says so instead.
            if first and sources.providers_available().get("apollo"):
                # Generic stage label on purpose: naming one vendor here reads as a
                # thin wrapper, and Saqua's value is the verify-and-rank that follows.
                # The SPECIFIC provider ("In Apollo's company database…") is still shown
                # per result on the card, which is where provenance belongs.
                say.step("Searching company databases")
            found = sources.search_apollo(
                query, plan=plan, keywords=step.get("apollo_tags"),
                job_titles=step.get("apollo_titles"), page=step["apollo_page"],
                stats=(stats := {}))
            batch += found
            if first:
                say.thought(narration.provider_gap(apollo=stats.get("state")))
                say.thought(narration.after_apollo(
                    total=stats.get("total"), kept=len(found),
                    staffing_dropped=stats.get("staffing_dropped", 0),
                    recruiter_names=stats.get("recruiter_names", ()),
                    agency_dropped=stats.get("agency_dropped", 0),
                    covers_demoted=stats.get("covers_demoted"),
                    role_label=role_label))
            searched_apollo = True
        if step["search_text"]:
            first = not searched_web
            if first:
                say.step("Cross-checking on the open web")
            found = sources.search_candidates(query, pool_size=pool,
                                              search_text=step["search_text"],
                                              stats=(wstats := {}))
            batch += found
            if first:
                say.thought(narration.provider_gap(web=wstats.get("web_state")))
                say.thought(narration.after_web(
                    demoted=wstats.get("demoted"), rejected=wstats.get("rejected"),
                    kept=len(found)))
            searched_web = True
        _merge(merged, batch, query, seen)
        _rescore(merged.values(), plan)
        passes_run = index
        after = _strong_count(merged, plan)
        gain = after - before
        log.info("discovery_pass %s (%s): +%s strong (total %s)",
                 index, step["label"], gain, after)

        if after >= query.limit:
            stop = "page_full_of_strong_candidates"
            break
        if index > 1 and gain < DISCOVERY_PASS_MIN_GAIN:
            stop = "extra_search_stopped_helping"
            break

    say.thought(narration.after_merge(
        companies=sum(1 for p in merged.values() if p.tier == "company"),
        fallback=sum(1 for p in merged.values() if p.tier != "company"),
        corroborated=sum(1 for p in merged.values() if len(p.sources) >= 2)))

    ranked = _rank(merged.values(), plan)
    # Verify the hiring signal on FINALISTS only (paid per company), then re-score
    # and re-rank: a verified role posting is the single biggest rank mover.
    verified = 0
    if ranked:
        finalists = ranked[:max(query.limit, 10)]
        if verify_terms:
            say.step("Checking their live job postings")
        try:
            verified = sources.verify_hiring(finalists, verify_terms)
        except Exception:  # noqa: BLE001 - verification is additive, never fatal
            log.info("hiring verification failed; continuing unverified")
        if verify_terms:
            say.thought(narration.after_verification(
                verified=verified, checked=len(finalists), role_label=role_label,
                hiring_any=sum(1 for p in finalists
                               if (p.hiring or {}).get("verified"))))
        say.step("Ranking by who is most likely to buy")
        _rescore(ranked, plan)
        ranked = _rank(ranked, plan)
        say.thought(narration.top_pick(next(
            (p for p in ranked if p.tier == "company"), None)))
    quality = {
        "passes": passes_run,
        "stopped_because": stop,
        "candidates_considered": len(merged),
        "strong_candidates": _strong_count(merged, plan),
        "companies": sum(1 for p in merged.values() if p.tier == "company"),
        "demoted_intermediaries": sum(1 for p in merged.values() if p.tier != "company"),
        "hiring_verified": verified,
        "role_terms": role,
        "plan": plan.public(),
    }
    return ranked, quality


def _rescore(prospects, plan) -> None:
    """Recompute customer likelihood for every candidate. ``confidence`` becomes
    the customer-likelihood total, so everything downstream (ranking, the card's
    match %, the free-tier cap) reads one number with one meaning."""
    for p in prospects:
        result = scoring.score_prospect(p, plan)
        p.score = result.public()
        p.confidence = result.total
        for reason in result.reasons:
            if reason and reason not in p.match_reasons:
                p.match_reasons.append(reason)
        if p.match_reasons:
            p.why_it_matches = "; ".join(p.match_reasons[:2])


def _pass_plans(query, plan) -> list:
    """The search angles, in order, derived from the PLAN.

    Role-anchored asks lead with the job-posting axis and only then widen; a
    category ask keeps the old category behaviour. The web passes deliberately use
    company-site vocabulary ("pricing", "book a demo") that job boards lack.
    """
    category = " ".join(plan.relevance_terms or []) or sources.category_phrase(query)
    titles = list(plan.role_titles or [])
    passes = []

    if plan.anchor in ("role", "both") and titles:
        # Primary: companies with a LIVE POSTING for the role, any industry.
        passes.append({"label": "hiring signal (any industry)", "apollo": True,
                       "apollo_page": 1, "apollo_titles": titles,
                       "apollo_tags": plan.keyword_tags or [],
                       "search_text": _careers_query(plan)})
        passes.append({"label": "hiring signal page 2", "apollo": True,
                       "apollo_page": 2, "apollo_titles": titles,
                       "apollo_tags": plan.keyword_tags or [],
                       "search_text": (f"{category} careers \"we're hiring\""
                                       if category else "")})
        if category:
            # Only now consider the category, and never as the only axis.
            passes.append({"label": "category relevance", "apollo": True,
                           "apollo_page": 1, "apollo_titles": [],
                           "apollo_tags": plan.relevance_terms[:2],
                           "search_text": f"{category} b2b company pricing customers"})
    else:
        # `plan.keyword_tags` is only populated when the user named a RECOGNISED
        # vertical, so a specific-but-unlisted ask ("AI SDR startups") reached
        # Apollo with no tag and skipped it entirely — losing SuperAGI, Klenty and
        # Clara, the only exactly-right answers available. Derive a tag from the
        # words instead. This is safe at both ends: apollo_keywords returns nothing
        # when the ask is purely generic, and search_apollo discards a match set
        # too broad to mean anything, so mega-corps cannot come back in.
        derived = plan.keyword_tags or sources.apollo_keywords(query)
        passes.append({"label": "literal ask", "apollo": True, "apollo_page": 1,
                       "apollo_tags": derived,
                       "apollo_titles": [], "search_text": query.search_string()})
        if category:
            passes.append({"label": "company category", "apollo": True,
                           "apollo_page": 2, "apollo_tags": derived,
                           "apollo_titles": [],
                           "search_text": f"{category} b2b saas company pricing customers"})
            passes.append({"label": "product angle", "apollo": False,
                           "apollo_page": 0, "apollo_titles": [],
                           "search_text": f"{category} platform for teams \"book a demo\""})
    return passes


def _careers_query(plan) -> str:
    """A web query aimed at EMPLOYERS' own careers pages for the role, not at job
    boards. The boards are still filtered, but this at least aims elsewhere."""
    role = (plan.roles or [""])[0]
    if not role:
        return ""
    return f'"{role}" careers site hiring -site:indeed.com -site:linkedin.com'


def _merge(merged: dict, batch, query, seen) -> None:
    """Fold a batch into the running set. A domain found by MORE THAN ONE source
    is corroborated, which is the strongest cheap signal we have, so it gains
    confidence and keeps the union of both sides' reasons."""
    for p in batch:
        if not p.domain or p.domain in seen:
            continue
        if _excluded_by_keyword(p, query.exclude_keywords):
            continue
        if p.confidence < DISCOVERY_MIN_CONFIDENCE:
            continue
        existing = merged.get(p.domain)
        if existing is None:
            merged[p.domain] = p
            continue
        # Same company from a second source.
        new_provider = not any(s.get("provider") == p.discovery_source
                               for s in existing.sources)
        for src in p.sources:
            existing.add_source(src.get("provider"), src.get("url"))
        for reason in p.match_reasons:
            if reason not in existing.match_reasons:
                existing.match_reasons.append(reason)
        if p.apollo_id and not existing.apollo_id:
            existing.apollo_id = p.apollo_id
        if p.growth and not existing.growth:
            existing.growth = p.growth
        if p.hiring and not existing.hiring:
            existing.hiring = p.hiring
        # Apollo's identity beats a web-scraped title for the company name.
        if p.discovery_source == "apollo" and p.company_name:
            existing.company_name = p.company_name
        # A second source can promote a demoted candidate back to "company" —
        # that is how an employer's own careers page escapes an ATS misread. But
        # not out of an evidence-backed verdict: a web hit that merely does not
        # recognise a trade publisher is weaker evidence than the industry code
        # that demoted it, and letting it win put FinTech Futures back at #1.
        if (existing.tier != "company" and p.tier == "company"
                and existing.kind not in aggregators.AUTHORITATIVE_KINDS):
            existing.tier, existing.kind = "company", p.kind
        base = max(existing.confidence, p.confidence)
        existing.confidence = min(1.0, base + (0.10 if new_provider else 0.0))
        if new_provider and len(existing.sources) >= 2:
            note = "Corroborated by " + " + ".join(
                sorted({s["provider"] for s in existing.sources}))
            if note not in existing.match_reasons:
                existing.match_reasons.append(note)


def _rank(prospects, plan) -> list:
    """Rank by CUSTOMER LIKELIHOOD, not keyword similarity.

    ``confidence`` is the weighted score from discovery/scoring.py, so a slightly
    less relevant company with a strong buying signal deliberately outranks a
    perfectly matching company with no sign of being in-market. Demoted
    intermediaries still can never outrank a real company.
    """
    return sorted(prospects, key=lambda x: (
        -(1 if x.tier == "company" else 0),
        -round(x.confidence, 4),
        x.company_name.lower(),          # deterministic tiebreak
    ))


def _strong_count(merged, plan) -> int:
    """How many candidates clear the quality bar. Accepts the merged dict or any
    iterable of prospects."""
    items = merged.values() if isinstance(merged, dict) else merged
    return sum(1 for p in items
               if p.tier == "company" and p.confidence >= DISCOVERY_STRONG_CONFIDENCE)


def _excluded_by_keyword(prospect, exclude_keywords) -> bool:
    if not exclude_keywords:
        return False
    hay = (prospect.company_name + " " + " ".join(prospect.basic_signals) + " "
           + prospect.industry).lower()
    return any(kw in hay for kw in exclude_keywords)


def _safe_store():
    try:
        return ProspectStore()
    except Exception:  # noqa: BLE001 - discovery still works without persistence
        log.info("prospect store unavailable; discovery will not persist")
        return None
