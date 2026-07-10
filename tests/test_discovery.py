"""Prospect Discovery Agent tests — offline, deterministic.

The search providers are mocked so no network is touched, and the store runs on
an isolated temp SQLite DB. These pin the behaviours the spec asks for: search,
candidate extraction, dedupe, cursor pagination, filters, provider fallback,
database storage, error handling, and the chat-tool integration.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

# Force the local SQLite backend (mirrors tests/conftest.py, but also holds when
# this module is run directly via unittest, which doesn't load conftest).
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.db import Database  # noqa: E402
from discovery import engine, sources  # noqa: E402
from discovery.models import DiscoveryQuery, Prospect, registrable_domain  # noqa: E402
from discovery.store import ProspectStore  # noqa: E402

# Sample provider results: real companies + an aggregator that MUST be dropped.
_EXA = [
    {"url": "https://stripe.com/pricing", "title": "Stripe | Payments",
     "content": "B2B fintech payments SaaS platform"},
    {"url": "https://wave.com", "title": "Wave",
     "content": "fintech for small business in Canada, raised series a"},
    {"url": "https://techcrunch.com/2024/list", "title": "Top 20 fintech startups",
     "content": "a listicle of companies"},
    {"url": "https://neo.com", "title": "Neo Financial",
     "content": "fintech banking Canada b2b"},
    {"url": "https://shopify.com", "title": "Shopify",
     "content": "ecommerce platform in Canada"},
]


def _store():
    db = Database(sqlite_path=os.path.join(tempfile.mkdtemp(), "prospects.db"))
    return ProspectStore(db=db)


def _patched(exa_results=_EXA, tavily_results=None):
    """Patch the providers to return fixed results and report available."""
    tavily_results = [] if tavily_results is None else tavily_results
    return [
        mock.patch("discovery.sources.exa.search", return_value=exa_results),
        mock.patch("discovery.sources.tavily.search", return_value=tavily_results),
        mock.patch("discovery.sources.exa.available", return_value=True),
        mock.patch("discovery.sources.tavily.available", return_value=True),
    ]


def _run(owner, query, store, exa_results=_EXA, tavily_results=None):
    patches = _patched(exa_results, tavily_results)
    for p in patches:
        p.start()
    try:
        return engine.discover(owner, query, store=store)
    finally:
        for p in patches:
            p.stop()


class ModelTests(unittest.TestCase):
    def test_registrable_domain(self):
        self.assertEqual(registrable_domain("https://www.Stripe.com/pricing"), "stripe.com")
        self.assertEqual(registrable_domain("neo.com"), "neo.com")

    def test_query_normalizes_and_builds_search(self):
        q = DiscoveryQuery(industry="Fintech", location="Canada",
                           keywords="B2B, AI", funding_stage="Series A")
        self.assertEqual(q.keywords, ["b2b", "ai"])
        s = q.search_string().lower()
        self.assertIn("fintech", s)
        self.assertIn("canada", s)
        self.assertIn("series a", s)

    def test_prospect_public_shape(self):
        p = Prospect(company_name="Wave", website="https://wave.com")
        pub = p.public()
        for k in ("company_name", "website", "industry", "location",
                  "estimated_company_size", "estimated_stage", "confidence",
                  "why_it_matches", "discovery_source", "basic_signals"):
            self.assertIn(k, pub)


class SourceExtractionTests(unittest.TestCase):
    def test_aggregator_dropped_company_kept(self):
        q = DiscoveryQuery(industry="fintech", keywords=["b2b"])
        for p in _patched():
            p.start()
        try:
            cands = sources.search_candidates(q, pool_size=10)
        finally:
            for p in _patched():
                pass
            mock.patch.stopall()
        domains = {c.domain for c in cands}
        self.assertIn("stripe.com", domains)
        self.assertNotIn("techcrunch.com", domains)     # listicle dropped

    def test_signals_and_stage_detected(self):
        q = DiscoveryQuery(industry="fintech")
        p = sources._build({"url": "https://acmefintech.example", "title": "Acme Fintech",
                            "content": "fintech, raised series a, hiring SDRs"},
                           q, "exa")
        self.assertIn("series a", p.basic_signals)
        self.assertEqual(p.estimated_stage, "series a")

    def test_wiki_and_entertainment_pages_rejected(self):
        q = DiscoveryQuery(raw="American horror story SaaS companies", keywords=["saas"])
        bad = [
            {"url": "https://americanhorrorstory.fandom.com/wiki/American_Horror_Story",
             "title": "American Horror Story Wiki | Fandom",
             "content": "American Horror Story episodes, cast, seasons and characters."},
            {"url": "https://en.wikipedia.org/wiki/American_Horror_Story:_Delicate",
             "title": "American Horror Story: Delicate - Wikipedia",
             "content": "American television season and episode guide."},
            {"url": "https://www.imdb.com/title/tt1844624/",
             "title": "American Horror Story (TV Series 2011-) - IMDb",
             "content": "Cast, reviews, episode guide and trailers."},
            {"url": "https://www.rottentomatoes.com/tv/american_horror_story",
             "title": "American Horror Story - Rotten Tomatoes",
             "content": "Critic reviews, trailers, cast and ratings."},
        ]
        for raw in bad:
            self.assertIsNone(sources._build(raw, q, "exa"), raw["url"])

    def test_articles_directories_and_listicles_rejected(self):
        q = DiscoveryQuery(industry="saas", keywords=["ai"])
        bad = [
            {"url": "https://example.com/blog/best-ai-saas-tools",
             "title": "Best 25 AI SaaS Tools",
             "content": "A blog post listicle of tools and reviews."},
            {"url": "https://directory.example.com/companies/acme",
             "title": "Acme profile",
             "content": "Directory listing and company profile."},
        ]
        for raw in bad:
            self.assertIsNone(sources._build(raw, q, "tavily"), raw["url"])

    def test_real_saas_company_homepage_allowed(self):
        q = DiscoveryQuery(industry="saas", keywords=["workflow"])
        p = sources._build(
            {"url": "https://linear.app",
             "title": "Linear - Plan and build products",
             "content": "Linear is a software platform for product teams with customers, pricing, integrations and careers."},
            q,
            "exa",
        )
        self.assertIsNotNone(p)
        self.assertEqual(p.domain, "linear.app")

    def test_pr_news_and_startup_databases_rejected(self):
        q = DiscoveryQuery(industry="saas", keywords=["ai"])
        bad = [
            {"url": "https://www.prnewswire.com/news-releases/acme-launches-ai-tool-302000000.html",
             "title": "Acme launches AI tool - PR Newswire",
             "content": "Press release and media distribution for company announcements."},
            {"url": "https://tracxn.com/d/companies/acme/__abc",
             "title": "Acme company profile - Tracxn",
             "content": "Startup database profile page with funding and competitors."},
            {"url": "https://startupsavant.com/startups-to-watch",
             "title": "Best Startups to Watch",
             "content": "A directory listicle of startup profiles and articles."},
        ]
        for raw in bad:
            self.assertIsNone(sources._build(raw, q, "exa"), raw["url"])

    def test_vc_domains_rejected_unless_icp_targets_investors(self):
        saas_q = DiscoveryQuery(industry="saas", keywords=["seed"])
        vc = {"url": "https://acronym.vc",
              "title": "Acronym VC",
              "content": "A venture capital fund investing in seed stage software companies."}
        self.assertIsNone(sources._build(vc, saas_q, "exa"))

        investor_q = DiscoveryQuery(raw="VC funds investing in B2B SaaS", keywords=["investors"])
        p = sources._build(vc, investor_q, "exa")
        self.assertIsNotNone(p)
        self.assertEqual(p.domain, "acronym.vc")

    def test_saas_icp_rejects_service_agency_or_company_builder(self):
        q = DiscoveryQuery(industry="saas", raw="B2B SaaS companies", keywords=["software"])
        bad = [
            {"url": "https://seedtechnologies.net",
             "title": "Seed Technologies - Software Development Services",
             "content": "Custom software development services, consulting, outsourcing and digital transformation."},
            {"url": "https://totipotent.vc",
             "title": "Totipotent VC",
             "content": "A company builder and venture studio backing portfolio companies."},
        ]
        for raw in bad:
            self.assertIsNone(sources._build(raw, q, "tavily"), raw["url"])


class EngineTests(unittest.TestCase):
    def test_error_without_filters(self):
        r = engine.discover("u", DiscoveryQuery(), store=_store())
        self.assertEqual(r.status, "error")

    def test_ok_ranks_and_stores(self):
        s = _store()
        r = _run("u1", DiscoveryQuery(industry="fintech", location="Canada",
                                      keywords=["b2b"], limit=3), s)
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.returned >= 1)
        self.assertTrue(all(0 <= p.confidence <= 1 for p in r.prospects))
        self.assertEqual(s.count_for_owner("u1"), r.returned)   # persisted

    def test_cursor_pagination_returns_new_each_time(self):
        s = _store()
        q = lambda: DiscoveryQuery(industry="fintech", keywords=["b2b"], limit=2)
        first = _run("u2", q(), s)
        second = _run("u2", q(), s)     # "find another 2"
        self.assertTrue(first.returned >= 1 and second.returned >= 1)
        d1 = {p.domain for p in first.prospects}
        d2 = {p.domain for p in second.prospects}
        self.assertEqual(d1 & d2, set())           # no overlap (dedupe cursor)

    def test_exclude_keyword_filter(self):
        s = _store()
        r = _run("u3", DiscoveryQuery(industry="fintech", keywords=["b2b"],
                                      exclude_keywords=["ecommerce"], limit=10), s)
        self.assertNotIn("shopify.com", {p.domain for p in r.prospects})

    def test_provider_fallback_exa_only(self):
        # Tavily unavailable -> still discovers via Exa.
        s = _store()
        patches = [
            mock.patch("discovery.sources.exa.search", return_value=_EXA),
            mock.patch("discovery.sources.tavily.search", return_value=[]),
            mock.patch("discovery.sources.exa.available", return_value=True),
            mock.patch("discovery.sources.tavily.available", return_value=False),
        ]
        for p in patches:
            p.start()
        try:
            r = engine.discover("u4", DiscoveryQuery(industry="fintech",
                                keywords=["b2b"], limit=5), store=s)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(r.status, "ok")

    def test_error_when_no_provider(self):
        patches = [mock.patch("discovery.sources.exa.available", return_value=False),
                   mock.patch("discovery.sources.tavily.available", return_value=False)]
        for p in patches:
            p.start()
        try:
            r = engine.discover("u5", DiscoveryQuery(industry="fintech"), store=_store())
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(r.status, "error")


class StoreTests(unittest.TestCase):
    def test_save_is_idempotent_per_owner_domain(self):
        s = _store()
        p = Prospect(company_name="Wave", website="https://wave.com", owner="o1")
        self.assertEqual(s.save_many([p]), 1)
        self.assertEqual(s.save_many([p]), 0)       # duplicate -> no-op
        self.assertIn("wave.com", s.seen_domains("o1"))

    def test_per_owner_isolation(self):
        s = _store()
        s.save_many([Prospect(company_name="Wave", website="https://wave.com", owner="a")])
        self.assertEqual(s.seen_domains("b"), set())
        self.assertEqual(s.count_for_owner("b"), 0)

    def test_mark_researched(self):
        s = _store()
        s.save_many([Prospect(company_name="Wave", website="https://wave.com", owner="a")])
        s.mark_researched("a", "wave.com")
        row = s.list_for_owner("a")[0]
        self.assertEqual(row.status, "researched")


class ChatToolTests(unittest.TestCase):
    def _conv(self):
        from chat.models import Conversation
        c = Conversation()
        c._user_id = "chat_user"
        return c

    def test_tool_discovers_and_stores_in_workspace(self):
        import chat.tools as tools
        from discovery.engine import DiscoveryResult
        result = DiscoveryResult(
            "ok", prospects=[Prospect(company_name="Wave", website="https://wave.com",
                                      industry="fintech", why_it_matches="Matches fintech",
                                      discovery_source="exa")],
            returned=1, has_more=True)
        with mock.patch("chat.tools.discovery_engine.discover", return_value=result):
            r = tools.execute("find_prospects",
                              {"industry": "fintech", "location": "Canada"}, self._conv())
        self.assertIn("Wave", r.summary)
        self.assertIn("another batch", r.summary)      # has_more surfaced
        self.assertEqual(len(r.workspace_updates["prospects_last"]), 1)

    def test_tool_empty_is_reported(self):
        import chat.tools as tools
        from discovery.engine import DiscoveryResult
        with mock.patch("chat.tools.discovery_engine.discover",
                        return_value=DiscoveryResult("empty", reason="No more matches.")):
            r = tools.execute("find_prospects", {"industry": "fintech"}, self._conv())
        self.assertIn("No more matches", r.summary)
        self.assertEqual(r.workspace_updates["prospects_last"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
