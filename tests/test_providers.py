"""Offline tests for the research providers + orchestrator.

No network and no API keys: every HTTP call (providers_common.request_json) and
the synthesis LLM call are mocked. These assert the CONTRACT of each provider
(shape parsing, availability gating, graceful failure) and the orchestrator's
own logic (intent selection, concurrency, caching, graceful degradation,
never-raises), which is where the integration's correctness lives.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import exa, firecrawl, jina, tavily  # noqa: E402
from research import orchestrator as orch  # noqa: E402


def _env(**kv):
    """Context manager: set provider env keys for the duration of a test."""
    return mock.patch.dict(os.environ, kv, clear=False)


class ProviderAvailabilityTests(unittest.TestCase):
    def test_available_reflects_env_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(firecrawl.available())
            self.assertFalse(tavily.available())
            self.assertFalse(exa.available())
            self.assertFalse(jina.available())
        with mock.patch.dict(os.environ, {
                "FIRECRAWL_API_KEY": "fc-x", "TAVILY_API_KEY": "tv-x",
                "EXA_API_KEY": "ex-x", "JINA_API_KEY": "jn-x"}, clear=True):
            self.assertTrue(firecrawl.available())
            self.assertTrue(tavily.available())
            self.assertTrue(exa.available())
            self.assertTrue(jina.available())


class ProviderDiagnosticsTests(unittest.TestCase):
    """The diagnostic that would have caught production running on web search
    alone for a full deploy cycle, because APOLLO_API_KEY was unset on the backend."""

    def test_env_names_match_each_provider_module(self):
        """PROVIDER_ENV is declared away from the modules (they import it), so it
        could silently drift and report a provider configured under a name nothing
        actually reads. Pin every one to its module's own _ENV."""
        from research import apollo_orgs, x_search
        from research.providers_common import PROVIDER_ENV
        for name, module in (("apollo", apollo_orgs), ("tavily", tavily),
                             ("exa", exa), ("x", x_search),
                             ("firecrawl", firecrawl), ("jina", jina)):
            self.assertEqual(PROVIDER_ENV[name], module._ENV, name)

    def test_status_is_booleans_only_and_tracks_the_environment(self):
        from research.providers_common import provider_status
        with mock.patch.dict(os.environ, {}, clear=True):
            off = provider_status()
        with mock.patch.dict(os.environ, {"APOLLO_API_KEY": "super-secret-value"},
                             clear=True):
            on = provider_status()
        self.assertFalse(off["apollo"])
        self.assertTrue(on["apollo"])
        self.assertFalse(on["tavily"])
        for value in list(off.values()) + list(on.values()):
            self.assertIsInstance(value, bool)      # never a key, length or prefix
        self.assertNotIn("super-secret-value", repr(on))

    def test_whitespace_only_key_counts_as_not_configured(self):
        from research.providers_common import provider_status
        with mock.patch.dict(os.environ, {"APOLLO_API_KEY": "   "}, clear=True):
            self.assertFalse(provider_status()["apollo"])

    def test_log_line_is_greppable_and_leaks_nothing(self):
        from research.providers_common import provider_status_line
        with mock.patch.dict(os.environ, {"APOLLO_API_KEY": "sk-live-abc123"},
                             clear=True):
            line = provider_status_line()
        self.assertIn("apollo=true", line)
        self.assertIn("tavily=false", line)
        self.assertNotIn("sk-live", line)
        self.assertNotIn("abc123", line)


class FirecrawlTests(unittest.TestCase):
    def test_scrape_parses_markdown(self):
        payload = {"data": {"markdown": "# Hello\nbody",
                            "metadata": {"title": "T", "sourceURL": "https://x.com"}}}
        with _env(FIRECRAWL_API_KEY="fc-x"), \
                mock.patch("research.firecrawl.request_json", return_value=payload):
            page = firecrawl.scrape("https://x.com")
        self.assertEqual(page["title"], "T")
        self.assertIn("Hello", page["markdown"])

    def test_scrape_none_when_no_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(firecrawl.scrape("https://x.com"))

    def test_scrape_none_on_failure(self):
        with _env(FIRECRAWL_API_KEY="fc-x"), \
                mock.patch("research.firecrawl.request_json", return_value=None):
            self.assertIsNone(firecrawl.scrape("https://x.com"))

    def test_map_filters_to_valid_urls(self):
        payload = {"links": [{"url": "https://x.com/a"}, {"url": "notaurl"},
                             "https://x.com/b"]}
        with _env(FIRECRAWL_API_KEY="fc-x"), \
                mock.patch("research.firecrawl.request_json", return_value=payload):
            urls = firecrawl.map_site("https://x.com")
        self.assertIn("https://x.com/a", urls)
        self.assertIn("https://x.com/b", urls)
        self.assertNotIn("notaurl", urls)

    def test_prioritize_keeps_homepage_first_and_same_host(self):
        home = "https://x.com"
        urls = ["https://x.com/blog/1", "https://other.com/pricing",
                "https://x.com/pricing", "https://x.com/about"]
        picked = firecrawl._prioritize(home, urls, 4)
        self.assertEqual(picked[0], home)                       # homepage first
        self.assertNotIn("https://other.com/pricing", picked)   # other host dropped
        self.assertIn("https://x.com/pricing", picked)


