"""Classifier: label each page by type (heuristic, no LLM call).

Single responsibility: (url, text, is_home) -> page type. Used for
explainability and to route future page-type-specific extractors. Deterministic
and cheap by design — classifying pages with an LLM would blow the call budget.
"""

from urllib.parse import urlparse

# Page type -> path keywords that signal it (checked as whole words).
_TYPE_KEYWORDS = (
    ("team", ("team", "our-team", "meet-the-team", "people", "leadership")),
    ("founders", ("founders", "founder")),
    ("about", ("about", "about-us", "who-we-are", "our-story", "story",
               "company", "mission")),
    ("pricing", ("pricing", "plans")),
    ("customers", ("customers", "customer", "case-studies", "case-study")),
    ("product", ("product", "products", "features", "feature")),
    ("blog", ("blog", "news", "press", "changelog", "release-notes")),
    ("careers", ("careers", "jobs", "hiring")),
    ("docs", ("docs", "documentation", "api", "developers", "developer")),
    ("legal", ("legal", "privacy", "terms", "cookie", "gdpr")),
)

PAGE_TYPES = tuple(t for t, _ in _TYPE_KEYWORDS) + ("homepage", "unknown")

# Path segments that introduce customer proof. A page AT one of these is the
# INDEX (a logo wall — still about us); a page BELOW one profiles a DIFFERENT
# company, and must not be read as evidence about the site's owner.
_CUSTOMER_SEGMENTS = frozenset({
    "customers", "customer", "case-studies", "case-study", "casestudies",
    "customer-stories", "customer-story", "success-stories", "success-story",
})


def _path_words(path: str) -> str:
    words = "".join(ch if ch.isalnum() else " " for ch in (path or "").lower())
    return f" {' '.join(words.split())} "


def classify_page(url: str, text: str = "", is_home: bool = False) -> str:
    """Return the page type for a URL (path-keyword first, then home fallback)."""
    path = urlparse(url).path
    padded = _path_words(path)
    if padded != "  ":
        for page_type, keywords in _TYPE_KEYWORDS:
            for kw in keywords:
                if f" {kw.replace('-', ' ')} " in padded:
                    return page_type
    if is_home or path.rstrip("/") in ("", "/"):
        return "homepage"
    return "unknown"


def is_customer_story(url: str) -> bool:
    """True for an INDIVIDUAL customer story (``/customers/mindbody``), False for
    the index that lists them (``/customers``).

    The distinction matters because the two pages are about different companies.
    An index is a logo wall — still the site owner talking about itself. A story
    is several screens about the CUSTOMER: their mission, their market, their
    staff. Extracting from one as though it described the site's owner is how
    Stripe ends up with "transform wellness experiences" as its mission and
    Notion ends up with OpenAI's COO on its team.
    """
    segments = [s for s in (urlparse(url or "").path or "").split("/") if s]
    for i, segment in enumerate(segments):
        if segment.lower() in _CUSTOMER_SEGMENTS:
            return i + 1 < len(segments)
    return False
