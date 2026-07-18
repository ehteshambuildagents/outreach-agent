"""Application configuration and secret loading.

The Anthropic API key lives ONLY in the .env file and is read here via
python-dotenv. It is never hard-coded and never logged.
"""

import os

from config.env import load_env

# Load .env.local + .env (project root) into the process environment via the
# canonical loader, so config here agrees with the server/worker/migrate/verifier.
load_env()

# ── Anthropic models ──────────────────────────────────────────────────
# Centralized model routing. FAST_MODEL is used for extraction, classification,
# synthesis, and simple structured reasoning. QUALITY_MODEL is reserved for
# cold-email writing where copy quality directly affects replies.
#
# Current Anthropic aliases are the defaults; set FAST_MODEL / QUALITY_MODEL in
# the environment to pin older snapshots or switch providers without code edits.
FAST_MODEL = os.getenv("FAST_MODEL", "claude-haiku-4-5")
QUALITY_MODEL = os.getenv("QUALITY_MODEL", "claude-sonnet-5")

# Backwards-compatible display/fallback name for older code paths.
CLAUDE_MODEL = QUALITY_MODEL


# ── Deployment environment ────────────────────────────────────────────
def is_production() -> bool:
    """True in a deployed production environment. Railway sets RAILWAY_ENVIRONMENT
    (its default environment is named "production"); ENVIRONMENT / APP_ENV are
    honored as generic fallbacks. Used to gate loud boot-time warnings that would
    only be noise in local development."""
    val = (os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("ENVIRONMENT")
           or os.getenv("APP_ENV") or "").strip().lower()
    return val in ("production", "prod")

# ── Networking / safety limits (applied to EVERY page we fetch) ────────
REQUEST_TIMEOUT_SECONDS = 10            # hard timeout per HTTP fetch
MAX_REDIRECTS = 3                       # redirects we will follow (re-validated)
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024    # cap each page download at 2 MB
HTTP_MAX_RETRIES = 2                    # retries for TRANSIENT fetch failures
HTTP_RETRY_BASE_SECONDS = 0.5          # exponential backoff base (+ jitter)

# ── JavaScript rendering (Playwright headless browser) ────────────────
# We try the FAST path (requests only) first and escalate to a browser ONLY
# when the fast result is poor (skip / sparse / a team page yielded no people).
RENDER_NAV_TIMEOUT_MS = 15000           # max time to navigate/render a page
RENDER_SETTLE_MS = 3500                 # best-effort 'networkidle' wait
JS_RENDER_TEXT_THRESHOLD = 600          # within the render pass: re-render a page
                                        # whose static text is thinner than this
RENDER_ESCALATE_MIN_CHARS = 1500        # fast-pass total text below this -> the
                                        # site is likely JS-heavy -> escalate
# Adaptive wait: poll the rendered text until it stops growing (deterministic),
# bounded so runtime stays controlled. One safe re-render if the page is thin.
RENDER_STABLE_POLL_MS = 350             # how often to sample rendered text length
RENDER_MAX_WAIT_MS = 6000               # cap on the adaptive wait after load
RENDER_STABLE_CHECKS = 2                # consecutive stable samples = "settled"
READ_DEADLINE_SECONDS = 25              # total wall-clock cap on one body read
                                        # (bounds slow-drip / slowloris reads)

# ── Anthropic API retry (production backoff; SDK auto-retry is disabled) ─
API_MAX_RETRIES = 3                     # retries AFTER the first attempt (bounded)
API_BACKOFF_BASE_SECONDS = 0.5          # exponential base
API_BACKOFF_MAX_SECONDS = 8.0           # per-attempt delay ceiling

# ── Multi-page crawl limits ───────────────────────────────────────────
MAX_EXTRA_PAGES = 18                    # candidate-pool cap (adaptive loop stops earlier)
MAX_PER_SECTION = 3                     # cap pages per top-level section (e.g. /blog/*)
SITEMAP_MAX_URLS = 40                   # cap URLs harvested from sitemap.xml
MAX_PAGE_TEXT_CHARS = 10000             # cap cleaned text kept PER page

