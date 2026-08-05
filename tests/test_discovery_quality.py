"""Candidate-quality tests: aggregator classification, strict selection, Apollo.

These lock in the behaviour behind a real bug report. Asking for "B2B founders
hiring for an AI video creator role" returned mediabistro.com, jobs-radar.com and
lever.co above the actual AI-video companies, because the old pipeline searched
the web for the job posting and then REWARDED hiring vocabulary.

Nothing here touches the network: the Apollo provider is stubbed, so the tests
assert the orchestration and ranking rules rather than a live API.
"""

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import (aggregators, display_gate, engine, intent,   # noqa: E402
                       scoring, sources)
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


class ProductCategoryVersusVacancyTests(unittest.TestCase):
    """A role noun in the ask does not always mean a vacancy.

    "AI SDR startups" names companies that BUILD an AI SDR. It was read as a
    hiring ask because the whole-field fallback saw the role noun "sdr", so a
    live run returned Anthropic, OpenAI and Salesforce (giants hiring a sales
    rep) and ranked them above Decagon, which is what the user actually asked
    for. Only the head noun separates the two phrasings, so that is what the
    parser tests.
    """

    def test_a_product_category_is_not_a_hiring_ask(self):
        for text in ("AI SDR startups", "SDR software", "AI recruiting startups",
                     "sales development platforms", "AI sales assistant companies"):
            plan = intent.parse(text)
            self.assertEqual(plan.anchor, "category", text)
            self.assertEqual(plan.roles, [], text)

    def test_a_hiring_word_still_wins_over_the_category_head_noun(self):
        """The guard requires BOTH no hiring word AND a company head noun, so an
        ask that says "hiring" stays role-anchored whatever it ends with."""
        for text, expected in (("startups hiring SDRs", "sdr"),
                               ("companies hiring an SDR", "sdr"),
                               ("software companies hiring BDRs", "bdr")):
            plan = intent.parse(text)
            self.assertEqual(plan.anchor, "role", text)
            self.assertEqual(plan.roles, [expected], text)

    def test_a_bare_role_hint_still_anchors_on_the_role(self):
        """The fallback is what lets a bare keyword hint work; the guard must not
        take that away, because it has no company head noun."""
        self.assertEqual(intent.parse("sdr").roles, ["sdr"])
        self.assertEqual(intent.parse("find prospects", keywords=["sdr"]).roles, ["sdr"])


class CoversTheCategoryTests(unittest.TestCase):
    """A live "fintech founders" run filled its first page with Fintech News
    Malaysia, FinTech Futures, FinTech Global and Colombia Fintech, and a
    "cybersecurity startups" run with Cyber Security News and CISA. All real
    organisations, none of them a company you can sell fintech or security to:
    they matched the keyword because they WRITE ABOUT the category. Their domains
    look ordinary, so only Apollo's own industry codes separate them.

    The codes below are the real ones those organisations returned.
    """

    def test_publishers_bodies_and_government_are_identified(self):
        for sic, naics, expected in (
                (["2721", "7389", "7311"], ["513120", "561920"], "publisher"),
                ([], ["519290", "513120", "561920"], "publisher"),
                (["7389"], ["54151", "813910", "561920"], "association"),
                (["9199"], ["54169", "928110", "922190"], "government")):
            self.assertEqual(intent.covers_category_kind(sic, naics), expected,
                             f"{sic} {naics}")

    def test_software_publishers_are_never_confused_with_periodicals(self):
        """NAICS 513210 is Software Publishers and 513120 is Periodical
        Publishers. A three-digit prefix would have demoted Apple and Microsoft."""
        for sic, naics in ((["3571", "7372"], ["334111", "513210"]),      # Apple
                           (["7372", "7373"], ["54151", "51321"]),        # Microsoft
                           (["7375"], ["541512", "561110"])):             # Wipro
            self.assertEqual(intent.covers_category_kind(sic, naics), "")

    def test_a_category_search_demotes_them_below_real_companies(self):
        orgs = [{"name": "FinTech Futures", "domain": "fintechfutures.com",
                 "sic_codes": ["7375"], "naics_codes": ["513120"]},
                {"name": "Acme Payments", "domain": "acmepay.com",
                 "sic_codes": ["7372"], "naics_codes": ["513210"]}]
        q = DiscoveryQuery(raw="fintech founders", keywords=["fintech"], limit=10)
        stats = {}
        # A NARROW match set, so this exercises the demotion rather than the
        # separate breadth gate that discards a six-figure category match whole.
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "search_organizations",
                               lambda **kw: {"status": "ok", "organizations": orgs,
                                             "total": 820}):
            found = sources.search_apollo(q, keywords=["fintech"], job_titles=[],
                                          stats=stats)
        by_domain = {p.domain: p for p in found}
        self.assertEqual(by_domain["fintechfutures.com"].tier, "fallback")
        self.assertEqual(by_domain["acmepay.com"].tier, "company")
        self.assertEqual(stats["covers_demoted"], {"publisher": 1})

    def test_a_web_hit_cannot_promote_a_demoted_publisher_back(self):
        """The demotion is backed by an industry code; a second source that simply
        does not recognise the domain is weaker evidence. Without this guard the
        merge promoted FinTech Futures straight back to the top of the page after
        Apollo had correctly demoted it."""
        publisher = Prospect(company_name="FinTech Futures",
                             website="https://fintechfutures.com",
                             domain="fintechfutures.com", confidence=0.5,
                             discovery_source="apollo", tier="fallback",
                             kind=aggregators.MEDIA)
        publisher.add_source("apollo", "")
        web_copy = Prospect(company_name="FinTech Futures",
                            website="https://fintechfutures.com",
                            domain="fintechfutures.com", confidence=0.5,
                            discovery_source="exa", tier="company",
                            kind=aggregators.COMPANY)
        web_copy.add_source("exa", "")

        merged = {publisher.domain: publisher}
        engine._merge(merged, [web_copy], DiscoveryQuery(raw="fintech", limit=10), set())
        self.assertEqual(merged["fintechfutures.com"].tier, "fallback")
        self.assertEqual(merged["fintechfutures.com"].kind, aggregators.MEDIA)

    def test_a_web_hit_can_still_promote_an_ordinary_demotion(self):
        """The promotion path must survive for the case it exists for: an
        employer's own careers page misread as an ATS."""
        demoted = Prospect(company_name="Acme", website="https://acme.com",
                           domain="acme.com", confidence=0.5,
                           discovery_source="exa", tier="fallback",
                           kind=aggregators.ATS)
        demoted.add_source("exa", "")
        apollo_copy = Prospect(company_name="Acme", website="https://acme.com",
                               domain="acme.com", confidence=0.5,
                               discovery_source="apollo", tier="company",
                               kind=aggregators.COMPANY)
        apollo_copy.add_source("apollo", "")

        merged = {demoted.domain: demoted}
        engine._merge(merged, [apollo_copy], DiscoveryQuery(raw="x", limit=10), set())
        self.assertEqual(merged["acme.com"].tier, "company")

    def test_a_role_search_keeps_them(self):
        """A trade publisher with a live SDR posting really is hiring one, so the
        demotion must not apply when the match came from a job title."""
        orgs = [{"name": "FinTech Futures", "domain": "fintechfutures.com",
                 "sic_codes": ["7375"], "naics_codes": ["513120"]}]
        q = DiscoveryQuery(raw="companies hiring an SDR", limit=10)
        with mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "search_organizations",
                               lambda **kw: {"status": "ok", "organizations": orgs,
                                             "total": 900}):
            found = sources.search_apollo(q, keywords=[], job_titles=["sdr"])
        self.assertEqual(found[0].tier, "company")


