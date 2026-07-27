"""Search + candidate extraction across Apollo (companies), Exa and Tavily (web).

Apollo's organization search is the PRIMARY source: it queries a curated company
database, so it cannot hand back a job board as a "company" the way a web search
does. Exa and Tavily then corroborate and add signal. Each web result is turned
into a candidate deterministically: normalize the domain, classify it against
[discovery/aggregators.py] so intermediaries get demoted rather than ranked,
derive a name, and read light SIGNALS out of the title + snippet.

Two rules learned from a bad live result ("B2B founders hiring an AI video
creator" returned job boards above real companies):

  1. Hiring vocabulary is NOT evidence of a company. Matching "hiring" in a
     snippet used to add confidence, which is precisely what floated boards to
     the top. A hiring signal now has to be VERIFIED: Apollo's dated job
     postings, or a careers URL on the candidate's own domain.
  2. Intermediaries are demoted, not deleted. A user who asks for recruiting
     platforms should get them (see ``aggregators.query_requests``).

No LLM here, and it never raises: a provider that's down contributes nothing.
"""

import concurrent.futures
from collections import Counter
import logging
import re
from urllib.parse import urlparse

from config.settings import (
    APOLLO_JOB_POSTING_LOOKUPS,
    APOLLO_LOOKUP_CONCURRENCY,
    APOLLO_ORG_SEARCH_ENABLED,
    DISCOVERY_PROVIDER_POOL,
    EXCLUDED_RESOLUTION_DOMAINS,
)
from discovery import aggregators, intent
from discovery.models import Prospect, registrable_domain
from research import apollo_orgs, exa, tavily

log = logging.getLogger("discovery.sources")

# Domains that are directories / media / listicles — never the prospect itself.
_EXTRA_EXCLUDE = frozenset({
    "techcrunch.com", "builtin.com", "clutch.co", "goodfirms.co",
    "softwareadvice.com", "getlatka.com", "growjo.com", "similarweb.com",
    "wellfound.com", "angel.co", "ycombinator.com", "medium.com", "substack.com",
    "notion.site", "wordpress.com", "wixsite.com", "blogspot.com", "gartner.com",
    "statista.com", "hubspot.com", "failory.com", "cbinsights.com", "owler.com",
    "slintel.com", "6sense.com", "theorg.com", "rocketreach.co", "apollo.io",
    "explodingtopics.com", "starterstory.com", "eu-startups.com", "sifted.eu",
    "prnewswire.com", "tracxn.com", "startupsavant.com", "pitchbook.com",
    "dealroom.co", "privco.com",
})
_EXCLUDE = frozenset(EXCLUDED_RESOLUTION_DOMAINS) | _EXTRA_EXCLUDE

_NON_COMPANY_DOMAINS = frozenset({
    "fandom.com", "wikia.org", "wikipedia.org", "wikimedia.org",
    "imdb.com", "rottentomatoes.com", "metacritic.com", "letterboxd.com",
    "tvguide.com", "thetvdb.com", "themoviedb.org", "screenrant.com",
    "collider.com", "ign.com", "gamespot.com", "polygon.com", "kotaku.com",
    "forbes.com", "inc.com", "entrepreneur.com", "businessinsider.com",
    "nytimes.com", "wsj.com", "bloomberg.com", "reuters.com", "apnews.com",
    "cnn.com", "bbc.com", "theverge.com", "wired.com", "fastcompany.com",
    "crunchbase.com", "g2.com", "capterra.com", "trustpilot.com",
})

_BAD_PATH_PARTS = (
    "/wiki/", "/title/", "/tv/", "/movie/", "/movies/", "/news/", "/article/",
    "/articles/", "/blog/", "/blogs/", "/post/", "/posts/", "/list/", "/lists/",
    "/reviews/", "/review/", "/directory/", "/directories/", "/category/",
    "/tag/", "/author/", "/watch/", "/episode/", "/episodes/",
)

_BAD_TEXT_RE = re.compile(
    r"\b("
    r"wiki|fandom|episode|season|cast|trailer|film|movie|tv series|television|"
    r"rotten tomatoes|imdb|recap|review|article|blog post|listicle|top \d+|"
    r"best \d+|directory|alternatives list"
    r")\b",
    re.I,
)

_BUSINESS_TEXT_RE = re.compile(
    r"\b("
    r"company|startup|business|platform|software|saas|api|sdk|product|solution|"
    r"customers?|pricing|careers?|hiring|join our team|about us|contact us|book a demo|request a demo|"
    r"enterprise|teams?|developers?|founders?|b2b|use cases?|integrations?|"
    r"security|compliance|workflow|automation"
    r")\b",
    re.I,
)

