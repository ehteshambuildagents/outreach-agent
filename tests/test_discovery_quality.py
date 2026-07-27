"""Candidate-quality tests: aggregator classification, strict selection, Apollo.

These lock in the behaviour behind a real bug report. Asking for "B2B founders
hiring for an AI video creator role" returned mediabistro.com, jobs-radar.com and
lever.co above the actual AI-video companies, because the old pipeline searched
the web for the job posting and then REWARDED hiring vocabulary.

Nothing here touches the network: the Apollo provider is stubbed, so the tests
assert the orchestration and ranking rules rather than a live API.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import aggregators, engine, intent, scoring, sources   # noqa: E402
from discovery.models import DiscoveryQuery, Prospect       # noqa: E402
from research import apollo_orgs                            # noqa: E402


class IntentPlannerTests(unittest.TestCase):
    """The plan is code, not a model guess. Same ask, same plan, every time."""

    ASK = "B2B founders hiring for an AI video creator role"

    def test_role_is_the_anchor_not_the_industry(self):
        """The bug this fixes: the ask was turned into the category "ai video" and
        returned AI-video VENDORS (HeyGen, Synthesia). But a company does not sell
        AI video to hire someone to make it, so Clay, Vanta and Ramp could never
        surface. The role must be the axis."""
        plan = intent.parse(self.ASK, industry="B2B SaaS",
                            keywords=["hiring AI video creator", "AI video",
                                      "video generation"])
        self.assertEqual(plan.anchor, "role")
        self.assertEqual(plan.roles, ["ai video creator"])
        # Crucially: NO category filter, because the user never named a vertical.
        self.assertEqual(plan.keyword_tags, [])
        # The category survives only as a ranking hint.
        self.assertIn("ai video", plan.relevance_terms)

    def test_generic_wrappers_are_not_industries(self):
        for text in ("B2B SaaS", "software companies", "startups", "tech"):
            self.assertEqual(intent.parse(f"find {text} hiring an SDR").industry, "",
                             text)

    def test_a_named_vertical_does_filter(self):
        plan = intent.parse("fintech companies hiring an SDR")
        self.assertEqual(plan.industry, "fintech")
        self.assertEqual(plan.anchor, "both")
        self.assertEqual(plan.keyword_tags, ["fintech"])

    def test_role_titles_expand_to_what_employers_actually_post(self):
        titles = intent.parse(self.ASK).role_titles
        for expected in ("ai video creator", "video editor", "video producer",
                         "video creator"):
            self.assertIn(expected, titles)

    def test_gtm_abbreviations_expand(self):
        titles = intent.parse("SaaS founders hiring an SDR").role_titles
        self.assertIn("sales development representative", titles)

    def test_size_location_and_stage_parsed_deterministically(self):
        plan = intent.parse("seed stage fintech companies in Berlin under 50 employees")
        self.assertEqual(plan.size_band, (1, 50))
        self.assertEqual(plan.employee_ranges, ["1,10", "11,20", "21,50"])
        self.assertIn("Berlin", plan.locations)
        self.assertIn("seed", plan.stages)

    def test_the_word_founders_asks_for_founder_reachable_companies(self):
        """"B2B founders …" must set the founder-access preference; the earlier
        regex only matched the singular, so the plural (the common phrasing)
        silently did nothing and enterprises were never demoted."""
        self.assertTrue(intent.parse("B2B founders hiring an AI video creator").wants_founder_access)
        self.assertTrue(intent.parse("small companies hiring an SDR").wants_founder_access)
        self.assertFalse(intent.parse("companies hiring an SDR").wants_founder_access)

    def test_same_ask_always_produces_the_same_plan(self):
        a = intent.parse(self.ASK, keywords=["AI video"]).public()
        b = intent.parse(self.ASK, keywords=["AI video"]).public()
        self.assertEqual(a, b)

    def test_recruiters_and_agencies_are_identified_from_codes(self):
        self.assertTrue(intent.is_staffing(["7361"]))          # employment agency
        self.assertTrue(intent.is_staffing(["7363"]))          # staffing
        self.assertTrue(intent.is_staffing(None, ["56131"]))   # staffing NAICS
        self.assertFalse(intent.is_staffing(["7372"]))         # prepackaged software

    def test_plan_steps_are_human_readable(self):
        steps = intent.parse(self.ASK).steps
        self.assertTrue(any("live posting" in s for s in steps))
        self.assertTrue(any("recruiters" in s for s in steps))


class PluralAcronymRoleTests(unittest.TestCase):
    """"Find B2B founders hiring SDRs" is the most natural phrasing of the
    query and it parsed with no role at all, so Apollo got no title search and
    nothing was posting-verified. Singular worked; only the plural was lost."""

    def test_plural_acronyms_resolve_to_the_singular_alias(self):
        for text, expected in (("Find B2B founders hiring SDRs", "sdr"),
                               ("companies hiring BDRs", "bdr"),
                               ("hiring AEs", "ae"),
                               ("hiring CSMs", "csm")):
            plan = intent.parse(text)
            self.assertEqual(plan.anchor, "role", text)
            self.assertEqual(plan.roles, [expected], text)

    def test_plural_and_singular_agree(self):
        self.assertEqual(intent.parse("hiring SDRs").role_titles,
                         intent.parse("hiring an SDR").role_titles)


class RoleFirstApolloTests(unittest.TestCase):
    """A role-anchored plan must search Apollo by JOB TITLE, not by category."""

    def test_job_titles_are_sent_and_category_is_not(self):
        plan = intent.parse("B2B founders hiring for an AI video creator role",
                            keywords=["AI video"])
        captured = {}

        def _search(**kwargs):
            captured.update(kwargs)
            return {"status": "empty", "organizations": [], "total": 0}

        q = DiscoveryQuery(raw=plan.raw, keywords=["ai video"], limit=10)
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "search_organizations", _search):
            sources.search_apollo(q, plan=plan)
        self.assertIn("ai video creator", captured.get("job_titles") or [])
        self.assertFalse(captured.get("keywords"),
                         "a role-anchored plan must not filter by category")

    def test_staffing_firms_are_dropped_from_role_searches(self):
        """A live role-first search came back dominated by recruiters, because
        recruiters post the most job ads."""
        plan = intent.parse("companies hiring an AI video creator")
        fake = {"status": "ok", "total": 3, "organizations": [
            apollo_orgs.organization({"id": "1", "name": "Clay",
                                      "primary_domain": "clay.com",
                                      "sic_codes": ["7372"]}),
            apollo_orgs.organization({"id": "2", "name": "Onward Search",
                                      "primary_domain": "onwardsearch.com",
                                      "sic_codes": ["7361", "7363"]}),
            apollo_orgs.organization({"id": "3", "name": "Handle Recruitment",
                                      "primary_domain": "handle.co.uk",
                                      "sic_codes": ["7335", "7361"]}),
        ]}
        q = DiscoveryQuery(raw=plan.raw, limit=10)
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "search_organizations", lambda **k: fake):
            got = sources.search_apollo(q, plan=plan)
        self.assertEqual([p.domain for p in got], ["clay.com"])


class CustomerLikelihoodTests(unittest.TestCase):
    """Optimise for "likely to become a paying customer", not keyword similarity."""

    def test_weights_put_in_market_above_on_topic(self):
        self.assertGreater(scoring.W_BUYING + scoring.W_HIRING, scoring.W_ICP)

    def test_every_factor_is_reported_for_auditability(self):
        plan = intent.parse("saas companies hiring an sdr", keywords=["sdr"])
        p = Prospect(company_name="Acme", website="https://acme.com", domain="acme.com",
                     industry_kind="software", founded_year=2021,
                     growth={"headcount_6mo": 0.3},
                     hiring={"verified": True, "match": "role", "open_roles": 4,
                             "summary": "Hiring: SDR"})
        result = scoring.score_prospect(p, plan)
        for key in ("icp_match", "buying_signal", "hiring_relevance",
                    "founder_access", "corroboration", "total"):
            self.assertIn(key, result.public())
        self.assertTrue(result.reasons)

    def test_large_companies_score_lower_on_founder_access(self):
        plan = intent.parse("companies hiring an sdr")
        small = Prospect(company_name="S", website="https://s.com", domain="s.com",
                         employee_count=20)
        big = Prospect(company_name="B", website="https://b.com", domain="b.com",
                       employee_count=5000)
        self.assertGreater(scoring.score_prospect(small, plan).access,
                           scoring.score_prospect(big, plan).access)

    def test_a_private_company_is_never_reported_as_publicly_traded(self):
        """Apollo fills `publicly_traded_exchange` speculatively: it returns
        "nasdaq" for OpenAI and Anthropic, which are private. The card states this
        back to the user ("Large public company"), so only the TICKER may set it.
        Scale still demotes them, via revenue."""
        private_giant = apollo_orgs.organization({
            "id": "1", "name": "Anthropic", "primary_domain": "anthropic.com",
            "publicly_traded_exchange": "nasdaq", "publicly_traded_symbol": None,
            "organization_revenue": 30_000_000_000})
        self.assertFalse(private_giant["is_public"])
        self.assertEqual(private_giant["revenue"], 30_000_000_000)
        listed = apollo_orgs.organization({
            "id": "2", "name": "Salesforce", "primary_domain": "salesforce.com",
            "publicly_traded_exchange": "nasdaq", "publicly_traded_symbol": "CRM"})
        self.assertTrue(listed["is_public"])
        # Either way the enterprise demotion still applies.
        plan = intent.parse("b2b founders hiring an sdr")
        giant = Prospect(company_name="Anthropic", website="https://a.com",
                         domain="a.com", annual_revenue=30_000_000_000,
                         industry_kind="software",
                         hiring={"verified": True, "match": "role"})
        small = Prospect(company_name="Small", website="https://s.com",
                         domain="s.com", annual_revenue=4_000_000,
                         industry_kind="software",
                         hiring={"verified": True, "match": "role"})
        engine._rescore([giant, small], plan)
        self.assertEqual(engine._rank([giant, small], plan)[0].domain, "s.com")

    def test_scale_signals_lower_founder_access(self):
        """Apollo's company search returns no headcount, so public-market status
        and revenue stand in for "can a founder actually reach the buyer"."""
        plan = intent.parse("companies hiring an sdr")
        public = Prospect(company_name="P", website="https://p.com", domain="p.com",
                          is_public=True, annual_revenue=2_000_000_000)
        small = Prospect(company_name="S", website="https://s.com", domain="s.com",
                         annual_revenue=3_000_000)
        self.assertGreater(scoring.score_prospect(small, plan).access,
                           scoring.score_prospect(public, plan).access)

    def test_enterprise_companies_are_demoted_below_a_founder_led_one(self):
        """The mega-corp bug: a role search returned Amazon and Microsoft at the
        top because they post the most jobs. A public / nine-figure company is not
        a plausible buyer for a founder's AI SDR, even with a perfect role match."""
        plan = intent.parse("b2b founders hiring an ai video creator")
        self.assertTrue(plan.wants_founder_access)
        founder_led = Prospect(
            company_name="Small", website="https://small.com", domain="small.com",
            industry_kind="software", annual_revenue=4_000_000, founded_year=2022,
            hiring={"verified": True, "match": "any", "open_roles": 3,
                    "summary": "3 open roles"})
        mega = Prospect(
            company_name="Mega", website="https://mega.com", domain="mega.com",
            industry_kind="software", is_public=True, annual_revenue=50_000_000_000,
            hiring={"verified": True, "match": "role", "open_roles": 900,
                    "summary": "Hiring: AI Video Creator"})
        engine._rescore([founder_led, mega], plan)
        ranked = engine._rank([founder_led, mega], plan)
        self.assertEqual(ranked[0].domain, "small.com",
                         f"small={founder_led.score} mega={mega.score}")