# ── Adaptive, confidence-driven crawl (research only as much as NEEDED) ─
# We crawl the homepage, extract, score; then add the next highest-value page,
# re-score, and STOP as soon as we have enough — instead of always crawling all.
#
# "Enough" is defined by INFORMATION SUFFICIENCY, not by hitting an arbitrary
# score: we stop once we KNOW ENOUGH to write a specific, personalized email —
# what the company does, who they serve, how they position themselves, how they
# make money, and a couple of genuinely specific hooks to open with. (This is
# what a human researcher would gather before writing; a numeric target like
# "score >= 80" would keep crawling long past that point on some sites and never
# reach it on others.) The 0-100 research score is still computed and still
# gates the final ok/skip decision (RESEARCH_SCORE_THRESHOLD) and reporting.
SUFFICIENT_MIN_STRONG_HOOKS = 2        # "one or two unique observations" to open with
SUFFICIENT_HOOK_MIN_SCORE = 0.35       # a hook this strong counts as genuinely specific
DIMINISHING_DELTA = 4                   # a checkpoint adding < this many points = "no gain"
DIMINISHING_STALLS = 2                  # stop after this many consecutive no-gain checkpoints
# After the homepage (checked alone, so a confident site can stop after ONE
# page/ONE model call), remaining pages are fetched and extracted in small
# BATCHES rather than strictly one at a time. A model call has a real fixed
# latency floor regardless of how small its input is, so checking after every
# single page multiplies that floor by the page count; batching amortizes it
# while still stopping well short of the full candidate list. Pages within a
# batch are fetched in parallel (fast/requests path only).
PAGE_BATCH_SIZE = 2                     # extra pages fetched + extracted together
MAX_PARALLEL_FETCHES = 5                # thread cap for fetching one batch
# Per-site page budget (incl. homepage), chosen by how many candidate pages exist:
SITE_SMALL_MAX_CANDIDATES = 8           # <= this many candidates -> small site
SITE_MEDIUM_MAX_CANDIDATES = 20         # <= this many -> medium; else large
PAGE_BUDGET_SMALL = 6
PAGE_BUDGET_MEDIUM = 8
PAGE_BUDGET_LARGE = 12
MAX_TEXT_CHARS = 50000                  # cap COMBINED text sent to the model
                                        # (homepage + high-priority team/about
                                        #  pages come first, so if the cap is hit
                                        #  the lowest-value pages are trimmed)
MIN_USABLE_TEXT_CHARS = 50             # below this, the page(s) are too thin

# ── Founder/team name discovery ───────────────────────────────────────
# The general per-page extraction ALWAYS surfaces a founder/team that is plainly
# stated on a page. This is the EXTRA focused name-hunt (dedicated Claude calls
# that re-read people-pages looking for a name the general pass missed). It is a
# real latency cost — it fires on every company that has no visible founder — so
# it is now OPT-IN (research_company(url, find_founder=True)), off by default.
NAME_SEARCH_RETRIES = 3                 # focused re-passes when founder discovery is requested

# ── Evidence pipeline ─────────────────────────────────────────────────
QUOTE_MAX_CHARS = 320                   # cap each supporting quote we keep
EVIDENCE_MAX_TOKENS = 8000             # per-field evidence + quotes across ~20
                                        # fields; must be high enough that the
                                        # JSON is never truncated mid-object
RESEARCH_SCORE_THRESHOLD = 25          # below this 0-100 score -> honest SKIP

# ── Model response ────────────────────────────────────────────────────
REQUEST_MAX_TOKENS = 2500              # larger output (team list + many fields)

# ── Email writer agent ────────────────────────────────────────────────
# Cold emails are short, so the output budget is tight (also bounds cost).
WRITER_MAX_TOKENS = 600
# A human cold email is short with VARIED sentence length (a sub-8-word line next
# to a 20+-word one), which runs ~3-7 short sentences. Length is gated by words,
# not sentence count, so the punchy rhythm isn't flattened into 5 medium ones.
WRITER_MIN_SENTENCES = 3
WRITER_MAX_SENTENCES = 7
WRITER_MAX_WORDS = 110                  # maximum ~105 words (small slack)
WRITER_FIELD_CHAR_CAP = 600            # cap each research field fed into the prompt
                                       # (keeps tokens low + limits injection surface)