_BUSINESS_PATH_PARTS = (
    "/pricing", "/customers", "/customer", "/case-studies", "/case-study",
    "/careers", "/jobs", "/about", "/company", "/contact", "/demo",
    "/product", "/features", "/solutions", "/use-cases", "/integrations",
    "/developers", "/docs", "/api", "/security",
)

_SAAS_PRODUCT_RE = re.compile(
    r"\b("
    r"saas|software|platform|product|api|sdk|docs?|developers?|integrations?|"
    r"pricing|customers?|case studies|demo|workflow|automation|dashboard|"
    r"analytics|enterprise|security|compliance"
    r")\b",
    re.I,
)

_SERVICE_OR_AGENCY_RE = re.compile(
    r"\b("
    r"agency|consulting|consultants?|software development services?|"
    r"custom software development|outsourcing|staff augmentation|nearshore|"
    r"offshore|digital transformation services?|it services?|managed services?|"
    r"company builder|venture studio|startup studio|holding company"
    r")\b",
    re.I,
)

_VC_INVESTOR_RE = re.compile(
    r"\b("
    r"venture capital|vc\b|investment firm|investors?|fund|portfolio companies|"
    r"seed fund|pre-seed fund|capital partners|angel investors?"
    r")\b",
    re.I,
)

# Signal detectors: (label, regex) over the title + snippet.
_SIGNALS = (
    ("seed", r"\bseed(?:\s+round|\s+stage|-stage)?\b"),
    ("series a", r"\bseries\s*a\b"),
    ("series b", r"\bseries\s*b\b"),
    ("series c", r"\bseries\s*c\b"),
    ("bootstrapped", r"\bbootstrapp?ed\b"),
    ("raised funding", r"\braised\s+\$?\d|\bsecured\s+\$?\d|\bfunding\b"),
    ("hiring", r"\bhiring\b|\bwe'?re hiring\b|\bcareers\b|\bjoin our team\b"),
    ("hiring sdrs", r"\bsdr\b|\bsales development\b|\baccount executive\b"),
    ("b2b", r"\bb2b\b"),
    ("saas", r"\bsaas\b|\bsoftware as a service\b"),
    ("fintech", r"\bfintech\b|\bpayments?\b|\bbanking\b"),
    ("ai", r"\bai\b|\bartificial intelligence\b|\bmachine learning\b|\bllm\b"),
    ("devtools", r"\bdevtool|\bdeveloper tool|\bapi\b|\bsdk\b"),
    ("shopify app", r"\bshopify\b"),
    ("yc", r"\by combinator\b|\byc\b|\byc[\s-]?[wsf]\d{2}\b"),
    ("ecommerce", r"\be-?commerce\b"),
    ("healthtech", r"\bhealthtech\b|\bhealthcare\b"),
)

# Stage signals mapped to a coarse stage label.
_STAGE_FROM_SIGNAL = {
    "seed": "seed", "series a": "series a", "series b": "series b",
    "series c": "series c+", "bootstrapped": "bootstrapped",
}


def _is_company_domain(domain: str) -> bool:
    if not domain or "." not in domain:
        return False
    if domain in _EXCLUDE or any(domain.endswith("." + e) for e in _EXCLUDE):
        return False
    if domain in _NON_COMPANY_DOMAINS or any(domain.endswith("." + e) for e in _NON_COMPANY_DOMAINS):
        return False
    return True


def assess(raw: dict, query=None) -> tuple:
    """``(kind, drop_reason)`` for one web search result.

    * ``("company", "")`` — a real candidate.
    * ``(<intermediary kind>, "")`` — a job board / ATS / marketplace /
      directory. NOT dropped: the engine puts these in the fallback tier, and if
      the user explicitly asked for that category it is promoted to a company.
    * ``("", reason)`` — drop entirely (media, wiki, listicle, no business
      evidence at all). These can never be outbound prospects.

    Splitting demote from drop is the point: the old single reject list threw
    away the information that a result was an aggregator, so there was no way to
    rank it below real companies instead of silently discarding it.
    """
    kind, why = aggregators.classify(
        raw.get("url") or "", raw.get("title") or "", raw.get("content") or "")
    if kind in aggregators.INTERMEDIARY_KINDS:
        # The split that matters: is this a page ABOUT companies, or a company we
        # happen not to want? A Tracxn profile of Acme is the former — Tracxn is
        # not the prospect and Acme is not on its own domain — so it is dropped.
        # A job board / ATS / marketplace is a real operating business, so it is
        # only demoted, and is promoted back if the user asked for that category.
        if kind in aggregators.DROP_KINDS:
            return "", why
        if aggregators.query_requests(kind, _query_text(query)):
            return aggregators.COMPANY, ""
        return kind, ""
    reason = _reject_reason(raw, query)
    return ("", reason) if reason else (aggregators.COMPANY, "")


