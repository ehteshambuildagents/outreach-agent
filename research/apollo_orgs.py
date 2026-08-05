"""Apollo COMPANY-side provider: organization search + verified job postings.

This is the company counterpart to [research/apollo.py], which only does
person-level People Match. Two endpoints, both read-only:

  * ``search_organizations`` — POST /api/v1/mixed_companies/search. A curated
    COMPANY database, so unlike a web search it cannot return a job board as a
    "company". This is why it is the primary candidate source: asking the web
    "who is hiring an AI video creator" returns Indeed; asking Apollo for AI
    video companies returns AI video companies.

  * ``job_postings`` — GET /api/v1/organizations/{id}/job_postings. Structured,
    DATED postings straight from the employer (title, city, url, posted_at,
    last_seen_at). This replaces scraping job boards for hiring signal: instead
    of trusting that a board listed a company, we read the company's own live
    postings and can quote the title and the date.

Cost and safety, matching every other paid provider here:
  * key from APOLLO_API_KEY, never logged;
  * every call goes through providers_common.request_json (per-user spend caps,
    metering under provider "apollo", transient-only retries, None on failure);
  * results cached in Redis by exact request for APOLLO_CACHE_TTL_SECONDS, so a
    repeated or paginated query does not re-hit the paid API;
  * job-posting lookups are per-company and therefore bounded by the caller
    (see APOLLO_JOB_POSTING_LOOKUPS) to the finalists only;
  * never raises: unavailable/error/no-match are typed status dicts.

Industry classification is deterministic and free: Apollo returns SIC/NAICS
codes, and software-publisher codes are a far better "is this a real B2B software
company" signal than keyword-matching a web snippet.
"""

import hashlib
import json
import logging
import re

from config.settings import (
    APOLLO_CACHE_TTL_SECONDS,
    APOLLO_ORG_SEARCH_PER_PAGE,
)
from research.providers_common import get_key, request_json

log = logging.getLogger("research.apollo_orgs")

_SEARCH_ENDPOINT = "https://api.apollo.io/api/v1/mixed_companies/search"
_POSTINGS_ENDPOINT = "https://api.apollo.io/api/v1/organizations/{org_id}/job_postings"
_ENRICH_ENDPOINT = "https://api.apollo.io/api/v1/organizations/enrich"
_ENV = "APOLLO_API_KEY"
_PROVIDER = "apollo"
_CACHE_PREFIX = "apollo_orgs:"

# SIC / NAICS codes that mark a PRODUCT software company (what "B2B SaaS" means
# in practice) versus a services shop. Verified against live responses: Storykit
# (an AI video SaaS) carries SIC 7375 + NAICS 51321; IncoreSoft carries SIC 7372.
_SOFTWARE_SIC = frozenset({"7372", "7375", "7379", "7371"})
_SOFTWARE_NAICS_PREFIXES = ("5132", "51321", "511210", "5112", "518210", "51821")
# Codes that usually mean consulting / custom development / staffing rather than
# a product. Kept separate so a query for SaaS can demote them.
_SERVICES_SIC = frozenset({"7371", "8742", "7361", "7363"})
_SERVICES_NAICS_PREFIXES = ("5415", "54151", "541611", "5613", "56131")


def available() -> bool:
    """True only when APOLLO_API_KEY is configured."""
    return bool(get_key(_ENV))


