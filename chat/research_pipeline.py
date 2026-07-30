"""Chat-directed research: one plain-language ask -> a scored prospect list.

This is a NEW ENTRY POINT into the pipeline the product already has — Prospect
Discovery -> Research -> Qualification — not new agent logic. The user describes
who to find/evaluate ("SaaS founders hiring an SDR", or "here's my list of 50
companies"); this runs the existing agents over those companies and returns a
scored, browsable list that is valuable ON ITS OWN, with no campaign or email
required.

Thin by design: this module only (1) fans the existing agents over a bounded set
of companies and (2) FORMATS each result for the browse UX. It invents nothing and
imports no writer/guard logic. Formatting is deterministic (no extra model call):

  * ``preview``  — ONE plain-language paragraph: the headline finding + the score.
                   This is what shows collapsed, so the list stays skimmable.
  * ``detail``   — the full trail revealed on expand: every finding with its
                   SOURCE + CONFIDENCE (the existing enrichment schema), the pages
                   used, and the reasoning behind the fit score.

Every run is telemetry-scoped exactly like the other pipeline runs, so the real
research/qualify model calls underneath are attributed and cost-tracked.
"""

import re
import uuid

from agents import qualification
from agents.research import research_company
from config.settings import RESEARCH_LIST_MAX
from discovery import engine as discovery_engine
from discovery.models import DiscoveryQuery, registrable_domain

# Draft targets offered per researched prospect (Part 2 safe channels + email).
# Presence in this list is an OFFER only — nothing here drafts or posts anything;
# the user clicks an action, which triggers the writer/channel tools separately.
PROSPECT_ACTIONS = ("draft_email", "draft_x_reply", "draft_reddit_comment",
                    "draft_hn_reply", "draft_contact_form")

# Human labels for the qualification recommendations (plain language for previews).
_REC_HUMAN = {
    qualification.HIGH_PRIORITY: "pursue now (strong fit + buying signals)",
    qualification.CONTINUE: "worth pursuing",
    qualification.RESEARCH_MORE: "needs more research to judge",
    qualification.REJECT: "probably skip (weak fit)",
}

# Evidence nodes worth surfacing as findings, in priority order, with a label.
_FINDING_NODES = (
    ("what_they_do", "What they do"),
    ("target_customer", "Who they serve"),
    ("metrics_or_traction", "Traction"),
    ("notable_customers", "Notable customers"),
    ("recent_focus", "Recent focus"),
    ("competitive_positioning", "Positioning"),
    ("business_model", "Business model"),
)


def _telemetry():
    """Return (scope, record_event) or (nullcontext factory, no-op) if telemetry
    isn't importable — logging must never break the feature."""
    try:
        from telemetry import record_event, scope
        return scope, record_event
    except Exception:  # noqa: BLE001
        import contextlib

        def _noop_event(*a, **k):
            return None

        def _noop_scope(*a, **k):
            return contextlib.nullcontext()

        return _noop_scope, _noop_event


# ── Entry point A: discover from a plain-language / structured ICP ─────────
def discover_leads(owner: str, query, *, limit=None, exclude_domains=None, progress=None):
    """Run the existing Discovery agent -> a list of lead dicts (company+website).
    Returns (status, leads, reason, has_more). Never raises. ``progress`` streams
    the real discovery stages to a caller (see discovery.engine.discover)."""
    if isinstance(query, DiscoveryQuery):
        q = query
    else:
        q = DiscoveryQuery(raw=str(query or ""),
                           limit=limit or RESEARCH_LIST_MAX)
    if limit:
        q.limit = max(1, int(limit))
    try:
        result = discovery_engine.discover(owner or "anon", q,
                                           exclude_domains=exclude_domains or [],
                                           progress=progress)
    except Exception:  # noqa: BLE001 - discovery is designed not to raise
        return "error", [], "Prospect discovery couldn't run just now.", False
    if result.status == "error":
        return "error", [], result.reason, False
    if result.status == "empty":
        return "empty", [], result.reason, False
    leads = [{"company_name": p.company_name, "website": p.website,
              "discovery": p.public()} for p in result.prospects]
    return "ok", leads, "", bool(result.has_more)


