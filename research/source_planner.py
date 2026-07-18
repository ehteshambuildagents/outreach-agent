"""Source planner: choose SUPPLEMENTAL research sources per prospect, by need.

The website crawl (research/pipeline.py) is the always-on grounding pass. AFTER
it runs, we finally know what we actually have and lack about THIS specific
prospect — and that, not the user's phrasing, is what should decide whether an
extra (paid/limited) source is worth calling. This module owns that decision.

Discipline: minimum sufficient sources, not maximum calls. Sources are considered
cheapest-gap-closing-first, and a later one is skipped the moment an earlier one
closes the gap:

  1. Apollo      — CONTACT gap: a named contact but no usable (verified/specific)
                   email. Stays behind APOLLO_ENRICH_ENABLED (default OFF).
  2. Tavily news — RECENCY gap: the site yielded no recent signal. Cheap, so it is
                   tried before X.
  3. X search    — RECENCY gap STILL open after news AND a named FOUNDER (someone
                   plausibly active): one targeted, cached query. Conservative by
                   design — never fired for a company with no named founder.

Every decision carries a reason string (so a before/after is explainable), and
every provider call degrades to a no-op — the planner can only ever ADD signal,
never break research.

Two independent gates keep spend deliberate: Apollo stays behind
APOLLO_ENRICH_ENABLED, and the paid recency escalation (news + X) behind
PLANNER_ESCALATION_ENABLED — both default OFF. With them off the planner still
runs and records its decisions; it just holds back the paid calls.
"""

import logging
import re
from urllib.parse import urlparse

from config.settings import APOLLO_ENRICH_ENABLED, PLANNER_ESCALATION_ENABLED
from research import apollo, tavily, x_search

log = logging.getLogger("research.source_planner")

# Length caps when folding external (untrusted) text into the research fields the
# writer/qualifier read. Kept short + single-line; downstream also re-sanitises.
_RECENT_FOCUS_CAP = 300
_HOOK_CAP = 200

# Generic tokens that don't distinguish a company (so a news item mentioning only
# these isn't treated as "about this company").
_SUFFIX_STOP = {"inc", "llc", "ltd", "corp", "co", "company", "the", "ai", "io",
                "app", "labs", "hq", "group", "technologies", "tech", "software"}


def run(data: dict, url: str) -> list:
    """Decide and execute supplemental sources for one prospect, mutating ``data``
    in place with anything found. Returns a list of decision records:

        [{"source", "fired": bool, "reason": str, "outcome"?: str}, ...]

    Never raises. The recency step is sequential: news runs first and, if it
    closes the gap, X is skipped (the reason string says so).
    """
    report = []
    try:
        report.append(_apollo_step(data, url))
        report.extend(_recency_steps(data, url))
    except Exception:  # noqa: BLE001 - the planner must never break research
        log.debug("source planner errored", exc_info=True)
    for d in report:
        log.info("source_planner: %s %s — %s", d["source"],
                 "FIRED" if d["fired"] else "skip", d["reason"])
    return report


# ── Apollo (contact gap) ───────────────────────────────────────────────────
def _apollo_step(data: dict, url: str) -> dict:
    fire, reason = _decide_apollo(data)
    if not fire:
        return _rec("apollo", False, reason)
    try:
        result = apollo.enrich_person(
            name=data.get("primary_contact_name") or data.get("founder_name"),
            domain=urlparse(url).hostname or "",
            organization_name=data.get("company_name"),
            linkedin_url=data.get("linkedin_url"),
        )
    except Exception:  # noqa: BLE001
        log.debug("apollo enrichment errored", exc_info=True)
        return _rec("apollo", True, reason, outcome="error")
    if result.get("status") == "ok" and result.get("person"):
        apollo.merge_into_research(data, result["person"])
        return _rec("apollo", True, reason, outcome="matched")
    return _rec("apollo", True, reason, outcome=result.get("status", "no_match"))