class CategoryBreadthTests(unittest.TestCase):
    """Apollo orders a broad keyword match by company size, so the match count
    itself says whether the tag narrowed anything.

    Measured live: "ai sdr" matches 147 companies and returns SuperAGI, Klenty
    and Clara, exactly the ask. "healthcare" matches 1,005,621 and returns
    Amazon, Google, Nestlé and Microsoft. So a specific tag is worth searching
    and a broad one is worth discarding, and one number separates them.
    """

    @staticmethod
    def _apollo(total, orgs):
        return mock.patch.multiple(
            apollo_orgs, available=lambda: True,
            search_organizations=lambda **kw: {"status": "ok", "total": total,
                                               "organizations": orgs})

    def test_a_specific_category_search_is_kept(self):
        orgs = [{"name": "SuperAGI", "domain": "superagi.com",
                 "sic_codes": ["7372"], "naics_codes": ["513210"]}]
        q = DiscoveryQuery(raw="AI SDR startups", limit=10)
        stats = {}
        with self._apollo(147, orgs):
            found = sources.search_apollo(q, keywords=["ai sdr"], job_titles=[],
                                          stats=stats)
        self.assertEqual([p.domain for p in found], ["superagi.com"])
        self.assertNotIn("too_broad", stats)

    def test_a_match_set_too_broad_to_narrow_is_discarded(self):
        orgs = [{"name": "Amazon", "domain": "amazon.com",
                 "sic_codes": ["5961"], "naics_codes": ["454110"]}]
        q = DiscoveryQuery(raw="healthcare SaaS", limit=10)
        stats = {}
        with self._apollo(1_005_621, orgs):
            found = sources.search_apollo(q, keywords=["healthcare"], job_titles=[],
                                          stats=stats)
        self.assertEqual(found, [], "a million-company match is not a shortlist")
        # Still reported honestly rather than looking like an outage.
        self.assertEqual(stats["state"], "ok")
        self.assertEqual(stats["total"], 1_005_621)
        self.assertTrue(stats["too_broad"])

    def test_a_role_search_is_never_discarded_for_breadth(self):
        """A live job posting is a real signal however many companies share it."""
        orgs = [{"name": "Acme", "domain": "acme.com",
                 "sic_codes": ["7372"], "naics_codes": ["513210"]}]
        q = DiscoveryQuery(raw="companies hiring an SDR", limit=10)
        with self._apollo(1_005_621, orgs):
            found = sources.search_apollo(q, keywords=[], job_titles=["sdr"])
        self.assertEqual([p.domain for p in found], ["acme.com"])

    def test_a_specific_ask_with_no_named_vertical_still_reaches_apollo(self):
        """The regression: "AI SDR startups" names no vertical the parser knows,
        so plan.keyword_tags was empty and Apollo was skipped entirely."""
        plan = intent.parse("AI SDR startups")
        self.assertEqual(plan.keyword_tags, [])
        q = DiscoveryQuery(raw="AI SDR startups", limit=10)
        first = engine._pass_plans(q, plan)[0]
        self.assertTrue(first["apollo"])
        self.assertIn("ai sdr", first["apollo_tags"])


