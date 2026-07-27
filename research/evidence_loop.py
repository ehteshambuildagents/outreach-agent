"""Execute the evidence plan: close the biggest gap, merge what comes back, stop.

[research/gaps.py] decides WHICH fact is missing and which tool supplies it. That
was only half a planner: the non-crawl actions it produced were computed and
discarded, so Firecrawl, Tavily and X were never actually reached by a gap. This
is the loop that runs them.

Apollo is deliberately NOT an executor here. Its two real jobs already live
elsewhere and neither fits this loop: ORGANIZATION search + dated job postings
belong to the discovery engine (and arrive as ``signals``), and People Match
ENRICHES a contact we can already name, which research/source_planner.py does.
People Match rejects a domain-only request outright, so routing a founder GAP to
it produced an action that could never succeed.

    run(graph, url=..., extract_fn=...) -> {"records": [...], "stop_reason": ...}

One action per cycle, re-assessing in between, because the whole point is that
the SECOND action depends on what the first returned. It ends when confidence is
sufficient, the budget is spent, or nothing useful is left to try.

SAFETY IS THE DESIGN CONSTRAINT HERE, not an afterthought. A loop that picks its
own next paid call is exactly the shape that quietly runs up a bill, so:

  * the whole loop is behind PLANNER_ESCALATION_ENABLED (default OFF);
  * at most EVIDENCE_MAX_ACTIONS per run, EVIDENCE_MAX_PER_PROVIDER per provider;
  * the same (company, slot, provider, target) is never tried twice, and neither
    is the same RESOLVED request;
  * a slot that already came back empty is not asked about again;
  * a Firecrawl target must be a page the site really has (sitemap/links, or a
    free existence check) — guessed paths cost money on apple.com for nothing;
  * a gap is skipped when it cannot lift confidence to the bar within the
    remaining budget, or is worth less than EVIDENCE_MIN_GAIN;
  * every provider call still goes through providers_common (spend caps, retries).

Every decision is recorded as one of four disjoint outcomes — succeeded,
no_evidence, failed, skipped — so a call that ran fine and simply found nothing
is never reported as a broken one, and cost is always attributable.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from config.settings import (
    EVIDENCE_MAX_ACTIONS,
    EVIDENCE_MAX_PER_PROVIDER,
    EVIDENCE_MIN_GAIN,
    PLANNER_ESCALATION_ENABLED,
)
from research import gaps
from research.evidence import Evidence

log = logging.getLogger("research.evidence_loop")

# Which graph node each slot's evidence lands in, and a rough per-call cost used
# only for the run's cost report (the real meter is providers_common).
_SLOT_NODE = {
    "pricing": "pricing_model",
    "customers": "notable_customers",
    "product_depth": "integrations",
    "recent_signal": "recent_focus",
    "funding": "company_stage",
    "founder": "founder_name",
    "target_customer": "target_customer",
    "positioning": "product_category",
    "what_they_do": "what_they_do",
}
_COST_USD = {"firecrawl": 0.002, "tavily": 0.004, "x": 0.005}

# Providers this loop can actually RUN. gaps.plan legitimately suggests others
# (Exa is a semantic-discovery tool, useful for finding companies, not for
# extracting a fact about one), and planning something unrunnable used to burn a
# budget slot and record a failure. Anything not here is skipped without cost.
_EXECUTABLE = frozenset({"firecrawl", "tavily", "x"})



@dataclass
class ActionRecord:
    """One decision, whether or not it ran. This is the audit trail."""
    slot: str
    provider: str
    target: str = ""
    status: str = "skipped"          # succeeded | no_evidence | failed | skipped
    reason: str = ""
    value: str = ""
    source_url: str = ""
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    cost_usd: float = 0.0

    @property
    def gain(self) -> float:
        return round(self.confidence_after - self.confidence_before, 3)

    def public(self) -> dict:
        return {"slot": self.slot, "provider": self.provider, "target": self.target,
                "status": self.status, "reason": self.reason, "value": self.value,
                "source_url": self.source_url,
                "confidence_before": round(self.confidence_before, 3),
                "confidence_after": round(self.confidence_after, 3),
                "gain": self.gain, "cost_usd": round(self.cost_usd, 4)}


@dataclass
class _Budget:
    """What is left to spend on this run."""
    actions: int = EVIDENCE_MAX_ACTIONS
    per_provider: dict = field(default_factory=dict)
    tried: set = field(default_factory=set)     # (company, slot, provider, target)
    calls: set = field(default_factory=set)     # (provider, resolved request)
    barren: set = field(default_factory=set)    # slots that already yielded nothing
    refusing: set = field(default_factory=set)  # providers shut off at the account

    def may_use(self, provider: str) -> bool:
        return self.per_provider.get(provider, 0) < EVIDENCE_MAX_PER_PROVIDER

    def spend(self, provider: str) -> None:
        self.actions -= 1
        self.per_provider[provider] = self.per_provider.get(provider, 0) + 1


def run(graph, *, url: str, company: str = "", signals: dict = None,
        extract_fn=None, narrate=None, providers=None, known_urls=()) -> dict:
    """Close evidence gaps with real provider calls. Never raises.

    ``extract_fn(pages) -> None`` folds fetched page text into ``graph`` using the
    pipeline's own extractor, so a Firecrawl page becomes evidence by exactly the
    same route as a crawled one. ``narrate(text)`` receives a short line ONLY when
    an action genuinely runs. ``known_urls`` are pages the site actually exposes
    (sitemap + internal links), so a paid scrape aims at a real page instead of a
    guessed path.
    """
    signals = dict(signals or {})
    say = narrate if callable(narrate) else (lambda *a, **k: None)
    records, budget = [], _Budget()
    company = company or (urlparse(url).hostname or "").replace("www.", "")

    if not PLANNER_ESCALATION_ENABLED:
        # Silent by default: a disabled cost guard is our business, not the user's.
        return _result(records, "escalation_disabled",
                       "evidence-gap execution is off (PLANNER_ESCALATION_ENABLED)")

    avail = providers if providers is not None else _available()
    while budget.actions > 0:
        ledger = gaps.assess(graph, signals)
        before = ledger.confidence
        if ledger.is_confident():
            return _result(records, "confidence_reached",
                           f"confidence {before:.0%} met the bar")

        chosen = _choose(ledger, avail, budget, company, records, url, known_urls)
        if chosen is None:
            return _result(records, "no_useful_actions",
                           "nothing left worth a paid call")
        action, resolved = chosen

        budget.tried.add(_key(company, action.slot, action.kind, resolved))
        # Also remember the CONCRETE call. Two different gaps can resolve to the
        # very same request (a customers gap and a recent-signal gap both asking
        # Tavily the same question), and paying twice for one answer is waste.
        budget.calls.add((action.kind, resolved))
        budget.spend(action.kind)
        rec = ActionRecord(slot=action.slot, provider=action.kind,
                           target=str(resolved or ""),
                           confidence_before=before, confidence_after=before)
        say(_narration(action))
        try:
            filled = _execute(action, graph, url=url, company=company,
                              signals=signals, extract_fn=extract_fn, rec=rec,
                              resolved=resolved)
            # The request WAS made and answered. Whether it carried anything
            # useful is a separate question from whether it worked: a pricing
            # page that simply has no price is not an infrastructure failure,
            # and calling it one hid the fact that the call still cost money.
            if filled:
                rec.status = "succeeded"
            else:
                # An empty answer and a REFUSED request look identical to a
                # caller (both are an empty list). Tavily was answering HTTP 432
                # "exceeds your plan's usage limit" for every query and the loop
                # reported "no recent coverage found", which blamed the world for
                # an account problem. Ask the provider layer which it was.
                refusal = _provider_refusal(action.kind)
                rec.status = "failed" if refusal else "no_evidence"
                rec.reason = (f"{action.kind} {refusal}" if refusal
                              else rec.reason or "the response held no usable evidence")
                if refusal:
                    # Say WHICH sources carry on, so the user never reads an
                    # account problem as "there is no recent news about them".
                    say(_fallback_note(action.kind, avail))
                    # ...and do not ask this provider anything else this run. It
                    # is refusing at the account level, so every further call is
                    # a wasted attempt and a duplicate line in the stream.
                    budget.refusing.add(action.kind)
        except Exception:  # noqa: BLE001 - one provider must never end the run
            log.info("evidence action %s/%s errored", action.kind, action.slot,
                     exc_info=True)
            rec.status, rec.reason = "failed", "the provider errored"
        # Cost is charged for any request that actually left the process.
        rec.cost_usd = _COST_USD.get(action.kind, 0.0)
        if rec.status != "succeeded":
            # This slot has now been tried and yielded nothing; do not pay to ask
            # the same kind of question again this run.
            budget.barren.add(action.slot)
        rec.confidence_after = gaps.assess(graph, signals).confidence
        records.append(rec)

    return _result(records, "budget_exhausted",
                   f"used the {EVIDENCE_MAX_ACTIONS}-action budget for this run")


# ── choosing ───────────────────────────────────────────────────────────────
def _choose(ledger, avail, budget, company, records, url, known_urls=()):
    """The best (action, resolved_target) we are actually allowed to run, or None."""
    seen_note = set()
    for action in gaps.plan(ledger, providers=avail, limit=12):
        if action.kind == "crawl":
            continue                                   # the crawl loop owns those
        if action.kind not in _EXECUTABLE:
            continue                                   # nothing here can run it
        if action.kind in budget.refusing:
            continue                       # already refused once; do not re-ask
        if not avail.get(action.kind):
            continue                                   # unconfigured -> never claimed
        if action.slot in budget.barren:
            continue        # already asked about this fact and got nothing back
        if gaps.gain_if_filled(action.slot) < _reachable_gain(ledger, budget):
            _note(records, action, "cannot reach the confidence bar within the "
                                   "remaining budget", ledger.confidence)
            continue
        resolved = _resolve_target(action, url, company, known_urls)
        if resolved is None:
            _note(records, action, f"no {action.target} page exists on the site",
                  ledger.confidence)
            continue                                   # skipped BEFORE paying
        if _key(company, action.slot, action.kind, resolved) in budget.tried:
            continue                                   # never the same call twice
        if (action.kind, resolved) in budget.calls:
            continue                                   # identical request already made
        if not budget.may_use(action.kind):
            if action.kind not in seen_note:
                seen_note.add(action.kind)
                _note(records, action, f"{action.kind} already used its per-run cap",
                      ledger.confidence)
            continue
        if gaps.gain_if_filled(action.slot) < EVIDENCE_MIN_GAIN:
            _note(records, action, "worth less than the cost of the call",
                  ledger.confidence)
            continue
        return action, resolved
    return None


def _reachable_gain(ledger, budget) -> float:
    """The smallest gain still worth buying.

    If even the best remaining gap cannot lift confidence to the bar within the
    actions left, paying for a partial climb is not worth it. Returns the floor a
    slot's weight must clear; EVIDENCE_MIN_GAIN is the absolute minimum.
    """
    shortfall = gaps.CONFIDENCE_TARGET - ledger.confidence
    best_possible = sum(sorted((gaps.gain_if_filled(s) for s in ledger.missing),
                               reverse=True)[:max(0, budget.actions)])
    if best_possible < shortfall:
        # Cannot get there this run. Only buy something genuinely substantial.
        return max(EVIDENCE_MIN_GAIN, 0.10)
    return EVIDENCE_MIN_GAIN


def _resolve_target(action, url, company, known_urls=()):
    """The CONCRETE request this action will make — a URL for Firecrawl, a query
    for the search providers. ``None`` means "do not pay for this".

    For Firecrawl the URL must be one the site actually has. Guessing paths cost
    real money on apple.com, where /about and /customers were scraped and held
    nothing: the page either did not exist or was not the page we wanted. So the
    target must come from pages the site itself exposes (sitemap + internal
    links), and otherwise it is verified with a FREE request before the paid
    scrape.
    """
    if action.kind == "firecrawl":
        hint = str(action.target or "").strip("/").lower()
        for candidate in known_urls or ():
            if hint and hint in str(candidate).lower():
                return candidate                       # the site really has it
        guess = _page_url(url, hint)
        return guess if _page_exists(guess) else None
    if action.kind == "tavily":
        topic = "funding round" if action.slot == "funding" else "news announcement launch"
        return f"{company} {topic}"
    if action.kind == "x":
        return f"{company} launch OR announcement OR hiring"
    return str(action.target or "")


def _note(records, action, reason, confidence) -> None:
    """Record a skip, so 'why didn't it check X' is answerable."""
    records.append(ActionRecord(
        slot=action.slot, provider=action.kind, target=str(action.target or ""),
        status="skipped", reason=reason,
        confidence_before=confidence, confidence_after=confidence))