# ── Discovery results -> the same browsable card shape ─────────────────────
def discovery_entries(prospects) -> list:
    """Format DISCOVERED (not yet researched) companies as card entries.

    Reuses the shape ``research_and_qualify`` already returns, so the existing
    prospects card renders both without a second component. The difference is
    honest and visible: ``status`` is ``"discovered"``, the number is a MATCH
    confidence rather than a fit score, and the only offered action is to research
    the company — nothing here has crawled a site or scored a lead yet.

    ``prospects`` are discovery ``Prospect.public()`` dicts.
    """
    entries = []
    for p in prospects or []:
        if not isinstance(p, dict):
            continue
        if _malformed_prospect(p):
            # A scraped job posting / listing fragment that leaked in as a
            # "company" is never shown — it isn't a prospect anyone can sell to.
            continue
        confidence = float(p.get("confidence") or 0)
        hiring = p.get("hiring") or None
        reasons = [r for r in (p.get("match_reasons") or []) if r]
        band = _qualification_band(confidence, p.get("tier"), hiring)
        entries.append({
            "company": p.get("company_name") or "",
            "website": p.get("website") or "",
            "status": "discovered",
            "score": int(round(confidence * 100)),
            "fit_level": p.get("tier") or "company",
            # Honest Strong / Possible / Weak label the card shows instead of a bare
            # percentage, so a 22% match reads as "Weak", never as a recommendation.
            "band": band,
            "priority": "none",
            "recommendation": "",
            # Only a genuinely strong candidate is "recommended". A company hiring
            # for a different role can never clear this bar (see _qualification_band).
            "recommended": band == "strong",
            "score_reason": p.get("why_it_matches") or "",
            "preview": _discovery_preview(p, hiring, reasons),
            "actions": ["research_prospect"],
            "detail": {
                "what_they_do": None,
                "match_reasons": reasons,
                "hiring": hiring,
                "recent_activity": p.get("recent_activity") or None,
                "growth": p.get("growth") or None,
                "kind": p.get("kind") or "company",
                "tier": p.get("tier") or "company",
                "strongest_signals": p.get("basic_signals") or [],
                "sources": _discovery_sources(p),
                # The customer-likelihood factor breakdown, so the card can show a
                # plain "Why this ranked here" checklist instead of a bare number.
                "score_breakdown": p.get("score") or None,
                "is_public": bool(p.get("is_public")),
                "annual_revenue": p.get("annual_revenue"),
                "industry_kind": p.get("industry_kind") or "",
            },
        })
    return entries


# Fragments that mean "this row is a job posting / listing, not a company". Kept
# multi-word so a legitimate company name ("Hiring.com", "Career Karma") is safe.
_JUNK_NAME_MARKERS = (
    "we're hiring", "we are hiring", "now hiring", "job description",
    "apply now", "careers at", "job at ", "full-time", "part-time",
    "view job", "job opening", "see all jobs", "no title",
)

# ── Prospect entity validation ─────────────────────────────────────────────
# A discovered "company" is only ever shown if it names a REAL, addressable
# business. Search/discovery occasionally leaks JOB TITLES, INDUSTRY/CATEGORY
# PHRASES, and SEARCH-QUERY FRAGMENTS into the company slot — e.g. the exact
# junk found in live QA: company_name="B2B SaaS" (dover.com) and
# company_name="Senior SDR Software Engineer" (tcibr.com). These have valid
# domains and short names, so structure alone (length / URL shape / junk
# markers) can't catch them.
#
# We classify the NAME's tokens instead. A name whose EVERY meaningful word is
# generic vocabulary — an industry/category word, a job-title word, or a query
# filler — is not a company. A single distinctive/brand token is enough to keep
# a row: "Karma" in "Career Karma", "AG" in "Software AG", "Layer" in "Sales
# Layer", "Greenhouse" in "Greenhouse Software", or a bare brand like "HubSpot"
# / "Salesforce". That is precisely how a legitimate name that merely CONTAINS
# Careers/Hiring/Software is preserved while a pure category/role phrase is not.

# Legal-entity suffixes: a strong positive signal of a registered company. Their
# presence alone keeps a row (protects "Software AG", "Acme Inc", "Foo GmbH").
_LEGAL_SUFFIX = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "gmbh", "ag", "sa", "nv", "bv", "plc", "pbc", "lp", "llp", "pty", "oy",
    "ab", "as", "srl", "spa", "kg", "kk", "aps", "sas", "sarl",
}

