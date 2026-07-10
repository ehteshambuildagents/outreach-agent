"""Hooks + research score (deterministic, explainable).

- rank_hooks: turn grounded evidence + the model's hook suggestions into a
  ranked list of personalization hooks, each scored and carrying its source.
- research_score: a 0-100 graded score from per-category confidence, used to
  SKIP honestly when a site genuinely lacks enough to personalize.
No extra LLM call — scoring is code, so it is cheap and auditable.
"""

from config.settings import QUOTE_MAX_CHARS
from research.cleaner import contains_phrase, normalize_for_match
from research.crawler import normalize_url
from research.evidence import Evidence, Hook

# Higher = better outreach angle.
_CATEGORY_WEIGHTS = {
    "founder": 1.0, "launch": 0.95, "recent": 0.95, "customers": 0.9,
    "metrics": 0.9, "mission": 0.8, "hiring": 0.8, "technology": 0.7,
    "product": 0.6, "pricing": 0.6,
}

# Research-score categories: graph node -> weight (weights sum to 1.0).
# Positioning context (product_category/business_model/industries) contributes
# too, so a site rich in useful context isn't wrongly scored as "thin".
_SCORE_WEIGHTS = {
    "what_they_do": 0.18,
    "founder_name": 0.15,
    "product_category": 0.08,
    "business_model": 0.06,
    "recent_focus": 0.10,
    "notable_customers": 0.10,
    "metrics_or_traction": 0.09,
    "their_mission_or_why": 0.08,
    "industries_served": 0.04,
    "pricing_model": 0.06,
    "tech_stack": 0.06,
}


def rank_hooks(graph, raw_hooks, pages: dict) -> list:
    """Return ranked, grounded Hook objects (highest score first)."""
    norm_pages = {normalize_url(u): normalize_for_match(t) for u, t in pages.items()}
    url_lookup = {normalize_url(u): u for u in pages}

    hooks, seen = [], set()
    for item in raw_hooks or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text or text.lower() in seen:
            continue
        real_url = _grounded(item.get("quote"), item.get("source_url"),
                             norm_pages, url_lookup)
        if real_url is None:
            continue  # ungrounded hook -> drop
        category = str(item.get("category") or "other").strip().lower()
        confidence = _clamp(item.get("confidence"))
        score = confidence * _CATEGORY_WEIGHTS.get(category, 0.5) * _specificity(text)
        seen.add(text.lower())
        hooks.append(Hook(category=category, text=text, score=score,
                          confidence=confidence, source_url=real_url,
                          quote=str(item.get("quote") or "").strip()[:QUOTE_MAX_CHARS]))

    hooks.sort(key=lambda h: -h.score)
    return hooks


def research_score(graph) -> tuple:
    """Return (score 0-100, breakdown dict of per-category 0-1 signals)."""
    breakdown, total = {}, 0.0
    for node, weight in _SCORE_WEIGHTS.items():
        best = graph.best(node)
        signal = best.confidence if best else 0.0
        if node == "founder_name" and not best and graph.team:
            signal = max(m.confidence for m in graph.team)
        breakdown[node] = round(signal, 2)
        total += weight * signal
    return round(100 * total), breakdown


# ── Deterministic fact recovery (grounded-only) ───────────────────────
# The model sometimes grounds a fact but emits it ONLY as a hook — e.g. it puts
# the price in a "pricing" hook yet leaves pricing_model null (observed on
# oculta.app). This copies such a fact back into its field using the hook's
# already-grounded VERBATIM quote, so it can never assert anything that isn't
# literally on a crawled page. Run AFTER research_score so recovering a fact
# never shifts the skip/ok decision.
_BACKFILL_FROM_HOOK = (
    # (target field, hook category, quote-must-look-like guard)
    ("pricing_model", "pricing", lambda q: _has_price_signal(q)),
    ("metrics_or_traction", "metrics", lambda q: _has_digit(q)),
)

_PRICE_WORDS = (
    "free", "month", "monthly", "year", "yearly", "annual", "annually",
    "lifetime", "/mo", "per seat", "per user", "per license", "plan", "pricing",
    "tier", "trial", "subscription",
)


def backfill_facts_from_hooks(graph, hooks) -> None:
    """Populate an EMPTY fact field from a matching grounded hook's quote.

    Never overwrites an existing grounded value; only ever uses a hook's verbatim
    grounded quote, so it cannot introduce anything not already on a crawled page
    (no hallucination, no guessing — null is still preferred over invention).
    """
    for field, category, guard in _BACKFILL_FROM_HOOK:
        if graph.value(field):                  # already have a grounded value
            continue
        for hook in hooks:                       # highest-score first
            quote = (hook.quote or "").strip()
            if hook.category == category and quote and guard(quote):
                graph.add(field, Evidence(value=quote, source_url=hook.source_url,
                                          quote=quote, confidence=hook.confidence))
                break


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _has_price_signal(text: str) -> bool:
    if any(sym in text for sym in "$€£¥") or _has_digit(text):
        return True
    low = text.lower()
    return any(word in low for word in _PRICE_WORDS)


# ──────────────────────────────────────────────────────────────────────
def _grounded(quote, source_url, norm_pages, url_lookup):
    nquote = normalize_for_match(str(quote or ""))
    if not nquote:
        return None
    key = normalize_url(str(source_url or ""))
    if key in norm_pages and contains_phrase(norm_pages[key], nquote):
        return url_lookup[key]
    return None


def _specificity(text: str) -> float:
    """Concrete details (numbers, length) score higher than vague ones."""
    score = 0.6
    if any(ch.isdigit() for ch in text):
        score += 0.2
    if len(text) > 60:
        score += 0.2
    return min(1.0, score)


def _clamp(conf) -> float:
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, c))
