"""Research trail — the canonical, persisted, evidence-bearing record of what the
agent ACTUALLY did (chat/research_trail.py, agent emission, persistence).

Fully offline: the Claude tool-use call and the research tool are mocked. The
trail is asserted to (a) come only from real tool execution, (b) carry safe,
de-duplicated evidence links, (c) persist across a store round-trip, and (d)
surface honest failure events — never a fabricated provider or a fake animation.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import agent, research_trail as trail  # noqa: E402
from chat.models import Conversation  # noqa: E402
from chat.store import ConversationStore  # noqa: E402


def _research_ok(company="Acme"):
    return {"status": "ok", "research_score": 72, "stop_reason": "x",
            "pages_crawled": ["https://acme.com/about"], "hooks": [],
            "data": {"company_name": company, "what_they_do": "warehouse robots",
                     "target_customer": "logistics", "business_model": "SaaS"}}


def _tool_use(name, tool_input, tuid="t1"):
    return {"stop_reason": "tool_use", "text": "",
            "tool_uses": [{"id": tuid, "name": name, "input": tool_input}],
            "assistant_content": [{"type": "tool_use", "id": tuid,
                                   "name": name, "input": tool_input}]}


def _final(text="Here's what I found."):
    return {"stop_reason": "end_turn", "text": text, "tool_uses": [],
            "assistant_content": [{"type": "text", "text": text}]}


class SafeUrlTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(trail.safe_url("https://acme.com/x"), "https://acme.com/x")
        self.assertEqual(trail.safe_url("http://acme.com"), "http://acme.com")

    def test_rejects_unsafe_and_malformed(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "  javascript:x",
                    "/relative/path", "https://nohost", "ftp://acme.com",
                    "https://ac me.com", "https://ac\nme.com", "", None, 123):
            self.assertIsNone(trail.safe_url(bad), bad)

    def test_domain_strips_www(self):
        self.assertEqual(trail.domain_of("https://www.acme.com/team"), "acme.com")

    def test_source_validates_and_marks_official(self):
        s = trail.source("About", "https://acme.com/about", official=True)
        self.assertEqual(s["domain"], "acme.com")
        self.assertTrue(s["official"])
        self.assertIsNone(trail.source("Bad", "javascript:x"))  # unsafe -> dropped

    def test_dedupe_sources_drops_repeats_and_falsy(self):
        s1 = trail.source("A", "https://acme.com")
        s2 = trail.source("B", "https://acme.com")   # same url
        s3 = trail.source("C", "https://other.com")
        out = trail.dedupe_sources([s1, None, s2, s3])
        self.assertEqual([s["url"] for s in out],
                         ["https://acme.com", "https://other.com"])

    def test_event_has_the_canonical_schema(self):
        rid = trail.new_run_id()
        e = trail.event(run_id=rid, event_type="research_company",
                        label="Researching the company", status=trail.COMPLETED,
                        target="Acme", provider="research_company",
                        sources=[trail.source("x", "https://acme.com")], confidence=0.7)
        for k in ("event_id", "run_id", "event_type", "label", "status", "target",
                  "detail", "provider", "sources", "confidence", "ts"):
            self.assertIn(k, e)
        self.assertTrue(e["event_id"].startswith("ev_"))
        self.assertEqual(e["run_id"], rid)
        self.assertEqual(e["status"], "completed")


class TrailEmissionTests(unittest.TestCase):
    def _run(self, conv, tool_calls_then_final, research=None):
        seq = list(tool_calls_then_final)
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=seq), \
             mock.patch("chat.tools.research_company",
                        return_value=research or _research_ok()):
            agent.respond(conv, "research acme.com", store=None, user_id="user_x")

    def test_completed_tool_emits_running_then_completed_with_target(self):
        conv = Conversation()
        self._run(conv, [_tool_use("research_company", {"query": "acme.com"}), _final()])
        types = [(e["event_type"], e["status"]) for e in conv.research_trail]
        self.assertIn(("research_company", "running"), types)
        self.assertIn(("research_company", "completed"), types)
        done = [e for e in conv.research_trail if e["status"] == "completed"][0]
        self.assertEqual(done["target"], "Acme")          # the real researched company
        self.assertEqual(done["provider"], "research_company")  # the tool that ran
        # Every evidence link is safe and de-duplicated.
        for s in done["sources"]:
            self.assertEqual(trail.safe_url(s["url"]), s["url"])
        self.assertTrue(any(s["official"] for s in done["sources"]))  # company's own site

    def test_a_plain_answer_produces_no_trail_events(self):
        # No tool ran, so there is NOTHING to narrate — the trail must stay empty
        # rather than inventing a "thinking" event (no fabricated activity).
        conv = Conversation()
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        return_value=_final("Sure, who do you sell to?")):
            agent.respond(conv, "hello", store=None, user_id="u")
        self.assertEqual(conv.research_trail, [])

    def test_tool_failure_emits_a_transparent_failed_event(self):
        conv = Conversation()
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        side_effect=[_tool_use("research_company", {"query": "x"}), _final()]), \
             mock.patch("chat.agent.tools.execute", side_effect=RuntimeError("boom")):
            agent.respond(conv, "research x", store=None, user_id="u")
        failed = [e for e in conv.research_trail if e["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["event_type"], "research_company")
        self.assertTrue(failed[0]["detail"])            # a retryable, human message

    def test_trail_persists_across_a_store_round_trip(self):
        base = tempfile.mkdtemp()
        store = ConversationStore(directory=base)
        conv = Conversation()
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        side_effect=[_tool_use("research_company", {"query": "acme.com"}), _final()]), \
             mock.patch("chat.tools.research_company", return_value=_research_ok()):
            agent.respond(conv, "research acme", store=store, user_id="u")
        store.save(conv)
        reloaded = store.load(conv.id)
        self.assertTrue(reloaded.research_trail)
        self.assertEqual([e["event_id"] for e in reloaded.research_trail],
                         [e["event_id"] for e in conv.research_trail])

    def test_add_trail_event_caps_history(self):
        conv = Conversation()
        for i in range(80):
            conv.add_trail_event(trail.event(run_id="r", event_type="t",
                                             label=f"e{i}", status=trail.COMPLETED),
                                 cap=60)
        self.assertEqual(len(conv.research_trail), 60)
        self.assertEqual(conv.research_trail[-1]["label"], "e79")  # newest kept


if __name__ == "__main__":
    unittest.main(verbosity=2)
