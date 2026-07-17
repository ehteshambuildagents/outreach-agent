"""Tests for per-user usage caps + the account kill switch (limits/).

Fully offline: SQLite in a temp file, no network. Covers the hard per-provider
call cap, the combined USD spend cap, system calls never being capped, the spike
kill switch (relative to a peer baseline), and the enforcement at the shared
provider HTTP layer (research/providers_common).

    python -m unittest tests.test_limits
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

import limits  # noqa: E402
from limits import store  # noqa: E402


class LimitsTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTOMATION_DB_PATH"] = tempfile.mktemp(suffix=".db")
        store.reset_ensured()

    def test_provider_daily_call_cap(self):
        u = "user_a"
        cap = limits.provider_daily_cap("firecrawl")
        # Spread across time so the hourly kill switch can't trip during setup.
        now = time.time()
        for i in range(cap):
            store.add_usage(u, "firecrawl", limits.provider_cost("firecrawl"), 1,
                            ts=now - (i % 20) * 3600 - 10)
        d = limits.allow("firecrawl", u)
        self.assertFalse(d.allowed)
        self.assertIn("firecrawl", d.reason.lower())
        # A different provider has its own separate budget.
        self.assertTrue(limits.allow("tavily", u).allowed)

    def test_combined_usd_daily_cap(self):
        u = "user_b"
        now = time.time()
        # Seed spend just over the daily USD cap, spread across hours.
        with mock.patch.object(limits, "LIMIT_DAILY_USD_PER_USER", 0.05):
            store.add_usage(u, "tavily", 0.04, 1, ts=now - 2 * 3600)
            store.add_usage(u, "tavily", 0.04, 1, ts=now - 1 * 3600)
            d = limits.allow("tavily", u)
        self.assertFalse(d.allowed)
        self.assertIn("usage limit", d.reason.lower())

    def test_system_call_is_never_capped(self):
        # No user context => internal/system call => always allowed, never metered.
        for _ in range(limits.provider_daily_cap("firecrawl") + 50):
            self.assertTrue(limits.allow("firecrawl", None).allowed)

    def test_kill_switch_trips_on_relative_spike(self):
        now = time.time()
        # Peers each do ~2 calls/hour over the last few hours -> low baseline.
        for peer in ("p1", "p2", "p3"):
            for h in range(1, 5):
                store.add_usage(peer, "firecrawl", 0.01, 2, ts=now - h * 3600 - 30)
        spiker = "spiker"
        for _ in range(50):                     # 50 calls in the last hour >> baseline
            limits.record("firecrawl", spiker)
        self.assertTrue(limits.is_paused(spiker))
        # A paused account is blocked from every paid provider.
        self.assertFalse(limits.allow("firecrawl", spiker).allowed)
        self.assertFalse(limits.allow("anthropic", spiker).allowed)
        # Resume clears it.
        limits.resume(spiker)
        self.assertTrue(limits.allow("anthropic", spiker).allowed)

    def test_record_updates_snapshot(self):
        u = "user_c"
        with mock.patch.object(limits, "LIMIT_SPIKE_MIN_CALLS", 10 ** 9):
            for _ in range(3):
                limits.record("tavily", u)
        snap = limits.usage_snapshot(u)
        self.assertEqual(snap["providers"]["tavily"]["calls_today"], 3)
        self.assertEqual(snap["state"], "active")

    def test_enforcement_at_provider_http_layer(self):
        """A paused/over-cap user never reaches the paid HTTP call."""
        from research import providers_common
        from telemetry import context
        u = "capped_user"
        limits.pause(u, "test")
        with context.scope(user_id=u):
            with mock.patch("research.providers_common.requests.request") as req:
                out = providers_common.request_json("GET", "https://x", provider="tavily")
        self.assertIsNone(out)
        req.assert_not_called()                 # cap short-circuited before any HTTP


if __name__ == "__main__":
    unittest.main()
