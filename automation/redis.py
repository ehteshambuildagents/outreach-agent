"""Ephemeral coordination via Upstash Redis (REST) — locks, dedup, rate limits.

Redis holds ONLY short-lived coordination state, never business data (that lives
in the SQLite store). Everything here has a TTL. When Upstash isn't configured
the module degrades to an in-process implementation so local dev and tests run
without a network — with identical semantics (single-process only, which is all
tests need).

Upstash's REST API takes a command as a JSON array (``["SET","k","v","EX","30"]``)
and returns ``{"result": ...}``. We only use a tiny, well-understood subset.
"""

import logging
import os
import threading
import time

import requests

log = logging.getLogger("automation.redis")

_TIMEOUT = 10


def _url():
    return (os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")


def _token():
    return (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()


def configured() -> bool:
    return bool(_url() and _token())


# ── Upstash REST command ───────────────────────────────────────────────
def _command(*args):
    """Run one Redis command via Upstash REST; returns the ``result`` or raises."""
    resp = requests.post(_url(), json=list(args),
                         headers={"Authorization": f"Bearer {_token()}"},
                         timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"redis error: {data['error']}")
    return data.get("result")


# ── In-memory fallback (single process; used when Upstash isn't configured) ──
class _MemoryStore:
    def __init__(self):
        self._d = {}                       # key -> (value, expires_at|None)
        self._lock = threading.Lock()

    def _live(self, key):
        v = self._d.get(key)
        if v is None:
            return None
        value, exp = v
        if exp is not None and exp < time.time():
            self._d.pop(key, None)
            return None
        return value

    def set(self, key, value, ex=None, nx=False):
        with self._lock:
            if nx and self._live(key) is not None:
                return None
            self._d[key] = (str(value), time.time() + ex if ex else None)
            return "OK"

    def get(self, key):
        with self._lock:
            return self._live(key)

    def delete(self, key):
        with self._lock:
            return 1 if self._d.pop(key, None) is not None else 0

    def ttl(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is None:
                return -2
            _val, exp = v
            if exp is None:
                return -1
            return max(0, int(exp - time.time()))

    def incr(self, key):
        with self._lock:
            cur = self._live(key)
            n = (int(cur) if cur else 0) + 1
            _v, exp = self._d.get(key, (None, None))
            self._d[key] = (str(n), exp)
            return n

    def incr_expiring(self, key, window):
        """Atomic INCR that arms the TTL when the key is (re)created — mirrors the
        Lua path so the very first hit always leaves a key that expires on its own."""
        with self._lock:
            cur = self._live(key)
            if cur is None:                    # absent or just expired -> new window
                n, exp = 1, time.time() + window
            else:
                n = int(cur) + 1
                _v, exp = self._d[key]         # keep the window's existing TTL
            self._d[key] = (str(n), exp)
            return n

    def expire(self, key, ex):
        with self._lock:
            if self._live(key) is None:
                return 0
            val, _exp = self._d[key]
            self._d[key] = (val, time.time() + ex)
            return 1

    def eval_del_if(self, key, token):
        with self._lock:
            if self._live(key) == token:
                self._d.pop(key, None)
                return 1
            return 0

    def clear(self):
        with self._lock:
            self._d.clear()


_mem = _MemoryStore()


# ── Public API (Upstash when configured, else in-memory) ───────────────
def set(key, value, ex=None, nx=False):
    if not configured():
        return _mem.set(key, value, ex=ex, nx=nx)
    args = ["SET", key, str(value)]
    if nx:
        args.append("NX")
    if ex:
        args += ["EX", str(int(ex))]
    return _command(*args)


def get(key):
    return _mem.get(key) if not configured() else _command("GET", key)


def delete(key):
    return _mem.delete(key) if not configured() else _command("DEL", key)


def ttl(key):
    return _mem.ttl(key) if not configured() else _command("TTL", key)


def incr(key):
    return _mem.incr(key) if not configured() else _command("INCR", key)


def expire(key, ex):
    return _mem.expire(key, ex) if not configured() else _command("EXPIRE", key, str(int(ex)))


# Atomic compare-and-delete for lock release (never drop someone else's lock).
_RELEASE_LUA = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end")


def _eval_del_if(key, token):
    if not configured():
        return _mem.eval_del_if(key, token)
    return _command("EVAL", _RELEASE_LUA, "1", key, token)


# INCR + first-hit EXPIRE as ONE atomic step. A plain INCR-then-EXPIRE can strand a
# TTL-less key if the EXPIRE call is dropped/fails after the INCR — the counter then
# never resets and blocks the caller forever. This never can.
_INCR_EXPIRE_LUA = ("local n = redis.call('INCR', KEYS[1]) "
                    "if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
                    "return n")


def incr_expiring(key, window) -> int:
    """Increment a fixed-window counter and guarantee it carries a TTL. Atomic on
    both backends: a single Lua eval on Upstash, a single locked op in memory."""
    window = int(window)
    if not configured():
        return _mem.incr_expiring(key, window)
    return int(_command("EVAL", _INCR_EXPIRE_LUA, "1", key, str(window)))


# ── Higher-level primitives ────────────────────────────────────────────
def acquire_lock(name, token, ttl_seconds=30) -> bool:
    """Best-effort distributed lock: SET name token NX EX. True if acquired."""
    try:
        return set(f"lock:{name}", token, ex=ttl_seconds, nx=True) == "OK"
    except Exception as exc:  # noqa: BLE001 - coordination must never crash a run
        log.warning("lock acquire failed for %s: %s", name, type(exc).__name__)
        return False


def release_lock(name, token) -> None:
    try:
        _eval_del_if(f"lock:{name}", token)
    except Exception as exc:  # noqa: BLE001
        log.warning("lock release failed for %s: %s", name, type(exc).__name__)


def seen_before(key, ttl_seconds=86400) -> bool:
    """Idempotency: True if this key was ALREADY recorded (duplicate), else
    records it and returns False. First caller wins."""
    try:
        first = set(f"seen:{key}", "1", ex=ttl_seconds, nx=True) == "OK"
        return not first
    except Exception as exc:  # noqa: BLE001 - fail OPEN would double-send, so on a
        # coordination error we treat as NOT seen but log; callers also have the
        # DB idempotency guard, which is the durable one.
        log.warning("dedup check failed for %s: %s", key, type(exc).__name__)
        return False


def rate_limited(bucket, limit, window_seconds) -> bool:
    """Fixed-window rate limit. True if the caller is OVER the limit.

    The counter and its TTL are set atomically (``incr_expiring``), so a failed or
    dropped EXPIRE can never strand a TTL-less key that counts up forever and locks
    the caller out permanently."""
    try:
        return incr_expiring(f"rl:{bucket}", window_seconds) > limit
    except Exception as exc:  # noqa: BLE001
        log.warning("rate check failed for %s: %s", bucket, type(exc).__name__)
        return False


def reset() -> None:
    """Clear in-memory state so a test starts isolated (locks, rate-limit windows,
    dedup keys). No-op against a real Upstash so it can never wipe live data."""
    if not configured():
        _mem.clear()