# Job-title vocabulary: seniority modifiers, role functions, and role head nouns.
# A name built ENTIRELY from these (plus connectors) is a role, not a company.
_SENIORITY = {"senior", "junior", "jr", "sr", "lead", "principal", "staff",
              "chief", "head", "entry", "mid", "level", "global", "regional",
              "vp", "svp", "evp", "avp"}
_ROLE_FUNCTION = {"sales", "marketing", "growth", "business", "development",
                  "account", "customer", "product", "engineering", "software",
                  "data", "revenue", "operations", "ops", "people", "talent",
                  "content", "brand", "demand", "field", "inside", "outbound",
                  "technical", "partnerships", "community", "success",
                  "acquisition", "generation", "enablement"}
_ROLE_NOUN = {"engineer", "developer", "representative", "rep", "executive",
              "manager", "director", "analyst", "designer", "recruiter",
              "coordinator", "specialist", "consultant", "officer", "intern",
              "administrator", "architect", "scientist", "technician", "agent",
              "advisor", "strategist", "generalist", "assistant", "salesperson",
              "seller", "sdr", "bdr", "ae", "csm", "am"}

# Industry / market descriptors and the head nouns that turn a descriptor into a
# "category phrase" ("SaaS companies", "Software company", "Startups hiring").
_INDUSTRY = {"saas", "b2b", "b2c", "d2c", "software", "tech", "technology",
             "fintech", "ai", "ml", "healthtech", "edtech", "ecommerce",
             "martech", "adtech", "cybersecurity", "security", "biotech",
             "cloud", "enterprise", "hardware", "digital", "mobile", "crypto",
             "web3", "devtools", "hr", "insurtech", "proptech", "legaltech",
             "logistics", "manufacturing", "retail", "media", "gaming"}
_CATEGORY_HEAD = {"companies", "company", "startups", "startup", "businesses",
                  "business", "firms", "firm", "vendors", "vendor", "brands",
                  "brand", "organizations", "organization", "orgs", "providers",
                  "provider", "platforms", "platform", "tools", "solutions",
                  "agencies", "agency", "players", "sector", "industry",
                  "market", "space", "leaders", "list", "directory", "category"}
# Words that only ever appear as scaffolding around a search query, never as a
# company's actual name on their own.
_QUERY_FILLER = {"hiring", "hire", "hires", "jobs", "job", "top", "best",
                 "leading", "growing", "fastest", "new", "emerging", "popular",
                 "remote", "open", "roles", "role", "positions", "position",
                 "openings", "opening", "vacancies", "careers", "career",
                 "for", "with", "that", "who", "of", "and", "the", "a", "an",
                 "in", "at", "to", "or"}

_GENERIC_TOKENS = (_SENIORITY | _ROLE_FUNCTION | _ROLE_NOUN | _INDUSTRY
                   | _CATEGORY_HEAD | _QUERY_FILLER)


def _name_tokens(name: str) -> list:
    """Lowercased alphanumeric word tokens of a company name."""
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def _is_generic_token(t: str) -> bool:
    """True when a token is generic vocabulary — matched whole (never as a
    substring), with a light plural fold so 'reps'/'roles'/'sdrs' count. The fold
    only applies to stems of 3+ chars, so a short brand/acronym like 'Srs' is not
    mistaken for the plural of 'sr' (senior)."""
    if t in _GENERIC_TOKENS:
        return True
    if t.endswith("s") and len(t) - 1 >= 3 and t[:-1] in _GENERIC_TOKENS:
        return True
    return False


def _looks_like_non_company(name: str) -> bool:
    """True when a name denotes a JOB TITLE, an INDUSTRY/CATEGORY phrase, or a
    SEARCH-QUERY fragment rather than a real company.

    Fires only when EVERY meaningful token is generic vocabulary — one
    distinctive/brand token keeps the row, so a legitimate name that merely
    contains Careers/Hiring/Software survives ("Career Karma", "Greenhouse
    Software", "Sales Layer"). A legal-entity suffix (Inc/LLC/AG/GmbH…) is proof
    of a real company and always keeps the row ("Software AG")."""
    tokens = _name_tokens(name)
    if not tokens:
        return False  # emptiness is handled by the missing-name check
    if any(t in _LEGAL_SUFFIX for t in tokens):
        return False
    return all(_is_generic_token(t) for t in tokens)


