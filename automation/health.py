"""Aggregate health for the Automation Agent — one honest snapshot.

Reports the live status of every dependency the conductor relies on: the SQLite
store, Redis coordination, each email provider's OAuth configuration, and the
background worker's heartbeat. "Honest" is the point — an unconfigured provider
says ``unconfigured`` (not ``ok``), and a stale worker says ``stale`` rather than
pretending sends are flowing. No secrets are ever included.
"""

import time

from automation import oauth, redis
from automation.store import WorkflowStore
from automation.tokens import TokenStore


def _redis_health() -> dict:
    try:
        probe = f"health:{time.time()}"
        redis.set(probe, "1", ex=5)
        ok = redis.get(probe) == "1"
        redis.delete(probe)
        mode = "upstash" if redis.configured() else "in-memory"
        return {"status": "ok" if ok else "error", "mode": mode}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}


def _db_health(store: WorkflowStore) -> dict:
    try:
        counts = store.count_by_state()
        return {"status": "ok", "by_state": counts}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}


def _provider_health(token_store: TokenStore, provider: str) -> dict:
    if not oauth.configured(provider):
        return {"status": "unconfigured",
                "detail": f"{provider} OAuth client id/secret not set"}
    connected = sum(1 for a in _all_accounts(token_store)
                    if a["provider"] == provider and a["status"] == "connected")
    reconnect = sum(1 for a in _all_accounts(token_store)
                    if a["provider"] == provider and a["status"] == "reconnect_required")
    if connected:
        return {"status": "ok", "connected_accounts": connected,
                "reconnect_required": reconnect}
    return {"status": "no_accounts",
            "detail": "OAuth client configured; no user has connected an account yet",
            "reconnect_required": reconnect}


_ACCOUNTS_CACHE = {"ts": 0.0, "rows": []}


def _all_accounts(token_store: TokenStore) -> list:
    """All accounts across users (health only) — read straight from the table."""
    rows = token_store.db.query("SELECT provider, status FROM oauth_accounts")
    return [{"provider": r["provider"], "status": r["status"]} for r in rows]


def _worker_health(worker) -> dict:
    if worker is None:
        return {"status": "unknown", "detail": "no worker handle in this process"}
    if not getattr(worker, "running", False):
        return {"status": "stopped"}
    last = getattr(worker, "last_tick_at", 0.0)
    if last == 0.0:
        return {"status": "starting"}
    age = time.time() - last
    stale_after = max(60, getattr(worker, "tick_interval", 15) * 3)
    return {"status": "ok" if age < stale_after else "stale",
            "seconds_since_tick": round(age, 1)}


def snapshot(store: WorkflowStore = None, token_store: TokenStore = None,
             worker=None) -> dict:
    store = store or WorkflowStore()
    token_store = token_store or TokenStore()
    checks = {
        "database": _db_health(store),
        "redis": _redis_health(),
        "gmail": _provider_health(token_store, "gmail"),
        "outlook": _provider_health(token_store, "outlook"),
        "worker": _worker_health(worker),
    }
    # Overall is the worst non-informational status. "unconfigured"/"no_accounts"/
    # "unknown" are acceptable (not failures) for an MVP without live OAuth yet.
    degraded = any(c.get("status") in ("error", "stale", "stopped")
                   for c in checks.values())
    return {"status": "degraded" if degraded else "ok", "checks": checks}