def _key(company, slot, provider, target) -> tuple:
    return (company.lower(), slot, provider, str(target or "").lower())


# ── executing ──────────────────────────────────────────────────────────────
def _execute(action, graph, *, url, company, signals, extract_fn, rec,
             resolved) -> bool:
    if action.kind == "firecrawl":
        return _run_firecrawl(action, graph, target=resolved,
                              extract_fn=extract_fn, rec=rec)
    if action.kind == "tavily":
        return _run_tavily(action, graph, query=resolved, rec=rec)
    if action.kind == "x":
        return _run_x(action, graph, query=resolved, rec=rec)
    rec.reason = f"no executor for {action.kind}"
    return False


def _run_firecrawl(action, graph, *, target, extract_fn, rec) -> bool:
    """Intentionally fetch the page that carries this evidence (a pricing page for
    a pricing gap), not merely as a fallback for a blocked fetch."""
    from research import firecrawl
    page = firecrawl.scrape(target)
    if not page or not (page.get("markdown") or "").strip():
        rec.reason = f"no {action.target} page found"
        return False
    text = page["markdown"]
    rec.source_url = page.get("url") or target
    if not callable(extract_fn):
        rec.reason = "no extractor available"
        return False
    before = _snapshot(graph)
    extract_fn([(rec.source_url, text)])
    added = _snapshot(graph) - before
    if not added:
        rec.reason = "the page held no new evidence"
        return False
    rec.value = ", ".join(sorted(added))[:120]
    return True


