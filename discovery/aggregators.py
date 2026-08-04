"""Aggregator / job-board / marketplace classification for discovery candidates.

Why this exists: a web search for "companies hiring an AI video creator" returns
mostly JOB BOARDS, because a job board is the page that best matches those words.
Worse, the old confidence scoring REWARDED hiring vocabulary, so a board like
remoteleaf.com outranked the actual companies. This module is the deterministic
gate that tells those apart.

Two independent questions, deliberately kept separate:

  1. ``domain_kind(domain)`` — is this domain FUNDAMENTALLY an intermediary?
     Indeed is a job board no matter which page you land on.
  2. ``page_kind(url, title, content)`` — is this particular PAGE a listing?
     A job posting on a company's own site is not the company's home page, but
     the company behind it is still a perfectly good prospect.

Neither is a hard ban. The engine DEMOTES these to a fallback tier rather than
dropping them, and ``query_requests(kind, text)`` re-admits a category the user
actually asked for ("find me recruiting platforms" should return recruiting
platforms). That matches the product rule: aggregators are a fallback, not a
primary result, and never a silent censorship of what the user asked for.

Note on ATS subdomains: ``jobs.storykit.io`` has registrable domain
``storykit.io``, so a company's own careers subdomain resolves to the COMPANY
and is treated as hiring evidence. Only third-party ATS domains (lever.co,
ashbyhq.com, …) are intermediaries. Several of those are also real B2B SaaS
companies, which is exactly why ``query_requests`` exists.
"""

import re
from urllib.parse import urlparse

# ── Kinds ──────────────────────────────────────────────────────────────────
COMPANY = "company"
JOB_BOARD = "job_board"
ATS = "ats"
MARKETPLACE = "marketplace"
DIRECTORY = "directory"
LIST_SITE = "list_site"
MEDIA = "media"
# Classified from Apollo's industry codes rather than the domain (see
# intent.covers_category_kind): a trade publisher or an industry body matches a
# category search because it COVERS that category, not because it operates in it.
ASSOCIATION = "association"
GOVERNMENT = "government"
UNKNOWN = "unknown"

# Everything that is an intermediary rather than a prospect.
INTERMEDIARY_KINDS = frozenset({JOB_BOARD, ATS, MARKETPLACE, DIRECTORY,
                                LIST_SITE, MEDIA, ASSOCIATION, GOVERNMENT})

# Of those, the ones that can never be a prospect at all, because the page is
# ABOUT other companies: a Tracxn profile of Acme is not Tracxn-the-prospect, and
# Acme is not on that domain, so there is nothing to keep. The remainder
# (job boards, ATS, marketplaces) are real operating businesses, so they are
# demoted to the fallback tier instead and promoted back when asked for.
DROP_KINDS = frozenset({DIRECTORY, LIST_SITE, MEDIA})
DEMOTE_KINDS = frozenset({JOB_BOARD, ATS, MARKETPLACE})

# Verdicts backed by EVIDENCE rather than by a guess: a curated domain list, or
# Apollo's own industry codes. A second source finding the same domain and simply
# not knowing any better must never promote one of these back to "company" —
# fintechfutures.com is NAICS 513120 (Periodical Publishers) whatever a web search
# result thinks, and letting the merge overturn it put a trade magazine at the top
# of the page again after it had been correctly demoted.
AUTHORITATIVE_KINDS = frozenset({MEDIA, ASSOCIATION, GOVERNMENT})