class AggregatorClassificationTests(unittest.TestCase):
    """The gate that was missing entirely."""

    def test_known_job_boards_and_ats_are_classified(self):
        for domain, kind in (
            ("indeed.com", aggregators.JOB_BOARD),
            ("remoteleaf.com", aggregators.JOB_BOARD),
            ("weworkremotely.com", aggregators.JOB_BOARD),
            ("lever.co", aggregators.ATS),
            ("greenhouse.io", aggregators.ATS),
            ("upwork.com", aggregators.MARKETPLACE),
            ("crunchbase.com", aggregators.DIRECTORY),
            ("techcrunch.com", aggregators.MEDIA),
        ):
            self.assertEqual(aggregators.domain_kind(domain), kind, domain)

    def test_job_domains_caught_by_pattern_not_only_by_list(self):
        """The four that a live run surfaced and the curated list missed."""
        for domain in ("jobs-radar.com", "simplify.jobs", "acme.careers",
                       "remotejobsboard.io"):
            self.assertEqual(aggregators.domain_kind(domain), aggregators.JOB_BOARD,
                             domain)

    def test_singular_career_compound_is_a_job_board(self):
        """gulfcareerhunt.com ranked 6th in a live 'hiring an AI video creator'
        run: the fused-compound rule only knew the plural 'careers'."""
        for domain in ("gulfcareerhunt.com", "careerhunt.io", "mycareer.com"):
            self.assertEqual(aggregators.domain_kind(domain), aggregators.JOB_BOARD,
                             domain)

    def test_real_companies_are_not_mistaken_for_job_boards(self):
        """Token-exact matching: 'jobber' must not trip the 'job' rule, and
        'carer'/'careem' must not trip the widened 'career' rule."""
        for domain in ("jobber.com", "stripe.com", "heygen.com", "runwayml.com",
                       "synthesia.io", "linear.app", "trycomp.ai",
                       "carers.org", "careem.com"):
            self.assertEqual(aggregators.domain_kind(domain), aggregators.COMPANY,
                             domain)

    def test_web_sourced_staffing_firms_are_demoted(self):
        """Apollo candidates are excluded by SIC code, but web candidates carry
        none: memoryblue.com, an outsourced-SDR firm, ranked as a prospect for
        "founders hiring SDRs"."""
        kind, _ = aggregators.classify(
            "https://memoryblue.com", "memoryBlue",
            "memoryBlue is a sales development company that recruits and "
            "trains SDRs for technology firms.")
        self.assertIn(kind, aggregators.INTERMEDIARY_KINDS)

    def test_recruiting_software_is_still_a_prospect(self):
        """The guard that keeps the rule safe: an ATS vendor sells a recruiting
        PLATFORM, never a recruiting AGENCY, and is a legitimate B2B prospect."""
        for title, content in (
                ("Gem", "Recruiting software and CRM for talent teams."),
                ("Rippling", "HR, IT and payroll software platform.")):
            self.assertFalse(aggregators.sells_staffing(title, content), title)

    def test_careers_page_heading_does_not_become_a_company_name(self):
        self.assertEqual(sources._name_from("Explore SDR Sales Jobs",
                                            "memoryblue.com"), "Memoryblue")
        self.assertEqual(sources._name_from("Linear - Plan and build products",
                                            "linear.app"), "Linear")

    def test_directory_pages_are_dropped_but_job_boards_only_demoted(self):
        q = DiscoveryQuery(raw="ai video companies", keywords=["ai video"])
        kind, drop = sources.assess(
            {"url": "https://tracxn.com/d/companies/acme", "title": "Acme profile",
             "content": "company profile"}, q)
        self.assertEqual(drop and kind, "")          # dropped outright
        kind, drop = sources.assess(
            {"url": "https://remoteleaf.com/jobs", "title": "Remote jobs",
             "content": "browse jobs"}, q)
        self.assertEqual(kind, aggregators.JOB_BOARD)
        self.assertFalse(drop)                        # demoted, not dropped

    def test_explicitly_requested_category_is_admitted(self):
        q = DiscoveryQuery(raw="find me recruiting platforms and ATS vendors",
                           keywords=["ats"])
        kind, drop = sources.assess(
            {"url": "https://lever.co", "title": "Lever", "content": "hiring software"}, q)
        self.assertEqual(kind, aggregators.COMPANY)
        self.assertFalse(drop)

    def test_own_careers_page_is_hiring_evidence_third_party_is_not(self):
        self.assertTrue(aggregators.is_hiring_evidence(
            "https://jobs.storykit.io/jobs/123", "storykit.io"))
        self.assertTrue(aggregators.is_hiring_evidence(
            "https://storykit.io/careers/video-producer", "storykit.io"))
        self.assertFalse(aggregators.is_hiring_evidence(
            "https://jobs.lever.co/storykit/123", "storykit.io"))

    def test_a_board_advertising_another_companys_job_is_demoted(self):
        """workwithindies.com carries no job token and uses a /careers path, so
        neither list caught it. But its page title advertises a DIFFERENT company's
        role, which is the general mark of a job board."""
        self.assertTrue(aggregators.hosts_third_party_posting(
            "Optillusion Games is hiring a Social Media Video Content Creator",
            "workwithindies.com"))
        # A company writing about ITSELF is not third-party (allowing get-/try- prefixes).
        self.assertFalse(aggregators.hosts_third_party_posting(
            "Storykit is hiring a Video Producer", "storykit.io"))
        self.assertFalse(aggregators.hosts_third_party_posting(
            "Acme is hiring a Designer", "getacme.io"))
        kind, _ = aggregators.classify(
            "https://www.workwithindies.com/careers/optillusion-games-video-content-creator",
            "Optillusion Games is hiring a Social Media Video Content Creator", "")
        self.assertEqual(kind, aggregators.JOB_BOARD)

    def test_a_third_party_listing_is_not_credited_as_own_hiring(self):
        q = DiscoveryQuery(raw="hiring an ai video creator",
                           keywords=["ai video creator"], limit=5)
        p = sources._build(
            {"url": "https://www.workwithindies.com/careers/optillusion-games-video-content-creator",
             "title": "Optillusion Games is hiring a Social Media Video Content Creator",
             "content": "apply now"}, q, "exa")
        if p is not None:                       # demoted, and never a role match
            self.assertNotEqual(p.tier, "company")
            self.assertNotEqual((p.hiring or {}).get("match"), "role")


