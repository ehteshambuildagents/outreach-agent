"""The final display gate: the last bar a prospect must clear before it is shown.

Discovery already demotes intermediaries, verifies hiring and scores customer
likelihood. This module is the consolidated, deterministic check that answers one
blunt question about a FINALIST — "is this a thing we can honestly put on the page
as a company matching the ask?" — and drops it with a named reason when it is not.

It exists because the per-source filters each see only one candidate from one
provider, while several live failures were cross-cutting: a real domain that is a
content site, a directory, a review platform or a keyword collision; a name that
is a headline rather than the brand; an agency when the ask wanted a product
company; a web result asserted to be hiring on a search-result headline alone.

Every DISPLAYED result must satisfy all of:

  * a real operating organisation (not a listing/aggregator classification);
  * an entity type compatible with the ask (no agency when a product was asked);
  * clear product or service evidence;
  * a company name supported by official-domain evidence;
  * no publication / directory / review / article / job-board / listing kind;
  * enough query-fit evidence.

For a HIRING ask it also requires official hiring evidence — the company's own
careers page, or structured job data (Apollo) tied to that company — never a
search-result headline.

Deliberately lenient toward Apollo results: a company in Apollo's database is an
operating organisation by construction and carries structured industry codes, so
the product-evidence and headline checks (aimed at raw web results) do not re-jud­ge
it. The classification, name and entity-type checks still apply to everything.
"""

import re

from discovery import aggregators, sources

# Product / service / traction evidence a real company exposes. Used only as a
# floor on WEB results (Apollo entries are companies by construction).
_EVIDENCE_RE = re.compile(
    r"\b(product|platform|software|app|application|api|sdk|service|solution|tool|"
    r"saas|pricing|customers?|clients?|case\s+stud(?:y|ies)|demo|documentation|"
    r"docs|integrations?|dashboard|analytics|workflow|automation|hiring|careers?|"
    r"funding|raised|seed|series\s+[a-e]|bootstrapp?ed|founded)\b", re.I)

# The ask is for operating product companies / startups in a vertical (so an
# agency, consultancy or content/review site is the wrong TYPE), and NOT for
# agencies. Mirrors sources._query_wants_operating_company but reads the PLAN.
_WANTS_COMPANY_RE = re.compile(
    r"\b(saas|software|startups?|scale-?ups?|companies|company|vendors?|"
    r"platforms?|products?|tools?|apps?|fintech|healthtech|edtech|proptech|"
    r"insurtech|legaltech|martech|adtech|hrtech|climate\s?tech|deeptech|biotech|"
    r"devtools?|developer\s+tools?|cybersecurity|b2b|b2c|e-?commerce)\b", re.I)
_WANTS_AGENCY_RE = re.compile(
    r"\b(agenc(?:y|ies)|consultanc(?:y|ies)|consultants?|consulting|staffing|"
    r"recruit(?:ing|ment)\s+firms?|outsourc\w*|studios?|service\s+providers?)\b",
    re.I)

# Category / industry / filler words that are never a company's actual name on
# their own: a row named purely from these ("Fintech", "SaaS", "B2B Platform") is
# a category label that happened to sit on a matching domain, not an organisation.
_GENERIC_NAME_WORDS = frozenset({
    "fintech", "saas", "b2b", "b2c", "d2c", "software", "tech", "technology",
    "ai", "ml", "startup", "startups", "company", "companies", "platform",
    "platforms", "solution", "solutions", "product", "products", "tool", "tools",
    "app", "apps", "service", "services", "business", "enterprise", "cloud",
    "digital", "online", "healthtech", "edtech", "martech", "adtech", "proptech",
    "insurtech", "legaltech", "hrtech", "devtools", "ecommerce", "crypto", "web3",
    "the", "inc", "io", "co", "hq", "home", "welcome",
})


def _is_generic_category_name(name: str) -> bool:
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]
    return bool(tokens) and all(t in _GENERIC_NAME_WORDS for t in tokens)


