"""Reliability tests: one provider failing must never end a run, and a failure
the user sees must tell them what happened and what to do next.

These pin real reported problems: "Research apple.com" answering only that it
could not research, and campaign creation answering "Orchestration failed".
Nothing here touches the network.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import fetcher, pipeline           # noqa: E402
from server.campaign_api import explain_failure  # noqa: E402


class FetchFallbackChainTests(unittest.TestCase):
    """requests -> browser -> Firecrawl -> Jina. Each rung only runs when the one
    before it failed, and the paid rungs only when explicitly allowed."""

    def test_the_fast_path_is_used_and_nothing_paid_is_touched(self):
        with mock.patch.object(fetcher, "fetch_static", return_value=(True, "<p>" + "x" * 900 + "</p>")), \
             mock.patch.object(fetcher, "_firecrawl_text") as fire, \
             mock.patch.object(fetcher, "_jina_text") as jina:
            ok, _, method = fetcher.fetch_with_fallbacks("https://x.test", allow_paid=True)
        self.assertTrue(ok)
        self.assertEqual(method, "fast")
        fire.assert_not_called()
        jina.assert_not_called()

    def test_a_blocked_site_escalates_to_firecrawl(self):
        render = mock.Mock()
        render.render.return_value = None                 # browser blocked too
        with mock.patch.object(fetcher, "fetch_static", return_value=(False, "HTTP 403")), \
             mock.patch.object(fetcher, "_firecrawl_text", return_value="F" * 400), \
             mock.patch.object(fetcher, "_jina_text") as jina:
            ok, content, method = fetcher.fetch_with_fallbacks(
                "https://apple.com", render_fetcher=render, allow_paid=True)
        self.assertTrue(ok)
        self.assertEqual(method, "firecrawl")
        self.assertEqual(len(content), 400)
        jina.assert_not_called()                          # stopped at the first success

    def test_jina_is_the_last_resort(self):
        render = mock.Mock()
        render.render.return_value = None
        with mock.patch.object(fetcher, "fetch_static", return_value=(False, "HTTP 403")), \
             mock.patch.object(fetcher, "_firecrawl_text", return_value=""), \
             mock.patch.object(fetcher, "_jina_text", return_value="J" * 400):
            ok, _, method = fetcher.fetch_with_fallbacks(
                "https://apple.com", render_fetcher=render, allow_paid=True)
        self.assertTrue(ok)
        self.assertEqual(method, "jina")

    def test_a_throwing_provider_hands_on_instead_of_breaking_the_chain(self):
        render = mock.Mock()
        render.render.return_value = None
        with mock.patch.object(fetcher, "fetch_static", return_value=(False, "HTTP 403")), \
             mock.patch.object(fetcher, "_firecrawl_text", side_effect=RuntimeError("boom")), \
             mock.patch.object(fetcher, "_jina_text", return_value="J" * 400):
            ok, _, method = fetcher.fetch_with_fallbacks(
                "https://x.test", render_fetcher=render, allow_paid=True)
        self.assertTrue(ok)
        self.assertEqual(method, "jina")

    def test_a_thin_fallback_response_is_not_treated_as_success(self):
        """An error page or cookie wall comes back as a short string; accepting it
        would be worse than failing, because everything downstream would treat it
        as the company's own content."""
        render = mock.Mock()
        render.render.return_value = None
        with mock.patch.object(fetcher, "fetch_static", return_value=(False, "HTTP 403")), \
             mock.patch.object(fetcher, "_firecrawl_text", return_value="Access denied"), \
             mock.patch.object(fetcher, "_jina_text", return_value=""):
            ok, _, _ = fetcher.fetch_with_fallbacks(
                "https://x.test", render_fetcher=render, allow_paid=True)
        self.assertFalse(ok)

    def test_paid_fallbacks_stay_off_for_ordinary_subpages(self):
        render = mock.Mock()
        render.render.return_value = None
        with mock.patch.object(fetcher, "fetch_static", return_value=(False, "HTTP 404")), \
             mock.patch.object(fetcher, "_firecrawl_text") as fire, \
             mock.patch.object(fetcher, "_jina_text") as jina:
            ok, _, _ = fetcher.fetch_with_fallbacks(
                "https://x.test/blog", render_fetcher=render)   # allow_paid=False
        self.assertFalse(ok)
        fire.assert_not_called()
        jina.assert_not_called()


