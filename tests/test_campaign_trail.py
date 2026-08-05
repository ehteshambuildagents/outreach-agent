"""Campaign / Prospects research-trail mapper (server/campaign_trail.py).

The Chat trail is emitted LIVE from the agent's tool loop; campaigns and discovery
have no such loop, so their trail is DERIVED, as a pure function, from the real,
already-persisted stage outcomes. These tests assert that derivation:

  * produces the SAME canonical event shape as chat.research_trail (so the frontend
    renders it identically);
  * emits an event only for a stage that GENUINELY ran (no fabricated steps);
  * represents a stage that failed to execute as FAILED, honestly;
  * carries only safe, de-duplicated, scheme-validated evidence links;
  * is deterministic — the same persisted data yields byte-for-byte the same trail,
    with stable event ids, so a restored view never replays a fake animation;
  * is JSON-serializable.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import research_trail as trail  # noqa: E402
from server import campaign_trail  # noqa: E402

_CANONICAL_KEYS = {"event_id", "run_id", "event_type", "label", "status", "target",
                   "detail", "provider", "sources", "confidence", "ts"}


def _full_prospect():
    """A prospect that cleared every stage (the shape server/campaign_api builds)."""
    return {
        "company_name": "Acme Robotics",
        "website": "https://acme.com",
        "domain": "acme.com",
        "confidence": 0.82,
        "final_status": "sendable",
        "research": {"status": "ok", "research_score": 74,
                     "company_name": "Acme Robotics",
                     "pages_crawled": ["https://acme.com/about",
                                       "https://acme.com/about",   # dup -> collapsed
                                       "https://acme.com/team"]},
        "qualification": {"recommendation": "high_priority", "qualification_score": 88},
        "strategy": {"recommended_action": "send_email"},
        "email": {"status": "ok", "subject": "Warehouse robots + your logistics team"},
        "guard": {"decision": "ALLOW", "overallRisk": 12},
    }


class ProspectTrailShapeTests(unittest.TestCase):
    def test_matches_the_canonical_event_shape(self):
        events = campaign_trail.prospect_trail(_full_prospect())
        self.assertTrue(events)
        for e in events:
            self.assertEqual(set(e.keys()), _CANONICAL_KEYS)

    def test_one_event_per_stage_that_ran(self):
        events = campaign_trail.prospect_trail(_full_prospect())
        self.assertEqual([e["event_type"] for e in events],
                         ["research", "qualification", "strategy", "writer", "guard"])
        self.assertTrue(all(e["status"] == trail.COMPLETED for e in events))

    def test_no_events_for_a_prospect_with_no_persisted_stages(self):
        # A prospect that failed before research even produced a result carries no
        # research/qualification/... keys -> no invented trail.
        bare = {"company_name": "Nothing Ltd", "domain": "nothing.example",
                "final_status": "research_failed"}
        self.assertEqual(campaign_trail.prospect_trail(bare), [])

    def test_failed_research_is_an_honest_failed_event(self):
        p = {"company_name": "Blocked Co", "domain": "blocked.com",
             "research": {"status": "error", "reason": "site blocked crawling"}}
        events = campaign_trail.prospect_trail(p)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "research")
        self.assertEqual(events[0]["status"], trail.FAILED)
        self.assertIn("blocked", events[0]["detail"])

    def test_evidence_links_are_safe_and_deduped(self):
        events = campaign_trail.prospect_trail(_full_prospect())
        research = next(e for e in events if e["event_type"] == "research")
        urls = [s["url"] for s in research["sources"]]
        self.assertIn("https://acme.com", urls)              # official site (from website)
        self.assertEqual(len(urls), len(set(urls)))          # de-duplicated
        self.assertTrue(all(u.startswith("https://") for u in urls))
        self.assertTrue(any(s["official"] for s in research["sources"]))

    def test_unsafe_urls_are_dropped(self):
        p = _full_prospect()
        p["website"] = "javascript:alert(1)"
        p["research"]["pages_crawled"] = ["data:text/html,x", "https://acme.com/ok"]
        research = next(e for e in campaign_trail.prospect_trail(p)
                        if e["event_type"] == "research")
        urls = [s["url"] for s in research["sources"]]
        self.assertEqual(urls, ["https://acme.com/ok"])

    def test_deterministic_and_stable_ids(self):
        a = campaign_trail.prospect_trail(_full_prospect())
        b = campaign_trail.prospect_trail(_full_prospect())
        self.assertEqual(a, b)                                # byte-for-byte
        ids = [e["event_id"] for e in a]
        self.assertEqual(len(ids), len(set(ids)))            # unique within a run
        self.assertTrue(all(e["ts"] == 0 for e in a))        # no live timestamp

    def test_json_serializable(self):
        json.dumps(campaign_trail.prospect_trail(_full_prospect()))

    def test_not_a_dict_is_empty(self):
        self.assertEqual(campaign_trail.prospect_trail(None), [])
        self.assertEqual(campaign_trail.prospect_trail("x"), [])


class DiscoveryTrailTests(unittest.TestCase):
    def _discovered(self):
        return {
            "company_name": "Nimbus Data",
            "website": "https://nimbus.io",
            "confidence": 0.6,
            "why_it_matches": "Seed-stage dev-tools company hiring SDRs",
            "discovery_source": "apollo",
            "sources": [{"provider": "apollo", "url": "https://apollo.io/nimbus"}],
            "funding": {"stage": "Seed", "source_url": "https://tc.com/nimbus-seed"},
        }

    def test_single_discovered_event_names_real_provider(self):
        events = campaign_trail.discovery_trail(self._discovered())
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(set(e.keys()), _CANONICAL_KEYS)
        self.assertEqual(e["event_type"], "discovery")
        self.assertEqual(e["provider"], "apollo")
        self.assertEqual(e["status"], trail.COMPLETED)
        self.assertIn("apollo", e["label"])

    def test_evidence_includes_official_site_and_funding(self):
        e = campaign_trail.discovery_trail(self._discovered())[0]
        urls = [s["url"] for s in e["sources"]]
        self.assertIn("https://nimbus.io", urls)
        self.assertIn("https://tc.com/nimbus-seed", urls)
        official = [s for s in e["sources"] if s["official"]]
        self.assertTrue(official and official[0]["url"] == "https://nimbus.io")

    def test_deterministic(self):
        self.assertEqual(campaign_trail.discovery_trail(self._discovered()),
                         campaign_trail.discovery_trail(self._discovered()))

    def test_bare_host_website_is_upgraded_to_https(self):
        p = {"company_name": "Bare", "website": "bare.com", "discovery_source": "exa"}
        e = campaign_trail.discovery_trail(p)[0]
        self.assertIn("https://bare.com", [s["url"] for s in e["sources"]])


if __name__ == "__main__":
    unittest.main()
