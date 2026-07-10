"""OAuth 2.0 (Authorization Code) for Google and Microsoft — the consent flow.

Pure transport over each provider's OAuth endpoints, plus CSRF ``state`` handling.
No secrets ever reach the browser: the client secret is used only server-side in
:func:`exchange_code` / :func:`refresh`. The flow is:

    login    -> build_authorize_url(state)      # redirect user to the provider
    callback -> exchange_code(code)              # code -> access + refresh tokens
             -> account_email(access_token)      # which mailbox did they connect?
    later    -> refresh(refresh_token)           # rotate/renew before expiry
    disconnect -> revoke(refresh_token)          # best-effort provider revoke

``state`` is a single-use, TTL'd random token bound to the Clerk user in Redis, so
a forged callback (CSRF) cannot connect an attacker's mailbox to a victim, nor
replay an old code. Requesting ``offline_access`` / ``access_type=offline`` is what
yields a refresh token; Microsoft rotates the refresh token on every refresh and
Google keeps it — :mod:`automation.tokens` handles both.
"""

import os
import secrets

import requests

from automation import redis
from config.settings import AUTOMATION_OAUTH_STATE_TTL

_TIMEOUT = 30


class OAuthError(Exception):
    """OAuth exchange/refresh failed (bad code, revoked grant, provider error)."""


# ── Provider configuration ─────────────────────────────────────────────
def _ms_tenant() -> str:
    return os.environ.get("MICROSOFT_TENANT_ID", "common")


PROVIDERS = {
    "gmail": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "revoke": "https://oauth2.googleapis.com/revoke",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ("openid email https://www.googleapis.com/auth/gmail.send "
                   "https://www.googleapis.com/auth/gmail.readonly"),
        "client_id_env": "GOOGLE_CLIENT_ID",
        "client_secret_env": "GOOGLE_CLIENT_SECRET",
        "redirect_env": "GOOGLE_REDIRECT_URI",
        "extra_auth": {"access_type": "offline", "prompt": "consent",
                       "include_granted_scopes": "true"},
    },
    "outlook": {
        "authorize": None,   # filled per-call (tenant-dependent)
        "token": None,
        "userinfo": "https://graph.microsoft.com/v1.0/me",
        "scopes": ("openid offline_access User.Read Mail.Send Mail.Read"),
        "client_id_env": "MICROSOFT_CLIENT_ID",
        "client_secret_env": "MICROSOFT_CLIENT_SECRET",
        "redirect_env": "MICROSOFT_REDIRECT_URI",
        "extra_auth": {"response_mode": "query"},
    },
}


def _cfg(provider: str) -> dict:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise OAuthError(f"unknown oauth provider: {provider}")
    cfg = dict(cfg)
    if provider == "outlook":
        base = f"https://login.microsoftonline.com/{_ms_tenant()}/oauth2/v2.0"
        cfg["authorize"] = f"{base}/authorize"
        cfg["token"] = f"{base}/token"
    return cfg


def client_id(provider: str) -> str:
    return os.environ.get(_cfg(provider)["client_id_env"], "")


def _client_secret(provider: str) -> str:
    return os.environ.get(_cfg(provider)["client_secret_env"], "")


def configured(provider: str) -> bool:
    return bool(client_id(provider) and _client_secret(provider))


def redirect_uri(provider: str, default: str = "") -> str:
    return os.environ.get(_cfg(provider)["redirect_env"], "") or default


# ── CSRF state (single-use, TTL'd, user-bound) ─────────────────────────
def make_state(user_id: str, provider: str, return_to: str = "/app.html") -> str:
    state = secrets.token_urlsafe(24)
    payload = f"{user_id}\x1f{provider}\x1f{return_to}"
    redis.set(f"oauthstate:{state}", payload, ex=AUTOMATION_OAUTH_STATE_TTL, nx=True)
    return state


def consume_state(state: str) -> dict:
    """Validate and single-use-consume a state token. Returns {user_id, provider,
    return_to} or None if unknown/expired/replayed."""
    if not state:
        return None
    key = f"oauthstate:{state}"
    payload = redis.get(key)
    if not payload:
        return None
    redis.delete(key)                          # single use — prevents replay
    parts = str(payload).split("\x1f")
    if len(parts) != 3:
        return None
    return {"user_id": parts[0], "provider": parts[1], "return_to": parts[2]}


# ── The flow ───────────────────────────────────────────────────────────
def build_authorize_url(provider: str, state: str, redirect: str) -> str:
    cfg = _cfg(provider)
    params = {
        "client_id": client_id(provider),
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": cfg["scopes"],
        "state": state,
        **cfg.get("extra_auth", {}),
    }
    from urllib.parse import urlencode
    return f"{cfg['authorize']}?{urlencode(params)}"


def exchange_code(provider: str, code: str, redirect: str) -> dict:
    cfg = _cfg(provider)
    data = {
        "code": code,
        "client_id": client_id(provider),
        "client_secret": _client_secret(provider),
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }
    return _token_request(cfg["token"], data)


def refresh(provider: str, refresh_token: str) -> dict:
    cfg = _cfg(provider)
    data = {
        "refresh_token": refresh_token,
        "client_id": client_id(provider),
        "client_secret": _client_secret(provider),
        "grant_type": "refresh_token",
    }
    if provider == "outlook":
        data["scope"] = cfg["scopes"]
    return _token_request(cfg["token"], data)


def _token_request(url: str, data: dict) -> dict:
    try:
        r = requests.post(url, data=data, timeout=_TIMEOUT,
                          headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise OAuthError(f"token endpoint unreachable: {type(exc).__name__}")
    if r.status_code != 200:
        # Never surface the provider's raw body (may echo secrets); keep it terse.
        raise OAuthError(f"token exchange failed (HTTP {r.status_code})")
    tok = r.json()
    if "access_token" not in tok:
        raise OAuthError("token response missing access_token")
    return tok


def account_email(provider: str, access_token: str) -> str:
    """Which mailbox was connected (for multi-account keys + display)."""
    cfg = _cfg(provider)
    try:
        r = requests.get(cfg["userinfo"],
                         headers={"Authorization": f"Bearer {access_token}"},
                         timeout=_TIMEOUT)
        if r.status_code != 200:
            return ""
        d = r.json()
    except requests.RequestException:
        return ""
    return (d.get("email") or d.get("mail") or d.get("userPrincipalName") or "").lower()


def revoke(provider: str, refresh_token: str) -> bool:
    """Best-effort provider-side revoke. Google supports a revoke endpoint;
    Microsoft has none for app tokens (local deletion is the effective revoke)."""
    cfg = _cfg(provider)
    if not cfg.get("revoke") or not refresh_token:
        return False
    try:
        r = requests.post(cfg["revoke"], data={"token": refresh_token},
                          timeout=_TIMEOUT)
        return r.status_code in (200, 204)
    except requests.RequestException:
        return False
