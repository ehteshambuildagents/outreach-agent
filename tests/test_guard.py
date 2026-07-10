"""Tests for the Deliverability & Cost Guard Agent (guard/).

Deterministic and offline. Covers the cost guard (budget ladder + hard blocks),
the deliverability guard (spam/AI-tells/formatting/personalization/sequence/
mailbox/auth + send-state blocks), the combined decision engine, the exact output
contract, and determinism. The guard never writes, researches, or sends.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guard import assess  # noqa: E402
from guard import cost, deliverability  # noqa: E402
from guard.models import ALLOW, BLOCK, WARN  # noqa: E402

_GOOD_BODY = ("Saw Linear shipped Linear for Agents last week — the way you kept the "
              "keyboard-first flow while adding agent handoffs is sharp. We help "
              "eng-heavy teams cut triage time; worth a quick look?")


def _clean_email():
    return {"email": {"subject": "Linear for Agents", "body": _GOOD_BODY,
                      "to": "karri@linear.app", "company": "Linear"}}


# ── Cost guard ─────────────────────────────────────────────────────────
class CostGuardTests(unittest.TestCase):
    def test_daily_budget_exceeded_blocks(self):
        f = cost.evaluate({"usage": {"daily_spend": 12, "daily_budget": 10}})
        self.assertTrue(f.block)

    def test_monthly_budget_exceeded_blocks(self):
        f = cost.evaluate({"usage": {"monthly_spend": 210, "monthly_budget": 200}})
        self.assertTrue(f.block)

    def test_budget_warn_ladder(self):
        # 50 / 80 / 95 accumulate risk without blocking
        low = cost.evaluate({"usage": {"daily_spend": 6, "daily_budget": 10}}).score
        mid = cost.evaluate({"usage": {"daily_spend": 8.5, "daily_budget": 10}}).score
        high = cost.evaluate({"usage": {"daily_spend": 9.7, "daily_budget": 10}}).score
        self.assertLess(low, mid)
        self.assertLess(mid, high)

    def test_estimated_cost_exceeds_remaining_blocks(self):
        f = cost.evaluate({"usage": {"daily_spend": 8, "daily_budget": 10},
                           "campaign": {"est_cost": 5}})
        self.assertTrue(f.block)      # $5 > $2 remaining

    def test_retry_loop_blocks(self):
        self.assertTrue(cost.evaluate({"execution": {"retry_loop": True}}).block)
        self.assertTrue(cost.evaluate({"execution": {"retries": 40}}).block)

    def test_duplicate_execution_blocks(self):
        self.assertTrue(cost.evaluate({"execution": {"duplicate_calls": 3}}).block)

    def test_duplicate_worker_blocks(self):
        self.assertTrue(cost.evaluate({"execution": {"duplicate_workers": 2}}).block)

    def test_unhealthy_queue_blocks(self):
        self.assertTrue(cost.evaluate({"execution": {"queue_healthy": False}}).block)

    def test_excessive_calls_warns(self):
        f = cost.evaluate({"campaign": {"ai_calls": 100, "recipients": 5}})
        self.assertGreater(f.score, 0)
        self.assertFalse(f.block)

    def test_cost_estimate_from_tokens_and_model(self):
        est = cost.estimate_cost({"tokens": 1_000_000, "model": "claude-opus"})
        self.assertAlmostEqual(est, 30.0, places=1)

    def test_clean_run_is_low_cost_risk(self):
        f = cost.evaluate({"usage": {"daily_spend": 1, "daily_budget": 10}})
        self.assertFalse(f.block)
        self.assertLess(f.score, 25)


# ── Deliverability guard ───────────────────────────────────────────────
class DeliverabilityGuardTests(unittest.TestCase):
    def test_clean_email_is_low_risk(self):
        f = deliverability.evaluate(_clean_email())
        self.assertFalse(f.block)
        self.assertLess(f.score, 25)

    def test_spam_cannon_copy_is_high_risk(self):
        f = deliverability.evaluate({"email": {
            "subject": "ACT NOW!!! GUARANTEED FREE OFFER",
            "body": "CLICK HERE to buy now! Risk free guarantee! Act now — limited time!!!"}})
        self.assertGreaterEqual(f.score, 50)

    def test_replied_prospect_blocks(self):
        f = deliverability.evaluate({**_clean_email(), "prospect": {"replied": True}})
        self.assertTrue(f.block)

    def test_unsubscribed_prospect_blocks(self):
        f = deliverability.evaluate({**_clean_email(), "prospect": {"unsubscribed": True}})
        self.assertTrue(f.block)

    def test_bounced_prospect_blocks(self):
        f = deliverability.evaluate({**_clean_email(), "prospect": {"bounced": True}})
        self.assertTrue(f.block)

    def test_duplicate_recipients_block(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "recipients": ["a@x.com", "A@x.com", "b@x.com"]})
        self.assertTrue(f.block)

    def test_fabricated_personalization_blocks(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "personalization": {"fabricated": True}})
        self.assertTrue(f.block)

    def test_generic_personalization_raises_risk(self):
        base = deliverability.evaluate(_clean_email()).score
        gen = deliverability.evaluate({**_clean_email(),
                                       "personalization": {"generic": True,
                                                           "based_on_research": False}}).score
        self.assertGreater(gen, base)
        self.assertTrue(deliverability.evaluate({**_clean_email(),
            "personalization": {"generic": True}}).block)

    def test_empty_body_blocks(self):
        f = deliverability.evaluate({"email": {"subject": "hi", "body": "",
            "to": "jane@acme.com", "company": "Acme"}})
        self.assertTrue(f.block)

    def test_empty_subject_blocks(self):
        f = deliverability.evaluate({"email": {"subject": "", "body": _GOOD_BODY,
            "to": "jane@acme.com", "company": "Acme"}})
        self.assertTrue(f.block)

    def test_missing_recipient_or_company_blocks(self):
        self.assertTrue(deliverability.evaluate(
            {"email": {"subject": "hi", "body": _GOOD_BODY, "company": "Acme"}}).block)
        self.assertTrue(deliverability.evaluate(
            {"email": {"subject": "hi", "body": _GOOD_BODY, "to": "jane@acme.com"}}).block)

    def test_writer_error_blocks(self):
        f = deliverability.evaluate({"writer": {"status": "error"},
                                     "email": {"subject": None, "body": None,
                                               "to": "jane@acme.com", "company": "Acme"}})
        self.assertTrue(f.block)

    def test_rejected_lead_blocks(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "qualification": {"recommendation": "reject"}})
        self.assertTrue(f.block)

    def test_strategy_hold_blocks(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "strategy": {"recommended_action": "hold"}})
        self.assertTrue(f.block)

    def test_weak_generic_email_blocks(self):
        body = "I help founders get replies from cold email without wasting time."
        f = deliverability.evaluate({"email": {"subject": "quick idea", "body": body,
            "to": "jane@acme.com", "company": "Acme"},
            "personalization": {"generic": True, "based_on_research": False}})
        self.assertTrue(f.block)

    def test_high_risk_phrases_flagged(self):
        f = deliverability.evaluate({"email": {"subject": "Quick question",
            "body": "Just checking in and circling back. Hope you're well — touching base again!"}})
        self.assertTrue(any("clich" in i.lower() for i in f.issues))
        self.assertGreater(f.score, 0)

    def test_attachments_and_links_penalized(self):
        f = deliverability.evaluate({"email": {"subject": "hi", "body": _GOOD_BODY,
            "to": "karri@linear.app", "company": "Linear",
            "links": 5, "attachments": 1}})
        self.assertTrue(any("link" in i.lower() for i in f.issues))
        self.assertTrue(any("attachment" in i.lower() for i in f.issues))

    def test_zero_sequence_spacing_blocks(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "sequence": {"spacing_days": [0, 3]}})
        self.assertTrue(f.block)

    def test_repetition_vs_prior_flagged(self):
        f = deliverability.evaluate({"email": {"subject": "hi", "body": _GOOD_BODY,
                                     "to": "karri@linear.app", "company": "Linear"},
                                     "sequence": {"prior_bodies": [_GOOD_BODY]}})
        self.assertTrue(any("previous email" in i.lower() for i in f.issues))

    def test_high_bounce_rate_blocks(self):
        self.assertTrue(deliverability.evaluate(
            {**_clean_email(), "mailbox": {"bounce_rate": 0.1}}).block)

    def test_high_spam_complaint_blocks(self):
        self.assertTrue(deliverability.evaluate(
            {**_clean_email(), "mailbox": {"spam_rate": 0.005}}).block)

    def test_new_mailbox_warns(self):
        f = deliverability.evaluate({**_clean_email(), "mailbox": {"age_days": 5}})
        self.assertTrue(any("new mailbox" in i.lower() for i in f.issues))

    def test_missing_auth_flagged(self):
        f = deliverability.evaluate({**_clean_email(),
                                     "auth": {"spf": True, "dkim": False, "dmarc": False}})
        self.assertTrue(any("authentication" in i.lower() for i in f.issues))

    def test_reading_level_dense_sentences(self):
        dense = ("I am reaching out because our platform provides an end to end "
                 "comprehensive solution that will fundamentally transform the way your "
                 "entire organization approaches its most critical strategic initiatives "
                 "across every single department and function without any exception at all.")
        f = deliverability.evaluate({"email": {"subject": "hi", "body": dense,
            "to": "jane@acme.com", "company": "Acme"}})
        self.assertTrue(any("sentence" in i.lower() for i in f.issues))


# ── Combined decision engine + contract ────────────────────────────────
class DecisionEngineTests(unittest.TestCase):
    def test_output_contract_shape(self):
        r = assess(_clean_email())
        self.assertIn(r["decision"], (ALLOW, WARN, BLOCK))
        self.assertIsInstance(r["overallRisk"], int)
        for section in ("cost", "deliverability"):
            for key in ("risk", "issues", "recommendations"):
                self.assertIn(key, r[section])

    def test_clean_email_allows(self):
        self.assertEqual(assess(_clean_email())["decision"], ALLOW)

    def test_hard_block_blocks_whole_run(self):
        r = assess({**_clean_email(), "prospect": {"replied": True}})
        self.assertEqual(r["decision"], BLOCK)
        self.assertGreaterEqual(r["overallRisk"], 80)

    def test_budget_block_blocks_whole_run(self):
        r = assess({**_clean_email(), "usage": {"daily_spend": 20, "daily_budget": 10}})
        self.assertEqual(r["decision"], BLOCK)

    def test_critical_copy_blocks_even_without_flag(self):
        r = assess({"email": {"subject": "ACT NOW!!! GUARANTEED FREE OFFER RISK FREE",
            "body": "CLICK HERE buy now!!! Risk free guarantee! Act now limited time free "
                    "offer!!! 100% free special promotion — click here now!!!"}})
        self.assertEqual(r["decision"], BLOCK)

    def test_moderate_issues_warn(self):
        r = assess({"email": {"subject": "Quick question",
            "body": "Just checking in and circling back — hope you're well, touching base."}})
        self.assertIn(r["decision"], (WARN, BLOCK))

    def test_uncertain_missing_data_is_safe_allow_or_warn(self):
        # empty input: nothing to flag -> ALLOW (there's nothing dangerous to send)
        self.assertEqual(assess({})["decision"], ALLOW)

    def test_deterministic(self):
        self.assertEqual(assess(_clean_email()), assess(_clean_email()))

    def test_never_mutates_input(self):
        inp = _clean_email()
        import copy
        before = copy.deepcopy(inp)
        assess(inp)
        self.assertEqual(inp, before)      # read-only; never rewrites anything


if __name__ == "__main__":
    unittest.main(verbosity=2)