def _reject_reason(raw: dict, query=None) -> str | None:
    """Return why a search result is not a company prospect, else None.

    Discovery gets broad web results. This gate admits only company/startup/
    product pages with business evidence; entertainment/wiki/media/list content
    must not enter the outbound pipeline.
    """
    url = raw.get("url") or ""
    parsed = urlparse(url)
    domain = registrable_domain(url)
    title = raw.get("title") or ""
    content = raw.get("content") or ""
    text = f"{title}\n{content}"
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    if not _is_company_domain(domain):
        return "non_company_domain"
    if domain.endswith(".vc") and not _query_allows_investors(query):
        return "vc_or_investor_domain"
    if any(part in path for part in _BAD_PATH_PARTS):
        return "non_company_path"
    if host.startswith(("directory.", "wiki.", "news.", "blog.")):
        return "non_company_subdomain"
    if re.search(r"\b(directory listing|company profile|profile page)\b", text, re.I):
        return "directory_or_profile_page"
    if _BAD_TEXT_RE.search(text) and not _BUSINESS_TEXT_RE.search(text):
        return "media_or_reference_content"
    if _VC_INVESTOR_RE.search(text) and not _query_allows_investors(query):
        return "vc_or_investor_site"
    if _query_targets_saas(query) and _SERVICE_OR_AGENCY_RE.search(text):
        return "service_agency_not_saas_product"

    root_or_home = path in ("", "/")
    business_path = any(path.startswith(part) for part in _BUSINESS_PATH_PARTS)
    business_text = bool(_BUSINESS_TEXT_RE.search(text))
    if not (root_or_home or business_path or business_text):
        return "missing_business_signals"
    if not (business_path or business_text):
        return "missing_company_likeness"
    if _query_targets_saas(query) and not (business_path or _SAAS_PRODUCT_RE.search(text)):
        return "missing_saas_product_signals"
    return None


def _query_text(query) -> str:
    if query is None:
        return ""
    parts = [
        getattr(query, "raw", ""),
        getattr(query, "industry", ""),
        getattr(query, "funding_stage", ""),
        " ".join(getattr(query, "keywords", []) or []),
    ]
    return " ".join(p for p in parts if p).lower()


def _query_allows_investors(query) -> bool:
    text = _query_text(query)
    return bool(re.search(r"\b(vc|venture capital|investors?|investment firms?|funds?)\b", text))


def _query_targets_saas(query) -> bool:
    text = _query_text(query)
    return bool(re.search(r"\b(saas|software|devtools?|developer tools?|ai tools?|platforms?)\b", text))


def _name_from(title: str, domain: str) -> str:
    """Best-effort company name: the lead fragment of the title, else the domain
    core capitalised."""
    t = (title or "").strip()
    if t:
        # Cut at the first separator that usually precedes a tagline.
        frag = re.split(r"\s[|\-–—:·]\s", t, maxsplit=1)[0].strip()
        # Reject obviously non-name fragments: listicle headings, and the
        # careers-page headings a hiring query surfaces constantly. A live run
        # returned the company "Explore SDR Sales Jobs" (really memoryblue.com),
        # where the domain core was the far better name.
        if 1 <= len(frag.split()) <= 6 and not re.search(
                r"\b(top|best|list|companies|startups|jobs|careers|hiring|"
                r"openings|vacancies|vacancy|\d{2,})\b", frag.lower()):
            return frag
    core = domain.split(".")[0]
    return core.replace("-", " ").title()


def _signals(text: str, query) -> list:
    low = text.lower()
    found = []
    for label, pat in _SIGNALS:
        if re.search(pat, low) and label not in found:
            found.append(label)
    for kw in query.keywords:
        if kw and kw in low and kw not in found:
            found.append(kw)
    return found[:8]