class RoleVocabularyTests(unittest.TestCase):
    """The two role-noun vocabularies must not drift apart.

    sources._ROLE_NOUN_RE strips role nouns out of Apollo category tags, and
    intent._ROLE_NOUNS decides what counts as a role at all. "executive" was in
    the first and missing from the second, so "Series A startups hiring account
    executives" lost the role, searched Apollo for the nonsense category "series
    account", and verified no job postings whatsoever.
    """

    def test_intent_recognises_every_noun_sources_strips(self):
        stripped = re.findall(r"([a-z]+)s\?", sources._ROLE_NOUN_RE.pattern)
        missing = [w for w in stripped if not intent._has_role_noun(w)]
        self.assertEqual(missing, [],
                         "sources strips these as role nouns but intent does not "
                         "recognise them as roles")

    def test_the_common_b2b_titles_are_recognised(self):
        for text, expected in (
                ("companies hiring an account executive", "account executive"),
                ("companies hiring a recruiter", "recruiter"),
                ("companies hiring a controller", "controller"),
                ("companies hiring a chief revenue officer", "chief revenue officer")):
            plan = intent.parse(text)
            self.assertEqual(plan.anchor, "role", text)
            self.assertEqual(plan.roles, [expected], text)


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

    def test_hiring_a_different_role_is_not_a_positive_signal(self):
        """#6: when a specific role was asked for, "hiring, but not that role" must
        contribute nothing to hiring-relevance. Presenting a company hiring content
        creators as a match for an SDR search is the exact mislabelling to avoid."""
        plan = intent.parse("b2b founders hiring an sdr")
        self.assertTrue(plan.roles)
        p_any = Prospect(company_name="A", website="https://a.com", domain="a.com",
                         hiring={"verified": True, "match": "any", "summary": "3 roles"})
        p_role = Prospect(company_name="B", website="https://b.com", domain="b.com",
                          hiring={"verified": True, "match": "role", "summary": "SDR"})
        self.assertEqual(scoring._hiring(p_any, plan)[0], 0.0)   # not that role -> 0
        self.assertEqual(scoring._hiring(p_role, plan)[0], 1.0)  # the role -> full

    def test_hiring_any_still_mildly_relevant_when_no_role_was_asked(self):
        # No specific role in the ask -> any active hiring keeps its old mild credit.
        plan = intent.parse("b2b fintech companies in europe")
        self.assertFalse(plan.roles)
        p_any = Prospect(company_name="A", website="https://a.com", domain="a.com",
                         hiring={"verified": True, "match": "any", "summary": "roles"})
        self.assertEqual(scoring._hiring(p_any, plan)[0], 0.35)

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


class PublicationAndDirectoryRejectionTests(unittest.TestCase):
    """A live 'B2B fintech startups that raised seed' run (2026-08-04) returned
    publications and a directory as top 'prospects': natlawreview.com (a legal-news
    publisher), thisweekinfintech.com (a newsletter), siliconsnark.com (a blog) and
    vcbacked.co (a directory of VC-backed startups). A valid domain is not proof of
    a company; these are content ABOUT the market, never buyers, and must drop."""

    Q = DiscoveryQuery(raw="B2B fintech startups that raised a seed round",
                       keywords=["fintech", "b2b"])

    def test_the_exact_production_domains_are_classified_non_company(self):
        for domain in ("natlawreview.com", "thisweekinfintech.com",
                       "siliconsnark.com"):
            self.assertEqual(aggregators.domain_kind(domain), aggregators.MEDIA, domain)
        self.assertEqual(aggregators.domain_kind("vcbacked.co"),
                         aggregators.LIST_SITE)

    def test_the_exact_production_domains_are_dropped_by_assess(self):
        for url, title in (
                ("https://natlawreview.com/article/fintech-law",
                 "The National Law Review"),
                ("https://thisweekinfintech.com", "This Week in Fintech"),
                ("https://siliconsnark.com", "Silicon Snark"),
                ("https://vcbacked.co", "VC Backed Startups Directory")):
            kind, drop = sources.assess({"url": url, "title": title,
                                         "content": "fintech news and analysis"}, self.Q)
            self.assertEqual(kind, "", f"{url} should be dropped")
            self.assertTrue(drop, url)

    def test_a_newsletter_title_on_an_unknown_domain_is_media(self):
        # Title-anchored, so a publication on a domain the curated list has never
        # seen is still caught (that is how the curated list stays finite).
        for title in ("This Week in Payments", "The Fintech Newsletter",
                      "Payments Weekly", "The Lending Digest", "Neobank Monthly",
                      "Fintech Brew — Issue #142"):
            self.assertEqual(
                aggregators.page_kind("https://unknown-domain-xyz.com", title, ""),
                aggregators.MEDIA, title)

    def test_an_article_headline_title_is_media(self):
        for title in ("The B2B Fintech Pricing Journey",
                      "The Complete Guide to Embedded Finance",
                      "A Founder's Playbook for Seed Fundraising",
                      "The State of Fintech in 2026"):
            self.assertEqual(
                aggregators.page_kind("https://someblog.example", title, ""),
                aggregators.MEDIA, title)

    def test_real_companies_are_not_mistaken_for_publications(self):
        # The false-positive guard. Real fintech/dev companies whose names brush the
        # publication vocabulary (Postman, Timescale, Journey, Ramp, Brex, Mercury)
        # must stay COMPANY: the domain has no publication token, and their home-page
        # titles are brand+tagline, not a newsletter idiom or an article headline.
        for domain in ("postman.com", "timescale.com", "journey.io", "ramp.com",
                       "brex.com", "mercury.com", "stripe.com", "reviewflow.io"):
            self.assertEqual(aggregators.domain_kind(domain),
                             aggregators.COMPANY, domain)
        for title, domain in (
                ("Ramp — Corporate cards and spend management", "ramp.com"),
                ("Postman | The API Platform", "postman.com"),
                ("Timescale: Postgres for time-series and events", "timescale.com"),
                ("Journey - Sales enablement software", "journey.io"),
                ("Mercury - Banking for startups", "mercury.com")):
            kind, drop = sources.assess(
                {"url": f"https://{domain}", "title": title,
                 "content": "Pricing, product and customers. Book a demo."}, self.Q)
            self.assertNotEqual(kind, "", f"{title} must not be dropped")
            self.assertEqual(kind, aggregators.COMPANY, title)


