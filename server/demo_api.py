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
from server.waitlist_api import _over_limit, client_ip, require_shared_redis

log = logging.getLogger("saqua.demo.api")

_DAY = 86400
_MAX_ICP_LEN = 200
_MAX_WEBSITE_LEN = 300

# ── Visitor-facing copy (every block is a clean STATE, never a raw error) ──────
CAPACITY_MESSAGE = (
    "The live demo has hit today's limit. It runs Saqua's real research pipeline on "
    "every request, so we cap how many run each day. Join the waitlist and you'll be "
    "first in when it opens — the demo resets tomorrow.")
IP_DAILY_MESSAGE = (
    "You've used all of today's demo runs. Join the waitlist for full access — the "
    "demo resets tomorrow.")
EMAIL_DAILY_MESSAGE = (
    "This email has used today's demo runs. Join the waitlist for full access — the "
    "demo resets tomorrow.")
BURST_MESSAGE = (
    "One run at a time — give the last one a few seconds, then try again.")
IN_PROGRESS_MESSAGE = (
    "A demo run is already going in this browser. Let it finish, then start another.")
NEED_EMAIL_MESSAGE = "Enter a valid email to run the live demo — no account needed."
NEED_INPUT_MESSAGE = "Tell me who you sell to, or paste your website, to run the demo."
UNAVAILABLE_MESSAGE = (
    "The live demo is briefly unavailable. Please try again shortly — or join the "
    "waitlist below.")

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


def register(app) -> None:
    """Mount the public demo route."""

    @app.post("/api/demo/run")
    def demo_run(body: DemoRequest, request: Request):
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

        icp, website = body.icp, body.website
        if not icp and not website:
            return _blocked("need_input", NEED_INPUT_MESSAGE, 400)

        email = waitlist.normalize(body.email)
        if not waitlist.valid(email):
            return _blocked("need_email", NEED_EMAIL_MESSAGE, 400)

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