def _malformed_prospect(p: dict) -> bool:
    """True when a discovered row is not a real, addressable company: missing a
    name or a website, a website that isn't even a plausible URL/domain, an
    over-long scraped headline rather than a name, an obvious job-posting/listing
    fragment, or a name that is purely a job title / category / query phrase
    (see ``_looks_like_non_company``). These are dropped, never shown."""
    name = (p.get("company_name") or "").strip()
    site = (p.get("website") or p.get("domain") or "").strip()
    if not name or not site:
        return True
    if "." not in site and "://" not in site:   # not even a plausible URL/domain
        return True
    if len(name) > 80:                            # a headline, not a company name
        return True
    low = name.lower()
    if any(marker in low for marker in _JUNK_NAME_MARKERS):
        return True
    return _looks_like_non_company(name)


def _qualification_band(confidence: float, tier, hiring) -> str:
    """Turn a customer-likelihood confidence into an honest Strong / Possible / Weak
    label. A fallback source (job board / aggregator) or a low score is Weak; a
    company whose only hiring signal is for a DIFFERENT role than asked is capped at
    Possible, so "hiring, but not that role" is never dressed up as a strong match."""
    if tier and tier != "company":
        return "weak"
    if confidence < 0.35:
        return "weak"
    band = "strong" if confidence >= 0.55 else "possible"
    # A verified-but-non-matching hiring signal can never be "strong".
    if isinstance(hiring, dict) and hiring.get("verified") and hiring.get("match") != "role":
        band = "possible" if band == "strong" else band
    return band


def _discovery_preview(p, hiring, reasons) -> str:
    """One plain sentence: what stood out, plus the verified hiring line if there
    is one. Deliberately short — the card holds the rest."""
    bits = []
    if hiring and hiring.get("summary"):
        bits.append(hiring["summary"].rstrip("."))
    seen = {b.strip().lower() for b in bits}
    # The hiring line is often also the lead match reason; don't print it twice.
    lead = next((r for r in reasons if not r.startswith("In Apollo")
                 and r.strip().lower() not in seen), "")
    if lead:
        bits.append(lead.rstrip("."))
    elif not bits and p.get("why_it_matches"):
        bits.append(str(p["why_it_matches"]).rstrip("."))
    if not bits:
        bits.append("Matched your search")
    return ". ".join(bits) + "."


def _discovery_sources(p) -> list:
    """Which providers found this company, as the card's {domain,url} chips. The
    provider name goes in ``domain`` so corroboration is visible at a glance."""
    out = []
    for src in p.get("sources") or []:
        if not isinstance(src, dict):
            continue
        provider = src.get("provider") or ""
        if provider and not any(o["domain"] == provider for o in out):
            out.append({"domain": provider, "url": src.get("url") or p.get("website") or ""})
    return out


# ── Entry point B (and the core): research + qualify a list of companies ───
def research_and_qualify(leads, *, icp=None, limit=None, user_id=None,
                         research_fn=None, qualify_fn=None, resolve_fn=None,
                         progress=None) -> dict:
    """Research + qualify each company in ``leads`` (list of dicts with a
    ``website`` and/or a ``company_name``, or bare name/URL strings) and return a
    scored, sorted, browsable list. Bounded by RESEARCH_LIST_MAX. Never raises.

    A bare company NAME with no domain is resolved to its official site first,
    reusing the SAME company-lookup the resolve_company path uses — so a pasted
    list of plain names works exactly like a list of domains.

    ``research_fn`` / ``qualify_fn`` / ``resolve_fn`` are injectable for tests;
    they default to the real agents so production reuses them exactly as-is.
    """
    research_fn = research_fn or research_company
    qualify_fn = qualify_fn or qualification.qualify
    resolve_fn = resolve_fn or _default_resolve
    emit = progress if callable(progress) else (lambda *a, **k: None)
    cap = min(int(limit or RESEARCH_LIST_MAX), RESEARCH_LIST_MAX)
    leads = _normalize_leads(leads)[:cap]
    if not leads:
        return {"status": "empty", "prospects": [], "count": 0,
                "reason": "No companies to research. Give me an ICP to search for, "
                          "or a list of companies/websites."}

    scope, record_event = _telemetry()
    run_id = "rp_" + uuid.uuid4().hex[:12]
    entries = []
    with scope(campaign_id=run_id, user_id=user_id or "chat", agent="research_prospects"):
        record_event("research", "prospect_run_start", user_id=user_id,
                     entity_id=run_id, detail=f"{len(leads)} companies")
        for i, lead in enumerate(leads, 1):
            emit(f"Researching {_lead_label(lead)} ({i} of {len(leads)})")
            entries.append(_research_one(lead, icp, research_fn, qualify_fn, resolve_fn))
        emit("Scoring fit and ranking")
        record_event("research", "prospect_run_done", user_id=user_id,
                     entity_id=run_id,
                     detail=f"{sum(1 for e in entries if e['status'] == 'ok')}/"
                            f"{len(entries)} researched")

    # Sort best-first: researched + highest fit score, then everything else.
    entries.sort(key=lambda e: (e["status"] == "ok", e["score"]), reverse=True)
    ranked = sum(1 for e in entries if e["status"] == "ok")
    return {"status": "ok", "run_id": run_id, "count": len(entries),
            "researched": ranked, "prospects": entries,
            "summary": _run_summary(entries)}


