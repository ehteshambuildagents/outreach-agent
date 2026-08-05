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
import threading
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


def _reset_demo_budget() -> None:
    """Clear the process-global demo budget state so tests don't leak into each
    other. Two backstops accumulate across a suite run: the in-memory Redis
    run-count/rate-limit buckets (reset by the callers via ``redis.reset()``), and
    the DB-backed dollar-spend ledger for the shared DEMO_LEDGER_USER — which
    ``redis.reset()`` does NOT touch, so a later test would otherwise inherit an
    already-exhausted daily budget and see spurious 429s / capacity blocks."""
    try:
        from automation.db import Database
        from limits import store as _limits_store
        db = Database()
        _limits_store.ensure(db)
        db.execute("DELETE FROM usage_ledger WHERE user_id=?",
                   (settings.DEMO_LEDGER_USER,))
    except Exception:  # noqa: BLE001 - best-effort test isolation
        pass


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

    def test_email_tag_roundtrips_in_token(self):
        # The email tag is signed into the token and is what the quota is charged
        # against — so it cannot be swapped without invalidating the signature.
        did = demo_session.new_demo_id()
        tag = demo_session.email_hmac("alice@gmail.com")
        tok, _ = demo_session.mint_token(did, tag)
        self.assertEqual(demo_session.verify_token(tok), did)
        self.assertEqual(demo_session.token_email_hmac(tok), tag)
        self.assertEqual(demo_session.quota_subject(tok), tag)

    def test_email_hmac_is_deterministic_and_hides_the_address(self):
        a = demo_session.email_hmac("alice@gmail.com")
        self.assertEqual(a, demo_session.email_hmac("alice@gmail.com"))  # stable
        self.assertNotEqual(a, demo_session.email_hmac("bob@gmail.com"))  # per-email
        self.assertNotIn("alice", a)          # discloses nothing about the address
        self.assertNotIn(".", a)              # can't corrupt the dotted grammar
        self.assertEqual(len(a), 32)
        self.assertEqual(demo_session.email_hmac(""), "")  # empty in, empty out

    def test_tampered_email_tag_rejected(self):
        # Flipping the tag (to charge another email's quota, or dodge one's own)
        # breaks the signature, so the whole token is refused.
        did = demo_session.new_demo_id()
        tok, _ = demo_session.mint_token(did, demo_session.email_hmac("alice@gmail.com"))
        demo_id, _tag, exp_s, sig = tok.split(".")
        forged = f"{demo_id}.{demo_session.email_hmac('bob@gmail.com')}.{exp_s}.{sig}"
        self.assertIsNone(demo_session.verify_token(forged))

    def test_legacy_three_part_token_still_authenticates(self):
        # A token minted before the email dimension existed must not log the
        # visitor out; it verifies, and its quota falls back to the demo id.
        did = demo_session.new_demo_id()
        payload = f"{did}.{int(__import__('time').time()) + 3600}"
        legacy = payload + "." + demo_session._sign(payload)
        self.assertEqual(demo_session.verify_token(legacy), did)
        self.assertIsNone(demo_session.token_email_hmac(legacy))
        self.assertEqual(demo_session.quota_subject(legacy), did)


