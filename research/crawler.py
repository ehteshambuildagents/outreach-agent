"""Crawler: discover the most valuable same-host pages to read.

Single responsibility: from a page's HTML, find internal links worth fetching —
normalised, de-duplicated, relevance-scored, capped, team/about/founder first.
Irrelevant bulk (blog posts, docs, changelog, legal/privacy) never matches and
is therefore never crawled.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config.settings import MAX_EXTRA_PAGES, MAX_PER_SECTION, SITEMAP_MAX_URLS

ALLOWED_SCHEMES = ("http", "https")

# Path keywords in PRIORITY order (highest value first): people -> story/company
# -> positioning/product -> customers/proof -> commercial -> recent/hiring/docs.
# Whole-word matching avoids false hits. Bulk sections (blog/docs/...) are capped
# per-section so they can't crowd out high-value pages.
SUBPAGE_KEYWORDS = (
    # people (highest — founders/team)
    "team", "our-team", "meet-the-team", "founders", "founder", "leadership",
    "people", "about-us", "about", "who-we-are",
    # story / company / mission
    "our-story", "story", "company", "mission",
    # positioning / what they do
    "services", "solutions", "product", "products", "features", "feature",
    "integrations", "industries",
    # customers / proof
    "customers", "customer", "case-studies", "case-study", "testimonials",
    # commercial
    "pricing", "plans",
    # recent activity / hiring / support
    "blog", "news", "press", "careers", "jobs", "docs", "documentation",
    "contact",
)
# Genuine team-listing pages (priority <= this index) — worth rendering.
PEOPLE_CUTOFF = SUBPAGE_KEYWORDS.index("who-we-are")


def normalize_url(url: str) -> str:
    """Canonical form for de-duplication: lower host, drop fragment, trim slash."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def keyword_priority(path: str):
    """Priority of a path (lower = higher), or None if not a target page.

    Whole word/phrase matching: "mission" won't match "commission"; "team"
    matches "/our-team" but not "/teams" or "/teamwork-blog".
    """
    words = "".join(ch if ch.isalnum() else " " for ch in (path or "").lower())
    padded = f" {' '.join(words.split())} "
    if padded == "  ":
        return None
    for index, keyword in enumerate(SUBPAGE_KEYWORDS):
        phrase = keyword.replace("-", " ")
        if f" {phrase} " in padded:
            return index
    return None


def is_people_page(url: str) -> bool:
    """True for genuine team/about/founder pages (worth rendering)."""
    priority = keyword_priority(urlparse(url).path)
    return priority is not None and priority <= PEOPLE_CUTOFF


# Order in which the ADAPTIVE crawler visits pages. Front-loaded on the pages
# that carry the anchors a personalized cold email needs — WHO to write to and
# WHY they're interesting: about/story, then team/leadership, then customer
# proof — BEFORE lower-personalization-value product/pricing pages. Reaching an
# anchor sooner means we can stop sooner (cheaper) while still gathering a named
# decision-maker and a concrete, recent reason to reach out.
# This is separate from keyword_priority (which ranks people pages first for
# founder discovery); the confidence score still pulls us to team pages when a
# founder would meaningfully raise confidence.
_CRAWL_ORDER = (
    ("about", "about-us", "who-we-are", "our-story", "story", "company", "mission"),
    ("team", "our-team", "meet-the-team", "founders", "founder", "leadership",
     "people"),
    ("customers", "customer", "case-studies", "case-study", "testimonials"),
    ("services", "solutions", "product", "products", "features", "feature",
     "integrations", "industries"),
    ("pricing", "plans"),
    ("blog", "news", "press"),
    ("careers", "jobs"),
    ("docs", "documentation"),
    ("contact",),
)

# Groups whose pages supply a personalization ANCHOR — a named person
# (about/team), customer proof (customers/case-studies), or a recent event
# (blog/news/press). Used to decide whether it's still worth crawling for an
# anchor before declaring research "sufficient".
_HIGH_VALUE_GROUPS = frozenset({0, 1, 2, 5})

# Groups whose pages can name a HUMAN (the person-discovery goal): about/company
# (0), team/leadership/people (1), customer stories (2), blog/news/press bylines
# (5), careers leadership refs (6), contact (8). Product/pricing/docs never name a
# decision-maker, so person-hunting never wastes budget on them.
_PERSON_SOURCE_GROUPS = frozenset({0, 1, 2, 5, 6, 8})


def crawl_order_rank(path: str) -> int:
    """Adaptive-crawl visiting order for a path (lower = visit sooner)."""
    words = "".join(ch if ch.isalnum() else " " for ch in (path or "").lower())
    padded = f" {' '.join(words.split())} "
    for index, group in enumerate(_CRAWL_ORDER):
        for keyword in group:
            if f" {keyword.replace('-', ' ')} " in padded:
                return index
    return len(_CRAWL_ORDER)  # unmatched (e.g. remaining sitemap pages) -> last


def is_high_value_page(url: str) -> bool:
    """True for pages that can supply a personalization anchor: about/team
    (a named human), customers/case-studies (proof), or blog/news (recent
    events). Product/pricing/careers/docs/contact are useful context but are
    NOT anchors, so they don't keep the crawler going on their own."""
    return crawl_order_rank(urlparse(url).path) in _HIGH_VALUE_GROUPS


