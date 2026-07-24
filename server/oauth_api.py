"""OAuth connect + inbound webhooks — the HTTP surface for real mailbox access.

Routes (mounted on the same FastAPI app):

    GET  /api/oauth/{provider}/login       (Clerk) -> {url} to send the user to
    GET  /api/oauth/{provider}/callback            -> provider redirects here; we
                                                      exchange the code + store tokens
    GET  /api/oauth/accounts               (Clerk) -> connected accounts (no tokens)
    POST /api/oauth/{provider}/disconnect  (Clerk) -> revoke + delete
    POST /api/oauth/{provider}/watch       (Clerk) -> enable reply push notifications
    POST /api/webhooks/gmail                       -> Gmail Pub/Sub push
    POST /api/webhooks/graph                       -> Microsoft Graph notifications

Security: login binds a single-use CSRF ``state`` to the Clerk user in Redis; the
callback refuses any state it can't consume (blocks forgery/replay). The client
secret is used only here, server-side; tokens are encrypted before storage. The
Gmail webhook is gated by a shared ``?token=`` (when configured) and the Graph
webhook by ``clientState`` — both are duplicate-safe.
"""

import hmac
import json
import logging
import os
from urllib.parse import urlencode, urljoin

from fastapi import Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from automation import oauth, push
from automation.store import WorkflowStore
from automation.tokens import TokenStore
from server.auth import require_user
from server.demo_auth import require_identity_or_demo

log = logging.getLogger("saqua.oauth_api")

_PROVIDERS = {"gmail", "outlook"}
_store = WorkflowStore()
_tokens = TokenStore()


def _provider_or_404(provider: str) -> str:
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider.")
    return provider


def _redirect_uri(request: Request, provider: str) -> str:
    """The redirect URI must be byte-identical at authorize and callback. Prefer an
    explicit env value (what's registered with the provider); else derive from
    APP_BASE_URL (the API origin), then finally from the request origin."""
    app_base = (os.environ.get("APP_BASE_URL") or "").strip()
    base = app_base or str(request.base_url).rstrip("/")
    derived = f"{base.rstrip('/')}/api/oauth/{provider}/callback"
    return oauth.redirect_uri(provider, default=derived)


def _frontend_redirect(request: Request, path: str, params: dict[str, str]) -> str:
    """Return a browser-facing redirect URL on FRONTEND_URL.

    The provider callback lands on the API origin. After token handling, the
    browser must return to the product frontend, not stay on api.saqua.io.
    """
    frontend = (os.environ.get("FRONTEND_URL") or "").strip()
    base = frontend.rstrip("/") if frontend else str(request.base_url).rstrip("/")
    safe_path = path if path.startswith("/") else "/settings"
    url = urljoin(f"{base}/", safe_path.lstrip("/"))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _debug_token_ok(request: Request) -> bool:
    """Temporary diagnostic gate: ?token= must equal an already-configured secret
    (GMAIL_PUBSUB_TOKEN — set in prod — or SAQUA_ADMIN_TOKEN). Unset both -> 404."""
    provided = (request.query_params.get("token") or "").strip()
    if not provided:
        return False
    for _env in ("GMAIL_PUBSUB_TOKEN", "SAQUA_ADMIN_TOKEN"):
        secret = (os.environ.get(_env) or "").strip()
        if secret and hmac.compare_digest(provided, secret):
            return True
    return False


