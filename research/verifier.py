"""Verifier: the deterministic anti-hallucination gate.

Takes the model's raw evidence + the cleaned page texts and produces a verified
ResearchGraph:
  1. GROUNDING: an evidence item is kept only if its source_url is a page we
     actually crawled AND its quote really appears in that page's text. Anything
     ungrounded is DROPPED — this is what enforces "every fact is real".
  2. CORROBORATION: the same value grounded on multiple pages raises confidence.
  3. CONFLICT: differing grounded values for a singular field lower confidence
     and are flagged for explainability.
  4. Team is filtered to the company's own people; a stated founder/CEO is
     promoted to founder_name only when grounded.
"""

from config.settings import QUOTE_MAX_CHARS
from research.classifier import is_customer_story
from research.cleaner import contains_phrase, normalize_for_match, strip_emails
from research.crawler import normalize_url
from research.evidence import Evidence, ResearchGraph, TeamMember
from research.extractor import _EVIDENCE_FIELDS
from research.evidence import SINGULAR_FIELDS
from services import claude_client

# Only promote to founder on an explicit founding role. "CEO" alone is NOT
# enough — a testimonial's "CEO, SomeCustomer" must never become the founder.
_FOUNDER_HINTS = ("founder", "co-founder", "cofounder")


# Person fields are grounded against email-stripped text so a name that appears
# ONLY inside an email address (hakan@company.com) is NOT accepted as real.
_PERSON_FIELDS = ("founder_name",)

# A customer story is mostly ABOUT THE CUSTOMER, so almost nothing on it
# describes the company we are researching. These are the exceptions — the facts
# such a page carries about the VENDOR: who they landed, which of their products
# that customer runs, and the problem it solved. Everything else (identity,
# mission, market, people, funding, recency) belongs to the customer and is
# dropped. An allowlist rather than a blocklist, so a field added later is
# quarantined by default instead of silently inheriting the bug.
_CUSTOMER_STORY_SAFE_FIELDS = frozenset({
    "notable_customers", "tech_stack", "integrations",
    "product_differentiators", "pain_points",
})


def verify(raw: dict, pages: dict) -> tuple:
    """Return (ResearchGraph, raw_hooks). `pages` is {url: cleaned_text}."""
    norm_pages = {normalize_url(u): normalize_for_match(t) for u, t in pages.items()}
    # Email-stripped variant for grounding person NAMES (not other facts).
    norm_pages_noemail = {
        normalize_url(u): normalize_for_match(strip_emails(t)) for u, t in pages.items()
    }
    url_lookup = {normalize_url(u): u for u in pages}
    # Pages that profile somebody else. Grounding proves a fact was ON the page;
    # it cannot tell whose fact it is, and on these pages it usually isn't ours.
    story_pages = {normalize_url(u) for u in pages if is_customer_story(u)}

    graph = ResearchGraph()
    for field in _EVIDENCE_FIELDS:
        pages_for_field = norm_pages_noemail if field in _PERSON_FIELDS else norm_pages
        quarantined = story_pages if field not in _CUSTOMER_STORY_SAFE_FIELDS else set()
        grounded = []
        for item in raw.get(field) or []:
            ev = _ground(item, pages_for_field, url_lookup, exclude=quarantined)
            if ev is not None:
                grounded.append(ev)
        merged = _corroborate(grounded)
        if field in SINGULAR_FIELDS:
            _flag_conflicts(merged)
        graph.nodes[field] = merged

    raw_team = raw.get("team_members") or []
    # People named in a customer story work for the CUSTOMER. Their titles rarely
    # say so ("COO", "Head of Support Ops"), which is why the role-text backstop
    # below misses them and the page they came from has to be the signal.
    graph.team = _verify_team(raw_team, norm_pages_noemail, url_lookup,
                              exclude=story_pages)
    # Names the model proposed but that are NOT this company's staff.
    excluded = {
        str(m.get("name") or "").strip().lower()
        for m in raw_team
        if isinstance(m, dict) and not claude_client.is_company_member(m)
    }
    excluded |= _drop_customer_affiliated(graph)
    _reconcile_founder(graph, excluded)
    _ensure_founder_from_team(graph)
    return graph, (raw.get("hooks") or [])


