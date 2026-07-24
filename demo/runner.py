"""The live-demo pipeline: ICP -> discover -> research + qualify N candidates ->
surface the best-fit, its real signal, and a real drafted opener.

``run_demo`` is a GENERATOR of ``(event, payload)`` tuples so the API layer can
stream each step to the visitor as it completes — the point of the demo is to let
someone WATCH the real qualification happen, not hand them one opaque answer.

It reuses the production agents unchanged:
  * discovery.engine.discover        (deterministic; Tavily + Exa search)
  * research.pipeline.research_company (evidence-first crawl + Haiku extraction)
  * agents.qualification.qualify      (deterministic fit/intent scoring — free)
  * agents.writer.write_email         (Sonnet; only for the ONE top match)

Cost is bounded structurally: exactly ``DEMO_CANDIDATES`` researches, one draft,
and a wall-clock deadline after which no new research starts. It never raises for
normal failures — a candidate that fails research is still shown, honestly, with
whatever score the (thin) evidence earns.
"""

import concurrent.futures
import logging
import re
import time

from agents.qualification import CONTINUE, HIGH_PRIORITY, qualify
from agents.writer import write_email
from config import settings
from discovery.engine import discover
from discovery.models import DiscoveryQuery, registrable_domain
from research.pipeline import research_company

log = logging.getLogger("saqua.demo")

# Shown verbatim to the visitor at the end of every successful run. This is the
# whole reason the demo stops here: Gmail sending is still in Google's review.
GMAIL_PENDING_MESSAGE = (
    "Connecting Gmail and sending is in final review with Google. Join the "
    "waitlist and you'll be first in the moment it opens."
)

_STOPWORDS = frozenset("""
a an the and or of for to in on with without that this those these you your our we
they them their who whom which what when where why how is are be being been at by
from into over under about as it its our sell selling sells company companies
business businesses startup startups team teams people who's whos i im i'm my me
""".split())