class MediaPlatformRejectionTests(unittest.TestCase):
    """A live 'B2B fintech startups' run (2026-08-05) returned viestories.com as a
    top 'prospect'. VIStories explicitly describes itself as a business media
    platform publishing startup news, stories, articles, magazines and
    newsletters — it covers the startup market, it is not a fintech buyer. A valid
    domain is not proof of a company; a self-described publisher must drop."""

    Q = DiscoveryQuery(raw="B2B fintech startups that raised a seed round",
                       keywords=["fintech", "b2b"])

    # The wording the prompt calls out, as it reads on VIStories' own About page.
    ABOUT = ("VIStories is a business media platform publishing the latest "
             "startup news, stories, articles, magazines and our newsletter for "
             "founders and the startup ecosystem.")

    def test_viestories_domain_is_classified_media(self):
        self.assertEqual(aggregators.domain_kind("viestories.com"),
                         aggregators.MEDIA)

    def test_viestories_is_excluded_from_prospect_results(self):
        kind, drop = sources.assess(
            {"url": "https://viestories.com", "title": "VIStories",
             "content": self.ABOUT}, self.Q)
        self.assertEqual(kind, "", "viestories.com must be dropped")
        self.assertTrue(drop)
        # And it never survives into a built Prospect.
        self.assertIsNone(sources._build(
            {"url": "https://viestories.com", "title": "VIStories",
             "content": self.ABOUT}, self.Q, "exa"))

    def test_about_wording_is_treated_as_publication_evidence(self):
        # The exact phrases the prompt names, judged as publication evidence even
        # on a domain the curated set has never seen (that is how the list stays
        # finite): "business media platform", plus publishing of startup news,
        # articles, magazines and a newsletter.
        self.assertTrue(aggregators.is_media_content("VIStories", self.ABOUT))
        self.assertEqual(
            aggregators.page_kind("https://unseen-media-xyz.com", "VIStories",
                                  self.ABOUT),
            aggregators.MEDIA)
        # An unseen media platform is dropped by assess on content alone.
        kind, drop = sources.assess(
            {"url": "https://unseen-media-xyz.com", "title": "VIStories",
             "content": self.ABOUT}, self.Q)
        self.assertEqual(kind, "")
        self.assertTrue(drop)

    def test_each_named_publication_signal_counts(self):
        # The wordings the prompt names are publication evidence in context. A
        # strong identity phrase qualifies alone; ambiguous phrasing plus content
        # types, or a spread of three-plus publisher content types, also qualifies.
        for phrase in (
                "a business media platform for founders",           # strong identity
                "an online magazine for the startup ecosystem",     # strong identity
                "a digital media platform publishing startup news and interviews",
                "read our latest startup news, stories, articles, "
                "magazines and newsletter"):                        # content spread
            self.assertTrue(aggregators.is_media_content("", phrase), phrase)

    def test_real_companies_are_not_mistaken_for_media_platforms(self):
        # The false-positive guard the prompt demands: Postman, Timescale and
        # Journey — and a social-media MARTECH product, the nearest miss — must
        # stay COMPANY. None self-identify as a publisher.
        safe = (
            ("Postman", "Postman is the API platform for building and using APIs."),
            ("Timescale", "Timescale is Postgres for time-series, events and analytics."),
            ("Journey", "Journey is sales enablement software with pricing and customers."),
            ("Buffer", "Buffer is a social media platform to schedule and publish posts."),
            ("Contentful", "A content platform and CMS; publish content to any channel."),
        )
        for title, content in safe:
            self.assertFalse(aggregators.is_media_content(title, content), title)
            kind, drop = sources.assess(
                {"url": f"https://{title.lower()}.com", "title": title,
                 "content": content + " Pricing, product, customers, book a demo."},
                self.Q)
            self.assertEqual(kind, aggregators.COMPANY, title)


class AcceptanceGateTests(unittest.TestCase):
    """A valid domain alone is not a company (prompt requirement #4): a displayed
    prospect needs at least two independent company signals. The bar catches the
    empty/parked case without rejecting a real but tersely-described company."""

    Q = DiscoveryQuery(raw="B2B fintech startups", keywords=["fintech"])

    def test_bare_domain_with_one_signal_is_rejected(self):
        # peopletech.cloud reached the list on its domain alone. With only its
        # name matching the domain and no product / about / proof / funding /
        # industry-match evidence, the final bar rejects it. ("compliance" clears
        # the earlier company-likeness check without adding a strong signal.)
        reason = sources._reject_reason(
            {"url": "https://peopletech.cloud", "title": "PeopleTech",
             "content": "b2b compliance."}, self.Q)
        self.assertEqual(reason, "insufficient_company_signals")

    def test_parked_or_empty_page_is_rejected(self):
        reason = sources._reject_reason(
            {"url": "https://parked-xyz.cloud", "title": "parked-xyz.cloud",
             "content": "This domain is for sale."}, self.Q)
        self.assertIsNotNone(reason)

    def test_two_signals_is_enough_to_be_accepted(self):
        # Product offering + pricing/customers proof: two independent signals.
        self.assertIsNone(sources._reject_reason(
            {"url": "https://acme.io", "title": "Acme",
             "content": "Acme is a software platform. See our pricing and customers."},
            self.Q))

    def test_real_but_terse_company_still_clears_the_bar(self):
        # The guard against over-rejection: a sparsely-described fintech company
        # with a name that matches its domain plus a funding signal still passes.
        self.assertIsNone(sources._reject_reason(
            {"url": "https://acmefintech.com", "title": "Acme Fintech",
             "content": "fintech, raised a seed round, hiring."}, self.Q))


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