def _lead_label(lead) -> str:
    """A short, human name for a lead, for the streamed "Researching X" step."""
    if isinstance(lead, dict):
        name = lead.get("company_name") or lead.get("company") or lead.get("website") or ""
    else:
        name = str(lead or "")
    name = name.replace("https://", "").replace("http://", "").split("/")[0]
    return (name.strip() or "a company")[:40]


def _research_one(lead, icp, research_fn, qualify_fn, resolve_fn) -> dict:
    company = lead.get("company_name") or ""
    website = lead.get("website") or ""
    # Bare name, no domain: resolve it to an official site first (same lookup the
    # resolve_company path uses) instead of skipping silently.
    if not website and company:
        website = resolve_fn(company) or ""
    if not website:
        research = {"status": "skip",
                    "reason": (f"Couldn't find a website for '{company}' to research"
                               if company else "No website to research")}
    else:
        try:
            research = research_fn(website)
        except Exception:  # noqa: BLE001 - a single bad site must not sink the batch
            research = {"status": "error", "reason": "Research failed for this site."}
    try:
        qual = qualify_fn(research=research, icp=icp)
        qual = qual.to_dict() if hasattr(qual, "to_dict") else (qual or {})
    except Exception:  # noqa: BLE001
        qual = {}
    return _entry(company, website, lead.get("discovery"), research, qual)


# Roles that can actually buy an AI SDR / outbound tool. A named employee is NOT a
# decision-maker just because they exist; their role has to match one of these, or
# we return nothing rather than inventing buying authority. Ordered loosely by how
# directly they own the outbound-buying decision.
_BUYING_ROLE_PATTERNS = [
    r"\bco[-\s]?founders?\b",
    r"\bfounders?\b",
    r"\bceo\b", r"\bchief executive\b",
    r"\bcro\b", r"\bchief revenue\b",
    r"\bvp\b[^.]*\bsales\b", r"\bvice president\b[^.]*\bsales\b",
    r"\bhead of sales\b", r"\bsales director\b", r"\bdirector of sales\b",
    r"\bhead of growth\b", r"\bvp\b[^.]*\bgrowth\b", r"\bgrowth lead\b",
    r"\bhead of revenue\b", r"\bchief growth\b",
    r"\bsales operations?\b", r"\brev[\s-]?ops\b", r"\bsales ops\b",
    r"\bbusiness development\b", r"\bhead of bd\b",
    r"\bpresident\b", r"\bowner\b", r"\bmanaging director\b",
    r"\bcmo\b", r"\bchief marketing\b",
]
_BUYING_ROLE_RE = re.compile("|".join(_BUYING_ROLE_PATTERNS), re.IGNORECASE)
# Roles that must NEVER pass even if a loose pattern brushes them (e.g. "sales
# recruiter" contains "sales"): sourcing/support/engineering are not the buyer.
_NON_BUYER_RE = re.compile(
    r"\b(recruit\w*|talent|sourcer|engineer\w*|developer|designer|support|"
    r"success|intern|assistant|coordinator|analyst)\b", re.IGNORECASE)