def _build(raw: dict, query, source: str, kind: str = None):
    """Turn one web search result into a Prospect, or None if it must be dropped.

    ``kind`` is the ``assess`` verdict when the caller already has it (the hot
    path). Left None, this re-runs ``assess`` itself, so calling _build directly
    can never smuggle a wiki page or a VC firm into the pipeline.
    """
    url = raw.get("url") or ""
    domain = registrable_domain(url)
    if not domain:
        return None
    if kind is None:
        kind, drop_reason = assess(raw, query)
        if drop_reason or not kind:
            return None
    title = raw.get("title") or ""
    content = raw.get("content") or ""
    text = f"{title}\n{content}"
    low = text.lower()

    signals = _signals(text, query)
    # Deterministic confidence from evidence overlap with the ICP. NOTE: nothing
    # here credits hiring vocabulary — an unverified "hiring" match in a snippet
    # is exactly how job boards used to outrank companies. Verified hiring is
    # added later, by verify_hiring().
    conf, reasons = 0.30, []
    if query.industry and query.industry.lower() in low:
        conf += 0.15
        reasons.append(f"Page mentions {query.industry}")
    if query.keywords:
        hit = [k for k in query.keywords if k in low]
        if hit:
            conf += min(0.20, 0.08 * len(hit))
            reasons.append("Matches your keywords: " + ", ".join(hit))
    if query.location and query.location.lower() in low:
        conf += 0.12
        reasons.append(f"Based in {query.location}")
    if query.funding_stage:
        st = query.funding_stage.lower()
        if any(st in s for s in signals) or st in low:
            conf += 0.12
            reasons.append(f"Funding stage looks like {query.funding_stage}")
    if source == "exa":               # Exa surfaces company homepages more often
        conf += 0.08
    if any(part in (urlparse(url).path or "").lower() for part in _BUSINESS_PATH_PARTS):
        conf += 0.04
    if _SAAS_PRODUCT_RE.search(text):
        conf += 0.04
        reasons.append("Reads like a software product, not a services shop")
    # An intermediary that survived to here is a fallback result: never let it
    # score into the same band as a real company.
    is_intermediary = kind in aggregators.INTERMEDIARY_KINDS
    if is_intermediary:
        conf = min(conf, 0.35) * 0.5
        reasons = [f"Fallback result: this is a {kind.replace('_', ' ')}"] + reasons
    conf = min(1.0, conf)

    stage = query.funding_stage or next(
        (_STAGE_FROM_SIGNAL[s] for s in signals if s in _STAGE_FROM_SIGNAL), "unknown")
    industry = query.industry or _infer_industry(signals)

    # On a careers/jobs URL the page title is the JOB title, not the company, so
    # deriving the name from it produces prospects called "Senior Video Producer".
    # A page advertising ANOTHER company's job (a board) is not own-hiring evidence,
    # even when the URL sits on the board's own /careers path.
    own_careers = (aggregators.is_hiring_evidence(url, domain)
                   and not aggregators.hosts_third_party_posting(title, domain))
    name = _name_from("", domain) if own_careers else _name_from(title, domain)

    p = Prospect(
        company_name=name,
        website=f"https://{domain}",
        domain=domain,
        industry=industry,
        location=query.location or "",
        estimated_company_size=_size_from(query.employee_range),
        estimated_stage=stage,
        confidence=conf,
        why_it_matches=_why(reasons, query),
        discovery_source=source,
        basic_signals=signals,
        query=query.search_string(),
        kind=kind,
        tier="fallback" if is_intermediary else "company",
        match_reasons=reasons,
    )
    p.add_source(source, url)
    # A careers URL on the candidate's OWN domain is real hiring evidence (unlike
    # a third-party listing). ``match`` records how strong it is, so that only a
    # ROLE match is allowed to reorder the list (see engine._rank).
    if own_careers:
        posted_role = _role_from_careers_url(url) or (title or "").strip()
        # With no role in the ask there is nothing to match, so a careers page is
        # page-level evidence only. Claiming "role" here would assert a match the
        # user never asked for.
        wanted = role_terms(query)
        matched = bool(wanted and posted_role and apollo_orgs.matching_postings(
            [{"title": posted_role}], wanted))
        p.hiring = {
            "verified": True, "source": "own_careers_page",
            "match": "role" if matched else "page",
            "summary": (f"Hiring on their own site: {posted_role}" if posted_role
                        else "Careers page on their own site"),
            "postings": ([{"title": posted_role, "url": url}] if posted_role else []),
            "url": url,
        }
        p.confidence = min(1.0, p.confidence + (0.14 if matched else 0.02))
        if matched:
            p.match_reasons.insert(0, p.hiring["summary"])
    return p


def _role_from_careers_url(url: str) -> str:
    """The job title from a careers URL slug: ".../careers/video-producer" ->
    "video producer". Empty when the path carries no slug."""
    path = (urlparse(url if "://" in url else "https://" + url).path or "").strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return ""
    slug = parts[-1]
    if "." in slug:                      # a file, not a slug
        return ""
    words = [w for w in re.split(r"[-_]+", slug) if w and not w.isdigit()]
    # Reject opaque slugs (UUIDs / hashes like ".../careers/e0fb2fa9-fbbc-4cfa"):
    # a hex token is an id, not a job title, and putting it on a card reads as a bug.
    if not (2 <= len(words) <= 6):
        return ""
    if any(re.fullmatch(r"[0-9a-f]{6,}", w) for w in words):
        return ""
    return " ".join(words)


def _why(reasons, query) -> str:
    """One short sentence for the collapsed view, built from the ranked reasons."""
    if reasons:
        return "; ".join(reasons[:2])
    return f"Surfaced by search for “{query.search_string()}”"


def _infer_industry(signals) -> str:
    for tag in ("fintech", "saas", "ai", "devtools", "ecommerce", "healthtech",
                "b2b"):
        if tag in signals:
            return tag
    return ""


def _size_from(employee_range: str) -> str:
    return employee_range.strip() if employee_range else "unknown"