# ── Domain sets ────────────────────────────────────────────────────────────
# Job boards and job aggregators: the class of site that was polluting results.
_JOB_BOARDS = frozenset({
    "indeed.com", "linkedin.com", "glassdoor.com", "glassdoor.co.uk",
    "ziprecruiter.com", "monster.com", "careerbuilder.com", "simplyhired.com",
    "dice.com", "snagajob.com", "flexjobs.com", "theladders.com",
    "remoteok.com", "remoteok.io", "weworkremotely.com", "remotive.com",
    "remoteleaf.com", "justremote.co", "workingnomads.com", "nodesk.co",
    "jobspresso.co", "otta.com", "hiring.cafe", "himalayas.app", "jobicy.com",
    "dailyremote.com", "arc.dev", "jobgether.com", "wellfound.com", "angel.co",
    "angellist.com", "builtin.com", "jooble.org", "talent.com", "adzuna.com",
    "jobrapido.com", "lensa.com", "whatjobs.com", "neuvoo.com", "trabajo.org",
    "startup.jobs", "authenticjobs.com", "workatastartup.com", "4dayweek.io",
    "remoterocketship.com", "nowhiteboard.org", "golangprojects.com",
    "aijobs.net", "ai-jobs.net", "cryptojobslist.com", "web3.career",
    "mediabistro.com", "tealhq.com", "jobs-radar.com", "simplify.jobs",
    "ziprecruiter.co.uk", "reed.co.uk", "totaljobs.com", "cv-library.co.uk",
    "seek.com.au", "naukri.com", "shine.com", "foundit.in", "bayt.com",
    "efinancialcareers.com", "idealist.org", "workingtitles.io", "jobright.ai",
})

# A curated list can never be complete: a live run surfaced mediabistro.com,
# jobs-radar.com, simplify.jobs and tealhq.com, none of which were listed. These
# TOKEN rules catch the whole family. Token-exact (not substring) on purpose:
# "jobber.com" is a real field-service SaaS and must not be caught by "job".
_JOB_TOKENS = frozenset({"job", "jobs", "hiring", "hire", "careers", "career",
                         "recruiting", "recruiter", "recruiters", "recruitment",
                         "vacancies", "vacancy", "resume", "resumes", "cv"})
# TLDs reserved for or dominated by employment/recruiting content.
_JOB_TLDS = ("jobs", "careers", "career", "recruiting")

# Applicant tracking systems / hosted careers pages. A URL here is a POSTING,
# never the employer's own site.
_ATS = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "recruitee.com",
    "teamtailor.com", "smartrecruiters.com", "bamboohr.com", "jazzhr.com",
    "breezy.hr", "jobvite.com", "icims.com", "taleo.net", "successfactors.com",
    "applytojob.com", "recruiterbox.com", "pinpointhq.com", "myworkdayjobs.com",
    "workday.com", "personio.com", "personio.de", "join.com", "hibob.com",
    "rippling-ats.com", "paylocity.com", "ultipro.com", "trakstar.com",
    "polymer.co", "gem.com", "wellfoundtalent.com", "getro.com", "getro.org",
    "consider.com", "pallet.com", "rolebot.io",
})

# Freelance / talent / service marketplaces: they SELL the labour, they are not
# the buying company. (Contra sits here; it is a real company, but as a search
# result for "hiring an AI video creator" it is the marketplace, not the buyer.)
_MARKETPLACES = frozenset({
    "upwork.com", "fiverr.com", "freelancer.com", "toptal.com", "guru.com",
    "peopleperhour.com", "99designs.com", "contra.com", "gun.io", "arc.dev",
    "codementor.io", "andela.com", "turing.com", "lemon.io", "braintrust.com",
    "malt.com", "workana.com", "twine.net", "catalant.com", "expertise.com",
    "thumbtack.com", "bark.com",
})

# Company directories / data vendors / review sites: profiles ABOUT companies.
_DIRECTORIES = frozenset({
    "crunchbase.com", "pitchbook.com", "dealroom.co", "tracxn.com", "owler.com",
    "zoominfo.com", "apollo.io", "rocketreach.co", "lusha.com", "cognism.com",
    "6sense.com", "slintel.com", "similarweb.com", "getlatka.com", "growjo.com",
    "theorg.com", "privco.com", "cbinsights.com", "clutch.co", "goodfirms.co",
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com",
    "trustpilot.com", "producthunt.com", "alternativeto.net", "saasworthy.com",
    "sourceforge.net", "slashdot.org", "bbb.org", "yelp.com", "manta.com",
    "dnb.com", "opencorporates.com", "companieshouse.gov.uk", "zippia.com",
    "comparably.com", "trycomp.com", "levels.fyi", "payscale.com",
    "salary.com",
})