WRITER_SUBJECT_MAX_CHARS = 90          # guard against a runaway subject line
WRITER_BODY_MAX_CHARS = 1500           # guard against a runaway body
# Bounded corrective regenerations when the FIRST draft fails validation in a way
# that can't be repaired deterministically (a banned phrase or wrong length).
# The happy path is exactly ONE Claude call; this only fires on a real failure.
# Set to 0 to enforce a strict single-call-no-retry policy.
WRITER_MAX_REPAIRS = 1

# Self-critique refine pass (a SECOND, distinct model call — an editor persona,
# not the generator). It reconciles the codebase's "one call on the happy path"
# rule with the requirement for a critique pass: it fires ONLY when the free
# deterministic AI-voice scan still flags a draft after generation/repair, so a
# clean+specific email stays one call and only a machine-sounding one pays for the
# editor pass. Set to False for strict single-pass behaviour; set _ALWAYS to run
# the editor on every draft regardless of the scan (max quality, higher cost).
WRITER_SELF_CRITIQUE = os.getenv("WRITER_SELF_CRITIQUE", "1").strip().lower() not in (
    "0", "false", "no", "off")
WRITER_SELF_CRITIQUE_ALWAYS = os.getenv("WRITER_SELF_CRITIQUE_ALWAYS", "0").strip().lower() in (
    "1", "true", "yes", "on")

# What WE (the sender) offer, stated as an OUTCOME (never a mechanism). The
# writer must describe what it helps a founder accomplish, not how it works, and
# vary that description each email. Edit this one line to change the pitch.
SENDER_PRODUCT_PITCH = (
    "a tool that helps founders do cold outbound that gets replies, without the "
    "manual grind of personalizing every email by hand"
)

# ── Conversational workspace (chat layer over the agents) ─────────────
# The chat agent orchestrates the existing tools (research, email writer, and
# future capabilities) via Claude tool-use. It never re-implements their logic;
# it only decides which tool to call. These bound its cost + loop length.
CHAT_MAX_TOKENS = 1200                  # orchestration replies are short
CHAT_MAX_TOOL_HOPS = 6                  # max tool calls per user turn (bounded loop)
CHAT_HISTORY_MAX_TURNS = 24            # prior messages replayed as context per turn
CHAT_STORE_DIR = "conversations"       # where threads persist (git-ignored)

# ── Multi-source research providers (Firecrawl / Tavily / Exa / Jina) ──
# The orchestrator (research/orchestrator.py) is the ONLY caller of these; each
# provider lives in its own module (research/firecrawl.py, tavily.py, exa.py,
# jina.py), reads its key from the environment, and degrades gracefully when the
# key is absent or a request fails. Keys are server-side only and never logged.
#   FIRECRAWL_API_KEY  TAVILY_API_KEY  EXA_API_KEY  JINA_API_KEY
PROVIDER_TIMEOUT_SECONDS = 20          # per-request HTTP timeout for a provider
PROVIDER_MAX_RETRIES = 2               # retries AFTER the first attempt (transient only)
PROVIDER_BACKOFF_BASE_SECONDS = 0.5    # exponential backoff base (+ jitter)
PROVIDER_MAX_WORKERS = 5               # thread cap for concurrent provider calls

FIRECRAWL_MAP_LIMIT = 25               # max URLs to discover when mapping a site
FIRECRAWL_SCRAPE_PAGES = 4             # max pages to scrape from a site per run
                                       # (homepage + top-valued; scraped in parallel)
FIRECRAWL_PAGE_CHARS = 8000            # cap markdown kept per scraped page

TAVILY_MAX_RESULTS = 6                 # results per Tavily search
TAVILY_NEWS_DAYS = 120                 # "recent" window for the news topic (days)

EXA_MAX_RESULTS = 6                    # results per Exa search
EXA_TEXT_CHARS = 1200                  # cap text kept per Exa result

JINA_PAGE_CHARS = 8000                 # cap markdown kept per Jina-cleaned page

# Orchestrator synthesis (Anthropic reads all gathered evidence -> grounded,
# de-duplicated, ranked findings with citations).
INTEL_CACHE_TTL_SECONDS = 900          # reuse a company's gathered intel for 15 min
INTEL_SYNTHESIS_MAX_TOKENS = 3000      # output budget for the synthesis call
INTEL_MAX_EVIDENCE_CHARS = 60000       # cap combined evidence sent to the model
INTEL_MAX_FINDINGS = 12                # ranked findings the synthesis returns