def search_candidates(query, pool_size: int = None, *, search_text: str = None,
                      stats: dict = None) -> list:
    """Candidate Prospects from ALL configured sources (unranked, may contain
    duplicate domains — the engine dedupes and treats duplicates across sources
    as corroboration). Providers that are unavailable or error contribute
    nothing.

    ``search_text`` overrides the query string for this pass, which is how the
    engine widens or re-angles a search on a later pass without mutating the
    caller's query object.

    ``stats``: an optional dict the caller passes in to receive what this pass
    actually saw (``raw``/``demoted``/``rejected``), so the run can be narrated
    from real observations rather than a guess.
    """
    pool = pool_size or DISCOVERY_PROVIDER_POOL
    q = search_text or query.search_string()
    if not q:
        return []

    # Per-provider outcome, so a provider that never ran is distinguishable from
    # one that ran and found nothing. Without this the narration cannot honestly
    # say "Tavily is unavailable" versus "Tavily found nothing".
    web_state = {}

    def _run(name, provider):
        from research.providers_common import last_error, refused
        if not provider.available():
            web_state[name] = "unavailable"
            return []
        if refused(name):
            # Already shut off at the account; do not spend another call on it.
            web_state[name] = "unavailable"
            return []
        try:
            results = [(name, r) for r in provider.search(q, max_results=pool)]
        except Exception:  # noqa: BLE001
            log.info("%s discovery search failed", name)
            web_state[name] = "failed"
            return []
        # An empty list means one of two very different things. A provider that
        # REFUSED (quota, bad key) must not be reported as "looked and found
        # nothing" — that is how Tavily being over its plan limit was invisible
        # for days while the stream said there was no recent coverage.
        if not results and last_error(name):
            log.info("%s refused: %s", name, last_error(name))
            web_state[name] = "unavailable"
            return []
        web_state[name] = "ok"
        return results

    raws = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool_ex:
        for fut in (pool_ex.submit(_run, "exa", exa),
                    pool_ex.submit(_run, "tavily", tavily)):
            raws.extend(fut.result())

    out = []
    rejected, demoted = Counter(), Counter()
    for source, raw in raws:
        kind, drop_reason = assess(raw, query)
        if drop_reason:
            rejected[drop_reason] += 1
            continue
        if kind in aggregators.INTERMEDIARY_KINDS:
            demoted[kind] += 1
        p = _build(raw, query, source, kind)
        if p is not None:
            out.append(p)
    log.info(
        "discovery_web query=%r raw=%s dropped=%s demoted=%s candidates=%s",
        q, len(raws), dict(rejected), dict(demoted), len(out),
    )
    if stats is not None:
        stats.update({"raw": len(raws), "demoted": dict(demoted),
                      "rejected": dict(rejected), "kept": len(out),
                      "web_state": dict(web_state)})
    return out