# ── Organization search ────────────────────────────────────────────────────
def search_organizations(*, keywords=None, job_titles=None, employee_ranges=None,
                         locations=None, page=1, per_page=None) -> dict:
    """Search Apollo's company database. NEVER raises. Returns:

        {"status": "ok"|"unavailable"|"error"|"empty",
         "organizations": [...], "total": int, "reason"?: str}

    Two independent axes, and which one you use decides what you get:

    * ``job_titles`` -> ``q_organization_job_titles``: companies with a LIVE
      POSTING for those titles, in ANY industry. Verified against the live API:
      28.2M companies total, 4,135 with an active "video editor" posting. This is
      the axis for "who is hiring X", because a company does not have to sell AI
      video to hire someone to make it.
    * ``keywords`` -> ``q_organization_keyword_tags``: companies whose PRODUCT is
      in that category. Apollo ORs multiple tags, so keep the list short and
      never mix a generic tag with a specific one.

    Passing both narrows to companies that are in the category AND hiring.
    ``employee_ranges`` are Apollo's own "min,max" band strings.
    """
    if not available():
        return _result("unavailable",
                       reason="Apollo isn't configured (set APOLLO_API_KEY).")
    tags = [k for k in (_clean_list(keywords)) if k]
    titles = [t for t in (_clean_list(job_titles)) if t]
    if not tags and not titles:
        return _result("error", reason="No keywords or job titles to search with.")

    body = {
        "page": max(1, int(page or 1)),
        "per_page": min(int(per_page or APOLLO_ORG_SEARCH_PER_PAGE), 100),
    }
    if tags:
        body["q_organization_keyword_tags"] = tags
    if titles:
        body["q_organization_job_titles"] = titles
    ranges = _clean_list(employee_ranges)
    if ranges:
        body["organization_num_employees_ranges"] = ranges
    locs = _clean_list(locations)
    if locs:
        body["organization_locations"] = locs

    cached = _cache_get(_SEARCH_ENDPOINT, body)
    if cached is not None:
        data = cached
    else:
        data = request_json("POST", _SEARCH_ENDPOINT, provider=_PROVIDER,
                            headers=_headers(), json_body=body)
        if data is None:
            return _result("error", reason="Apollo organization search failed.")
        _cache_put(_SEARCH_ENDPOINT, body, data)

    orgs = [o for o in (data.get("organizations") or []) if isinstance(o, dict)]
    # Apollo splits results: `accounts` are orgs already in the workspace CRM.
    orgs += [o for o in (data.get("accounts") or []) if isinstance(o, dict)]
    total = ((data.get("pagination") or {}).get("total_entries")) or len(orgs)
    if not orgs:
        return _result("empty", total=0,
                       reason="Apollo has no companies matching that search.")
    log.info("apollo org search tags=%s titles=%s page=%s returned=%s total=%s",
             tags, titles, body["page"], len(orgs), total)
    return _result("ok", organizations=[organization(o) for o in orgs], total=total)


def organization(org: dict) -> dict:
    """Minimize an Apollo organization to the fields we rank and display with."""
    domain = _domain(org.get("primary_domain") or org.get("website_url") or "")
    return {
        "apollo_id": org.get("id"),
        "name": (org.get("name") or "").strip(),
        "domain": domain,
        "website": f"https://{domain}" if domain else "",
        "linkedin_url": org.get("linkedin_url") or "",
        "twitter_url": org.get("twitter_url") or "",
        "founded_year": org.get("founded_year"),
        "sic_codes": [str(c) for c in (org.get("sic_codes") or [])],
        "naics_codes": [str(c) for c in (org.get("naics_codes") or [])],
        # Headcount growth is Apollo's own measure and a real expansion signal.
        "headcount_growth_6mo": _num(org.get("organization_headcount_six_month_growth")),
        "headcount_growth_12mo": _num(org.get("organization_headcount_twelve_month_growth")),
        # SCALE signals. This search endpoint does NOT return an employee count, so
        # these are how we tell a founder-led company apart from a Fortune 500: a
        # role-title search returns Amazon and Microsoft (they post the most jobs),
        # and neither is a plausible customer for a founder's AI SDR. Publicly
        # traded or nine-figure-revenue companies are demoted in scoring.
        # The TICKER is the only trustworthy "is this listed" signal. Apollo fills
        # `publicly_traded_exchange` speculatively: it returns "nasdaq" for OpenAI
        # and Anthropic, both private, and calling them public in the UI is a
        # false claim. Scale is still caught by revenue below.
        "is_public": bool(org.get("publicly_traded_symbol")),
        "revenue": _num(org.get("organization_revenue")),
        "owned_by": org.get("owned_by_organization_id") or "",
        # Apollo buying-intent, when the workspace has intent data enabled.
        "intent": (org.get("intent_signal_account") or None),
        "intent_strength": org.get("intent_strength") or None,
        "has_intent": bool(org.get("has_intent_signal_account")),
    }