# Listicle / SEO round-up publishers.
_LIST_SITES = frozenset({
    "explodingtopics.com", "starterstory.com", "failory.com", "statista.com",
    "eu-startups.com", "sifted.eu", "startupsavant.com", "toolify.ai",
    "futurepedia.io", "theresanaiforthat.com", "aitoolhunt.com", "topai.tools",
    "aitools.fyi", "insidr.ai", "supertools.therundown.ai",
    # A directory of VC-backed startups — a page ABOUT companies, not a company.
    "vcbacked.co",
})

_MEDIA = frozenset({
    "techcrunch.com", "forbes.com", "inc.com", "entrepreneur.com",
    "businessinsider.com", "nytimes.com", "wsj.com", "bloomberg.com",
    "reuters.com", "apnews.com", "cnn.com", "bbc.com", "theverge.com",
    "wired.com", "fastcompany.com", "venturebeat.com", "thenextweb.com",
    "prnewswire.com", "businesswire.com", "medium.com", "substack.com",
    "dev.to", "hashnode.dev", "hackernoon.com", "wikipedia.org",
    "wikimedia.org", "fandom.com", "reddit.com", "quora.com", "youtube.com",
    "pinterest.com", "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "github.io", "notion.site", "wordpress.com",
    "blogspot.com", "wixsite.com",
    # Publications / newsletters / blogs surfaced as false "prospects" on a live
    # fintech search (2026-08-04): a legal-news publisher, a fintech newsletter,
    # and a tech blog. They cover the category, they do not operate in it.
    # NB: trade publishers Apollo can identify by NAICS 513120 (e.g.
    # fintechfutures.com) are intentionally NOT hard-dropped here — they are
    # demoted via industry code so a genuine role posting still keeps them.
    "natlawreview.com", "thisweekinfintech.com", "siliconsnark.com",
})

_BY_KIND = (
    (JOB_BOARD, _JOB_BOARDS),
    (ATS, _ATS),
    (MARKETPLACE, _MARKETPLACES),
    (DIRECTORY, _DIRECTORIES),
    (LIST_SITE, _LIST_SITES),
    (MEDIA, _MEDIA),
)

# ── Page-level patterns ────────────────────────────────────────────────────
# Vocabulary that only appears on a listing/aggregation page, not on a company's
# own marketing site. "we're hiring" is deliberately ABSENT: that is a company
# signal, and conflating the two is the bug this module fixes.
_LISTING_TEXT_RE = re.compile(
    r"("
    r"\b\d[\d,]*\+?\s+(?:jobs|openings|vacancies|positions|companies|tools|"
    r"startups|freelancers)\b"
    r"|\bbrowse\s+(?:jobs|companies|freelancers|categories)\b"
    r"|\bsearch\s+(?:thousands|millions)\b"
    r"|\bpost\s+a\s+(?:job|project|gig)\b"
    r"|\bfind\s+(?:jobs|work|freelancers|talent|candidates)\b"
    r"|\bjob\s+(?:board|alerts|seekers?|search engine)\b"
    r"|\bapply\s+(?:now|to\s+\d)"
    r"|\bhire\s+(?:top|vetted|pre-screened|freelance)\b"
    r"|\bcompare\s+\d+\s+"
    r"|\b(?:top|best)\s+\d+\s+"
    r"|\balternatives\s+to\b|\bvs\.?\s+\w+\s+comparison\b"
    r"|\bsalar(?:y|ies)\s+(?:range|data|report|guide)\b"
    r"|\bcompany\s+profile\b|\bdirectory\s+listing\b"
    r")",
    re.I,
)