def _run_tavily(action, graph, *, query, rec) -> bool:
    from research import tavily
    results = tavily.search(query, max_results=4) or []
    best = next((r for r in results if (r or {}).get("title")), None)
    if not best:
        # The caller classifies refusal-vs-nothing via _provider_refusal; this
        # only supplies the fallback wording for a genuine absence of coverage.
        rec.reason = "no recent coverage found"
        return False
    dated = str(best.get("published_date") or "")[:10]
    value = best.get("title").strip()
    if dated:
        value = f"{value} ({dated})"
    _merge(graph, _SLOT_NODE.get(action.slot, "recent_focus"), value,
           source_url=best.get("url") or "", provider="tavily",
           quote=str(best.get("content") or "")[:300], confidence=0.6)
    rec.value, rec.source_url = value, best.get("url") or ""
    return True


def _run_x(action, graph, *, query, rec) -> bool:
    from research import x_search
    result = x_search.search_recent_posts(query) or {}
    posts = result.get("posts") or []
    if result.get("status") != "ok" or not posts:
        rec.reason = "no recent posts found"
        return False
    post = posts[0]
    value = str(post.get("text") or "").strip()[:160]
    if not value:
        rec.reason = "no usable post text"
        return False
    _merge(graph, "recent_focus", value, source_url=post.get("url") or "",
           provider="x", quote=value, confidence=0.5)
    rec.value, rec.source_url = value, post.get("url") or ""
    return True


