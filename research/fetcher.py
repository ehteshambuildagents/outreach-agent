"""Fetcher: SSRF-safe page retrieval (requests fast path + Playwright render).

Single responsibility: given a URL, return HTML safely. Security is enforced
here for EVERY fetch — fast or rendered:
  * scheme allowlist + DNS resolution; private/loopback/link-local/reserved blocked
  * redirects followed manually and re-validated each hop
  * per-page timeout + download size cap
  * Playwright loads abort any sub-resource request to a non-public host
The adaptive pipeline fetches pages one at a time (it must score after each
page before deciding whether the next is needed), so fetches are sequential.
"""

import ipaddress
import logging
import random
import socket
import time
from urllib.parse import urljoin, urlparse

import requests

from config.settings import (
    HTTP_MAX_RETRIES,
    HTTP_RETRY_BASE_SECONDS,
    JS_RENDER_TEXT_THRESHOLD,
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    READ_DEADLINE_SECONDS,
    RENDER_MAX_WAIT_MS,
    RENDER_NAV_TIMEOUT_MS,
    RENDER_SETTLE_MS,
    RENDER_STABLE_CHECKS,
    RENDER_STABLE_POLL_MS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)
from research.cleaner import clean_html_text

log = logging.getLogger("research.fetcher")

ALLOWED_SCHEMES = ("http", "https")

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# host -> is-public, shared by the Playwright SSRF route guard.
# Bounded so a long-running process can't grow it without limit.
_HOST_PUBLIC_CACHE = {}
_HOST_CACHE_MAX = 4096


# ──────────────────────────────────────────────────────────────────────
#  URL validation + SSRF
# ──────────────────────────────────────────────────────────────────────
def validate_url(url) -> tuple:
    """Validate a URL's format and safety. Returns (ok: bool, reason: str|None)."""
    if not url or not isinstance(url, str):
        return False, "No URL was provided."
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "That URL could not be parsed."
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, "URL must start with http:// or https://"
    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no host name."
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False, "Could not resolve that host name."
    except Exception:
        return False, "Could not resolve that host name."
    resolved = {info[4][0] for info in infos}
    if not resolved:
        return False, "Could not resolve that host name."
    for ip_str in resolved:
        if not is_public_ip(ip_str):
            return False, "Refusing to fetch an internal/non-public address."
    return True, None


def is_public_ip(ip_str: str) -> bool:
    """True only for normal, routable, public IP addresses."""
    ip_str = ip_str.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return is_public_ip(str(ip.ipv4_mapped))
    return True


def host_is_public(host: str) -> bool:
    """Resolve a host and return True only if every address is public (cached)."""
    key = (host or "").lower()
    if key in _HOST_PUBLIC_CACHE:
        return _HOST_PUBLIC_CACHE[key]
    try:
        infos = socket.getaddrinfo(key, None)
        result = len(infos) > 0 and all(is_public_ip(i[4][0]) for i in infos)
    except Exception:
        result = False
    if len(_HOST_PUBLIC_CACHE) >= _HOST_CACHE_MAX:
        _HOST_PUBLIC_CACHE.clear()
    _HOST_PUBLIC_CACHE[key] = result
    return result


def ssrf_route_guard(route):
    """Playwright request router: abort any request to a non-public host."""
    try:
        parsed = urlparse(route.request.url)
        if parsed.scheme.lower() in ("http", "https"):
            host = parsed.hostname
            if not host or not host_is_public(host):
                return route.abort()
        return route.continue_()
    except Exception:
        try:
            return route.abort()
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────────
#  Static (requests) fetch
# ──────────────────────────────────────────────────────────────────────
def fetch_static(url: str, session=None) -> tuple:
    """Fetch one page with requests, retrying TRANSIENT failures. Returns
    (ok: bool, html_or_error: str).

    Retries timeouts, connection drops, and 5xx with exponential backoff +
    jitter. Does NOT retry 4xx / bad content-type / SSRF blocks (permanent).
    Timeouts are reported distinctly from connection and parse failures.
    """
    owns_session = session is None
    session = session or requests.Session()
    try:
        last_error = "The page could not be fetched."
        for attempt in range(HTTP_MAX_RETRIES + 1):
            ok, payload, retryable = _fetch_static_once(session, url)
            if ok:
                return True, payload
            last_error = payload
            if not retryable or attempt == HTTP_MAX_RETRIES:
                break
            delay = HTTP_RETRY_BASE_SECONDS * (2 ** attempt)
            delay += random.uniform(0, delay * 0.25)
            log.info("retry %d/%d for %s (%s)", attempt + 1, HTTP_MAX_RETRIES,
                     url, payload)
            time.sleep(delay)
        log.info("fetch failed: %s -> %s", url, last_error)
        return False, last_error
    finally:
        if owns_session:
            session.close()