class QueryInterpretationTests(unittest.TestCase):
    """Turning the ASK into the right query for each source."""

    def _q(self):
        return DiscoveryQuery(raw="B2B founders hiring for an AI video creator role",
                              industry="B2B SaaS",
                              keywords=["ai video creator", "hiring"], limit=10)

    def test_apollo_tag_is_the_category_not_the_job_title(self):
        self.assertEqual(sources.apollo_keywords(self._q()), ["ai video"])

    def test_generic_tags_are_never_sent_alongside_a_specific_one(self):
        """Apollo ORs its tags, so 'b2b saas' next to 'ai video' floods the result
        with every SaaS company (it returned Anaplan and DocuSign in a live run)."""
        tags = sources.apollo_keywords(self._q())
        self.assertNotIn("b2b saas", tags)
        self.assertTrue(all("saas" not in t for t in tags))

    def test_purely_generic_ask_sends_no_apollo_tag(self):
        q = DiscoveryQuery(raw="find me B2B SaaS companies", industry="B2B SaaS")
        self.assertEqual(sources.apollo_keywords(q), [])

    def test_role_terms_extracted_with_a_looser_variant(self):
        self.assertEqual(sources.role_terms(self._q()),
                         ["ai video creator", "video creator"])

    def test_role_terms_never_bleed_across_keyword_boundaries(self):
        """The live chat model sends keywords as separate phrases. Matching over
        the concatenation produced "ai video creator generation", which could then
        falsely match an unrelated posting and label it as hiring for the role."""
        q = DiscoveryQuery(industry="B2B SaaS", raw="",
                           keywords=["hiring AI video creator", "AI video",
                                     "video generation"], limit=20)
        self.assertEqual(sources.role_terms(q), ["ai video creator", "video creator"])

    def test_a_product_category_is_never_treated_as_a_role(self):
        q = DiscoveryQuery(raw="find ai video companies",
                           keywords=["ai video", "video generation"])
        self.assertEqual(sources.role_terms(q), [])

    def test_unrelated_postings_never_count_as_a_role_match(self):
        q = DiscoveryQuery(industry="B2B SaaS", raw="",
                           keywords=["hiring AI video creator"], limit=20)
        titles = [{"title": "Video Editor"}, {"title": "Plumbing Design Engineer"},
                  {"title": "AI Video Creator"}]
        matched = [p["title"] for p in
                   apollo_orgs.matching_postings(titles, sources.role_terms(q))]
        self.assertEqual(matched, ["AI Video Creator"])

    def test_no_role_terms_when_the_ask_is_not_about_hiring(self):
        q = DiscoveryQuery(raw="seed stage fintech companies in Berlin",
                           industry="fintech", keywords=["payments"])
        self.assertEqual(sources.role_terms(q), [])

    def test_posting_titles_match_the_role_and_reject_unrelated_ones(self):
        postings = [{"title": "AI Video Creator"},
                    {"title": "Senior Video Content Creator"},
                    {"title": "Plumbing Design Engineer"},
                    {"title": "Sales Development Representative"}]
        matched = [p["title"] for p in
                   apollo_orgs.matching_postings(postings, sources.role_terms(self._q()))]
        self.assertEqual(matched, ["AI Video Creator", "Senior Video Content Creator"])

    def test_scattered_words_in_a_long_title_are_not_a_role_match(self):
        """The Amazon bug: a role-title search ranked Amazon #1 for "AI video
        creator" because its posting "Brand Creator Marketing Manager, Amazon MGM
        Studios + Prime Video" happens to contain both "creator" and "video". That
        is a MANAGER role; the head-noun rule rejects the scattered match."""
        postings = [
            {"title": "Brand Creator Marketing Manager, Amazon MGM Studios + Prime Video"},
            {"title": "Senior Video Content Creator"}]
        matched = [p["title"] for p in apollo_orgs.matching_postings(
            postings, ["ai video creator", "video creator"])]
        self.assertEqual(matched, ["Senior Video Content Creator"])


