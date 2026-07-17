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
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from chat.agent import respond  # noqa: E402
from chat.models import Conversation, new_id  # noqa: E402
from chat.store import ConversationStore  # noqa: E402
from server.auth import auth_enabled, publishable_key, require_user  # noqa: E402
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
                            detail="You're going a little fast — please wait a moment.")
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


def _prospect_public(p: dict) -> dict:
    """One researched prospect for the browse card — the fields the UI renders
    (preview shown collapsed; detail revealed on expand). No prompts/internals."""
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
        "detail": {k: detail.get(k) for k in
                   ("what_they_do", "research_confidence", "findings", "sources",
                    "score_breakdown", "strongest_signals", "missing_information",
                    "disqualifiers", "why_discovered")},
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
                "gradually — you'll be able to sign in as soon as you're approved."),
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


# ── Routes ─────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True}


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
                       user: str = Depends(require_approved_user)):
    out = []
    for s in _store_for(user).list_summaries():
        out.append({"id": s.get("id"), "title": s.get("title") or "New chat",
                    "updated_at": s.get("updated_at")})
    return {"conversations": out}


@app.post("/api/conversations")
def create_conversation(request: Request, _=Depends(_rl_write),
                        user: str = Depends(require_approved_user)):
    conv = Conversation()
    _store_for(user).save(conv)
    return _conversation_public(conv)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str, request: Request, _=Depends(_rl_read),
                     user: str = Depends(require_approved_user)):
    conv = _store_for(user).load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _conversation_public(conv)


@app.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, body: RenameConversation, request: Request,
                        _=Depends(_rl_write), user: str = Depends(require_approved_user)):
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conv.title = body.title
    store.save(conv)
    return _conversation_public(conv)


@app.post("/api/conversations/{cid}/duplicate")
def duplicate_conversation(cid: str, request: Request, _=Depends(_rl_write),
                           user: str = Depends(require_approved_user)):
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
                        user: str = Depends(require_approved_user)):
    _store_for(user).delete(_valid_id(cid))
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: str, body: SendMessage, request: Request,
                 _=Depends(_rl_agent), user: str = Depends(require_approved_user)):
    """Run one agent turn. Defined as `def` so the (possibly slow) blocking call
    executes in FastAPI's threadpool and never blocks the event loop."""
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


# ── Company profile (the sender's own details, remembered in every chat) ─
@app.get("/api/company")
def get_company(request: Request, _=Depends(_rl_read),
                user: str = Depends(require_approved_user)):
    return {"company": _store_for(user).load_company()}


@app.put("/api/company")
def put_company(body: CompanyProfile, request: Request, _=Depends(_rl_write),
                user: str = Depends(require_approved_user)):
    data = body.model_dump()
    _store_for(user).save_company(data)
    return {"company": data}


# ── Billing / plan (the user's real plan + free-tier usage) ────────────
@app.get("/api/billing")
def get_billing(request: Request, _=Depends(_rl_read),
                user: str = Depends(require_approved_user)):
    from config.settings import FREE_PROSPECT_LIMIT
    usage = _store_for(user).load_usage()
    used = len(usage.get("prospects") or [])
    limit = FREE_PROSPECT_LIMIT
    return {
        "plan": "free",
        "prospect_limit": limit,
        "prospects_used": used,
        "prospects_remaining": (max(0, limit - used) if limit > 0 else None),
    }


# ── Automation Agent routes (Clerk-gated, per-user) ────────────────────
from server import admin_api, automation_api, campaign_api, oauth_api  # noqa: E402
admin_api.register(app)                          # internal ops views (X-Admin-Token)
automation_api.register(app, rl_read=_rl_read, rl_write=_rl_write)
oauth_api.register(app, rl_read=_rl_read, rl_write=_rl_write)
campaign_api.register(app, rl_read=_rl_read, rl_write=_rl_write)


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