# ── Apollo: the primary company source ─────────────────────────────────────
def search_apollo(query, *, plan=None, keywords=None, job_titles=None,
                  page: int = 1, stats: dict = None) -> list:
    """Candidate Prospects from Apollo's COMPANY database.

    ``job_titles`` searches by LIVE JOB POSTING, which is industry-agnostic: that
    is what lets a "hiring an AI video creator" ask return Clay, Vanta or Ramp
    instead of only AI-video vendors. ``keywords`` searches by product category.

    Results skip the web heuristics (they are companies by construction) but not
    the intermediary rules, and RECRUITERS ARE DROPPED OUTRIGHT: a role-first
    search is dominated by staffing firms, because recruiters post the most job
    ads, and none of them is ever the prospect.

    ``stats``: an optional dict the caller passes in to receive what this search
    actually saw (Apollo's ``total``, how many recruiters were dropped and WHICH),
    so a streaming caller can narrate the run from real observations.
    """
    if not (APOLLO_ORG_SEARCH_ENABLED and apollo_orgs.available()):
        if stats is not None:
            stats["state"] = "unavailable"
        return []
    tags = keywords if keywords is not None else (
        list(getattr(plan, "keyword_tags", None) or []) if plan else apollo_keywords(query))
    titles = job_titles if job_titles is not None else (
        list(getattr(plan, "role_titles", None) or []) if plan else [])
    if not tags and not titles:
        return []

    bands = (list(getattr(plan, "employee_ranges", None) or []) if plan
             else _apollo_employee_ranges(query.employee_range))
    locations = (list(getattr(plan, "locations", None) or []) if plan
                 else ([query.location] if query.location else None))
    res = apollo_orgs.search_organizations(
        keywords=tags, job_titles=titles, employee_ranges=bands,
        locations=locations, page=page)
    if res.get("status") != "ok":
        log.info("apollo org search unusable: %s", res.get("reason") or res.get("status"))
        if stats is not None:
            # "empty" is a real answer from a working provider; the others mean the
            # provider did not actually run, which the narration must not gloss over.
            stats["state"] = ("empty" if res.get("status") == "empty"
                              else "unavailable" if res.get("status") == "unavailable"
                              else "failed")
        return []

    query_text = _query_text(query)
    out, staffing_dropped, recruiter_names, agency_dropped = [], 0, [], 0
    for org in res.get("organizations") or []:
        domain = org.get("domain") or ""
        name = org.get("name") or ""
        if not domain or not name:
            continue
        # Recruiters / staffing / agencies: they post other companies' roles. The
        # two classes are counted apart so the narration states the true reason
        # (only a real staffing firm gets named as a recruiter).
        excluded = intent.staffing_kind(org.get("sic_codes"), org.get("naics_codes"))
        if excluded:
            staffing_dropped += 1
            if excluded == "recruiter":
                if len(recruiter_names) < 3:
                    recruiter_names.append(name)
            else:
                agency_dropped += 1
            continue
        kind = aggregators.domain_kind(domain)
        if kind in aggregators.INTERMEDIARY_KINDS and not aggregators.query_requests(
                kind, query_text):
            if kind in aggregators.DROP_KINDS:
                continue
            tier = "fallback"
        else:
            kind, tier = aggregators.COMPANY, "company"

        industry_kind = apollo_orgs.industry_kind(org)
        reasons = ["In Apollo's company database (a real company, not a listing)"]
        if titles:
            reasons.append("Has a live posting for "
                           + (", ".join(titles[:2]) + " or similar"
                              if len(titles) > 1 else titles[0]))
        if industry_kind == "software":
            reasons.append("Software product company (SIC/NAICS)")
        if org.get("founded_year"):
            reasons.append(f"Founded {org['founded_year']}")

        p = Prospect(
            company_name=name,
            website=org.get("website") or f"https://{domain}",
            domain=domain,
            industry=(getattr(plan, "industry", "") if plan else query.industry) or "",
            location=(locations[0] if locations else ""),
            estimated_company_size=_size_from(query.employee_range),
            estimated_stage=query.funding_stage or "unknown",
            confidence=0.5,           # provisional; scoring.py sets the real rank
            why_it_matches=_why(reasons, query),
            discovery_source="apollo",
            basic_signals=[s for s in (["saas"] if industry_kind == "software" else [])],
            query=query.search_string(),
            kind=kind,
            tier=tier,
            match_reasons=reasons,
            apollo_id=org.get("apollo_id") or "",
            growth={"headcount_6mo": org.get("headcount_growth_6mo"),
                    "headcount_12mo": org.get("headcount_growth_12mo")},
            industry_kind=industry_kind,
            founded_year=org.get("founded_year"),
            is_public=bool(org.get("is_public")),
            annual_revenue=org.get("revenue"),
            has_intent=bool(org.get("has_intent")),
        )
        # A posting-title search IS a hiring signal, pending per-company
        # verification, which upgrades match to "role" and attaches the posting.
        if titles:
            p.hiring = {"verified": False, "source": "apollo_title_filter",
                        "match": "any", "postings": [],
                        "summary": "Apollo lists a matching open role"}
        p.add_source("apollo", org.get("linkedin_url") or "")
        out.append(p)
    log.info("discovery_apollo tags=%s titles=%s candidates=%s staffing_dropped=%s of %s total",
             tags, titles, len(out), staffing_dropped, res.get("total"))
    if stats is not None:
        stats.update({"state": "ok", "total": res.get("total"), "kept": len(out),
                      "staffing_dropped": staffing_dropped,
                      "recruiter_names": recruiter_names,
                      "agency_dropped": agency_dropped})
    return out