def fetch_with_fallbacks(url: str, *, render_fetcher=None, prefer_render=False,
                         pre_fetched=None, allow_paid=False, fetch_fn=None) -> tuple:
    """Fetch one page through the whole escalation chain.

    Returns ``(ok, content_or_error, method)`` where method is one of
    ``fast`` | ``rendered`` | ``firecrawl`` | ``jina``.

        requests  ->  headless browser  ->  Firecrawl  ->  Jina Reader

    The first two are free and cover almost everything. The last two are PAID and
    exist for the case that used to end a whole research run: a site that refuses
    automated traffic outright (apple.com answers 403 to both requests and a
    plain headless browser), where the old chain gave up and the user was told
    only that research failed.

    ``allow_paid`` gates the paid tail so it runs for the pages that decide
    whether a run happens at all (the homepage), not for every candidate subpage
    in a crawl loop. Every step is best-effort: a provider that is unconfigured or
    throws simply hands on to the next one.

    ``fetch_fn`` overrides the fast path. The caller owns that seam so a module
    that already exposes (and tests substitute) its own ``fetch_static`` keeps
    exactly one place to intercept.
    """
    fast = fetch_fn or fetch_static
    ok, html = pre_fetched if pre_fetched is not None else fast(url)
    if ok:
        if prefer_render and render_fetcher is not None:
            rendered = render_fetcher.render(url)
            if rendered is not None and \
                    len(clean_html_text(rendered)) > len(clean_html_text(html)):
                return True, rendered, "rendered"
        elif render_fetcher is not None and \
                len(clean_html_text(html)) < JS_RENDER_TEXT_THRESHOLD:
            rendered = render_fetcher.render(url)
            if rendered is not None and \
                    len(clean_html_text(rendered)) > len(clean_html_text(html)):
                return True, rendered, "rendered"
        return True, html, "fast"

    first_error = html
    if render_fetcher is not None:
        rendered = render_fetcher.render(url)
        if rendered is not None:
            return True, rendered, "rendered"
    if not allow_paid:
        return False, first_error, "fast"

    for method, reader in (("firecrawl", _firecrawl_text), ("jina", _jina_text)):
        try:
            text = reader(url)
        except Exception:  # noqa: BLE001 - a fallback must never raise past here
            log.info("%s fallback errored for %s", method, url, exc_info=True)
            continue
        if text and len(text.strip()) >= _MIN_FALLBACK_CHARS:
            log.info("fetch recovered via %s: %s", method, url)
            return True, text, method
    return False, first_error, "fast"


# Below this a "successful" fallback is really an error page or a cookie wall.
_MIN_FALLBACK_CHARS = 200


def _firecrawl_text(url: str) -> str:
    """Firecrawl renders and de-bots pages the plain chain cannot reach."""
    from research import firecrawl
    if not firecrawl.available():
        return ""
    page = firecrawl.scrape(url) or {}
    return str(page.get("text") or page.get("markdown") or "")


def _jina_text(url: str) -> str:
    """Jina Reader returns clean markdown for a URL; last resort before giving up."""
    from research import jina
    if not jina.available():
        return ""
    return jina.clean_url(url) or ""


