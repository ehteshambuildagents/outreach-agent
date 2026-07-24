"""Sandboxed demo sessions — the security surface of the in-app live demo.

The whole isolation story rests on four properties, each pinned here:

  * a demo principal can ONLY be produced from a server-signed, unexpired token
    (forgery/expiry/wrong-secret all rejected);
  * its id lives in a namespace disjoint from real users (``demo_*`` vs
    ``user_*``), so every per-user store scopes it automatically — a demo session
    cannot read another principal's data (real OR demo);
  * it reaches only allowlisted routes (member-only routes still 401);
  * it NEVER touches the access approval queue, and the chat agent never offers
    it a send/launch tool.

Everything runs offline: in-memory Redis, local SQLite, a fixed demo secret.
"""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")
# In-memory coordination — never touch a real Upstash from the limiter tests.
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""
os.environ["DEMO_SESSION_SECRET"] = "test-demo-secret"

import access  # noqa: E402
from config import settings  # noqa: E402
from server import demo_session  # noqa: E402
import server.api as api  # noqa: E402


class TokenTests(unittest.TestCase):
    """The signed-token primitive — the only way to become a demo principal."""

    def test_mint_verify_roundtrip(self):
        did = demo_session.new_demo_id()
        self.assertTrue(did.startswith("demo_"))
        tok, exp = demo_session.mint_token(did)
        self.assertEqual(demo_session.verify_token(tok), did)
        self.assertEqual(demo_session.token_expiry(tok), exp)

    def test_expired_token_is_invalid(self):
        tok, _ = demo_session.mint_token(demo_session.new_demo_id(), ttl_seconds=-5)
        self.assertIsNone(demo_session.verify_token(tok))

    def test_forged_id_with_stale_signature_rejected(self):
        tok, _ = demo_session.mint_token(demo_session.new_demo_id())
        forged = "demo_" + "f" * 32 + "." + tok.split(".", 1)[1]
        self.assertIsNone(demo_session.verify_token(forged))

    def test_tampered_signature_rejected(self):
        tok, _ = demo_session.mint_token(demo_session.new_demo_id())
        body, sig = tok.rsplit(".", 1)
        self.assertIsNone(demo_session.verify_token(body + "." + "0" * len(sig)))

    def test_wrong_secret_rejected(self):
        tok, _ = demo_session.mint_token(demo_session.new_demo_id())
        with mock.patch.object(settings, "DEMO_SESSION_SECRET", "a-different-secret"):
            # The fallback secret is per-process, so this both changes the secret
            # and proves a token can't survive a secret rotation.
            with mock.patch.object(demo_session, "_FALLBACK_SECRET", "x" * 64):
                self.assertIsNone(demo_session.verify_token(tok))

    def test_non_demo_namespace_never_verifies(self):
        # Even a correctly-signed token for a real-looking id is refused: the
        # verifier gates on the demo_ prefix, so demo tokens can't impersonate a
        # ``user_*`` principal.
        payload = "user_real.99999999999"
        signed = payload + "." + demo_session._sign(payload)
        self.assertIsNone(demo_session.verify_token(signed))