class HiringSignalScoringTests(unittest.TestCase):
    """The specific bug: hiring vocabulary must not buy confidence."""

    def test_hiring_keyword_in_a_snippet_earns_no_hiring_credit(self):
        q = DiscoveryQuery(raw="companies hiring", keywords=["hiring"], limit=5)
        board = sources._build(
            {"url": "https://someboard.io/listings", "title": "Jobs at startups",
             "content": "we're hiring! browse jobs, apply now"}, q, "tavily")
        # Either dropped/demoted, but never credited with a hiring signal.
        if board is not None:
            self.assertNotEqual(board.tier, "company")
            self.assertIsNone(board.hiring)

    def test_own_careers_page_sets_a_page_level_signal_only(self):
        q = DiscoveryQuery(raw="ai video companies", keywords=["ai video"], limit=5)
        p = sources._build(
            {"url": "https://acme.com/careers/office-manager", "title": "Office Manager",
             "content": "join our team, we build an ai video platform for teams"},
            q, "exa")
        self.assertIsNotNone(p)
        self.assertEqual(p.hiring["match"], "page")
        # And the company name comes from the DOMAIN, not the job title.
        self.assertEqual(p.company_name, "Acme")

    def test_role_matching_careers_page_scores_higher_than_a_bare_one(self):
        q = DiscoveryQuery(raw="hiring for a video creator role",
                           keywords=["video creator"], limit=5)
        content = "we build an ai video platform for teams, pricing and customers"
        match = sources._build(
            {"url": "https://acme.com/careers/senior-video-creator",
             "title": "Senior Video Creator", "content": content}, q, "exa")
        other = sources._build(
            {"url": "https://beta.com/careers/office-manager",
             "title": "Office Manager", "content": content}, q, "exa")
        self.assertEqual(match.hiring["match"], "role")
        self.assertEqual(other.hiring["match"], "page")
        self.assertGreater(match.confidence, other.confidence)


