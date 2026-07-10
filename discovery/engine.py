"""The Prospect Discovery Agent — the deterministic orchestrator.

    discover(owner, query) -> DiscoveryResult

It gathers candidates from the search providers, drops anything the caller has
already seen (per-user dedupe + explicit excludes + already-researched), applies
exclude-keyword filters, ranks by match confidence, paginates, persists the page
to the database, and returns structured JSON. No LLM, no deep research, no
writing — those belong to the other agents.
"""

import logging
from dataclasses import dataclass, field

from config.settings import DISCOVERY_MIN_CONFIDENCE, DISCOVERY_PROVIDER_POOL
from discovery import sources
from discovery.models import DiscoveryQuery, registrable_domain
from discovery.store import ProspectStore

log = logging.getLogger("discovery.engine")


@dataclass
class DiscoveryResult:
    status: str                       # ok | empty | error
    prospects: list = field(default_factory=list)     # list[Prospect]
    page: int = 0
    limit: int = 0
    returned: int = 0
    has_more: bool = False
    providers: dict = field(default_factory=dict)
    reason: str = ""

    def public(self) -> dict:
        return {
            "status": self.status,
            "page": self.page,
            "limit": self.limit,
            "returned": self.returned,
            "has_more": self.has_more,
            "providers": self.providers,
            "reason": self.reason,
            "prospects": [p.public() for p in self.prospects],
        }


def discover(owner, query, *, store=None, exclude_domains=None,
             skip_seen=True) -> DiscoveryResult:
    """Find companies matching ``query`` for ``owner``.

    exclude_domains: extra domains to drop (e.g. the company being worked on).
    skip_seen: drop domains this owner has already discovered (durable dedupe).
    Never raises for normal failures.
    """
    if isinstance(query, dict):
        query = DiscoveryQuery(**query)
    if query.is_empty():
        return DiscoveryResult("error", reason="No ICP filters given — say what "
                               "kind of companies to find.", limit=query.limit)

    avail = sources.providers_available()
    if not any(avail.values()):
        return DiscoveryResult("error", providers=avail,
                               reason="No search provider is configured.",
                               limit=query.limit)

    store = store or _safe_store()
    seen = set(registrable_domain(d) for d in (exclude_domains or []))
    if skip_seen and store is not None:
        try:
            seen |= store.seen_domains(owner)
        except Exception:  # noqa: BLE001 - dedupe is best-effort, never fatal
            log.info("seen_domains lookup failed; continuing without dedupe")

    search_query = query.search_string()
    pool = min(DISCOVERY_PROVIDER_POOL, max(query.limit * 2, DISCOVERY_PROVIDER_POOL))
    log.info("discovery_start query=%r limit=%s pool=%s", search_query, query.limit, pool)
    try:
        candidates = sources.search_candidates(query, pool_size=pool)
    except Exception:  # noqa: BLE001 - providers are wrapped, belt-and-braces
        return DiscoveryResult("error", providers=avail, limit=query.limit,
                               reason="The search providers couldn't be reached.")

    ranked = _dedupe_filter_rank(candidates, query, seen)
    log.info(
        "discovery_ranked query=%r raw_company_candidates=%s ranked_valid=%s seen=%s",
        search_query, len(candidates), len(ranked), len(seen),
    )
    # When durable dedupe is on (the chat "find another 20" flow), already-returned
    # companies are excluded via ``seen``, so that IS the pagination cursor —
    # always take the top `limit` unseen. Only a stateless caller (skip_seen=False)
    # pages by an explicit offset over the full ranked pool.
    start = 0 if skip_seen else query.page * query.limit
    page_items = ranked[start:start + query.limit]
    has_more = len(ranked) > start + query.limit

    if store is not None and page_items:
        for p in page_items:
            p.owner = owner
        try:
            store.save_many(page_items)
        except Exception:  # noqa: BLE001 - persistence must not break discovery
            log.info("prospect save failed (returning results anyway)")

    if not page_items:
        log.info("discovery_result query=%r status=empty valid_company_prospects=0", search_query)
        return DiscoveryResult(
            "empty", page=query.page, limit=query.limit, providers=avail,
            reason=("No more new matches — try broadening the filters or a new query."
                    if query.page > 0 else
                    "No matching companies found for those filters."))
    log.info("discovery_result query=%r status=ok returned=%s has_more=%s", search_query, len(page_items), has_more)
    return DiscoveryResult("ok", prospects=page_items, page=query.page,
                           limit=query.limit, returned=len(page_items),
                           has_more=has_more, providers=avail)


# ── deterministic dedupe + filter + rank ───────────────────────────────
def _dedupe_filter_rank(candidates, query, seen):
    by_domain = {}
    for p in candidates:
        if not p.domain or p.domain in seen:
            continue
        if _excluded_by_keyword(p, query.exclude_keywords):
            continue
        if p.confidence < DISCOVERY_MIN_CONFIDENCE:
            continue
        # Keep the strongest candidate per domain; if two sources found it, that
        # is corroboration, so bump confidence a little.
        existing = by_domain.get(p.domain)
        if existing is None:
            by_domain[p.domain] = p
        else:
            if p.discovery_source != existing.discovery_source:
                existing.confidence = min(1.0, existing.confidence + 0.05)
                if existing.discovery_source not in existing.basic_signals:
                    pass
            if p.confidence > existing.confidence:
                p.confidence = min(1.0, p.confidence + 0.05)
                by_domain[p.domain] = p
    ranked = sorted(by_domain.values(),
                    key=lambda x: (x.confidence, x.company_name.lower()),
                    reverse=True)
    return ranked


def _excluded_by_keyword(prospect, exclude_keywords) -> bool:
    if not exclude_keywords:
        return False
    hay = (prospect.company_name + " " + " ".join(prospect.basic_signals) + " "
           + prospect.industry).lower()
    return any(kw in hay for kw in exclude_keywords)


def _safe_store():
    try:
        return ProspectStore()
    except Exception:  # noqa: BLE001 - discovery still works without persistence
        log.info("prospect store unavailable; discovery will not persist")
        return None