def verify_hiring(prospects, role_terms, *, limit: int = None) -> int:
    """Verify the hiring signal for the top ``limit`` prospects using Apollo's
    DATED job postings, and fold the result into confidence. Returns how many
    were verified as hiring for the requested role.

    This is what makes "companies actively hiring" trustworthy: instead of
    believing a job board listed them, we read the employer's own live postings
    and keep the title, url, and date as the evidence. Paid per company, so it
    runs on finalists only and never raises.
    """
    if not (APOLLO_ORG_SEARCH_ENABLED and apollo_orgs.available()):
        return 0
    cap = APOLLO_JOB_POSTING_LOOKUPS if limit is None else limit
    targets = [x for x in prospects if x.apollo_id][:max(0, cap)]
    if not targets:
        return 0

    # CONCURRENTLY: these are independent per-company HTTP calls, and running a
    # dozen of them in sequence added minutes to a single chat turn. Bounded pool,
    # same pattern as the web providers above.
    lookups = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(APOLLO_LOOKUP_CONCURRENCY, len(targets))) as pool:
        futures = {pool.submit(apollo_orgs.job_postings, p.apollo_id): p
                   for p in targets}
        for fut in concurrent.futures.as_completed(futures):
            try:
                lookups[futures[fut].domain] = fut.result()
            except Exception:  # noqa: BLE001 - one bad lookup must not sink the batch
                continue

    verified = 0
    for p in targets:
        res = lookups.get(p.domain) or {}
        if res.get("status") != "ok":
            continue
        postings = res.get("postings") or []
        # No role asked for => open roles are generic spend signal ("any"), never a
        # role match. Only an explicit role can produce a "role" verdict.
        matches = apollo_orgs.matching_postings(postings, role_terms) if role_terms else []
        if not matches:
            # Hiring at all is weaker evidence than hiring for THIS role, but it
            # is still evidence the company is spending.
            if postings:
                p.hiring = {"verified": True, "source": "apollo", "match": "any",
                            "open_roles": len(postings),
                            "summary": (f"{len(postings)} open roles, none matching "
                                        "the role you asked about" if role_terms
                                        else f"{len(postings)} open roles"),
                            "postings": postings[:3]}
                p.confidence = min(1.0, p.confidence + 0.04)
            continue
        top = matches[0]
        when = (top.get("posted_at") or "")[:10]
        p.hiring = {
            "verified": True, "source": "apollo", "match": "role",
            "open_roles": len(postings),
            "summary": (f"Hiring: {top.get('title')}"
                        + (f" (posted {when})" if when else "")),
            "postings": matches[:3],
        }
        p.match_reasons.append(p.hiring["summary"])
        p.confidence = min(1.0, p.confidence + 0.14)
        p.why_it_matches = _why(p.match_reasons, None) if p.match_reasons else p.why_it_matches
        verified += 1
    log.info("discovery_hiring_verified=%s of %s looked up", verified, cap)
    return verified


def apollo_keywords(query) -> list:
    """Apollo keyword tags for an ICP. Apollo indexes what a company DOES, so
    the tag has to describe the PRODUCT CATEGORY, not the ask.

    Two hard-won rules, both from a bad live run:

      * Strip role nouns. "ai video creator" is a job title; "ai video" is a
        category. Apollo has 1044 companies for the latter and noise for the
        former.
      * Never send a generic tag next to a specific one. Apollo ORs its tags, so
        adding "b2b saas" to "ai video" returned Anaplan, Autodesk and DocuSign
        above the actual AI-video companies. At most two tags, most specific
        first, and none of them generic.

    Returns [] when the ask is only generic ("find me B2B SaaS companies"), which
    is the honest answer: Apollo cannot narrow that, so the web providers should
    handle it alone.
    """
    cands = []
    for kw in list(query.keywords or []):
        tag = _tag_from(kw)
        if tag:
            cands.append(tag)
    if not cands:
        tag = _tag_from(category_phrase(query))
        if tag:
            cands.append(tag)
    cands.sort(key=lambda s: (-len(s.split()), -len(s)))
    out = []
    for tag in cands:
        if tag not in out:
            out.append(tag)
    return out[:2]


def _tag_from(text: str) -> str:
    """A distinguishing Apollo tag, or "" when the text carries no category."""
    cleaned = _ROLE_NOUN_RE.sub(" ", _strip_hiring_words(text))
    words = [w for w in cleaned.split() if w not in _GENERIC_TAG_WORDS]
    return " ".join(words[:3]).strip()


def category_phrase(query) -> str:
    """The product category behind the ask, with hiring vocabulary, stopwords and
    repetition removed. Used for the later search passes and as a tag fallback."""
    raw = " ".join(x for x in [getattr(query, "raw", ""),
                               getattr(query, "industry", ""),
                               " ".join(getattr(query, "keywords", []) or [])] if x)
    return _dedupe_words(_strip_hiring_words(raw))


_HIRING_WORDS_RE = re.compile(
    r"\b(hiring|hire|hires|recruit(?:ing|ment)?|job|jobs|role|roles|position|"
    r"positions|opening|openings|vacanc(?:y|ies)|career|careers|candidate|"
    r"candidates|founders?|companies|company|startups?)\b",
    re.I,
)

# Filler that carries no search meaning and, worse, leaks into extracted phrases
# ("for an ai video creator") where it corrupts an Apollo tag.
_STOPWORDS_RE = re.compile(
    r"\b(for|an|a|the|with|who|whose|that|are|is|be|in|on|at|of|and|or|to|from|"
    r"by|as|their|its|our|your|my|me|i|we|us|find|show|get|list|give|need|want|"
    r"looking|search|any|some|more|please)\b",
    re.I,
)

# Job-title nouns: the difference between a role and a product category.
_ROLE_NOUN_RE = re.compile(
    r"\b(creators?|specialists?|managers?|engineers?|designers?|developers?|"
    r"editors?|producers?|leads?|directors?|analysts?|associates?|coordinators?|"
    r"strategists?|marketers?|writers?|representatives?|interns?|consultants?|"
    r"assistants?|architects?|scientists?|technicians?|executives?|officers?)\b",
    re.I,
)