class RankingTests(unittest.TestCase):
    def _p(self, domain, conf, tier="company", match=None):
        return Prospect(company_name=domain.split(".")[0], website=f"https://{domain}",
                        domain=domain, confidence=conf, tier=tier,
                        hiring=({"verified": True, "match": match} if match else None))

    def test_companies_always_outrank_demoted_intermediaries(self):
        ranked = engine._rank([self._p("board.com", 0.95, tier="fallback"),
                               self._p("acme.com", 0.30)], None)
        self.assertEqual(ranked[0].domain, "acme.com")

    def test_a_role_match_outranks_a_bare_careers_page(self):
        """Hiring relevance now lives in the customer-likelihood score, so ranking
        happens after scoring rather than on a hand-set confidence."""
        plan = intent.parse("companies hiring an SDR")
        items = [self._p("weak.com", 0.0, match="page"),
                 self._p("hiring.com", 0.0, match="role")]
        engine._rescore(items, plan)
        ranked = engine._rank(items, plan)
        self.assertEqual(ranked[0].domain, "hiring.com")

    def test_buying_signal_beats_a_perfect_match_with_no_signal(self):
        """The explicit product rule: a slightly less relevant company with a
        strong buying signal outranks a perfect match that shows no sign of being
        in-market. This holds arithmetically from the weights, not by luck."""
        plan = intent.parse("B2B SaaS companies hiring an SDR",
                            keywords=["sales enablement"])
        perfect = Prospect(company_name="Perfect Match", website="https://perfect.com",
                           domain="perfect.com", industry_kind="software",
                           basic_signals=["sales enablement"])
        in_market = Prospect(company_name="In Market", website="https://inmarket.com",
                             domain="inmarket.com", industry_kind="unknown",
                             growth={"headcount_6mo": 0.25},
                             hiring={"verified": True, "match": "role",
                                     "open_roles": 8,
                                     "summary": "Hiring: SDR"})
        engine._rescore([perfect, in_market], plan)
        ranked = engine._rank([perfect, in_market], plan)
        self.assertEqual(ranked[0].domain, "inmarket.com",
                         f"perfect={perfect.score} in_market={in_market.score}")
        self.assertGreater(perfect.score["icp_match"], in_market.score["icp_match"])

    def test_corroboration_across_sources_raises_confidence(self):
        merged = {}
        a = self._p("acme.com", 0.50)
        a.discovery_source = "apollo"
        a.add_source("apollo")
        b = self._p("acme.com", 0.50)
        b.discovery_source = "exa"
        b.add_source("exa")
        q = DiscoveryQuery(raw="x", keywords=["x"])
        engine._merge(merged, [a], q, set())
        engine._merge(merged, [b], q, set())
        self.assertGreater(merged["acme.com"].confidence, 0.50)
        self.assertEqual(len(merged["acme.com"].sources), 2)
        self.assertTrue(any("Corroborated by" in r
                            for r in merged["acme.com"].match_reasons))