class FinalProspectQualityGateTests(unittest.TestCase):
    """The launch-blocking prospect-quality pass (prompt.fix.txt, 2026-08-05).

    Each failure FAMILY, exercised on the exact domain the live smoke test leaked
    AND on a synthetic domain, so the fix is the generic detector, not a blocklist
    entry. False-positive guards keep real companies, requested agencies, media
    SOFTWARE, Postman, Timescale and Journey.
    """

    SALES = DiscoveryQuery(raw="SaaS companies hiring SDRs", keywords=["saas", "sdr"])
    SEED = DiscoveryQuery(raw="B2B fintech startups that raised a seed round",
                          keywords=["fintech", "b2b"])

    def _drop(self, raw, q):
        kind, drop = sources.assess(raw, q)
        return kind == "" and bool(drop), drop

    # ── Family 1: content / article / informational sites ──────────────────
    def test_soc2compliancecost_content_site_is_dropped(self):
        dropped, why = self._drop(
            {"url": "https://soc2compliancecost.com",
             "title": "How Much Does SOC 2 Compliance Cost?",
             "content": "In this guide we break down the cost of SOC 2 compliance "
                        "for startups. Learn everything you need to know."}, self.SALES)
        self.assertTrue(dropped, why)

    def test_a_generic_informational_article_domain_is_dropped(self):
        # No curated entry: the informational-content detector carries it.
        self.assertTrue(aggregators.is_informational_content(
            "How Much Does It Cost to Build an MVP?",
            "In this article we explain the average cost. Learn everything you "
            "need to know before you start."))
        dropped, why = self._drop(
            {"url": "https://howmuchtobuildanmvp.io",
             "title": "How Much Does It Cost to Build an MVP?",
             "content": "In this article we break down the cost. Learn what to "
                        "budget."}, self.SALES)
        self.assertTrue(dropped, why)

    # ── Family 2: directories, review platforms, jobs/news hubs ────────────
    def test_repvue_review_platform_is_dropped(self):
        self.assertEqual(aggregators.domain_kind("repvue.com"), aggregators.DIRECTORY)
        dropped, why = self._drop(
            {"url": "https://repvue.com", "title": "RepVue",
             "content": "RepVue is the largest sales org ratings platform. Browse "
                        "company ratings and reviews from verified reps."}, self.SALES)
        self.assertTrue(dropped, why)

    def test_a_generic_review_platform_is_directory_by_page(self):
        self.assertTrue(aggregators.is_review_platform(
            "SalesReviewz", "Rate and review companies. Browse verified employee "
            "reviews of employers."))
        self.assertEqual(aggregators.page_kind(
            "https://unseen-reviews-xyz.io", "SalesReviewz",
            "Rate and review companies. Read company ratings."), aggregators.DIRECTORY)

    def test_3xfintech_ecosystem_hub_is_dropped(self):
        self.assertEqual(aggregators.domain_kind("3xfintech.com"),
                         aggregators.DIRECTORY)
        dropped, why = self._drop(
            {"url": "https://3xfintech.com", "title": "3xFintech",
             "content": "Browse jobs, companies, funding rounds, reports and news "
                        "across the fintech ecosystem."}, self.SEED)
        self.assertTrue(dropped, why)

    def test_a_generic_market_hub_is_directory_by_page(self):
        self.assertTrue(aggregators.is_directory_hub(
            "Fintech Hub", "Discover jobs, companies, investors, deals and news in "
            "one place."))
        self.assertEqual(aggregators.page_kind(
            "https://unseen-hub-xyz.io", "Fintech Hub",
            "Discover jobs, companies, investors, deals and news."),
            aggregators.DIRECTORY)

    # ── Family 3: agencies / staffing incompatible with the ask ────────────
    def test_salessourcers_staffing_firm_is_dropped_for_a_hiring_ask(self):
        self.assertTrue(aggregators.sells_staffing(
            "SaleSourcers", "We book meetings through our experienced SDRs. "
            "Outsourced SDR team for B2B."))
        dropped, why = self._drop(
            {"url": "https://salessourcers.com", "title": "SaleSourcers",
             "content": "We book meetings through our experienced SDRs. Outsourced "
                        "SDR team for B2B SaaS."}, self.SALES)
        self.assertTrue(dropped, why)

    def test_caugia_agency_is_dropped_when_a_product_company_is_asked(self):
        for q in (self.SALES, self.SEED):
            dropped, why = self._drop(
                {"url": "https://caugia.com", "title": "Caugia",
                 "content": "A software development agency and consulting firm "
                            "building custom software for clients."}, q)
            self.assertTrue(dropped, f"{why} (q={q.raw})")

    def test_an_agency_is_kept_when_the_user_asked_for_agencies(self):
        q = DiscoveryQuery(raw="software development agencies", keywords=["agency"])
        kind, drop = sources.assess(
            {"url": "https://caugia.com", "title": "Caugia",
             "content": "A software development agency building custom software. "
                        "Pricing, portfolio and customers."}, q)
        self.assertEqual(kind, aggregators.COMPANY)
        self.assertFalse(drop)

    # ── Family 4: keyword ambiguity (SDR = software-defined radio) ──────────
    def test_rtl_sdr_radio_site_is_dropped_for_a_sales_search(self):
        dropped, why = self._drop(
            {"url": "https://rtl-sdr.com", "title": "RTL-SDR",
             "content": "RTL-SDR software defined radio. A cheap dongle receiver "
                        "for ham radio and frequency scanning. MHz antenna."},
            self.SALES)
        self.assertTrue(dropped, why)

    def test_a_real_sales_company_mentioning_frequency_is_kept(self):
        # The false-positive guard: sales vocabulary present => not the radio sense.
        kind, drop = sources.assess(
            {"url": "https://outreachco.com", "title": "OutreachCo",
             "content": "Sales development platform. Our SDRs and AEs build "
                        "pipeline with outbound. Pricing and customers."}, self.SALES)
        self.assertEqual(kind, aggregators.COMPANY)
        self.assertFalse(drop)

    # ── Family 5: empty / unverifiable domains ─────────────────────────────
    def test_cuota_empty_domain_is_rejected(self):
        self.assertIsNotNone(sources._reject_reason(
            {"url": "https://cuota.io", "title": "Cuota", "content": ""}, self.SALES))

    # ── Family 6: incorrect prospect names ─────────────────────────────────
    def test_victorfi_is_named_from_the_domain_not_a_truncated_headline(self):
        self.assertEqual(
            sources._name_from("Victor - Modern finance for founders",
                               "victorfi.com"), "Victorfi")

    def test_nooks_is_named_from_the_domain_not_a_job_headline(self):
        self.assertEqual(
            sources._name_from("Looking for your next SDR role?", "nooks.ai"),
            "Nooks")

    def test_a_domain_wrapper_prefix_still_yields_the_brand(self):
        # "Mercury" on getmercury.com is the brand, not a truncation.
        self.assertEqual(
            sources._name_from("Mercury - Banking for startups", "getmercury.com"),
            "Mercury")

    def test_a_landing_page_section_is_trimmed_from_the_name(self):
        # A live run showed "Zenskar Pricing Plans"; the brand is "Zenskar".
        self.assertEqual(sources._name_from("Zenskar Pricing Plans", "zenskar.com"),
                         "Zenskar")
        self.assertEqual(sources._name_from("Acme Reviews", "acme.com"), "Acme")

    def test_a_colon_tagline_is_cut_from_the_name(self):
        # A live run showed "Adyen: Fintech platform for enterprises".
        self.assertEqual(
            sources._name_from("Adyen: Fintech platform for enterprises",
                               "adyen.com"), "Adyen")

    def test_the_named_agency_and_staffing_domains_are_rejected_even_when_thin(self):
        # Their live snippets were near-empty, so the content detectors could not
        # classify them; the curated backstop rejects the exact named domains.
        for domain in ("caugia.com", "salessourcers.com"):
            dropped, why = self._drop(
                {"url": f"https://{domain}", "title": domain.split(".")[0],
                 "content": "fintech b2b"}, self.SEED)
            self.assertTrue(dropped, f"{domain}: {why}")

    # ── False-positive guards the prompt demands ───────────────────────────
    def test_postman_timescale_journey_stay_companies(self):
        for title, domain, content in (
                ("Postman | The API Platform", "postman.com",
                 "Pricing, product, customers. Book a demo."),
                ("Timescale: Postgres for time-series", "timescale.com",
                 "Pricing, docs and customers for developers."),
                ("Journey - Sales enablement software", "journey.io",
                 "Sales enablement software with pricing and customers.")):
            kind, drop = sources.assess(
                {"url": f"https://{domain}", "title": title, "content": content},
                self.SEED)
            self.assertEqual(kind, aggregators.COMPANY, title)
            self.assertFalse(drop, title)

    def test_a_media_software_product_is_not_mistaken_for_a_publisher(self):
        # "media software" / a social-media product must stay a company.
        for title, domain, content in (
                ("Buffer", "buffer.com",
                 "A social media platform to schedule and publish posts. Pricing "
                 "and customers."),
                ("Brightcove", "brightcove.com",
                 "Video hosting and streaming software for enterprises. Pricing, "
                 "product, customers.")):
            kind, drop = sources.assess(
                {"url": f"https://{domain}", "title": title, "content": content},
                self.SALES)
            self.assertEqual(kind, aggregators.COMPANY, title)
            self.assertFalse(drop, title)

    def test_a_product_that_books_meetings_is_not_mistaken_for_staffing(self):
        # Nooks sells a tool to book YOUR OWN meetings; it is not an SDR agency.
        self.assertFalse(aggregators.sells_staffing(
            "Nooks", "Book more meetings with our AI powered parallel dialer."))
        kind, drop = sources.assess(
            {"url": "https://nooks.ai", "title": "Nooks - AI Sales Assistant",
             "content": "Book more meetings with our AI dialer. Pricing, product, "
                        "customers."}, self.SALES)
        self.assertEqual(kind, aggregators.COMPANY)
        self.assertFalse(drop)


