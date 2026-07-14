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
        return push.handle_gmail_pubsub(_store, _tokens, envelope)

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