class DemoDependencyTests(unittest.TestCase):
    """The demo-aware auth dependencies on the real routes."""

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        api.app.dependency_overrides.clear()
        api._STORE_BASE = tempfile.mkdtemp()
        api._BUCKETS.clear()
        # Production has Clerk configured, so a member-only route 401s a no-bearer
        # request. Force that here (tests otherwise run with auth disabled, which
        # would treat everyone as "anonymous").
        self._auth = mock.patch("server.auth.auth_enabled", return_value=True)
        self._auth.start()
        self.c = TestClient(api.app)

    def tearDown(self):
        self._auth.stop()
        api.app.dependency_overrides.clear()
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    def _demo(self, did=None):
        did = did or demo_session.new_demo_id()
        tok, _ = demo_session.mint_token(did)
        return {demo_session.HEADER_NAME: tok}, did

    def test_no_credentials_is_401(self):
        self.assertEqual(self.c.get("/api/company").status_code, 401)

    def test_valid_demo_session_reaches_allowlisted_route(self):
        headers, _ = self._demo()
        r = self.c.get("/api/company", headers=headers)
        self.assertEqual(r.status_code, 200)

    def test_invalid_demo_token_is_401(self):
        r = self.c.get("/api/company", headers={demo_session.HEADER_NAME: "demo_x.1.bad"})
        self.assertEqual(r.status_code, 401)

    def test_demo_sessions_are_isolated_from_each_other(self):
        h1, _ = self._demo()
        cid = self.c.post("/api/conversations", headers=h1).json()["id"]
        self.assertEqual(self.c.get(f"/api/conversations/{cid}", headers=h1).status_code, 200)
        # A DIFFERENT demo principal cannot see the first's conversation.
        h2, _ = self._demo()
        self.assertEqual(self.c.get(f"/api/conversations/{cid}", headers=h2).status_code, 404)

    def test_demo_cannot_read_a_real_users_conversation(self):
        # Seed a conversation owned by a real member.
        api.app.dependency_overrides[api.require_member_or_demo] = lambda: "user_real"
        member = TestClient(api.app)
        cid = member.post("/api/conversations").json()["id"]
        api.app.dependency_overrides.clear()
        # A demo principal is refused it (its store namespace can't address it).
        headers, _ = self._demo()
        self.assertEqual(self.c.get(f"/api/conversations/{cid}", headers=headers).status_code, 404)

    def test_demo_cannot_reach_member_only_route(self):
        # OAuth login stays member-only (require_user); a demo session is not a
        # member, so the allowlist boundary holds.
        headers, _ = self._demo()
        self.assertEqual(self.c.get("/api/oauth/gmail/login", headers=headers).status_code, 401)

    def test_paused_demo_session_is_403(self):
        headers, did = self._demo()
        with mock.patch("limits.is_paused", side_effect=lambda u: u == did):
            self.assertEqual(self.c.get("/api/company", headers=headers).status_code, 403)

    def test_demo_never_pollutes_the_access_queue(self):
        before = {r.get("user_id") for r in access.list_all()}
        headers, did = self._demo()
        self.c.get("/api/company", headers=headers)
        self.c.post("/api/conversations", headers=headers)
        after = {r.get("user_id") for r in access.list_all()}
        self.assertNotIn(did, after)
        self.assertEqual(before, after)  # nothing recorded at all for the demo id


class DemoSessionEndpointTests(unittest.TestCase):
    """Mint / status / turn-cap for the session endpoints."""

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        self._join = mock.patch("waitlist.join")
        self._join.start()
        self.c = TestClient(api.app)

    def tearDown(self):
        self._join.stop()
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    def test_mint_sets_cookie_and_returns_active(self):
        r = self.c.post("/api/demo/session", json={"email": "v@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["active"])
        self.assertIn(demo_session.COOKIE_NAME, r.cookies)

    def test_status_reflects_a_live_session(self):
        did = demo_session.new_demo_id()
        tok, _ = demo_session.mint_token(did)
        st = self.c.get("/api/demo/session", headers={demo_session.HEADER_NAME: tok})
        self.assertTrue(st.json()["active"])
        self.assertEqual(st.json()["turns_used"], 0)

    def test_status_without_session_is_inactive(self):
        self.assertFalse(self.c.get("/api/demo/session").json()["active"])

    def test_email_gate_on_mint(self):
        r = self.c.post("/api/demo/session", json={"email": "not-an-email"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["state"], "need_email")

    def test_honeypot_on_mint_starts_no_session(self):
        r = self.c.post("/api/demo/session",
                        json={"email": "v@example.com", "company": "Bot LLC"})
        self.assertEqual(r.json().get("state"), "capacity")
        self.assertNotIn(demo_session.COOKIE_NAME, r.cookies)

    def test_turn_cap_blocks_after_limit(self):
        from server import demo_api
        did = demo_session.new_demo_id()
        with mock.patch.object(settings, "DEMO_SESSION_TURNS", 2):
            self.assertEqual(demo_api.reserve_demo_turn(did), (True, ""))
            self.assertTrue(demo_api.reserve_demo_turn(did)[0])
            ok, msg = demo_api.reserve_demo_turn(did)
            self.assertFalse(ok)
            self.assertIn("message limit", msg.lower())


class DemoToolPolicyTests(unittest.TestCase):
    """The chat agent never offers a demo principal a send/launch tool, and hard-
    denies one even if it were somehow selected."""

    def test_demo_toolset_excludes_send_and_launch(self):
        import chat.tools as t
        demo = {s["name"] for s in t.tool_specs(user_id="demo_abc")}
        self.assertNotIn("send_email", demo)
        self.assertNotIn("launch_campaign", demo)
        self.assertNotIn("pause_campaign", demo)
        self.assertIn("write_email", demo)      # drafting stays available
        self.assertIn("research_company", demo)

    def test_execute_hard_denies_a_blocked_tool_for_demo(self):
        import chat.tools as t
        conv = mock.Mock()
        conv._user_id = "demo_abc"
        res = t.execute("send_email", {}, conv)
        self.assertIn("live demo", res.summary.lower())

    def test_member_still_gets_full_toolset(self):
        import chat.tools as t
        member = {s["name"] for s in t.tool_specs(user_id="user_real")}
        self.assertIn("send_email", member)
        self.assertIn("launch_campaign", member)


if __name__ == "__main__":
    unittest.main(verbosity=2)
