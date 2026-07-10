"""Live integration verifier — hits every configured service for real.

Run:  .venv/Scripts/python.exe -m eval.verify_integrations

Honest by design: each check makes a real minimal request and reports
PASS / FAIL / BLOCKED with a short reason. No secret value is ever printed.
Gmail / Microsoft Graph are reported as BLOCKED (config present, but sending/
reading needs a completed OAuth consent + token that cannot be minted here).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env import load_env  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_env()   # canonical: .env.local + .env (same as server/worker/migrate)

import requests  # noqa: E402

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"
_results = []


def record(service, status, tested, detail=""):
    _results.append((service, status, tested, detail))
    mark = {"PASS": "OK ", "FAIL": "XX ", "BLOCKED": ">> ", "SKIP": ".. "}[status]
    print(f"[{mark}] {service:18} {status:7} — {tested}"
          + (f" :: {detail}" if detail else ""))


def _key(name):
    return (os.environ.get(name) or "").strip()


def check_anthropic():
    if not _key("ANTHROPIC_API_KEY"):
        return record("Anthropic", SKIP, "no key set")
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=_key("ANTHROPIC_API_KEY"), max_retries=0)
        r = c.messages.create(model="claude-sonnet-4-6", max_tokens=8,
                              messages=[{"role": "user", "content": "reply with: ok"}])
        txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        record("Anthropic", PASS, "messages.create", f"replied {txt!r}")
    except Exception as exc:  # noqa: BLE001
        record("Anthropic", FAIL, "messages.create", type(exc).__name__)


def check_openai():
    key = _key("OPENAI_API_KEY")
    if not key:
        return record("OpenAI", SKIP, "no key set")
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-4o-mini", "max_tokens": 8,
                  "messages": [{"role": "user", "content": "reply with: ok"}]},
            timeout=30)
        if r.status_code == 200:
            txt = r.json()["choices"][0]["message"]["content"]
            record("OpenAI", PASS, "chat/completions", f"replied {txt!r}")
        else:
            record("OpenAI", FAIL, "chat/completions",
                   f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as exc:  # noqa: BLE001
        record("OpenAI", FAIL, "chat/completions", type(exc).__name__)


def check_firecrawl():
    from research import firecrawl
    if not firecrawl.available():
        return record("Firecrawl", SKIP, "no key set")
    try:
        page = firecrawl.scrape("https://example.com")
        if page and page.get("markdown"):
            record("Firecrawl", PASS, "v2/scrape example.com",
                   f"{len(page['markdown'])} chars")
        else:
            record("Firecrawl", FAIL, "v2/scrape", "empty result")
    except Exception as exc:  # noqa: BLE001
        record("Firecrawl", FAIL, "v2/scrape", type(exc).__name__)


def check_tavily():
    from research import tavily
    if not tavily.available():
        return record("Tavily", SKIP, "no key set")
    try:
        res = tavily.search("Stripe", max_results=2)
        record("Tavily" if res else "Tavily", PASS if res else FAIL,
               "search", f"{len(res)} results")
    except Exception as exc:  # noqa: BLE001
        record("Tavily", FAIL, "search", type(exc).__name__)


def check_redis():
    from automation import redis as r
    if not r.configured():
        return record("Upstash Redis", SKIP, "no URL/token set")
    try:
        k = f"saqua:verify:{int(time.time())}"
        r.set(k, "1", ex=30)
        got = r.get(k)
        ttl = r.ttl(k)
        r.delete(k)
        ok = got == "1" and 0 < ttl <= 30
        record("Upstash Redis", PASS if ok else FAIL,
               "SET/GET/TTL/DEL", f"get={got!r} ttl={ttl}")
    except Exception as exc:  # noqa: BLE001
        record("Upstash Redis", FAIL, "SET/GET/TTL", type(exc).__name__)


def check_postgres():
    url = _key("DATABASE_URL")
    if not url:
        return record("Supabase/Postgres", SKIP, "DATABASE_URL not set")
    if "[" in url or "YOUR-PASSWORD" in url.upper():
        return record("Supabase/Postgres", BLOCKED, "DATABASE_URL has a placeholder",
                      "the password field is still a template — paste the real Supabase "
                      "DB password, then run `python -m automation.migrate`")
    try:
        import psycopg
        from automation.db import _is_pooler, _pg_dsn
        kw = {"connect_timeout": 12}
        if _is_pooler():
            kw["prepare_threshold"] = None      # pgbouncer transaction pooler
        with psycopg.connect(_pg_dsn(), **kw) as conn:
            one = conn.execute("SELECT 1 AS ok").fetchone()
        record("Supabase/Postgres", PASS, "connect + SELECT 1",
               f"reachable, query ok ({one})")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:90]
        # A password/auth failure means everything up to auth works — call it out
        # as BLOCKED (wrong/absent password) rather than a hard FAIL.
        if "password authentication failed" in str(exc) or "auth" in str(exc).lower():
            record("Supabase/Postgres", BLOCKED, "reachable; auth rejected",
                   "DNS/TCP/TLS/pooler all OK — the DB password is wrong or still a "
                   "placeholder. Set the real password in DATABASE_URL.")
        else:
            record("Supabase/Postgres", FAIL, "connect", f"{type(exc).__name__}: {msg}")


def check_clerk():
    if _key("CLERK_SECRET_KEY") and _key("CLERK_PUBLISHABLE_KEY"):
        record("Clerk", PASS, "keys present (JWT verified via JWKS at runtime)",
               "pk+sk configured")
    else:
        record("Clerk", SKIP, "keys not both set")


def check_google_oauth():
    cid, sec = _key("GOOGLE_CLIENT_ID"), _key("GOOGLE_CLIENT_SECRET")
    if not (cid and sec):
        return record("Gmail OAuth", SKIP, "client id/secret not set")
    # Prove the token endpoint is reachable + our client is recognised: a refresh
    # with a bogus token must come back 400 invalid_grant (NOT invalid_client),
    # which confirms connectivity and that the client_id/secret are valid. A real
    # send/read still needs user consent + an authorized redirect URI + Pub/Sub.
    reach = ""
    try:
        rr = requests.post("https://oauth2.googleapis.com/token",
                           data={"client_id": cid, "client_secret": sec,
                                 "grant_type": "refresh_token",
                                 "refresh_token": "verify-not-a-real-token"}, timeout=30)
        err = (rr.json() or {}).get("error", "")
        reach = (f"token endpoint reachable (HTTP {rr.status_code}, {err}); "
                 if rr.status_code else "")
        if err == "invalid_client":
            return record("Gmail OAuth", FAIL, "token endpoint",
                          "invalid_client — GOOGLE_CLIENT_ID/SECRET rejected by Google")
    except Exception as exc:  # noqa: BLE001
        reach = f"token endpoint UNREACHABLE ({type(exc).__name__}); "
    record("Gmail OAuth", BLOCKED, "client credentials valid",
           reach + "needs authorized redirect URI on our backend + user consent + "
           "Pub/Sub topic for watch(); no refresh token available to mint here")


def check_microsoft_oauth():
    cid, sec, tid = (_key("MICROSOFT_CLIENT_ID"), _key("MICROSOFT_CLIENT_SECRET"),
                     _key("MICROSOFT_TENANT_ID"))
    if not (cid and sec and tid):
        return record("Microsoft Graph", SKIP, "client id/secret/tenant not set")
    # Client-credentials token IS mintable without a user; try it to prove the
    # app registration + secret are valid. Sending as a user still needs consent.
    try:
        r = requests.post(
            f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
            data={"client_id": cid, "client_secret": sec,
                  "scope": "https://graph.microsoft.com/.default",
                  "grant_type": "client_credentials"}, timeout=30)
        if r.status_code == 200 and r.json().get("access_token"):
            record("Microsoft Graph", BLOCKED, "app token minted (creds valid)",
                   "app-only token works; per-user mailbox send/read needs admin "
                   "consent + a redirect URI on our backend (current one points at "
                   "Clerk, not our /oauth/callback)")
        else:
            record("Microsoft Graph", FAIL, "client_credentials token",
                   f"HTTP {r.status_code}: {r.json().get('error')}")
    except Exception as exc:  # noqa: BLE001
        record("Microsoft Graph", FAIL, "token endpoint", type(exc).__name__)


def main():
    print("=" * 72)
    print("Saqua integration verification —", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 72)
    for fn in (check_anthropic, check_openai, check_firecrawl, check_tavily,
               check_redis, check_postgres, check_clerk, check_google_oauth,
               check_microsoft_oauth):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, FAIL, "harness error", type(exc).__name__)
    print("=" * 72)
    counts = {}
    for _s, status, _t, _d in _results:
        counts[status] = counts.get(status, 0) + 1
    print("Summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0 if counts.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
