"""Deterministic intent parser: a plain-language ask -> a structured SearchPlan.

Why this exists: the chat model used to invent the search keywords, and the result
quality moved with whatever it happened to pick. One turn sent
``["video generation", "ai video"]`` and found HeyGen and Runway; the next sent
``["hiring", "AI video"]`` and drifted into video-surveillance vendors. That is a
design smell, not bad luck, so planning is now code.

    parse("B2B founders hiring for an AI video creator role") -> SearchPlan(
        roles=["ai video creator"],
        role_titles=["ai video creator", "video content creator", "video editor", ...],
        anchor="role",            # <- NOT the industry
        company_type="b2b",
        buying_signals=["hiring"],
        ...)

THE ANCHOR IS THE POINT. An earlier version turned this ask into the product
category "ai video" and returned AI-video vendors (HeyGen, Synthesia, Luma). But a
company does not have to sell AI video to hire someone to make AI video: Clay,
Vanta, Linear, Ramp and Mercury are all plausible buyers and none of them would
ever surface from a category search. So when the ask contains a ROLE, the role is
the primary axis and the industry is at most a secondary filter, applied only when
the user actually named one.

Everything here is pure: no network, no model call, fully unit-testable.
"""

import re
from dataclasses import dataclass, field
from typing import List

# ── Role vocabulary ────────────────────────────────────────────────────────
# Job-title nouns, and the equivalents a hiring company might post instead. A
# company wanting an "AI video creator" may advertise "Video Editor" or "Content
# Producer", so the plan searches all of them.
_ROLE_NOUN_EQUIVALENTS = {
    "creator": ("creator", "producer", "editor", "specialist", "designer"),
    "producer": ("producer", "creator", "editor", "manager"),
    "editor": ("editor", "creator", "producer", "specialist"),
    "designer": ("designer", "creator", "specialist"),
    "marketer": ("marketer", "manager", "specialist", "lead"),
    "engineer": ("engineer", "developer"),
    "developer": ("developer", "engineer"),
    "specialist": ("specialist", "manager", "lead", "creator"),
    "manager": ("manager", "lead", "specialist", "head"),
    "lead": ("lead", "manager", "head"),
}

_ROLE_NOUNS = tuple(_ROLE_NOUN_EQUIVALENTS) + (
    "representative", "rep", "analyst", "strategist", "writer", "director",
    "head", "associate", "coordinator", "architect", "scientist", "consultant",
)

# Common GTM abbreviations the user will type, mapped to the titles companies post.
_ROLE_ALIASES = {
    "sdr": ("sales development representative", "sdr", "business development representative"),
    "bdr": ("business development representative", "bdr", "sales development representative"),
    "ae": ("account executive", "ae"),
    "cs": ("customer success manager", "customer success"),
    "csm": ("customer success manager", "customer success"),
    "pmm": ("product marketing manager", "product marketing"),
    "rev ops": ("revenue operations", "revops"),
    "revops": ("revenue operations", "revops"),
    "growth": ("head of growth", "growth marketing manager", "growth lead"),
}

# Where a role sits in the org, used for founder/decision-maker accessibility.
_SENIORITY_HINTS = (
    (r"\b(founder|co-?founder|ceo|cto|cmo|cro|coo)\b", "founder"),
    (r"\b(vp|head of|chief|director)\b", "executive"),
)

# ── ICP vocabulary ─────────────────────────────────────────────────────────
_B2B_HINTS = r"\b(b2b|saas|software|startups?|companies|vendors?|platforms?)\b"
_B2C_HINTS = r"\b(b2c|consumer|d2c|dtc|ecommerce brands?)\b"

_SIZE_PATTERNS = (
    (r"\bunder\s+(\d+)\s*(?:employees|people|staff|headcount)?\b", "under"),
    (r"\b(?:<|less than|fewer than|below)\s*(\d+)\b", "under"),
    (r"\b(\d+)\s*(?:\+|or more|plus)\b", "over"),
    (r"\b(\d+)\s*(?:-|to|–)\s*(\d+)\b", "between"),
)

_SIZE_WORDS = (
    (r"\b(solo|one[- ]person|indie)\b", (1, 10)),
    (r"\b(tiny|very small)\b", (1, 20)),
    (r"\b(small|smb|lean)\b", (1, 50)),
    (r"\b(mid[- ]?market|mid[- ]?size[d]?|scale[- ]?up)\b", (51, 500)),
    (r"\b(enterprise|large)\b", (501, 10000)),
    (r"\bseed\b", (1, 50)),
    (r"\bseries\s*a\b", (11, 200)),
    (r"\bseries\s*b\b", (51, 500)),
)