def _drop_customer_affiliated(graph) -> set:
    """Backstop: a person whose role names a known customer is not our staff.

    Returns the set of dropped names so the founder field can be reconciled too.
    Uses whole-word matching so e.g. a customer "Box" doesn't drop a "Sandbox"
    role.
    """
    customers = [normalize_for_match(c) for c in graph.values("notable_customers")]
    customers = [c for c in customers if len(c) >= 3]
    if not customers:
        return set()
    dropped, kept = set(), []
    for member in graph.team:
        role_norm = normalize_for_match(member.role or "")
        if any(contains_phrase(role_norm, c) for c in customers):
            dropped.add(member.name.strip().lower())
        else:
            kept.append(member)
    graph.team = kept
    return dropped


def _reconcile_founder(graph, excluded_names: set):
    """Clear founder_name if it refers to a person we excluded as non-staff.

    Prevents a testimonial/customer CEO the model listed as 'founder' from
    surviving in the founder field after we dropped them from the team.
    """
    founder = graph.value("founder_name")
    if founder and founder.strip().lower() in excluded_names:
        graph.nodes["founder_name"] = []
        graph.nodes["founder_role"] = []


# ──────────────────────────────────────────────────────────────────────
def _ground(item, norm_pages, url_lookup, exclude=()):
    """Return a grounded Evidence, or None if it can't be verified.

    Grounding = the cited page is one we crawled AND the fact is really on it,
    proven by EITHER the supporting quote OR the value itself appearing in the
    page text. Value-grounding recovers real facts (especially names) when the
    model paraphrases its quote, while still blocking fabrications — an invented
    name/number appears in neither the quote nor the page. If only the value
    grounded (quote not verbatim), confidence is reduced to reflect that.

    ``exclude`` names pages this particular fact may not be sourced from, which
    is how a customer story is stopped from describing the company we are
    researching. Grounded-but-not-ours is still a drop.
    """
    if not isinstance(item, dict):
        return None
    value = str(item.get("value") or "").strip()
    if not value:
        return None
    # source must be a page we crawled
    src_key = normalize_url(str(item.get("source_url") or ""))
    if src_key in exclude:
        return None
    page_norm = norm_pages.get(src_key)
    if page_norm is None:
        return None

    quote = str(item.get("quote") or "").strip()[:QUOTE_MAX_CHARS]
    nquote = normalize_for_match(quote)
    nvalue = normalize_for_match(value)
    # Whole-word/phrase matching: a short value like "ben" must NOT ground
    # against "benefits", nor "ana" against "analytics".
    quote_ok = contains_phrase(page_norm, nquote)
    value_ok = len(nvalue) >= 3 and contains_phrase(page_norm, nvalue)
    if not (quote_ok or value_ok):
        return None

    confidence = _clamp(item.get("confidence"))
    if not quote_ok:                      # value present but quote not verbatim
        confidence *= 0.85
    return Evidence(
        value=value,
        source_url=url_lookup[src_key],
        quote=quote,
        confidence=confidence,
    )


def _clamp(conf) -> float:
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, c))


def _corroborate(evidences):
    """Merge same-value evidence; more grounded sources -> higher confidence."""
    groups = {}
    for ev in evidences:
        key = ev.value.strip().lower()
        groups.setdefault(key, []).append(ev)
    merged = []
    for items in groups.values():
        best = max(items, key=lambda e: e.confidence)
        sources = {e.source_url for e in items}
        count = len(sources)
        best.corroborations = count
        best.confidence = min(1.0, best.confidence + 0.08 * (count - 1))
        merged.append(best)
    merged.sort(key=lambda e: -e.confidence)
    return merged


def _flag_conflicts(merged):
    """If a singular field has multiple distinct grounded values, lower + flag."""
    if len(merged) > 1:
        for ev in merged:
            ev.conflict = True
            ev.confidence = max(0.05, ev.confidence - 0.15)
        merged.sort(key=lambda e: -e.confidence)


