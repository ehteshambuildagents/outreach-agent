"""Waitlist: double opt-in, abuse controls, and broadcast idempotency.

The abuse-control tests matter more here than in most modules: this is the app's
only unauthenticated write endpoint, so a regression in the rate limiter, the
honeypot, or the client-IP parsing is directly exploitable from the internet.
"""

import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")
# Force the in-memory coordination backend: these tests exercise the rate limiter
# heavily, and a real UPSTASH_* in .env would otherwise send that traffic to the
# shared production Redis.
#
# Set to empty rather than pop: config.env.load_env() calls load_dotenv(override=
# False) later in the import chain, which would re-populate a popped key straight
# from .env — leaving redis.configured() True and the whole module pointed at prod.
# A present-but-empty key is left untouched. Same reasoning as DATABASE_URL in
# tests/conftest.py.
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""

import waitlist                       # noqa: E402
from waitlist import broadcast, store  # noqa: E402
from waitlist import email as mailer   # noqa: E402
from automation.db import Database     # noqa: E402
from server import waitlist_api as W   # noqa: E402


class _Req:
    """Minimal stand-in for a Starlette Request (headers + client peer)."""

    def __init__(self, headers=None, host="10.0.0.1"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


def _fresh_db():
    """A temp DB passed EXPLICITLY as db=. For code you call directly."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store.reset_ensured()
    db = Database(sqlite_path=path)
    store.ensure(db, force=True)
    return db, path


def _fresh_default_db():
    """Point the DEFAULT Database() at a temp file.

    Endpoint tests need this rather than _fresh_db: the request path constructs its
    own Database() with no db= argument, so an explicitly-passed handle is never
    seen and the assertions would read a different file than the endpoint wrote.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["AUTOMATION_DB_PATH"] = path
    store.reset_ensured()
    store.ensure(Database(), force=True)
    return path


class ClientIpTests(unittest.TestCase):
    """Pinned against the two forwarding chains PRODUCTION actually produced, captured
    via /api/admin/echo-ip on 2026-07-19. The previous version of these tests passed
    on invented chains while production bucketed every visitor on infrastructure, so
    the literals below are deliberately the real measured values rather than tidy
    examples — if the topology changes, these should fail.

        direct  api.saqua.io  xff="116.71.134.159, 152.233.68.97"  real=116.71.134.159
        proxied www.saqua.io  xff="34.229.241.47, 152.233.47.67"   real=34.229.241.47
                              x-vercel-forwarded-for=116.71.134.159 (browser)

    In both, Railway's edge REWROTE x-forwarded-for to "<peer it saw>, <internal hop>".
    The rightmost entry is therefore never a client, at any hop count.
    """

    CLIENT = "116.71.134.159"        # the browser, both paths
    VERCEL_EGRESS = "34.229.241.47"  # our frontend's egress, proxied path only
    RAILWAY_HOP = "152.233.68.97"    # Railway internal, rightmost, never a client
    SECRET = "test-proxy-secret"

    def _direct(self, extra=None):
        h = {"x-forwarded-for": f"{self.CLIENT}, {self.RAILWAY_HOP}",
             "x-real-ip": self.CLIENT}
        h.update(extra or {})
        return _Req(h, host="100.64.0.4")

    def _proxied(self, extra=None):
        h = {"x-forwarded-for": f"{self.VERCEL_EGRESS}, 152.233.47.67",
             "x-real-ip": self.VERCEL_EGRESS}
        h.update(extra or {})
        return _Req(h, host="100.64.0.3")

    def test_direct_path_uses_the_real_caller(self):
        self.assertEqual(W.client_ip(self._direct()), self.CLIENT)

    def test_proxied_path_uses_the_ip_the_proxy_forwarded(self):
        with mock.patch.dict(os.environ, {"SAQUA_PROXY_SECRET": self.SECRET}):
            ip = W.client_ip(self._proxied({"x-saqua-client-ip": self.CLIENT,
                                            "x-saqua-proxy-secret": self.SECRET}))
        self.assertEqual(ip, self.CLIENT)

    def test_forwarded_ip_is_ignored_without_the_secret(self):
        """The whole point of the secret. api.saqua.io is publicly reachable, so an
        unauthenticated x-saqua-client-ip is an attacker choosing their own bucket."""
        with mock.patch.dict(os.environ, {"SAQUA_PROXY_SECRET": self.SECRET}):
            ip = W.client_ip(self._direct({"x-saqua-client-ip": "203.0.113.77"}))
        self.assertEqual(ip, self.CLIENT)

    def test_forwarded_ip_is_ignored_with_a_wrong_secret(self):
        with mock.patch.dict(os.environ, {"SAQUA_PROXY_SECRET": self.SECRET}):
            ip = W.client_ip(self._direct({"x-saqua-client-ip": "203.0.113.77",
                                           "x-saqua-proxy-secret": "wrong"}))
        self.assertEqual(ip, self.CLIENT)

    def test_no_secret_configured_means_nothing_is_ever_trusted(self):
        """Unset secret must not become a bypass: fall back to the peer."""
        with mock.patch.dict(os.environ, {"SAQUA_PROXY_SECRET": ""}):
            ip = W.client_ip(self._proxied({"x-saqua-client-ip": "203.0.113.77",
                                            "x-saqua-proxy-secret": ""}))
        self.assertEqual(ip, self.VERCEL_EGRESS)

    def test_rightmost_entry_is_never_used(self):
        """Regression guard on the actual bug: hop-counting from the right picked
        Railway's internal hop on every path, which is how all traffic ended up in
        infrastructure-keyed buckets."""
        for req in (self._direct(), self._proxied()):
            self.assertNotIn(W.client_ip(req), (self.RAILWAY_HOP, "152.233.47.67"))

    def test_forged_xff_cannot_override_x_real_ip(self):
        """Measured in production: Railway discards a caller's x-forwarded-for. We
        prefer x-real-ip regardless, so a forged chain changes nothing."""
        req = self._direct({"x-forwarded-for": f"203.0.113.99, {self.RAILWAY_HOP}"})
        self.assertEqual(W.client_ip(req), self.CLIENT)

    def test_falls_back_to_peer(self):
        self.assertEqual(W.client_ip(_Req({}, host="10.9.9.9")), "10.9.9.9")


class RateLimitTests(unittest.TestCase):
    def test_fails_closed_when_limiter_unavailable(self):
        """redis.rate_limited fails OPEN, which is wrong for a public write. This
        path must refuse instead."""
        from automation import redis
        original = redis.incr_expiring
        redis.incr_expiring = lambda k, w: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            self.assertTrue(W._over_limit("test-bucket", 5, 60))
        finally:
            redis.incr_expiring = original


class OptInTests(unittest.TestCase):
    def setUp(self):
        self.db, self.path = _fresh_db()
        self._sent = []
        self._orig_send = mailer.send
        mailer.send = lambda to, s, h, text=None, headers=None: (
            self._sent.append(to) or (True, "sent"))

    def tearDown(self):
        mailer.send = self._orig_send
        store.reset_ensured()

    def test_join_stores_unconfirmed_and_mails_once(self):
        self.assertEqual(waitlist.join("A@Test.com", db=self.db), waitlist.OK)
        row = store.get("a@test.com", db=self.db)
        self.assertEqual(row["status"], store.UNCONFIRMED)
        self.assertEqual(self._sent, ["a@test.com"])

    def test_rejoin_does_not_reset_or_remail(self):
        waitlist.join("a@test.com", db=self.db)
        token = store.get("a@test.com", db=self.db)["token"]
        waitlist.confirm(token, db=self.db)
        self._sent.clear()
        self.assertEqual(waitlist.join("a@test.com", db=self.db), waitlist.ALREADY)
        self.assertEqual(self._sent, [])
        self.assertEqual(store.get("a@test.com", db=self.db)["status"], store.SUBSCRIBED)

    def test_unsubscribed_is_never_resurrected(self):
        """Re-mailing someone who opted out because they hit a form again is how a
        sending domain gets blocklisted."""
        waitlist.join("a@test.com", db=self.db)
        token = store.get("a@test.com", db=self.db)["token"]
        waitlist.confirm(token, db=self.db)
        waitlist.unsubscribe(token, db=self.db)
        self._sent.clear()
        waitlist.join("a@test.com", db=self.db)
        self.assertEqual(self._sent, [])
        self.assertEqual(store.get("a@test.com", db=self.db)["status"], store.UNSUBSCRIBED)

    def test_confirm_is_idempotent(self):
        waitlist.join("a@test.com", db=self.db)
        token = store.get("a@test.com", db=self.db)["token"]
        self.assertEqual(waitlist.confirm(token, db=self.db)["status"], store.SUBSCRIBED)
        self.assertEqual(waitlist.confirm(token, db=self.db)["status"], store.SUBSCRIBED)

    def test_unknown_token_returns_none(self):
        self.assertIsNone(waitlist.confirm("nope", db=self.db))
        self.assertIsNone(waitlist.unsubscribe("nope", db=self.db))

    def test_invalid_addresses_rejected(self):
        for bad in ("", "nope", "a@b", "a b@c.com", "x@" + "y" * 300 + ".com"):
            self.assertEqual(waitlist.join(bad, db=self.db), waitlist.INVALID)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        self.path = _fresh_default_db()
        # Rate-limit windows live in a module-level store; without this the buckets
        # from one test bleed into the next and later cases 429 spuriously.
        redis.reset()
        self._orig_send = mailer.send
        mailer.send = lambda to, s, h, text=None, headers=None: (True, "sent")
        app = FastAPI()
        W.register(app)
        self.c = TestClient(app)

    def tearDown(self):
        mailer.send = self._orig_send
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)
        os.environ.pop("AUTOMATION_DB_PATH", None)
        store.reset_ensured()

    def test_honeypot_looks_successful_but_stores_nothing(self):
        r = self.c.post("/api/waitlist",
                        json={"email": "bot@x.com", "company": "ACME"},
                        headers={"x-forwarded-for": "203.0.113.10"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(store.get("bot@x.com"))

    def test_valid_join_is_stored(self):
        r = self.c.post("/api/waitlist", json={"email": "ok@x.com"},
                        headers={"x-forwarded-for": "203.0.113.11"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(store.get("ok@x.com")["status"], store.UNCONFIRMED)

    def test_invalid_email_rejected(self):
        r = self.c.post("/api/waitlist", json={"email": "nope"},
                        headers={"x-forwarded-for": "203.0.113.12"})
        self.assertEqual(r.status_code, 400)

    def test_per_ip_limit_blocks_a_flood(self):
        codes = [
            self.c.post("/api/waitlist", json={"email": f"f{i}@x.com"},
                        headers={"x-forwarded-for": "203.0.113.13"}).status_code
            for i in range(W.IP_LIMIT + 2)
        ]
        self.assertEqual(codes[:W.IP_LIMIT], [200] * W.IP_LIMIT)
        self.assertEqual(codes[W.IP_LIMIT:], [429, 429])

    def test_unsubscribe_get_does_not_act(self):
        """Mail clients and scanners prefetch links; acting on GET would
        unsubscribe people who never clicked."""
        self.c.post("/api/waitlist", json={"email": "g@x.com"},
                    headers={"x-forwarded-for": "203.0.113.77"})
        token = store.get("g@x.com")["token"]
        self.c.get("/api/waitlist/confirm", params={"t": token})
        self.c.get("/api/waitlist/unsubscribe", params={"t": token})
        self.assertEqual(store.get("g@x.com")["status"], store.SUBSCRIBED)
        self.c.post("/api/waitlist/unsubscribe", params={"t": token})
        self.assertEqual(store.get("g@x.com")["status"], store.UNSUBSCRIBED)

    def test_production_guard_refuses_without_shared_redis(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "1"
        original = redis.configured
        redis.configured = lambda: False
        try:
            r = self.c.post("/api/waitlist", json={"email": "x@y.com"})
            self.assertEqual(r.status_code, 503)
        finally:
            redis.configured = original


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.db, self.path = _fresh_db()
        self._sent = []
        self._orig_send, self._orig_conf = mailer.send, mailer.configured
        mailer.send = lambda to, s, h, text=None, headers=None: (
            self._sent.append(to) or (True, "sent"))
        mailer.configured = lambda: True

    def tearDown(self):
        mailer.send, mailer.configured = self._orig_send, self._orig_conf
        store.reset_ensured()

    def _subscribed(self, email):
        store.create_unconfirmed(email, db=self.db)
        store.confirm(email, db=self.db)

    def test_dry_run_sends_nothing(self):
        self._subscribed("a@x.com")
        result = broadcast.run(send=False, db=self.db)
        self.assertEqual(self._sent, [])
        self.assertEqual(result["pending"], 1)

    def test_sends_once_and_is_resumable(self):
        self._subscribed("a@x.com")
        self._subscribed("b@x.com")
        store.create_unconfirmed("never@x.com", db=self.db)   # unconfirmed
        first = broadcast.run(send=True, sleep=0, db=self.db)
        self.assertEqual(first["sent"], 2)
        second = broadcast.run(send=True, sleep=0, db=self.db)
        self.assertEqual(second["sent"], 0)                   # no double-send
        self.assertNotIn("never@x.com", self._sent)           # unconfirmed never mailed

    def test_failed_send_is_retried_next_run(self):
        self._subscribed("a@x.com")
        mailer.send = lambda to, s, h, text=None, headers=None: (False, "boom")
        self.assertEqual(broadcast.run(send=True, sleep=0, db=self.db)["failed"], 1)
        self.assertIsNone(store.get("a@x.com", db=self.db)["notified_at"])
        mailer.send = lambda to, s, h, text=None, headers=None: (
            self._sent.append(to) or (True, "sent"))
        self.assertEqual(broadcast.run(send=True, sleep=0, db=self.db)["sent"], 1)

    def test_refuses_to_send_when_provider_unconfigured(self):
        self._subscribed("a@x.com")
        mailer.configured = lambda: False
        result = broadcast.run(send=True, sleep=0, db=self.db)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(self._sent, [])


if __name__ == "__main__":
    unittest.main()
