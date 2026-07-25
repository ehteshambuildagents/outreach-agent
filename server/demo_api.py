"""Public live-demo endpoint — the second UNAUTHENTICATED surface in the app, and
the only one that spends real API money per request (it runs the true
discovery→research→qualify→write pipeline; see ``demo/runner.py``).

Abuse controls, in order of severity, layered on purpose because no single one is
enough for an anonymous paid endpoint:

  * hard refusal when shared coordination is missing (production) — same posture as
    the waitlist: a public endpoint whose limiter could degrade to per-process state
    must not be reachable;
  * an EMAIL GATE before the first run — no account/password, just a captured email
    (also a soft waitlist add). Per-IP caps alone fall to any VPN; requiring an email
    makes an extractor rotate emails too, and converts the attempt into a lead;
  * per-IP + per-email fixed-window caps (burst + daily), fail-closed;
  * a single in-flight run per IP (a distributed lock), so parallel requests can't
    dodge the burst counter and multiply cost;
  * a GLOBAL daily ceiling — a dollar budget metered in the shared usage ledger AND
    a fail-closed run-count backstop — because per-IP caps never bound TOTAL spend.

When the global ceiling is hit (most likely on launch day) the visitor gets a clean,
honest ``capacity`` state pointing at the waitlist — never a broken-looking error.

Transport is Server-Sent Events so the visitor watches each stage land live. The
limiter helpers are reused from the waitlist (proxy-aware client IP, fail-closed
counter, shared-Redis guard) exactly as ``contact_api`` does.
"""

import json
import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

import waitlist
from automation import redis
from config import settings
from demo import run_demo
from limits import store as limits_store
from server import demo_session
from server.waitlist_api import _over_limit, client_ip, require_shared_redis

log = logging.getLogger("saqua.demo.api")

_DAY = 86400
_MAX_ICP_LEN = 200
_MAX_WEBSITE_LEN = 300

# ── Visitor-facing copy (every block is a clean STATE, never a raw error) ──────
CAPACITY_MESSAGE = (
    "The live demo has hit today's limit. It runs Saqua's real research pipeline on "
    "every request, so we cap how many run each day. Join the waitlist and you'll be "
    "first in when it opens. The demo resets tomorrow.")
IP_DAILY_MESSAGE = (
    "You've used all of today's demo runs. Join the waitlist for full access. "
    "The demo resets tomorrow.")
EMAIL_DAILY_MESSAGE = (
    "This email has used today's demo runs. Join the waitlist for full access. "
    "The demo resets tomorrow.")
BURST_MESSAGE = (
    "One run at a time. Give the last one a few seconds, then try again.")
IN_PROGRESS_MESSAGE = (
    "A demo run is already going in this browser. Let it finish, then start another.")
NEED_EMAIL_MESSAGE = "Enter a valid email to run the live demo. No account needed."
GMAIL_ONLY_MESSAGE = (
    "The live demo currently supports personal Gmail addresses only, so enter an "
    "@gmail.com address to start. Every other provider gets full access at launch, "
    "and the waitlist takes any email.")
NEED_INPUT_MESSAGE = "Tell me who you sell to, or paste your website, to run the demo."
UNAVAILABLE_MESSAGE = (
    "The live demo is briefly unavailable. Please try again shortly, or join the "
    "waitlist below.")
TURNS_MESSAGE = (
    "You've reached this demo session's message limit. Join the waitlist for full, "
    "unlimited access. Real sends open the moment Gmail clears Google's review.")
SESSION_ENDED_MESSAGE = (
    "Your demo session has ended. Start a new one from the demo page, or join the "
    "waitlist for full access.")

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",        # ask any intermediary NOT to buffer the stream
    "Connection": "keep-alive",
}


class DemoRequest(BaseModel):
    # Optional at the schema layer so a missing/blank email returns the friendly
    # ``need_email`` STATE (uniform {state,message} contract) rather than a raw 422.
    email: str = ""
    icp: str = ""
    website: str = ""
    # Honeypot — hidden in the real form; a bot fills it. A filled value is answered
    # benignly and the run never starts.
    company: str = None

    @field_validator("email")
    @classmethod
    def _trim_email(cls, v: str) -> str:
        return (v or "").strip()[:waitlist.MAX_EMAIL_LEN]

    @field_validator("icp")
    @classmethod
    def _trim_icp(cls, v):
        return (v or "").replace("\x00", "").strip()[:_MAX_ICP_LEN]

    @field_validator("website")
    @classmethod
    def _trim_site(cls, v):
        return (v or "").replace("\x00", "").strip()[:_MAX_WEBSITE_LEN]


def _blocked(state: str, message: str, status: int, **extra) -> JSONResponse:
    """A non-streaming, friendly block response. Distinct content-type from the SSE
    stream so the frontend can branch on one check."""
    return JSONResponse(status_code=status,
                        content={"state": state, "message": message, **extra})