def _fetch_static_once(session, url: str) -> tuple:
    """One fetch attempt. Returns (ok, html_or_error, retryable: bool)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ok, reason = validate_url(current)
        if not ok:
            return False, reason, False               # SSRF/format -> permanent
        try:
            resp = session.get(
                current, headers=_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False, stream=True,
            )
        except requests.exceptions.Timeout:
            return False, "The site took too long to respond (timed out).", True
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to the site.", True
        except requests.exceptions.RequestException:
            return False, "The page could not be fetched.", False
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return False, "The site sent a redirect with no target.", False
                current = urljoin(current, location)
                continue
            if resp.status_code >= 500:
                return False, f"The site returned HTTP {resp.status_code}.", True
            if resp.status_code != 200:
                return False, f"The site returned HTTP {resp.status_code}.", False
            ctype = resp.headers.get("Content-Type", "").lower()
            if not _is_textual(ctype):
                return False, "That URL is not a text/HTML document.", False
            try:
                return True, _read_capped(resp), False
            except requests.exceptions.Timeout:
                return False, "The site took too long to send the page.", True
            except requests.exceptions.ConnectionError:
                return False, "The connection dropped while reading the page.", True
            except requests.exceptions.RequestException:
                return False, "The page could not be read.", False
        finally:
            resp.close()
    return False, "Too many redirects.", False


def _is_textual(ctype: str) -> bool:
    """Accept HTML plus XML/plain/markdown (docs, sitemaps); reject binaries."""
    if not ctype:
        return True
    return any(t in ctype for t in ("html", "xml", "text/", "markdown", "json"))


def _read_capped(resp) -> str:
    # Cap BOTH bytes and wall-clock time: requests' timeout is per-read, so a
    # server dripping bytes just under it could hold the connection open for a
    # long time (slow-loris). The deadline bounds total read time.
    chunks, total = [], 0
    deadline = time.monotonic() + READ_DEADLINE_SECONDS
    for chunk in resp.iter_content(chunk_size=8192):
        if time.monotonic() > deadline:
            break
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            break
        chunks.append(chunk)
    raw = b"".join(chunks)
    encoding = resp.encoding or "utf-8"
    # A malformed Content-Type charset (e.g. "charset=foo") makes bytes.decode
    # raise LookupError BEFORE errors="replace" can apply — fall back to utf-8 so
    # one bad header can never crash a fetch.
    try:
        return raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────
#  Headless browser (Playwright) — used only when escalation is needed
# ──────────────────────────────────────────────────────────────────────
class RenderFetcher:
    """Lazily-launched headless browser for JS pages. Optional; degrades to None."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._disabled = False

    def render(self, url: str):
        """Return rendered HTML, or None if rendering is unavailable/failed.

        Waits for the rendered text to STOP growing (bounded) so JS-built
        content like team cards is captured deterministically rather than
        whatever happened to exist at an arbitrary moment. Retries ONCE if the
        result is still suspiciously thin (one extra render, not a loop).
        """
        ok, _ = validate_url(url)
        if not ok or self._disabled:
            return None
        best = self._render_once(url)
        if best is None:
            return None
        if len(clean_html_text(best)) < JS_RENDER_TEXT_THRESHOLD:
            retry = self._render_once(url)
            if retry is not None and \
                    len(clean_html_text(retry)) > len(clean_html_text(best)):
                best = retry
        return best

    def _render_once(self, url: str):
        # Launch failure (e.g. Playwright not installed) disables rendering for
        # the rest of this run; a single PAGE failure must NOT — otherwise one
        # slow page would silently drop us to requests-only for the whole site.
        try:
            self._ensure_browser()
        except Exception:
            self._disabled = True
            return None
        page = None
        try:
            page = self._context.new_page()
            page.route("**/*", ssrf_route_guard)
            page.goto(url, wait_until="domcontentloaded",
                      timeout=RENDER_NAV_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=RENDER_SETTLE_MS)
            except Exception:
                pass
            _wait_for_stable_text(page)
            return page.content()
        except Exception:
            return None  # per-page failure: keep the browser for other pages
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _ensure_browser(self):
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright  # lazy import

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=USER_AGENT)
        self._context.set_default_navigation_timeout(RENDER_NAV_TIMEOUT_MS)

    def close(self):
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass


def _wait_for_stable_text(page):
    """Poll the rendered text length until it stops growing, or the deadline.

    This makes rendering deterministic: we capture once the DOM has settled
    rather than at an arbitrary instant after load (the root cause of run-to-run
    founder/team inconsistency on JS pages). Bounded by RENDER_MAX_WAIT_MS so it
    never significantly increases runtime.
    """
    deadline = time.monotonic() + RENDER_MAX_WAIT_MS / 1000.0
    prev, stable = -1, 0
    while time.monotonic() < deadline:
        try:
            cur = page.evaluate(
                "() => (document.body && document.body.innerText || '').length"
            )
        except Exception:
            return
        if cur and cur == prev:
            stable += 1
            if stable >= RENDER_STABLE_CHECKS:
                return
        else:
            stable = 0
        prev = cur
        try:
            page.wait_for_timeout(RENDER_STABLE_POLL_MS)
        except Exception:
            return