# ── merging into the REAL graph ────────────────────────────────────────────
def _merge(graph, node, value, *, source_url, provider, quote, confidence) -> None:
    """Fold a provider value into the existing ResearchGraph.

    Uses gaps.merge_value to reconcile it against what is already there, so
    agreement across independent sources raises confidence and disagreement
    lowers it and is MARKED rather than silently resolved. There is no parallel
    ledger: Evidence in the ResearchGraph stays the single source of truth, and a
    weaker new value never overwrites a stronger existing one.
    """
    existing = list(graph.nodes.get(node) or [])
    candidates = [(e.value, e.source_url or "existing", e.confidence) for e in existing]
    candidates.append((value, provider, confidence))
    verdict = gaps.merge_value(candidates)

    same = [e for e in existing
            if e.value.strip().lower() == str(value).strip().lower()]
    if same:
        # Corroboration: strengthen what we already had rather than duplicating it.
        for e in same:
            e.corroborations += 1
            e.confidence = max(e.confidence, verdict["confidence"])
        return

    conflict = bool(verdict.get("conflict"))
    if conflict:
        for e in existing:                     # both sides stay visible
            e.conflict = True
    graph.add(node, Evidence(
        value=str(value), source_url=source_url, quote=quote,
        confidence=min(float(confidence), verdict["confidence"]) if conflict
        else float(confidence),
        corroborations=1, conflict=conflict))


