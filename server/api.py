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

log = logging.getLogger("saqua.api")


def _configure_app_logging() -> None:
    level = getattr(logging, os.environ.get("SAQUA_LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    for name in ("saqua", "discovery"):
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
    return {"role": m.role, "kind": m.kind, "content": m.content or "", "data": safe}


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
                       user: str = Depends(require_user)):
    out = []
    for s in _store_for(user).list_summaries():
        out.append({"id": s.get("id"), "title": s.get("title") or "New chat",
                    "updated_at": s.get("updated_at")})
    return {"conversations": out}


@app.post("/api/conversations")
def create_conversation(request: Request, _=Depends(_rl_write),
                        user: str = Depends(require_user)):
    conv = Conversation()
    _store_for(user).save(conv)
    return _conversation_public(conv)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str, request: Request, _=Depends(_rl_read),
                     user: str = Depends(require_user)):
    conv = _store_for(user).load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _conversation_public(conv)


@app.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, body: RenameConversation, request: Request,
                        _=Depends(_rl_write), user: str = Depends(require_user)):
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conv.title = body.title
    store.save(conv)
    return _conversation_public(conv)


@app.post("/api/conversations/{cid}/duplicate")
def duplicate_conversation(cid: str, request: Request, _=Depends(_rl_write),
                           user: str = Depends(require_user)):
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
                        user: str = Depends(require_user)):
    _store_for(user).delete(_valid_id(cid))
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: str, body: SendMessage, request: Request,
                 _=Depends(_rl_agent), user: str = Depends(require_user)):
    """Run one agent turn. Defined as `def` so the (possibly slow) blocking call
    executes in FastAPI's threadpool and never blocks the event loop."""
    store = _store_for(user)
    conv = store.load(_valid_id(cid))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    try:
        respond(conv, body.text, store, user_id=user)  # owner-scoped (for send_email)
    except Exception:                            # noqa: BLE001 - never leak internals
        log.exception("agent turn failed for %s", cid)
        raise HTTPException(status_code=502,
                            detail="The assistant couldn't respond just now. Please try again.")
    return _conversation_public(conv)


# ── Automation Agent routes (Clerk-gated, per-user) ────────────────────
from server import automation_api, campaign_api, oauth_api  # noqa: E402
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
