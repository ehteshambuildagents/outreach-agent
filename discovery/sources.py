"""Search + candidate extraction — reuses the existing Tavily & Exa providers.

The providers return web results; this turns them into candidate companies
deterministically: normalize the domain, drop aggregators / media / directories
(a listicle is not a company), derive a name, and read light SIGNALS out of the
title + snippet (industry, location, funding stage, hiring, tech). No LLM, no new
API, and it never raises — a provider that's down just contributes nothing.
"""

import concurrent.futures
from collections import Counter
import logging
import re
from urllib.parse import urlparse

from config.settings import (
    DISCOVERY_PROVIDER_POOL,
    EXCLUDED_RESOLUTION_DOMAINS,
)
from discovery.models import Prospect, registrable_domain
from research import exa, tavily

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
        # Reject obviously non-name fragments (listicle headings).
        if 1 <= len(frag.split()) <= 6 and not re.search(
                r"\b(top|best|list|companies|startups|\d{2,})\b", frag.lower()):
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


def _build(raw: dict, query, source: str):
    """Turn one search result into a Prospect (or None if it isn't a company)."""
    url = raw.get("url") or ""
    domain = registrable_domain(url)
    if _reject_reason(raw, query):
        return None
    title = raw.get("title") or ""
    content = raw.get("content") or ""
    text = f"{title}\n{content}"
    low = text.lower()

    signals = _signals(text, query)
    # Deterministic confidence from evidence overlap with the ICP.
    conf, matched = 0.30, []
    if query.industry and query.industry.lower() in low:
        conf += 0.15
        matched.append(query.industry)
    if query.keywords:
        hit = [k for k in query.keywords if k in low]
        if hit:
            conf += min(0.20, 0.08 * len(hit))
            matched.append("keywords: " + ", ".join(hit))
    if query.location and query.location.lower() in low:
        conf += 0.12
        matched.append(query.location)
    if query.funding_stage:
        st = query.funding_stage.lower()
        if any(st in s for s in signals) or st in low:
            conf += 0.12
            matched.append(query.funding_stage)
    if source == "exa":               # Exa surfaces company homepages more often
        conf += 0.08
    if any(part in (urlparse(url).path or "").lower() for part in _BUSINESS_PATH_PARTS):
        conf += 0.04
    if _SAAS_PRODUCT_RE.search(text):
        conf += 0.04
    conf = min(1.0, conf)

    stage = query.funding_stage or next(
        (_STAGE_FROM_SIGNAL[s] for s in signals if s in _STAGE_FROM_SIGNAL), "unknown")
    industry = query.industry or _infer_industry(signals)
    why = ("Matches " + "; ".join(matched) if matched
           else f"Surfaced by search for “{query.search_string()}”")

    return Prospect(
        company_name=_name_from(title, domain),
        website=f"https://{domain}",
        domain=domain,
        industry=industry,
        location=query.location or "",
        estimated_company_size=_size_from(query.employee_range),
        estimated_stage=stage,
        confidence=conf,
        why_it_matches=why,
        discovery_source=source,
        basic_signals=signals,
        query=query.search_string(),
    )


def _infer_industry(signals) -> str:
    for tag in ("fintech", "saas", "ai", "devtools", "ecommerce", "healthtech",
                "b2b"):
        if tag in signals:
            return tag
    return ""


def _size_from(employee_range: str) -> str:
    return employee_range.strip() if employee_range else "unknown"


def search_candidates(query, pool_size: int = None) -> list:
    """Run the configured search providers CONCURRENTLY and return candidate
    Prospects (unranked, may contain duplicates). Providers that are unavailable
    or error simply contribute nothing (graceful fallback)."""
    pool = pool_size or DISCOVERY_PROVIDER_POOL
    q = query.search_string()
    if not q:
        return []

    def run_tavily():
        try:
            return [("tavily", r) for r in tavily.search(q, max_results=pool)]
        except Exception:  # noqa: BLE001
            log.info("tavily discovery search failed")
            return []

    def run_exa():
        try:
            return [("exa", r) for r in exa.search(q, max_results=pool)]
        except Exception:  # noqa: BLE001
            log.info("exa discovery search failed")
            return []

    raws = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool_ex:
        for fut in (pool_ex.submit(run_exa), pool_ex.submit(run_tavily)):
            raws.extend(fut.result())

    out = []
    rejected = Counter()
    for source, raw in raws:
        reason = _reject_reason(raw, query)
        if reason:
            rejected[reason] += 1
            continue
        p = _build(raw, query, source)
        if p is not None:
            out.append(p)
    log.info(
        "discovery_candidates query=%r raw_candidates=%s rejected_by_reason=%s valid_company_prospects=%s",
        q, len(raws), dict(rejected), len(out),
    )
    return out


def providers_available() -> dict:
    return {"tavily": tavily.available(), "exa": exa.available()}
