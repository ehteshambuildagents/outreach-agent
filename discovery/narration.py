"""Reasoning narration for a streaming discovery run.

The progress stream used to report only WHAT the engine was doing ("Searching
Apollo", "Ranking"). That reads like a pipeline executing. This module turns the
run's REAL observed state into the short "why" lines a senior SDR would say while
working, so the user watches decisions being made rather than stages ticking by.

    Found 5,532 companies with a live posting for that role.
    Most of these are staffing firms, they post other people's jobs. Dropping 9.
    Only 47 look like genuine companies.
    Two are hiring the exact role. The rest just have a careers page.

THE ONE RULE HERE IS GROUNDING. Every function takes the counts and objects the
engine actually produced, and returns NOTHING when the state does not support a
claim: no staffing firms dropped means no sentence about recruiters, zero verified
postings means no claim that anything was verified. The phrasing also adapts to
the magnitude of what happened ("most of these" vs "a couple"), because a fixed
script that ignores the data is exactly the canned narration this replaces.

Pure: no network, no model, no I/O. Every line is a unit test away from being
checked against the state that produced it (see tests/test_discovery_narration.py).
"""

from discovery import aggregators

# How lopsided a drop has to be before it is worth calling "most of them".
_MOST = 0.5
_MANY = 0.25

# Plain-language names for the intermediary kinds the classifier emits.
_KIND_WORDS = {
    aggregators.JOB_BOARD: "job boards",
    aggregators.ATS: "applicant tracking pages",
    aggregators.MARKETPLACE: "freelancer marketplaces",
    aggregators.DIRECTORY: "company directories",
    aggregators.LIST_SITE: "listicles",
    aggregators.MEDIA: "news and media pages",
}


def _plural(n: int, one: str, many: str = None) -> str:
    return one if n == 1 else (many or one + "s")


# Job-title acronyms that must not be printed lowercase. "10,241 companies hiring
# sdr" reads like a bug; "hiring an SDR" reads like a person wrote it.
_ACRONYMS = {"sdr", "bdr", "ae", "csm", "cs", "pmm", "ai", "vp", "hr", "it", "qa",
             "seo", "ux", "ui", "cto", "ceo", "cmo", "cro", "coo"}


def role_phrase(role_label: str) -> str:
    """A role as it should READ mid-sentence: acronyms upper-cased and an article
    in front, so the narration says "an SDR" rather than "sdr"."""
    label = " ".join(str(role_label or "").split())
    if not label:
        return ""
    words = [w.upper() if w.lower() in _ACRONYMS else w for w in label.split()]
    phrase = " ".join(words)
    article = "an" if phrase[0].lower() in "aeiou" or words[0].lower() in (
        "sdr", "ae", "hr", "it", "ux", "ui", "seo") else "a"
    return f"{article} {phrase}"


# ── 1. After the Apollo company search ─────────────────────────────────────
def after_apollo(*, total=None, kept=0, staffing_dropped=0, recruiter_names=(),
                 agency_dropped=0, role_label="") -> list:
    """What the company-database search actually returned, and what was thrown
    away. ``total`` is Apollo's own match count, ``kept`` what survived filtering.

    Recruiters and agencies are reported SEPARATELY because they are excluded for
    different reasons, and only a firm classified as true staffing is ever NAMED:
    the agency/consulting codes are coarse enough to catch a Google or a Deloitte,
    and "ignoring Google, it's a recruiter" is a false claim.
    """
    out = []
    if isinstance(total, int) and total > 0:
        role = (f" hiring {role_phrase(role_label)}" if role_label
                else " with a matching open role")
        out.append(f"Apollo has {total:,} companies{role}.")

    if staffing_dropped > 0:
        seen = staffing_dropped + max(kept, 0)
        share = staffing_dropped / seen if seen else 0
        scale = ("Most of the top matches" if share >= _MOST
                 else "Several" if share >= _MANY else "A few")
        recruiters = staffing_dropped - max(agency_dropped, 0)
        if recruiters > 0:
            named = ", ".join(list(recruiter_names)[:2])
            out.append(f"{scale} are recruiters, they post other companies' roles "
                       "rather than hiring."
                       + (f" Ignoring {named}." if named else f" Dropping {recruiters}."))
        if agency_dropped > 0:
            if recruiters <= 0:
                lead, verb = f"{scale} are agencies or consultancies", "hire"
            elif agency_dropped == 1:
                lead, verb = "One more is an agency or consultancy", "hires"
            else:
                lead, verb = (f"Another {agency_dropped} are agencies or "
                              "consultancies"), "hire"
            out.append(f"{lead}, which {verb} on a client's behalf. "
                       "Leaving those out.")
    return out


