"""End-to-end tests for evidence-gap EXECUTION.

The planner used to decide and then throw the decision away: gaps.plan() produced
Apollo/Tavily/Firecrawl/X actions that nothing ever ran. These tests go through
the real loop and assert the whole chain — plan, call, merge into the real
ResearchGraph, provenance, confidence movement, and stopping — rather than
checking the plan in isolation.

Safety is asserted as hard as behaviour: nothing runs with the flag off, a
disabled provider is never called, and the budget is never exceeded.
"""

import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import evidence_loop, gaps                    # noqa: E402
from research.evidence import Evidence, ResearchGraph       # noqa: E402


def graph_with(*nodes):
    g = ResearchGraph()
    for n in nodes:
        g.add(n, Evidence(f"{n}-value", "https://acme.com", "q", 0.9))
    return g


def _on(*patches):
    """Escalation enabled, plus whatever the case needs.

    `_page_exists` is stubbed to True by default so no test reaches the network
    for a guessed path (that check is real HTTP and made this file take minutes).
    A case that cares about it patches it again, and being entered later, wins.
    """
    st = ExitStack()
    st.enter_context(mock.patch.object(evidence_loop, "PLANNER_ESCALATION_ENABLED", True))
    st.enter_context(mock.patch.object(evidence_loop, "_page_exists", return_value=True))
    for p in patches:
        st.enter_context(p)
    return st


def _avail(**kw):
    base = {"tavily": False, "x": False, "firecrawl": False, "apollo": False, "exa": False}
    base.update(kw)
    return mock.patch.object(evidence_loop, "_available", return_value=base)


def _executed(out):
    return [r for r in out["records"] if r["status"] == "succeeded"]


class RecentSignalTests(unittest.TestCase):
    """Missing recent signal -> Tavily selected, called, merged; X stands down."""

    def setUp(self):
        # 0.62 confidence: below the bar, with recent_signal the biggest gap.
        self.graph = graph_with("what_they_do", "founder_name", "product_category",
                                "target_customer", "pricing_model")
        self.x_calls = []

    def _run(self, results):
        with _on(_avail(tavily=True, x=True),
                 mock.patch("research.tavily.search", return_value=results),
                 mock.patch("research.x_search.search_recent_posts",
                            side_effect=lambda *a, **k: self.x_calls.append(a)
                            or {"status": "empty", "posts": []})):
            return evidence_loop.run(self.graph, url="https://acme.com", company="Acme")

    def test_tavily_runs_merges_and_raises_confidence(self):
        out = self._run([{"title": "Acme raises $12M Series A",
                          "url": "https://news.test/acme",
                          "content": "Acme announced a Series A",
                          "published_date": "2026-07-01"}])
        done = _executed(out)
        self.assertEqual(len(done), 1)
        rec = done[0]
        self.assertEqual(rec["provider"], "tavily")
        self.assertEqual(rec["slot"], "recent_signal")
        self.assertGreater(rec["gain"], 0)                       # confidence moved
        self.assertEqual(rec["source_url"], "https://news.test/acme")   # provenance

        # ...and it landed in the REAL graph, dated, with the source kept.
        ev = self.graph.best("recent_focus")
        self.assertIsNotNone(ev)
        self.assertIn("2026-07-01", ev.value)
        self.assertEqual(ev.source_url, "https://news.test/acme")

    def test_x_is_not_called_once_tavily_closes_the_gap(self):
        self._run([{"title": "Acme raises $12M", "url": "https://news.test/a",
                    "content": "c", "published_date": "2026-07-01"}])
        self.assertEqual(self.x_calls, [])

    def test_the_same_action_is_never_retried(self):
        out = self._run([])                       # nothing found, every time
        keys = [(r["provider"], r["target"]) for r in out["records"]]
        self.assertEqual(len(keys), len(set(keys)))