def _global_budget_reached() -> bool:
    """True once the rolling-24h demo spend meets the dollar ceiling. A ledger read
    failure returns False here (does not hard-block) — the fail-closed run-count
    backstop is what guarantees a stop when coordination is unavailable."""
    if settings.DEMO_DAILY_BUDGET_USD <= 0:
        return False
    try:
        spent = limits_store.spend_since(settings.DEMO_LEDGER_USER, time.time() - _DAY)
    except Exception:  # noqa: BLE001
        return False
    return spent >= settings.DEMO_DAILY_BUDGET_USD


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _event_stream(icp: str, website: str, ip: str, token: str):
    """Stream the pipeline as SSE, always releasing the per-IP in-flight lock."""
    try:
        yield _sse("start", {"candidates": settings.DEMO_CANDIDATES})
        for event, payload in run_demo(icp_text=icp, website=website):
            yield _sse(event, payload)
    except Exception:  # noqa: BLE001 - a mid-run crash becomes a clean terminal event
        log.exception("demo run crashed")
        yield _sse("error", {"reason": "Something went wrong mid-run. Please try again."})
    finally:
        try:
            redis.release_lock(f"demo:run:{ip}", token)
        except Exception:  # noqa: BLE001
            pass


# ── Demo-session helpers (cookies + per-session turn budget) ──────────────────
def _is_https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    return proto == "https"


def _set_session_cookies(resp: JSONResponse, request: Request, token: str, exp: int) -> None:
    """Set the HttpOnly token cookie plus a readable expiry cookie (no secret in it —
    just the epoch, so the client can show a countdown without reading the token)."""
    secure = _is_https(request)
    ttl = settings.DEMO_SESSION_TTL_SECONDS
    resp.set_cookie(demo_session.COOKIE_NAME, token, max_age=ttl, httponly=True,
                    secure=secure, samesite="lax", path="/")
    resp.set_cookie(demo_session.EXP_COOKIE, str(exp), max_age=ttl, httponly=False,
                    secure=secure, samesite="lax", path="/")


def _clear_session_cookies(resp: JSONResponse) -> None:
    resp.delete_cookie(demo_session.COOKIE_NAME, path="/")
    resp.delete_cookie(demo_session.EXP_COOKIE, path="/")


def _turn_key(demo_id: str) -> str:
    return f"demo:turns:{demo_id}"


def demo_turns_used(demo_id: str) -> int:
    try:
        v = redis.get(_turn_key(demo_id))
        return int(v) if v else 0
    except Exception:  # noqa: BLE001
        return 0


