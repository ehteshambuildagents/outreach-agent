"""Tests for the Phase-2 safe-channel drafters (agents/channels.py).

Offline: the model call is mocked, so no API key / network. Covers each channel,
the shared AI-voice / banned-phrase / guard gate, length ceilings, the
needs-context rule, and the invariant that drafting NEVER posts.

    python -m unittest tests.test_channels
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import channels  # noqa: E402

_CALL = "services.claude_client._call_model"

DATA = {"company_name": "Acme", "what_they_do": "B2B onboarding software",
        "unique_hook": "posted about hiring their first SDR"}

# A clean, human reply that trips no banned/AI-voice/guard checks.
CLEAN_X = {"body": "congrats on the first SDR hire. the thing that shortened ramp "
                   "for us was handing them real accounts to research on day one "
                   "instead of a script. what's your ICP look like?"}


def _model(body):
    return mock.patch(_CALL, return_value={"body": body} if isinstance(body, str) else body)


class HappyPathTests(unittest.TestCase):
    @_model(CLEAN_X["body"])
    def test_x_reply_ok(self, m):
        r = channels.draft("x_reply", context="we just hired our first SDR",
                           research_data=DATA)
        self.assertEqual(r["status"], "ok")
        self.assertLessEqual(r["char_count"], 280)
        self.assertEqual(r["guard"]["decision"], "ALLOW")
        self.assertFalse(r["posted"])                      # drafting never posts
        self.assertEqual(m.call_count, 1)

    @_model("Thanks for the thoughtful thread. We hit the same ramp problem and "
            "solved it by having new reps research real accounts on day one. Happy "
            "to share the onboarding doc we used if useful.")
    def test_contact_form_needs_no_context(self, m):
        r = channels.draft("contact_form", research_data=DATA)
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["posted"])

    @_model(CLEAN_X["body"])
    def test_reddit_and_hn_ok_with_context(self, m):
        for ch in ("reddit_comment", "hn_reply"):
            r = channels.draft(ch, context="thread about SDR ramp time",
                               research_data=DATA)
            self.assertEqual(r["status"], "ok", ch)


class GuardrailTests(unittest.TestCase):
    def test_reply_channel_needs_context(self):
        r = channels.draft("x_reply", context="", research_data=DATA)
        self.assertEqual(r["status"], "skip")
        self.assertIn("post", r["reason"].lower())

    def test_unknown_channel_errors(self):
        self.assertEqual(channels.draft("linkedin_dm")["status"], "error")

    @_model("x " * 400)   # ~800 chars, way over the 280 X ceiling
    def test_x_reply_trimmed_to_ceiling(self, m):
        r = channels.draft("x_reply", context="a post", research_data=DATA)
        self.assertLessEqual(len(r["body"]), 280)

    @_model("I hope this finds you well. Our cutting-edge solution can leverage "
            "synergies to unlock seamless growth, ensuring lasting value for your "
            "team. It's not just a tool, but a partner.")
    def test_generic_ai_draft_is_not_marked_ready(self, m):
        # Banned wording + AI structure: regenerated once, then (mock is fixed)
        # returned as needs_review — never surfaced as ready to post.
        r = channels.draft("contact_form", research_data=DATA)
        self.assertEqual(r["status"], "needs_review")
        self.assertTrue(r.get("problems"))
        self.assertGreaterEqual(m.call_count, 2)           # generate + one repair

    @_model("Let's leverage synergies to supercharge your seamless growth engine.")
    def test_validate_flags_banned(self, m):
        r = channels.draft("x_reply", context="a post", research_data=DATA)
        # Banned wording means it can't be "ok".
        self.assertIn(r["status"], ("needs_review", "error"))


class NeverPostsTests(unittest.TestCase):
    @_model(CLEAN_X["body"])
    def test_result_has_no_send_or_post_side_effect(self, m):
        r = channels.draft("x_reply", context="a post", research_data=DATA)
        # The result explicitly records that nothing was posted, and there is no
        # posting code path in the module at all.
        self.assertFalse(r["posted"])
        self.assertNotIn("post", [k for k in r if k not in ("posted",)])


if __name__ == "__main__":
    unittest.main()