def _is_buying_role(role) -> bool:
    """True only when a role title maps to an approved buying persona and is not an
    excluded non-buyer role. Empty/unknown roles are False (unverified)."""
    r = re.sub(r"\s+", " ", str(role or "")).strip()
    if not r:
        return False
    if _NON_BUYER_RE.search(r):
        return False
    return bool(_BUYING_ROLE_RE.search(r))


def _normalize_role(role) -> str:
    """Tidy a role title for display (collapse whitespace, trim)."""
    return re.sub(r"\s+", " ", str(role or "")).strip()


def _decision_maker(data: dict):
    """A RELEVANT decision-maker to reach, or None.

    A person is surfaced only when their role matches an approved buying persona
    (founder / CEO / CRO / VP or Head of Sales / Head of Growth / revenue or sales-
    ops leader / owner / president / CMO). A named employee with an irrelevant role
    (engineer, recruiter) or no role is NOT a decision-maker — we return None rather
    than invent buying authority. When several people qualify, the first APPROVED
    one wins, not merely the first listed."""
    if not isinstance(data, dict):
        return None
    # Candidates in priority order: an explicit founder, the primary contact, then
    # each team member. Only those whose role qualifies are eligible.
    candidates = []
    if data.get("founder_name"):
        candidates.append((data["founder_name"], data.get("founder_role") or "Founder"))
    if data.get("primary_contact_name"):
        candidates.append((data["primary_contact_name"], data.get("primary_contact_role")))
    for member in data.get("team_members") or []:
        if isinstance(member, dict) and member.get("name"):
            candidates.append((member.get("name"), member.get("role")))
    for name, role in candidates:
        if _is_buying_role(role):
            return {"name": str(name), "role": _normalize_role(role)}
    return None


def _default_resolve(name: str) -> str:
    """Resolve a bare company NAME to its official website, reusing chat.resolver
    (the same company-lookup the resolve_company tool uses). Returns a URL or "".
    Best-effort in a batch: an ambiguous 'choices' result takes the top-ranked
    candidate rather than stalling to ask, since we can't prompt per company."""
    name = (name or "").strip()
    if not name:
        return ""
    try:
        from chat import resolver
        res = resolver.resolve_company_name(name)
    except Exception:  # noqa: BLE001 - resolution is best-effort, never fatal
        return ""
    if not isinstance(res, dict):
        return ""
    if res.get("status") == "resolved" and res.get("url"):
        return res["url"]
    if res.get("status") == "choices":
        choices = res.get("choices") or []
        if choices and choices[0].get("url"):
            return choices[0]["url"]
    return ""


# ── Formatting: one prospect -> a browsable entry (preview + detail) ───────
def _entry(company, website, discovery, research, qual) -> dict:
    status = research.get("status") if isinstance(research, dict) else "error"
    data = (research.get("data") if isinstance(research, dict) else None) or {}
    company = data.get("company_name") or company or _company_from_site(website)
    score = int(qual.get("qualification_score") or 0)
    recommendation = qual.get("recommendation") or ""
    findings = _findings(research)
    entry = {
        "company": company,
        "website": website,
        "status": status,
        "score": score,
        "fit_level": qual.get("fit_level") or "unknown",
        "priority": qual.get("priority") or "none",
        "recommendation": recommendation,
        "recommended": recommendation in (qualification.CONTINUE,
                                          qualification.HIGH_PRIORITY),
        "score_reason": _score_reason(qual, status, research),
        "preview": _preview(company, data, score, recommendation, status, research),
        "detail": {
            "what_they_do": data.get("what_they_do"),
            "research_confidence": research.get("research_score") if isinstance(research, dict) else None,
            "findings": findings,
            "sources": _sources(research),
            "score_breakdown": qual.get("signals") or {},
            "strongest_signals": qual.get("strongest_signals") or [],
            "missing_information": qual.get("missing_information") or [],
            "disqualifiers": qual.get("disqualifiers") or [],
            # Decision-maker availability (#14): a named contact from the research,
            # or None so the card can honestly say "no named contact yet".
            "decision_maker": _decision_maker(data),
        },
        "actions": list(PROSPECT_ACTIONS),
    }
    if discovery:
        entry["detail"]["why_discovered"] = discovery.get("why_it_matches")
    return entry


