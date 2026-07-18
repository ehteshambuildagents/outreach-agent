"""Source planner — offline unit tests.

All three providers (apollo / tavily / x_search) are mocked, so the suite never
hits a network or spends money. Focus: the DECISION discipline the design
promised — Apollo only on a contact gap and only behind the flag; news tried
before X; X only when the recency gap survives news AND a named founder exists;
and NO paid call at all when there is no gap.
"""

import unittest
from unittest import mock

import research.source_planner as sp


class ApolloDecisionTests(unittest.TestCase):
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    def test_disabled_flag_blocks(self):
        fire, reason = sp._decide_apollo({"primary_contact_name": "Dana"})
        self.assertFalse(fire)
        self.assertIn("disabled", reason)

    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", True)
    @mock.patch("research.source_planner.apollo.available", return_value=True)
    def test_fires_on_generic_email(self, _av):
        fire, reason = sp._decide_apollo(
            {"primary_contact_name": "Dana", "public_contact_email": "info@acme.com"})
        self.assertTrue(fire)
        self.assertIn("generic", reason)

    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", True)
    @mock.patch("research.source_planner.apollo.available", return_value=True)
    def test_fires_on_missing_email(self, _av):
        fire, reason = sp._decide_apollo({"founder_name": "Dana"})
        self.assertTrue(fire)
        self.assertIn("no email", reason)

    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", True)
    @mock.patch("research.source_planner.apollo.available", return_value=True)
    def test_skips_specific_email(self, _av):
        fire, reason = sp._decide_apollo(
            {"primary_contact_name": "Dana", "public_contact_email": "dana@acme.com"})
        self.assertFalse(fire)
        self.assertIn("specific email", reason)

    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", True)
    @mock.patch("research.source_planner.apollo.available", return_value=True)
    def test_skips_when_no_name(self, _av):
        fire, reason = sp._decide_apollo({"public_contact_email": "info@acme.com"})
        self.assertFalse(fire)
        self.assertIn("no named contact", reason)


class RecencyGapTests(unittest.TestCase):
    def test_gap_when_nothing_recent(self):
        gap, _ = sp._recency_gap({"company_name": "Acme"})
        self.assertTrue(gap)

    def test_no_gap_with_recent_focus(self):
        gap, reason = sp._recency_gap({"recent_focus": "Shipped v2 last week"})
        self.assertFalse(gap)
        self.assertIn("recent_focus", reason)

    def test_no_gap_with_traction(self):
        gap, _ = sp._recency_gap({"metrics_or_traction": "$1M ARR"})
        self.assertFalse(gap)


class MergeTests(unittest.TestCase):
    def test_merge_news_picks_freshest_relevant_and_fills_fields(self):
        data = {}
        ok = sp._merge_news(data, [
            {"url": "u1", "title": "Acme old thing", "published_date": "2026-01-01"},
            {"url": "u2", "title": "Acme raised a Series B", "published_date": "2026-07-10"},
        ], {"acme"})
        self.assertTrue(ok)
        self.assertEqual(data["recent_focus"], "Acme raised a Series B")
        self.assertEqual(data["unique_hook"], "Acme raised a Series B")
        self.assertIn("Acme raised a Series B", data["additional_hooks"])
        self.assertEqual(data["recency_enrichment"]["source"], "tavily_news")
        self.assertEqual(data["recency_enrichment"]["url"], "u2")

    def test_merge_news_rejects_irrelevant_item(self):
        # The exact false positive the live demo caught: a fuzzy match on the short
        # name "Dub" returning a construction-news article that never says "Dub".
        data = {}
        ok = sp._merge_news(data, [{
            "url": "u", "content": "",
            "title": "6 contech startups raise a combined $121M - Construction Dive",
            "published_date": "2026-06-03"}], {"dub"})
        self.assertFalse(ok)
        self.assertNotIn("recent_focus", data)

    def test_tokens_drops_generic_suffixes(self):
        self.assertEqual(sp._tokens("Acme Technologies Inc", "https://acme.io"), {"acme"})

    def test_merge_posts_picks_freshest_by_created_at(self):
        data = {}
        ok = sp._merge_posts(data, [
            {"text": "older post", "created_at": "2026-06-01", "url": "p1"},
            {"text": "just shipped v2", "created_at": "2026-07-15", "url": "p2"},
        ])
        self.assertTrue(ok)
        self.assertEqual(data["recent_focus"], "just shipped v2")
        self.assertEqual(data["recency_enrichment"]["source"], "x_search")

    def test_apply_recent_never_overwrites_existing_focus(self):
        data = {"recent_focus": "real site signal"}
        sp._apply_recent(data, "news blurb", source="tavily_news", ref="u", when=None)
        self.assertEqual(data["recent_focus"], "real site signal")   # untouched
        self.assertIn("news blurb", data["additional_hooks"])        # still recorded