_STAGE_RE = re.compile(
    r"\b(pre-?seed|seed|series\s*[abcd]|bootstrapped|profitable|"
    r"venture[- ]backed|yc|y combinator)\b", re.I)

# "in Berlin", "in the US", "based in Europe" — deliberately conservative, since a
# wrong location filter silently empties the result set.
_LOCATION_RE = re.compile(
    r"\b(?:in|from|based in|located in|across)\s+"
    r"((?:the\s+)?[A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){0,2}|"
    r"us|usa|uk|uae|eu|emea|apac|latam|europe|asia|africa)\b")

_LOCATION_STOPWORDS = {
    "the", "a", "an", "find", "show", "get", "list", "me", "my", "our", "their",
    "b2b", "saas", "ai", "video", "role", "roles", "job", "jobs", "hiring",
    "founders", "companies", "company", "startups", "startup", "last", "next",
}

# ── Buying signals the plan can ask for ────────────────────────────────────
_SIGNAL_PATTERNS = (
    (r"\b(hir(?:e|ing)|recruit(?:ing)?|open roles?|job posting|opening)\b", "hiring"),
    (r"\b(raised|funding|funded|series|seed round|investment)\b", "funding"),
    (r"\b(grow(?:ing|th)|scaling|expand(?:ing)?|headcount)\b", "growth"),
    (r"\b(launch(?:ed|ing)?|shipped|new product|rebrand)\b", "launch"),
    (r"\b(in[- ]market|evaluating|switching|migrating|looking for a)\b", "intent"),
)

# What must never be returned as a prospect for a hiring-signal search: the firms
# that post other companies' roles. Discovered live: a role-first Apollo search
# came back dominated by staffing agencies (Onward Search, Handle Recruitment,
# hackajob, MCG Talent) because recruiters post the most job ads.
# TWO classes, kept apart because they are excluded for DIFFERENT reasons and the
# streaming narration states the reason out loud. Recruiters literally sell
# staffing; agencies and consultancies hire on a client's behalf. Conflating them
# made the narration claim "Google is a recruiter" (Google carries the advertising
# code), which is both false and instantly discrediting.
RECRUITER_SIC = frozenset({
    "7361",   # employment agencies
    "7363",   # help supply services (staffing)
})
RECRUITER_NAICS_PREFIXES = ("5613", "56131", "56132")

AGENCY_SIC = frozenset({
    "7311",   # advertising agencies
    "8742",   # management consulting
})
AGENCY_NAICS_PREFIXES = ("541612", "54161", "5418", "541810", "541613")

STAFFING_SIC = RECRUITER_SIC | AGENCY_SIC
STAFFING_NAICS_PREFIXES = RECRUITER_NAICS_PREFIXES + AGENCY_NAICS_PREFIXES


@dataclass
class SearchPlan:
    """The structured plan the engine executes. ``anchor`` decides the primary
    axis, which is the single most important field here."""
    raw: str = ""
    anchor: str = "category"          # role | category | both
    roles: List[str] = field(default_factory=list)        # as the user said them
    role_titles: List[str] = field(default_factory=list)  # expanded, for Apollo
    industry: str = ""                # only if the user named one
    keyword_tags: List[str] = field(default_factory=list)  # only if named
    company_type: str = "any"         # b2b | b2c | any
    employee_ranges: List[str] = field(default_factory=list)   # Apollo bands
    size_band: tuple = ()             # (low, high) or ()
    locations: List[str] = field(default_factory=list)
    stages: List[str] = field(default_factory=list)
    buying_signals: List[str] = field(default_factory=list)
    wants_founder_access: bool = False
    exclude_keywords: List[str] = field(default_factory=list)
    # Category words that describe what the user is interested in but must NOT
    # filter the search. They add ICP relevance in ranking only.
    relevance_terms: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)   # human-readable, for streaming

    def public(self) -> dict:
        return {
            "anchor": self.anchor,
            "roles": self.roles,
            "role_titles": self.role_titles,
            "industry": self.industry,
            "keyword_tags": self.keyword_tags,
            "relevance_terms": self.relevance_terms,
            "company_type": self.company_type,
            "employee_ranges": self.employee_ranges,
            "locations": self.locations,
            "stages": self.stages,
            "buying_signals": self.buying_signals,
            "wants_founder_access": self.wants_founder_access,
            "steps": self.steps,
        }