def _decide_apollo(data: dict):
    """(fire, reason) for the Apollo contact-enrichment step."""
    if not APOLLO_ENRICH_ENABLED:
        return False, "Apollo disabled (APOLLO_ENRICH_ENABLED off)"
    if not apollo.available():
        return False, "Apollo unavailable (no APOLLO_API_KEY)"
    name = data.get("primary_contact_name") or data.get("founder_name")
    if not name:
        return False, "no named contact to enrich"
    if data.get("contact_enrichment", {}).get("source") == "apollo":
        return False, "already Apollo-enriched"
    email = (data.get("public_contact_email") or "").strip()
    if email and not apollo.is_generic_email(email):
        return False, f"already have a specific email ({email})"
    have = "no email" if not email else f"generic email ({email})"
    return True, f"have contact '{name}' but {have}"


# ── Recency (news, then X) ─────────────────────────────────────────────────
def _recency_steps(data: dict, url: str) -> list:
    # Kill switch: the paid recency escalation (news + X) stays OFF until it has
    # been watched on real campaigns. The planner still ran (and Apollo was still
    # considered under its own flag); only the paid escalation is held back.
    if not PLANNER_ESCALATION_ENABLED:
        skip = "recency escalation disabled (PLANNER_ESCALATION_ENABLED off)"
        return [_rec("tavily_news", False, skip), _rec("x_search", False, skip)]
    gap, gap_reason = _recency_gap(data)
    if not gap:
        skip = f"no recency gap ({gap_reason})"
        return [_rec("tavily_news", False, skip), _rec("x_search", False, skip)]

    out = [_news_step(data, url, gap_reason)]
    # Re-check the gap: if news just closed it, X must not fire (cheaper source won).
    gap, gap_reason2 = _recency_gap(data)
    if not gap:
        out.append(_rec("x_search", False,
                        "recency gap closed by news — X not needed"))
        return out
    out.append(_x_step(data, gap_reason))
    return out


def _news_step(data: dict, url: str, gap_reason: str) -> dict:
    company = _company(data, url)
    if not tavily.available():
        return _rec("tavily_news", False, "Tavily unavailable (no TAVILY_API_KEY)")
    if not company:
        return _rec("tavily_news", False, "no company name to search news for")
    reason = f"recency gap ({gap_reason}); trying cheap news first"
    try:
        results = tavily.recent_news(company)
    except Exception:  # noqa: BLE001
        return _rec("tavily_news", True, reason, outcome="error")
    if results and _merge_news(data, results, _tokens(company, url)):
        return _rec("tavily_news", True, reason, outcome="found recent signal")
    # Reached the API but nothing that actually names the company — do NOT merge a
    # false positive (a fuzzy match on a short/common name), leave the gap open.
    return _rec("tavily_news", True, reason, outcome="no relevant recent news")


def _x_step(data: dict, gap_reason: str) -> dict:
    founder = (data.get("founder_name") or "").strip()
    if not x_search.available():
        return _rec("x_search", False, "X unavailable (no X_BEARER_TOKEN)")
    if not founder:
        return _rec("x_search", False,
                    "recency gap open but no named founder — X not fired (conservative)")
    company = _company(data, "")
    query = _x_query(founder, company)
    reason = (f"recency gap ({gap_reason}) still open after news + founder "
              f"'{founder}' (likely active); one cached query")
    try:
        result = x_search.search_recent_posts(query)
    except Exception:  # noqa: BLE001
        return _rec("x_search", True, reason, outcome="error")
    posts = result.get("posts") if isinstance(result, dict) else None
    if posts and _merge_posts(data, posts):
        return _rec("x_search", True, reason, outcome="found recent post")
    return _rec("x_search", True, reason,
                outcome=(result or {}).get("status", "no posts"))


def _recency_gap(data: dict):
    """(gap, reason). A gap means the site gave us nothing current to open on."""
    if (data.get("recent_focus") or "").strip():
        return False, "site already has recent_focus"
    if (data.get("metrics_or_traction") or "").strip():
        return False, "site already has traction/metrics"
    return True, "no recent_focus and no traction on the site"