class DisplayGateTests(unittest.TestCase):
    """The consolidated final display gate: every displayed row must clear it."""

    SEED = DiscoveryQuery(raw="B2B fintech startups that raised a seed round",
                          keywords=["fintech", "b2b"])
    HIRING = DiscoveryQuery(raw="SaaS companies hiring SDRs", keywords=["saas", "sdr"])

    def _plan(self, q):
        return intent.parse(q.raw, keywords=q.keywords)

    def test_a_real_apollo_company_passes(self):
        p = Prospect(company_name="Zenskar", website="https://zenskar.com",
                     domain="zenskar.com", industry_kind="software",
                     discovery_source="apollo", apollo_id="a1", tier="company",
                     match_reasons=["In Apollo's company database"])
        ok, reason = display_gate.evaluate(p, self._plan(self.SEED))
        self.assertTrue(ok, reason)

    def test_a_pure_category_name_is_never_displayed(self):
        p = Prospect(company_name="Fintech", website="https://fintech.com",
                     domain="fintech.com", tier="company", discovery_source="exa",
                     match_reasons=["Reads like a software product"])
        ok, reason = display_gate.evaluate(p, self._plan(self.SEED))
        self.assertFalse(ok)
        self.assertEqual(reason, "generic_category_name")

    def test_a_demoted_intermediary_is_never_displayed(self):
        p = Prospect(company_name="Some Board", website="https://board.com",
                     domain="board.com", tier="fallback", kind=aggregators.JOB_BOARD)
        ok, reason = display_gate.evaluate(p, self._plan(self.HIRING))
        self.assertFalse(ok)
        self.assertEqual(reason, "not_an_operating_company")

    def test_a_media_kind_is_never_displayed(self):
        p = Prospect(company_name="VIStories", website="https://viestories.com",
                     domain="viestories.com", tier="company", kind=aggregators.MEDIA)
        ok, reason = display_gate.evaluate(p, self._plan(self.SEED))
        self.assertFalse(ok)
        self.assertEqual(reason, "not_an_operating_company")

    def test_a_headline_name_unbacked_by_the_domain_is_dropped(self):
        p = Prospect(company_name="Looking for your next SDR role?",
                     website="https://nooks.ai", domain="nooks.ai", tier="company",
                     discovery_source="exa",
                     match_reasons=["Reads like a software product"])
        ok, reason = display_gate.evaluate(p, self._plan(self.HIRING))
        self.assertFalse(ok)
        self.assertEqual(reason, "company_name_unverified")

    def test_a_services_shop_is_the_wrong_entity_type_for_a_product_ask(self):
        p = Prospect(company_name="Caugia", website="https://caugia.com",
                     domain="caugia.com", industry_kind="services", tier="company",
                     discovery_source="apollo", apollo_id="a2")
        ok, reason = display_gate.evaluate(p, self._plan(self.SEED))
        self.assertFalse(ok)
        self.assertEqual(reason, "entity_type_mismatch")

    def test_a_hiring_ask_requires_official_hiring_evidence_from_web_rows(self):
        # A web row with no own-careers evidence is not accepted for a hiring ask.
        p = Prospect(company_name="Acme", website="https://acme.com",
                     domain="acme.com", tier="company", discovery_source="exa",
                     match_reasons=["Software platform, pricing, customers"])
        ok, reason = display_gate.evaluate(p, self._plan(self.HIRING))
        self.assertFalse(ok)
        self.assertEqual(reason, "no_official_hiring_evidence")

    def test_own_careers_evidence_satisfies_the_hiring_requirement(self):
        p = Prospect(company_name="Acme", website="https://acme.com",
                     domain="acme.com", tier="company", discovery_source="exa",
                     match_reasons=["Software platform, pricing, customers"],
                     hiring={"verified": True, "source": "own_careers_page",
                             "match": "role", "summary": "Hiring: SDR"})
        ok, reason = display_gate.evaluate(p, self._plan(self.HIRING))
        self.assertTrue(ok, reason)

    def test_apollo_rows_satisfy_the_hiring_requirement_structurally(self):
        p = Prospect(company_name="Clay", website="https://clay.com",
                     domain="clay.com", industry_kind="software", tier="company",
                     discovery_source="apollo", apollo_id="a3",
                     hiring={"verified": False, "source": "apollo_title_filter",
                             "match": "any"})
        ok, reason = display_gate.evaluate(p, self._plan(self.HIRING))
        self.assertTrue(ok, reason)


