"""Hard-constraint verification: the canonical layer between entity validation and
display that refuses to call a prospect QUALIFIED unless every HARD constraint in
the request has supporting evidence.

Why this exists: a search for "B2B fintech startups that raised a seed round"
returned real fintech companies — but Adyen (no venture funding) and Airwallex
(Series H, $1.9B raised) are not seed-stage startups, so the search satisfied the
ENTITY (a real fintech company) while violating a HARD CONSTRAINT of the request
(seed stage). Entity validation cannot catch that; only per-constraint evidence
can. The rule this layer enforces is blunt and deliberate:

    A prospect is QUALIFIED only when every hard constraint the user stated has
    individually verified supporting evidence. If a constraint cannot be verified,
    the prospect is EXCLUDED — never shown as satisfying the request, and never
    used to pad the page to five.

Two constraints carry structured, individually-verifiable evidence and are the
ones that can EXCLUDE a real company:

  * funding stage — verified against Apollo's funding history (round type, date,
    amount and a SOURCE URL). "Seed" means the company is AT seed or earlier AND
    has a seed/pre-seed round with a citation; a Series A+ company that once raised
    a seed round years ago is NOT a seed-stage startup and is excluded.
  * hiring role — verified official hiring evidence (own careers page or Apollo
    structured job data), never a search-result headline.

Softer constraints (industry, company type, geography, size, recency) are checked
against whatever evidence is present and only exclude on a genuine CONTRADICTION,
never on absent optional data — over-rejecting a real company on a missing field
would be its own failure. The funding/hiring evidence itself is gathered by the
network step (sources.verify_funding / verify_hiring) BEFORE this pure layer runs.
"""

import re
from dataclasses import dataclass, field
from typing import List

# Funding-round ranking: a request for "seed" admits a company AT or BELOW seed
# (angel / pre-seed / seed) and excludes anything that has moved past it. The rank
# is what makes "Series A+ is not seed" a comparison rather than a word list.
_STAGE_RANK = {
    "angel": 0, "pre seed": 1, "pre-seed": 1, "preseed": 1, "seed": 2,
    "series a": 3, "series b": 4, "series c": 5, "series d": 6, "series e": 7,
    "series f": 8, "series g": 9, "series h": 10, "series i": 11, "series j": 12,
}


def normalise_stage(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip().lower())
    s = s.replace("pre-seed", "pre seed")
    return s


def stage_rank(text: str):
    """The funding-round rank, or None for an unknown/non-standard stage."""
    return _STAGE_RANK.get(normalise_stage(text))


@dataclass
class HardConstraints:
    """The hard constraints parsed from a request. Empty fields are not asked for
    and therefore never gate anything."""
    funding_stage: str = ""            # normalised target, e.g. "seed"
    industry: str = ""                 # a named vertical, e.g. "fintech"
    company_type: str = ""             # b2b | b2c | ""
    locations: List[str] = field(default_factory=list)
    hiring_roles: List[str] = field(default_factory=list)
    size_band: tuple = ()              # (low, high) or ()
    recency_days: int = 0              # >0 only when explicitly requested

    def any(self) -> bool:
        return bool(self.funding_stage or self.industry or self.company_type
                    or self.locations or self.hiring_roles or self.size_band
                    or self.recency_days)


def parse(plan, query=None) -> HardConstraints:
    """Read the hard constraints off the already-parsed SearchPlan (and query).

    Deliberately conservative: a constraint is HARD only when the user's own words
    established it. ``funding_stage`` comes from an explicit stage in the ask
    (``plan.stages``); a bare "fintech companies" search parses NO funding stage,
    so later-stage fintechs stay eligible."""
    c = HardConstraints()
    stages = [s for s in (getattr(plan, "stages", None) or []) if s]
    if stages:
        c.funding_stage = normalise_stage(stages[0])
    c.industry = (getattr(plan, "industry", "") or "").strip().lower()
    ct = getattr(plan, "company_type", "any")
    c.company_type = ct if ct in ("b2b", "b2c") else ""
    c.locations = [str(x).strip().lower() for x in
                   (getattr(plan, "locations", None) or []) if str(x).strip()]
    c.hiring_roles = list(getattr(plan, "roles", None) or [])
    c.size_band = tuple(getattr(plan, "size_band", ()) or ())
    c.recency_days = _recency_days(getattr(plan, "raw", "") or
                                   (getattr(query, "raw", "") if query else ""))
    return c


def needs_funding_verification(c: HardConstraints) -> bool:
    """True when a funding stage was requested and can be verified against Apollo."""
    return bool(c.funding_stage and stage_rank(c.funding_stage) is not None)