def _snapshot(graph) -> set:
    """Which nodes currently hold a value — used to tell whether a fetched page
    actually added anything."""
    return {n for n, items in (graph.nodes or {}).items() if items}


# ── plumbing ───────────────────────────────────────────────────────────────
def _fallback_note(down: str, avail: dict) -> str:
    """Name the provider that is unavailable and the ones still working, so the
    stream never implies the absence of a fact when the real problem is ours."""
    others = [n.capitalize() for n in ("firecrawl", "exa", "tavily", "x")
              if n != down and avail.get(n)]
    carry_on = (" and ".join([", ".join(others[:-1]), others[-1]])
                if len(others) > 1 else (others[0] if others else ""))
    if not carry_on:
        return (f"{down.capitalize()} is unavailable, and no other source can "
                "fill that gap, so I'm working from the site alone.")
    return f"{down.capitalize()} is unavailable, so I'm continuing with {carry_on}."


def _provider_refusal(kind: str) -> str:
    """Whether this provider REFUSED the last request (quota/auth), rather than
    answering with nothing. Returns a short reason, or "" if it answered fine."""
    name = {"x": "x"}.get(kind, kind)
    try:
        from research.providers_common import last_error
        return last_error(name)
    except Exception:  # noqa: BLE001
        return ""


def _page_exists(url: str) -> bool:
    """A FREE check that a guessed path is really there, so a 404 is never paid
    for. Uses the ordinary fetcher (SSRF-safe, retries transient failures); if it
    cannot be read at all we decline rather than gamble a paid scrape on it."""
    try:
        from research.fetcher import fetch_static
        ok, _ = fetch_static(url)
        return bool(ok)
    except Exception:  # noqa: BLE001 - a checker must never break the loop
        return False


def _page_url(base: str, hint: str) -> str:
    parsed = urlparse(base if "://" in base else "https://" + base)
    return f"{parsed.scheme}://{parsed.netloc}/{str(hint or '').strip('/')}"


def _narration(action) -> str:
    """A short, honest line for the stream. Emitted only as an action RUNS."""
    slot = gaps.slot_label(action.slot)
    if action.kind == "firecrawl":
        return f"{slot.capitalize()} is still unclear, so I'm checking their {action.target} page."
    if action.kind == "tavily":
        return f"I still need {slot}, so I'm checking recent public coverage."
    if action.kind == "x":
        return f"I still need {slot}, so I'm checking their recent posts."
    return f"Looking for {slot}."


def _available() -> dict:
    from research import firecrawl, tavily, x_search
    from research.providers_common import provider_status
    status = provider_status()
    return {"firecrawl": firecrawl.available(), "tavily": tavily.available(),
            "x": x_search.available(), "exa": bool(status.get("exa"))}


def _result(records, stop_reason, detail) -> dict:
    """The run's outcome in terms that cannot be misread.

    The earlier shape reported "executed=0 failed=3 cost=$0.008", which implied
    three broken calls when in fact three requests ran fine and simply found
    nothing. These four states are disjoint and mean exactly one thing each:

      succeeded    ran, returned evidence, merged        (cost charged)
      no_evidence  ran, answered, nothing usable in it   (cost charged)
      failed       the request itself errored            (cost charged)
      skipped      never left the process                (no cost)

    ``attempted`` is everything that actually made a request, so cost is always
    attributable to it.
    """
    def n(status):
        return sum(1 for r in records if r.status == status)

    succeeded, no_evidence, failed, skipped = (
        n("succeeded"), n("no_evidence"), n("failed"), n("skipped"))
    return {
        "records": [r.public() for r in records],
        "stop_reason": stop_reason,
        "detail": detail,
        "attempted": succeeded + no_evidence + failed,
        "succeeded": succeeded,
        "no_evidence": no_evidence,
        "failed": failed,
        "skipped": skipped,
        "confidence_gained": round(sum(r.gain for r in records
                                       if r.status == "succeeded"), 3),
        "estimated_cost_usd": round(sum(r.cost_usd for r in records), 4),
    }
