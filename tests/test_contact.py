"""Public contact endpoint: honeypot, rate limiting, validation, and delivery.

Like the waitlist, this is an unauthenticated write that sends mail, so the abuse
controls and the failure-is-reported contract matter more than usual.
"""

import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")
# Force in-memory coordination: these tests exercise the rate limiter, and a real
# UPSTASH_* in .env would send that traffic to production Redis. Set empty rather
# than pop, so a later load_dotenv cannot restore it (see tests/test_waitlist.py).
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""

from waitlist import email as mailer      # noqa: E402
from server import contact_api as C       # noqa: E402


class ContactEndpointTests(unittest.TestCase):
    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        self.sent = []
        self._orig = mailer.send

        def _capture(to, subject, html, text=None, headers=None):
            self.sent.append({"to": to, "subject": subject, "html": html,
                              "text": text, "headers": headers or {}})
            return True, "sent"

        mailer.send = _capture
        app = FastAPI()
        C.register(app)
        self.c = TestClient(app)

    def tearDown(self):
        mailer.send = self._orig
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    def _post(self, **body):
        return self.c.post("/api/contact", json=body,
                           headers={"x-forwarded-for": "203.0.113.40"})

    def test_valid_message_is_sent_to_support_with_reply_to(self):
        r = self._post(email="Visitor@Example.com", subject="Hi", message="Please help.")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(len(self.sent), 1)
        msg = self.sent[0]
        self.assertEqual(msg["to"], "support@saqua.io")
        # Reply-To carries the visitor (normalized) so a reply reaches them.
        self.assertEqual(msg["headers"].get("Reply-To"), "visitor@example.com")
        self.assertIn("Please help.", msg["text"])
        self.assertIn("Hi", msg["subject"])

    def test_honeypot_is_dropped_but_looks_successful(self):
        r = self._post(email="bot@x.com", message="spam", company="ACME")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sent, [])

    def test_invalid_email_rejected(self):
        r = self._post(email="not-an-email", message="hello")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.sent, [])

    def test_empty_message_rejected(self):
        r = self._post(email="a@b.com", message="   ")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.sent, [])

    def test_rate_limit_blocks_a_flood(self):
        codes = [self._post(email=f"a{i}@b.com", message="hi").status_code
                 for i in range(C.IP_LIMIT + 2)]
        self.assertEqual(codes[:C.IP_LIMIT], [200] * C.IP_LIMIT)
        self.assertEqual(codes[C.IP_LIMIT:], [429, 429])

    def test_send_failure_is_surfaced_not_swallowed(self):
        """A failed send must tell the visitor, so they can fall back to emailing
        support directly rather than believing the message went through."""
        mailer.send = lambda *a, **k: (False, "resend down")
        r = self._post(email="a@b.com", message="hello")
        self.assertEqual(r.status_code, 502)
        self.assertIn("support@saqua.io", r.json()["detail"])

    def test_html_body_escapes_the_message(self):
        self._post(email="a@b.com", message="<script>alert(1)</script>")
        self.assertNotIn("<script>", self.sent[0]["html"])
        self.assertIn("&lt;script&gt;", self.sent[0]["html"])

    def test_limiter_keys_on_the_client_ip_bucket(self):
        """Sanity: the limiter keys on client_ip, shared with the rest of the app."""
        with mock.patch.object(C, "_over_limit", return_value=False) as m:
            self._post(email="a@b.com", message="hi")
        self.assertTrue(any(str(call.args[0]).startswith("contact:ip:")
                            for call in m.call_args_list))


if __name__ == "__main__":
    unittest.main()