# ── X (Twitter) recent search — OPTIONAL recent-social-signal source ───
# Read-only App-only Bearer auth against GET /2/tweets/search/recent
# (research/x_search.py). This is the ONE research source with NO free tier — it
# costs real money PER POST READ — so it is deliberately NOT on the always-on
# research path: it runs only when a request implies a need for recent social
# signal, results are cached by exact query, and max_results is capped well under
# the API's 100. Key: X_BEARER_TOKEN (server-side ONLY, never logged/client-side;
# regenerate in the X console if a token was ever exposed). No Consumer Key/Secret
# here — those are only for posting (a later phase), not read-only search.
X_SEARCH_MAX_RESULTS = int(os.getenv("X_SEARCH_MAX_RESULTS", "25"))     # << API max (100)
X_SEARCH_CACHE_TTL_SECONDS = int(os.getenv("X_SEARCH_CACHE_TTL_SECONDS", str(6 * 3600)))  # 6h
# Estimated USD per post read, for cost LOGGING only (never billing). Override to
# your plan's real per-read rate; the default is a conservative placeholder so
# spend shows up in the logs instead of only on the invoice.
X_SEARCH_COST_PER_READ_USD = float(os.getenv("X_SEARCH_COST_PER_READ_USD", "0.005"))


# ── Apollo People-Match enrichment — OPTIONAL verified-contact source ──
# research/apollo.py enriches a known contact (name + company domain, optionally a
# LinkedIn URL) into a verified professional email + exact title/seniority via
# Apollo's People Match endpoint. It costs Apollo credits PER match, so — like X
# search — it is deliberately OFF the always-on research path and metered through
# providers_common.request_json (provider "apollo": cost + daily-call caps above).
# Key: APOLLO_API_KEY (server-side ONLY, never logged; rotate in Apollo's console
# if a key was ever exposed). APOLLO_ENRICH_ENABLED gates whether a caller may run
# it at all — default OFF so it never fires (or spends) until deliberately enabled.
APOLLO_ENRICH_ENABLED = (os.getenv("APOLLO_ENRICH_ENABLED", "0").strip().lower()
                         in ("1", "true", "yes", "on"))


# ── Source planner — paid recency escalation (news + X) kill switch ────
# The post-crawl planner (research/source_planner.py) may escalate a prospect with
# no recent on-site signal to Tavily news and then X recent-search. Both cost money
# / carry rate limits, so the escalation is a KILL SWITCH: OFF by default until it
# has been watched on real campaigns. This controls ONLY the news + X steps; Apollo
# enrichment has its own gate (APOLLO_ENRICH_ENABLED). With this OFF the planner
# still runs, but the paid escalation is skipped (recorded as a skip, with reason).
PLANNER_ESCALATION_ENABLED = (os.getenv("PLANNER_ESCALATION_ENABLED", "0")
                              .strip().lower() in ("1", "true", "yes", "on"))


# ── Automation Agent (the conductor: scheduling, sending, recovery) ────
# All tunable; nothing about timing is hard-coded in the engine.
AUTOMATION_MAX_RETRIES = 4              # send retries before a step is FAILED-terminal
AUTOMATION_BACKOFF_BASE_SECONDS = 30    # exponential backoff base for retries
AUTOMATION_BACKOFF_MAX_SECONDS = 3600   # cap on a single retry delay
AUTOMATION_SEND_RATE_PER_MIN = 20       # per-user send rate limit (Redis fixed window)
AUTOMATION_REPLY_WAIT_DAYS = 3          # default wait between steps; also the follow-up spacing
AUTOMATION_MAX_FOLLOWUPS = 4            # follow-ups after the initial email (5 total touches)
AUTOMATION_LOCK_TTL_SECONDS = 60        # per-workflow lock while a tick processes it
AUTOMATION_TICK_BATCH = 50              # max workflows advanced per tick