def _cadence_report(wf) -> dict:
    """Full cadence breakdown + verdict for ONE workflow — shared by the
    workflow-steps and campaign-workflows debug endpoints. Per step: schedule,
    status, subject, body preview, and an inferred content source (original / bump
    / writer / breakup) corroborated by content signatures; plus a one-glance
    verdict vs the expected 5-touch cadence. Pure/read-only."""
    import hashlib
    from automation import scheduler
    from config.settings import (AUTOMATION_MAX_FOLLOWUPS,
                                  AUTOMATION_REPLY_WAIT_DAYS as WAIT)
    # Stable content signatures from the cadence builder (campaign_api._bump_step /
    # _breakup_step). Position decides the source; these corroborate it.
    BUMP_SIG = "Floating this back to the top"
    BREAKUP_SIG = "so I'm not cluttering your inbox"

    def _sha8(s):
        return hashlib.sha1((s or "").encode("utf-8")).hexdigest()[:8]

    def _quote(b):                              # mirrors campaign_api._quote_original
        return "\n".join(f"> {ln}" if ln else ">"
                         for ln in (b or "").strip().splitlines())

    steps = wf.steps
    n = len(steps)
    base = steps[0].scheduled_at if steps else None
    orig_body = steps[0].body if steps else ""
    rows = []
    for i, s in enumerate(steps):
        body = s.body or ""
        is_bump, is_breakup = (BUMP_SIG in body), (BREAKUP_SIG in body)
        if i == 0:
            src = "original"
        elif is_breakup or i == n - 1:
            src = "breakup"
        elif is_bump:
            src = "bump"
        else:
            src = "writer"
        sched = s.scheduled_at
        rows.append({
            "index": s.index,
            "delay_days": s.delay_days,
            "scheduled_at": sched,
            "scheduled_at_iso": (scheduler.local_time(sched, wf.timezone).isoformat()
                                 if sched else None),
            "days_from_first": (round((sched - base) / 86400.0, 3)
                                if sched and base else None),
            "status": str(s.status),
            "subject": s.subject,
            "content_source": src,
            "content_signals": {"is_bump_format": is_bump, "is_breakup_format": is_breakup},
            "body_len": len(body),
            "body_sha8": _sha8(body),
            "body_preview": body[:240],
        })

    writer_shas = [r["body_sha8"] for r in rows if r["content_source"] == "writer"]
    bump_row = next((s for s in steps if BUMP_SIG in (s.body or "")), None)
    day_spacing = [r["days_from_first"] for r in rows]
    source_seq = [r["content_source"] for r in rows]
    expected_count = AUTOMATION_MAX_FOLLOWUPS + 1                     # 5 touches
    expected_spacing = [round(float(WAIT) * k, 3) for k in range(n)]  # 0, W, 2W, ...
    expected_sources = (["original", "bump"] + ["writer"] * max(0, n - 3)
                        + ["breakup"]) if n >= 3 else source_seq
    writers_distinct = (len(writer_shas) == len(set(writer_shas))) if writer_shas else None
    bump_reuses_original = bool(bump_row and _quote(orig_body) in (bump_row.body or ""))

    return {
        "workflow_id": wf.id,
        "workflow": {"state": str(wf.state), "provider": wf.provider,
                     "to_email": wf.to_email, "company": wf.company,
                     "current_index": wf.current_index, "created_at": wf.created_at,
                     "next_run_at": wf.next_run_at, "timezone": wf.timezone},
        "config": {"reply_wait_days": WAIT, "max_followups": AUTOMATION_MAX_FOLLOWUPS},
        "step_count": n,
        "delay_days_sequence": [r["delay_days"] for r in rows],
        "day_spacing": day_spacing,
        "expected_day_spacing": expected_spacing,
        "source_sequence": source_seq,
        "steps": rows,
        "verdict": {
            "step_count_ok": n == expected_count,
            "spacing_ok": day_spacing == expected_spacing,
            "sources_ok": source_seq == expected_sources,
            "writer_slots_distinct": writers_distinct,
            "bump_reuses_original": bump_reuses_original,
            "all_good": bool(n == expected_count and day_spacing == expected_spacing
                             and source_seq == expected_sources and writers_distinct
                             and bump_reuses_original),
        },
    }


