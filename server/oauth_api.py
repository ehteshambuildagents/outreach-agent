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
                       user: str = Depends(require_user)):
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
    @app.get("/api/debug/gmail-state")
    def gmail_debug_state(request: Request):
        """One URL to diagnose reply detection without reading deploy logs.

        Reports which push.py is live (BUILD_MARKER + commit), the stored
        watch_state, a LIVE history.list at the stored history_id, this user's
        workflow provider_thread_ids, and the exact reply-thread vs stored-thread
        comparison ingest_reply performs. Read-only: never advances history_id,
        never marks a message processed. Remove once reply detection is confirmed.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        email = (request.query_params.get("email") or "").strip().lower()
        out = {
            "build_marker": getattr(push, "BUILD_MARKER", None),
            "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
            "email_queried": email,
        }
        if not email:
            out["error"] = "add &email=<your connected gmail address> to the URL"
            return out
        accounts = _tokens.accounts_by_email("gmail", email)
        out["accounts_matched"] = len(accounts)
        if not accounts:
            out["error"] = "no connected gmail account for that email"
            return out
        acct = accounts[0]
        user_id, account_email = acct["user_id"], acct["account_email"]
        ws = acct.get("watch_state") or {}
        start = ws.get("history_id") or ws.get("historyId")
        out["account"] = {
            "account_email": account_email,
            "watch_state": {"history_id": ws.get("history_id"),
                            "historyId": ws.get("historyId"),
                            "expiration": ws.get("expiration"),
                            "keys": sorted(ws.keys())},
            "start_history_id_used": start,
        }
        token = _tokens.valid_access_token(user_id, "gmail", account_email)
        out["has_valid_token"] = bool(token)

        # LIVE, read-only history.list at the stored seed (same seed the handler uses).
        hl = {"start_history_id": start}
        if not token:
            hl["error"] = "no valid token (reconnect required)"
        elif not start:
            hl["error"] = "no stored history_id — watch not armed yet"
        else:
            try:
                hist = push.get_provider("gmail", credentials=token).list_history(start)
                msgs = hist.get("messages", [])
                hl.update(ok=True, new_history_id=hist.get("history_id"),
                          message_count=len(msgs),
                          messages=[{"message_id": m.get("message_id"),
                                     "thread_id": m.get("thread_id"),
                                     "labels": m.get("labels")} for m in msgs[:25]])
            except Exception as exc:  # noqa: BLE001
                hl.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        out["history_list"] = hl

        # Stored provider_thread_ids for this user (what ingest_reply matches against).
        stored_threads, wf_rows = set(), []
        for cand in _store.list_for_user(user_id):
            threads = [getattr(s, "provider_thread_id", None) for s in cand.steps]
            if not any(threads):
                continue                       # nothing sent yet -> can't match a reply
            stored_threads.update(t for t in threads if t)
            wf_rows.append({"workflow_id": cand.id, "state": str(cand.state),
                            "step_thread_ids": threads,
                            "step_status": [str(getattr(s, "status", None))
                                            for s in cand.steps]})
        out["workflows_with_sends"] = wf_rows[:25]

        # The exact comparison ingest_reply does (string equality), shown both ways.
        reply_threads = sorted({m["thread_id"] for m in hl.get("messages", [])
                                if m.get("thread_id")
                                and "SENT" not in (m.get("labels") or [])})
        matches = sorted(set(reply_threads) & stored_threads)
        out["thread_match"] = {
            "reply_thread_ids": reply_threads,
            "stored_provider_thread_ids": sorted(stored_threads),
            "matches": matches,
            "any_match": bool(matches),
            "verdict": ("reply thread MATCHES a sent workflow — ingest_reply should stop it"
                        if matches else
                        "no overlap — either no reply in this history window, "
                        "or the reply's thread_id differs from the stored one"),
        }
        return out

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

    @app.get("/api/debug/gmail-history-raw")
    def gmail_debug_history_raw(request: Request):
        """Show the RAW Gmail history.list records for an account, UNFILTERED by
        historyTypes — so label/read/delete changes that the messageAdded-only
        production query hides are visible. Answers 'the historyId advanced but
        message_count is 0 — what actually changed?'. Also runs the messageAdded-
        only query for side-by-side comparison, and follows nextPageToken (which
        the production list_history does NOT). Read-only, token-gated. Temporary.

        Params: &email=<connected gmail>  [&start_history_id=<override>].
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        email = (request.query_params.get("email") or "").strip().lower()
        out = {"build_marker": getattr(push, "BUILD_MARKER", None), "email_queried": email}
        if not email:
            out["error"] = "add &email=<your connected gmail address> to the URL"
            return out
        accounts = _tokens.accounts_by_email("gmail", email)
        if not accounts:
            out["error"] = "no connected gmail account for that email"
            return out
        acct = accounts[0]
        user_id, account_email = acct["user_id"], acct["account_email"]
        ws = acct.get("watch_state") or {}
        start = (request.query_params.get("start_history_id")
                 or ws.get("history_id") or ws.get("historyId"))
        out["start_history_id_used"] = start
        token = _tokens.valid_access_token(user_id, "gmail", account_email)
        out["has_valid_token"] = bool(token)
        if not token:
            out["error"] = "no valid token (reconnect required) — cannot query Gmail"
            return out
        if not start:
            out["error"] = "no start history id (watch not armed) — pass &start_history_id="
            return out
        prov = push.get_provider("gmail", credentials=token)
        try:
            all_types = prov.history_raw(start, history_types=None)      # every change type
            added_only = prov.history_raw(start, history_types="messageAdded")
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        all_types["records"] = all_types["records"][:50]                 # cap payload
        added_only["records"] = added_only["records"][:50]
        out["all_types"] = all_types
        out["message_added_only"] = added_only
        added_n = all_types["type_counts"].get("messagesAdded", 0)
        out["verdict"] = (
            f"{added_n} messageAdded record(s) in this window — production SHOULD "
            "surface these; if message_count was 0, suspect pagination or parsing."
            if added_n else
            "0 messageAdded records — the historyId advance was label/read/delete "
            "changes (see type_counts), NOT a new inbound message. message_count=0 "
            "is correct here; a real reply would show a messagesAdded record.")
        return out

    @app.get("/api/debug/gmail-thread")
    def gmail_debug_thread(request: Request):
        """Ground truth per thread, independent of history windows/checkpoints.

        For each &thread_id (comma-separated), calls Gmail threads.get and lists
        every message + labelIds (SENT vs received), so you can SEE whether an
        inbound reply exists — regardless of timing or checkpoint state. Then, for
        the same thread, reports the STORED ingest_reply outcome we can check
        directly rather than re-derive: the matching workflow's state (STOPPED?),
        reply_detected / reply_at / reply_message_id, its reply+stopped events, and
        whether the reply message id is in the durable `processed` ledger. This
        distinguishes 'Gmail has a reply AND production stopped it' from a real
        miss. Read-only, token-gated. Params: &email=<gmail> &thread_id=<id[,id]>.
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        email = (request.query_params.get("email") or "").strip().lower()
        thread_ids = [t.strip() for t in
                      (request.query_params.get("thread_id") or "").split(",") if t.strip()]
        out = {"build_marker": getattr(push, "BUILD_MARKER", None),
               "email_queried": email, "thread_ids": thread_ids}
        if not email or not thread_ids:
            out["error"] = "add &email=<connected gmail>&thread_id=<id[,id,...]>"
            return out
        accounts = _tokens.accounts_by_email("gmail", email)
        if not accounts:
            out["error"] = "no connected gmail account for that email"
            return out
        user_ids = sorted({a["user_id"] for a in accounts})
        out["user_id_tails_for_email"] = [u[-8:] for u in user_ids]
        token = _tokens.valid_access_token(accounts[0]["user_id"], "gmail",
                                           accounts[0]["account_email"])
        out["has_valid_token"] = bool(token)
        prov = push.get_provider("gmail", credentials=token) if token else None

        # Preload every workflow for the email's user(s) once, then match by thread.
        wfs = [wf for uid in user_ids for wf in _store.list_for_user(uid)]

        def _in_ledger(key):
            try:
                return bool(_store.db.query_one("SELECT 1 AS x FROM processed WHERE key=?", (key,)))
            except Exception as exc:  # noqa: BLE001
                return f"error: {type(exc).__name__}"

        results = {}
        for tid in thread_ids:
            entry = {}
            inbound_ids = []
            # 1) Gmail ground truth for the thread.
            if prov is None:
                entry["gmail"] = {"error": "no valid token (reconnect required)"}
            else:
                try:
                    th = prov.get_thread(tid)
                    inbound_ids = [m["message_id"] for m in th["messages"] if not m["is_sent"]]
                    entry["gmail"] = {
                        "message_count": th["message_count"],
                        "sent_count": sum(1 for m in th["messages"] if m["is_sent"]),
                        "inbound_count": len(inbound_ids),
                        "reply_present": bool(inbound_ids),
                        "messages": th["messages"],
                    }
                except Exception as exc:  # noqa: BLE001
                    entry["gmail"] = {"error": f"{type(exc).__name__}: {exc}"}
            # 2) Stored ingest_reply outcome for the workflow(s) on this thread.
            matched = [wf for wf in wfs
                       if any(s.provider_thread_id == tid for s in wf.steps)]
            wf_reports = []
            for wf in matched:
                evs = [e for e in _store.events_for(wf.id) if e["type"] in ("reply", "stopped")]
                wf_reports.append({
                    "workflow_id": wf.id,
                    "user_id_tail": (wf.user_id or "")[-8:],
                    "state": str(wf.state),
                    "stopped": str(wf.state) == "STOPPED",
                    "reply_detected": wf.reply_detected,
                    "reply_at": wf.reply_at,
                    "reply_message_id": wf.reply_message_id,
                    "steps": [{"status": str(s.status), "thread_id": s.provider_thread_id}
                              for s in wf.steps],
                    "reply_stopped_events": [{"ts": e["ts"], "type": e["type"],
                                              "detail": e["detail"]} for e in evs],
                })
            ledger_keys = {f"reply:{mid}": _in_ledger(f"reply:{mid}") for mid in inbound_ids}
            for wf in matched:
                if wf.reply_message_id:
                    ledger_keys[f"reply:{wf.reply_message_id}"] = _in_ledger(f"reply:{wf.reply_message_id}")
            entry["stored"] = {"matching_workflows": len(matched),
                               "workflows": wf_reports, "processed_ledger": ledger_keys}
            # 3) Plain-language verdict tying the two together.
            reply_here = entry.get("gmail", {}).get("reply_present")
            stopped_any = any(w["stopped"] for w in wf_reports)
            if reply_here and stopped_any:
                v = "Gmail HAS a reply AND a workflow is STOPPED with reply_detected — production caught it."
            elif reply_here and matched and not stopped_any:
                v = "Gmail HAS a reply but the matching workflow is NOT stopped — real miss (production did not stop it)."
            elif reply_here and not matched:
                v = "Gmail HAS a reply but NO workflow tracks this thread (untracked / different user_id)."
            elif reply_here is None:
                v = "Could not read Gmail (see gmail.error); stored state shown regardless."
            else:
                v = "No inbound reply in this thread per Gmail (only SENT messages)."
            entry["verdict"] = v
            results[tid] = entry
        out["results"] = results
        return out

    @app.api_route("/api/debug/gmail-reply-recover", methods=["GET", "POST"])
    def gmail_reply_recover(request: Request):
        """Recover replies lost to the old ingest_reply key-burning bug (b7f6d9c).

        Without &confirm = DRY RUN — resolves each &thread_id to its workflow +
               inbound reply message id and shows EXACTLY what would change; writes
               NOTHING.
        &confirm=1 = EXECUTE — for each resolved, un-stopped workflow: clears the
               burned `reply:<id>` ledger key, then re-runs the now-fixed
               ingest_reply (which stops the sequence, sets reply fields, writes the
               reply+stopped events). Strictly limited to the resolved workflows.
               Method-agnostic on purpose: a proxy can downgrade POST->GET, so the
               &confirm flag (not the HTTP verb) decides.

        Safety: default (no &confirm) never writes. If &workflow_ids=<a,b,..> is
        supplied, the resolved set MUST equal it exactly or the whole call aborts
        untouched — so nothing outside the ids you name is ever modified.
        Already-stopped workflows are skipped (idempotent). Token-gated. Params:
        &email= &thread_id=<id[,id]> [&workflow_ids=<id[,id]>] [&confirm=1].
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        from automation import engine
        email = (request.query_params.get("email") or "").strip().lower()
        thread_ids = [t.strip() for t in
                      (request.query_params.get("thread_id") or "").split(",") if t.strip()]
        expect_wf = sorted({w.strip() for w in
                            (request.query_params.get("workflow_ids") or "").split(",") if w.strip()})
        # Execute is gated by the &confirm flag ALONE — deliberately NOT by
        # request.method. A platform/proxy redirect (http->https, host or trailing
        # slash) can downgrade POST->GET, which silently trapped every execute in
        # dry-run. Safety still holds: token gate + &workflow_ids allowlist +
        # idempotency (already-stopped workflows are skipped).
        confirm_raw = request.query_params.get("confirm")
        is_execute = (confirm_raw or "").strip().lower() in ("1", "true", "yes")
        out = {"mode": "execute" if is_execute else "dry_run",
               "email_queried": email, "thread_ids": thread_ids,
               "workflow_ids_allowlist": expect_wf or None,
               "received_method": request.method,        # server-side truth (was POST downgraded?)
               "received_confirm_param": confirm_raw}
        if not email or not thread_ids:
            out["error"] = ("add &email=<gmail>&thread_id=<id[,id,...]>; GET previews, "
                            "POST with &confirm=1 executes")
            return out
        accounts = _tokens.accounts_by_email("gmail", email)
        if not accounts:
            out["error"] = "no connected gmail account for that email"
            return out
        user_ids = sorted({a["user_id"] for a in accounts})
        token = _tokens.valid_access_token(accounts[0]["user_id"], "gmail",
                                           accounts[0]["account_email"])
        prov = push.get_provider("gmail", credentials=token) if token else None
        wfs = [wf for uid in user_ids for wf in _store.list_for_user(uid)]

        def _events_ct(wfid):
            return [{"type": e["type"], "detail": e["detail"]}
                    for e in _store.events_for(wfid) if e["type"] in ("reply", "stopped")]

        plans, resolved_ids = [], []
        for tid in thread_ids:
            plan = {"thread_id": tid, "reply_message_id": None, "workflows": []}
            mid = None
            if prov is None:
                plan["gmail_error"] = "no valid token (reconnect required)"
            else:
                try:
                    th = prov.get_thread(tid)
                    inbound = [m for m in th["messages"] if not m["is_sent"]]
                    mid = inbound[0]["message_id"] if inbound else None
                    plan["reply_present"] = bool(inbound)
                    plan["reply_message_id"] = mid
                except Exception as exc:  # noqa: BLE001
                    plan["gmail_error"] = f"{type(exc).__name__}: {exc}"
            for wf in [w for w in wfs if any(s.provider_thread_id == tid for s in w.steps)]:
                resolved_ids.append(wf.id)
                already = str(wf.state) == "STOPPED" or wf.reply_detected
                will = (not already) and bool(mid)
                plan["workflows"].append({
                    "workflow_id": wf.id,
                    "user_id_tail": (wf.user_id or "")[-8:],
                    "before": {"state": str(wf.state), "stopped": str(wf.state) == "STOPPED",
                               "reply_detected": wf.reply_detected,
                               "reply_message_id": wf.reply_message_id,
                               "reply_stopped_events": _events_ct(wf.id)},
                    "will_change": will,
                    "planned_change": ({"state": "STOPPED", "reply_detected": True,
                                        "reply_message_id": mid,
                                        "adds_events": ["reply", "stopped"],
                                        "skips_pending_steps": True} if will else None),
                    "skip_reason": (None if will else
                                    "already stopped / reply_detected" if already else
                                    "no reply message id resolved from Gmail"),
                })
            plans.append(plan)
        out["plan"] = plans
        out["resolved_workflow_ids"] = sorted(set(resolved_ids))
        out["will_change_count"] = sum(1 for p in plans for w in p["workflows"] if w["will_change"])

        if expect_wf and expect_wf != sorted(set(resolved_ids)):
            out["error"] = "ABORT: resolved workflow set != &workflow_ids allowlist; nothing executed"
            return out
        out["allowlist_ok"] = bool(expect_wf) or None

        if not is_execute:
            out["note"] = ("DRY RUN — nothing written. Review resolved_workflow_ids + each "
                           "planned_change, then re-run the SAME URL with &confirm=1 to execute "
                           "(any HTTP method; add &workflow_ids=<those ids> to hard-pin the set).")
            return out

        results = []
        for plan in plans:
            tid, mid = plan["thread_id"], plan.get("reply_message_id")
            for wc in plan["workflows"]:
                wfid = wc["workflow_id"]
                if not wc["will_change"]:
                    results.append({"workflow_id": wfid, "thread_id": tid,
                                    "action": "skipped", "reason": wc["skip_reason"]})
                    continue
                owner = next((w.user_id for w in wfs if w.id == wfid), None)
                # force=True: these workflows ran to COMPLETED (a terminal state), so
                # the default ingest_reply guard would silently no-op. Recovery must
                # record the reply on a finished sequence.
                try:
                    _store.db.execute("DELETE FROM processed WHERE key=?", (f"reply:{mid}",))
                    returned = engine.ingest_reply(_store, message_id=mid, workflow_id=wfid,
                                                   user_id=owner, thread_id=tid, force=True)
                except Exception as exc:  # noqa: BLE001
                    results.append({"workflow_id": wfid, "thread_id": tid,
                                    "action": "error", "detail": f"{type(exc).__name__}: {exc}"})
                    continue
                r = _store.load(wfid, user_id=owner)
                did_stop = bool(r) and str(r.state) == "STOPPED" and r.reply_detected
                results.append({
                    "workflow_id": wfid, "thread_id": tid, "reply_message_id": mid,
                    # Raw signal: what ingest_reply actually returned (state, or None).
                    "ingest_reply_returned": (str(returned.state) if returned is not None else None),
                    "action": ("recovered" if did_stop else "no_change"),
                    "before": wc["before"],
                    "after": {"state": str(r.state) if r else None,
                              "stopped": (str(r.state) == "STOPPED") if r else None,
                              "reply_detected": r.reply_detected if r else None,
                              "reply_message_id": r.reply_message_id if r else None,
                              "reply_stopped_events": _events_ct(wfid)},
                })
        out["results"] = results
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
        """Look up a campaign BY NAME (token-gated, no Clerk session needed) and
        return its id + workflow_ids, chaining straight into the per-workflow cadence
        breakdown so one URL gives the full verdict. Read-only. Params: &name=
        <campaign name> [&steps=0 to omit the step breakdown].
        """
        if not _debug_token_ok(request):
            raise HTTPException(status_code=404, detail="Not found.")
        from server.campaign_store import CampaignStore
        name = (request.query_params.get("name") or "").strip()
        with_steps = (request.query_params.get("steps") or "1").strip() != "0"
        out = {"build_marker": getattr(push, "BUILD_MARKER", None), "name_queried": name}
        if not name:
            out["error"] = "add &name=<campaign name>"
            return out
        cs = CampaignStore()
        try:
            rows = cs.db.query(
                "SELECT * FROM campaigns WHERE lower(trim(name))=lower(trim(?)) "
                "ORDER BY updated_at DESC", (name,))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        if not rows:
            out["error"] = "no campaign with that name"
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