class RunTests(unittest.TestCase):
    """End-to-end run() with every provider mocked. Asserts the ordering rules and
    the no-network-without-a-gap discipline."""

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", True)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily")
    @mock.patch("research.source_planner.x_search")
    @mock.patch("research.source_planner.apollo")
    def test_no_gap_fires_nothing(self, m_apollo, m_x, m_tav):
        data = {"company_name": "Acme", "recent_focus": "Series B last month"}
        report = sp.run(data, "https://acme.com")
        m_apollo.enrich_person.assert_not_called()
        m_tav.recent_news.assert_not_called()
        m_x.search_recent_posts.assert_not_called()
        self.assertTrue(all(not r["fired"] for r in report))

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", True)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily.available", return_value=True)
    @mock.patch("research.source_planner.tavily.recent_news")
    @mock.patch("research.source_planner.x_search.available", return_value=True)
    @mock.patch("research.source_planner.x_search.search_recent_posts")
    def test_news_closes_gap_and_x_is_skipped(self, m_posts, _xav, m_news, _tav):
        m_news.return_value = [{"url": "u", "title": "Acme raised a Series B",
                                "published_date": "2026-07-10"}]
        data = {"company_name": "Acme", "founder_name": "Dana Lee"}   # gap present
        report = sp.run(data, "https://acme.com")
        m_news.assert_called_once()
        m_posts.assert_not_called()                                  # X not needed
        self.assertIn("Series B", data["recent_focus"])
        x_rec = _by_source(report, "x_search")
        self.assertFalse(x_rec["fired"])
        self.assertIn("closed by news", x_rec["reason"])

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", True)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily.available", return_value=True)
    @mock.patch("research.source_planner.tavily.recent_news", return_value=[])
    @mock.patch("research.source_planner.x_search.available", return_value=True)
    @mock.patch("research.source_planner.x_search.search_recent_posts")
    def test_x_fires_when_news_empty_and_founder_present(self, m_posts, *_):
        m_posts.return_value = {"status": "ok", "posts": [
            {"text": "just shipped v2", "created_at": "2026-07-15", "url": "x"}]}
        data = {"company_name": "Acme", "founder_name": "Dana Lee"}
        report = sp.run(data, "https://acme.com")
        m_posts.assert_called_once()
        self.assertIn("shipped v2", data["recent_focus"])
        self.assertTrue(_by_source(report, "x_search")["fired"])

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", True)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily.available", return_value=True)
    @mock.patch("research.source_planner.tavily.recent_news", return_value=[])
    @mock.patch("research.source_planner.x_search.available", return_value=True)
    @mock.patch("research.source_planner.x_search.search_recent_posts")
    def test_x_not_fired_without_named_founder(self, m_posts, *_):
        data = {"company_name": "Acme"}                 # gap, but NO founder
        report = sp.run(data, "https://acme.com")
        m_posts.assert_not_called()
        x_rec = _by_source(report, "x_search")
        self.assertFalse(x_rec["fired"])
        self.assertIn("no named founder", x_rec["reason"])

    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", True)
    @mock.patch("research.source_planner.apollo.available", return_value=True)
    @mock.patch("research.source_planner.apollo.enrich_person")
    @mock.patch("research.source_planner.tavily.available", return_value=False)
    @mock.patch("research.source_planner.x_search.available", return_value=False)
    def test_apollo_fires_and_merges(self, _xav, _tav, m_enrich, _apav):
        m_enrich.return_value = {"status": "ok", "person": {
            "name": "Dana Lee", "title": "VP of Growth",
            "email": "dana@acme.com", "email_status": "verified"}}
        data = {"primary_contact_name": "Dana Lee", "company_name": "Acme",
                "public_contact_email": "info@acme.com",
                "recent_focus": "already fresh"}       # close recency gap
        report = sp.run(data, "https://acme.com")
        m_enrich.assert_called_once()
        self.assertEqual(data["primary_contact_email"], "dana@acme.com")  # real merge
        self.assertEqual(_by_source(report, "apollo")["outcome"], "matched")

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", True)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily.available", return_value=True)
    @mock.patch("research.source_planner.tavily.recent_news",
                side_effect=RuntimeError("boom"))
    @mock.patch("research.source_planner.x_search.available", return_value=False)
    def test_provider_exception_never_raises(self, _xav, _news, _tav):
        data = {"company_name": "Acme", "founder_name": "Dana Lee"}
        report = sp.run(data, "https://acme.com")          # must not raise
        self.assertEqual(_by_source(report, "tavily_news")["outcome"], "error")

    @mock.patch("research.source_planner.PLANNER_ESCALATION_ENABLED", False)
    @mock.patch("research.source_planner.APOLLO_ENRICH_ENABLED", False)
    @mock.patch("research.source_planner.tavily")
    @mock.patch("research.source_planner.x_search")
    def test_escalation_flag_off_skips_news_and_x_without_calls(self, m_x, m_tav):
        # A real recency gap + a founder — but the kill switch is OFF, so neither
        # paid source is touched.
        data = {"company_name": "Acme", "founder_name": "Dana Lee"}
        report = sp.run(data, "https://acme.com")
        m_tav.recent_news.assert_not_called()
        m_x.search_recent_posts.assert_not_called()
        news = _by_source(report, "x_search")
        self.assertFalse(news["fired"])
        self.assertIn("PLANNER_ESCALATION_ENABLED off", news["reason"])


def _by_source(report, source):
    return next(r for r in report if r["source"] == source)


if __name__ == "__main__":
    unittest.main()