# ── 2. After the web corroboration pass ────────────────────────────────────
def after_web(*, demoted=None, rejected=None, kept=0) -> list:
    """What the web pass contributed. ``demoted``/``rejected`` are the engine's
    real Counters, keyed by aggregator kind and by drop reason."""
    demoted = dict(demoted or {})
    rejected = dict(rejected or {})
    out = []

    if demoted:
        kind, n = max(demoted.items(), key=lambda kv: kv[1])
        words = _KIND_WORDS.get(kind, kind.replace("_", " ") + "s")
        total_demoted = sum(demoted.values())
        if total_demoted > kept and kept >= 0:
            out.append(f"The web results are mostly {words}, which is normal for a "
                       "hiring search. Pushing those below the real companies.")
        else:
            out.append(f"Set aside {total_demoted} {words}.")

    third_party = rejected.get("advertises another company's job posting", 0)
    if third_party:
        out.append(f"{third_party} {_plural(third_party, 'page')} advertised someone "
                   "else's job, so the site is the middleman, not the employer.")
    return out


# ── 3. After merging + classifying the candidate pool ──────────────────────
def after_merge(*, companies=0, fallback=0, corroborated=0) -> list:
    """The shape of the surviving pool."""
    out = []
    if companies:
        line = f"{companies} genuine {_plural(companies, 'company', 'companies')} left"
        if fallback:
            line += f" after setting aside {fallback} " + \
                    _plural(fallback, "intermediary", "intermediaries")
        out.append(line + ".")
    if corroborated >= 2:
        out.append(f"{corroborated} of them turned up in more than one source, "
                   "which is usually a good sign.")
    return out


# ── 4. After verifying live job postings ───────────────────────────────────
def after_verification(*, verified=0, checked=0, hiring_any=0, role_label="") -> list:
    """What the paid per-company posting check established. Never claims a
    verification that did not happen."""
    role = role_phrase(role_label) if role_label else "that role"
    out = []
    if not checked:
        return out
    if verified:
        out.append(f"{verified} of them {_plural(verified, 'has', 'have')} a live "
                   f"posting for {role}. That is the signal worth acting on.")
        if hiring_any > verified:
            out.append(f"The others are hiring, just not for {role}. "
                       "Weaker reason to reach out.")
    elif hiring_any:
        out.append(f"None has a posting for {role} specifically. They are hiring, "
                   "so the money is moving, but the match is looser.")
    else:
        out.append(f"No live postings for {role} on any of them.")
    return out


# ── 5. Why the top company wins ────────────────────────────────────────────
def top_pick(prospect) -> list:
    """The case for ranking this company first, read off ITS OWN score and
    evidence. Returns [] when the object carries nothing to justify."""
    if prospect is None:
        return []
    name = getattr(prospect, "company_name", "") or "The top match"
    score = getattr(prospect, "score", None) or {}
    hiring = getattr(prospect, "hiring", None) or {}
    growth = ((getattr(prospect, "growth", None) or {}).get("headcount_6mo"))

    facts = []
    if hiring.get("match") == "role":
        facts.append("hiring the exact role")
    if isinstance(growth, (int, float)) and growth >= 0.05:
        facts.append(f"headcount up {round(growth * 100)}%")
    if (score.get("icp_match") or 0) >= 0.6:
        facts.append("the kind of company that buys this")
    if not getattr(prospect, "is_public", False) and (score.get("founder_access") or 0) >= 0.55:
        facts.append("small enough that a founder still answers")
    if not facts:
        return []
    if len(facts) == 1:
        return [f"{name} stands out: {facts[0]}."]
    # Two reasons carry the point; a four-clause sentence stops being skimmable
    # while it streams past. Keep the strongest, in the order they were gathered.
    facts = facts[:3]
    return [f"{name} stands out. It is {', '.join(facts[:-1])} and {facts[-1]}. "
            "That is why it goes first."]


# ── 6. Honest confidence about the page as a whole ─────────────────────────
def confidence(*, strong=0, returned=0) -> list:
    """Say plainly how much of the page is actually worth acting on. Quality over
    quantity is the product promise, so a thin result gets said out loud."""
    if not returned:
        return []
    if strong == 0:
        return ["None of these clears the bar I would want before reaching out. "
                "Treat them as leads to check, not as ready prospects."]
    if strong == 1:
        return ["Only one of these I would confidently recommend. "
                "The rest have weaker evidence behind them."]
    if strong < returned:
        return [f"I am confident about the top {strong}. "
                "The evidence thins out after that."]
    return []


# ── 7. Nothing survived ────────────────────────────────────────────────────
def empty(*, considered=0, staffing_dropped=0, demoted=0) -> list:
    """Explain an empty page from what was actually seen, rather than shrugging."""
    if not considered:
        return ["Nothing came back for that. The filters may be too narrow."]
    parts = [f"Looked at {considered} "
             + _plural(considered, "candidate") + ", none of them held up."]
    if staffing_dropped or demoted:
        dropped = staffing_dropped + demoted
        parts.append(f"{dropped} were recruiters, job boards or directories "
                     "rather than companies you could sell to.")
    return parts