class PricingFirecrawlTests(unittest.TestCase):
    """Missing pricing -> Firecrawl intentionally targets a pricing path."""

    def test_firecrawl_targets_the_pricing_page_and_merges(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        def extract(pages):
            graph.add("pricing_model",
                      Evidence("$29/mo", pages[0][0], "Plans start at $29/mo", 0.85))
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape",
                            return_value={"url": "https://acme.com/pricing",
                                          "markdown": "Plans start at $29/mo " * 30,
                                          "title": "Pricing"})) :
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme",
                                    extract_fn=extract,
                                    known_urls=["https://acme.com/pricing"])
        done = _executed(out)
        self.assertTrue(done)
        rec = done[0]
        self.assertEqual(rec["provider"], "firecrawl")
        self.assertEqual(rec["slot"], "pricing")
        self.assertTrue(rec["target"].endswith("/pricing"))       # intentional path
        self.assertEqual(rec["source_url"], "https://acme.com/pricing")
        self.assertGreater(rec["gain"], 0)
        self.assertEqual(graph.best("pricing_model").value, "$29/mo")

    def test_a_page_that_does_not_exist_is_recorded_as_failed_not_invented(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape", return_value=None)):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme",
                                    extract_fn=lambda pages: None,
                                    known_urls=["https://acme.com/pricing"])
        self.assertEqual(_executed(out), [])
        self.assertTrue(any(r["status"] == "no_evidence" for r in out["records"]))
        self.assertIsNone(graph.best("pricing_model"))


class MergeAndConflictTests(unittest.TestCase):
    """Provider values go into the existing Evidence model, never a parallel one."""

    def test_a_contradiction_keeps_both_values_and_lowers_confidence(self):
        g = ResearchGraph()
        g.add("founder_name", Evidence("John Smith", "https://acme.com/about", "q", 0.9))
        before = gaps.assess(g).confidence
        evidence_loop._merge(g, "founder_name", "Jane Doe",
                             source_url="https://li/jane", provider="apollo",
                             quote="CEO", confidence=0.8)
        after = gaps.assess(g).confidence

        values = [e.value for e in g.nodes["founder_name"]]
        self.assertIn("John Smith", values)          # neither is discarded
        self.assertIn("Jane Doe", values)
        self.assertTrue(all(e.conflict for e in g.nodes["founder_name"]))
        self.assertIn("founder_name", gaps.assess(g).conflicts)
        self.assertLess(after, before)
        # Provenance survives for BOTH sides.
        sources = {e.source_url for e in g.nodes["founder_name"]}
        self.assertEqual(sources, {"https://acme.com/about", "https://li/jane"})

    def test_agreement_corroborates_instead_of_duplicating(self):
        g = ResearchGraph()
        g.add("founder_name", Evidence("John Smith", "https://acme.com/about", "q", 0.7))
        evidence_loop._merge(g, "founder_name", "John Smith",
                             source_url="https://li/john", provider="apollo",
                             quote="CEO", confidence=0.8)
        self.assertEqual(len(g.nodes["founder_name"]), 1)
        entry = g.nodes["founder_name"][0]
        self.assertEqual(entry.corroborations, 2)
        self.assertGreater(entry.confidence, 0.7)
        self.assertFalse(entry.conflict)

    def test_a_weaker_value_never_overwrites_a_stronger_one(self):
        g = ResearchGraph()
        g.add("what_they_do", Evidence("Payments API", "https://acme.com", "q", 0.95))
        evidence_loop._merge(g, "what_they_do", "Some vague thing",
                             source_url="https://news.test", provider="tavily",
                             quote="q", confidence=0.3)
        self.assertEqual(g.best("what_they_do").value, "Payments API")