def parse(text: str, *, industry: str = "", keywords=None, employee_range: str = "",
          funding_stage: str = "", location: str = "", exclude_keywords=None,
          limit: int = 20) -> SearchPlan:
    """Build a SearchPlan from the user's own words.

    Structured hints (``industry``, ``keywords``, …) come from the chat tool call
    and are treated as HINTS ONLY: they can add a filter the text did not mention,
    but they can never make a category the anchor when the text names a role. That
    asymmetry is deliberate, because those hints are model-generated and were the
    source of the variance this parser replaces.
    """
    raw = (text or "").strip()
    hints = [str(k or "").strip() for k in (keywords or []) if str(k or "").strip()]
    blob = " ".join([raw] + hints + [industry or ""]).strip()

    plan = SearchPlan(raw=raw)
    plan.roles = _roles(raw, hints)
    plan.role_titles = expand_role_titles(plan.roles)
    plan.buying_signals = _signals(blob)
    plan.company_type = _company_type(blob)
    plan.stages = _stages(blob, funding_stage)
    plan.locations = _locations(blob, location)
    plan.size_band = _size_band(blob, employee_range, plan.stages)
    plan.employee_ranges = apollo_bands(*plan.size_band) if plan.size_band else []
    plan.wants_founder_access = bool(
        re.search(r"\b(founders?|co-?founders?|ceos?|solo|small team|"
                  r"small compan(?:y|ies)|smb|indie)\b", blob, re.I))
    plan.exclude_keywords = [k.lower() for k in (exclude_keywords or []) if k]

    # The industry/category is only a HARD FILTER when the USER named a real
    # vertical. A model hint alone is not enough: that is exactly how "B2B SaaS"
    # plus "ai video" became the anchor and buried Clay, Vanta, Linear and Ramp.
    named_industry = _named_industry(raw, industry)
    plan.industry = named_industry
    plan.relevance_terms = _relevance_terms(raw, hints, named_industry)

    if plan.roles:
        plan.anchor = "both" if named_industry else "role"
        # Role-anchored: category words become a RANKING hint only. Filtering on
        # them would re-anchor the search on industry, which is the whole bug.
        plan.keyword_tags = [named_industry] if named_industry else []
    else:
        plan.anchor = "category"
        plan.keyword_tags = _category_tags(raw, named_industry, hints)
    plan.steps = _steps(plan)
    return plan


# ── Roles ──────────────────────────────────────────────────────────────────
_ROLE_PATTERNS = (
    r"hiring\s+(?:for\s+)?(?:an?\s+|the\s+)?([a-z][a-z0-9 /+&-]{2,44}?)\s+(?:role|position|job|opening)s?\b",
    r"(?:role|position|job|opening)s?\s+(?:of|for)\s+(?:an?\s+)?([a-z][a-z0-9 /+&-]{2,44})",
    r"hiring\s+(?:for\s+)?(?:an?\s+|the\s+)?([a-z][a-z0-9 /+&-]{2,44})$",
    r"(?:looking|search(?:ing)?)\s+for\s+(?:an?\s+)?([a-z][a-z0-9 /+&-]{2,44}?)\s+(?:role|hire)s?\b",
)

_HIRING_NOISE_RE = re.compile(
    r"\b(hiring|hire|hires|recruit(?:ing|ment)?|job|jobs|role|roles|position|"
    r"positions|opening|openings|vacanc(?:y|ies)|career|careers|for|an|a|the|"
    r"founders?|companies|company|startups?|b2b|b2c|saas|who|that|are|is|in|"
    r"currently|actively|right now|new)\b", re.I)


def _singular_alias(word: str) -> str:
    """Fold a plural acronym onto the alias it names: "sdrs" -> "sdr".

    People ask for "founders hiring SDRs"; employers post "SDR". Only the
    singular is a key in _ROLE_ALIASES, and expansion matches that key on a word
    boundary, so "sdrs" resolved to no titles at all and the whole query fell
    back to a category search with no role and no verified postings.
    """
    if word in _ROLE_ALIASES:
        return word
    stem = word.rstrip("s")
    return stem if stem != word and stem in _ROLE_ALIASES else word


def _clean_role(text: str) -> str:
    out = _HIRING_NOISE_RE.sub(" ", str(text or ""))
    words, seen = [], set()
    for raw_word in out.lower().split():
        w = _singular_alias(raw_word)
        if w not in seen:
            seen.add(w)
            words.append(w)
    return " ".join(words).strip(" ,-/")