class HardConstraintVerificationTests(unittest.TestCase):
    """The constraint-verification layer (prompt.fix.txt, 2026-08-05, iteration 2).

    A search satisfied the ENTITY (a real fintech company) while violating a HARD
    CONSTRAINT of the request (seed stage): Adyen and Airwallex are real fintechs
    but not seed-stage startups. A prospect is QUALIFIED only when every hard
    constraint has individually verified evidence — for "raised a seed round", an
    actual seed/pre-seed round with a SOURCE URL, and a current stage no later than
    seed. Unverifiable => excluded, never padded.
    """

    from discovery import constraints as _C

    SEED = DiscoveryQuery(raw="B2B fintech startups that raised a seed round",
                          keywords=["fintech", "b2b"])
    HIRING = DiscoveryQuery(raw="SaaS companies hiring SDRs", keywords=["saas", "sdr"])

    # Canned Apollo enrichment shapes, mirroring the LIVE records observed.
    ENRICH = {
        "adyen.com": {"funding": {"latest_stage": "", "events": []}},
        "airwallex.com": {"funding": {"latest_stage": "Series H", "events": [
            {"type": "Seed", "date": "2016-06-01", "amount": "3M", "currency": "$",
             "news_url": "https://techcrunch.com/2016/07/08/airwallex", "investors": "X"}]}},
        "zenskar.com": {"funding": {"latest_stage": "Series A", "events": [
            {"type": "Seed", "date": "2022-10-01", "amount": "6.5M", "currency": "$",
             "news_url": "https://zenskar.com/blog/seed", "investors": "Bessemer"}]}},
        "getfwd.com": {"funding": {"latest_stage": "Seed", "events": [
            {"type": "Seed", "date": "2024-05-01", "amount": "16M", "currency": "$",
             "news_url": "https://techcrunch.com/2024/05/30/forward-16m",
             "investors": "Fiserv, Commerce Ventures"}]}},
        "nourl.com": {"funding": {"latest_stage": "Seed", "events": [
            {"type": "Seed", "date": "2021-12-01", "amount": "6M", "news_url": ""}]}},
    }

    def _plan(self, q):
        return intent.parse(q.raw, keywords=q.keywords)

    def _prospect(self, name, domain, **kw):
        return Prospect(company_name=name, website=f"https://{domain}", domain=domain,
                        industry="fintech", discovery_source="exa", tier="company",
                        **kw)

    def _verify_funding(self, prospects):
        def enrich(domain):
            rec = self.ENRICH.get(domain)
            return {"status": "ok", **rec} if rec else {"status": "empty", "funding": {}}
        with mock.patch.object(sources, "APOLLO_ORG_SEARCH_ENABLED", True), \
             mock.patch.object(apollo_orgs, "available", lambda: True), \
             mock.patch.object(apollo_orgs, "enrich", enrich):
            return sources.verify_funding(prospects, "seed", limit=10)

    # ── pure stage matching ────────────────────────────────────────────────
    def test_stage_matching_admits_seed_and_excludes_late_or_uncited(self):
        C = self._C
        self.assertTrue(C.stage_satisfied_by(
            "Seed", self.ENRICH["getfwd.com"]["funding"]["events"], "seed"))
        self.assertFalse(C.stage_satisfied_by("Series A",
            self.ENRICH["zenskar.com"]["funding"]["events"], "seed"))
        self.assertFalse(C.stage_satisfied_by("Series H",
            self.ENRICH["airwallex.com"]["funding"]["events"], "seed"))
        self.assertFalse(C.stage_satisfied_by("", [], "seed"))
        self.assertFalse(C.stage_satisfied_by(  # a seed round but no source URL
            "Seed", self.ENRICH["nourl.com"]["funding"]["events"], "seed"))

    def test_parse_reads_funding_stage_only_when_stated(self):
        self.assertEqual(self._C.parse(self._plan(self.SEED)).funding_stage, "seed")
        self.assertEqual(self._C.parse(
            intent.parse("B2B fintech companies", keywords=["fintech"])).funding_stage, "")

    # ── the exact excluded companies ───────────────────────────────────────
    def test_adyen_is_excluded_from_a_seed_stage_query(self):
        p = self._prospect("Adyen", "adyen.com")
        self._verify_funding([p])
        self.assertFalse((p.funding or {}).get("verified"))
        ok, unmet = self._C.verify(p, self._C.parse(self._plan(self.SEED)))
        self.assertFalse(ok)
        self.assertIn("funding_stage", unmet)

    def test_airwallex_is_excluded_from_a_seed_stage_query(self):
        # Airwallex HAS a seed event with a URL, but its CURRENT stage is Series H.
        p = self._prospect("Airwallex", "airwallex.com")
        self._verify_funding([p])
        self.assertFalse((p.funding or {}).get("verified"))
        ok, unmet = self._C.verify(p, self._C.parse(self._plan(self.SEED)))
        self.assertFalse(ok)

    # ── the accepted company, with evidence ────────────────────────────────
    def test_a_verified_seed_fintech_is_accepted_with_evidence(self):
        p = self._prospect("Getfwd", "getfwd.com")
        n = self._verify_funding([p])
        self.assertEqual(n, 1)
        self.assertTrue(p.funding["verified"])
        self.assertEqual(p.funding["round_type"], "Seed")
        self.assertEqual(p.funding["amount"], "$16M")
        self.assertTrue(p.funding["source_url"].startswith("https://techcrunch.com"))
        self.assertIn("2024-05-01", p.funding["date"])
        ok, unmet = self._C.verify(p, self._C.parse(self._plan(self.SEED)))
        self.assertTrue(ok, unmet)

    def test_missing_funding_evidence_is_rejected(self):
        for name, domain in (("NoUrl", "nourl.com"), ("Unknown", "unknownco.com")):
            p = self._prospect(name, domain)
            self._verify_funding([p])
            self.assertFalse((p.funding or {}).get("verified"))
            ok, _ = self._C.verify(p, self._C.parse(self._plan(self.SEED)))
            self.assertFalse(ok, name)

    def test_apply_keeps_only_the_verified_seed_company(self):
        adyen = self._prospect("Adyen", "adyen.com")
        airwallex = self._prospect("Airwallex", "airwallex.com")
        getfwd = self._prospect("Getfwd", "getfwd.com")
        pool = [adyen, airwallex, getfwd]
        self._verify_funding(pool)
        kept, dropped = self._C.apply(pool, self._C.parse(self._plan(self.SEED)))
        self.assertEqual([p.company_name for p in kept], ["Getfwd"])
        self.assertEqual(dropped.get("funding_stage"), 2)

    # ── the non-constraint case must NOT over-reject ───────────────────────
    def test_fintech_without_a_funding_constraint_allows_later_stage(self):
        plan = intent.parse("B2B fintech companies", keywords=["fintech"])
        c = self._C.parse(plan)
        # A late-stage fintech with NO funding evidence attached still qualifies,
        # because no funding stage was requested.
        p = self._prospect("Adyen", "adyen.com")
        ok, unmet = self._C.verify(p, c)
        self.assertTrue(ok, unmet)

    # ── hiring constraint keeps its official-evidence requirement ──────────
    def test_hiring_constraint_still_requires_official_hiring_evidence(self):
        c = self._C.parse(self._plan(self.HIRING))
        self.assertEqual(c.hiring_roles, ["sdr"])
        web = Prospect(company_name="Acme", website="https://acme.com",
                       domain="acme.com", discovery_source="exa", tier="company")
        ok, unmet = self._C.verify(web, c)
        self.assertFalse(ok)
        self.assertIn("hiring", unmet)
        web.hiring = {"verified": True, "source": "own_careers_page", "match": "role"}
        ok, _ = self._C.verify(web, c)
        self.assertTrue(ok)
        apollo = Prospect(company_name="Clay", website="https://clay.com",
                          domain="clay.com", discovery_source="apollo",
                          apollo_id="a1", tier="company")
        self.assertTrue(self._C.verify(apollo, c)[0])


if __name__ == "__main__":
    unittest.main()