def industry_kind(org: dict) -> str:
    """``"software"`` | ``"services"`` | ``"unknown"`` from SIC/NAICS codes.

    Deterministic and free (the codes are already in the search response), and a
    much better B2B-SaaS test than looking for the word "platform" in a snippet.
    Software wins ties: a product company that also consults is still a product
    company, and 7371/5415 appear in both sets.
    """
    sic = set(org.get("sic_codes") or [])
    naics = list(org.get("naics_codes") or [])
    software = bool(sic & (_SOFTWARE_SIC - _SERVICES_SIC)) or any(
        c.startswith(_SOFTWARE_NAICS_PREFIXES) for c in naics)
    services = bool(sic & _SERVICES_SIC) or any(
        c.startswith(_SERVICES_NAICS_PREFIXES) for c in naics)
    if software:
        return "software"
    if services:
        return "services"
    return "unknown"


# ── Verified job postings ──────────────────────────────────────────────────
def job_postings(org_id: str) -> dict:
    """Live job postings for one Apollo organization. NEVER raises. Returns:

        {"status": "ok"|"empty"|"unavailable"|"error", "postings": [...]}

    Each posting: {title, city, state, country, url, posted_at, last_seen_at}.
    Paid per call, so callers run this on FINALISTS only.
    """
    if not available():
        return _result("unavailable", postings=[],
                       reason="Apollo isn't configured.")
    oid = (str(org_id or "")).strip()
    if not oid:
        return _result("error", postings=[], reason="No organization id.")

    url = _POSTINGS_ENDPOINT.format(org_id=oid)
    cached = _cache_get(url, {})
    if cached is not None:
        data = cached
    else:
        data = request_json("GET", url, provider=_PROVIDER, headers=_headers())
        if data is None:
            return _result("error", postings=[],
                           reason="Apollo job-posting lookup failed.")
        _cache_put(url, {}, data)

    raw = data.get("organization_job_postings") or data.get("job_postings") or []
    postings = [_posting(p) for p in raw if isinstance(p, dict)]
    if not postings:
        return _result("empty", postings=[], reason="No live postings found.")
    return _result("ok", postings=postings)


def matching_postings(postings, role_terms) -> list:
    """Postings whose TITLE is actually for one of ``role_terms``. Pure; unit-tested.

    A title matches a term when EITHER:
      * the full term is a substring ("video creator" in "Senior Video Creator"), or
      * the term's HEAD NOUN is the head of the title AND every other word is
        present ("video creator" matches "Senior Video Content Creator").

    The head-noun rule is what stops a scattered all-words match from firing: a
    live search returned Amazon at the top for "AI video creator" because its
    posting "Brand Creator Marketing Manager, Amazon MGM Studios + Prime Video"
    happened to contain both "creator" and "video" in unrelated places. That title
    is a MANAGER role, not a creator role, so it must not count.
    """
    terms = [t for t in _clean_list(role_terms) if t]
    if not terms:
        return list(postings or [])
    out = []
    for p in postings or []:
        title = (p.get("title") or "").lower()
        if not title:
            continue
        title_words = re.findall(r"[a-z0-9]+", title)
        head = title_words[-1] if title_words else ""
        for term in terms:
            words = [w for w in re.findall(r"[a-z0-9]+", term)]
            if not words:
                continue
            role_head = words[-1]
            substring = term in title
            head_match = (role_head == head
                          and all(w in title_words for w in words[:-1]))
            if substring or head_match:
                out.append(p)
                break
    return out


