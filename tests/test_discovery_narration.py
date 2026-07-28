"""Reasoning-narration tests: the streamed "why" must be TRUE and ADAPTIVE.

The narration exists to make the agent read like an experienced SDR thinking
aloud. That is only worth anything if every line is grounded in what the run
actually did, so most of these tests assert what the narration must NEVER say:
no recruiters dropped means no sentence about recruiters, nothing verified means
no claim of verification, and a company is only ever NAMED when the classifier
was precise enough to justify naming it.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import aggregators, engine, intent, narration  # noqa: E402
from discovery.models import DiscoveryQuery, Prospect        # noqa: E402


def _text(lines) -> str:
    return " ".join(lines).lower()


class GroundingTests(unittest.TestCase):
    """The narration may only describe things that actually happened."""

    def test_no_staffing_dropped_means_no_claim_about_recruiters(self):
        lines = narration.after_apollo(total=1200, kept=20, staffing_dropped=0)
        self.assertTrue(lines)                       # it still reports the count
        self.assertNotIn("recruiter", _text(lines))
        self.assertNotIn("agenc", _text(lines))

    def test_unknown_total_is_not_invented(self):
        self.assertEqual(narration.after_apollo(total=None, kept=0), [])

    def test_nothing_verified_is_never_described_as_verified(self):
        lines = narration.after_verification(verified=0, checked=10, hiring_any=4,
                                             role_label="an SDR")
        self.assertNotIn("worth acting on", _text(lines))
        self.assertIn("none has a posting", _text(lines))

    def test_no_verification_run_says_nothing(self):
        self.assertEqual(narration.after_verification(verified=0, checked=0), [])

    def test_a_prospect_with_no_evidence_gets_no_case_made_for_it(self):
        bare = Prospect(company_name="Bare", website="https://bare.com", domain="bare.com")
        self.assertEqual(narration.top_pick(bare), [])
        self.assertEqual(narration.top_pick(None), [])

    def test_the_top_pick_case_only_cites_signals_it_actually_has(self):
        p = Prospect(company_name="HackerRank", website="https://h.com", domain="h.com",
                     hiring={"verified": True, "match": "role"},
                     score={"icp_match": 0.8, "founder_access": 0.2})
        said = _text(narration.top_pick(p))
        self.assertIn("hiring the exact role", said)
        self.assertIn("buys this", said)
        # It has NO growth figure and poor founder access, so neither may appear.
        self.assertNotIn("headcount", said)
        self.assertNotIn("founder", said)

    def test_a_public_company_is_never_called_founder_reachable(self):
        p = Prospect(company_name="Mega", website="https://m.com", domain="m.com",
                     is_public=True, hiring={"verified": True, "match": "role"},
                     score={"icp_match": 0.9, "founder_access": 0.9})
        self.assertNotIn("founder", _text(narration.top_pick(p)))


class ProviderDegradationTests(unittest.TestCase):
    """When a source is missing, say so. Production ran on web search alone for a
    whole deploy cycle and the stream gave no hint, because a provider with no key
    contributes nothing rather than failing."""

    def test_a_missing_apollo_is_stated_with_its_cost(self):
        said = _text(narration.provider_gap(apollo="unavailable"))
        self.assertIn("apollo is not configured", said)
        self.assertIn("continuing with web sources", said)
        self.assertIn("verification will be weaker", said)

    def test_a_failing_provider_is_described_as_failing_not_missing(self):
        self.assertIn("did not respond", _text(narration.provider_gap(apollo="failed")))

    def test_a_working_or_merely_empty_provider_says_nothing(self):
        self.assertEqual(narration.provider_gap(apollo="ok"), [])
        # "empty" is a real answer from a working provider, not an outage.
        self.assertEqual(narration.provider_gap(apollo="empty"), [])
        self.assertEqual(narration.provider_gap(), [])
        self.assertEqual(narration.provider_gap(web={"exa": "ok", "tavily": "ok"}), [])

    def test_web_providers_report_who_is_left(self):
        one = _text(narration.provider_gap(web={"tavily": "unavailable", "exa": "ok"}))
        self.assertIn("tavily is unavailable", one)
        self.assertIn("from exa alone", one)
        both = _text(narration.provider_gap(
            web={"tavily": "unavailable", "exa": "failed"}))
        self.assertIn("nothing to corroborate", both)

    def test_the_engine_never_claims_a_search_it_could_not_run(self):
        """The narration must not announce "Searching Apollo" when Apollo is not
        configured; it must announce the gap instead."""
        q = DiscoveryQuery(raw="companies hiring an sdr", keywords=["sdr"], limit=5)
        got = []
        with mock.patch.object(engine.sources, "providers_available",
                               lambda: {"apollo": False, "exa": True, "tavily": True}), \
             mock.patch.object(engine.sources, "search_apollo",
                               lambda *a, **k: (k.get("stats", {}).update(
                                   {"state": "unavailable"}) or [])), \
             mock.patch.object(engine.sources, "search_candidates",
                               lambda *a, **k: []), \
             mock.patch.object(engine.sources, "verify_hiring", lambda *a, **k: 0):
            engine._search_until_good(
                q, set(), 20, progress=lambda t, kind="step": got.append((kind, t)))
        said = _text([t for _, t in got])
        self.assertNotIn("searching apollo", said)
        self.assertIn("apollo is not configured", said)


class NamingPrecisionTests(unittest.TestCase):
    """A company is only NAMED when the reason given for dropping it is true.

    Live bug this pins: the staffing filter also carries the advertising and
    consulting codes, so a role search dropped Google and Deloitte. Narrating
    "ignoring Google, it's a recruiter" is false and instantly discrediting.
    """

    def test_recruiters_and_agencies_are_separated_by_code(self):
        self.assertEqual(intent.staffing_kind(["7361"]), "recruiter")
        self.assertEqual(intent.staffing_kind(["7363"]), "recruiter")
        self.assertEqual(intent.staffing_kind(["7311"]), "agency")   # advertising
        self.assertEqual(intent.staffing_kind(["8742"]), "agency")   # consulting
        self.assertEqual(intent.staffing_kind(["7372"]), "")         # software
        self.assertEqual(intent.staffing_kind(None, ["56131"]), "recruiter")
        # Both classes are still excluded from a hiring search.
        self.assertTrue(intent.is_staffing(["7311"]))
        self.assertTrue(intent.is_staffing(["7361"]))

    def test_agency_only_drops_are_never_named_as_recruiters(self):
        lines = narration.after_apollo(total=5000, kept=20, staffing_dropped=2,
                                       agency_dropped=2, recruiter_names=[])
        said = _text(lines)
        self.assertIn("agencies or consultancies", said)
        self.assertNotIn("recruiters, they post", said)

    def test_real_recruiters_are_named(self):
        said = _text(narration.after_apollo(
            total=5000, kept=10, staffing_dropped=2,
            recruiter_names=["Onward Search", "MCG Talent"]))
        self.assertIn("onward search", said)
        self.assertIn("post other companies' roles", said)


class AdaptiveToneTests(unittest.TestCase):
    """The wording follows the data. A fixed script regardless of what happened is
    exactly the canned narration this replaces."""

    def test_scale_wording_tracks_the_actual_share_dropped(self):
        most = _text(narration.after_apollo(total=100, kept=2, staffing_dropped=18,
                                            recruiter_names=["A"]))
        few = _text(narration.after_apollo(total=100, kept=50, staffing_dropped=1,
                                           recruiter_names=["A"]))
        self.assertIn("most of the top matches", most)
        self.assertIn("a few", few)
        self.assertNotIn("most of the top matches", few)

    def test_confidence_is_honest_about_a_thin_page(self):
        self.assertIn("none of these clears the bar",
                      _text(narration.confidence(strong=0, returned=6)))
        self.assertIn("only one", _text(narration.confidence(strong=1, returned=6)))
        self.assertIn("top 2", _text(narration.confidence(strong=2, returned=6)))
        # Everything strong needs no caveat at all.
        self.assertEqual(narration.confidence(strong=6, returned=6), [])
        self.assertEqual(narration.confidence(strong=0, returned=0), [])

    def test_roles_read_like_a_person_wrote_them(self):
        """"10,241 companies hiring sdr" reads like a bug, not a colleague."""
        self.assertEqual(narration.role_phrase("sdr"), "an SDR")
        self.assertEqual(narration.role_phrase("ai video creator"), "an AI video creator")
        self.assertEqual(narration.role_phrase("growth marketer"), "a growth marketer")
        self.assertEqual(narration.role_phrase(""), "")
        said = _text(narration.after_apollo(total=10241, kept=5, role_label="sdr"))
        self.assertIn("hiring an sdr.", said)        # article + acronym, not "hiring sdr"

    def test_a_plural_role_takes_no_article(self):
        """People ask for "account executives" as often as "an account executive",
        and the label is kept as typed, so a blanket article produced the live
        line "27 companies hiring an account executives"."""
        self.assertEqual(narration.role_phrase("account executives"),
                         "account executives")
        self.assertEqual(narration.role_phrase("ml engineers"), "ml engineers")
        # A word merely ending in "s" is not a plural.
        self.assertEqual(narration.role_phrase("business analyst"),
                         "a business analyst")
        said = _text(narration.after_apollo(total=27, kept=5,
                                            role_label="account executives"))
        self.assertIn("hiring account executives.", said)
        self.assertNotIn("an account executives", said)

    def test_a_category_search_never_claims_a_role(self):
        """Apollo matched on a category keyword, not a job posting. Saying
        "companies with a matching open role" invented a hiring signal that was
        never looked for."""
        said = _text(narration.after_apollo(total=101395, kept=20, role_label=""))
        self.assertIn("in that category", said)
        self.assertNotIn("open role", said)
        self.assertIn("too broad", said)      # and says so when it cannot narrow

    def test_organisations_that_cover_the_category_are_reported_as_demoted(self):
        said = _text(narration.after_apollo(
            total=900, kept=20, role_label="",
            covers_demoted={"publisher": 3, "association": 1}))
        self.assertIn("3 trade publications and 1 industry body", said)
        self.assertIn("rather than sell in it", said)
        # Nothing claimed when nothing was demoted.
        self.assertNotIn("trade publication", _text(
            narration.after_apollo(total=900, kept=20, covers_demoted={})))

    def test_counts_agree_with_their_verbs(self):
        one = _text(narration.after_apollo(total=99, kept=9, staffing_dropped=2,
                                           recruiter_names=["A"], agency_dropped=1))
        self.assertIn("one more is an agency", one)
        self.assertNotIn("another 1 are", one)

    def test_an_empty_run_explains_itself_from_what_was_seen(self):
        said = _text(narration.empty(considered=40, demoted=31))
        self.assertIn("40", said)
        self.assertIn("job boards", said)
        self.assertIn("nothing came back", _text(narration.empty(considered=0)))

    def test_web_narration_names_the_dominant_intermediary_kind(self):
        said = _text(narration.after_web(demoted={aggregators.JOB_BOARD: 9}, kept=2))
        self.assertIn("job boards", said)
        self.assertEqual(narration.after_web(demoted={}, rejected={}, kept=5), [])


class StyleTests(unittest.TestCase):
    """House style: no em dashes anywhere in Saqua copy, and keep it short."""

    def _all_lines(self):
        p = Prospect(company_name="Acme", website="https://a.com", domain="a.com",
                     hiring={"verified": True, "match": "role"},
                     growth={"headcount_6mo": 0.3},
                     score={"icp_match": 0.8, "founder_access": 0.8})
        return (narration.after_apollo(total=5000, kept=10, staffing_dropped=4,
                                       recruiter_names=["X"], agency_dropped=1,
                                       role_label="an SDR")
                + narration.after_web(demoted={aggregators.JOB_BOARD: 6}, kept=3)
                + narration.after_merge(companies=12, fallback=3, corroborated=4)
                + narration.after_verification(verified=2, checked=10, hiring_any=5,
                                               role_label="an SDR")
                + narration.top_pick(p)
                + narration.confidence(strong=2, returned=6)
                + narration.empty(considered=9, demoted=9))

    def test_no_em_dashes(self):
        for line in self._all_lines():
            self.assertNotIn("—", line)

    def test_lines_stay_short_enough_to_read_while_streaming(self):
        for line in self._all_lines():
            self.assertLessEqual(len(line), 160, line)


class EngineNarrationTests(unittest.TestCase):
    """The engine must stream reasoning through the real run, and must not break
    when the caller supplies an older one-argument progress sink."""

    def _run(self, sink):
        q = DiscoveryQuery(raw="companies hiring an sdr", keywords=["sdr"], limit=5)

        def _apollo(query, **kw):
            if kw.get("stats") is not None:
                kw["stats"].update({"total": 4100, "kept": 1, "staffing_dropped": 3,
                                    "recruiter_names": ["Onward Search"],
                                    "agency_dropped": 0})
            return [Prospect(company_name="Acme", website="https://acme.com",
                             domain="acme.com", confidence=0.5, tier="company",
                             industry_kind="software", apollo_id="1")]

        with mock.patch.object(engine.sources, "search_apollo", _apollo), \
             mock.patch.object(engine.sources, "search_candidates",
                               lambda *a, **k: []), \
             mock.patch.object(engine.sources, "verify_hiring", lambda *a, **k: 0):
            engine._search_until_good(q, set(), 20, progress=sink)

    def test_reasoning_is_streamed_as_thoughts_alongside_steps(self):
        got = []
        self._run(lambda text, kind="step": got.append((kind, text)))
        kinds = {k for k, _ in got}
        self.assertIn("step", kinds)
        self.assertIn("thought", kinds)
        thoughts = _text([t for k, t in got if k == "thought"])
        self.assertIn("4,100", thoughts)             # Apollo's REAL total
        self.assertIn("onward search", thoughts)     # the REAL dropped recruiter

    def test_a_legacy_one_argument_sink_still_works(self):
        got = []
        self._run(lambda text: got.append(text))     # no `kind` parameter
        self.assertTrue(got)

    def test_a_raising_sink_never_breaks_discovery(self):
        def _boom(text, kind="step"):
            raise RuntimeError("UI went away")
        self._run(_boom)                             # must not propagate


if __name__ == "__main__":
    unittest.main(verbosity=2)