# Title conventions of a PUBLICATION / newsletter / blog, so one on an unlisted
# domain is caught the way the curated set catches the known ones. Anchored on the
# TITLE (a signal the search result always carries), not on the domain — a bare
# domain token like "post" or "times" would wrongly flag real companies (Postman,
# Timescale), so the publication word must appear as a distinct trailing noun or a
# newsletter idiom. "we're hiring"/product words are absent on purpose.
_PUBLICATION_TITLE_RE = re.compile(
    r"(?:"
    r"\bthis\s+week\s+in\b"                       # "This Week in Fintech"
    r"|\bissue\s+#?\d+\b"                          # a newsletter issue number
    r"|\bsubscribe\s+to\b|\bour\s+newsletter\b"
    r"|\bsign\s+up\s+for\s+[\w\s]*newsletter\b"
    r"|\b(?:the\s+)?[\w'&.-]+(?:\s+[\w'&.-]+){0,3}\s+"
    r"(?:newsletter|weekly|biweekly|daily|digest|dispatch|gazette|chronicle|"
    r"herald|tribune|journal|magazine|gazette|bulletin|podcast|"
    r"times|review|monthly)\b"
    r")",
    re.I,
)

# Headline shape of an ARTICLE / content asset, never a company home page:
# "The B2B Fintech Pricing Journey", "… Playbook", "… Deep Dive". Requires words
# BEFORE the trailing noun so a company literally named "Journey" is untouched.
_ARTICLE_TITLE_RE = re.compile(
    r"(?:"
    # Headline ending in a content-asset noun ("… Pricing Journey", "… Playbook").
    r"^\s*(?:the\s+)?[\w'&,\-]+(?:\s+[\w'&,\-]+){1,7}\s+"
    r"(?:journey|guide|playbook|handbook|report|breakdown|deep\s+dive|"
    r"landscape|roadmap|checklist|tutorial|walkthrough|whitepaper|ebook|"
    r"webinar|explained)\s*$"
    # Common mid-title article idioms that a company home page never uses.
    r"|\b(?:complete|ultimate|definitive|beginner'?s|founder'?s|quick|step-by-step)"
    r"\s+guide\s+to\b"
    r"|\bplaybook\s+for\b"
    r"|\bthe\s+state\s+of\s+\w+"
    r")",
    re.I,
)

# Paths that mark a listing/index page rather than a company page.
_LISTING_PATHS = (
    "/jobs/", "/job/", "/careers/search", "/vacancies", "/openings",
    "/companies/", "/company/", "/directory", "/listings", "/listing/",
    "/browse", "/search", "/category/", "/categories/", "/tag/", "/topics/",
    "/best-", "/top-", "/alternatives", "/vs/", "/compare/", "/reviews/",
    "/salaries", "/salary/", "/profile/", "/profiles/",
)

# What the USER has to say for an intermediary category to be legitimate.
_REQUESTED_RE = {
    JOB_BOARD: r"\b(job\s*board|job\s*site|job\s*aggregator|hiring\s*platform|"
               r"job\s*search\s*engine)\b",
    ATS: r"\b(ats|applicant\s*tracking|recruit(?:ing|ment)\s*(?:platform|software|"
         r"tool)|hr\s*(?:tech|software|platform))\b",
    MARKETPLACE: r"\b(marketplace|freelance\s*platform|talent\s*platform|"
                 r"gig\s*platform|staffing)\b",
    DIRECTORY: r"\b(director(?:y|ies)|data\s*(?:vendor|provider)|review\s*site|"
               r"listing\s*site)\b",
    LIST_SITE: r"\b(listicle|round-?up|list\s*site)\b",
    MEDIA: r"\b(media|publisher|news\s*(?:site|outlet)|blog|newsletter)\b",
}


