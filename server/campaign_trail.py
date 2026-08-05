"""Canonical research-trail events for the Campaign and Prospects surfaces.

The Chat trail (chat/research_trail.py) emits canonical events LIVE from the agent's
tool loop and persists them on the conversation. Campaigns and discovery do not run
that loop: a campaign is a synchronous pipeline (discovery -> research -> qualify ->
strategy -> writer -> guard) whose REAL per-stage outcomes are already persisted on
the campaign result, and a prospect row is a persisted discovery result. Rather than
invent a second event system or fabricate "AI thinking" steps, this module DERIVES
the same canonical event shape from those persisted, real outcomes.

Because the derivation is a pure function of already-persisted data, a restored
campaign (or the Prospects list) shows byte-for-byte the same trail every time, with
no live animation to replay and no stored duplicate to reconcile. Every event maps to
a stage that genuinely ran; every source URL is scheme-validated through the same
chat.research_trail helpers before it can reach the client.
"""

import hashlib

from chat import research_trail as trail


def _run_id(seed: str) -> str:
    """A STABLE per-entity run id (unlike the random one the live trail mints), so a
    re-derived trail keeps the same grouping across reads/restores."""
    return "cmp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _abs(url):
    """A best-effort absolute URL: discovery/research sometimes store a bare host
    (``acme.com``) and safe_url rightly rejects a scheme-less string, so add https
    before validation. Returns None for anything empty."""
    if not url:
        return None
    u = str(url).strip()
    if not u:
        return None
    return u if u.lower().startswith(("http://", "https://")) else "https://" + u


def _event(run_id, event_type, label, status, *, target=None, detail=None,
           provider=None, sources=None, confidence=None):
    """One canonical trail event with a DETERMINISTIC id (derived, not live), matching
    chat.research_trail.event's shape exactly so the frontend renders it identically."""
    return {
        "event_id": f"ev_{event_type}_{run_id[4:]}",
        "run_id": run_id,
        "event_type": str(event_type),
        "label": str(label),
        "status": status,
        "target": target or None,
        "detail": (str(detail) if detail else None),
        "provider": provider or None,
        "sources": trail.dedupe_sources(sources or []),
        "confidence": confidence,
        "ts": 0,   # derived from persisted data, not a live moment in time
    }


def prospect_trail(prospect: dict) -> list[dict]:
    """Canonical trail for ONE campaign prospect, built from the stages that really
    ran on it (each present sub-result), with the research step's real evidence links.

    Only research and writer can *fail to execute* (their step returns a non-ok
    status); qualification, strategy and guard always complete and produce a decision,
    so they are COMPLETED with that decision in the detail — the gating OUTCOME lives
    in the prospect's final_status/badge, not in a fake "failed" step."""
    if not isinstance(prospect, dict):
        return []
    research = prospect.get("research") or {}
    domain = prospect.get("domain") or prospect.get("website") or ""
    company = (prospect.get("company_name") or research.get("company_name")
               or domain or "this company")
    rid = _run_id("campaign:" + (domain or company))
    events: list[dict] = []

    if research:
        sources = [trail.source(company, _abs(prospect.get("website")), official=True)]
        for page in (research.get("pages_crawled") or [])[:6]:
            sources.append(trail.source(None, _abs(page), official=True))
        ok = research.get("status") == "ok"
        score = research.get("research_score")
        events.append(_event(
            rid, "research", "Researched the company",
            trail.COMPLETED if ok else trail.FAILED,
            target=company, provider="research",
            detail=(f"Research score {score}" if ok and score is not None
                    else (None if ok else (research.get("reason")
                          or "Research did not produce usable evidence"))),
            sources=sources))

    qual = prospect.get("qualification") or {}
    if qual:
        rec = qual.get("recommendation")
        events.append(_event(
            rid, "qualification", "Qualified against your ICP", trail.COMPLETED,
            target=company, provider="qualification",
            detail=(f"{rec} (score {qual.get('qualification_score')})" if rec else None)))

    strat = prospect.get("strategy") or {}
    if strat:
        events.append(_event(
            rid, "strategy", "Chose an outreach strategy", trail.COMPLETED,
            target=company, provider="strategy",
            detail=strat.get("recommended_action") or None))

    email = prospect.get("email") or {}
    if email:
        ok = email.get("status") == "ok"
        events.append(_event(
            rid, "writer", "Wrote the email",
            trail.COMPLETED if ok else trail.FAILED,
            target=company, provider="writer",
            detail=(email.get("subject") if ok
                    else (email.get("reason") or "Writer did not produce a sendable email"))))

    guard = prospect.get("guard") or {}
    if guard:
        decision = guard.get("decision")
        risk = guard.get("overallRisk")
        blocked = prospect.get("final_status") == "guard_blocked"
        events.append(_event(
            rid, "guard", "Deliverability & cost guard", trail.COMPLETED,
            target=company, provider="guard",
            detail=((prospect.get("reason") or f"{decision}") if blocked
                    else (f"{decision} · risk {risk}/100" if decision else None))))

    return events


def discovery_trail(prospect: dict) -> list[dict]:
    """Canonical trail for ONE discovered prospect: a single, real 'discovered'
    event naming the provider that found it, with the company's own site and any
    corroborating provider/funding/activity links as validated evidence."""
    if not isinstance(prospect, dict):
        return []
    company = prospect.get("company_name") or ""
    website = prospect.get("website")
    domain = prospect.get("domain") or website or company
    provider = prospect.get("discovery_source") or None
    rid = _run_id("discovery:" + (domain or company))

    sources = []
    if website:
        sources.append(trail.source(company or None, _abs(website), official=True))
    for s in (prospect.get("sources") or [])[:6]:
        if isinstance(s, dict) and s.get("url"):
            sources.append(trail.source(s.get("provider"), _abs(s.get("url"))))
    funding = prospect.get("funding") or {}
    if funding.get("source_url"):
        stage = funding.get("stage") or funding.get("latest_stage") or "Funding"
        sources.append(trail.source(f"Funding: {stage}", _abs(funding.get("source_url"))))
    activity = prospect.get("recent_activity") or {}
    if activity.get("url"):
        sources.append(trail.source(activity.get("summary") or "Recent activity",
                                    _abs(activity.get("url"))))

    label = f"Discovered via {provider}" if provider else "Discovered"
    conf = prospect.get("confidence")
    return [_event(
        rid, "discovery", label, trail.COMPLETED, target=company or None,
        provider=provider, detail=prospect.get("why_it_matches") or None,
        sources=sources, confidence=conf if isinstance(conf, (int, float)) else None)]