class SafetyTests(unittest.TestCase):
    """The guardrails, asserted as hard as the behaviour."""

    def test_nothing_runs_while_the_flag_is_off(self):
        called = []
        with mock.patch.object(evidence_loop, "PLANNER_ESCALATION_ENABLED", False), \
             mock.patch("research.tavily.search", side_effect=lambda *a, **k: called.append(a)):
            out = evidence_loop.run(graph_with("what_they_do"), url="https://acme.com")
        self.assertEqual(out["stop_reason"], "escalation_disabled")
        self.assertEqual(out["succeeded"], 0)
        self.assertEqual(out["estimated_cost_usd"], 0.0)
        self.assertEqual(called, [])

    def test_apollo_people_enrichment_needs_its_own_flag(self):
        called = []
        with _on(_avail(apollo=True),
                 mock.patch.object(evidence_loop, "APOLLO_ENRICH_ENABLED", False),
                 mock.patch("research.apollo.enrich_person",
                            side_effect=lambda **k: called.append(k))):
            out = evidence_loop.run(graph_with("what_they_do"),
                                    url="https://acme.com", company="Acme")
        self.assertEqual(called, [])                       # never called
        self.assertEqual(out["succeeded"], 0)
        self.assertEqual(out["estimated_cost_usd"], 0.0)   # no budget consumed
        skips = [r for r in out["records"] if r["status"] == "skipped"]
        self.assertTrue(skips)
        self.assertIn("APOLLO_ENRICH_ENABLED", skips[0]["reason"])

    def test_apollo_runs_and_merges_when_both_flags_are_on(self):
        graph = graph_with("what_they_do", "product_category", "target_customer",
                           "pricing_model", "recent_focus")
        with _on(_avail(apollo=True),
                 mock.patch.object(evidence_loop, "APOLLO_ENRICH_ENABLED", True),
                 mock.patch("research.apollo.enrich_person",
                            return_value={"status": "ok", "person": {
                                "name": "Jane Doe", "title": "CEO",
                                "linkedin_url": "https://li/jane", "confidence": 0.8}})):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        done = _executed(out)
        self.assertTrue(done)
        self.assertEqual(done[0]["provider"], "apollo")
        self.assertEqual(done[0]["slot"], "founder")
        self.assertEqual(graph.best("founder_name").value, "Jane Doe")
        self.assertEqual(graph.best("founder_name").source_url, "https://li/jane")
        self.assertGreater(done[0]["gain"], 0)

    def test_an_unconfigured_provider_is_never_called_or_claimed(self):
        called = []
        with _on(_avail(tavily=False),
                 mock.patch("research.tavily.search",
                            side_effect=lambda *a, **k: called.append(a))):
            out = evidence_loop.run(graph_with("what_they_do"),
                                    url="https://acme.com", company="Acme")
        self.assertEqual(called, [])
        self.assertFalse(any(r["provider"] == "tavily" and r["status"] == "succeeded"
                             for r in out["records"]))

    def test_the_action_budget_is_never_exceeded(self):
        # Two providers, so the per-provider cap (2) does not bind before the
        # overall action budget (3) does.
        with _on(_avail(firecrawl=True, tavily=True),
                 mock.patch("research.firecrawl.scrape", return_value=None),
                 mock.patch("research.tavily.search", return_value=[])):
            out = evidence_loop.run(
                graph_with("what_they_do"), url="https://acme.com", company="Acme",
                extract_fn=lambda pages: None,
                known_urls=["https://acme.com/about", "https://acme.com/customers",
                            "https://acme.com/pricing", "https://acme.com/blog"])
        self.assertEqual(out["attempted"], evidence_loop.EVIDENCE_MAX_ACTIONS)
        self.assertEqual(out["stop_reason"], "budget_exhausted")

    def test_the_per_provider_cap_can_bind_before_the_action_budget(self):
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape", return_value=None)):
            out = evidence_loop.run(
                graph_with("what_they_do"), url="https://acme.com", company="Acme",
                extract_fn=lambda pages: None,
                known_urls=["https://acme.com/about", "https://acme.com/customers",
                            "https://acme.com/blog"])
        self.assertEqual(out["attempted"], evidence_loop.EVIDENCE_MAX_PER_PROVIDER)
        self.assertEqual(out["stop_reason"], "no_useful_actions")

    def test_per_provider_cap_is_respected(self):
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search", return_value=[])):
            out = evidence_loop.run(graph_with("what_they_do"),
                                    url="https://acme.com", company="Acme")
        used = [r for r in out["records"]
                if r["provider"] == "tavily" and r["status"] != "skipped"]
        self.assertLessEqual(len(used), evidence_loop.EVIDENCE_MAX_PER_PROVIDER)

    def test_it_stops_as_soon_as_confidence_is_sufficient(self):
        rich = graph_with("what_they_do", "founder_name", "product_category",
                          "target_customer", "pricing_model", "notable_customers",
                          "integrations", "recent_focus", "company_stage")
        called = []
        with _on(_avail(tavily=True, firecrawl=True),
                 mock.patch("research.tavily.search",
                            side_effect=lambda *a, **k: called.append(a))):
            out = evidence_loop.run(rich, url="https://acme.com", company="Acme",
                                    signals={"hiring": True})
        self.assertEqual(out["stop_reason"], "confidence_reached")
        self.assertEqual(called, [])          # nothing spent when already confident

    def test_every_decision_is_recorded_for_audit(self):
        with _on(_avail(apollo=True),
                 mock.patch.object(evidence_loop, "APOLLO_ENRICH_ENABLED", False)):
            out = evidence_loop.run(graph_with("what_they_do"), url="https://acme.com")
        for rec in out["records"]:
            self.assertIn(rec["status"],
                          ("succeeded", "no_evidence", "failed", "skipped"))
            self.assertTrue(rec["reason"] or rec["value"])
            self.assertIn("cost_usd", rec)