# Words too common to narrow anything: as an Apollo tag they match everything.
_GENERIC_TAG_WORDS = frozenset({
    "b2b", "b2c", "saas", "software", "tech", "technology", "platform",
    "platforms", "business", "businesses", "service", "services", "solution",
    "solutions", "app", "apps", "tool", "tools", "product", "products",
    "digital", "online", "cloud", "enterprise", "team", "teams",
})


def _strip_hiring_words(text: str) -> str:
    out = _HIRING_WORDS_RE.sub(" ", str(text or ""))
    out = _STOPWORDS_RE.sub(" ", out)
    return " ".join(out.split()).strip(" ,-·").lower()


def _dedupe_words(text: str) -> str:
    """Collapse repetition created by concatenating raw + industry + keywords."""
    out = []
    for word in text.split():
        if word not in out:
            out.append(word)
    return " ".join(out)


_ROLE_PATTERNS = (
    r"hiring\s+(?:for\s+)?(?:an?\s+|the\s+)?([a-z][a-z0-9 /+&-]{2,44}?)\s+(?:role|position|job|opening)s?\b",
    r"(?:role|position|job|opening)s?\s+(?:of|for)\s+(?:an?\s+)?([a-z][a-z0-9 /+&-]{2,44})",
    r"hiring\s+(?:for\s+)?(?:an?\s+|the\s+)?([a-z][a-z0-9 /+&-]{2,44})$",
)


def role_terms(query) -> list:
    """The ROLE the user wants companies to be hiring for, e.g.
    "hiring for an AI video creator role" -> ["ai video creator", "video creator"].

    The shorter variant is included deliberately: job titles vary ("AI Video
    Creator" vs "Video Content Creator"), and ``matching_postings`` requires ALL
    words of a term, so a single long term would miss near-identical roles.

    Returns [] when the ask has nothing to do with hiring, in which case any open
    posting counts only as generic spend signal (and is labelled as such).
    """
    fields = [str(getattr(query, "raw", "") or "")]
    fields += [str(k or "") for k in (getattr(query, "keywords", None) or [])]
    blob = " ".join(fields)
    if not re.search(r"\b(hir(?:e|ing)|recruit|job|role|opening|vacanc)", blob, re.I):
        return []

    terms = []

    def add(candidate: str) -> None:
        term = _dedupe_words(_strip_hiring_words(candidate))
        words = term.split()
        if len(term) < 3 or not words or len(words) > 5:
            return
        if term not in terms:
            terms.append(term)
        if len(words) > 2:                      # looser variant, see docstring
            short = " ".join(words[-2:])
            if short not in terms:
                terms.append(short)

    # PER FIELD, never over the concatenation. The chat model sends keywords as
    # separate phrases (["hiring AI video creator", "AI video", "video
    # generation"]); matching across their boundaries produced the nonsense term
    # "ai video creator generation", which could then falsely match an unrelated
    # posting and label it as hiring for the requested role.
    for text in fields:
        if not text.strip():
            continue
        matched_here = False
        for pat in _ROLE_PATTERNS:
            for m in re.finditer(pat, text, re.I):
                add(m.group(1))
                matched_here = True
        # A keyword can also name the role outright ("hiring AI video creator").
        # A role NOUN is required: "ai video" is a product category, not a role,
        # and treating it as one would match half of every careers page.
        if not matched_here and _ROLE_NOUN_RE.search(text):
            add(text)
    return terms[:3]


def _apollo_employee_ranges(employee_range: str) -> list:
    """Map our coarse size filter onto Apollo's "min,max" range strings."""
    r = (employee_range or "").strip().lower()
    if not r:
        return []
    digits = [int(n) for n in re.findall(r"\d+", r)]
    if r.startswith("<") and digits:
        return _apollo_bands(1, digits[0])
    if (r.endswith("+") or "more" in r) and digits:
        return _apollo_bands(digits[0], 10000)
    if len(digits) >= 2:
        return _apollo_bands(digits[0], digits[1])
    if digits:
        return _apollo_bands(max(1, digits[0] // 2), digits[0] * 2)
    return []


# Apollo only accepts its own bands, so a range is expressed as the bands it spans.
_APOLLO_BANDS = ((1, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 500),
                 (501, 1000), (1001, 2000), (2001, 5000), (5001, 10000))


def _apollo_bands(low: int, high: int) -> list:
    return [f"{a},{b}" for a, b in _APOLLO_BANDS if b >= low and a <= high]


def providers_available() -> dict:
    return {"tavily": tavily.available(), "exa": exa.available(),
            "apollo": bool(APOLLO_ORG_SEARCH_ENABLED and apollo_orgs.available())}