def _verify_team(members, norm_pages, url_lookup, exclude=()):
    cleaned, seen = [], set()
    for member in members:
        if not claude_client.is_company_member(member):
            continue
        name = str(member.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        ev = _ground(
            {"value": name, "source_url": member.get("source_url"),
             "quote": member.get("quote"), "confidence": member.get("confidence")},
            norm_pages, url_lookup, exclude=exclude,
        )
        if ev is None:
            continue  # unverifiable person -> drop (never assert an ungrounded name)
        seen.add(key)
        role = member.get("role")
        role = role.strip() if isinstance(role, str) and role.strip() else None
        cleaned.append(TeamMember(name=name, role=role, source_url=ev.source_url,
                                  quote=ev.quote, confidence=ev.confidence))
    return cleaned[:25]


def _ensure_founder_from_team(graph):
    """If no grounded founder yet, promote a grounded founder/co-founder team
    member. A bare CEO/President is NOT a founder (that stays in founder_name as
    null); the outreach decision-maker is surfaced separately via
    select_primary_contact()."""
    if graph.value("founder_name"):
        return
    for member in graph.team:
        role = (member.role or "").lower()
        if any(hint in role for hint in _FOUNDER_HINTS):
            graph.add("founder_name", Evidence(member.name, member.source_url,
                                               member.quote, member.confidence))
            if member.role:
                graph.add("founder_role", Evidence(member.role, member.source_url,
                                                   member.quote, member.confidence))
            return


# Best-contact priority for cold outreach: the primary DECISION-MAKER, which is
# not always a founder. Matched as whole words against an already-verified team
# member's role, so a CEO/President we already trust becomes the contact even
# when the page names no "founder". We never guess: only a grounded founder or a
# verified team member is eligible.
_CONTACT_ROLE_TIERS = (
    ("founder", "co founder", "cofounder"),            # 0
    ("chief executive", "ceo"),                        # 1
    ("president",),                                    # 2
    ("managing director",),                            # 3
    ("executive chairman", "chairman", "chairwoman"),  # 4
    ("owner",),                                        # 5
    # NOT bare "partner": "Business/Operations/People Partner" are job titles,
    # not equity partners. Only genuine senior-partner roles count.
    ("managing partner", "founding partner", "senior partner", "equity partner"),
)
# Sub-leader or job-title variants that must NOT count as a top decision-maker:
# "Vice President" != President, "Product Owner" != Owner, etc.
_NOT_TOP_ROLE = (
    " vice ", " deputy ", " assistant ", " associate ", " interim ", " former ",
    " ex ", " product ", " process ", " program ", " project ", " account ",
    " risk ", " data ", " scrum ", " feature ", " service ", " delivery ",
)


def _contact_rank(role):
    """Priority tier of a role (lower = more senior), or None if it is not a
    clear top decision-maker role."""
    role_norm = normalize_for_match(role or "")
    if not role_norm:
        return None
    padded = f" {role_norm} "
    if any(marker in padded for marker in _NOT_TOP_ROLE):
        return None  # e.g. "Vice President" must not rank as "President"
    for tier, keywords in enumerate(_CONTACT_ROLE_TIERS):
        if any(contains_phrase(role_norm, normalize_for_match(k)) for k in keywords):
            return tier
    return None


def select_primary_contact(graph):
    """Best leadership contact for outreach as (name, role), or (None, None).

    A grounded founder is always the contact; otherwise the highest-ranking
    grounded EXECUTIVE (CEO > President > Managing Director > Executive Chairman
    > Owner > Partner), ties broken by confidence. Only verified people are
    eligible, so this neither invents a contact nor weakens grounding, and it
    returns (None, None) rather than guessing when no clear decision-maker exists.
    """
    founder = graph.value("founder_name")
    if founder:
        return founder, graph.value("founder_role") or "Founder"
    best, best_rank = None, None
    for member in graph.team:
        rank = _contact_rank(member.role)
        if rank is None:
            continue
        if (best_rank is None or rank < best_rank
                or (rank == best_rank and member.confidence > best.confidence)):
            best, best_rank = member, rank
    return (best.name, best.role) if best else (None, None)