def register(app, rl_read=None, rl_write=None):

    def _read(r: Request):
        return rl_read(r) if rl_read else None

    def _write(r: Request):
        return rl_write(r) if rl_write else None

    # ── connect ────────────────────────────────────────────────────────
    @app.get("/api/oauth/{provider}/login")
    def oauth_login(provider: str, request: Request, _=Depends(_read),
                    user: str = Depends(require_user)):
        _provider_or_404(provider)
        if not oauth.configured(provider):
            raise HTTPException(status_code=503,
                                detail=f"{provider} sign-in is not configured.")
        return_to = request.query_params.get("return_to", "/settings")
        if not return_to.startswith("/"):        # only same-origin returns
            return_to = "/settings"
        state = oauth.make_state(user, provider, return_to)
        url = oauth.build_authorize_url(provider, state, _redirect_uri(request, provider))
        return {"url": url}

    # Reconnect is just a fresh consent (Google forces prompt=consent already).
    @app.get("/api/oauth/{provider}/reconnect")
    def oauth_reconnect(provider: str, request: Request, _=Depends(_read),
                        user: str = Depends(require_user)):
        return oauth_login(provider, request, None, user)

    @app.get("/api/oauth/{provider}/callback")
    def oauth_callback(provider: str, request: Request):
        _provider_or_404(provider)
        params = request.query_params
        if params.get("error"):
            return RedirectResponse(
                _frontend_redirect(request, "/settings", {"error": provider}),
                status_code=302,
            )
        ctx = oauth.consume_state(params.get("state"))
        if ctx is None or ctx["provider"] != provider:
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
        code = params.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code.")
        try:
            tok = oauth.exchange_code(provider, code, _redirect_uri(request, provider))
            email = oauth.account_email(provider, tok["access_token"])
        except oauth.OAuthError as exc:
            log.info("oauth callback failed for %s: %s", provider, exc)
            return RedirectResponse(
                _frontend_redirect(request, "/settings", {"error": provider}),
                status_code=302,
            )
        import time
        account_email = email or f"{provider}-account"
        _tokens.upsert(
            user_id=ctx["user_id"], provider=provider,
            account_email=account_email,
            access_token=tok["access_token"], refresh_token=tok.get("refresh_token"),
            expires_at=time.time() + tok.get("expires_in", 3600),
            scopes=tok.get("scope", ""))
        log.info("connected %s account for user (email hidden in logs)", provider)
        # Arm Gmail reply-detection push the moment the account connects — no
        # separate frontend step. Best-effort: a watch failure (e.g. the Pub/Sub
        # topic isn't configured yet, or a transient error) must NOT break the
        # connect flow; the worker's maintenance sweep re-arms any account that
        # ends up without a watch. Logged explicitly (same logger as the "connected"
        # line above) so a reconnect is traceable in the deploy logs.
        if provider == "gmail":
            try:
                res = push.enable_gmail_watch(_tokens, ctx["user_id"], account_email)
                if res.get("ok"):
                    watch = res.get("watch") or {}
                    log.info("gmail watch armed for %s (history_id=%s, expiration=%s)",
                             account_email, watch.get("historyId"), watch.get("expiration"))
                else:
                    # e.g. reconnect_required — no token to watch with. Not an
                    # exception, but still a "watch not armed" the sweep must retry.
                    log.error("gmail watch NOT armed for %s: %s; worker sweep will retry",
                              account_email, res.get("reason") or "unknown reason")
            except Exception as exc:  # noqa: BLE001 - never fail connect on watch
                # Full exception (message + traceback), incl. the underlying Gmail
                # API error, so a silent failure is impossible to miss in the logs.
                log.error("gmail watch failed for %s: %s; worker sweep will retry",
                          account_email, exc, exc_info=True)
        return RedirectResponse(
            _frontend_redirect(request, ctx["return_to"], {"connected": provider}),
            status_code=302,
        )

    @app.get("/api/oauth/accounts")
    def oauth_accounts(request: Request, _=Depends(_read),
                       user: str = Depends(require_identity_or_demo)):
        # Read-only, own-scoped: a demo principal has no connected mailboxes, so
        # this returns an empty list and the Settings page shows Gmail "Coming
        # soon". The OAuth login/disconnect/watch routes stay member-only.
        return {"accounts": _tokens.list_accounts(user)}

    @app.post("/api/oauth/{provider}/disconnect")
    def oauth_disconnect(provider: str, request: Request, _=Depends(_write),
                         user: str = Depends(require_user)):
        _provider_or_404(provider)
        account = request.query_params.get("account_email")
        rec = _tokens.get(user, provider, account)
        if rec is None:
            raise HTTPException(status_code=404, detail="No connected account.")
        oauth.revoke(provider, rec["refresh_token"])     # best-effort provider revoke
        _tokens.delete(user, provider, rec["account_email"])
        return {"ok": True, "disconnected": rec["account_email"]}

    @app.post("/api/oauth/{provider}/watch")
    def oauth_watch(provider: str, request: Request, _=Depends(_write),
                    user: str = Depends(require_user)):
        _provider_or_404(provider)
        account = request.query_params.get("account_email")
        rec = _tokens.get(user, provider, account)
        if rec is None:
            raise HTTPException(status_code=404, detail="No connected account.")
        if provider == "gmail":
            result = push.enable_gmail_watch(_tokens, user, rec["account_email"])
        else:
            raise HTTPException(status_code=501,
                                detail="Outlook watch is enabled via subscription webhook.")
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail="Reconnect required.")
        return result

    # ── inbound webhooks (public; verified by shared secret / clientState) ──
    @app.post("/api/webhooks/gmail")
    async def gmail_webhook(request: Request):
        # Read the RAW body ourselves (not request.json()) so a malformed or empty
        # payload is LOGGED and acked with 200 — instead of an uncaught 500 that
        # Pub/Sub then retries (which is what a hit at two timestamps ~a minute
        # apart looks like). The entry line is on saqua.oauth_api (confirmed
        # visible) and reports the real content-type + byte size of every hit.
        raw = await request.body()
        log.info("gmail webhook route hit: content_type=%r bytes=%d token_present=%s",
                 request.headers.get("content-type"), len(raw or b""),
                 bool(request.query_params.get("token")))
        secret = os.environ.get("GMAIL_PUBSUB_TOKEN", "")
        if secret and request.query_params.get("token") != secret:
            raise HTTPException(status_code=401, detail="Bad push token.")
        try:
            envelope = json.loads(raw) if raw else {}
        except (ValueError, TypeError) as exc:
            log.error("gmail webhook: body is not JSON (%s); first 300 bytes=%r",
                      type(exc).__name__, (raw or b"")[:300])
            return {"ok": True, "ignored": "bad_json"}      # 200 so Pub/Sub stops retrying
        # Wrap the handler call on THIS (proven-visible) logger. handle_gmail_pubsub
        # logs its own steps on automation.push; if those aren't surfacing in the
        # deploy (or push.py is stale), these two lines still show the payload shape
        # AND the handler's return value, proving whether the handler ran at all.
        log.info("gmail webhook: parsed envelope keys=%s -> handle_gmail_pubsub",
                 list(envelope.keys()) if isinstance(envelope, dict) else type(envelope).__name__)
        result = push.handle_gmail_pubsub(_store, _tokens, envelope)
        # Mirror the handler's step trace onto THIS (visible) logger — automation.push
        # is being dropped in the deploy, and this trace holds the real reply-detection
        # detail: history.list message count, startHistoryId, and per-message thread
        # matching (SENT-skip / no-match / STOPPED).
        for _line in (result.get("trace") or []):
            log.info("gmail push> %s", _line)
        log.info("gmail webhook: done ok=%s stopped=%s duplicate=%s ignored=%s",
                 result.get("ok"), result.get("stopped"),
                 result.get("duplicate"), result.get("ignored"))
        return result

    @app.api_route("/api/webhooks/graph", methods=["POST"])
    async def graph_webhook(request: Request):
        # Graph subscription handshake: echo the validationToken as text/plain.
        token = request.query_params.get("validationToken")
        if token:
            return PlainTextResponse(token, status_code=200)
        body = await request.json()
        expected = os.environ.get("GRAPH_CLIENT_STATE") or None
        return push.handle_graph_notifications(_store, _tokens, body,
                                               expected_client_state=expected)

    # ── TEMPORARY diagnostic (token-gated, strictly read-only) ─────────
    @app.get("/api/debug/gmail-accounts")
    def gmail_debug_accounts(request: Request):
        """List EVERY stored Gmail account (ANY status), no email needed — to see
        what's actually on record vs what you've been testing. Shows the stored
        email AND its exact repr (so hidden case/whitespace is visible), status,
        token expiry, and watch_state. Token-gated, read-only. Temporary.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        import time as _t
        out = {"build_marker": getattr(push, "BUILD_MARKER", None), "gmail_accounts": []}
        try:
            rows = _tokens.db.query(
                "SELECT user_id, account_email, status, expires_at, watch_state, "
                "created_at, updated_at FROM oauth_accounts WHERE provider=? "
                "ORDER BY updated_at DESC", ("gmail",))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        for r in rows:
            try:
                ws = json.loads(r["watch_state"]) if r["watch_state"] else {}
            except (ValueError, TypeError):
                ws = {}
            out["gmail_accounts"].append({
                "account_email": r["account_email"],
                "account_email_repr": repr(r["account_email"]),   # exposes case/whitespace
                "status": r["status"],
                "user_id_tail": (r["user_id"] or "")[-8:],
                "token_expired": bool(r["expires_at"] and r["expires_at"] < _t.time()),
                "watch_state": {"history_id": ws.get("history_id") or ws.get("historyId"),
                                "expiration": ws.get("expiration"),
                                "armed": bool(ws)},
            })
        out["count"] = len(out["gmail_accounts"])
        return out

    @app.get("/api/debug/storage")
    def gmail_debug_storage(request: Request):
        """Report the ACTUAL storage backend in THIS deploy: sqlite vs postgres,
        the sqlite path + whether it is ephemeral or on a persistent volume, and
        row counts — answers 'is my data surviving deploys?'. The three sqlite
        causes (no DATABASE_URL / broken DATABASE_URL / AUTOMATION_FORCE_SQLITE set)
        each need a different fix, so all three signals are surfaced. Read-only,
        token-gated. Temporary.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        import os as _os
        from urllib.parse import urlsplit
        from automation import db as _db
        url = _db.database_url()
        target = None
        if url:
            try:
                sp = urlsplit(url)
                target = f"{sp.hostname}:{sp.port or ''}/{(sp.path or '').lstrip('/')}"
            except Exception:  # noqa: BLE001
                target = "<unparseable>"
        backend = _db.backend()
        vol = _os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
        out = {
            "build_marker": getattr(push, "BUILD_MARKER", None),
            "backend": backend,
            "database_url_set": bool(url),
            "database_url_target": target,                 # host/db only — no credentials
            "force_sqlite_env": (_os.environ.get("AUTOMATION_FORCE_SQLITE") or "").strip() or None,
            "railway_volume_mount_path": vol,
        }
        if backend == "sqlite":
            path = _db.sqlite_path()
            exists = _os.path.exists(path)
            on_vol = bool(vol and _os.path.abspath(path).startswith(_os.path.abspath(vol)))
            out["sqlite"] = {"path": path, "exists": exists,
                             "size_bytes": _os.path.getsize(path) if exists else 0,
                             "mtime_epoch": _os.path.getmtime(path) if exists else None,
                             "on_persistent_volume": on_vol}
            out["persistent"] = on_vol
            out["verdict"] = (
                "SQLite on a persistent Railway volume — survives deploys."
                if on_vol else
                "SQLite on the EPHEMERAL container filesystem — WIPED on every "
                "deploy/restart. Connected accounts do not survive. THIS is the bug.")
        else:
            out["persistent"] = True
            out["verdict"] = "Postgres — persistent; survives deploys."
        try:
            q = _tokens.db.query_one
            out["counts"] = {
                "gmail_accounts": q("SELECT COUNT(*) AS n FROM oauth_accounts "
                                    "WHERE provider=?", ("gmail",))["n"],
                "oauth_accounts_total": q("SELECT COUNT(*) AS n FROM oauth_accounts")["n"],
                "workflows": q("SELECT COUNT(*) AS n FROM workflows")["n"],
                "processed_ledger": q("SELECT COUNT(*) AS n FROM processed")["n"],
            }
        except Exception as exc:  # noqa: BLE001
            out["counts_error"] = f"{type(exc).__name__}: {exc}"
        return out

    @app.get("/api/debug/workflow-steps")
    def workflow_steps_debug(request: Request):
        """Inspect a workflow's assembled cadence WITHOUT waiting for delays to
        elapse. Per step: index, delay_days, scheduled_at (epoch + iso + days-from-
        first), status, subject, a body preview, and an inferred content source
        (original / bump / writer / breakup) corroborated by content signatures.
        Plus a one-glance verdict vs the expected 5-touch cadence: step count, day
        spacing, source sequence, whether the writer slots are DISTINCT, and whether
        the bump actually reuses the original body. Read-only, token-gated.
        Params: &workflow_id=<id>.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        wfid = (request.query_params.get("workflow_id") or "").strip()
        out = {"build_marker": getattr(push, "BUILD_MARKER", None), "workflow_id": wfid}
        if not wfid:
            out["error"] = "add &workflow_id=<id>"
            return out
        wf = _store.load(wfid)
        if wf is None:
            out["error"] = "no workflow with that id"
            return out
        out.update(_cadence_report(wf))
        return out

    @app.get("/api/debug/campaign-workflows")
    def campaign_workflows_debug(request: Request):
        """Look up campaigns (token-gated, no Clerk session needed).

        With &name: match that campaign by name (case/space-insensitive) and chain
        straight into the per-workflow cadence breakdown so one URL gives the full
        verdict. WITHOUT &name: LIST all campaigns (most-recently-created first) —
        id, stored name, status, workflow_ids — so you can find the real stored
        name/id (the stored name is whatever was typed at creation, NOT the URL
        slug). Read-only. Params: [&name=<campaign name>] [&steps=0 omits breakdown].
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        from server.campaign_store import CampaignStore
        name = (request.query_params.get("name") or "").strip()
        with_steps = (request.query_params.get("steps") or "1").strip() != "0"
        out = {"build_marker": getattr(push, "BUILD_MARKER", None),
               "mode": "lookup" if name else "list", "name_queried": name or None}
        cs = CampaignStore()
        try:
            if name:
                rows = cs.db.query(
                    "SELECT * FROM campaigns WHERE lower(trim(name))=lower(trim(?)) "
                    "ORDER BY updated_at DESC", (name,))
            else:
                # No name -> list ALL campaigns so you can find the real name/id.
                # Step breakdown is omitted in list mode to keep the payload small.
                rows = cs.db.query("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100")
                with_steps = False
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        if not rows:
            out["error"] = ("no campaigns found at all" if not name
                            else "no campaign with that name (omit &name to LIST all)")
            return out
        campaigns = []
        for row in rows:
            c = cs._row(row)
            entry = {"campaign_id": c["id"], "name": c["name"], "status": c["status"],
                     "owner_tail": (c["owner"] or "")[-8:], "workflow_ids": c["workflow_ids"],
                     "created_at": c["created_at"], "updated_at": c["updated_at"]}
            if with_steps:
                breakdown = []
                for wfid in c["workflow_ids"]:
                    wf = _store.load(wfid)
                    breakdown.append({"workflow_id": wfid, "error": "workflow not found"}
                                     if wf is None else _cadence_report(wf))
                entry["workflows"] = breakdown
            campaigns.append(entry)
        out["campaigns_matched"] = len(campaigns)
        if len(campaigns) > 1:
            out["note"] = ("more than one campaign shares this name; most-recently-"
                           "updated first. Use the campaign_id you expect.")
        out["campaigns"] = campaigns
        return out

    @app.get("/api/debug/campaign-followups")
    def campaign_followups_debug(request: Request):
        """Explain, from PERSISTED campaign data, why a prospect's mid-cadence
        follow-ups (slots 2 & 3) are present or missing — no create-time logs needed.
        Per prospect: final_status, whether a MANUAL recipient was used, the original
        email's guard decision, surviving follow-ups (slot + guard_decision), and a
        diagnosis distinguishing:
          * follow-ups generated & survived,
          * MANUAL-recipient prospect -> follow-ups NEVER generated (code gap; they
            are only generated at preview for auto-'sendable' prospects),
          * auto-'sendable' but 0 survivors -> both dropped at create time (writer
            non-ok or guard BLOCK); exact reason only in create-time logs,
          * not sendable at preview -> never generated.
        Read-only, token-gated. Params: &campaign_id=<id> OR &name=<name>.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        from server.campaign_store import CampaignStore
        # Same pure helper the real GET /api/campaigns/{id} runs (via
        # _result_with_warnings), so the cadence_warning reported below is an exact
        # mirror of what that Clerk-gated endpoint attaches — no DevTools needed.
        from server.campaign_api import _cadence_warning
        cid = (request.query_params.get("campaign_id") or "").strip()
        name = (request.query_params.get("name") or "").strip()
        out = {"build_marker": getattr(push, "BUILD_MARKER", None),
               "campaign_id": cid or None, "name_queried": name or None}
        if not cid and not name:
            out["error"] = "add &campaign_id=<id> or &name=<campaign name>"
            return out
        cs = CampaignStore()
        try:
            if cid:
                rows = cs.db.query("SELECT * FROM campaigns WHERE id=?", (cid,))
            else:
                rows = cs.db.query(
                    "SELECT * FROM campaigns WHERE lower(trim(name))=lower(trim(?)) "
                    "ORDER BY updated_at DESC", (name,))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        if not rows:
            out["error"] = "no matching campaign"
            return out
        results = []
        for row in rows:
            c = cs._row(row)
            prospects = []
            for p in (c.get("result") or {}).get("prospects") or []:
                email = p.get("email") or {}
                recipient = email.get("recipient") or p.get("recipient") or {}
                fups = p.get("followups") or []
                nf = len(fups)
                fs = p.get("final_status")
                manual = bool(p.get("manual_recipient")) or bool(recipient.get("manual"))
                if nf >= 1:
                    diagnosis = f"{nf} follow-up(s) generated and cleared guard."
                elif manual:
                    diagnosis = ("0 follow-ups: prospect got a MANUAL recipient. Follow-ups "
                                 "are generated only at preview for auto-'sendable' prospects, "
                                 "so the manual path never generated them. CODE GAP, not a "
                                 "guard block — launches as 3 steps.")
                elif fs == "sendable":
                    diagnosis = ("0 follow-ups despite auto-'sendable': both slots dropped at "
                                 "create time (writer non-ok, or guard BLOCK after one retry). "
                                 "Exact reason is only in create-time logs — grep "
                                 "campaign_followup_blocked / campaign_followup_writer_failed / "
                                 "campaign_followup_guard_failed for this campaign's trace/domain.")
                else:
                    diagnosis = (f"final_status={fs!r}: not sendable at preview, so follow-ups "
                                 "were never generated.")
                cw = _cadence_warning(p)  # EXACT value GET /api/campaigns/{id} attaches
                prospects.append({
                    "domain": p.get("domain") or p.get("website"),
                    "final_status": fs,
                    "manual_recipient": manual,
                    "guard_decision": (p.get("guard") or {}).get("decision"),
                    "followup_count": nf,
                    "followups": [{"slot": f.get("slot"),
                                   "guard_decision": f.get("guard_decision"),
                                   "subject": f.get("subject")} for f in fups],
                    "cadence_steps_at_launch": 3 + nf,      # original + bump + writers + breakup
                    "diagnosis": diagnosis,
                    # Faithful mirror of GET /api/campaigns/{id}: non-null => the real
                    # endpoint includes a "cadence_warning" key on this prospect (the amber
                    # badge renders once the frontend build is also live); null => the real
                    # endpoint omits the key. This isolates backend/data from the frontend.
                    "cadence_warning": cw,
                    "cadence_warning_in_api": cw is not None,
                })
            results.append({"campaign_id": c["id"], "name": c["name"], "status": c["status"],
                            "workflow_ids": c["workflow_ids"], "prospects": prospects})
        out["campaigns"] = results
        return out