class StoppingCriteriaTests(unittest.TestCase):
    """Stop on QUALITY, not on 'the first pass returned something'."""

    def _fake_web(self, n):
        """Candidates that are genuinely STRONG under customer-likelihood scoring:
        a software product company, growing, hiring the asked-for role. A candidate
        with no buying signal deliberately no longer counts as strong."""
        def _search(query, pool_size=None, search_text=None, stats=None):
            out = []
            for i in range(n):
                # confidence=0.5 is the provisional value every real source sets
                # (Apollo 0.5, web >=0.30) so the candidate clears the merge floor;
                # scoring.py then overwrites it with the customer-likelihood total.
                p = Prospect(company_name=f"Co{i}", website=f"https://co{i}.com",
                             domain=f"co{i}.com", tier="company", confidence=0.5,
                             industry_kind="software",
                             growth={"headcount_6mo": 0.25},
                             hiring={"verified": True, "match": "role",
                                     "open_roles": 6, "summary": "Hiring: SDR"})
                p.add_source("exa")
                p.add_source("apollo")
                out.append(p)
            return out
        return _search

    def test_stops_early_once_the_page_is_full_of_strong_candidates(self):
        q = DiscoveryQuery(raw="saas companies hiring an sdr", keywords=["sdr"], limit=3)
        with mock.patch.object(sources, "search_candidates", self._fake_web(5)), \
             mock.patch.object(sources, "search_apollo", lambda *a, **k: []), \
             mock.patch.object(sources, "verify_hiring", lambda *a, **k: 0):
            ranked, quality = engine._search_until_good(q, set(), 20)
        self.assertEqual(quality["passes"], 1)
        self.assertEqual(quality["stopped_because"], "page_full_of_strong_candidates")

    def test_a_candidate_with_no_buying_signal_is_not_strong(self):
        """Optimising for keyword match is the thing we moved away from: matching
        the words with nothing else is not a strong candidate."""
        plan = intent.parse("saas companies hiring an sdr", keywords=["sdr"])
        bare = Prospect(company_name="Bare", website="https://bare.com",
                        domain="bare.com", tier="company")
        engine._rescore([bare], plan)
        self.assertLess(bare.confidence, 0.55)

    def test_keeps_searching_when_the_first_pass_is_thin(self):
        q = DiscoveryQuery(raw="ai video companies", keywords=["ai video"], limit=20)
        calls = []

        def _search(query, pool_size=None, search_text=None, stats=None):
            calls.append(search_text)
            return [Prospect(company_name="Co", website="https://co.com",
                             domain=f"co{len(calls)}.com", confidence=0.7,
                             tier="company")]

        with mock.patch.object(sources, "search_candidates", _search), \
             mock.patch.object(sources, "search_apollo", lambda *a, **k: []), \
             mock.patch.object(sources, "verify_hiring", lambda *a, **k: 0):
            ranked, quality = engine._search_until_good(q, set(), 20)
        self.assertGreater(quality["passes"], 1)
        self.assertEqual(quality["stopped_because"], "extra_search_stopped_helping")
        # Later passes must use a DIFFERENT angle, not repeat the same string.
        self.assertGreater(len(set(calls)), 1)