# Background worker cadence (a real deployment runs one worker process).
AUTOMATION_WORKER_TICK_SECONDS = 15     # how often the worker advances due workflows
AUTOMATION_WORKER_MAINT_SECONDS = 3600  # maintenance sweep: token refresh + watch renewal
AUTOMATION_OAUTH_STATE_TTL = 600        # CSRF state lifetime during an OAuth round-trip
AUTOMATION_TOKEN_REFRESH_SKEW = 120     # refresh an access token this many s before expiry
AUTOMATION_WATCH_RENEW_BEFORE = 86400   # renew a Gmail/Graph watch within a day of expiry

# ── Deliverability & Cost Guard — AI budgets ───────────────────────────
# The Cost Guard compares LIVE telemetry spend (telemetry.query) against these
# budgets to warn (>50/80/95%) and BLOCK (>=100%). Global defaults for the MVP
# (there is no per-user budget store yet); overridable per environment. Set to 0
# to disable the budget check entirely (the guard then simply omits it — never a
# false block). Env vars GUARD_DAILY_BUDGET_USD / GUARD_MONTHLY_BUDGET_USD win.
GUARD_DAILY_BUDGET_USD = 10.0
GUARD_MONTHLY_BUDGET_USD = 200.0


# ── Free tier (self-serve entitlement) ─────────────────────────────────
# With no billing backend yet, every account is on the Free plan: it may WORK a
# small number of distinct prospects (research → write → send), after which the
# chat prompts an upgrade. Metered per-user (chat/store `_usage.json`), counted
# on research (the gateway to a prospect). Set FREE_PROSPECT_LIMIT=0 to disable
# the cap (e.g. local dev). The paid plans on /pricing are Starter (50) / Growth
# (150) / Enterprise — wire a real plan lookup here when billing exists.
FREE_PROSPECT_LIMIT = int(os.getenv("FREE_PROSPECT_LIMIT", "3"))


# ── Per-user usage caps & account kill switch (public-signup safety) ────
# With public signups there's no informal "I know how much I've used" bound, so
# every paid provider call is metered per user against these hard caps (limits/).
# The cap is enforced at two choke points — research/providers_common.request_json
# (Firecrawl/Tavily/Exa/Jina/Hunter/X) and services/claude_client (Anthropic) —
# using the ambient telemetry user_id, so no signatures change. A cap only DENIES
# the next paid call (degrading gracefully); it never crashes a request, and a
# call with no user context (system/internal) is never capped. Set LIMITS_ENFORCED
# to 0 to disable enforcement (metering still records).
LIMITS_ENFORCED = (os.getenv("LIMITS_ENFORCED", "1").strip().lower()
                   not in ("0", "false", "no", ""))
# Hard per-user spend ceilings (USD) across ALL paid providers combined. 0 disables.
LIMIT_DAILY_USD_PER_USER = float(os.getenv("LIMIT_DAILY_USD_PER_USER", "5.0"))
LIMIT_MONTHLY_USD_PER_USER = float(os.getenv("LIMIT_MONTHLY_USD_PER_USER", "50.0"))
# Per-provider daily CALL caps (deterministic, no pricing needed). Unknown
# providers fall back to LIMIT_DEFAULT_DAILY_CALLS.
LIMIT_DEFAULT_DAILY_CALLS = int(os.getenv("LIMIT_DEFAULT_DAILY_CALLS", "300"))
LIMIT_DAILY_CALLS = {
    "firecrawl": int(os.getenv("LIMIT_FIRECRAWL_DAILY_CALLS", "150")),
    "tavily":    int(os.getenv("LIMIT_TAVILY_DAILY_CALLS", "300")),
    "exa":       int(os.getenv("LIMIT_EXA_DAILY_CALLS", "300")),
    "jina":      int(os.getenv("LIMIT_JINA_DAILY_CALLS", "300")),
    "hunter":    int(os.getenv("LIMIT_HUNTER_DAILY_CALLS", "100")),
    "apollo":    int(os.getenv("LIMIT_APOLLO_DAILY_CALLS", "100")),
    "x_search":  int(os.getenv("LIMIT_XSEARCH_DAILY_CALLS", "60")),
    "anthropic": int(os.getenv("LIMIT_ANTHROPIC_DAILY_CALLS", "600")),
}
# Rough per-call USD estimate used ONLY to accumulate the per-user spend ledger
# for the USD caps above — never billing. Unknown providers use the default.
LIMIT_DEFAULT_COST_USD = float(os.getenv("LIMIT_DEFAULT_COST_USD", "0.01"))
LIMIT_PROVIDER_COST_USD = {
    "firecrawl": 0.012,
    "tavily":    0.008,
    "exa":       0.005,
    "jina":      0.0,
    "hunter":    0.010,
    "apollo":    0.010,
    "x_search":  round(X_SEARCH_COST_PER_READ_USD * X_SEARCH_MAX_RESULTS, 5),
    "anthropic": 0.020,
}
# Account kill switch (anomaly detector — the hard caps above are the primary cost
# ceiling; this catches abnormal SHAPE). A user is paused + flagged for review when
# their last-hour call volume is both above the noise floor AND abnormal:
#   * with peers on record: >= LIMIT_SPIKE_MULTIPLE x the median hourly volume;
#   * cold start (no peer baseline): >= LIMIT_SPIKE_ABS_CEILING (so the first
#     legitimate heavy user isn't paused, but a runaway bot still is).
LIMIT_SPIKE_MULTIPLE = float(os.getenv("LIMIT_SPIKE_MULTIPLE", "10"))
LIMIT_SPIKE_MIN_CALLS = int(os.getenv("LIMIT_SPIKE_MIN_CALLS", "40"))   # noise floor
LIMIT_SPIKE_ABS_CEILING = int(os.getenv("LIMIT_SPIKE_ABS_CEILING", "300"))  # cold-start hard stop


