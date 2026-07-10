"""Cleaner: turn raw HTML into meaningful visible text.

Single responsibility: HTML -> clean text. Also provides text normalisation
used by the verifier to ground quotes against page content.
"""

import re

from bs4 import BeautifulSoup

from config.settings import MAX_PAGE_TEXT_CHARS

# Tags whose contents are navigation/boilerplate/noise, not company substance.
_STRIP_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header", "aside",
    "form", "iframe", "svg", "button", "input", "template",
)


def clean_html_text(html: str) -> str:
    """Extract meaningful visible text, dropping nav/footer/scripts/styles/etc."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())  # collapse runs of whitespace
    return text[:MAX_PAGE_TEXT_CHARS]


def normalize_for_match(text: str) -> str:
    """Lowercase + collapse non-alphanumerics to single spaces.

    Used to test whether a model-supplied quote actually appears in a page's
    cleaned text, tolerant of punctuation/whitespace/casing differences.
    """
    if not text:
        return ""
    out = []
    prev_space = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        else:
            if not prev_space:
                out.append(" ")
            prev_space = True
    return "".join(out).strip()


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


def strip_emails(text: str) -> str:
    """Remove email addresses so a name appearing ONLY inside an email
    ('hakan@company.com') is not treated as a real on-page mention."""
    if not text:
        return text
    return _EMAIL_RE.sub(" ", text)


def contains_phrase(haystack_norm: str, needle_norm: str) -> bool:
    """Whole-word/phrase containment over already-normalised text.

    Token-boundary aware so a short value like "ben" does NOT match inside
    "benefits", and "ana" does not match inside "analytics". Both arguments
    must already be normalised with `normalize_for_match`.
    """
    if not needle_norm or not haystack_norm:
        return False
    return f" {needle_norm} " in f" {haystack_norm} "
