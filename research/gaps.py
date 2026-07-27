"""The evidence ledger: what do we still not know, and who can tell us?

The research loop used to think in PROVIDERS ("try requests, then a browser, then
Firecrawl") and in PAGES ("crawl up to N"). Both are the wrong objective. A human
researcher thinks in EVIDENCE: I still don't know what they charge, so I'll look
for a pricing page; I still don't have a name, so I'll check the about page.
The provider is a means, chosen per gap.

This module is that reasoning, kept pure so it is fully testable:

    assess(graph, signals)      -> Ledger (confidence, have, missing)
    plan(ledger, ...)           -> [Action]  best move per missing item
    merge_value(...)            -> multi-source agreement / contradiction

Nothing here fetches, calls a model, or knows about HTTP. The pipeline owns the
doing; this owns the deciding, which is why the decisions can be asserted in
tests rather than inferred from a live crawl.

CONFIDENCE IS EARNED FROM EVIDENCE, NEVER FROM EFFORT. Pages crawled, providers
called and bytes downloaded contribute nothing. Only a satisfied evidence slot
raises it, weighted by how much that slot matters to writing a credible email.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional


# ── What counts as evidence ────────────────────────────────────────────────
@dataclass(frozen=True)
class Slot:
    """One kind of evidence: how to tell we have it, what it is worth, and where
    it is usually found.

    ``page_hints`` are path keywords the crawler/Firecrawl can aim at.
    ``providers`` are the non-crawl tools that can supply it, best first. Both
    are PREFERENCES for this slot, not a global provider order: which tool runs
    depends entirely on which slot is empty.
    """
    name: str
    weight: float
    label: str                                  # human phrasing for narration
    satisfied: Callable[["Ledger"], bool] = None
    page_hints: tuple = ()
    providers: tuple = ()


def _has(graph, *nodes) -> bool:
    for node in nodes:
        if graph.value(node) or graph.values(node):
            return True
    return False


# The slots, in rough order of how much a cold email needs them. Weights sum to
# 1.0 so confidence reads as a percentage of "everything worth knowing".
SLOTS = (
    Slot("what_they_do", 0.18, "what they do",
         lambda l: _has(l.graph, "what_they_do"),
         ("about", "company", "product", "home"), ()),
    Slot("target_customer", 0.12, "who they sell to",
         lambda l: _has(l.graph, "target_customer", "industries_served"),
         ("customers", "solutions", "industries", "product"), ("exa",)),
    Slot("positioning", 0.10, "how they position themselves",
         lambda l: _has(l.graph, "product_category", "competitive_positioning",
                        "product_differentiators"),
         ("product", "features", "solutions"), ()),
    # NOTE: Apollo is deliberately NOT a provider for this slot. Apollo People
    # Match ENRICHES a known contact (it needs a name or LinkedIn URL and rejects
    # a domain-only request outright, verified against the live API), so it cannot
    # DISCOVER a decision maker — and by the time we have a name this slot is
    # already satisfied. Enriching a known contact's email/title is real work, but
    # it belongs to research/source_planner.py, which already does it. Listing
    # Apollo here only produced an action that could never succeed.
    Slot("founder", 0.14, "a named decision maker",
         lambda l: bool(l.graph.value("founder_name") or l.graph.team),
         ("about", "team", "leadership", "founders", "people"),
         ("tavily",)),
    Slot("pricing", 0.08, "how they charge",
         lambda l: _has(l.graph, "pricing_model", "business_model"),
         ("pricing", "plans"), ()),
    Slot("customers", 0.08, "customer proof",
         lambda l: _has(l.graph, "notable_customers"),
         ("customers", "case-studies", "testimonials"), ("tavily",)),
    Slot("product_depth", 0.06, "product detail",
         lambda l: _has(l.graph, "tech_stack", "integrations"),
         ("docs", "documentation", "integrations", "developers", "api"), ()),
    Slot("recent_signal", 0.12, "something recent worth mentioning",
         lambda l: _has(l.graph, "recent_focus", "metrics_or_traction")
         or bool(l.signals.get("recent_launch")),
         ("blog", "news", "press", "newsroom", "changelog"), ("tavily", "x")),
    Slot("funding", 0.06, "funding or stage",
         lambda l: _has(l.graph, "company_stage") or bool(l.signals.get("funding")),
         ("investors", "press", "news"), ("tavily",)),
    # Apollo is not listed here either: hiring intent comes from Apollo's
    # ORGANIZATION search + dated job postings, which the DISCOVERY engine
    # already runs and passes in via ``signals``. This loop's job is the
    # company's own careers page.
    Slot("hiring", 0.06, "hiring intent",
         lambda l: bool(l.signals.get("hiring")),
         ("careers", "jobs"), ()),
)

_BY_NAME = {s.name: s for s in SLOTS}

# Above this the picture is good enough to write a specific, credible email.
CONFIDENCE_TARGET = 0.72
# Below this after the homepage, the page was almost certainly navigation only.
WEAK_PAGE_CONFIDENCE = 0.35


@dataclass
class Ledger:
    """What is known so far, and therefore what is worth doing next."""
    graph: object
    signals: dict = field(default_factory=dict)
    have: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    confidence: float = 0.0
    conflicts: List[str] = field(default_factory=list)

    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENCE_TARGET

    def missing_labels(self) -> List[str]:
        return [_BY_NAME[m].label for m in self.missing if m in _BY_NAME]

    def public(self) -> dict:
        return {
            "confidence": round(self.confidence, 3),
            "have": list(self.have),
            "missing": list(self.missing),
            "missing_labels": self.missing_labels(),
            "conflicts": list(self.conflicts),
        }


def assess(graph, signals: dict = None) -> Ledger:
    """Score what is known. Confidence is the weighted share of evidence slots
    that are actually filled, reduced where sources contradict each other."""
    ledger = Ledger(graph=graph, signals=dict(signals or {}))
    total = 0.0
    for slot in SLOTS:
        try:
            ok = bool(slot.satisfied(ledger))
        except Exception:  # noqa: BLE001 - a malformed graph must not break planning
            ok = False
        if ok:
            ledger.have.append(slot.name)
            total += slot.weight
        else:
            ledger.missing.append(slot.name)

    # A contradiction means we are LESS sure than the raw slot count suggests.
    ledger.conflicts = _conflicting_nodes(graph)
    total -= min(0.15, 0.05 * len(ledger.conflicts))
    ledger.confidence = max(0.0, min(1.0, total))
    return ledger


def _conflicting_nodes(graph) -> List[str]:
    out = []
    for node, items in (getattr(graph, "nodes", None) or {}).items():
        if any(getattr(e, "conflict", False) for e in items):
            out.append(node)
    return sorted(out)


# ── Choosing the next move ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Action:
    """One thing worth doing next, and the gap that justifies it."""
    kind: str                 # crawl | firecrawl | tavily | exa | x
    target: Optional[str]     # url or query hint
    slot: str                 # the evidence this is meant to fill
    why: str                  # human phrasing, for the stream

    def public(self) -> dict:
        return {"kind": self.kind, "target": self.target,
                "slot": self.slot, "why": self.why}


def plan(ledger: Ledger, *, candidate_urls=(), crawled=(), providers=None,
         limit: int = 4) -> List[Action]:
    """The best next moves, highest-value gap first.

    For each missing slot: prefer a page on the company's OWN site (cheapest and
    most authoritative), then a provider that specialises in that fact. A slot
    with nowhere to look produces no action rather than a wasted call, and a
    provider that is not configured is skipped instead of being planned for.
    """
    providers = {k: bool(v) for k, v in (providers or {}).items()}
    done = {str(u).rstrip("/").lower() for u in crawled}
    used_urls, actions = set(done), []

    for name in _ordered_missing(ledger):
        slot = _BY_NAME[name]
        url = _best_page(slot, candidate_urls, used_urls)
        if url:
            used_urls.add(url.rstrip("/").lower())
            actions.append(Action("crawl", url, name,
                                  f"looking for {slot.label}"))
        elif slot.page_hints and providers.get("firecrawl"):
            # No linked page for it, but the site may still have one that the
            # homepage never linked. Firecrawl can go find it by path.
            actions.append(Action("firecrawl", slot.page_hints[0], name,
                                  f"hunting a {slot.page_hints[0]} page for "
                                  f"{slot.label}"))
        for provider in slot.providers:
            if providers.get(provider):
                actions.append(Action(provider, None, name,
                                      f"asking {provider} for {slot.label}"))
                break
        if len(actions) >= limit:
            break
    return actions[:limit]


def _ordered_missing(ledger: Ledger) -> List[str]:
    """Missing slots, most valuable first — the gap that buys the most
    confidence is always the one worth closing next."""
    return sorted((m for m in ledger.missing if m in _BY_NAME),
                  key=lambda n: -_BY_NAME[n].weight)


def _best_page(slot: Slot, candidate_urls, used) -> Optional[str]:
    """An uncrawled candidate URL whose path matches this slot's hints."""
    for hint in slot.page_hints:
        for url in candidate_urls:
            low = str(url or "").lower()
            if hint in low and low.rstrip("/") not in used:
                return url
    return None