class TavilyTests(unittest.TestCase):
    def test_search_parses_results(self):
        payload = {"results": [
            {"url": "https://n.com/a", "title": "A", "content": "ca",
             "published_date": "2026-01-01"},
            {"title": "no url"}]}
        with _env(TAVILY_API_KEY="tv-x"), \
                mock.patch("research.tavily.request_json", return_value=payload):
            res = tavily.search("q")
        self.assertEqual(len(res), 1)                # the url-less item is dropped
        self.assertEqual(res[0]["url"], "https://n.com/a")

    def test_empty_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tavily.search("q"), [])

    def test_news_sets_topic(self):
        seen = {}
        def capture(method, url, **kw):
            seen.update(kw.get("json_body") or {})
            return {"results": []}
        with _env(TAVILY_API_KEY="tv-x"), \
                mock.patch("research.tavily.request_json", side_effect=capture):
            tavily.recent_news("Acme")
        self.assertEqual(seen.get("topic"), "news")
        self.assertIn("days", seen)


class ExaTests(unittest.TestCase):
    def test_search_parses_text_contents(self):
        payload = {"results": [{"url": "https://e.com/x", "title": "X",
                                "text": "long", "publishedDate": "2026-02-02"}]}
        with _env(EXA_API_KEY="ex-x"), \
                mock.patch("research.exa.request_json", return_value=payload):
            res = exa.search("q")
        self.assertEqual(res[0]["url"], "https://e.com/x")
        self.assertEqual(res[0]["content"], "long")

    def test_empty_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(exa.search("q"), [])


class JinaTests(unittest.TestCase):
    def test_clean_url_parses_content(self):
        payload = {"data": {"content": "# clean markdown"}}
        with _env(JINA_API_KEY="jn-x"), \
                mock.patch("research.jina.request_json", return_value=payload):
            md = jina.clean_url("https://x.com")
        self.assertEqual(md, "# clean markdown")

    def test_clean_url_empty_on_failure(self):
        with _env(JINA_API_KEY="jn-x"), \
                mock.patch("research.jina.request_json", return_value=None):
            self.assertEqual(jina.clean_url("https://x.com"), "")


