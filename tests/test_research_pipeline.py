"""Tests for chat-directed research (chat/research_pipeline.py).

Offline: research is injected (fake results); qualification runs FOR REAL (it's
deterministic), so the scored list is exercised end to end without the network.

    python -m unittest tests.test_research_pipeline
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import research_pipeline as rp  # noqa: E402
from config.settings import RESEARCH_LIST_MAX  # noqa: E402

# A strong lead: good research + a buying signal (hiring) -> should score high.
STRONG = {
    "status": "ok", "research_score": 72,
    "data": {"company_name": "Acme", "what_they_do": "B2B onboarding automation",
             "target_customer": "SaaS ops teams", "unique_hook": "just hired their first SDR",
             "industries_served": "saas", "recent_focus": "hiring SDRs to scale outbound",
             "primary_contact_name": "Jane Doe", "primary_contact_role": "CEO",
             "metrics_or_traction": "2x YoY growth"},
    "hooks": [{"category": "traction", "text": "2x YoY growth, just hired first SDR",
               "score": 0.8, "confidence": 0.7, "source": "https://acme.com/about",
               "quote": "We doubled"}],
    "evidence": {"what_they_do": [{"value": "B2B onboarding automation",
                                   "source": "https://acme.com", "confidence": 0.9,
                                   "quote": "We automate onboarding"}]},
    "pages_crawled": ["https://acme.com", "https://acme.com/about"],
}
WEAK = {"status": "ok", "research_score": 18,
        "data": {"company_name": "Weak", "what_they_do": "a landing page"},
        "hooks": [], "evidence": {}, "pages_crawled": ["https://weak.com"]}
DEAD = {"status": "skip", "reason": "Site not reachable"}

_BY_URL = {"https://acme.com": STRONG, "https://weak.com": WEAK,
           "https://dead.com": DEAD}


def _fake_research(url):
    return _BY_URL.get(url, {"status": "error", "reason": "unknown site"})


class ResearchAndQualifyTests(unittest.TestCase):
    def _run(self):
        leads = [
            {"company_name": "Acme", "website": "https://acme.com",
             "discovery": {"why_it_matches": "matches ICP: hiring SDR"}},
            "https://weak.com",
            "https://dead.com",
        ]
        return rp.research_and_qualify(leads, research_fn=_fake_research, user_id="u1")

    def test_returns_scored_sorted_list(self):
        out = self._run()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["researched"], 2)
        scores = [e["score"] for e in out["prospects"]]
        self.assertEqual(scores, sorted(scores, reverse=True))     # best first
        self.assertEqual(out["prospects"][0]["company"], "Acme")

    def test_strong_lead_is_high_priority(self):
        top = self._run()["prospects"][0]
        self.assertIn(top["recommendation"], ("high_priority", "continue"))
        self.assertTrue(top["recommended"])
        self.assertGreaterEqual(top["score"], 45)

    def test_preview_is_one_plain_paragraph(self):
        top = self._run()["prospects"][0]
        self.assertIn("Acme", top["preview"])
        self.assertIn("/100", top["preview"])
        self.assertNotIn("\n", top["preview"])                     # collapsed one-liner
        self.assertLess(len(top["preview"]), 400)

    def test_detail_findings_carry_source_and_confidence(self):
        top = self._run()["prospects"][0]
        findings = top["detail"]["findings"]
        self.assertTrue(findings)
        f = findings[0]
        for key in ("label", "value", "source", "confidence"):
            self.assertIn(key, f)
        self.assertTrue(top["detail"]["sources"])                  # source trail present

    def test_actions_offered_but_nothing_runs(self):
        top = self._run()["prospects"][0]
        self.assertIn("draft_email", top["actions"])
        self.assertIn("draft_x_reply", top["actions"])

    def test_unresearchable_lead_is_kept_with_reason(self):
        dead = [e for e in self._run()["prospects"] if e["company"] == "Dead"][0]
        self.assertEqual(dead["status"], "skip")
        self.assertEqual(dead["score"], 0)
        self.assertIn("reachable", dead["preview"].lower())

    def test_empty_leads(self):
        out = rp.research_and_qualify([], research_fn=_fake_research)
        self.assertEqual(out["status"], "empty")

    def test_bounded_by_list_max(self):
        many = [f"https://co{i}.com" for i in range(RESEARCH_LIST_MAX + 5)]
        out = rp.research_and_qualify(many, research_fn=_fake_research)
        self.assertLessEqual(out["count"], RESEARCH_LIST_MAX)


class NormalizeLeadsTests(unittest.TestCase):
    def test_accepts_strings_and_dicts(self):
        leads = rp._normalize_leads([
            "https://acme.com", "acme.com", "Acme Corp",
            {"company_name": "Beta", "website": "https://beta.io"}])
        self.assertEqual(leads[0]["website"], "https://acme.com")
        self.assertEqual(leads[1]["website"], "https://acme.com")
        self.assertEqual(leads[2]["company_name"], "Acme Corp")    # bare name, no site
        self.assertEqual(leads[3]["company_name"], "Beta")


class BareNameResolutionTests(unittest.TestCase):
    """A pasted list of PLAIN names (no domains) must resolve to sites and produce
    results, the same as a domain list — reusing the resolve_company lookup."""

    def test_bare_names_resolve_and_research(self):
        name_to_url = {"Acme": "https://acme.com", "Weak": "https://weak.com"}
        out = rp.research_and_qualify(
            ["Acme", "Weak"], research_fn=_fake_research,
            resolve_fn=lambda n: name_to_url.get(n, ""))
        self.assertEqual(out["researched"], 2)
        self.assertEqual(out["prospects"][0]["company"], "Acme")
        self.assertTrue(out["prospects"][0]["detail"]["findings"])
        # website was filled in by resolution, not left blank
        self.assertEqual(out["prospects"][0]["website"], "https://acme.com")

    def test_unresolvable_name_skips_with_clear_reason(self):
        out = rp.research_and_qualify(
            ["Nonexistent Co"], research_fn=_fake_research, resolve_fn=lambda n: "")
        e = out["prospects"][0]
        self.assertEqual(e["status"], "skip")
        self.assertIn("website", e["preview"].lower())     # explained, not silent
        self.assertIn("Nonexistent", e["preview"])

    def test_default_resolver_uses_chat_resolver(self):
        # The default path delegates to chat.resolver.resolve_company_name.
        with mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "resolved", "url": "https://acme.com"}):
            self.assertEqual(rp._default_resolve("Acme"), "https://acme.com")
        with mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "choices",
                                      "choices": [{"url": "https://acme.com"}]}):
            self.assertEqual(rp._default_resolve("Acme"), "https://acme.com")
        with mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "none"}):
            self.assertEqual(rp._default_resolve("Ghost"), "")


class DiscoverLeadsTests(unittest.TestCase):
    def test_discovery_maps_prospects_to_leads(self):
        fake_result = mock.Mock(status="ok", has_more=True, prospects=[
            mock.Mock(company_name="Acme",
                      website="https://acme.com",
                      **{"public.return_value": {"why_it_matches": "ICP match"}})])
        with mock.patch.object(rp.discovery_engine, "discover", return_value=fake_result):
            status, leads, reason, has_more = rp.discover_leads("owner", "b2b saas")
        self.assertEqual(status, "ok")
        self.assertEqual(leads[0]["company_name"], "Acme")
        self.assertTrue(has_more)

    def test_discovery_empty_is_reported(self):
        fake_result = mock.Mock(status="empty", reason="No matches.", prospects=[])
        with mock.patch.object(rp.discovery_engine, "discover", return_value=fake_result):
            status, leads, reason, _ = rp.discover_leads("owner", "nonsense query xyz")
        self.assertEqual(status, "empty")
        self.assertEqual(leads, [])


if __name__ == "__main__":
    unittest.main()
