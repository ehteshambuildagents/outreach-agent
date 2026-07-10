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
