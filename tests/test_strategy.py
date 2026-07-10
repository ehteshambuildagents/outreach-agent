"""Tests for the Outreach Strategy Agent (agents/strategy.py).

Deterministic decision logic, exercised across every branch: gather research,
hold (too thin / no hook), enrich (low confidence), draft, and sequence — plus
confidence maths, persona/channel/sequence selection, missing-info, the
structured shape, workspace convenience, and determinism.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import strategy  # noqa: E402


def research(score=86, hooks=("Elite teams like Ramp and Vercel use it",),
             status="ok", **data_over):
    data = {"company_name": "Linear", "what_they_do": "issue tracking for eng teams",
            "target_customer": "product & engineering teams",
            "primary_contact_name": "Karri Saarinen", "primary_contact_role": "CEO",
            "recent_focus": "Linear for Agents", "metrics_or_traction": "used by Ramp, Vercel",
            "unique_hook": "the tool of choice for elite teams"}
    data.update(data_over)
    return {"status": status, "research_score": score,
            "hooks": [{"text": h} for h in hooks], "data": data}


def intel(hooks=("Just raised a Series B", "Shipped an AI agent")):
    return {"status": "ok", "company": "Linear",
            "hooks": [{"text": h} for h in hooks], "summary": "issue tracking"}


class DecisionBranchTests(unittest.TestCase):
    def test_no_context_recommends_research(self):
        d = strategy.decide()
        self.assertEqual(d.recommended_action, strategy.RESEARCH)
        self.assertEqual(d.confidence, 0)
        self.assertIsNone(d.primary_hook)
        self.assertIn("company research", d.missing_information)

    def test_thin_research_holds(self):
        d = strategy.decide(research={"status": "skip", "research_score": 15,
                                      "data": {}, "reason": "too little"})
        self.assertEqual(d.recommended_action, strategy.HOLD)
        self.assertLessEqual(d.confidence, strategy._HOOKLESS_CAP)

    def test_no_hook_holds_never_generic(self):
        d = strategy.decide(research=research(score=70, hooks=(), unique_hook=""))
        self.assertEqual(d.recommended_action, strategy.HOLD)
        self.assertIsNone(d.primary_hook)
        self.assertLessEqual(d.confidence, strategy._HOOKLESS_CAP)
        self.assertIn("a specific, verifiable hook", d.missing_information)

    def test_low_confidence_enriches(self):
        d = strategy.decide(research=research(score=30))
        self.assertEqual(d.recommended_action, strategy.ENRICH)
        self.assertLess(d.confidence, strategy.CONF_ENRICH_MAX)
        self.assertTrue(d.primary_hook)

    def test_good_confidence_drafts_single(self):
        d = strategy.decide(research=research(score=60))
        self.assertEqual(d.recommended_action, strategy.DRAFT)
        self.assertEqual(d.recommended_sequence, {"type": "single", "steps": 1})
        self.assertEqual(d.recommended_channel, "email")
        self.assertEqual(d.primary_hook, "Elite teams like Ramp and Vercel use it")

    def test_strong_fit_recommends_sequence(self):
        d = strategy.decide(research=research(
            score=88, hooks=("hook one", "hook two", "hook three")))
        self.assertEqual(d.recommended_action, strategy.SEQUENCE)
        self.assertGreaterEqual(d.recommended_sequence["steps"], 3)
        self.assertGreaterEqual(d.confidence, strategy.CONF_HIGH)

    def test_existing_draft_recommends_followup_sequence(self):
        d = strategy.decide(research=research(score=60),
                            email={"status": "ok", "subject": "s", "body": "b"})
        self.assertEqual(d.recommended_action, strategy.SEQUENCE)
        self.assertEqual(d.recommended_sequence, {"type": "short", "steps": 3})

    def test_intel_only_is_usable_thin(self):
        d = strategy.decide(intel=intel())
        self.assertIn(d.recommended_action, (strategy.DRAFT, strategy.ENRICH))
        self.assertEqual(d.signals["source"], "intel")
        self.assertTrue(d.primary_hook)

    def test_intel_without_hooks_holds(self):
        d = strategy.decide(intel={"status": "ok", "company": "X", "hooks": []})
        self.assertEqual(d.recommended_action, strategy.HOLD)

    def test_rejected_qualification_holds(self):
        d = strategy.decide(
            research=research(score=85),
            qualification={"recommendation": "reject", "confidence": 90},
        )
        self.assertEqual(d.recommended_action, strategy.HOLD)
        self.assertEqual(d.recommended_sequence, {"type": "none", "steps": 0})

    def test_research_more_qualification_enriches_not_send_ready(self):
        d = strategy.decide(
            research=research(score=85),
            qualification={"recommendation": "research_more", "confidence": 30},
        )
        self.assertEqual(d.recommended_action, strategy.ENRICH)
        self.assertEqual(d.recommended_sequence, {"type": "none", "steps": 0})

    def test_high_priority_qualification_continues(self):
        d = strategy.decide(
            research=research(score=88, hooks=("hook one", "hook two", "hook three")),
            qualification={"recommendation": "high_priority", "confidence": 90},
        )
        self.assertEqual(d.recommended_action, strategy.SEQUENCE)

    def test_continue_qualification_continues(self):
        d = strategy.decide(
            research=research(score=60),
            qualification={"recommendation": "continue", "confidence": 70},
        )
        self.assertEqual(d.recommended_action, strategy.DRAFT)

    def test_explicit_override_can_continue_rejected_lead(self):
        d = strategy.decide(
            research=research(score=60),
            qualification={"recommendation": "reject", "confidence": 90},
            override=True,
        )
        self.assertEqual(d.recommended_action, strategy.DRAFT)


class SignalTests(unittest.TestCase):
    def test_confidence_bonuses_and_hookless_cap(self):
        full = strategy.decide(research=research(score=60)).confidence
        self.assertEqual(full, min(100, 60 + 5 + 5 + 3))          # hook+contact+target
        capped = strategy.decide(research=research(score=90, hooks=(), unique_hook="")).confidence
        self.assertLessEqual(capped, strategy._HOOKLESS_CAP)

    def test_enterprise_persona_and_sequence(self):
        d = strategy.decide(research=research(
            score=80, hooks=("only one",), business_model="enterprise SaaS",
            competitive_positioning="the enterprise alternative"))
        self.assertEqual(d.recommended_persona, "enterprise")
        self.assertEqual(d.recommended_action, strategy.SEQUENCE)   # enterprise + high conf

    def test_missing_information_lists_gaps(self):
        d = strategy.decide(research=research(
            score=55, recent_focus="", metrics_or_traction=""))
        self.assertIn("a recent activity or trigger", d.missing_information)
        self.assertIn("traction / metrics", d.missing_information)

    def test_persona_defaults_to_founder(self):
        self.assertEqual(strategy.decide(research=research()).recommended_persona, "founder")


class ShapeTests(unittest.TestCase):
    def test_to_dict_has_stable_shape(self):
        d = strategy.decide(research=research()).to_dict()
        for key in ("recommended_action", "confidence", "primary_hook",
                    "recommended_persona", "recommended_channel",
                    "recommended_sequence", "reasoning_summary",
                    "missing_information", "signals"):
            self.assertIn(key, d)
        self.assertIn(d["recommended_action"], strategy.ACTIONS)

    def test_decide_from_workspace(self):
        ws = {"research": research(score=60)}
        self.assertEqual(strategy.decide_from_workspace(ws).recommended_action,
                         strategy.DRAFT)
        self.assertEqual(strategy.decide_from_workspace({}).recommended_action,
                         strategy.RESEARCH)

    def test_is_deterministic(self):
        r = research(score=73, hooks=("a", "b"))
        self.assertEqual(strategy.decide(research=r).to_dict(),
                         strategy.decide(research=r).to_dict())

    def test_reasoning_summary_is_present_but_internal(self):
        d = strategy.decide(research=research())
        self.assertTrue(d.reasoning_summary)          # exists for internal use
        self.assertIn("Confidence", d.reasoning_summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