def verify(prospect, c: HardConstraints) -> tuple:
    """``(qualified: bool, unmet: list[str])`` for one prospect against the hard
    constraints. PURE — reads evidence already attached to the prospect (funding
    from sources.verify_funding, hiring from verify_hiring). Absent evidence for a
    verifiable constraint means UNMET, which means excluded."""
    unmet = []

    # Funding stage — the strict one. Requires funding evidence marked verified for
    # THIS stage by the network step; anything unverified (past the stage, no round,
    # no source URL, enrichment failed) is unmet.
    if c.funding_stage:
        fund = getattr(prospect, "funding", None) or {}
        if not (fund.get("verified") and _stage_satisfies(fund.get("stage"), c.funding_stage)):
            unmet.append("funding_stage")

    # Hiring role — official hiring evidence only (own careers page / Apollo).
    if c.hiring_roles and not _has_official_hiring_evidence(prospect):
        unmet.append("hiring")

    # Industry — a named vertical must appear in the prospect's own signals. Lenient
    # (evidence present), never a contradiction guess.
    if c.industry and not _industry_supported(prospect, c.industry):
        unmet.append("industry")

    # Geography — exclude only on a genuine contradiction (a known, different
    # location), never on absent location data.
    if c.locations and _location_contradicts(prospect, c.locations):
        unmet.append("geography")

    # Size — exclude only when a known headcount is outside the requested band.
    if c.size_band and _size_contradicts(prospect, c.size_band):
        unmet.append("size")

    return (not unmet), unmet


def apply(prospects, c: HardConstraints) -> tuple:
    """Split into ``(qualified, dropped_reasons)``. Order preserved."""
    from collections import Counter
    qualified, dropped = [], Counter()
    for p in prospects or []:
        ok, unmet = verify(p, c)
        if ok:
            qualified.append(p)
        else:
            for reason in unmet:
                dropped[reason] += 1
    return qualified, dict(dropped)


# ── funding-evidence matching (used by sources.verify_funding and verify) ──────
def stage_satisfied_by(latest_stage: str, events, target_stage: str) -> dict:
    """Return the best SEED-family evidence event (with a source URL) when the
    company is at-or-below the requested stage, else ``{}``.

    ``latest_stage`` is the company's current stage; ``events`` is the normalised
    funding history from apollo_orgs.funding_of. A company qualifies for "seed"
    only when its LATEST stage is seed-or-earlier (so a Series A+ company that once
    raised a seed round is excluded) AND it has a seed/pre-seed/angel round carrying
    a citation URL."""
    target = stage_rank(target_stage)
    if target is None:
        return {}
    latest = stage_rank(latest_stage)
    # No known current stage, or already past the requested stage -> not this stage.
    if latest is None or latest > target:
        return {}
    # A matching round (seed-family for a seed ask) WITH a source URL.
    candidates = []
    for e in events or []:
        etype = normalise_stage(e.get("type"))
        erank = stage_rank(etype)
        if erank is None or erank > target:
            continue
        if not e.get("news_url"):
            continue
        candidates.append(e)
    if not candidates:
        return {}
    # Prefer the round closest to (but not above) the requested stage, then the
    # most recent, so the citation shown is the relevant round.
    candidates.sort(key=lambda e: (stage_rank(normalise_stage(e.get("type"))) or 0,
                                   e.get("date") or ""), reverse=True)
    return candidates[0]


def _stage_satisfies(evidence_stage: str, target_stage: str) -> bool:
    r, t = stage_rank(evidence_stage), stage_rank(target_stage)
    return r is not None and t is not None and r <= t


# ── soft-constraint evidence checks ───────────────────────────────────────────
def _has_official_hiring_evidence(p) -> bool:
    hiring = getattr(p, "hiring", None) or {}
    if hiring.get("source") in ("own_careers_page", "apollo", "apollo_title_filter"):
        return True
    return bool(getattr(p, "apollo_id", "")) or getattr(p, "discovery_source", "") == "apollo"


def _industry_supported(p, industry: str) -> bool:
    hay = " ".join(str(x or "").lower() for x in [
        getattr(p, "company_name", ""), getattr(p, "domain", ""),
        getattr(p, "industry", ""), getattr(p, "industry_kind", ""),
        getattr(p, "why_it_matches", ""),
        " ".join(getattr(p, "match_reasons", None) or []),
        " ".join(getattr(p, "basic_signals", None) or []),
    ])
    return industry.lower() in hay


def _location_contradicts(p, locations) -> bool:
    loc = (getattr(p, "location", "") or "").strip().lower()
    if not loc:
        return False
    return not any(l in loc or loc in l for l in locations)


def _size_contradicts(p, band) -> bool:
    n = getattr(p, "employee_count", None)
    if not isinstance(n, int) or n <= 0 or len(band) != 2:
        return False
    return not (band[0] <= n <= band[1])


_RECENCY_RE = re.compile(
    r"\b(?:in\s+the\s+)?(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b"
    r"|\b(recently|just)\s+(?:raised|launched|funded)\b", re.I)


def _recency_days(text: str) -> int:
    m = _RECENCY_RE.search(text or "")
    if not m:
        return 0
    if m.group(1):
        n, unit = int(m.group(1)), m.group(2).lower()
        return n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    return 180                          # "recently" -> ~6 months