def domain_kind(domain: str) -> str:
    """Classify a registrable domain. Returns a kind; COMPANY when nothing
    matches (absence of evidence is not evidence of an aggregator)."""
    d = (domain or "").strip().lower()
    if not d:
        return UNKNOWN
    for kind, members in _BY_KIND:
        if d in members or any(d.endswith("." + m) for m in members):
            return kind
    if _looks_like_job_domain(d):
        return JOB_BOARD
    return COMPANY


def _looks_like_job_domain(domain: str) -> bool:
    """Token-level test for the job-board family, so the curated list is not the
    only defence. ``jobs-radar.com`` and ``simplify.jobs`` are caught; a real
    company like ``jobber.com`` is not, because the match is on whole tokens."""
    parts = domain.split(".")
    if len(parts) >= 2 and parts[-1] in _JOB_TLDS:
        return True
    core = parts[0] if parts else ""
    tokens = [t for t in re.split(r"[^a-z0-9]+", core) if t]
    if any(t in _JOB_TOKENS for t in tokens):
        return True
    # Fused compounds a split cannot see ("remotejobs", "aijobsboard").
    # Singular "career" counts too: _JOB_TOKENS already trusts it on a hyphen
    # split, and omitting it here let gulfcareerhunt.com through as a real
    # company. Singular "job" is deliberately NOT here — it is short enough to
    # sit inside plausible product names, and "jobber" already needs an
    # exception. A board named that way still gets caught by the curated list.
    return bool(re.search(r"(?:^|[a-z])(jobs|careers?|hiring|recruiting)(?:$|[a-z])", core)
                and not re.search(r"(?:jobber|carer)", core))


def page_kind(url: str = "", title: str = "", content: str = "") -> str:
    """Classify a PAGE by its URL shape and visible text. Independent of the
    domain: a listing page on an unknown domain is still a listing page."""
    path = (urlparse(url or "").path or "").lower()
    text = f"{title or ''}\n{content or ''}"
    # A publication/newsletter title, or an article-headline title, marks content
    # ABOUT a market rather than a company operating in it → MEDIA (dropped). Judged
    # on the TITLE specifically so a body-copy mention can't misclassify a real site.
    t = (title or "").strip()
    if t and (_PUBLICATION_TITLE_RE.search(t) or _ARTICLE_TITLE_RE.search(t)):
        return MEDIA
    if any(part in path for part in _LISTING_PATHS):
        return LIST_SITE if any(p in path for p in ("/best-", "/top-", "/compare/",
                                                    "/alternatives")) else DIRECTORY
    if _LISTING_TEXT_RE.search(text):
        return DIRECTORY
    return COMPANY


def classify(url: str, title: str = "", content: str = "",
             domain: str = "") -> tuple:
    """``(kind, reason)`` for one search result. Domain identity wins over page
    shape, because a job board's home page is still a job board."""
    dom = domain or _registrable(url)
    by_domain = domain_kind(dom)
    if by_domain in INTERMEDIARY_KINDS:
        return by_domain, f"{dom} is a known {by_domain.replace('_', ' ')}"
    # A page that advertises SOMEONE ELSE'S job is a board even when its domain
    # carries no job token. workwithindies.com slipped past both lists this way:
    # "Optillusion Games is hiring a Video Content Creator" is not workwithindies
    # hiring — it is a listing, and workwithindies is the intermediary.
    if hosts_third_party_posting(title, dom):
        return JOB_BOARD, "advertises another company's job posting"
    # A firm that SUPPLIES staff is the same problem one step removed: it is not
    # hiring for itself, so a hiring-signal search is not evidence it will buy.
    # Apollo candidates are excluded by SIC/NAICS (intent.staffing_kind), but web
    # candidates carry no codes, which is how memoryblue.com — an outsourced-SDR
    # firm — ranked as a prospect for "founders hiring SDRs".
    if sells_staffing(title, content):
        return MARKETPLACE, "sells staffing or recruiting services"
    by_page = page_kind(url, title, content)
    if by_page in INTERMEDIARY_KINDS:
        return by_page, f"page looks like a {by_page.replace('_', ' ')} listing"
    return COMPANY, ""


