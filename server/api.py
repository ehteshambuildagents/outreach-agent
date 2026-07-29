"""Saqua API — a thin FastAPI layer over the existing backend.

    web/ (browser)  ->  FastAPI (this file)  ->  chat.agent / chat.store  ->  research + writer

This module ONLY does transport + serialization. It imports and calls the
existing modules; it never re-implements or modifies them. It also serves the
static `web/` frontend so the whole product runs from one origin.

Security posture:
  * Secrets (API keys) are read by the backend from the environment — this layer
    never reads, returns, or logs them.
  * Requests are validated (Pydantic + length caps) and rate-limited per IP.
  * Only a CURATED, safe view of each conversation leaves the server — never the
    raw research internals, prompts, model reasoning, or stack traces.
  * Any unhandled error becomes a generic, user-friendly message; details are
    logged server-side only.
  * OpenAPI/docs are disabled so the schema isn't exposed.
"""

import json
import logging
import os
import re
import time
import traceback
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote, urlparse

# Load secrets before anything reads them (.env.local + .env) via the canonical
# loader, so the server agrees with the worker/migrate/verifier on configuration.
# Neither file's contents ever reach the client.
from config.env import load_env  # noqa: E402
_ROOT = Path(__file__).resolve().parent.parent
load_env()

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from chat.agent import respond, respond_stream  # noqa: E402
from chat.models import Conversation, new_id  # noqa: E402
from chat.store import ConversationStore  # noqa: E402
from server.auth import auth_enabled, publishable_key, require_user  # noqa: E402
from server import demo_session  # noqa: E402  - sandboxed anonymous demo principals
from server import demo_auth  # noqa: E402  - demo-aware auth dependencies (shared)
import access  # noqa: E402  - soft-launch request-access gating
import limits  # noqa: E402  - per-user account pause (kill switch)
from server import error_log  # noqa: E402  - durable capture of unhandled errors

log = logging.getLogger("saqua.api")


def _configure_app_logging() -> None:
    level = getattr(logging, os.environ.get("SAQUA_LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    # Every top-level logger namespace the app uses. Anything omitted here has no
    # handler in its ancestry (root is left unconfigured), so its INFO/DEBUG records
    # hit Python's last-resort handler (WARNING+ only) and vanish. That is exactly
    # what silently swallowed the `chat.agent` tool-selection lines and the
    # `research.x_search` cost lines — they live under `chat`/`research`, not `saqua`.
    for name in ("saqua", "discovery", "chat", "research", "guard", "automation"):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)


_configure_app_logging()

# The backend is designed to never raise for normal failures; this layer adds a
# safety net so a user never sees a trace regardless.
app = FastAPI(title="Saqua", docs_url=None, redoc_url=None, openapi_url=None)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_MESSAGE_LEN = 2000
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Per-user conversation isolation: every user's threads live in their own
# subdirectory keyed by the verified Clerk user id, so no user can list, open,
# or delete another user's conversations. This reuses ConversationStore as-is
# (no storage-layer changes) — it just scopes the directory per caller.
_STORE_BASE = str(Path(__file__).resolve().parent.parent / "conversations")


@app.on_event("startup")
def _log_oauth_configuration() -> None:
    from automation import push  # deployed marker below: proves which push.py is live
    log.info(
        "oauth_config provider=gmail client_id_present=%s redirect_uri_present=%s",
        bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip()),
        bool(os.environ.get("GOOGLE_REDIRECT_URI", "").strip()),
    )
    # Boot-time build fingerprint on the visible saqua.api logger. If the deploy is
    # serving a stale automation/push.py (build-layer or .pyc cache), the marker here
    # won't match push.py's current BUILD_MARKER (or falls back to the STALE string) —
    # a stale build is then obvious at boot, without sending a test push.
    log.info("saqua boot: commit=%s push.BUILD_MARKER=%s",
             os.environ.get("RAILWAY_GIT_COMMIT_SHA", "?"),
             getattr(push, "BUILD_MARKER", "<<STALE: push.py has no BUILD_MARKER>>"))