class NarrationTests(unittest.TestCase):
    """Narrate only what genuinely ran."""

    def test_narration_fires_for_a_real_action_and_names_the_gap(self):
        lines = []
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "pricing_model")
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search",
                            return_value=[{"title": "Acme raises", "url": "https://n.test",
                                           "content": "c", "published_date": "2026-07-01"}])):
            evidence_loop.run(graph, url="https://acme.com", company="Acme",
                              narrate=lines.append)
        self.assertTrue(lines)
        self.assertIn("recent", " ".join(lines).lower())

    def test_a_disabled_provider_is_never_narrated_as_used(self):
        lines = []
        with _on(_avail(apollo=True),
                 mock.patch.object(evidence_loop, "APOLLO_ENRICH_ENABLED", False)):
            evidence_loop.run(graph_with("what_they_do"), url="https://acme.com",
                              narrate=lines.append)
        self.assertNotIn("apollo", " ".join(lines).lower())

    def test_the_flag_being_off_narrates_nothing_at_all(self):
        lines = []
        with mock.patch.object(evidence_loop, "PLANNER_ESCALATION_ENABLED", False):
            evidence_loop.run(graph_with("what_they_do"), url="https://acme.com",
                              narrate=lines.append)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class UnrunnableProviderTests(unittest.TestCase):
    """A planned provider with no executor must not consume the budget.

    Found in a live apple.com trace: gaps.plan suggested Exa for a
    target-customer gap, the loop had no Exa executor, and the attempt burned one
    of three action slots and recorded a failure.
    """

    def test_a_provider_with_no_executor_is_skipped_not_attempted(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "pricing_model", "recent_focus")
        with _on(_avail(exa=True, tavily=False, firecrawl=False, x=False, apollo=False)):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        self.assertFalse(any(r["provider"] == "exa" for r in out["records"]))
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["estimated_cost_usd"], 0.0)
        self.assertEqual(out["stop_reason"], "no_useful_actions")

    def test_a_runnable_provider_is_still_reached_past_an_unrunnable_one(self):
        """Exa outranking Tavily for a slot must not block Tavily's own gap."""
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "pricing_model")
        with _on(_avail(exa=True, tavily=True),
                 mock.patch("research.tavily.search",
                            return_value=[{"title": "Acme ships v2",
                                           "url": "https://n.test/a", "content": "c",
                                           "published_date": "2026-07-02"}])):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        done = _executed(out)
        self.assertTrue(done)
        self.assertEqual(done[0]["provider"], "tavily")


class MetricSemanticsTests(unittest.TestCase):
    """"executed=0 failed=3 cost=$0.008" implied three broken calls when three
    requests had in fact run fine and simply found nothing. The four outcomes are
    disjoint, and cost is attributable to what actually left the process."""

    def test_a_working_call_that_finds_nothing_is_not_a_failure(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape",
                            return_value={"url": "https://acme.com/pricing",
                                          "markdown": "no prices here " * 40,
                                          "title": "Pricing"})):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme",
                                    extract_fn=lambda pages: None,
                                    known_urls=["https://acme.com/pricing"])
        first = out["records"][0]
        self.assertEqual(first["status"], "no_evidence")   # ran fine, found nothing
        self.assertEqual(out["failed"], 0)                 # NOT an infrastructure fault
        self.assertEqual(out["succeeded"], 0)
        self.assertGreaterEqual(out["attempted"], 1)
        self.assertGreater(out["estimated_cost_usd"], 0)   # it still cost money

    def test_a_provider_exception_is_a_failure(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape",
                            side_effect=RuntimeError("connection reset"))):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme",
                                    known_urls=["https://acme.com/pricing"])
        self.assertGreaterEqual(out["failed"], 1)
        self.assertEqual(out["records"][0]["status"], "failed")
        self.assertEqual(out["no_evidence"], 0)

    def test_a_skipped_action_costs_nothing_and_is_not_attempted(self):
        with _on(_avail(apollo=True),
                 mock.patch.object(evidence_loop, "APOLLO_ENRICH_ENABLED", False)):
            out = evidence_loop.run(graph_with("what_they_do"), url="https://acme.com")
        self.assertEqual(out["attempted"], 0)
        self.assertGreater(out["skipped"], 0)
        self.assertEqual(out["estimated_cost_usd"], 0.0)

    def test_confidence_gained_counts_only_successful_actions(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "pricing_model")
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search",
                            return_value=[{"title": "Acme ships v2",
                                           "url": "https://n.test/a", "content": "c",
                                           "published_date": "2026-07-02"}])):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        self.assertEqual(out["succeeded"], 1)
        self.assertGreater(out["confidence_gained"], 0)