class ApolloOrgsTests(unittest.TestCase):
    def test_industry_kind_from_sic_naics_codes(self):
        self.assertEqual(apollo_orgs.industry_kind({"sic_codes": ["7372"]}), "software")
        self.assertEqual(apollo_orgs.industry_kind({"naics_codes": ["513210"]}), "software")
        self.assertEqual(apollo_orgs.industry_kind({"naics_codes": ["541611"]}), "services")
        self.assertEqual(apollo_orgs.industry_kind({}), "unknown")

    def test_organization_minimisation_normalises_the_domain(self):
        org = apollo_orgs.organization({
            "id": "abc", "name": "Storykit", "website_url": "http://www.storykit.io",
            "sic_codes": [7375], "organization_headcount_six_month_growth": 0.1153,
        })
        self.assertEqual(org["domain"], "storykit.io")
        self.assertEqual(org["website"], "https://storykit.io")
        self.assertEqual(org["sic_codes"], ["7375"])
        self.assertEqual(org["headcount_growth_6mo"], 0.1153)

    def test_apollo_candidates_skip_web_heuristics_but_not_the_blocklist(self):
        # Neutral ask: the user did NOT request job boards, so Indeed must be
        # demoted even though Apollo (correctly) considers it a company.
        q = DiscoveryQuery(raw="ai video companies", keywords=["ai video"], limit=5)
        fake = {"status": "ok", "total": 2, "organizations": [
            apollo_orgs.organization({"id": "1", "name": "HeyGen",
                                      "primary_domain": "heygen.com",
                                      "sic_codes": ["7372"]}),
            apollo_orgs.organization({"id": "2", "name": "Indeed",
                                      "primary_domain": "indeed.com"}),
        ]}
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "search_organizations", lambda **k: fake):
            got = sources.search_apollo(q)
        by_domain = {p.domain: p for p in got}
        self.assertEqual(by_domain["heygen.com"].tier, "company")
        # Indeed is a real company but still an intermediary, so it is demoted.
        self.assertEqual(by_domain["indeed.com"].tier, "fallback")
        # Confidence is assigned by scoring, not by the source, so score first.
        engine._rescore(got, intent.parse("ai video companies", keywords=["ai video"]))
        self.assertLess(by_domain["indeed.com"].confidence,
                        by_domain["heygen.com"].confidence)

    def test_verify_hiring_labels_a_non_matching_posting_honestly(self):
        p = Prospect(company_name="HeyGen", website="https://heygen.com",
                     domain="heygen.com", confidence=0.6, apollo_id="org1")
        postings = {"status": "ok", "postings": [{"title": "Plumbing Design Engineer"}]}
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "job_postings", lambda oid: postings):
            verified = sources.verify_hiring([p], ["ai video creator"])
        self.assertEqual(verified, 0)
        self.assertEqual(p.hiring["match"], "any")
        self.assertIn("none matching", p.hiring["summary"])