def _roles(raw: str, hints) -> list:
    """Roles the user wants companies to be hiring. Extracted PER FIELD so a
    phrase in one keyword never bleeds into the next."""
    found = []

    def add(candidate):
        role = _clean_role(candidate)
        if not role or len(role) < 2 or len(role.split()) > 5:
            return
        if role in _ROLE_ALIASES or _has_role_noun(role):
            if role not in found:
                found.append(role)

    for field_text in [raw] + list(hints):
        if not field_text.strip():
            continue
        matched = False
        for pat in _ROLE_PATTERNS:
            for m in re.finditer(pat, field_text, re.I):
                add(m.group(1))
                matched = True
        if not matched:
            add(field_text)
    return found[:3]


def _has_role_noun(text: str) -> bool:
    words = re.split(r"[^a-z0-9]+", text.lower())
    return any(w.rstrip("s") in _ROLE_NOUNS or w in _ROLE_ALIASES for w in words if w)


def expand_role_titles(roles) -> list:
    """The job titles a hiring company might actually post for these roles.

    "ai video creator" also matches "video content creator", "video editor" and
    "video producer", because employers do not use the user's phrasing.
    """
    titles = []

    def add(title):
        t = " ".join(str(title or "").split()).lower()
        if t and len(t) > 2 and t not in titles:
            titles.append(t)

    for role in roles or []:
        add(role)
        for alias, expansions in _ROLE_ALIASES.items():
            if alias == role or re.search(rf"\b{re.escape(alias)}\b", role):
                for e in expansions:
                    add(e)
        words = role.split()
        if len(words) < 2:
            continue
        noun, modifiers = words[-1], words[:-1]
        # Swap the role noun for its equivalents ("creator" -> "editor", ...).
        for equivalent in _ROLE_NOUN_EQUIVALENTS.get(noun.rstrip("s"), ()):
            add(" ".join(modifiers + [equivalent]))
        # Drop the leading modifier ("ai video creator" -> "video creator").
        if len(words) > 2:
            add(" ".join(words[1:]))
            for equivalent in _ROLE_NOUN_EQUIVALENTS.get(noun.rstrip("s"), ()):
                add(" ".join(words[1:-1] + [equivalent]))
        # Insert "content", a very common employer phrasing for creator roles.
        if noun.rstrip("s") in ("creator", "producer", "editor") and "content" not in role:
            add(" ".join(modifiers + ["content", noun]))
    return titles[:10]


# ── ICP fields ─────────────────────────────────────────────────────────────
def _company_type(blob: str) -> str:
    if re.search(_B2C_HINTS, blob, re.I):
        return "b2c"
    if re.search(_B2B_HINTS, blob, re.I):
        return "b2b"
    return "any"


def _named_industry(raw: str, industry_hint: str) -> str:
    """An industry counts only when the USER's own words carry a real vertical.
    Generic wrappers ("B2B SaaS", "companies", "startups") are not industries;
    treating them as one is what anchored the search and buried the ICP."""
    generic = re.compile(r"^(b2b|b2c|saas|software|tech|technology|startups?|"
                         r"companies|company|platforms?|vendors?|\s)+$", re.I)
    for candidate in (raw, industry_hint):
        if not candidate:
            continue
        m = re.search(r"\b(fintech|healthtech|edtech|proptech|insurtech|legaltech|"
                      r"martech|adtech|hrtech|climate\s?tech|deeptech|biotech|"
                      r"cybersecurity|security|devtools?|developer tools?|"
                      r"e-?commerce|logistics|manufacturing|real estate|"
                      r"hospitality|travel|gaming|crypto|web3|agencies)\b",
                      candidate, re.I)
        if m:
            return m.group(1).lower()
    if industry_hint and not generic.match(industry_hint.strip()):
        return industry_hint.strip().lower()
    return ""


def _category_tags(raw: str, industry: str, hints) -> list:
    """Apollo keyword tags. Empty unless the user named a real category, because
    a category tag next to a role filter re-anchors the search on industry."""
    tags = []
    if industry:
        tags.append(industry)
    for hint in hints:
        cleaned = _clean_role(hint)
        if cleaned and not _has_role_noun(cleaned) and len(cleaned.split()) <= 3:
            if industry and cleaned in industry:
                continue
            tags.append(cleaned)
    out = []
    for t in tags:
        if t and t not in out:
            out.append(t)
    return out[:2]