class TargetResolutionTests(unittest.TestCase):
    """apple.com paid to scrape /about and /customers and got nothing. A target
    must be a page the site really has, checked for free, before any paid call."""

    def test_a_known_site_url_is_preferred_over_a_guess(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        seen = []
        with _on(_avail(firecrawl=True),
                 mock.patch("research.firecrawl.scrape",
                            side_effect=lambda u: seen.append(u) or None)):
            evidence_loop.run(graph, url="https://acme.com", company="Acme",
                              extract_fn=lambda pages: None,
                              known_urls=["https://acme.com/plans-and-pricing"])
        # The site's real page is used; the guessed /pricing path never is.
        self.assertEqual(seen[0], "https://acme.com/plans-and-pricing")
        self.assertNotIn("https://acme.com/pricing", seen)

    def test_a_guessed_path_that_does_not_exist_is_skipped_before_paying(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "recent_focus")
        scraped = []
        with _on(_avail(firecrawl=True),
                 mock.patch.object(evidence_loop, "_page_exists", return_value=False),
                 mock.patch("research.firecrawl.scrape",
                            side_effect=lambda u: scraped.append(u))):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme",
                                    extract_fn=lambda pages: None)
        self.assertEqual(scraped, [])                    # never paid for a 404
        self.assertEqual(out["estimated_cost_usd"], 0.0)
        self.assertTrue(any("no " in r["reason"] and "page exists" in r["reason"]
                            for r in out["records"]))

    def test_a_slot_that_came_back_empty_is_not_asked_about_again(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "pricing_model")
        with _on(_avail(tavily=True, x=True),
                 mock.patch("research.tavily.search", return_value=[]),
                 mock.patch("research.x_search.search_recent_posts",
                            return_value={"status": "empty", "posts": []})):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        slots = [r["slot"] for r in out["records"] if r["status"] != "skipped"]
        self.assertEqual(len(slots), len(set(slots)))    # one attempt per gap

    def test_it_stops_when_the_bar_is_unreachable_within_the_budget(self):
        """Almost nothing known and only low-value gaps addressable: buying a
        fractional climb it can never finish is not worth the money."""
        graph = graph_with("what_they_do")
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search", return_value=[])):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        for rec in out["records"]:
            if rec["status"] == "skipped":
                continue
            self.assertGreaterEqual(gaps.gain_if_filled(rec["slot"]), 0.10)


class ProviderRefusalTests(unittest.TestCase):
    """A provider that REFUSES (quota/auth) must not be reported as "found
    nothing". Tavily was answering HTTP 432 "exceeds your plan's usage limit" for
    every query and the loop blamed the world for an account problem."""

    def test_a_quota_refusal_is_a_failure_not_an_empty_answer(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "pricing_model")
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search", return_value=[]),
                 mock.patch.object(evidence_loop, "_provider_refusal",
                                   return_value="refused: plan or usage limit reached")):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        first = out["records"][0]
        self.assertEqual(first["status"], "failed")
        self.assertIn("usage limit", first["reason"])
        self.assertEqual(out["no_evidence"], 0)

    def test_a_genuine_empty_answer_is_still_no_evidence(self):
        graph = graph_with("what_they_do", "founder_name", "product_category",
                           "target_customer", "pricing_model")
        with _on(_avail(tavily=True),
                 mock.patch("research.tavily.search", return_value=[]),
                 mock.patch.object(evidence_loop, "_provider_refusal", return_value="")):
            out = evidence_loop.run(graph, url="https://acme.com", company="Acme")
        self.assertEqual(out["records"][0]["status"], "no_evidence")
        self.assertEqual(out["failed"], 0)