class DiscoveryCardTests(unittest.TestCase):
    """Discovery results must render as cards without claiming to be researched."""

    def test_entries_carry_the_fields_the_card_shows(self):
        from chat import research_pipeline
        p = Prospect(company_name="HeyGen", website="https://heygen.com",
                     domain="heygen.com", confidence=0.71, tier="company",
                     match_reasons=["In Apollo's company database", "Headcount up 12%"],
                     hiring={"verified": True, "match": "role",
                             "summary": "Hiring: AI Video Creator"})
        p.add_source("apollo", "https://linkedin.com/company/heygen")
        p.add_source("exa", "https://heygen.com")
        entry = research_pipeline.discovery_entries([p.public()])[0]
        self.assertEqual(entry["company"], "HeyGen")
        self.assertEqual(entry["website"], "https://heygen.com")
        self.assertEqual(entry["status"], "discovered")   # NOT "ok"
        self.assertEqual(entry["score"], 71)
        self.assertEqual(entry["actions"], ["research_prospect"])
        self.assertEqual(entry["detail"]["hiring"]["match"], "role")
        self.assertEqual([s["domain"] for s in entry["detail"]["sources"]],
                         ["apollo", "exa"])
        self.assertIn("Hiring: AI Video Creator", entry["preview"])
        self.assertIsNone(entry["detail"]["what_they_do"])   # nothing researched


if __name__ == "__main__":
    unittest.main()