def _preview(company, data, score, recommendation, status, research) -> str:
    """ONE plain-language paragraph — the headline finding + the verdict. This is
    the collapsed view; the full trail lives in ``detail`` behind an expand."""
    if status != "ok":
        reason = (research.get("reason") or research.get("error")
                  if isinstance(research, dict) else None) or "couldn't be researched"
        return f"{company}: {reason}."
    what = (data.get("what_they_do") or "").strip().rstrip(".")
    hook = _headline_hook(research, data)
    bits = [company + (f": {what}." if what else ".")]
    if hook:
        bits.append(hook.rstrip(".") + ".")
    bits.append(f"Fit {score}/100: {_REC_HUMAN.get(recommendation, recommendation)}.")
    return " ".join(bits).strip()


def _headline_hook(research, data) -> str:
    hooks = research.get("hooks") if isinstance(research, dict) else None
    if isinstance(hooks, list) and hooks:
        top = max(hooks, key=lambda h: (h or {}).get("score") or 0)
        if isinstance(top, dict) and top.get("text"):
            return str(top["text"]).strip()
    return (str(data.get("unique_hook")).strip() if data.get("unique_hook") else "")


def _findings(research) -> list:
    """Findings with SOURCE + CONFIDENCE, reusing the research enrichment schema.
    Combines the ranked hooks (personalization angles) with the strongest evidence
    for the key company facts. Deduped by text, capped for a clean detail panel."""
    if not isinstance(research, dict):
        return []
    out, seen = [], set()

    def add(label, text, source, confidence, quote=None):
        key = (str(text or "")).strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"label": label, "value": str(text).strip(),
                    "source": source, "confidence": confidence, "quote": quote})

    for h in (research.get("hooks") or [])[:5]:
        if isinstance(h, dict) and h.get("text"):
            add("Hook", h["text"], h.get("source"), h.get("confidence"), h.get("quote"))

    evidence = research.get("evidence") or {}
    for node, label in _FINDING_NODES:
        items = evidence.get(node) or []
        if isinstance(items, list) and items:
            best = max(items, key=lambda e: (e or {}).get("confidence") or 0)
            if isinstance(best, dict) and best.get("value"):
                add(label, best["value"], best.get("source"),
                    best.get("confidence"), best.get("quote"))
    return out[:8]


def _sources(research) -> list:
    if not isinstance(research, dict):
        return []
    seen, out = set(), []
    for url in research.get("pages_crawled") or []:
        dom = registrable_domain(url)
        if dom and dom not in seen:
            seen.add(dom)
            out.append({"domain": dom, "url": url})
    return out[:8]


def _score_reason(qual, status, research) -> str:
    if qual.get("reasoning_summary"):
        return qual["reasoning_summary"]
    if status != "ok":
        return "Not enough research to score this lead."
    return qual.get("next_best_action") or ""


def _run_summary(entries) -> dict:
    by_rec = {}
    for e in entries:
        by_rec[e["recommendation"] or "unresearched"] = \
            by_rec.get(e["recommendation"] or "unresearched", 0) + 1
    top = entries[0] if entries and entries[0]["status"] == "ok" else None
    return {"total": len(entries),
            "researched": sum(1 for e in entries if e["status"] == "ok"),
            "by_recommendation": by_rec,
            "top": (f"{top['company']} ({top['score']}/100)" if top else None)}


# ── small helpers ──────────────────────────────────────────────────────────
def _normalize_leads(leads) -> list:
    """Accept discovery Prospect.public() dicts, {company,website} dicts, or bare
    strings (a name or a URL). Returns a clean list of {company_name, website}."""
    out = []
    for item in leads or []:
        if isinstance(item, str):
            s = item.strip()
            if not s:
                continue
            if "://" in s or ("." in s and " " not in s):
                out.append({"company_name": _company_from_site(s), "website":
                            s if "://" in s else "https://" + s})
            else:
                out.append({"company_name": s, "website": ""})
        elif isinstance(item, dict):
            website = item.get("website") or item.get("url") or ""
            out.append({"company_name": item.get("company_name") or item.get("company") or "",
                        "website": website, "discovery": item.get("discovery") or (
                            item if "why_it_matches" in item else None)})
    return out


def _company_from_site(site) -> str:
    dom = registrable_domain(site or "")
    return dom.split(".")[0].capitalize() if dom else (site or "")