@app.on_event("startup")
def _log_redis_configuration() -> None:
    """Make a silent in-memory fallback impossible to miss. When Upstash is not
    configured, the send rate limiter, per-workflow lock, and reply dedup all run on
    per-process in-memory state — fine locally, but unsafe across a multi-instance
    deployment (limits under-enforced, locks/dedup not shared → possible double
    sends). In production we WARN loudly; we never refuse to boot."""
    from automation import redis
    from config import settings
    if redis.configured():
        log.info("redis: Upstash configured (coordination shared across instances)")
    elif settings.is_production():
        log.warning(
            "REDIS NOT CONFIGURED IN PRODUCTION — using per-process in-memory fallback. "
            "Send rate limiting, workflow locking, and reply dedup are NOT shared across "
            "instances and will misbehave in any multi-instance deployment. "
            "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN on every service.")
    else:
        log.info("redis: in-memory fallback (local/dev); set UPSTASH_* for shared Redis")


@app.on_event("startup")
def _log_discovery_providers() -> None:
    """Make a missing provider key impossible to miss at boot.

    A provider with no key does not error, it just contributes nothing: discovery
    quietly falls back to whatever is left. Production ran on web search alone for
    a full deploy cycle because APOLLO_API_KEY was never set on the backend
    service, and the only symptom was slightly worse results. Apollo is the
    PRIMARY company source (verified job postings, recruiter filtering), so losing
    it is a quality regression, not a degraded extra.

    Booleans only: no key, prefix or length is ever logged.
    """
    from config import settings
    from research.providers_common import provider_status, provider_status_line
    status = provider_status()
    log.info("discovery providers: %s", provider_status_line())
    missing = [name for name in ("apollo", "tavily", "exa") if not status.get(name)]
    if missing and settings.is_production():
        log.warning(
            "DISCOVERY PROVIDER(S) NOT CONFIGURED IN PRODUCTION: %s. Discovery will "
            "silently run without them (Apollo missing means no verified job "
            "postings and no recruiter filtering). Set the matching *_API_KEY on the "
            "BACKEND service, not the frontend.", ", ".join(missing))


@app.on_event("startup")
def _log_proxy_secret_configuration() -> None:
    """The frontend proxy proves its origin with SAQUA_PROXY_SECRET; without it the
    backend cannot tell our own proxy from any other caller, so it buckets proxied
    traffic on the proxy's address and every visitor shares one rate limit. That is
    safe (over-restrictive, never permissive) but it silently disables per-visitor
    limiting, which is exactly the failure that went unnoticed before — so say so."""
    from server import waitlist_api
    from config import settings
    if waitlist_api.proxy_secret():
        log.info("proxy: SAQUA_PROXY_SECRET set (per-visitor rate limiting active)")
    elif settings.is_production():
        log.warning(
            "SAQUA_PROXY_SECRET NOT SET IN PRODUCTION — requests arriving through the "
            "frontend proxy cannot be attributed to a visitor, so they all share one "
            "rate-limit bucket keyed on the proxy. Set the SAME value on the frontend "
            "(Vercel) and this service.")
    else:
        log.info("proxy: SAQUA_PROXY_SECRET unset (local/dev)")


def _store_for(user_id: str) -> ConversationStore:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", user_id or "") or "anonymous"
    return ConversationStore(directory=str(Path(_STORE_BASE) / safe))


# ── Security headers (applied to every response) ───────────────────────
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")            # anti-clickjacking
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    return resp


# ── Rate limiting (in-memory, per IP + bucket) ─────────────────────────
_BUCKETS: dict = defaultdict(deque)


def _rate_limit(request: Request, bucket: str, limit: int, window: float) -> None:
    ip = request.client.host if request.client else "?"
    now = time.time()
    dq = _BUCKETS[(ip, bucket)]
    while dq and dq[0] <= now - window:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(status_code=429,
                            detail="You're going a little fast. Please wait a moment.")
    dq.append(now)


def _rl_read(request: Request):    _rate_limit(request, "read", 120, 60)
def _rl_write(request: Request):   _rate_limit(request, "write", 30, 60)
def _rl_agent(request: Request):   _rate_limit(request, "agent", 20, 60)