class IntentTests(unittest.TestCase):
    def test_plain_research_is_website_plus_news(self):
        self.assertEqual(set(orch.intents_for("research Stripe")),
                         {orch.WEBSITE, orch.NEWS})

    def test_recent_launch_includes_news(self):
        self.assertIn(orch.NEWS, orch.intents_for("what did Stripe launch recently"))

    def test_founder_hook_includes_deep(self):
        self.assertIn(orch.DEEP, orch.intents_for("find a unique founder hook"))

    def test_everything_uses_all_sources(self):
        self.assertEqual(set(orch.intents_for("give me everything on Stripe")),
                         {orch.WEBSITE, orch.NEWS, orch.DEEP})


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        orch.clear_cache()

    def test_provider_status_is_booleans_only(self):
        status = orch.provider_status()
        self.assertEqual(set(status), {"anthropic", "firecrawl", "tavily", "exa", "jina"})
        self.assertTrue(all(isinstance(v, bool) for v in status.values()))

    def test_gather_runs_only_chosen_intents(self):
        with mock.patch("research.orchestrator._gather_website",
                        return_value=[orch._ev("firecrawl", "website", "u", "t", "x")]) as w, \
             mock.patch("research.orchestrator._gather_news", return_value=[]) as n, \
             mock.patch("research.orchestrator._gather_deep", return_value=[]) as d:
            orch.gather("Acme", "https://acme.com", intents=(orch.WEBSITE,))
        w.assert_called_once()
        n.assert_not_called()
        d.assert_not_called()

    def test_gather_survives_one_provider_raising(self):
        with mock.patch("research.orchestrator._gather_website",
                        side_effect=RuntimeError("boom")), \
             mock.patch("research.orchestrator._gather_news",
                        return_value=[orch._ev("tavily", "news", "u", "t", "x")]):
            out = orch.gather("Acme", "https://acme.com",
                              intents=(orch.WEBSITE, orch.NEWS))
        self.assertEqual(len(out), 1)                       # news survived
        self.assertEqual(out[0]["provider"], "tavily")

    def test_research_empty_when_no_evidence(self):
        with mock.patch("research.orchestrator.gather", return_value=[]), \
             mock.patch("research.orchestrator.synthesize") as synth:
            r = orch.research("Acme", url="https://acme.com")
        self.assertEqual(r["status"], "empty")
        synth.assert_not_called()                           # no LLM call on empty

    def test_research_synthesizes_and_caches(self):
        ev = [orch._ev("firecrawl", "website", "https://acme.com", "Acme", "does X")]
        synth_out = {"summary": "S", "findings": [{"text": "f", "category": "product",
                     "source_url": "https://acme.com", "recency": "recent",
                     "usefulness": 0.9}], "hooks": []}
        with mock.patch("research.orchestrator.gather", return_value=ev) as g, \
             mock.patch("research.orchestrator.synthesize",
                        return_value=synth_out) as synth:
            r1 = orch.research("Acme", url="https://acme.com", focus="summarize")
            r2 = orch.research("Acme", url="https://acme.com", focus="summarize")
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r1["summary"], "S")
        self.assertIn("firecrawl", r1["providers_used"])
        self.assertIs(r1, r2)                               # cache hit -> same object
        g.assert_called_once()                              # gather not re-run
        synth.assert_called_once()

    def test_research_error_is_graceful(self):
        from services.claude_client import ClaudeClientError
        ev = [orch._ev("firecrawl", "website", "https://acme.com", "Acme", "x")]
        with mock.patch("research.orchestrator.gather", return_value=ev), \
             mock.patch("research.orchestrator.synthesize",
                        side_effect=ClaudeClientError("nope")):
            r = orch.research("Acme", url="https://acme.com")
        self.assertEqual(r["status"], "error")
        self.assertIn("error", r)

    def test_research_reports_missing_providers(self):
        ev = [orch._ev("fetch", "website", "https://acme.com", "Acme", "x")]
        with mock.patch("research.orchestrator.gather", return_value=ev), \
             mock.patch("research.orchestrator.synthesize",
                        return_value={"summary": "s", "findings": [], "hooks": []}), \
             mock.patch.dict(os.environ, {}, clear=True):
            r = orch.research("Acme", url="https://acme.com")
        self.assertEqual(set(r["providers_missing"]),
                         {"firecrawl", "tavily", "exa", "jina"})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class RefusalLatchTests(unittest.TestCase):
    """A provider refusing at the ACCOUNT level must not be asked again.

    Tavily answered HTTP 432 "exceeds your plan's usage limit" to every query for
    days. Each caller kept trying, and each read the empty result as "found
    nothing" — so the outage was invisible and the retries were pure waste.
    """

    def setUp(self):
        from research import providers_common as pc
        pc.clear_error("tavily")

    tearDown = setUp

    def test_a_quota_refusal_latches_the_provider_off(self):
        from research import providers_common as pc
        pc.note_error("tavily", "refused: plan or usage limit reached")
        self.assertTrue(pc.refused("tavily"))

    def test_an_auth_rejection_latches_too(self):
        from research import providers_common as pc
        pc.note_error("tavily", "rejected the API key (auth)")
        self.assertTrue(pc.refused("tavily"))

    def test_an_ordinary_failure_does_not_latch(self):
        from research import providers_common as pc
        pc.note_error("tavily", "timed out")
        self.assertFalse(pc.refused("tavily"))       # transient: keep retrying

    def test_a_latched_provider_is_not_called_again(self):
        from research import providers_common as pc
        pc.note_error("tavily", "refused: plan or usage limit reached")
        with mock.patch.object(pc.requests, "request") as sent:
            out = pc.request_json("POST", "https://api.tavily.com/search",
                                  provider="tavily", json_body={"q": "x"})
        self.assertIsNone(out)
        sent.assert_not_called()                     # no wasted call, no credit

    def test_a_good_response_clears_the_latch(self):
        from research import providers_common as pc
        pc.note_error("tavily", "refused: plan or usage limit reached")
        self.assertTrue(pc.refused("tavily"))
        pc.clear_error("tavily")                     # what a 200 does
        self.assertFalse(pc.refused("tavily"))

    def test_the_latch_expires_so_a_key_swap_recovers_itself(self):
        from research import providers_common as pc
        pc.note_error("tavily", "refused: plan or usage limit reached")
        with mock.patch.object(pc.time, "time",
                               return_value=pc.time.time() + pc._REFUSAL_LATCH_SECONDS + 1):
            self.assertFalse(pc.refused("tavily"))