def _relevance_terms(raw: str, hints, industry: str) -> list:
    """Soft ICP terms used for SCORING only: a company that also looks like the
    category the user mentioned is more relevant, but a company that does not is
    still a candidate, because the hiring signal is what matters."""
    terms = []
    if industry:
        terms.append(industry)
    for hint in hints:
        cleaned = _clean_role(hint)
        if cleaned and not _has_role_noun(cleaned) and 1 <= len(cleaned.split()) <= 3:
            if cleaned not in terms:
                terms.append(cleaned)
    return terms[:4]


def _signals(blob: str) -> list:
    found = []
    for pattern, label in _SIGNAL_PATTERNS:
        if re.search(pattern, blob, re.I) and label not in found:
            found.append(label)
    return found


def _stages(blob: str, hint: str) -> list:
    stages = []
    for m in _STAGE_RE.finditer(blob):
        s = " ".join(m.group(1).lower().split())
        if s not in stages:
            stages.append(s)
    if hint and hint.lower() not in stages:
        stages.append(hint.lower())
    return stages[:3]


def _locations(blob: str, hint: str) -> list:
    out = []
    if hint:
        out.append(hint.strip())
    for m in _LOCATION_RE.finditer(blob or ""):
        loc = " ".join(m.group(1).replace("the ", "").split())
        if loc.lower() in _LOCATION_STOPWORDS:
            continue
        if loc and loc.lower() not in [o.lower() for o in out]:
            out.append(loc)
    return out[:2]


def _size_band(blob: str, hint: str, stages) -> tuple:
    text = f"{blob} {hint or ''}"
    for pattern, kind in _SIZE_PATTERNS:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        nums = [int(n) for n in m.groups() if n and n.isdigit()]
        if kind == "under" and nums:
            return (1, nums[0])
        if kind == "over" and nums:
            return (nums[0], 10000)
        if kind == "between" and len(nums) >= 2:
            return (min(nums), max(nums))
    for pattern, band in _SIZE_WORDS:
        if re.search(pattern, text, re.I):
            return band
    return ()


# Apollo only accepts its own bands, so a range is expressed as the bands it spans.
_APOLLO_BANDS = ((1, 10), (11, 20), (21, 50), (51, 100), (101, 200), (201, 500),
                 (501, 1000), (1001, 2000), (2001, 5000), (5001, 10000))


def apollo_bands(low: int, high: int) -> list:
    return [f"{a},{b}" for a, b in _APOLLO_BANDS if b >= low and a <= high]


def is_staffing(sic_codes=None, naics_codes=None) -> bool:
    """True for recruiters, staffing firms and agencies: they post other people's
    roles, so a hiring-signal search surfaces them heavily and none of them is the
    prospect. Deterministic, straight off Apollo's classification codes."""
    return bool(staffing_kind(sic_codes, naics_codes))


def staffing_kind(sic_codes=None, naics_codes=None) -> str:
    """WHY a company is excluded from a hiring search: ``"recruiter"`` (it sells
    staffing), ``"agency"`` (it hires on a client's behalf), or ``""``.

    The distinction exists so the narration can name the real reason. Recruiter
    wins ties: a firm carrying both codes is a staffing firm first.
    """
    sic = {str(c) for c in (sic_codes or [])}
    naics = [str(c) for c in (naics_codes or [])]
    if sic & RECRUITER_SIC or any(c.startswith(RECRUITER_NAICS_PREFIXES) for c in naics):
        return "recruiter"
    if sic & AGENCY_SIC or any(c.startswith(AGENCY_NAICS_PREFIXES) for c in naics):
        return "agency"
    return ""


def _steps(plan: SearchPlan) -> list:
    """The plan in plain language. Doubles as the streaming narration later."""
    steps = []
    if plan.anchor in ("role", "both") and plan.role_titles:
        steps.append("Find companies with a live posting for "
                     + ", ".join(plan.role_titles[:3])
                     + (" and similar titles" if len(plan.role_titles) > 3 else ""))
    if plan.keyword_tags:
        steps.append("Narrow to " + ", ".join(plan.keyword_tags))
    elif plan.relevance_terms and plan.anchor == "role":
        steps.append("Rank " + ", ".join(plan.relevance_terms)
                     + " relevance higher, without filtering others out")
    if plan.employee_ranges:
        low, high = plan.size_band
        steps.append(f"Keep companies with roughly {low} to {high} employees")
    if plan.locations:
        steps.append("Prefer " + ", ".join(plan.locations))
    steps.append("Drop recruiters, staffing firms, job boards and marketplaces")
    steps.append("Corroborate on the web and score for buying signals")
    if plan.wants_founder_access:
        steps.append("Prefer companies where a founder is reachable")
    return steps
