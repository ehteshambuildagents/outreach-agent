"""#9: the deterministic email-quality gate must SEPARATE a real founder email
from the ways cold email goes wrong (AI throat-clearing, templated boilerplate,
ungrounded personalization) and must catch a repetitive batch. Fully offline.

This is the "benchmark set + before/after" the fix calls for: before strengthening
the gate, the generic/templated/unsupported examples all scored 71-81 and passed
the weak threshold; they now score at or below it. The strong examples still pass.
Run ``python -m agents.writer_benchmark`` to print the table.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import writer_benchmark as bench       # noqa: E402
from agents import writer_review as reviewer       # noqa: E402


class GateDiscriminationTests(unittest.TestCase):
    def _score(self, subject, body, data):
        return reviewer.review({"subject": subject, "body": body}, data).score

    def test_strong_founder_emails_pass(self):
        for name, subject, body, data in bench.STRONG:
            r = reviewer.review({"subject": subject, "body": body}, data)
            self.assertFalse(r.weak, f"strong example {name} scored weak ({r.score})")

    def test_generic_ai_emails_are_flagged_weak(self):
        for name, subject, body, data in bench.GENERIC_AI:
            r = reviewer.review({"subject": subject, "body": body}, data)
            self.assertTrue(r.weak, f"generic AI example {name} passed ({r.score})")

    def test_templated_boilerplate_is_flagged_weak(self):
        for name, subject, body, data in bench.TEMPLATED:
            r = reviewer.review({"subject": subject, "body": body}, data)
            self.assertTrue(r.weak, f"templated example {name} passed ({r.score})")

    def test_ungrounded_personalization_is_flagged_weak(self):
        # "Impressive Series C / Berlin office" that appears nowhere in the research.
        for name, subject, body, data in bench.UNSUPPORTED:
            r = reviewer.review({"subject": subject, "body": body}, data)
            self.assertTrue(r.weak, f"unsupported example {name} passed ({r.score})")

    def test_gate_separates_strong_from_weak_by_a_clear_margin(self):
        strong = [self._score(s, b, d) for _, s, b, d in bench.STRONG]
        weak = [self._score(s, b, d)
                for group in (bench.GENERIC_AI, bench.TEMPLATED, bench.UNSUPPORTED)
                for _, s, b, d in group]
        self.assertGreaterEqual(min(strong) - max(weak), 20)


class BatchRepetitionTests(unittest.TestCase):
    def test_a_repetitive_batch_is_flagged(self):
        worst, pair = reviewer.batch_distinctiveness(bench.REPETITIVE_BATCH)
        self.assertIsNotNone(pair, f"repetitive batch not flagged (worst {worst:.2f})")

    def test_a_varied_batch_is_not_flagged(self):
        worst, pair = reviewer.batch_distinctiveness(bench.VARIED_BATCH)
        self.assertIsNone(pair, f"varied batch wrongly flagged (worst {worst:.2f})")

    def test_repetition_lowers_a_drafts_score_when_priors_are_given(self):
        # The same draft scores lower when it echoes a prior one than in isolation.
        _, subject, body, data = bench.STRONG[0]
        alone = reviewer.review({"subject": subject, "body": body}, data).score
        echo = reviewer.review({"subject": subject, "body": body}, data,
                               prior_bodies=[body]).score
        self.assertLess(echo, alone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