# ── Organization enrichment (funding evidence) ─────────────────────────────
def enrich(domain: str) -> dict:
    """Enrich ONE company by domain. NEVER raises. Returns:

        {"status": "ok"|"empty"|"unavailable"|"error",
         "name": str, "domain": str,
         "funding": {"latest_stage": str, "total": str,
                     "events": [{"type","date","amount","currency","news_url",
                                 "investors"}]}}

    This is the funding counterpart to ``job_postings``: the search endpoint does
    not return funding data, so verifying a "raised a seed round" constraint means
    reading the company's structured funding history — the round TYPE, DATE, AMOUNT
    and, crucially, a SOURCE URL for the announcement. Paid per company, so callers
    run it on FINALISTS only. Straight off Apollo's own record, no inference.
    """
    if not available():
        return _result("unavailable", reason="Apollo isn't configured.")
    dom = _domain(domain)
    if not dom:
        return _result("error", reason="No domain to enrich.")

    params = {"domain": dom}
    cached = _cache_get(_ENRICH_ENDPOINT, params)
    if cached is not None:
        data = cached
    else:
        data = request_json("GET", _ENRICH_ENDPOINT, provider=_PROVIDER,
                            headers=_headers(), params=params)
        if data is None:
            return _result("error", reason="Apollo enrichment failed.")
        _cache_put(_ENRICH_ENDPOINT, params, data)

    org = data.get("organization") or {}
    if not org:
        return _result("empty", reason="Apollo has no record for that domain.")
    out = _result("ok")
    out["name"] = (org.get("name") or "").strip()
    out["domain"] = _domain(org.get("primary_domain") or org.get("website_url") or dom)
    out["funding"] = funding_of(org)
    return out


def funding_of(org: dict) -> dict:
    """Normalise an Apollo org's funding history to typed evidence."""
    events = []
    for e in (org.get("funding_events") or []):
        if not isinstance(e, dict):
            continue
        events.append({
            "type": (e.get("type") or "").strip(),
            "date": (e.get("date") or "")[:10],
            "amount": (e.get("amount") or "").strip() if e.get("amount") else "",
            "currency": (e.get("currency") or "").strip() if e.get("currency") else "",
            "news_url": (e.get("news_url") or "").strip(),
            "investors": (e.get("investors") or "").strip() if e.get("investors") else "",
        })
    return {
        "latest_stage": (org.get("latest_funding_stage") or "").strip(),
        "total": (org.get("total_funding_printed") or "").strip()
        if org.get("total_funding_printed") else "",
        "latest_date": (org.get("latest_funding_round_date") or "")[:10],
        "events": events,
    }


# ── Helpers ────────────────────────────────────────────────────────────────
def _posting(p: dict) -> dict:
    return {
        "title": (p.get("title") or "").strip(),
        "city": p.get("city") or "",
        "state": p.get("state") or "",
        "country": p.get("country") or "",
        "url": p.get("url") or "",
        "posted_at": p.get("posted_at") or "",
        "last_seen_at": p.get("last_seen_at") or "",
    }


def _result(status, *, organizations=None, postings=None, total=None,
            reason=None) -> dict:
    out = {"status": status}
    if organizations is not None or postings is None:
        out["organizations"] = organizations or []
    if postings is not None:
        out["postings"] = postings
    if total is not None:
        out["total"] = total
    if reason:
        out["reason"] = reason
    return out


def _headers() -> dict:
    return {"X-Api-Key": get_key(_ENV), "Content-Type": "application/json",
            "Cache-Control": "no-cache"}


def _clean_list(values) -> list:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for v in values:
        s = str(v or "").strip()
        if s and s.lower() not in [o.lower() for o in out]:
            out.append(s)
    return out


def _num(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _domain(value: str) -> str:
    text = (value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0].strip()
    if text.startswith("www."):
        text = text[4:]
    return text


# ── Redis cache (best-effort; a cache miss is just a paid call) ─────────────
def _cache_key(url: str, body: dict) -> str:
    blob = json.dumps({"u": url, "b": body}, sort_keys=True, default=str)
    return _CACHE_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _cache_get(url: str, body: dict):
    if APOLLO_CACHE_TTL_SECONDS <= 0:
        return None
    try:
        from automation import redis
        raw = redis.get(_cache_key(url, body))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - cache is an optimisation, never a dependency
        return None


def _cache_put(url: str, body: dict, data: dict) -> None:
    if APOLLO_CACHE_TTL_SECONDS <= 0:
        return
    try:
        from automation import redis
        redis.set(_cache_key(url, body), json.dumps(data, default=str),
                  ex=APOLLO_CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