# ── Merge helpers (fold external evidence into the research fields) ─────────
def _merge_news(data: dict, results: list, tokens: set) -> bool:
    # RELEVANCE GUARD: keep only items whose HEADLINE names the company. Tavily
    # fuzzy-matches short/common names (e.g. "Dub" -> a construction article, or a
    # game trailer mentioning an "English dub"), so a body-text match is unsafe for
    # common-word names. Real company news almost always names the company in the
    # title; requiring that trades a little recall for precision on purpose — a
    # false "recent signal" fed to the writer is worse than none.
    relevant = ([r for r in results if _mentions(r.get("title") or "", tokens)]
                if tokens else results)
    item = _freshest(relevant, "published_date")
    text = _flat((item.get("title") or item.get("content") or ""), _RECENT_FOCUS_CAP)
    if not text:
        return False
    _apply_recent(data, text, source="tavily_news",
                  ref=item.get("url"), when=item.get("published_date"))
    return True


def _merge_posts(data: dict, posts: list) -> bool:
    post = _freshest(posts, "created_at")
    text = _flat(post.get("text") or "", _RECENT_FOCUS_CAP)
    if not text:
        return False
    _apply_recent(data, text, source="x_search",
                  ref=post.get("url"), when=post.get("created_at"))
    return True


def _apply_recent(data: dict, text: str, *, source: str, ref, when) -> None:
    """Fill the recency fields the writer reads — only where empty, so a real
    site signal is never overwritten — and record provenance."""
    if not (data.get("recent_focus") or "").strip():
        data["recent_focus"] = text
    if not (data.get("unique_hook") or "").strip():
        data["unique_hook"] = text[:_HOOK_CAP]
    hooks = data.get("additional_hooks")
    hooks = hooks if isinstance(hooks, list) else []
    hook = text[:_HOOK_CAP]
    if hook not in hooks:
        data["additional_hooks"] = (hooks + [hook])[:6]
    data["recency_enrichment"] = {"source": source, "url": ref, "published_date": when}


# ── small helpers ──────────────────────────────────────────────────────────
def _rec(source: str, fired: bool, reason: str, *, outcome: str = None) -> dict:
    rec = {"source": source, "fired": fired, "reason": reason}
    if outcome:
        rec["outcome"] = outcome
    return rec


def _company(data: dict, url: str) -> str:
    company = (data.get("company_name") or "").strip()
    if company:
        return company
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0] if host else ""


def _tokens(company: str, url: str = "") -> set:
    """Distinctive lowercase tokens that identify the company — its name words
    (minus generic suffixes) plus the domain's second-level label. Used to check a
    news item is actually about this company before trusting it."""
    toks = {w for w in re.findall(r"[a-z0-9]+", (company or "").lower())
            if len(w) >= 3 and w not in _SUFFIX_STOP}
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else ""
    if len(label) >= 3:
        toks.add(label)
    return toks


def _mentions(text: str, tokens: set) -> bool:
    blob = (text or "").lower()
    return any(re.search(r"\b" + re.escape(t) + r"\b", blob) for t in tokens)


def _x_query(founder: str, company: str) -> str:
    founder = (founder or "").strip()
    company = (company or "").strip()
    return f"{founder} {company}".strip() if company else founder


def _freshest(items: list, date_key: str):
    """The item with the most recent parseable date, else the first item. Dates are
    ISO-ish strings; lexical comparison is correct for ISO-8601 and safe otherwise."""
    items = [i for i in (items or []) if isinstance(i, dict)]
    if not items:
        return {}
    dated = [i for i in items if (i.get(date_key) or "").strip()]
    if dated:
        return max(dated, key=lambda i: i.get(date_key) or "")
    return items[0]


def _flat(text: str, cap: int) -> str:
    """Collapse to a single trimmed line and cap length (external text is untrusted)."""
    s = " ".join(str(text or "").split())
    return s[:cap].strip()