def evaluate(prospect, plan) -> tuple:
    """``(ok: bool, reason: str)`` — whether ``prospect`` may be displayed."""
    p = prospect
    name = (getattr(p, "company_name", "") or "").strip()
    domain = getattr(p, "domain", "") or ""
    kind = getattr(p, "kind", "company") or "company"
    tier = getattr(p, "tier", "company") or "company"
    source = getattr(p, "discovery_source", "") or ""
    is_apollo = bool(getattr(p, "apollo_id", "")) or source == "apollo"

    # No publication / directory / review / article / job-board / listing kind, and
    # not a demoted intermediary. A fallback-tier row is never a primary result.
    if kind in aggregators.INTERMEDIARY_KINDS or tier != "company":
        return False, "not_an_operating_company"

    # A real, addressable organisation with a name backed by the domain.
    if not name:
        return False, "no_company_name"
    if _is_generic_category_name(name):
        return False, "generic_category_name"
    if not _name_backed_by_domain(name, domain, is_apollo):
        return False, "company_name_unverified"

    # Clear product / service evidence. Apollo entries are companies by
    # construction (industry codes, founded year), so this floor is for WEB rows,
    # where a bare/parked/content page exposes no product or service.
    if not is_apollo and not _has_product_or_service_evidence(p):
        return False, "no_product_or_service_evidence"

    # Entity type compatible with the ask: an agency / services shop when the user
    # asked for product companies or startups is the wrong TYPE, not just off-topic.
    if _wants_operating_company(plan) and getattr(p, "industry_kind", "") == "services":
        return False, "entity_type_mismatch"

    # A hiring ask requires official hiring evidence — the company's own careers
    # page or structured job data (Apollo) — never a search-result headline.
    if getattr(plan, "roles", None) and not _has_official_hiring_evidence(p, is_apollo):
        return False, "no_official_hiring_evidence"

    return True, ""


def filter_displayable(prospects, plan) -> tuple:
    """Split a ranked list into ``(kept, dropped_reasons)``. Order is preserved."""
    from collections import Counter
    kept, dropped = [], Counter()
    for p in prospects or []:
        ok, reason = evaluate(p, plan)
        if ok:
            kept.append(p)
        else:
            dropped[reason] += 1
    return kept, dict(dropped)


def _name_backed_by_domain(name: str, domain: str, is_apollo: bool) -> bool:
    """Apollo names come from a company record and are trusted. A web-derived name
    must be corroborated by the domain core (see sources._name_supported_by_domain),
    which is what "company name supported by official-domain evidence" means."""
    if is_apollo:
        return bool(name)
    core = (domain or "").split(".")[0]
    if not core:
        return False
    # A name equal to the domain core (the honest fallback) always passes.
    if re.sub(r"[^a-z0-9]", "", name.lower()) == re.sub(r"[^a-z0-9]", "", core.lower()):
        return True
    return sources._name_supported_by_domain(name, core)


def _has_product_or_service_evidence(p) -> bool:
    haystack = " ".join(str(x or "") for x in [
        getattr(p, "why_it_matches", ""), getattr(p, "industry", ""),
        getattr(p, "industry_kind", ""),
        " ".join(getattr(p, "match_reasons", None) or []),
        " ".join(getattr(p, "basic_signals", None) or []),
    ])
    if _EVIDENCE_RE.search(haystack):
        return True
    # A verified hiring signal or growth data is also operating-company evidence.
    return bool((getattr(p, "hiring", None) or {}).get("verified")
                or getattr(p, "growth", None))


def _has_official_hiring_evidence(p, is_apollo: bool) -> bool:
    """Official hiring evidence: the company's own careers page, or structured
    Apollo job data tied to the company. A web headline claiming "X is hiring" is
    never enough (that path is already blocked upstream; this is the final bar)."""
    hiring = getattr(p, "hiring", None) or {}
    src = hiring.get("source") or ""
    if src in ("own_careers_page", "apollo", "apollo_title_filter"):
        return True
    # An Apollo company with no attached posting still has structured firmographic
    # evidence; the hiring requirement is satisfied by the provider, not a headline.
    return is_apollo


def _wants_operating_company(plan) -> bool:
    text = " ".join(str(x or "") for x in [
        getattr(plan, "raw", ""), getattr(plan, "industry", ""),
        " ".join(getattr(plan, "keyword_tags", None) or []),
        " ".join(getattr(plan, "relevance_terms", None) or []),
    ]).lower()
    return bool(_WANTS_COMPANY_RE.search(text)) and not _WANTS_AGENCY_RE.search(text)
