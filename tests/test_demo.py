"""Public live-demo: the runner's event contract and the endpoint's abuse gates.

The demo is an unauthenticated endpoint that spends real API money, so what
matters most is (a) the layered gating in front of it and (b) the honesty of the
event stream — most of all that an all-reject run yields ``no_pursue`` and never
crowns a "best fit" the scorer itself rejected.

Everything here runs offline: the pipeline agents are patched with deterministic
fakes; the endpoint tests patch ``run_demo`` itself.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AUTOMATION_FORCE_SQLITE", "1")
# Force in-memory coordination: these tests exercise the rate limiter, and a real
# UPSTASH_* in .env would send that traffic to production Redis. Set empty rather
# than pop, so a later load_dotenv cannot restore it (see tests/test_waitlist.py).
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""

from agents.qualification import CONTINUE, HIGH_PRIORITY, REJECT  # noqa: E402
from config import settings  # noqa: E402
from demo import runner  # noqa: E402
from server import demo_api as D  # noqa: E402


# ── Deterministic pipeline fakes ───────────────────────────────────────
def _prospect(domain: str, name: str = ""):
    return SimpleNamespace(company_name=name or domain.split(".")[0].title(),
                           domain=domain, website=f"https://{domain}",
                           why_it_matches="matched the search")


def _research(founder: str = "", role: str = ""):
    return {"status": "ok",
            "data": {"company_name": "Acme Metrics", "founder_name": founder,
                     "founder_role": role, "unique_hook": "shipping a new API"},
            "hooks": [{"text": "shipping a new API",
                       "source_url": "https://acme.dev/blog/launch"}],
            "pages_crawled": ["https://acme.dev"]}


def _q(score: int, rec: str):
    return SimpleNamespace(qualification_score=score, fit_level="strong",
                           recommendation=rec, priority="P2",
                           strongest_signals=["ICP match"], signals={})


def _events(gen):
    out = []
    for event, payload in gen:
        out.append((event, payload))
    return out


class RunnerContractTests(unittest.TestCase):
    """demo.runner.run_demo event stream, with the agents faked."""

    def _run(self, qualify_results, write_effects=None, founder="Dana Reyes"):
        prospects = [_prospect(f"c{i}.example.com") for i in range(len(qualify_results))]
        discovery = SimpleNamespace(status="ok", prospects=prospects)
        research = _research(founder=founder, role="Co-founder")
        seq = iter(qualify_results)
        write = mock.Mock(side_effect=write_effects or
                          [{"status": "ok", "subject": "hey", "body": "real body"}] * 2)
        with mock.patch.object(runner, "discover", return_value=discovery), \
             mock.patch.object(runner, "research_company", return_value=research), \
             mock.patch.object(runner, "qualify", side_effect=lambda **_: next(seq)), \
             mock.patch.object(runner, "write_email", write):
            return _events(runner.run_demo(icp_text="devtools startups")), write

    def test_pursue_run_yields_top_and_draft(self):
        events, _ = self._run([_q(80, HIGH_PRIORITY), _q(50, CONTINUE)])
        names = [e for e, _ in events]
        self.assertIn("candidates", names)
        self.assertIn("top", names)
        self.assertIn("draft", names)
        self.assertIn("gmail_pending", names)
        self.assertEqual(names[-1], "done")
        self.assertNotIn("no_pursue", names)
        top = dict(events)[("top")]
        self.assertEqual(top["fit_score"], 80)
        # The researched decision-maker is surfaced, with the card too.
        self.assertEqual(top["person"]["name"], "Dana Reyes")
        scored = [p for e, p in events if e == "scored"]
        self.assertTrue(all(c["person"]["name"] == "Dana Reyes" for c in scored))

    def test_all_reject_run_is_honest_no_pursue(self):
        events, _ = self._run([_q(36, REJECT), _q(24, REJECT)])
        names = [e for e, _ in events]
        self.assertIn("no_pursue", names)
        self.assertNotIn("top", names)      # no crowned best fit
        self.assertNotIn("draft", names)    # and no draft for a rejected company
        self.assertNotIn("gmail_pending", names)
        self.assertEqual(names[-1], "done")
        payload = dict(events)["no_pursue"]
        self.assertEqual(payload["best_score"], 36)
        self.assertIn("pursue bar", payload["reason"])

    def test_described_non_name_is_not_shown_as_a_person(self):
        # Extraction sometimes returns a DESCRIPTION in founder_name; the card
        # must show no person rather than "Unnamed founder (…)" (seen live).
        events, _ = self._run([_q(80, HIGH_PRIORITY)],
                              founder="Unnamed founder (stated as first-person narrator)")
        scored = [p for e, p in events if e == "scored"]
        self.assertTrue(all(c["person"]["name"] is None for c in scored))

    def test_writer_failure_retries_once_then_skips_with_our_copy(self):
        events, write = self._run(
            [_q(80, HIGH_PRIORITY)],
            write_effects=[{"status": "error", "reason": "The model returned an empty response."},
                           {"status": "error", "reason": "The model returned an empty response."}])
        self.assertEqual(write.call_count, 2)          # one retry, no more
        skip = dict(events)["draft_skip"]
        self.assertNotIn("model", skip["reason"])      # internal reason never shown
        self.assertIn("Saqua drafts", skip["reason"])

    def test_writer_transient_failure_recovers_on_retry(self):
        events, write = self._run(
            [_q(80, HIGH_PRIORITY)],
            write_effects=[{"status": "error", "reason": "boom"},
                           {"status": "ok", "subject": "hey", "body": "real body"}])
        self.assertEqual(write.call_count, 2)
        self.assertIn("draft", [e for e, _ in events])

    def test_discovery_failure_is_a_clean_error(self):
        with mock.patch.object(runner, "discover", side_effect=RuntimeError("boom")):
            events = _events(runner.run_demo(icp_text="devtools startups"))
        self.assertEqual(events[-1][0], "error")

    def test_no_matches_is_a_clean_empty(self):
        empty = SimpleNamespace(status="ok", prospects=[], reason="")
        with mock.patch.object(runner, "discover", return_value=empty):
            events = _events(runner.run_demo(icp_text="devtools startups"))
        self.assertEqual(events[-1][0], "empty")


class DemoEndpointTests(unittest.TestCase):
    """Gating order and SSE framing of POST /api/demo/run."""

    def setUp(self):
        from automation import redis
        os.environ["WAITLIST_REQUIRE_SHARED_REDIS"] = "0"
        redis.reset()
        self.joined = []
        self._join = mock.patch.object(D.waitlist, "join",
                                       side_effect=lambda email, source="": self.joined.append((email, source)))
        self._join.start()
        self.runs = []

        def _fake_run(*, icp_text="", website=""):
            self.runs.append(icp_text or website)
            yield "icp", {"label": icp_text}
            yield "done", {}

        self._run = mock.patch.object(D, "run_demo", side_effect=_fake_run)
        self._run.start()
        app = FastAPI()
        D.register(app)
        self.c = TestClient(app)

    def tearDown(self):
        self._join.stop()
        self._run.stop()
        os.environ.pop("WAITLIST_REQUIRE_SHARED_REDIS", None)

    def _post(self, ip="203.0.113.77", **body):
        return self.c.post("/api/demo/run", json=body,
                           headers={"x-forwarded-for": ip})

    def test_input_required_before_anything_else(self):
        r = self._post(email="a@b.co")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["state"], "need_input")
        self.assertEqual(self.runs, [])

    def test_email_gate(self):
        r = self._post(icp="devtools startups")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["state"], "need_email")
        self.assertEqual(self.runs, [])

    def test_honeypot_answers_benignly_and_never_runs(self):
        r = self._post(icp="devtools", email="a@b.co", company="Bot LLC")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["state"], "capacity")
        self.assertEqual(self.runs, [])
        self.assertEqual(self.joined, [])

    def test_happy_path_streams_sse_and_soft_joins_waitlist(self):
        r = self._post(icp="devtools startups", email="visitor@example.com")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/event-stream", r.headers["content-type"])
        self.assertIn("event: start", r.text)
        self.assertIn("event: icp", r.text)
        self.assertIn("event: done", r.text)
        self.assertEqual(self.runs, ["devtools startups"])
        self.assertEqual(self.joined, [("visitor@example.com", "demo")])

    def test_per_ip_burst_blocks_second_immediate_run(self):
        first = self._post(icp="devtools", email="a@b.co", ip="203.0.113.88")
        self.assertEqual(first.status_code, 200)
        second = self._post(icp="devtools", email="a@b.co", ip="203.0.113.88")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["state"], "rate_limited")
        self.assertEqual(second.json()["scope"], "burst")
        self.assertEqual(len(self.runs), 1)

    def test_global_run_backstop_is_capacity_not_error(self):
        with mock.patch.object(settings, "DEMO_GLOBAL_DAILY_RUNS", 0):
            r = self._post(icp="devtools", email="a@b.co", ip="203.0.113.99")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["state"], "capacity")
        self.assertEqual(self.runs, [])

    def test_disabled_flag_is_a_clean_unavailable(self):
        with mock.patch.object(settings, "DEMO_ENABLED", False):
            r = self._post(icp="devtools", email="a@b.co")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["state"], "unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
