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
def discover_leads(owner: str, query, *, limit=None, exclude_domains=None):
    """Run the existing Discovery agent -> a list of lead dicts (company+website).
    Returns (status, leads, reason, has_more). Never raises."""
    if isinstance(query, DiscoveryQuery):
        q = query
    else:
        q = DiscoveryQuery(raw=str(query or ""),
                           limit=limit or RESEARCH_LIST_MAX)
    if limit:
        q.limit = max(1, int(limit))
    try:
        result = discovery_engine.discover(owner or "anon", q,
                                           exclude_domains=exclude_domains or [])
    except Exception:  # noqa: BLE001 - discovery is designed not to raise
        return "error", [], "Prospect discovery couldn't run just now.", False
    if result.status == "error":
        return "error", [], result.reason, False
    if result.status == "empty":
        return "empty", [], result.reason, False
    leads = [{"company_name": p.company_name, "website": p.website,
              "discovery": p.public()} for p in result.prospects]
    return "ok", leads, "", bool(result.has_more)


# ── Entry point B (and the core): research + qualify a list of companies ───
def research_and_qualify(leads, *, icp=None, limit=None, user_id=None,
                         research_fn=None, qualify_fn=None, resolve_fn=None) -> dict:
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
        for lead in leads:
            entries.append(_research_one(lead, icp, research_fn, qualify_fn, resolve_fn))
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