# ── Company resolution (name -> official website, via a web-search API) ─
# A company NAME is resolved to its real site with a search API BEFORE research
# (preferred over guessing a domain). Provider is auto-selected by which key is
# present in the environment: TAVILY_API_KEY (preferred) or BRAVE_API_KEY. With
# neither set, the resolver degrades to a best-effort guess and asks the user to
# confirm. This lives entirely in the chat layer; the research engine is untouched.
COMPANY_SEARCH_MAX_RESULTS = 6         # candidates pulled from the search API
COMPANY_SEARCH_TIMEOUT = 8             # per-request HTTP timeout (seconds)
# Domains that are never a company's OWN official site (social, press,
# aggregators, app stores) — excluded when picking the official domain.
EXCLUDED_RESOLUTION_DOMAINS = (
    "wikipedia.org", "linkedin.com", "crunchbase.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com", "medium.com",
    "github.com", "reddit.com", "g2.com", "capterra.com", "glassdoor.com",
    "indeed.com", "bloomberg.com", "forbes.com", "producthunt.com",
    "trustpilot.com", "apps.apple.com", "play.google.com", "apple.com",
    "pitchbook.com", "owler.com", "zoominfo.com", "yelp.com", "quora.com",
)

# ── Prospect Discovery Agent (find companies matching an ICP) ──────────
# Reuses the existing search providers (Tavily/Exa); deterministic, no new APIs.
DISCOVERY_DEFAULT_LIMIT = 20           # companies returned per page by default
DISCOVERY_MAX_LIMIT = 50               # hard cap per request

# ── Chat-directed research (find/evaluate prospects straight from chat) ─
# One natural-language ask -> discovery -> research -> qualification, returning a
# scored, browsable list. Researching each company is a real crawl + model call,
# so a single chat run is bounded to keep latency/cost sane (the user can ask for
# more). This is a NEW ENTRY POINT into the existing agents, not new agent logic.
RESEARCH_LIST_MAX = 10                 # max companies researched+qualified per run
DISCOVERY_PROVIDER_POOL = 30           # results pulled from EACH provider per query
DISCOVERY_MIN_CONFIDENCE = 0.15        # drop candidates below this match confidence

# A realistic desktop-browser User-Agent so public pages serve normal HTML.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def get_api_key() -> str:
    """Return the Anthropic API key, or raise a clear, actionable error.

    The key is read from the environment (populated from .env by
    python-dotenv). If it is missing we raise with exact instructions so
    the user knows precisely what to do — without ever printing the key
    itself.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Open the .env file in the project root and add your key:\n\n"
            "    ANTHROPIC_API_KEY=<your-anthropic-api-key>\n\n"
            "Create a key at https://console.anthropic.com/settings/keys "
            "then run the command again."
        )
    return key.strip()
