"""Discovery data models — the ICP query and a discovered Prospect.

A ``Prospect`` is a lightweight lead, NOT a researched company: it carries only
what a search pass can establish (name, site, coarse industry/location/size/
stage) plus WHY it matched and the evidence signals behind that. Deep facts are
the Research Agent's job later in the pipeline.
"""

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

from config.settings import DISCOVERY_DEFAULT_LIMIT, DISCOVERY_MAX_LIMIT


def registrable_domain(url: str) -> str:
    """A stable dedupe key: the registrable domain (last two labels), lowercased,
    ``www.`` stripped. ``https://www.Stripe.com/pricing`` -> ``stripe.com``."""
    host = (urlparse(url if "://" in (url or "") else "https://" + (url or "")).hostname
            or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _norm_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;]", value)
    else:
        items = list(value)
    out = []
    for it in items:
        s = str(it or "").strip().lower()
        if s and s not in out:
            out.append(s)
    return out


@dataclass
class DiscoveryQuery:
    """Structured ICP filters. The chat agent fills these from a free-text ask;
    the engine treats them deterministically."""
    industry: str = ""
    location: str = ""
    employee_range: str = ""          # e.g. "<50", "50-200", "200+"
    funding_stage: str = ""           # e.g. "seed", "series a", "bootstrapped"
    keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    raw: str = ""                     # the user's original phrasing (optional)
    limit: int = DISCOVERY_DEFAULT_LIMIT
    page: int = 0

    def __post_init__(self):
        self.industry = (self.industry or "").strip()
        self.location = (self.location or "").strip()
        self.employee_range = (self.employee_range or "").strip()
        self.funding_stage = (self.funding_stage or "").strip()
        self.keywords = _norm_list(self.keywords)
        self.exclude_keywords = _norm_list(self.exclude_keywords)
        self.limit = max(1, min(int(self.limit or DISCOVERY_DEFAULT_LIMIT),
                                DISCOVERY_MAX_LIMIT))
        self.page = max(0, int(self.page or 0))

    def is_empty(self) -> bool:
        return not any([self.industry, self.location, self.employee_range,
                        self.funding_stage, self.keywords, self.raw.strip()])

    def search_string(self) -> str:
        """Build ONE deterministic search query from the filters. Same filters
        always yield the same string (reproducible, debuggable)."""
        parts = []
        if self.funding_stage:
            parts.append(self.funding_stage)
        if self.industry:
            parts.append(self.industry)
        parts.append("companies" if "compan" not in " ".join(parts).lower() else "")
        if self.keywords:
            parts.append(" ".join(self.keywords))
        if self.location:
            parts.append("in " + self.location)
        built = " ".join(p for p in parts if p).strip()
        # Fall back to the user's raw phrasing if the filters were sparse.
        if len(built.split()) <= 1 and self.raw.strip():
            return self.raw.strip()
        return built or self.raw.strip()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Prospect:
    """One discovered company (a lead, not a researched profile).

    Beyond the coarse firmographics, a Prospect now carries the EVIDENCE behind
    its ranking, because "why was this picked" is part of the result rather than
    a debugging aid: ``match_reasons`` (itemised, human-readable), ``sources``
    (which providers found it, so corroboration is visible), ``hiring``
    (structurally verified postings, not a keyword guess), and ``tier``
    (``company`` vs the demoted ``fallback`` used for intermediaries).
    """
    company_name: str
    website: str
    domain: str = ""
    industry: str = ""
    location: str = ""
    estimated_company_size: str = "unknown"
    estimated_stage: str = "unknown"
    confidence: float = 0.0
    why_it_matches: str = ""
    discovery_source: str = ""
    basic_signals: List[str] = field(default_factory=list)
    # ── evidence / ranking transparency ──
    tier: str = "company"             # company | fallback (demoted intermediary)
    kind: str = "company"             # aggregators.classify verdict
    match_reasons: List[str] = field(default_factory=list)
    sources: List[dict] = field(default_factory=list)   # [{provider, url}]
    hiring: Optional[dict] = None     # {verified, source, match, summary, postings[]}
    # Verified funding evidence for a funding-stage constraint (discovery/constraints):
    # {verified, stage, latest_stage, amount, date, source_url, summary}
    funding: Optional[dict] = None
    growth: Optional[dict] = None     # {headcount_6mo, headcount_12mo}
    recent_activity: Optional[dict] = None             # {summary, url, source}
    apollo_id: str = ""
    # ── customer-likelihood inputs (see discovery/scoring.py) ──
    industry_kind: str = ""           # software | services | "" (unknown)
    employee_count: Optional[int] = None
    founded_year: Optional[int] = None
    is_public: bool = False           # publicly traded => not founder-accessible
    annual_revenue: Optional[float] = None    # USD; a scale proxy when headcount is absent
    has_intent: bool = False
    contact_route: str = ""           # a known way to reach a decision maker
    score: Optional[dict] = None      # the Score breakdown that produced the rank
    # bookkeeping (not part of the public discovery shape but stored):
    id: str = field(default_factory=lambda: "prs_" + uuid.uuid4().hex[:16])
    owner: str = ""
    query: str = ""
    status: str = "new"               # new | researched
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.domain:
            self.domain = registrable_domain(self.website)

    def add_source(self, provider: str, url: str = "") -> None:
        """Record a provider that found this company. Corroboration across
        sources is a ranking signal, so the list is deduped by provider."""
        if not provider:
            return
        if any(s.get("provider") == provider for s in self.sources):
            return
        self.sources.append({"provider": provider, "url": url or ""})

    @property
    def source_count(self) -> int:
        return len(self.sources) or (1 if self.discovery_source else 0)

    def public(self) -> dict:
        """The structured JSON the spec asks for (no internal bookkeeping)."""
        return {
            "company_name": self.company_name,
            "website": self.website,
            "industry": self.industry,
            "location": self.location,
            "estimated_company_size": self.estimated_company_size,
            "estimated_stage": self.estimated_stage,
            "confidence": round(self.confidence, 3),
            "why_it_matches": self.why_it_matches,
            "discovery_source": self.discovery_source,
            "basic_signals": self.basic_signals,
            "tier": self.tier,
            "kind": self.kind,
            "match_reasons": self.match_reasons,
            "sources": self.sources,
            "hiring": self.hiring,
            "funding": self.funding,
            "growth": self.growth,
            "recent_activity": self.recent_activity,
            "industry_kind": self.industry_kind,
            "employee_count": self.employee_count,
            "founded_year": self.founded_year,
            "is_public": self.is_public,
            "annual_revenue": self.annual_revenue,
            "has_intent": self.has_intent,
            "score": self.score,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Prospect":
        known = {f: d.get(f) for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)
