"""Tests for writing-style memory (chat/style.py) — fully deterministic, offline.

These pin the "learn once, never repeat" behaviour: standing-style cues activate
(explicit ones immediately, tone/length nudges only when repeated), "never say X"
captures a phrase to avoid, and the profile renders back into a prompt note.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import style  # noqa: E402


class StyleLearningTests(unittest.TestCase):
    def test_explicit_prefs_activate_immediately(self):
        p = style.default_profile()
        p = style.learn_from_guidance(p, "make the subject lowercase and no emojis")
        note = style.profile_note(p)
        self.assertIn("lowercase", note)
        self.assertIn("emojis", note)

    def test_tone_nudge_needs_repetition(self):
        p = style.default_profile()
        p = style.learn_from_guidance(p, "make it shorter")
        self.assertNotIn("short and punchy", style.profile_note(p))  # once = not standing
        p = style.learn_from_guidance(p, "shorter please")
        self.assertIn("short and punchy", style.profile_note(p))     # twice = standing

    def test_never_say_captures_phrase(self):
        p = style.default_profile()
        p = style.learn_from_guidance(p, "never say \"I hope you're well\"")
        self.assertIn("i hope you're well", [a.lower() for a in p["avoid"]])
        self.assertIn("never use these phrases", style.profile_note(p))

    def test_avoid_ignores_overlong_sentence(self):
        p = style.default_profile()
        p = style.learn_from_guidance(
            p, "don't say anything that sounds like a marketing brochure written "
               "by committee over several weeks")
        # too long to be a crisp phrase -> not stored as an avoid phrase
        self.assertEqual(p["avoid"], [])

    def test_empty_profile_note_is_blank(self):
        self.assertEqual(style.profile_note(style.default_profile()), "")

    def test_learning_is_idempotent_for_avoid(self):
        p = style.default_profile()
        p = style.learn_from_guidance(p, "stop saying quick reason")
        p = style.learn_from_guidance(p, "stop saying quick reason")
        self.assertEqual(len([a for a in p["avoid"] if "quick reason" in a.lower()]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