def slot_label(slot_name: str) -> str:
    """The human phrasing for a slot, for narration."""
    slot = _BY_NAME.get(slot_name)
    return slot.label if slot else slot_name.replace("_", " ")


def slot_hints(slot_name: str) -> tuple:
    """The path keywords that usually carry this evidence."""
    slot = _BY_NAME.get(slot_name)
    return slot.page_hints if slot else ()


def gain_if_filled(slot_name: str) -> float:
    """How much confidence closing this gap would add. Lets a caller decide
    whether another fetch is worth its cost."""
    slot = _BY_NAME.get(slot_name)
    return slot.weight if slot else 0.0


# ── Multi-source agreement ─────────────────────────────────────────────────
def merge_value(candidates) -> dict:
    """Reconcile the same fact reported by several sources.

    ``candidates`` are ``(value, source, confidence)``. Agreement across
    independent sources RAISES confidence; disagreement lowers it and is kept
    visible rather than silently resolved, because quietly picking a winner is
    how a wrong founder name ends up in an email.
    """
    grouped = {}
    for value, source, conf in candidates:
        key = " ".join(str(value or "").strip().lower().split())
        if not key:
            continue
        entry = grouped.setdefault(key, {"value": str(value).strip(),
                                         "sources": [], "confidence": 0.0})
        if source and source not in entry["sources"]:
            entry["sources"].append(source)
        entry["confidence"] = max(entry["confidence"], float(conf or 0.0))

    if not grouped:
        return {"value": None, "sources": [], "confidence": 0.0,
                "agreement": "none", "conflict": False}

    ranked = sorted(grouped.values(),
                    key=lambda e: (len(e["sources"]), e["confidence"]),
                    reverse=True)
    best, others = ranked[0], ranked[1:]
    n = len(best["sources"])
    confidence = best["confidence"]
    if n >= 3:
        confidence, agreement = min(1.0, confidence + 0.25), "very high"
    elif n == 2:
        confidence, agreement = min(1.0, confidence + 0.15), "high"
    else:
        agreement = "single source"

    conflict = bool(others)
    if conflict:
        # Something else was also reported. Keep the better-supported value but
        # stop pretending we are sure.
        confidence = max(0.0, confidence - 0.25)
        agreement = "contradicted"
    return {"value": best["value"], "sources": best["sources"],
            "confidence": round(confidence, 3), "agreement": agreement,
            "conflict": conflict,
            "alternatives": [o["value"] for o in others]}