# ── Request models (validation + sanitization) ─────────────────────────
class SendMessage(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = (v or "").replace("\x00", "").strip()
        if not v:
            raise ValueError("Message is empty.")
        if len(v) > MAX_MESSAGE_LEN:
            raise ValueError("Message is too long.")
        return v


class RenameConversation(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join((v or "").replace("\x00", "").split()).strip()
        if not v:
            raise ValueError("Title is empty.")
        return v[:80]


_COMPANY_FIELD_MAX = 400


class CompanyProfile(BaseModel):
    """The sender's own company details (set in Settings, remembered in every
    chat). Every field is optional and sanitized to a single capped line."""
    name: str = ""
    website: str = ""
    one_liner: str = ""
    audience: str = ""
    value_prop: str = ""
    tone: str = ""

    @field_validator("*")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join((v or "").replace("\x00", "").split()).strip()
        return v[:_COMPANY_FIELD_MAX]


def _valid_id(cid: str) -> str:
    if not ID_RE.match(cid or ""):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return cid


# ── Curated, safe serialization (what leaves the server) ───────────────
def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _source_mark(domain: str) -> str:
    if "linkedin" in domain:
        return "in"
    if "crunchbase" in domain:
        return "cb"
    core = domain.split(".")[0] if domain else "?"
    return (core[:2] or "?").upper()


def _message_public(m) -> dict:
    """Only the fields the UI renders. Message.data is already product output
    (email subject/body, research summary) — no prompts or reasoning."""
    data = m.data if isinstance(m.data, dict) else None
    safe = None
    if data:
        if m.kind == "email":
            safe = {k: data.get(k) for k in
                    ("subject", "body", "to", "company", "label")}
        elif m.kind == "research":
            safe = {k: data.get(k) for k in
                    ("company", "what_they_do", "research_score", "pages_crawled",
                     "hooks", "stop_reason")}
        elif m.kind == "prospects":
            # A scored, browsable list: each prospect carries a collapsed preview
            # and an expandable detail trail (already curated product output).
            safe = {"summary": data.get("summary"),
                    "prospects": [_prospect_public(p)
                                  for p in (data.get("prospects") or [])]}
        elif m.kind == "channel":
            safe = {k: data.get(k) for k in
                    ("channel", "label", "body", "char_count", "company",
                     "posted", "guard")}
        elif m.kind == "stats":
            # The user's own outreach analytics (co-founder). Numbers only.
            safe = {k: data.get(k) for k in
                    ("emails_sent", "replies", "reply_rate", "sequences_active",
                     "sequences_paused", "prospects_contacted", "campaigns")}
        elif m.kind == "replies":
            safe = {"count": data.get("count"),
                    "replies": [{k: r.get(k) for k in
                                 ("company", "to", "replied_at", "emails_before_reply")}
                                for r in (data.get("replies") or [])]}
        elif m.kind == "campaigns":
            safe = {"count": data.get("count"),
                    "campaigns": [{k: c.get(k) for k in
                                   ("id", "name", "status", "launched", "discovered",
                                    "updated_at")}
                                  for c in (data.get("campaigns") or [])]}
    return {"role": m.role, "kind": m.kind, "content": m.content or "", "data": safe}


# The detail fields the browse card renders. TWO shapes share this card: a
# RESEARCHED prospect (findings, confidence) and a DISCOVERED lead (hiring
# evidence, why it matched, scale). The discovery half was missing here, so a
# streamed card rendered its hiring dates, "why it matched" list and caveats, and
# then lost all of them the moment the conversation was reloaded from the store.
# Every key below is already curated public output (discovery.models.Prospect
# .public() / research_pipeline.discovery_entries) — no prompts, no internals.
_PROSPECT_DETAIL_FIELDS = (
    # researched
    "what_they_do", "research_confidence", "findings", "missing_information",
    "disqualifiers", "why_discovered",
    # shared
    "sources", "score_breakdown", "strongest_signals",
    # discovered
    "match_reasons", "hiring", "growth", "recent_activity", "kind", "tier",
    "is_public", "annual_revenue", "industry_kind",
)


def _prospect_public(p: dict) -> dict:
    """One prospect for the browse card — the fields the UI renders (preview shown
    collapsed; detail revealed on expand). No prompts/internals."""
    p = p if isinstance(p, dict) else {}
    detail = p.get("detail") if isinstance(p.get("detail"), dict) else {}
    return {
        "company": p.get("company"),
        "website": p.get("website"),
        "status": p.get("status"),
        "score": p.get("score"),
        "fit_level": p.get("fit_level"),
        "priority": p.get("priority"),
        "recommendation": p.get("recommendation"),
        "recommended": p.get("recommended"),
        "score_reason": p.get("score_reason"),
        "preview": p.get("preview"),
        "actions": p.get("actions") or [],
        "detail": {k: detail.get(k) for k in _PROSPECT_DETAIL_FIELDS},
    }


def _panel_public(workspace: dict) -> dict:
    """The right-panel view — company summary, confidence, evidence, sources,
    contact — curated from the research result. Never the raw research object."""
    research = (workspace or {}).get("research")
    if not research or research.get("status") != "ok":
        return {"has_research": False}
    data = research.get("data") or {}

    evidence = [h.get("text") for h in (research.get("hooks") or []) if h.get("text")][:4]
    pages = research.get("pages_crawled") or []
    seen, sources = set(), []
    for u in pages:
        d = _domain(u)
        if d and d not in seen:
            seen.add(d)
            sources.append({"mark": _source_mark(d), "domain": d, "url": u})

    contact = None
    name = data.get("primary_contact_name") or data.get("founder_name")
    if name:
        initials = "".join(w[0] for w in str(name).split()[:2]).upper() or "•"
        company = data.get("company_name") or ""
        contact = {
            "name": name,
            "role": data.get("primary_contact_role") or data.get("founder_role") or "",
            "initials": initials,
            "linkedin": "https://www.linkedin.com/search/results/people/?keywords="
                        + quote(f"{name} {company}".strip()),
        }
    return {
        "has_research": True,
        "company": data.get("company_name"),
        "summary": data.get("what_they_do"),
        "confidence": research.get("research_score"),
        "evidence": evidence,
        "sources": sources[:6],
        "source_count": len(sources),
        "contact": contact,
    }


def _conversation_public(conv: Conversation) -> dict:
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [_message_public(m) for m in conv.messages],
        "panel": _panel_public(conv.workspace),
        # The TRUE active research target (workspace.company), so the UI can show a
        # persistent "Researching: X" tied to real state, not guessed from messages.
        "active_company": (conv.workspace or {}).get("company") or None,
    }


# ── Errors: friendly only ──────────────────────────────────────────────
@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    # Durably capture it + alert (best-effort) so a real 500 is noticed same-day,
    # not discovered later from a confused user — or never.
    try:
        error_log.record_error(
            path=str(request.url.path), method=request.method, status=500,
            error_type=type(exc).__name__, message=str(exc),
            tb="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:  # noqa: BLE001 - capture must never mask the response
        pass
    return JSONResponse(status_code=500,
                        content={"error": "Something went wrong. Please try again."})


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    # Same {"error": "..."} contract as every other response — never the raw
    # Pydantic error list (field paths, types, ctx internals).
    first = exc.errors()[0] if exc.errors() else {}
    msg = str(first.get("msg") or "Invalid request.").removeprefix("Value error, ")
    return JSONResponse(status_code=422, content={"error": msg})


# ── Access control: verified + approved + not-paused ───────────────────
_ACCESS_MESSAGE = {
    "pending": ("Your access request is pending approval. We're rolling out access "
                "gradually, and you'll be able to sign in as soon as you're approved."),
    "denied": "This account doesn't have access.",
}


def require_approved_user(request: Request, user: str = Depends(require_user)) -> str:
    """Full product gate: verify the Clerk session, enforce soft-launch access
    approval, and refuse a paused (kill-switched) account. Layered on top of
    require_user (via Depends, so dependency overrides still apply) — identity
    verification is unchanged."""
    allowed, status = access.check_access(user)  # records first-seen users as pending
    if not allowed:
        raise HTTPException(status_code=403,
                            detail=_ACCESS_MESSAGE.get(status, _ACCESS_MESSAGE["pending"]))
    if limits.is_paused(user):
        raise HTTPException(
            status_code=403,
            detail="Your account is paused for review after unusual activity. "
                   "Please contact support.")
    return user


def require_member_or_demo(request: Request) -> str:
    """Authorize an APPROVED member OR a sandboxed demo visitor on a SHARED route.

    A Bearer token means a real member: the full member gate runs unchanged
    (identity + access approval + pause), so real users are entirely unaffected.
    With no bearer, a valid demo session yields its ``demo_*`` principal — which
    every per-user store scopes on automatically, so the real pages render over
    demo-only data that can never address another principal's. The demo path is
    still subject to the pause kill switch, but never the access store.

    Plain function (not ``Depends``-based) so the member branch can delegate to
    ``require_approved_user`` with a directly-resolved ``require_user`` — the
    dependency-override tests still work via ``require_user``. The demo branch is
    shared with the identity-only routes through ``demo_auth``.
    """
    if demo_auth.has_bearer(request):
        return require_approved_user(request, require_user(request))
    return demo_auth.demo_or_401(request)


# ── Routes ─────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    """Liveness plus the two facts that decide whether the public waitlist endpoint
    is safe to serve: whether coordination is shared across instances, and whether
    this process considers itself production. Exposed unauthenticated on purpose so
    a deploy can be verified from outside; it reveals no secrets and no counts."""
    from automation import redis
    from config import settings
    return {
        "ok": True,
        "redis": "upstash" if redis.configured() else "in-memory",
        "production": settings.is_production(),
    }


@app.get("/api/public-config")
def public_config():
    """Public, client-safe config. Exposes ONLY the Clerk PUBLISHABLE key — the
    secret key is never returned here or anywhere else."""
    return {"clerkPublishableKey": publishable_key(), "authEnabled": auth_enabled()}


# ── Protected endpoints ────────────────────────────────────────────────
# Every conversation route requires a valid Clerk session JWT (require_user) and
# operates ONLY on the caller's own per-user store (_store_for(user)).
@app.get("/api/conversations")
def list_conversations(request: Request, _=Depends(_rl_read),
                       user: str = Depends(require_member_or_demo)):
    out = []
    for s in _store_for(user).list_summaries():
        out.append({"id": s.get("id"), "title": s.get("title") or "New chat",
                    "updated_at": s.get("updated_at")})
    return {"conversations": out}


@app.post("/api/conversations")
def create_conversation(request: Request, _=Depends(_rl_write),
                        user: str = Depends(require_member_or_demo)):
    conv = Conversation()
    _store_for(user).save(conv)
    return _conversation_public(conv)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str, request: Request, _=Depends(_rl_read),
                     user: str = Depends(require_member_or_demo)):
    conv = _store_for(user).load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _conversation_public(conv)


@app.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, body: RenameConversation, request: Request,
                        _=Depends(_rl_write), user: str = Depends(require_member_or_demo)):
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conv.title = body.title
    store.save(conv)
    return _conversation_public(conv)


@app.post("/api/conversations/{cid}/duplicate")
def duplicate_conversation(cid: str, request: Request, _=Depends(_rl_write),
                           user: str = Depends(require_member_or_demo)):
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    dup = Conversation.from_dict(conv.to_dict())     # copy messages + workspace
    dup.id = new_id()
    dup.created_at = dup.updated_at = time.time()
    dup.title = ((conv.title or "New chat") + " (copy)")[:80]
    store.save(dup)
    return _conversation_public(dup)


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str, request: Request, _=Depends(_rl_write),
                        user: str = Depends(require_member_or_demo)):
    _store_for(user).delete(_valid_id(cid))
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: str, body: SendMessage, request: Request,
                 _=Depends(_rl_agent), user: str = Depends(require_member_or_demo)):
    """Run one agent turn. Defined as `def` so the (possibly slow) blocking call
    executes in FastAPI's threadpool and never blocks the event loop."""
    # Demo principals spend real API money per turn, so each turn passes the same
    # global budget ceiling as a pipeline run plus a per-session turn cap.
    if demo_session.is_demo_id(user):
        allowed, message = demo_api.reserve_demo_turn(user)
        if not allowed:
            raise HTTPException(status_code=429, detail=message)
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    try:
        respond(conv, body.text, store, user_id=user)  # owner-scoped (for send_email)
    except Exception as exc:                     # noqa: BLE001 - never leak internals
        log.exception("agent turn failed for %s", cid)
        error_log.record_error(
            path=str(request.url.path), method=request.method, status=502,
            user_id=user, error_type=type(exc).__name__, message=str(exc),
            tb="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise HTTPException(status_code=502,
                            detail="The assistant couldn't respond just now. Please try again.")
    return _conversation_public(conv)


_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",          # tell any nginx/Vercel edge not to buffer
    "Connection": "keep-alive",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.post("/api/conversations/{cid}/messages/stream")
def send_message_stream(cid: str, body: SendMessage, request: Request,
                        _=Depends(_rl_agent),
                        user: str = Depends(require_member_or_demo)):
    """Run one agent turn and STREAM it as Server-Sent Events, so the real pipeline
    stages, each card, and the reply surface live instead of after a blocking wait.

    Same auth, rate limit and demo-budget gate as the blocking sibling above; the
    turn itself is identical (both share chat.agent._run_turn). Event frames:
    ``step`` (a real stage), ``message`` (a transcript message), ``error``, ``done``.
    """
    if demo_session.is_demo_id(user):
        allowed, message = demo_api.reserve_demo_turn(user)
        if not allowed:
            raise HTTPException(status_code=429, detail=message)
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    def stream():
        # The whole conversation, once, so a reconnecting client can reconcile the
        # optimistic user bubble with the persisted transcript.
        yield _sse("open", {"conversation_id": conv.id})
        try:
            for event, data in respond_stream(conv, body.text, store, user_id=user):
                yield _sse(event, data)
        except Exception as exc:  # noqa: BLE001 - a crash becomes a clean terminal event
            log.exception("streaming agent turn failed for %s", cid)
            error_log.record_error(
                path=str(request.url.path), method=request.method, status=502,
                user_id=user, error_type=type(exc).__name__, message=str(exc),
                tb="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            yield _sse("error", {"message": "The assistant couldn't respond just now. "
                                 "Please try again."})
            yield _sse("done", {})
        # Title may have been auto-generated this turn; hand the client the final
        # canonical state so the sidebar and transcript are exactly what's stored.
        yield _sse("final", _conversation_public(conv))

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


# ── Company profile (the sender's own details, remembered in every chat) ─
@app.get("/api/company")
def get_company(request: Request, _=Depends(_rl_read),
                user: str = Depends(require_member_or_demo)):
    return {"company": _store_for(user).load_company()}


@app.put("/api/company")
def put_company(body: CompanyProfile, request: Request, _=Depends(_rl_write),
                user: str = Depends(require_member_or_demo)):
    data = body.model_dump()
    _store_for(user).save_company(data)
    return {"company": data}


# ── Billing / plan ─────────────────────────────────────────────────────
# GET /api/billing (plan + usage), checkout, portal, and the Lemon Squeezy webhook
# now live in server.billing_api, registered below with the shared limiters and the
# already-defined auth + per-user-store helpers injected (so it never imports back
# into this module). The plan it reports is the user's REAL Lemon Squeezy plan, and
# the same limit is what the chat gate enforces (billing.limit_for_user).


# ── Automation Agent routes (Clerk-gated, per-user) ────────────────────
from server import admin_api, automation_api, billing_api, campaign_api, oauth_api  # noqa: E402
admin_api.register(app)                          # internal ops views (X-Admin-Token)
automation_api.register(app, rl_read=_rl_read, rl_write=_rl_write)
oauth_api.register(app, rl_read=_rl_read, rl_write=_rl_write)
campaign_api.register(app, rl_read=_rl_read, rl_write=_rl_write)
billing_api.register(app, rl_read=_rl_read, rl_write=_rl_write, store_for=_store_for,
                     member=require_approved_user, member_or_demo=require_member_or_demo)

# Public (unauthenticated) waitlist. Deliberately NOT given _rl_read/_rl_write:
# those count per-process and key on the proxy's IP, which is not good enough for
# an anonymous write endpoint. It brings its own Redis-backed, fail-closed limiter.
from server import waitlist_api  # noqa: E402
waitlist_api.register(app)

# Public (unauthenticated) contact form. Sends a message to the support inbox
# rather than opening the visitor's mail client. Reuses the waitlist limiter and
# shared-Redis guard (see server/contact_api.py).
from server import contact_api  # noqa: E402
contact_api.register(app)

# Public (unauthenticated) LIVE DEMO. Runs the real pipeline for a visitor and
# streams it (SSE). It is the only public endpoint that spends real API money, so
# it carries layered caps + an email gate + a global daily ceiling on top of the
# shared waitlist limiter (see server/demo_api.py).
from server import demo_api  # noqa: E402
demo_api.register(app)


# ── Background worker (opt-in; a real deployment runs one) ──────────────
# Off by default so importing the app (tests, one-off scripts) never spawns a
# thread. Set AUTOMATION_WORKER_ENABLED=1 in the deployed process (or run
# `python -m automation.worker` as a separate service).
@app.on_event("startup")
def _maybe_start_worker():
    import os
    if os.environ.get("AUTOMATION_WORKER_ENABLED", "").strip() in ("1", "true", "yes"):
        from automation import worker
        worker.start()
        log.info("automation background worker started")


@app.on_event("shutdown")
def _stop_worker():
    from automation import worker
    worker.stop()


# ── Static frontend (mounted last so /api/* wins) ──────────────────────
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