class DemoDependencyTests(unittest.TestCase):
    """The demo-aware auth dependencies on the real routes."""

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        _reset_demo_budget()
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

    def test_demo_can_list_campaigns(self):
        # Regression: campaign create/list/get were guarded on require_user, so
        # every demo visitor got 401 on the Campaigns and New Campaign pages while
        # list_prospects (same module) was already demo-aware. They now share
        # require_identity_or_demo and resolve to the demo principal's own (empty)
        # campaign store.
        headers, _ = self._demo()
        r = self.c.get("/api/campaigns", headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"campaigns": []})

    def test_demo_launch_stays_member_only(self):
        # Creating/listing/viewing is drafts-only and demo-allowed, but launching
        # moves real mail and must stay member-only: a demo session is refused
        # before the handler runs (401 from require_user, not a 404).
        headers, _ = self._demo()
        r = self.c.post("/api/campaigns/does-not-exist/launch", headers=headers, json={})
        self.assertEqual(r.status_code, 401)

    def test_paused_demo_session_is_403(self):
        headers, did = self._demo()
        with mock.patch("limits.is_paused", side_effect=lambda u: u == did):
            self.assertEqual(self.c.get("/api/company", headers=headers).status_code, 403)

    def test_streamed_turn_decrements_the_demo_allowance(self):
        # End-to-end on the path the UI actually uses (/messages/stream): each
        # eligible user message reserves exactly one turn, the status endpoint
        # reflects it immediately, and the turn AFTER the cap is refused. This is
        # the "5 messages left forever" regression, pinned server-side.
        from config import settings as _s
        headers, _did = self._demo()
        cid = self.c.post("/api/conversations", headers=headers).json()["id"]
        fake = {"stop_reason": "end_turn", "text": "ok", "tool_uses": [],
                "assistant_content": [{"type": "text", "text": "ok"}]}
        with mock.patch.object(_s, "DEMO_SESSION_TURNS", 3), \
             mock.patch("chat.agent.claude_client.call_with_tools", return_value=fake):
            for i in range(3):
                r = self.c.post(f"/api/conversations/{cid}/messages/stream",
                                headers=headers, json={"text": "hi"})
                self.assertEqual(r.status_code, 200)
                _ = r.text  # drain the SSE body
                st = self.c.get("/api/demo/session", headers=headers).json()
                self.assertEqual(st["turns_used"], i + 1,
                                 f"turn {i + 1} should have been counted")
            blocked = self.c.post(f"/api/conversations/{cid}/messages/stream",
                                  headers=headers, json={"text": "hi"})
            self.assertEqual(blocked.status_code, 429)

    def test_streamed_turn_persists_the_conversation_and_survives_a_refresh(self):
        # The production regression this pins: an accepted streamed turn must (a)
        # increment the SERVER-authoritative count, (b) have that count survive a
        # fresh /api/demo/session read (a "refresh"), and (c) persist the
        # conversation with both the user and assistant messages so a reload shows
        # the thread. All three are read back through the real HTTP surface.
        headers, _did = self._demo()
        cid = self.c.post("/api/conversations", headers=headers).json()["id"]
        self.assertEqual(self.c.get("/api/demo/session", headers=headers).json()["turns_used"], 0)
        fake = {"stop_reason": "end_turn", "text": "ok", "tool_uses": [],
                "assistant_content": [{"type": "text", "text": "ok"}]}
        with mock.patch("chat.agent.claude_client.call_with_tools", return_value=fake):
            r = self.c.post(f"/api/conversations/{cid}/messages/stream",
                            headers=headers, json={"text": "who should we target?"})
            self.assertEqual(r.status_code, 200)
            _ = r.text  # drain the SSE body
        # (a) counted, and (b) a separate status read still reflects it (refresh).
        self.assertEqual(self.c.get("/api/demo/session", headers=headers).json()["turns_used"], 1)
        self.assertEqual(self.c.get("/api/demo/session", headers=headers).json()["turns_used"], 1)
        # (c) persists: it lists, loads, and carries the turn's messages.
        listed = self.c.get("/api/conversations", headers=headers).json()["conversations"]
        self.assertIn(cid, [c["id"] for c in listed])
        conv = self.c.get(f"/api/conversations/{cid}", headers=headers).json()
        roles = [m["role"] for m in conv["messages"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        # A DIFFERENT demo principal cannot see this conversation (isolation holds).
        h2, _ = self._demo()
        self.assertEqual(self.c.get(f"/api/conversations/{cid}", headers=h2).status_code, 404)

    def test_a_clarifying_turn_that_runs_the_model_still_consumes_one_turn(self):
        # Policy pin: every accepted message that reaches the paid model consumes a
        # turn, EVEN when the assistant only asks a clarifying question and returns
        # no results. Reservation happens before the turn runs, so an empty/clarify
        # result cannot make it free (nor is it refunded).
        headers, _did = self._demo()
        cid = self.c.post("/api/conversations", headers=headers).json()["id"]
        clarify = {"stop_reason": "end_turn",
                   "text": "Which vertical did you mean, lending or payments?",
                   "tool_uses": [],
                   "assistant_content": [{"type": "text",
                                          "text": "Which vertical did you mean?"}]}
        with mock.patch("chat.agent.claude_client.call_with_tools", return_value=clarify):
            r = self.c.post(f"/api/conversations/{cid}/messages/stream",
                            headers=headers, json={"text": "find me fintech"})
            self.assertEqual(r.status_code, 200)
            _ = r.text
        self.assertEqual(self.c.get("/api/demo/session", headers=headers).json()["turns_used"], 1)

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
        _reset_demo_budget()
        self._join = mock.patch("waitlist.join")
        self._join.start()
        self.c = TestClient(api.app)

    def tearDown(self):
        self._join.stop()
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    def test_mint_sets_cookie_and_returns_active(self):
        r = self.c.post("/api/demo/session", json={"email": "v@gmail.com"})
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

    def test_rapid_mints_are_not_burst_blocked(self):
        """A session mint is cheap, so it must absorb a double-click and several
        visitors behind one NAT. Sharing the paid run's 1-per-90s burst meant the
        SECOND request from an IP was answered "one run at a time" (seen in
        production on launch day)."""
        for i in range(settings.DEMO_SESSION_IP_BURST):
            r = self.c.post("/api/demo/session", json={"email": f"v{i}@gmail.com"},
                            headers={"x-forwarded-for": "203.0.113.42"})
            self.assertEqual(r.status_code, 200, f"mint {i + 1} should be admitted")
            self.assertTrue(r.json()["active"])

    def test_session_mint_still_has_a_burst_ceiling(self):
        with mock.patch.object(settings, "DEMO_SESSION_IP_BURST", 2):
            for _ in range(2):
                self.assertEqual(
                    self.c.post("/api/demo/session", json={"email": "v@gmail.com"},
                                headers={"x-forwarded-for": "203.0.113.43"}).status_code, 200)
            over = self.c.post("/api/demo/session", json={"email": "v@gmail.com"},
                               headers={"x-forwarded-for": "203.0.113.43"})
        self.assertEqual(over.status_code, 429)
        self.assertEqual(over.json()["scope"], "burst")
        # The block carries how long it lasts, and the bucket TTL equals that
        # window, so nobody is wedged out for longer than it.
        self.assertEqual(over.json()["retry_after"], settings.DEMO_SESSION_IP_BURST_WINDOW)

    def test_mint_and_run_use_separate_buckets(self):
        """A paid run must not consume the cheap mint allowance, or vice versa."""
        from server import demo_api
        r = self.c.post("/api/demo/session", json={"email": "v@gmail.com"},
                        headers={"x-forwarded-for": "203.0.113.44"})
        self.assertEqual(r.status_code, 200)
        with mock.patch.object(demo_api, "run_demo",
                               side_effect=lambda **_: iter([("done", {})])):
            run = self.c.post("/api/demo/run", json={"email": "v@gmail.com", "icp": "devtools"},
                              headers={"x-forwarded-for": "203.0.113.44"})
        self.assertEqual(run.status_code, 200)  # run's own burst bucket is untouched

    def test_non_gmail_email_is_rejected_on_mint(self):
        import waitlist
        r = self.c.post("/api/demo/session", json={"email": "founder@work.io"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["state"], "gmail_only")
        self.assertNotIn(demo_session.COOKIE_NAME, r.cookies)
        waitlist.join.assert_not_called()  # a rejected visitor is never signed up

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

    def test_concurrent_reservations_never_exceed_the_cap(self):
        # Multiple tabs / rapid concurrent requests share ONE server-side counter.
        # No more than the cap is admitted even when they race, AND the stored
        # consumed count equals accepted turns (5), NOT attempts (9): a rejected
        # attempt never consumes a turn.
        from server import demo_api
        did = demo_session.new_demo_id()
        admitted = []
        with mock.patch.object(settings, "DEMO_SESSION_TURNS", 5), \
             mock.patch.object(demo_api, "_global_budget_reached", return_value=False), \
             mock.patch.object(demo_api.limits_store, "add_usage"):
            def reserve():
                admitted.append(demo_api.reserve_demo_turn(did)[0])
            threads = [threading.Thread(target=reserve) for _ in range(9)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sum(admitted), 5)          # exactly the cap admitted
            self.assertEqual(demo_api.demo_turns_used(did), 5)  # only accepted consumed
            # Retrying a rejected request must not move the count.
            self.assertFalse(demo_api.reserve_demo_turn(did)[0])
            self.assertFalse(demo_api.reserve_demo_turn(did)[0])
            self.assertEqual(demo_api.demo_turns_used(did), 5)

    def test_rejected_turns_do_not_consume_the_allowance(self):
        # Sequential proof of the same rule: 5 accepted, further attempts rejected,
        # stored count stays at exactly 5.
        from server import demo_api
        did = demo_session.new_demo_id()
        with mock.patch.object(settings, "DEMO_SESSION_TURNS", 5), \
             mock.patch.object(demo_api, "_global_budget_reached", return_value=False), \
             mock.patch.object(demo_api.limits_store, "add_usage"):
            for _ in range(5):
                self.assertTrue(demo_api.reserve_demo_turn(did)[0])
            for _ in range(4):
                self.assertFalse(demo_api.reserve_demo_turn(did)[0])
            self.assertEqual(demo_api.demo_turns_used(did), 5)


class DemoPersistenceTests(unittest.TestCase):
    """The production regression this pins end-to-end: a returning demo visitor in
    the SAME browser restores their identity, conversations and remaining
    allowance, while the five-message quota can never be reset by exiting and
    re-entering — and history is never exposed to someone who merely types the
    same email in a different browser.

    A fresh ``TestClient`` models one browser: httpx keeps a cookie jar, so the
    signed session cookie round-trips exactly as it would for a real visitor.
    """

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        _reset_demo_budget()
        api.app.dependency_overrides.clear()
        api._STORE_BASE = tempfile.mkdtemp()
        api._BUCKETS.clear()
        self._join = mock.patch("waitlist.join")
        self._join.start()
        # Keep the demo turn off the real spend ledger during these tests.
        from server import demo_api
        self._budget = mock.patch.object(demo_api, "_global_budget_reached",
                                         return_value=False)
        self._usage = mock.patch.object(demo_api.limits_store, "add_usage")
        self._budget.start()
        self._usage.start()

    def tearDown(self):
        self._join.stop()
        self._budget.stop()
        self._usage.stop()
        api.app.dependency_overrides.clear()
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    @staticmethod
    def _browser():
        return TestClient(api.app)               # its own cookie jar == one browser

    @staticmethod
    def _id_of(client):
        return demo_session.verify_token(client.cookies.get(demo_session.COOKIE_NAME))

    def _enter(self, client, email):
        return client.post("/api/demo/session", json={"email": email})

    _FAKE_TURN = {"stop_reason": "end_turn", "text": "ok", "tool_uses": [],
                  "assistant_content": [{"type": "text", "text": "ok"}]}

    def _send_one(self, client, cid):
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        return_value=self._FAKE_TURN):
            r = client.post(f"/api/conversations/{cid}/messages/stream",
                            json={"text": "who should we target?"})
            _ = r.text                            # drain the SSE body
            return r

    def test_first_entry_mints_a_fresh_identity(self):
        c = self._browser()
        r = self._enter(c, "alice@gmail.com")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["active"])
        self.assertFalse(r.json()["restored"])
        self.assertEqual(r.json()["turns_used"], 0)
        self.assertTrue(demo_session.is_demo_id(self._id_of(c)))

    def test_same_browser_reentry_restores_id_conversation_and_turns(self):
        c = self._browser()
        self._enter(c, "alice@gmail.com")
        id1 = self._id_of(c)
        cid = c.post("/api/conversations").json()["id"]
        self._send_one(c, cid)                    # consume one turn, persist a thread

        # "Exit demo" navigates away but keeps the cookie; a re-entry through the
        # gate must REUSE the principal, not mint a new one.
        r = self._enter(c, "alice@gmail.com")
        self.assertTrue(r.json()["restored"])
        self.assertEqual(self._id_of(c), id1)     # same principal
        self.assertEqual(r.json()["turns_used"], 1)   # allowance NOT reset

        listed = [x["id"] for x in c.get("/api/conversations").json()["conversations"]]
        self.assertIn(cid, listed)                # the conversation is still there
        conv = c.get(f"/api/conversations/{cid}").json()
        self.assertIn("user", [m["role"] for m in conv["messages"]])

    def test_refresh_and_new_tab_see_authoritative_turns(self):
        c = self._browser()
        self._enter(c, "alice@gmail.com")
        cid = c.post("/api/conversations").json()["id"]
        self._send_one(c, cid)
        # A "refresh" is just another status read; a "new tab" is a second client
        # carrying the same cookie. Both report the server's authoritative count.
        self.assertEqual(c.get("/api/demo/session").json()["turns_used"], 1)
        tab2 = self._browser()
        tab2.cookies.set(demo_session.COOKIE_NAME,
                         c.cookies.get(demo_session.COOKIE_NAME))
        self.assertEqual(tab2.get("/api/demo/session").json()["turns_used"], 1)

    def test_different_email_same_browser_gets_separate_identity_and_allowance(self):
        c = self._browser()
        self._enter(c, "alice@gmail.com")
        id_a = self._id_of(c)
        cid = c.post("/api/conversations").json()["id"]
        self._send_one(c, cid)
        # Switching email in the same browser is a different person: fresh identity,
        # fresh allowance, and none of the first email's conversations.
        r = self._enter(c, "bob@gmail.com")
        self.assertFalse(r.json()["restored"])
        self.assertNotEqual(self._id_of(c), id_a)
        self.assertEqual(r.json()["turns_used"], 0)
        listed = [x["id"] for x in c.get("/api/conversations").json()["conversations"]]
        self.assertNotIn(cid, listed)

    def test_five_then_429_cannot_be_reset_by_reentry(self):
        from server import demo_api
        import waitlist
        tag = demo_session.email_hmac(waitlist.normalize("carol@gmail.com"))
        with mock.patch.object(settings, "DEMO_SESSION_TURNS", 5):
            for _ in range(5):
                self.assertTrue(demo_api.reserve_demo_turn(tag)[0])
            self.assertFalse(demo_api.reserve_demo_turn(tag)[0])   # 6th refused

            # A BRAND-NEW browser (cleared cookies) re-entering with the same email
            # inherits the exhausted allowance — it cannot be reset.
            c = self._browser()
            r = self._enter(c, "carol@gmail.com")
            self.assertEqual(r.json()["turns_used"], 5)
            cid = c.post("/api/conversations").json()["id"]
            blocked = c.post(f"/api/conversations/{cid}/messages/stream",
                             json={"text": "hi"})
            self.assertEqual(blocked.status_code, 429)

    def test_email_only_impersonation_reveals_no_history(self):
        # Victim builds a conversation in their browser.
        victim = self._browser()
        self._enter(victim, "victim@gmail.com")
        cid = victim.post("/api/conversations").json()["id"]
        self._send_one(victim, cid)

        # An attacker in a DIFFERENT browser types the same email. They get a fresh
        # principal (not the victim's), so the victim's conversation is a 404 —
        # history is never restored from an email alone.
        attacker = self._browser()
        r = self._enter(attacker, "victim@gmail.com")
        self.assertFalse(r.json()["restored"])
        self.assertNotEqual(self._id_of(attacker), self._id_of(victim))
        self.assertEqual(attacker.get(f"/api/conversations/{cid}").status_code, 404)
        # (The shared email-keyed allowance IS inherited — that is the anti-reset
        # property, not a data leak.)
        self.assertEqual(r.json()["turns_used"], 1)

    def test_expired_cookie_mints_fresh_identity(self):
        c = self._browser()
        dead, _ = demo_session.mint_token(demo_session.new_demo_id(),
                                          demo_session.email_hmac("x@gmail.com"),
                                          ttl_seconds=-5)
        c.cookies.set(demo_session.COOKIE_NAME, dead)
        r = self._enter(c, "x@gmail.com")
        self.assertTrue(r.json()["active"])
        self.assertFalse(r.json()["restored"])   # a dead token is never adopted

    def test_legacy_cookie_is_adopted_and_upgraded(self):
        import time as _t
        import waitlist
        did = demo_session.new_demo_id()
        payload = f"{did}.{int(_t.time()) + 3600}"
        legacy = payload + "." + demo_session._sign(payload)
        c = self._browser()
        c.cookies.set(demo_session.COOKIE_NAME, legacy)
        r = self._enter(c, "leg@gmail.com")
        self.assertTrue(r.json()["restored"])     # same principal kept
        # Read the re-issued token from the response (the jar holds both the
        # pre-seeded legacy cookie and the new one, which would make .get ambiguous).
        upgraded = r.cookies.get(demo_session.COOKIE_NAME)
        self.assertEqual(demo_session.verify_token(upgraded), did)
        self.assertEqual(demo_session.token_email_hmac(upgraded),
                         demo_session.email_hmac(waitlist.normalize("leg@gmail.com")))

    def test_reset_endpoint_clears_cookie_without_restoring_allowance(self):
        from server import demo_api
        import waitlist
        c = self._browser()
        self._enter(c, "dana@gmail.com")
        tag = demo_session.email_hmac(waitlist.normalize("dana@gmail.com"))
        with mock.patch.object(settings, "DEMO_SESSION_TURNS", 5):
            for _ in range(5):
                demo_api.reserve_demo_turn(tag)
            # Explicit reset clears the cookie…
            end = c.delete("/api/demo/session")
            self.assertFalse(end.json()["active"])
            # …but re-entering still sees the exhausted, email-keyed allowance.
            r = self._enter(c, "dana@gmail.com")
            self.assertEqual(r.json()["turns_used"], 5)


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


class DemoAutomationDashboardTests(unittest.TestCase):
    """The dashboard's automation reads (/api/automation/workflows + /metrics) are
    now demo-aware, mirroring /api/campaigns and /api/prospects. A demo visitor gets
    its OWN (empty) workflows and metrics scoped to itself — never the global
    process counters — while every create/control route stays member-only.
    """

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        api.app.dependency_overrides.clear()
        # Same posture as production: a member-only route 401s a no-bearer request.
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

    def test_valid_demo_lists_its_own_empty_workflows(self):
        headers, _ = self._demo()
        r = self.c.get("/api/automation/workflows", headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"workflows": []})

    def test_valid_demo_gets_scoped_metrics_not_global_counters(self):
        # Make the GLOBAL process counters non-zero, then prove the demo response
        # still reports zeros — i.e. it never leaks another user's aggregate data.
        from automation import metrics as m
        m.incr("emails_sent", 7)
        m.incr("replies", 3)
        try:
            headers, _ = self._demo()
            r = self.c.get("/api/automation/metrics", headers=headers)
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body, {"metrics": {"emails_sent": 0, "replies": 0,
                                                "reply_rate": 0.0}, "by_state": {}})
            # None of the global-only snapshot fields leak into the demo response.
            self.assertNotIn("avg_send_latency_ms", body["metrics"])
            self.assertNotIn("stop_rate", body["metrics"])
        finally:
            m.reset()

    def test_member_metrics_still_return_the_global_snapshot(self):
        # A real member (bearer) keeps the existing global process snapshot — the
        # demo-scoping branch must not change their workspace.
        with mock.patch("server.demo_auth.require_user", return_value="user_m1"):
            r = self.c.get("/api/automation/metrics",
                           headers={"authorization": "Bearer x"})
        self.assertEqual(r.status_code, 200)
        # Global-only fields the scoped demo view never includes.
        self.assertIn("avg_send_latency_ms", r.json()["metrics"])
        self.assertIn("stop_rate", r.json()["metrics"])

    def test_invalid_demo_token_is_unauthorized(self):
        bad = {demo_session.HEADER_NAME: "demo_x.1.bad"}
        self.assertEqual(self.c.get("/api/automation/workflows", headers=bad).status_code, 401)
        self.assertEqual(self.c.get("/api/automation/metrics", headers=bad).status_code, 401)

    def test_no_credentials_is_unauthorized(self):
        self.assertEqual(self.c.get("/api/automation/workflows").status_code, 401)
        self.assertEqual(self.c.get("/api/automation/metrics").status_code, 401)

    def test_expired_demo_session_is_unauthorized(self):
        tok, _ = demo_session.mint_token(demo_session.new_demo_id(), ttl_seconds=-5)
        h = {demo_session.HEADER_NAME: tok}
        self.assertEqual(self.c.get("/api/automation/workflows", headers=h).status_code, 401)
        self.assertEqual(self.c.get("/api/automation/metrics", headers=h).status_code, 401)

    def test_demo_cannot_see_a_members_workflows_or_count_them(self):
        from automation import engine
        from server import automation_api
        # Seed one workflow owned by a real member directly in the shared store.
        engine.create_workflow(
            automation_api._store, "user_real_owner",
            [{"subject": "Hi", "body": "Body", "delay_days": 0}],
            company="Acme", to_email="a@acme.com", provider="dryrun")
        headers, _ = self._demo()
        # The member's workflow is invisible to the demo principal…
        wfs = self.c.get("/api/automation/workflows", headers=headers).json()["workflows"]
        self.assertEqual(wfs, [])
        # …and does not appear in the demo's scoped metrics either.
        body = self.c.get("/api/automation/metrics", headers=headers).json()
        self.assertEqual(body["by_state"], {})
        self.assertEqual(body["metrics"]["emails_sent"], 0)

    def test_two_demo_sessions_are_isolated_on_the_dashboard(self):
        from automation import engine
        from server import automation_api
        _h1, did1 = self._demo()
        # Seed a workflow owned by the FIRST demo principal.
        engine.create_workflow(
            automation_api._store, did1,
            [{"subject": "Hi", "body": "Body", "delay_days": 0}],
            company="Acme", to_email="a@acme.com", provider="dryrun")
        # A DIFFERENT demo principal sees none of it.
        h2, _ = self._demo()
        self.assertEqual(self.c.get("/api/automation/workflows", headers=h2).json()["workflows"], [])
        self.assertEqual(self.c.get("/api/automation/metrics", headers=h2).json()["by_state"], {})

    def test_demo_cannot_create_or_control_workflows(self):
        # Every write/destructive automation route stays member-only: a demo
        # session is refused before the handler runs (401 from require_user).
        headers, _ = self._demo()
        self.assertEqual(self.c.post("/api/automation/workflows", headers=headers,
                                     json={"to_email": "a@b.com", "steps": []}).status_code, 401)
        for path in ("cancel", "pause", "resume", "run", "force-retry", "force-complete"):
            self.assertEqual(
                self.c.post(f"/api/automation/workflows/wf_x/{path}", headers=headers).status_code,
                401, f"{path} must stay member-only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
