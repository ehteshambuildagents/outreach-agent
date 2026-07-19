"""Internal admin API — basic operational views for the soft launch.

Deliberately NOT a polished dashboard: plain JSON endpoints (plus the `python -m
limits` / `python -m access` CLIs) that let you, at a glance, see per-user
usage-vs-cap, approve pending signups, and read recent unhandled errors.

Auth: a single shared secret in ``SAQUA_ADMIN_TOKEN`` sent as the ``X-Admin-Token``
header. If the env var is unset, these endpoints are DISABLED (404) so there's no
open hole in a default deployment.
"""

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import access
import limits
from server import error_log


def _require_admin(request: Request) -> None:
    token = (os.environ.get("SAQUA_ADMIN_TOKEN") or "").strip()
    if not token:
        # Feature disabled unless an admin token is configured — never an open door.
        raise HTTPException(status_code=404, detail="Not found.")
    provided = (request.headers.get("x-admin-token") or "").strip()
    if not (provided and hmac.compare_digest(provided, token)):
        raise HTTPException(status_code=403, detail="Forbidden.")


class UserBody(BaseModel):
    user_id: str
    note: str | None = None


def register(app) -> None:
    router = APIRouter(prefix="/api/admin")

    # ── Task 2: usage vs cap ───────────────────────────────────────────
    @router.get("/usage")
    def usage(request: Request, user_id: str | None = None):
        _require_admin(request)
        if user_id:
            return {"user": limits.usage_snapshot(user_id)}
        return {"users": limits.all_usage()}

    @router.post("/account/pause")
    def pause(body: UserBody, request: Request):
        _require_admin(request)
        limits.pause(body.user_id, body.note or "paused by admin")
        return {"ok": True, "user": limits.usage_snapshot(body.user_id)}

    @router.post("/account/resume")
    def resume(body: UserBody, request: Request):
        _require_admin(request)
        limits.resume(body.user_id)
        return {"ok": True, "user": limits.usage_snapshot(body.user_id)}

    # ── Waitlist (pre-launch) ──────────────────────────────────────────
    @router.get("/waitlist")
    def waitlist_counts(request: Request):
        """Counts per opt-in status. Only 'subscribed' are ever broadcast to."""
        _require_admin(request)
        import waitlist
        return {"counts": waitlist.counts()}

    @router.get("/waitlist/list")
    def waitlist_list(request: Request, status: str | None = None):
        _require_admin(request)
        from waitlist import store as wl_store
        rows = wl_store.list_by_status(status)
        # Never return the per-row link token: it authorises unsubscribing that
        # address, and an admin view has no use for it.
        return {"entries": [{k: v for k, v in r.items() if k != "token"} for r in rows]}

    # ── Proxy / client-IP diagnostic ───────────────────────────────────
    @router.get("/echo-ip")
    def echo_ip(request: Request):
        """Report the raw forwarding chain this request arrived with.

        Exists because the per-IP rate limiter was found bucketing on infrastructure
        addresses in production rather than on real clients: three requests produced
        three distinct ``rl:wl:ip:*`` buckets, none of them the caller's address. The
        code is right in isolation and its unit tests pass on synthetic chains — what
        was never established is the SHAPE of the chain this deployment actually
        receives, which decides whether TRUSTED_PROXY_HOPS is the fix at all.

        So this returns the raw header alongside what ``client_ip`` currently makes of
        it, letting the two be compared against a known caller address instead of
        guessed at. Values are returned only for headers that are known to carry a
        client address; every other header is reported by NAME only, so an unexpected
        platform header can be spotted without this endpoint echoing an Authorization
        or Cookie value back to whoever holds the admin token.
        """
        _require_admin(request)
        from server import waitlist_api

        known = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip",
                 "true-client-ip", "x-client-ip", "x-vercel-forwarded-for",
                 "x-envoy-external-address", "fly-client-ip", "x-saqua-client-ip")
        values = {h: request.headers.get(h) for h in known if request.headers.get(h)}

        xff = request.headers.get("x-forwarded-for") or ""
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        return {
            "client_ip_headers": values,
            "xff_parts": parts,
            "xff_len": len(parts),
            # The peer as the ASGI server sees it — Railway's internal mesh, not a
            # client. The address that reached our EDGE is `edge_peer` below.
            "peer": request.client.host if request.client else None,
            "edge_peer": waitlist_api._peer(request),
            # Whether this request proved it came through our own frontend proxy.
            "proxy_secret_configured": bool(waitlist_api.proxy_secret()),
            "proxied": bool(request.headers.get("x-saqua-client-ip")),
            # What the limiter would key on right now. Compare against the address
            # the call was actually made from: if they differ, the bucket is wrong.
            "client_ip_computed": waitlist_api.client_ip(request),
            # Names only — never values. See docstring.
            "other_header_names": sorted(
                h for h in request.headers.keys() if h.lower() not in known),
        }

    # ── Task 3: request-access gating ──────────────────────────────────
    @router.get("/access/pending")
    def pending(request: Request):
        _require_admin(request)
        return {"pending": access.list_pending()}

    @router.get("/access/all")
    def access_all(request: Request):
        _require_admin(request)
        return {"users": access.list_all()}

    @router.post("/access/approve")
    def approve(body: UserBody, request: Request):
        _require_admin(request)
        access.approve(body.user_id, body.note)
        return {"ok": True, "user_id": body.user_id, "status": "approved"}

    @router.post("/access/deny")
    def deny(body: UserBody, request: Request):
        _require_admin(request)
        access.deny(body.user_id, body.note)
        return {"ok": True, "user_id": body.user_id, "status": "denied"}

    # ── Task 4: error visibility ───────────────────────────────────────
    @router.get("/errors")
    def errors(request: Request, limit: int = 50):
        _require_admin(request)
        return {"errors": error_log.recent(min(max(int(limit), 1), 500))}

    app.include_router(router)
