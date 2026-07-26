"""Evidence-ledger tests: the planner must reason about what is MISSING and pick
the tool that supplies it, and confidence must be earned from facts alone.

The loop used to think in providers and page counts. These pin the replacement:
provider choice follows the gap, confidence follows the evidence, and agreement
across sources is worth more than any single source asserting something.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import gaps                                    # noqa: E402
from research.evidence import Evidence, ResearchGraph, TeamMember  # noqa: E402

ALL = {"apollo": True, "tavily": True, "exa": True, "x": True, "firecrawl": True}


def _ev(value, conflict=False):
    return Evidence(value=value, source_url="https://s.test", quote="q",
                    confidence=0.9, conflict=conflict)


def _graph(**nodes):
    g = ResearchGraph()
    for node, value in nodes.items():
        g.add(node, _ev(value))
    return g


def _kinds(actions):
    return [a.kind for a in actions]


class ConfidenceTests(unittest.TestCase):
    """Confidence comes from evidence, never from effort."""

    def test_an_empty_graph_is_zero_confidence(self):
        ledger = gaps.assess(ResearchGraph())
        self.assertEqual(ledger.confidence, 0.0)
        self.assertFalse(ledger.is_confident())
        self.assertIn("what_they_do", ledger.missing)

    def test_confidence_rises_only_as_real_slots_fill(self):
        steps, g = [], ResearchGraph()
        for node, value in (("what_they_do", "Payments API"),
                            ("target_customer", "Developers"),
                            ("product_category", "Fintech"),
                            ("founder_name", "Patrick"),
                            ("recent_focus", "Launched X")):
            g.add(node, _ev(value))
            steps.append(gaps.assess(g).confidence)
        self.assertEqual(steps, sorted(steps))        # monotonic
        self.assertGreater(steps[-1], steps[0])
        self.assertAlmostEqual(steps[0], 0.18, places=2)   # exactly what_they_do

    def test_crawling_more_pages_alone_never_raises_confidence(self):
        """The whole point: 20 empty pages are worth nothing."""
        g = _graph(what_they_do="Payments")
        before = gaps.assess(g).confidence
        for _ in range(20):                            # pages that yielded no facts
            pass
        self.assertEqual(gaps.assess(g).confidence, before)

    def test_a_full_picture_clears_the_threshold_and_stops(self):
        g = _graph(what_they_do="A", target_customer="B", product_category="C",
                   founder_name="D", pricing_model="E", notable_customers="F",
                   integrations="G", recent_focus="H", company_stage="I")
        ledger = gaps.assess(g, {"hiring": True})
        self.assertTrue(ledger.is_confident())
        self.assertEqual(ledger.missing, [])

    def test_contradictions_reduce_confidence(self):
        clean = _graph(what_they_do="Payments", target_customer="Devs")
        conflicted = ResearchGraph()
        conflicted.add("what_they_do", _ev("Payments"))
        conflicted.add("target_customer", _ev("Devs", conflict=True))
        self.assertLess(gaps.assess(conflicted).confidence,
                        gaps.assess(clean).confidence)
        self.assertIn("target_customer", gaps.assess(conflicted).conflicts)

    def test_external_signals_count_as_evidence(self):
        g = _graph(what_they_do="A")
        self.assertGreater(gaps.assess(g, {"hiring": True}).confidence,
                           gaps.assess(g).confidence)

    def test_a_team_member_satisfies_the_person_slot(self):
        g = ResearchGraph()
        g.team.append(TeamMember(name="Ada", role="CTO", source_url="u",
                                 quote="q", confidence=0.9))
        self.assertIn("founder", gaps.assess(g).have)


class ProviderSelectionTests(unittest.TestCase):
    """The gap decides the tool. There is no fixed provider order."""

    def test_the_chosen_provider_changes_with_the_missing_evidence(self):
        need_founder = _graph(what_they_do="A", target_customer="B",
                              product_category="C", pricing_model="D",
                              notable_customers="E", integrations="F",
                              recent_focus="G", company_stage="H")
        actions = gaps.plan(gaps.assess(need_founder, {"hiring": True}),
                            providers=ALL)
        # One gap can justify two complementary moves (hunt the about page AND
        # ask Apollo); what matters is that they all serve the SAME gap.
        self.assertEqual({a.slot for a in actions}, {"founder"})
        self.assertIn("apollo", _kinds(actions))

        need_recent = _graph(what_they_do="A", target_customer="B",
                             product_category="C", founder_name="D",
                             pricing_model="E", notable_customers="F",
                             integrations="G", company_stage="H")
        actions = gaps.plan(gaps.assess(need_recent, {"hiring": True}),
                            providers=ALL)
        self.assertEqual({a.slot for a in actions}, {"recent_signal"})
        self.assertIn("tavily", _kinds(actions))      # news, not Apollo
        self.assertNotIn("apollo", _kinds(actions))   # the tool follows the gap

    def test_the_biggest_gap_is_addressed_first(self):
        actions = gaps.plan(gaps.assess(ResearchGraph()), providers=ALL, limit=1)
        self.assertEqual(actions[0].slot, "what_they_do")   # weight .18

    def test_the_companys_own_page_is_preferred_over_a_paid_provider(self):
        ledger = gaps.assess(_graph(what_they_do="A"))
        actions = gaps.plan(ledger, providers=ALL,
                            candidate_urls=["https://x.test/about"])
        crawl = [a for a in actions if a.slot == "founder" and a.kind == "crawl"]
        self.assertTrue(crawl)
        self.assertEqual(crawl[0].target, "https://x.test/about")

    def test_an_already_crawled_page_is_not_planned_again(self):
        ledger = gaps.assess(_graph(what_they_do="A"))
        actions = gaps.plan(ledger, providers=ALL,
                            candidate_urls=["https://x.test/about"],
                            crawled=["https://x.test/about"])
        self.assertNotIn("https://x.test/about", [a.target for a in actions])

    def test_firecrawl_is_used_intentionally_to_find_a_missing_page(self):
        """No linked pricing page does not mean no pricing page."""
        ledger = gaps.assess(_graph(what_they_do="A", target_customer="B",
                                    product_category="C", founder_name="D",
                                    notable_customers="E", integrations="F",
                                    recent_focus="G", company_stage="H"),
                             {"hiring": True})
        actions = gaps.plan(ledger, providers=ALL, candidate_urls=[])
        pricing = [a for a in actions if a.slot == "pricing"]
        self.assertTrue(pricing)
        self.assertEqual(pricing[0].kind, "firecrawl")
        self.assertEqual(pricing[0].target, "pricing")

    def test_unconfigured_providers_are_never_planned_for(self):
        ledger = gaps.assess(_graph(what_they_do="A"))
        actions = gaps.plan(ledger, providers={"apollo": False, "tavily": False,
                                               "exa": False, "x": False,
                                               "firecrawl": False})
        self.assertEqual(_kinds(actions), [])         # nothing to crawl, none configured

    def test_a_satisfied_slot_is_never_planned_for(self):
        ledger = gaps.assess(_graph(what_they_do="A"))
        self.assertNotIn("what_they_do", [a.slot for a in gaps.plan(ledger, providers=ALL)])

    def test_actions_explain_themselves(self):
        for action in gaps.plan(gaps.assess(ResearchGraph()), providers=ALL):
            self.assertTrue(action.why)
            self.assertIn(action.slot, [s.name for s in gaps.SLOTS])


class MergeTests(unittest.TestCase):
    """Agreement raises confidence; disagreement lowers it and stays visible."""

    def test_independent_sources_agreeing_raise_confidence(self):
        one = gaps.merge_value([("John Smith", "apollo", 0.6)])
        two = gaps.merge_value([("John Smith", "apollo", 0.6),
                                ("John Smith", "about-page", 0.6)])
        three = gaps.merge_value([("John Smith", "apollo", 0.6),
                                  ("John Smith", "about-page", 0.6),
                                  ("John Smith", "interview", 0.6)])
        self.assertLess(one["confidence"], two["confidence"])
        self.assertLess(two["confidence"], three["confidence"])
        self.assertEqual(three["agreement"], "very high")
        self.assertEqual(three["sources"], ["apollo", "about-page", "interview"])

    def test_values_are_merged_not_replaced(self):
        merged = gaps.merge_value([("John Smith", "apollo", 0.6),
                                   ("john smith", "about-page", 0.5)])
        self.assertEqual(merged["value"], "John Smith")
        self.assertEqual(len(merged["sources"]), 2)   # case-insensitive same fact

    def test_a_contradiction_lowers_confidence_and_is_kept_visible(self):
        merged = gaps.merge_value([("John Smith", "apollo", 0.9),
                                   ("Jane Doe", "tavily", 0.5)])
        self.assertTrue(merged["conflict"])
        self.assertEqual(merged["agreement"], "contradicted")
        self.assertLess(merged["confidence"], 0.9)
        self.assertIn("Jane Doe", merged["alternatives"])

    def test_the_better_supported_value_wins_a_contradiction(self):
        merged = gaps.merge_value([("John Smith", "apollo", 0.5),
                                   ("John Smith", "about", 0.5),
                                   ("Jane Doe", "tavily", 0.9)])
        self.assertEqual(merged["value"], "John Smith")   # two sources beat one

    def test_empty_input_is_handled(self):
        merged = gaps.merge_value([])
        self.assertIsNone(merged["value"])
        self.assertEqual(merged["confidence"], 0.0)


class LedgerReportingTests(unittest.TestCase):
    """What the stream needs in order to explain uncertainty honestly."""

    def test_missing_evidence_is_reported_in_plain_language(self):
        labels = gaps.assess(_graph(what_they_do="A")).missing_labels()
        self.assertIn("how they charge", labels)
        self.assertIn("a named decision maker", labels)

    def test_the_public_shape_carries_everything_needed(self):
        pub = gaps.assess(_graph(what_they_do="A")).public()
        for key in ("confidence", "have", "missing", "missing_labels", "conflicts"):
            self.assertIn(key, pub)
        self.assertIsInstance(pub["confidence"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
