"""Tests for the Lead Qualification Agent (agents/qualification.py).

Deterministic decision logic across every branch: reject (poor fit /
disqualifier / ICP-exclude), research_more (no data / thin research), continue,
and high_priority — plus ICP matching, buying-intent detection, scoring
components, the structured shape, workspace convenience, and determinism.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import qualification as q  # noqa: E402


def research(score=80, status="ok", **over):
    data = {"company_name": "Acme", "what_they_do": "AI infra for engineering teams",
            "target_customer": "engineering teams", "industries_served": "developer tools",
            "primary_contact_name": "Jane Doe", "primary_contact_role": "VP Engineering",
            "recent_focus": "just raised a Series B and is hiring aggressively",
            "metrics_or_traction": "used by Vercel and Ramp",
            "notable_customers": "Vercel, Ramp"}
    data.update(over)
    return {"status": status, "research_score": score,
            "hooks": [{"text": "raised Series B"}], "data": data}


DEV_ICP = {"industries": ["developer tools"], "keywords": ["infra", "engineering"],
           "roles": ["VP Engineering"]}


class RecommendationBranchTests(unittest.TestCase):
    def test_no_data_recommends_research_more(self):
        r = q.qualify()
        self.assertEqual(r.recommendation, q.RESEARCH_MORE)
        self.assertEqual(r.fit_level, q.FIT_UNKNOWN)

    def test_weak_research_recommends_research_more(self):
        r = q.qualify(research={"status": "skip", "research_score": 15, "data": {}})
        self.assertEqual(r.recommendation, q.RESEARCH_MORE)
        self.assertEqual(r.fit_level, q.FIT_UNKNOWN)
        self.assertLessEqual(r.confidence, 40)

    def test_low_score_research_still_research_more(self):
        r = q.qualify(research=research(score=20))
        self.assertEqual(r.recommendation, q.RESEARCH_MORE)

    def test_strong_fit_plus_intent_is_high_priority(self):
        r = q.qualify(research=research(), icp=DEV_ICP)
        self.assertEqual(r.recommendation, q.HIGH_PRIORITY)
        self.assertEqual(r.priority, q.PRIORITY_HIGH)
        self.assertEqual(r.fit_level, q.FIT_STRONG)
        self.assertGreaterEqual(r.qualification_score, q.QUALIFY_HIGH)

    def test_decent_fit_no_intent_is_continue(self):
        r = q.qualify(research=research(score=72, recent_focus="", metrics_or_traction=""),
                      icp={"industries": ["developer tools"]})
        self.assertEqual(r.recommendation, q.CONTINUE)
        self.assertEqual(r.priority, q.PRIORITY_MEDIUM)

    def test_poor_fit_good_research_is_reject(self):
        r = q.qualify(research=research(
            score=75, what_they_do="a neighbourhood bakery", target_customer="local shoppers",
            industries_served="food retail", notable_customers="", metrics_or_traction="",
            recent_focus=""), icp={"industries": ["fintech"], "keywords": ["payments"]})
        self.assertEqual(r.recommendation, q.REJECT)
        self.assertEqual(r.fit_level, q.FIT_WEAK)      # judged, not "unknown"

    def test_hard_disqualifier_is_reject(self):
        r = q.qualify(research=research(recent_focus="the company is shutting down next month"))
        self.assertEqual(r.recommendation, q.REJECT)
        self.assertTrue(r.disqualifiers)
        self.assertEqual(r.priority, q.PRIORITY_NONE)

    def test_icp_exclude_is_reject(self):
        r = q.qualify(research=research(what_they_do="we are a staffing agency"),
                      icp={"exclude": ["staffing agency"]})
        self.assertEqual(r.recommendation, q.REJECT)
        self.assertIn("excluded by ICP: staffing agency", r.disqualifiers)

    def test_disqualifier_beats_everything(self):
        # even an otherwise-perfect lead is rejected on a hard disqualifier
        r = q.qualify(research=research(recent_focus="raised Series B but is now defunct"),
                      icp=DEV_ICP)
        self.assertEqual(r.recommendation, q.REJECT)


class SignalTests(unittest.TestCase):
    def test_buying_intent_detected(self):
        r = q.qualify(research=research(), icp=DEV_ICP)
        self.assertGreater(r.signals["intent_signals"], 0)
        self.assertTrue(any("Buying signal" in s for s in r.strongest_signals))

    def test_no_intent_when_absent(self):
        r = q.qualify(research=research(score=72, recent_focus="a quiet steady business",
                                        metrics_or_traction=""), icp={"industries": ["developer tools"]})
        self.assertEqual(r.signals["intent_signals"], 0)

    def test_icp_match_ratio_and_strongest_signals(self):
        r = q.qualify(research=research(), icp=DEV_ICP)
        self.assertIsNotNone(r.signals["icp_match_ratio"])
        self.assertTrue(any("ICP match" in s for s in r.strongest_signals))

    def test_generic_fit_without_icp(self):
        r = q.qualify(research=research())        # no ICP -> generic fit heuristic
        self.assertIsNone(r.signals["icp_match_ratio"])
        self.assertIn(r.recommendation, (q.CONTINUE, q.HIGH_PRIORITY))

    def test_missing_information_lists_gaps(self):
        r = q.qualify(research=research(score=60, industries_served="", target_customer="",
                                       recent_focus="", metrics_or_traction="",
                                       notable_customers="", primary_contact_name="",
                                       founder_name=""))
        self.assertTrue(r.missing_information)

    def test_score_never_exceeds_100(self):
        r = q.qualify(research=research(score=100), icp=DEV_ICP)
        self.assertLessEqual(r.qualification_score, 100)


class ShapeTests(unittest.TestCase):
    def test_to_dict_has_required_fields(self):
        d = q.qualify(research=research(), icp=DEV_ICP).to_dict()
        for key in ("qualification_score", "fit_level", "priority", "recommendation",
                    "confidence", "strongest_signals", "disqualifiers",
                    "missing_information", "next_best_action"):
            self.assertIn(key, d)
        self.assertIn(d["recommendation"], q.RECOMMENDATIONS)
        self.assertTrue(d["next_best_action"])

    def test_qualify_from_workspace(self):
        ws = {"research": research(), "icp": DEV_ICP}
        self.assertEqual(q.qualify_from_workspace(ws).recommendation, q.HIGH_PRIORITY)
        self.assertEqual(q.qualify_from_workspace({}).recommendation, q.RESEARCH_MORE)

    def test_is_deterministic(self):
        self.assertEqual(q.qualify(research=research(), icp=DEV_ICP).to_dict(),
                         q.qualify(research=research(), icp=DEV_ICP).to_dict())

    def test_reasoning_summary_present_but_internal(self):
        r = q.qualify(research=research(), icp=DEV_ICP)
        self.assertTrue(r.reasoning_summary)
        self.assertIn("Qualification", r.reasoning_summary)


class FitMatchingTests(unittest.TestCase):
    """The ICP fit matcher: token/stem tolerant, and not diluted by a broad ICP,
    while still scoring an off-ICP company at zero (see agents/qualification.py)."""

    def test_stemming_matches_morphological_variants(self):
        # ICP says "monitoring"/"APIs"; the prose says "monitor"/"api" -> still matched.
        r = q.qualify(
            research=research(score=75,
                              what_they_do="we monitor production APIs for backend teams",
                              industries_served="", target_customer="", recent_focus="",
                              metrics_or_traction="", notable_customers=""),
            icp={"keywords": ["monitoring", "APIs"]})
        self.assertEqual(r.signals["icp_match_ratio"], 1.0)
        self.assertTrue(any("ICP match" in s for s in r.strongest_signals))

    def test_broad_icp_is_not_diluted(self):
        # A 6-term ICP the company clearly hits on 3+ concepts must not read as a weak
        # 3/6 fit — the saturation cap keeps it a genuine match worth pursuing.
        r = q.qualify(
            research=research(score=78,
                              what_they_do="open-source observability with tracing and monitoring",
                              industries_served="developer tools", target_customer="engineers",
                              recent_focus="", metrics_or_traction="", notable_customers=""),
            icp={"keywords": ["observability", "monitoring", "tracing",
                              "developer", "tools", "kubernetes"]})
        self.assertEqual(r.signals["icp_match_ratio"], 1.0)   # >=3 concepts -> saturated
        self.assertIn(r.recommendation, (q.CONTINUE, q.HIGH_PRIORITY))

    def test_unrelated_company_still_zero_fit(self):
        # The generalisation must NOT turn an off-ICP company into a match.
        r = q.qualify(
            research=research(score=75, what_they_do="a neighbourhood bakery",
                              target_customer="local shoppers", industries_served="food retail",
                              recent_focus="", metrics_or_traction="", notable_customers=""),
            icp={"keywords": ["observability", "monitoring", "kubernetes"]})
        self.assertEqual(r.signals["icp_match_ratio"], 0.0)
        self.assertEqual(r.recommendation, q.REJECT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
