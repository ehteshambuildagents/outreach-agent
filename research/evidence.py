"""Evidence data model — the heart of the engine.

Every extracted fact is an ``Evidence`` (value + where it came from + the exact
supporting quote + a confidence). Facts are organised into a ``ResearchGraph``
of named nodes. Keeping this as plain, typed dataclasses makes the whole
pipeline explainable and easy to test.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Evidence:
    """A single fact with its provenance."""
    value: str
    source_url: Optional[str]
    quote: Optional[str]
    confidence: float                 # 0.0 – 1.0 (post-verification)
    corroborations: int = 1           # how many grounded pages support it
    conflict: bool = False            # a different value was also grounded

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source_url,
            "quote": self.quote,
            "confidence": round(self.confidence, 3),
            "corroborations": self.corroborations,
            "conflict": self.conflict,
        }


@dataclass
class TeamMember:
    name: str
    role: Optional[str]
    source_url: Optional[str]
    quote: Optional[str]
    confidence: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "source": self.source_url,
            "quote": self.quote,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class Hook:
    category: str
    text: str
    score: float
    confidence: float
    source_url: Optional[str]
    quote: Optional[str]

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "text": self.text,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "source": self.source_url,
            "quote": self.quote,
        }


# Singular fields hold one best value; list fields aggregate many.
SINGULAR_FIELDS = (
    "company_name", "founder_name", "founder_role", "what_they_do",
    "target_customer", "recent_focus", "their_mission_or_why", "tone_style",
    "pricing_model", "metrics_or_traction",
    "product_category", "business_model", "company_stage",
    "competitive_positioning",
)
LIST_FIELDS = (
    "notable_customers", "tech_stack",
    "industries_served", "product_differentiators", "pain_points",
    "integrations",
)


@dataclass
class ResearchGraph:
    """Named nodes, each holding verified Evidence (highest-confidence first)."""
    nodes: Dict[str, List[Evidence]] = field(default_factory=dict)
    team: List[TeamMember] = field(default_factory=list)

    def add(self, node: str, evidence: Evidence) -> None:
        self.nodes.setdefault(node, []).append(evidence)

    def best(self, node: str) -> Optional[Evidence]:
        items = self.nodes.get(node) or []
        return max(items, key=lambda e: e.confidence) if items else None

    def value(self, node: str) -> Optional[str]:
        best = self.best(node)
        return best.value if best else None

    def values(self, node: str) -> List[str]:
        """De-duplicated values for a list node, highest-confidence first."""
        seen, out = set(), []
        for ev in sorted(self.nodes.get(node, []), key=lambda e: -e.confidence):
            key = ev.value.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(ev.value.strip())
        return out