# A firm describing its own STAFFING SERVICE. Deliberately requires a service
# noun ("recruiting agency/firm/services"), never a product noun, so that HR and
# recruiting SOFTWARE — a legitimate B2B prospect — is untouched: an ATS vendor
# calls itself a recruiting platform, never a recruiting agency.
_STAFFING_SERVICE_RE = re.compile(
    r"\b(?:staffing|recruiting|recruitment|placement|talent)\s+"
    r"(?:agency|agencies|firm|firms|partner|services)\b"
    r"|\bexecutive\s+search\b"
    r"|\boutsourced\s+(?:sdr|bdr|sales\s+development|recruiting)\b"
    r"|\b(?:sdr|bdr)s?\s+as\s+a\s+service\b"
    r"|\bsales\s+development\s+company\b"
    r"|\bwe\s+(?:place|recruit\s+and\s+train)\b",
    re.I)


def sells_staffing(title: str = "", content: str = "") -> bool:
    """True when a page's own words say it supplies people to other companies."""
    return bool(_STAFFING_SERVICE_RE.search(f"{title or ''}\n{content or ''}"))


# "<Company> is hiring …" — the title convention a job board uses to advertise
# another employer's role. A company's OWN careers page says "Careers at X" or
# "We're hiring", not "SomeOtherCo is hiring".
_THIRD_PARTY_HIRING_RE = re.compile(
    r"^\s*([a-z0-9][\w.&'’-]*(?:\s+[\w.&'’-]+){0,4}?)\s+is\s+(?:now\s+)?hiring\b", re.I)


def hosts_third_party_posting(title: str, candidate_domain: str) -> bool:
    """True when ``title`` advertises a job at a company OTHER than the domain.

    This is what tells a job board apart from an employer's own careers page when
    the board's domain name looks innocent: the employer named in "X is hiring …"
    is not the domain, so the domain is the intermediary and the page must not be
    credited as the domain's own hiring signal.
    """
    m = _THIRD_PARTY_HIRING_RE.match(title or "")
    if not m:
        return False
    named = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
    core = re.sub(r"[^a-z0-9]", "", (candidate_domain or "").split(".")[0].lower())
    if len(named) < 3 or not core:
        return False
    # A company writing about itself is fine, allowing for "get"/"try" domain
    # prefixes ("Acme is hiring" on getacme.io): treat as third-party only when
    # neither name contains the other.
    return named not in core and core not in named


def query_requests(kind: str, query_text: str) -> bool:
    """True when the user explicitly asked for this intermediary category, so it
    should be admitted as a normal result rather than demoted."""
    pattern = _REQUESTED_RE.get(kind)
    if not pattern or not query_text:
        return False
    return bool(re.search(pattern, query_text, re.I))


def is_hiring_evidence(url: str, candidate_domain: str) -> bool:
    """True when a URL is hiring evidence FOR ``candidate_domain`` rather than a
    third-party listing: the company's own careers page or careers subdomain.

    This is the fix for the scoring bug. A hiring signal now has to come from the
    company itself (``jobs.storykit.io``, ``storykit.io/careers``) or from a
    structured source like Apollo's job postings, never from the mere presence of
    the word "hiring" in a search snippet.
    """
    if not url or not candidate_domain:
        return False
    if _registrable(url) != candidate_domain.lower():
        return False
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    return (host.split(".")[0] in ("jobs", "careers", "hiring", "work")
            or any(path.startswith(p) for p in
                   ("/careers", "/jobs", "/join", "/hiring", "/work-with-us")))


def _registrable(url: str) -> str:
    host = (urlparse(url if "://" in (url or "") else "https://" + (url or "")).hostname
            or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host