def is_person_source_page(url: str) -> bool:
    """True for pages that can name a HUMAN decision-maker (about/team/leadership/
    people/company, customer stories, blog & press bylines, careers, contact).
    Drives the dedicated person-discovery phase; product/pricing/docs are
    excluded so hunting a person never burns budget on pages that never list one."""
    return crawl_order_rank(urlparse(url).path) in _PERSON_SOURCE_GROUPS


def ordered_candidates(base_url, home_html, sitemap_subs, limit=MAX_EXTRA_PAGES):
    """Same-host relevant sub-pages, de-duplicated, in ADAPTIVE-crawl order."""
    from_links = discover_subpages(base_url, home_html, limit=limit)
    merged, seen = [], set()
    for url in list(from_links) + list(sitemap_subs or []):
        key = normalize_url(url)
        if key not in seen:
            seen.add(key)
            merged.append(url)
    merged.sort(key=lambda u: crawl_order_rank(urlparse(u).path))
    return merged[:limit]


def discover_subpages(base_url: str, html: str, limit: int = MAX_EXTRA_PAGES) -> list:
    """Return up to `limit` same-host target URLs, ranked by relevance."""
    base = urlparse(base_url)
    base_host = (base.hostname or "").lower()
    base_path = base.path.lower().rstrip("/")

    candidates, seen = [], set()
    try:
        anchors = BeautifulSoup(html or "", "html.parser").find_all("a", href=True)
    except Exception:
        return []

    for anchor in anchors:
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlparse(urljoin(base_url, href))
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            continue
        if (parsed.hostname or "").lower() != base_host:
            continue                      # same-host crawl only
        path = parsed.path.lower().rstrip("/")
        if not path or path == base_path:
            continue                      # skip the homepage itself
        priority = keyword_priority(path)
        if priority is None:
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        key = normalize_url(clean)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((priority, clean))

    return _rank_and_cap(candidates, limit)


def _section(url: str) -> str:
    """First path segment, e.g. '/blog/post' -> 'blog' (for per-section caps)."""
    return urlparse(url).path.strip("/").split("/", 1)[0].lower()


def _rank_and_cap(candidates, limit: int) -> list:
    """Sort by priority, then cap pages per top-level section so bulk areas
    (blog/docs/...) can't crowd out high-value pages."""
    candidates.sort(key=lambda item: item[0])
    result, per_section = [], {}
    for _, url in candidates:
        section = _section(url)
        if per_section.get(section, 0) >= MAX_PER_SECTION:
            continue
        per_section[section] = per_section.get(section, 0) + 1
        result.append(url)
        if len(result) >= limit:
            break
    return result


# ──────────────────────────────────────────────────────────────────────
#  Sitemap / robots.txt discovery (finds pages the homepage doesn't link,
#  e.g. on JS sites whose links aren't in the static HTML).
# ──────────────────────────────────────────────────────────────────────
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_SITEMAP_DIRECTIVE_RE = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")


def discover_from_sitemap(base_url: str, fetch_fn, limit: int = MAX_EXTRA_PAGES) -> list:
    """Return relevant same-host URLs found via robots.txt + sitemap.xml.

    `fetch_fn(url) -> (ok, text)` is injected (kept SSRF-safe by the caller);
    bounded so it never fetches an unbounded number of sitemaps.
    """
    base = urlparse(base_url)
    base_host = (base.hostname or "").lower()
    root = f"{base.scheme}://{base.netloc}"

    sitemap_urls = _sitemaps_from_robots(root, fetch_fn) or []
    if f"{root}/sitemap.xml" not in sitemap_urls:
        sitemap_urls.append(f"{root}/sitemap.xml")

    locs, fetched = [], 0
    for sm in sitemap_urls[:3]:                       # bounded: at most 3 docs
        if fetched >= 3:
            break
        ok, body = fetch_fn(sm)
        fetched += 1
        if not ok:
            continue
        found = _LOC_RE.findall(body)
        # A sitemap index lists more .xml sitemaps — follow one level, bounded.
        child_maps = [u for u in found if u.lower().endswith(".xml")]
        page_locs = [u for u in found if not u.lower().endswith(".xml")]
        locs.extend(page_locs)
        for child in child_maps[:2]:
            if fetched >= 3:
                break
            ok2, body2 = fetch_fn(child)
            fetched += 1
            if ok2:
                locs.extend(u for u in _LOC_RE.findall(body2)
                            if not u.lower().endswith(".xml"))
        if len(locs) >= SITEMAP_MAX_URLS:
            break

    candidates, seen = [], set()
    for loc in locs[:SITEMAP_MAX_URLS]:
        parsed = urlparse(loc.strip())
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            continue
        if (parsed.hostname or "").lower() != base_host:
            continue
        priority = keyword_priority(parsed.path)
        if priority is None:
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        key = normalize_url(clean)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((priority, clean))

    return _rank_and_cap(candidates, limit)


def _sitemaps_from_robots(root: str, fetch_fn) -> list:
    """Read robots.txt for 'Sitemap:' directives (bounded)."""
    ok, body = fetch_fn(f"{root}/robots.txt")
    if not ok:
        return []
    return _SITEMAP_DIRECTIVE_RE.findall(body)[:5]


def merge_discovered(*url_lists, limit: int = MAX_EXTRA_PAGES) -> list:
    """Merge several ranked URL lists, de-duplicated, preserving order + caps."""
    combined, seen = [], set()
    for urls in url_lists:
        for url in urls:
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            priority = keyword_priority(urlparse(url).path)
            combined.append((999 if priority is None else priority, url))
    return _rank_and_cap(combined, limit)
