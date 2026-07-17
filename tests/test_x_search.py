"""Tests for the X (Twitter) recent-search provider (research/x_search.py).

Fully offline: the HTTP layer (providers_common.request_json) is mocked and Redis
runs in its in-memory fallback, so no network and no real API cost. Covers the
things that matter for a paid endpoint: caching (no repeat call), the max_results
cap, honest empty results, cost/read logging + telemetry, the social-signal gate,
and graceful degradation without a token.

    python -m unittest tests.test_x_search
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation import redis as R  # noqa: E402
from config.settings import X_SEARCH_COST_PER_READ_USD, X_SEARCH_MAX_RESULTS  # noqa: E402
from research import x_search  # noqa: E402

_REQ = "research.x_search.request_json"

PAYLOAD = {
    "data": [
        {"id": "1", "text": "we just hired our first SDR!", "created_at": "2026-07-13T00:00:00Z",
         "lang": "en", "author_id": "u1", "public_metrics": {"like_count": 5, "reply_count": 1, "retweet_count": 0}},
        {"id": "2", "text": "any advice on outbound ramp?", "created_at": "2026-07-13T01:00:00Z",
         "lang": "en", "author_id": "u2", "public_metrics": {"like_count": 2}},
    ],
    "includes": {"users": [
        {"id": "u1", "username": "acme", "name": "Acme"},
        {"id": "u2", "username": "globex", "name": "Globex"},
    ]},
    "meta": {"result_count": 2},
}
EMPTY = {"meta": {"result_count": 0}}


class XSearchTests(unittest.TestCase):
    def setUp(self):
        # Force the in-memory Redis fallback (no network) and a clean cache.
        self._redis_patch = mock.patch("automation.redis.configured", return_value=False)
        self._redis_patch.start()
        R._mem = R._MemoryStore()
        os.environ["X_BEARER_TOKEN"] = "TEST_TOKEN"

    def tearDown(self):
        self._redis_patch.stop()
        os.environ.pop("X_BEARER_TOKEN", None)

    # ── availability / gate ──
    def test_unavailable_without_token(self):
        os.environ.pop("X_BEARER_TOKEN", None)
        with mock.patch(_REQ) as m:
            out = x_search.search_recent_posts("anything")
        self.assertEqual(out["status"], "unavailable")
        m.assert_not_called()                       # never hits the paid API

    def test_wants_recent_social_gate(self):
        for yes in ("who is complaining about us on X", "recent posts about SDR hiring",
                    "what people are saying on twitter", "check the buzz / sentiment"):
            self.assertTrue(x_search.wants_recent_social(yes), yes)
        for no in ("research acme.com", "draft an email for Stripe", "qualify this lead"):
            self.assertFalse(x_search.wants_recent_social(no), no)

    def test_gate_catches_natural_phrasing(self):
        # The exact query that used to slip through, plus natural variations a real
        # user would type ("in x", "posting about", "saying about", "posted about").
        for yes in ("Find SaaS founder recent tweet about sales in x",
                    "find a saas founder posting about sales on x",
                    "what are SaaS founders saying about outbound",
                    "who posted about their SDR ramp on twitter"):
            self.assertTrue(x_search.wants_recent_social(yes), yes)

    # ── parsing / cost ──
    def test_parses_posts_and_estimates_cost(self):
        with mock.patch(_REQ, return_value=PAYLOAD):
            out = x_search.search_recent_posts("SDR hiring")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 2)
        self.assertFalse(out["cached"])
        self.assertAlmostEqual(out["estimated_cost_usd"], round(2 * X_SEARCH_COST_PER_READ_USD, 4))
        self.assertEqual(out["posts"][0]["author_username"], "acme")
        self.assertEqual(out["posts"][0]["url"], "https://x.com/acme/status/1")

    def test_cost_recorded_to_telemetry(self):
        with mock.patch(_REQ, return_value=PAYLOAD), \
                mock.patch("telemetry.record_event") as rec:
            x_search.search_recent_posts("SDR hiring")
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs.get("value"),
                         round(2 * X_SEARCH_COST_PER_READ_USD, 4))

    # ── caching (the whole point for a paid endpoint) ──
    def test_second_identical_call_is_cached_no_api_hit(self):
        with mock.patch(_REQ, return_value=PAYLOAD) as m:
            first = x_search.search_recent_posts("SDR hiring", max_results=25)
            second = x_search.search_recent_posts("SDR hiring", max_results=25)
        self.assertEqual(m.call_count, 1)           # cache prevented the 2nd read
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["count"], first["count"])

    def test_empty_result_is_also_cached(self):
        with mock.patch(_REQ, return_value=EMPTY) as m:
            x_search.search_recent_posts("nothing here zzz")
            again = x_search.search_recent_posts("nothing here zzz")
        self.assertEqual(m.call_count, 1)           # don't pay twice for nothing
        self.assertEqual(again["status"], "empty")
        self.assertTrue(again["cached"])

    # ── honesty on empty / failure ──
    def test_empty_is_honest(self):
        with mock.patch(_REQ, return_value=EMPTY):
            out = x_search.search_recent_posts("nothing here zzz")
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["posts"], [])
        self.assertIn("no recent", out["reason"].lower())

    def test_provider_failure_is_error_not_raise(self):
        with mock.patch(_REQ, return_value=None):
            out = x_search.search_recent_posts("SDR hiring")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["posts"], [])

    # ── cost control: max_results cap + floor ──
    def test_max_results_capped_below_api_max(self):
        with mock.patch(_REQ, return_value=PAYLOAD) as m:
            x_search.search_recent_posts("SDR hiring", max_results=500)
        self.assertEqual(m.call_args.kwargs["params"]["max_results"], X_SEARCH_MAX_RESULTS)

    def test_max_results_floored_to_api_min(self):
        with mock.patch(_REQ, return_value=PAYLOAD) as m:
            x_search.search_recent_posts("SDR hiring", max_results=3)
        self.assertEqual(m.call_args.kwargs["params"]["max_results"], 10)  # API minimum

    def test_empty_query_is_error(self):
        with mock.patch(_REQ) as m:
            out = x_search.search_recent_posts("   ")
        self.assertEqual(out["status"], "error")
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
