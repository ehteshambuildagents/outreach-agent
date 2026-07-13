"""Tests for the structural AI-voice detector (agents/ai_voice.py).

Fully offline and deterministic — no model, no network. Covers each structural
tell, the shape/rhythm split, banned-phrase reuse, and the proxy score ordering
that the eval harness and the guard depend on.

    python -m unittest tests.test_ai_voice
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import ai_voice  # noqa: E402

# A grounded, human founder email — the detector must leave it alone.
HUMAN = ("Saw Plausible crossed $1M ARR fully bootstrapped. That's rare in "
         "analytics. I'm building a thing that takes the manual research off "
         "outbound. Want the version I'd write for you?")
# Swappable AI/marketing copy — every structural tell at once.
ROBOT = ("Our cutting-edge platform delivers speed, scale, and precision, "
         "ensuring seamless growth. It's not just a tool, but a partner. We help "
         "teams unlock value and drive success.")


class SentenceSplitTests(unittest.TestCase):
    def test_splits_on_terminators(self):
        self.assertEqual(len(ai_voice.split_sentences("One. Two! Three?")), 3)

    def test_keeps_decimals_and_abbreviations_intact(self):
        self.assertEqual(len(ai_voice.split_sentences("We hit 99.9% uptime, e.g. last week.")), 1)

    def test_empty(self):
        self.assertEqual(ai_voice.split_sentences("   "), [])


class TricolonTests(unittest.TestCase):
    def test_rule_of_three_flagged(self):
        self.assertTrue(any("rule-of-three" in t for t in ai_voice.tells(
            "We deliver speed, scale, and precision here.")))

    def test_proper_noun_list_not_flagged(self):
        # A real list of capitalised products is not the abstract-noun tricolon.
        self.assertFalse(any("rule-of-three" in t for t in ai_voice.tells(
            "You connect Gmail, Outlook, and Slack in one click.")))


class AntithesisTests(unittest.TestCase):
    def test_not_x_but_y_flagged(self):
        self.assertTrue(any("antithesis" in t for t in ai_voice.tells(
            "It's not just a tool, but a partner.")))

    def test_not_x_comma_its_y_flagged(self):
        # Self-audit: the comma form with no "but" ("it's not just X, it's Y").
        self.assertTrue(any("antithesis" in t for t in ai_voice.tells(
            "It's not just faster, it's cheaper.")))

    def test_plain_sentence_not_flagged(self):
        self.assertFalse(any("antithesis" in t for t in ai_voice.tells(
            "It's a tool we built for outbound.")))


class ContrastPivotTests(unittest.TestCase):
    """Pattern 1: setup -> contrast-pivot (name a pain, pivot to the pitch)."""
    def test_other_side_of_that_flagged(self):
        self.assertTrue(any("pivot" in t for t in ai_voice.tells(
            "Outreach gets ignored if it's a template. I'm working on the other "
            "side of that, a few emails that fit.")))

    def test_opposite_of_that_flagged(self):
        self.assertTrue(any("pivot" in t for t in ai_voice.tells(
            "Most notes are a blast. I built the opposite of that.")))

    def test_legit_contrast_not_flagged(self):
        # A real contrast that isn't a pain->pitch pivot must not false-positive.
        self.assertFalse(any("pivot" in t for t in ai_voice.tells(
            "We moved off Mailchimp and used Postgres instead of that.")))


class BinaryCloserTests(unittest.TestCase):
    """Pattern 2: forced either/or closer capped by a choice-restating tag."""
    def test_binary_then_tag_flagged(self):
        self.assertTrue(any("binary" in t for t in ai_voice.tells(
            "Is that real for a team like yours, or is your outbound already "
            "personal enough. Which is it?")))

    def test_single_either_or_question_not_flagged(self):
        # One honest either/or question (no short choice-restating tag) is fine.
        self.assertFalse(any("binary" in t for t in ai_voice.tells(
            "Are you the right person for outbound, or should I ask someone else?")))

    def test_choice_tag_without_alternative_not_flagged(self):
        self.assertFalse(any("binary" in t for t in ai_voice.tells(
            "We help eng teams move faster. Which works for you?")))


class FormattingCrutchTests(unittest.TestCase):
    """Self-audit: em-dash and semicolon overuse (threshold >=2, since one of
    either is ordinary human writing)."""
    def test_two_em_dashes_flagged(self):
        self.assertTrue(any("em-dash" in t for t in ai_voice.tells(
            "Saw your launch — big move. We build outbound — the human kind.")))

    def test_one_em_dash_not_flagged(self):
        self.assertFalse(any("em-dash" in t for t in ai_voice.tells(
            "Saw Linear ship last week — sharp. Worth a look?")))

    def test_two_semicolons_flagged(self):
        self.assertTrue(any("semicolon" in t for t in ai_voice.tells(
            "We help teams; we save time; we get replies.")))

    def test_one_semicolon_not_flagged(self):
        self.assertFalse(any("semicolon" in t for t in ai_voice.tells(
            "We cut triage time; worth a quick look?")))


class ZeroGptRegressionTests(unittest.TestCase):
    """The two exact sentences an external detector (ZeroGPT) flagged must now be
    caught internally too — this is the round-2 acceptance anchor."""
    P1 = ("Writing because outbound to marketing folks tends to get ignored fast "
          "if it reads like a template. I'm working on the other side of that, "
          "moving from spray-and-pray to just a handful of emails.")
    P2 = ("Curious if that's even a real problem for a team like yours, or if your "
          "outbound already feels personal enough. Which is it?")

    def test_pattern1_flags(self):
        self.assertTrue(any("pivot" in t for t in ai_voice.tells(self.P1)))

    def test_pattern2_flags_structurally_and_as_banned(self):
        from agents import writer_validator as wv
        self.assertTrue(any("binary" in t for t in ai_voice.tells(self.P2)))
        self.assertIn("Which is it?", wv.find_banned(self.P2, []))


class ParticipialTailTests(unittest.TestCase):
    def test_ensuring_tail_flagged(self):
        self.assertTrue(any("participial" in t for t in ai_voice.tells(
            "We automate the grind, ensuring you save time.")))

    def test_helping_tail_flagged(self):
        self.assertTrue(any("participial" in t for t in ai_voice.tells(
            "It runs the research, helping teams move faster.")))


class RhythmTests(unittest.TestCase):
    def test_metronome_rhythm_flagged(self):
        # Four sentences of ~identical length = robotic rhythm.
        body = ("We build outbound tools today. We help teams reach buyers. We "
                "make the copy sound human. We save your team time.")
        self.assertTrue(any("uniform sentence length" in t for t in ai_voice.tells(body)))

    def test_varied_rhythm_not_flagged(self):
        body = ("Saw your launch. The multi-model routing that actually ships is "
                "the genuinely hard part most teams only ever promise. Sharp. "
                "Worth a look?")
        self.assertFalse(any("uniform sentence length" in t for t in ai_voice.tells(body)))


class AnaphoraTests(unittest.TestCase):
    def test_repeated_opener_flagged(self):
        body = "I build tools. I write copy. I save you time on outbound work here."
        self.assertTrue(any("repeated sentence opener" in t for t in ai_voice.tells(body)))


class ShapeVsRhythmSplitTests(unittest.TestCase):
    def test_shape_tells_exclude_rhythm(self):
        # A metronome-rhythm email has a rhythm tell but no SHAPE tell.
        body = ("We build outbound tools today. We help teams reach buyers. We "
                "make the copy sound human. We save your team time.")
        self.assertTrue(ai_voice.tells(body))                 # full scan sees it
        self.assertFalse(ai_voice.shape_tells(body))          # shape-only does not

    def test_shape_tells_include_tricolon(self):
        self.assertTrue(ai_voice.shape_tells("speed, scale, and precision matter."))


class BannedReuseTests(unittest.TestCase):
    def test_banned_hits_uses_shared_list(self):
        hits = ai_voice.banned_hits("I hope this email finds you well and I wanted to reach out.")
        self.assertTrue(hits)

    def test_new_buzzwords_detected(self):
        for word in ("supercharge your growth", "a seamless experience",
                     "unlock value", "our cutting-edge platform"):
            self.assertTrue(ai_voice.banned_hits(word), word)


class AiScoreTests(unittest.TestCase):
    def test_human_scores_low_robot_scores_high(self):
        self.assertLess(ai_voice.ai_score(HUMAN), 15)
        self.assertGreater(ai_voice.ai_score(ROBOT), 45)
        self.assertLess(ai_voice.ai_score(HUMAN), ai_voice.ai_score(ROBOT))

    def test_empty_is_zero(self):
        self.assertEqual(ai_voice.ai_score(""), 0)
        self.assertEqual(ai_voice.ai_score(None), 0)

    def test_score_capped_0_100(self):
        self.assertLessEqual(ai_voice.ai_score(ROBOT * 4), 100)
        self.assertGreaterEqual(ai_voice.ai_score(HUMAN), 0)


class DeterminismTests(unittest.TestCase):
    def test_scan_is_stable(self):
        self.assertEqual(ai_voice.scan(ROBOT), ai_voice.scan(ROBOT))

    def test_penalty_matches_scan_weights(self):
        self.assertEqual(ai_voice.penalty(ROBOT),
                         min(100, sum(w for _, w in ai_voice.scan(ROBOT))))


if __name__ == "__main__":
    unittest.main()