def _keywords(text: str, cap: int = 6) -> list:
    """A few salient lowercase keywords from free text — feeds both the discovery
    search and the qualification ICP so fit scores actually reflect the ask."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+\-]{2,}", (text or "").lower())
    out = []
    for w in words:
        if w in _STOPWORDS or w in out:
            continue
        out.append(w)
        if len(out) >= cap:
            break
    return out


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return raw


def _icp_from_text(text: str):
    """(icp_dict, discovery_query, human_label) from a plain ICP description."""
    kw = _keywords(text)
    icp = {"keywords": kw, "industries": [], "roles": [], "exclude": []}
    query = DiscoveryQuery(raw=text, keywords=kw, limit=settings.DEMO_CANDIDATES)
    return icp, query, (text or "").strip()


def _icp_from_profile(profile: dict):
    """(icp_dict, discovery_query, human_label) inferred from the visitor's OWN
    site research — who THEY appear to sell to becomes the ICP we go find."""
    data = (profile or {}).get("data") or {}
    target = (data.get("target_customer") or "").strip()
    industries = data.get("industries_served") or []
    what = (data.get("what_they_do") or "").strip()
    seed = target or (", ".join(industries) if industries else "") or what
    kw = _keywords(" ".join([target, " ".join(industries), what]))
    icp = {"keywords": kw, "industries": [str(i).lower() for i in industries],
           "roles": [], "exclude": []}
    label = target or (", ".join(industries) if industries else what) or "your customers"
    query = DiscoveryQuery(raw=seed or label, keywords=kw,
                           industry=(industries[0] if industries else ""),
                           limit=settings.DEMO_CANDIDATES)
    return icp, query, label


# The discovered "company name" is a best-effort fragment of a search-result title,
# so it is sometimes a category heading ("Monitoring & Observability") rather than a
# brand. For the demo we prefer the research extraction's brand, then a clean
# discovered name, then the domain core — so a card never shows a section heading.
_GENERIC_NAME_BITS = ("monitoring", "observability", "platform", "analytics",
                      "dashboard", "solutions", "&", " and ", "home", "welcome",
                      "overview", "the ")


def _clean_name(name: str) -> str:
    n = (name or "").strip()
    if not n or len(n) > 34 or any(b in n.lower() for b in _GENERIC_NAME_BITS):
        return ""
    return n


def _domain_name(prospect) -> str:
    core = registrable_domain(prospect.website or prospect.domain or "").split(".")[0]
    return core.replace("-", " ").title()


def _display_name(prospect, research=None) -> str:
    """A brand-like name for the UI: research's extracted name, else a clean
    discovered name, else the domain core — never a scraped section heading."""
    data = (research or {}).get("data") or {}
    return (_clean_name(data.get("company_name")) or _clean_name(prospect.company_name)
            or _domain_name(prospect) or (prospect.company_name or "").strip())


def _signal_from_research(research: dict) -> dict:
    """The single strongest, source-anchored signal to show for a candidate."""
    data = (research or {}).get("data") or {}
    hooks = (research or {}).get("hooks") or []
    text = (data.get("unique_hook") or data.get("recent_focus")
            or data.get("metrics_or_traction") or "")
    quote = source = None
    if hooks and isinstance(hooks[0], dict):
        text = text or hooks[0].get("text") or ""
        quote = hooks[0].get("quote") or hooks[0].get("evidence")
        source = hooks[0].get("source_url")
    if not source:
        pages = (research or {}).get("pages_crawled") or []
        source = pages[0] if pages else None
    return {"text": text or None, "quote": quote, "source_url": source}


def _why_fits(q) -> str:
    if q.strongest_signals:
        return q.strongest_signals[0]
    return {HIGH_PRIORITY: "Strong fit with active buying signals",
            CONTINUE: "A reasonable fit worth pursuing"}.get(
        q.recommendation, "Scored on the evidence found")


# Extraction sometimes fills founder_name with a DESCRIPTION instead of a name
# ("Unnamed founder (stated as first-person narrator)" — seen live). Only a
# plausible human name is worth showing; a described non-name is worse than none.
_NON_NAME_BITS = ("unnamed", "unknown", "not named", "narrator", "first-person",
                  "founder", "owner", "team", "n/a", "(")


def _person_from_research(research: dict) -> dict:
    """The named decision-maker research found (evidence-gated upstream), if any.
    Shown on the card because a real name is the difference between "a list" and
    "someone you could actually email"."""
    data = (research or {}).get("data") or {}
    name = (data.get("founder_name") or "").strip()
    role = (data.get("founder_role") or "").strip()
    low = name.lower()
    plausible = (name and " " in name and len(name) <= 40
                 and not any(b in low for b in _NON_NAME_BITS))
    if not plausible:
        return {"name": None, "role": None}
    return {"name": name, "role": role or None}


def _card(index: int, prospect, research, q) -> dict:
    """The per-candidate payload the UI renders as a scored card."""
    return {
        "index": index,
        "company": _display_name(prospect, research),
        "domain": prospect.domain,
        "website": prospect.website,
        "fit_score": q.qualification_score,
        "fit_level": q.fit_level,
        "recommendation": q.recommendation,
        "priority": q.priority,
        "why": _why_fits(q),
        "signal": _signal_from_research(research),
        "person": _person_from_research(research),
        "researched": (research or {}).get("status") == "ok",
    }


def _rank_key(item):
    """Highest score first; a 'pursue' recommendation outranks the same score."""
    prospect, research, q = item
    rec_rank = {HIGH_PRIORITY: 2, CONTINUE: 1}.get(q.recommendation, 0)
    return (q.qualification_score, rec_rank)


def run_demo(*, icp_text: str = "", website: str = "",
             candidates: int = None, deadline: float = None):
    """Yield ``(event, payload)`` tuples for one demo run. Never raises."""
    candidates = candidates or settings.DEMO_CANDIDATES
    deadline = deadline or (time.time() + settings.DEMO_RUN_TIMEOUT_SECONDS)

    # ── 1. Establish the ICP ──────────────────────────────────────────────
    try:
        if website:
            yield "stage", {"step": "profile",
                            "label": "Reading your site to learn who you sell to"}
            profile = research_company(_normalize_url(website))
            icp, query, label = _icp_from_profile(profile)
        else:
            icp, query, label = _icp_from_text(icp_text)
    except Exception:  # noqa: BLE001 - never let ICP derivation crash the run
        log.info("demo: ICP derivation failed", exc_info=True)
        icp, query, label = _icp_from_text(icp_text or website)

    if query.is_empty():
        yield "empty", {"reason": "Tell me who you sell to (an industry, a kind of "
                        "company, or your website) and I'll go find matches."}
        return
    yield "icp", {"label": label, "keywords": icp.get("keywords", [])}

    # ── 2. Discover candidates (deterministic; no LLM) ────────────────────
    yield "stage", {"step": "discovery", "label": "Finding companies that match"}
    try:
        result = discover("__demo__", query, store=None, skip_seen=False)
    except Exception:  # noqa: BLE001
        log.info("demo: discovery failed", exc_info=True)
        yield "error", {"reason": "Couldn't reach the discovery sources just now. "
                        "Please try again in a moment."}
        return
    if result.status != "ok" or not result.prospects:
        yield "empty", {"reason": getattr(result, "reason", "")
                        or "No matching companies found — try a broader description."}
        return
    prospects = result.prospects[:candidates]
    yield "candidates", {"count": len(prospects), "prospects": [
        {"index": i, "company": _display_name(p), "domain": p.domain,
         "website": p.website, "why_discovered": p.why_it_matches}
        for i, p in enumerate(prospects)]}

    # ── 3. Research + qualify each (bounded concurrency; stream as they land) ─
    scored = []
    workers = max(1, min(settings.DEMO_RESEARCH_CONCURRENCY, len(prospects)))

    def _one(prospect):
        research = research_company(prospect.website)
        q = qualify(research=research, icp=icp)
        return prospect, research, q

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_one, p): i for i, p in enumerate(prospects)}
        pending = set(futures)
        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                for fut in pending:
                    idx = futures[fut]
                    fut.cancel()
                    yield "scored", {"index": idx, "company": _display_name(prospects[idx]),
                                     "domain": prospects[idx].domain,
                                     "website": prospects[idx].website,
                                     "fit_score": None, "timed_out": True,
                                     "why": "Ran out of time this run"}
                break
            done, pending = concurrent.futures.wait(
                pending, timeout=remaining,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                idx = futures[fut]
                try:
                    prospect, research, q = fut.result()
                except Exception:  # noqa: BLE001 - a single failure never aborts
                    log.info("demo: research/qualify failed for %s",
                             prospects[idx].domain, exc_info=True)
                    continue
                card = _card(idx, prospect, research, q)
                scored.append((prospect, research, q))
                yield "scored", card
    finally:
        # NEVER wait for threads whose results we've already written off. The
        # context-manager form blocks here until still-running research calls
        # finish — measured at 30–68s of dead air on the live stream after the
        # deadline. Abandon them instead; they finish quietly in the background
        # and their results are discarded.
        pool.shutdown(wait=False, cancel_futures=True)

    if not scored:
        yield "empty", {"reason": "Couldn't research those candidates deeply enough "
                        "this run. Please try again."}
        return

    # ── 4. Best fit + its real signal ─────────────────────────────────────
    scored.sort(key=_rank_key, reverse=True)
    top_prospect, top_research, top_q = scored[0]

    # HONESTY GATE: if even the best candidate scored below the pursue bar, say so
    # and stop — no "Best fit" crown, no draft. Saqua's whole pitch is that it
    # refuses to write generic email to weak fits; the demo must behave the same
    # way, not contradict its own scorer in front of a visitor. (Also skips the
    # writer call, so an all-reject run costs less.)
    if top_q.recommendation not in (CONTINUE, HIGH_PRIORITY):
        yield "no_pursue", {
            "best_company": _display_name(top_prospect, top_research),
            "best_score": top_q.qualification_score,
            "reason": (
                "None of these cleared Saqua's pursue bar — the closest was "
                f"{_display_name(top_prospect, top_research)} at "
                f"{top_q.qualification_score}/100. Saqua only writes when the "
                "evidence gives it a real reason to reach out; sending generic "
                "email to weak fits is exactly what it exists to avoid. Try "
                "describing who you sell to more concretely — a specific kind "
                "of company tends to match far better than a broad category."),
        }
        yield "done", {}
        return

    yield "top", {"company": _display_name(top_prospect, top_research),
                  "domain": top_prospect.domain,
                  "fit_score": top_q.qualification_score,
                  "fit_level": top_q.fit_level,
                  "recommendation": top_q.recommendation,
                  "why": _why_fits(top_q),
                  "signal": _signal_from_research(top_research),
                  "person": _person_from_research(top_research)}

    # ── 5. Real drafted opener for the top match ──────────────────────────
    # The draft is the payoff of the whole run, so a transient model failure gets
    # ONE retry (it only costs anything when the first attempt failed). And the
    # skip reason shown to a visitor is always our own copy — writer-internal
    # errors ("The model returned an empty response.", observed live) go to the
    # log, not the page.
    yield "stage", {"step": "writing", "label": "Writing the opener around the evidence"}
    draft = {"status": "error"}
    for attempt in (1, 2):
        try:
            draft = write_email(top_research, allow_thin=True)
        except Exception:  # noqa: BLE001
            log.info("demo: writer raised (attempt %d)", attempt, exc_info=True)
            draft = {"status": "error"}
        if draft.get("status") == "ok":
            break
        log.info("demo: writer attempt %d not ok: %s", attempt, draft.get("reason"))
    if draft.get("status") == "ok":
        yield "draft", {"company": _display_name(top_prospect, top_research),
                        "subject": draft.get("subject"),
                        "body": draft.get("body"),
                        "to": draft.get("to")}
    else:
        yield "draft_skip", {"reason":
                             "Couldn't finish a grounded opener for this one just now — "
                             "Saqua drafts around verified evidence or not at all. "
                             "Run it again, or try another description."}

    # ── 6. Gmail is out of scope for the demo ─────────────────────────────
    yield "gmail_pending", {"message": GMAIL_PENDING_MESSAGE}
    yield "done", {}