class BlockedSiteResearchTests(unittest.TestCase):
    """A site that refuses automation must not end the research."""

    def test_public_sources_are_used_when_the_site_cannot_be_read(self):
        pages = [("https://news.test/apple", "Apple designs consumer hardware " * 20)]
        with mock.patch.object(pipeline, "_fetch_page", return_value=(False, "HTTP 403", "fast")), \
             mock.patch.object(pipeline, "_public_source_pages", return_value=pages), \
             mock.patch.object(pipeline, "_research_from_public_sources",
                               return_value={"status": "ok"}) as fallback:
            out = pipeline._adaptive_research("https://apple.com", mock.Mock(), [])
        self.assertEqual(out["status"], "ok")
        note = fallback.call_args[0][2]
        self.assertIn("apple.com", note)
        self.assertIn("blocked automated crawling", note)
        self.assertIn("public sources", note)

    def test_giving_up_explains_what_was_tried_and_what_to_do(self):
        with mock.patch.object(pipeline, "_fetch_page", return_value=(False, "HTTP 403", "fast")), \
             mock.patch.object(pipeline, "_public_source_pages", return_value=[]):
            out = pipeline._adaptive_research("https://apple.com", mock.Mock(), [])
        self.assertEqual(out["status"], "error")
        self.assertTrue(out["site_unreachable"])
        reason = out["error"].lower()
        self.assertIn("apple.com", reason)
        self.assertIn("403", reason)
        self.assertIn("tried", reason)          # names the attempts
        self.assertIn("paste", reason)          # offers a next step
        self.assertNotIn("traceback", reason)

    def test_the_public_source_result_is_labelled_as_second_hand(self):
        pages = [("https://news.test/a", "Acme builds payments software " * 30)]
        with mock.patch.object(pipeline, "_extract_pages_raw", return_value={}), \
             mock.patch.object(pipeline, "_score_from_raw",
                               return_value=({"what_they_do": "payments"}, [], 55, {})), \
             mock.patch.object(pipeline, "_finalize",
                               return_value={"status": "ok", "research_score": 55}):
            out = pipeline._research_from_public_sources("https://acme.test", pages, "NOTE-TEXT")
        self.assertTrue(out["site_unreachable"])
        self.assertEqual(out["evidence_note"], "NOTE-TEXT")


class OrchestrationErrorTests(unittest.TestCase):
    """"Orchestration failed" told the user nothing. Every branch must name what
    succeeded, what broke, and the next step, without leaking internals."""

    def test_each_fault_class_becomes_an_actionable_sentence(self):
        cases = [
            (TimeoutError("workflow timed out"), "did not respond in time", "retry"),
            (RuntimeError("redis unreachable"), "redis", "retry"),
            (RuntimeError("could not connect to database"), "database", "nothing was sent"),
            (RuntimeError("no mailbox connected"), "mailbox", "settings"),
            (RuntimeError("429 rate limit"), "rate limit", "wait"),
        ]
        for exc, expect_cause, expect_action in cases:
            msg = explain_failure(exc, completed="Research and drafting").lower()
            self.assertIn("research and drafting succeeded", msg)
            self.assertIn(expect_cause, msg)
            self.assertIn(expect_action, msg)

    def test_nothing_internal_leaks(self):
        exc = RuntimeError('postgres://user:hunter2@db.internal:5432 connection refused\n'
                           '  File "x.py", line 3, in y\n    Traceback (most recent call last)')
        msg = explain_failure(exc, completed="Research")
        self.assertNotIn("hunter2", msg)
        self.assertNotIn("Traceback", msg)
        self.assertNotIn("File \"", msg)
        self.assertNotIn("5432", msg)

    def test_an_unknown_fault_still_reads_as_a_sentence(self):
        msg = explain_failure(ValueError("odd"))
        self.assertIn("could not be created because of an unexpected fault", msg)
        self.assertTrue(msg.endswith("degraded."), msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BenchmarkSiteTests(unittest.TestCase):
    """The five sites that exposed the weak-homepage problem.

    Offline: the point is not to re-crawl the live web on every test run, it is
    that a nav-heavy homepage must never be accepted as a finished run. Each case
    feeds a homepage whose own text yields almost nothing and asserts the planner
    keeps going after evidence rather than declaring victory.
    """

    SITES = ("apple.com", "stripe.com", "notion.so", "linear.app", "openai.com")

    def test_a_nav_only_homepage_is_never_enough_on_its_own(self):
        from research import gaps
        from research.evidence import Evidence, ResearchGraph
        for host in self.SITES:
            graph = ResearchGraph()
            graph.add("what_they_do", Evidence(value=f"{host} does something",
                                               source_url=f"https://{host}",
                                               quote="q", confidence=0.9))
            ledger = gaps.assess(graph)
            self.assertFalse(ledger.is_confident(), host)
            self.assertLess(ledger.confidence, gaps.WEAK_PAGE_CONFIDENCE, host)
            # ...and it must know where to look next, on the company's own site.
            actions = gaps.plan(ledger, providers={"firecrawl": True, "apollo": True},
                                candidate_urls=[f"https://{host}/about",
                                                f"https://{host}/pricing"])
            self.assertTrue(actions, host)
            self.assertTrue(any(a.kind == "crawl" for a in actions), host)

    def test_the_pipeline_reports_its_evidence_ledger(self):
        """The ledger must reach the caller so uncertainty can be explained, and
        must NOT collide with the per-field evidence map."""
        from research import gaps
        from research.evidence import Evidence, ResearchGraph
        graph = ResearchGraph()
        graph.add("what_they_do", Evidence(value="Payments", source_url="u",
                                           quote="q", confidence=0.9))
        out = pipeline._finalize("https://stripe.com", [("https://stripe.com", "text")],
                                 graph, [], 40, {}, False, "done", {})
        self.assertIn("evidence_ledger", out)
        self.assertIn("confidence", out["evidence_ledger"])
        self.assertIn("missing_labels", out["evidence_ledger"])
        self.assertIn("evidence", out)          # the original provenance map survives
        self.assertIsNot(out["evidence"], out["evidence_ledger"])