def reserve_demo_turn(demo_id: str) -> tuple[bool, str]:
    """Admit ONE demo chat turn or return (False, message). Reserves the turn
    up front (increment-then-check) so concurrent turns can't both slip under the
    per-session cap, enforces the SAME global $ ceiling as pipeline runs, and
    meters the turn's estimated cost into the shared demo budget."""
    if _global_budget_reached():
        return False, CAPACITY_MESSAGE
    try:
        used = redis.incr_expiring(_turn_key(demo_id), settings.DEMO_SESSION_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - fail closed on a public paid path
        return False, UNAVAILABLE_MESSAGE
    if used > settings.DEMO_SESSION_TURNS:
        return False, TURNS_MESSAGE
    try:
        limits_store.add_usage(settings.DEMO_LEDGER_USER, "demo_chat",
                               settings.DEMO_EST_COST_PER_RUN_USD, 1)
    except Exception:  # noqa: BLE001
        pass
    return True, ""


def _gate(body: DemoRequest, request: Request):
    """The shared admission gate for anything demo (a pipeline run OR a session
    mint): kill switch, shared-Redis posture, honeypot, email validity, the
    Gmail-only rule, global budget, per-IP burst/daily + per-email daily
    (fail-closed), soft waitlist add. Returns a friendly block response, or the
    (email, ip) of an admitted visitor."""
    if not settings.DEMO_ENABLED:
        return _blocked("unavailable", UNAVAILABLE_MESSAGE, 503)

    # Public paid endpoint: refuse rather than run on per-process limiter state.
    if require_shared_redis() and not redis.configured():
        log.error("DEMO DISABLED — shared Redis not configured; refusing to serve "
                  "a public, money-spending endpoint on per-process limits.")
        return _blocked("unavailable", UNAVAILABLE_MESSAGE, 503)

    # Honeypot: answer benignly, start nothing.
    if (body.company or "").strip():
        log.info("demo: honeypot tripped from %s", client_ip(request))
        return _blocked("capacity", CAPACITY_MESSAGE, 200)

    email = waitlist.normalize(body.email)
    if not waitlist.valid(email):
        return _blocked("need_email", NEED_EMAIL_MESSAGE, 400)

    # Personal Gmail only for the demo period (product decision). Checked before
    # any limiter so a work-address attempt never burns a per-IP bucket, and
    # before the soft waitlist add so a rejected visitor is never signed up to
    # anything they were just told they can't use.
    if not email.endswith("@gmail.com"):
        return _blocked("gmail_only", GMAIL_ONLY_MESSAGE, 400)

    ip = client_ip(request)

    # Global ceiling FIRST, so a capacity-blocked visitor doesn't lose a per-IP
    # run to a limit that isn't theirs.
    if _global_budget_reached():
        return _blocked("capacity", CAPACITY_MESSAGE, 503)

    # Per-IP burst, then per-IP daily, then per-email daily (all fail-closed).
    if _over_limit(f"demo:burst:{ip}", settings.DEMO_IP_BURST, settings.DEMO_IP_BURST_WINDOW):
        return _blocked("rate_limited", BURST_MESSAGE, 429, scope="burst")
    if _over_limit(f"demo:ip:{ip}", settings.DEMO_IP_DAILY, _DAY):
        return _blocked("rate_limited", IP_DAILY_MESSAGE, 429, scope="ip")
    if _over_limit(f"demo:em:{email}", settings.DEMO_EMAIL_DAILY, _DAY):
        return _blocked("rate_limited", EMAIL_DAILY_MESSAGE, 429, scope="email")

    # Soft waitlist add — the gate doubles as a lead. Best-effort; never blocks.
    try:
        waitlist.join(email, source="demo")
    except Exception:  # noqa: BLE001
        log.info("demo: soft waitlist add failed for a visitor", exc_info=True)
    return email, ip


def register(app) -> None:
    """Mount the public demo routes."""

    @app.post("/api/demo/run")
    def demo_run(body: DemoRequest, request: Request):
        # Input check precedes the gate so an empty ask never burns a limit.
        if not body.icp and not body.website:
            return _blocked("need_input", NEED_INPUT_MESSAGE, 400)
        admitted = _gate(body, request)
        if not isinstance(admitted, tuple):
            return admitted
        email, ip = admitted
        icp, website = body.icp, body.website

        # One in-flight run per IP: stops parallel requests dodging the burst counter.
        token = uuid.uuid4().hex
        if not redis.acquire_lock(f"demo:run:{ip}", token,
                                  ttl_seconds=settings.DEMO_RUN_TIMEOUT_SECONDS + 15):
            return _blocked("in_progress", IN_PROGRESS_MESSAGE, 429)

        # Fail-closed global run-count backstop (holds even if the $ ledger can't be
        # read). Counts only real, fully-admitted runs.
        if _over_limit("demo:global:runs", settings.DEMO_GLOBAL_DAILY_RUNS, _DAY):
            redis.release_lock(f"demo:run:{ip}", token)
            return _blocked("capacity", CAPACITY_MESSAGE, 503)

        # Meter the run's estimated cost up front so concurrent runs count toward the
        # same daily budget (real per-token cost is also captured by telemetry).
        try:
            limits_store.add_usage(settings.DEMO_LEDGER_USER, "demo",
                                   settings.DEMO_EST_COST_PER_RUN_USD, 1)
        except Exception:  # noqa: BLE001
            pass

        log.info("demo run start ip=%s email=%s mode=%s", ip, email,
                 "website" if website else "icp")
        return StreamingResponse(_event_stream(icp, website, ip, token),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/api/demo/session")
    def demo_session_start(body: DemoRequest, request: Request):
        """Mint a sandboxed demo session for an anonymous visitor: the SAME email
        gate + caps as a run, then a signed short-lived cookie that lets the real
        app pages render under a ``demo_*`` principal (disjoint from real users)."""
        admitted = _gate(body, request)
        if not isinstance(admitted, tuple):
            return admitted
        email, ip = admitted
        demo_id = demo_session.new_demo_id()
        token, exp = demo_session.mint_token(demo_id)
        log.info("demo session start ip=%s email=%s id=%s", ip, email, demo_id)
        resp = JSONResponse({"active": True, "expires_at": exp,
                             "turns_used": 0,
                             "turns_limit": settings.DEMO_SESSION_TURNS})
        _set_session_cookies(resp, request, token, exp)
        return resp

    @app.get("/api/demo/session")
    def demo_session_status(request: Request):
        """Report the current demo session (or ``{active: false}``). Unauthenticated
        and cheap: a real logged-in user simply has no demo cookie."""
        tok = (request.headers.get(demo_session.HEADER_NAME)
               or request.cookies.get(demo_session.COOKIE_NAME))
        demo_id = demo_session.verify_token(tok) if tok else None
        if not demo_id:
            return JSONResponse({"active": False})
        return JSONResponse({"active": True,
                             "expires_at": demo_session.token_expiry(tok),
                             "turns_used": demo_turns_used(demo_id),
                             "turns_limit": settings.DEMO_SESSION_TURNS})

    @app.delete("/api/demo/session")
    def demo_session_end(request: Request):
        """End the demo session by clearing its cookies (the token is stateless, so
        there is nothing server-side to revoke; expiry does the rest)."""
        resp = JSONResponse({"active": False})
        _clear_session_cookies(resp)
        return resp
