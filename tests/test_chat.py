"""Tests for the conversational workspace (chat layer).

Fully offline: the research engine, email writer, and the Claude tool-use call
are all mocked. These tests pin the behaviours the feature promises:
  * research + email happen via TOOLS, not hardcoded chat logic;
  * existing research is REUSED, never re-run, unless explicitly forced;
  * email revisions pass the user's guidance + current draft to the writer;
  * conversations persist and reload (sidebar threads survive a restart);
  * roadmap tools are registered and defer gracefully.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat import agent, resolver, tools  # noqa: E402
from chat.context import research_digest, workspace_state_text  # noqa: E402
from chat.models import Conversation, EMAIL, RESEARCH, TEXT, Message  # noqa: E402
from chat.store import ConversationStore  # noqa: E402


def _sr(url, title="", description=""):
    return {"url": url, "title": title, "description": description}


# ── fixtures ───────────────────────────────────────────────────────────
def _research_ok(company="Acme"):
    return {
        "status": "ok",
        "research_score": 72,
        "stop_reason": "sufficient research after homepage (score 72)",
        "pages_crawled": ["https://acme.com"],
        "hooks": [{"text": "Trusted by DHL"}, {"text": "Series B in 2025"}],
        "data": {
            "company_name": company,
            "what_they_do": "warehouse robots",
            "target_customer": "logistics teams",
            "business_model": "SaaS",
            "product_category": "robotics",
            "primary_contact_name": "Bob Vance",
            "primary_contact_role": "CEO",
            "team_members": [{"name": "Bob Vance", "role": "CEO"},
                             {"name": "Amy Lee", "role": "CTO"}],
            "notable_customers": ["DHL"],
            "pricing_model": None,
        },
    }


def _email_ok(subject="warehouse robots", body=None):
    if body is None:
        body = ("Hey Bob, saw Acme's warehouse robots focus on logistics teams. "
                "Turning that pilot detail into specific account-by-account outreach "
                "feels useful for warehouse buyers; worth a quick look?")
    return {"status": "ok", "subject": subject, "body": body,
            "company": "Acme", "to": "Bob", "used_reveal": False}


def _tool_use(name, tool_input, tuid="t1"):
    return {"stop_reason": "tool_use", "text": "",
            "tool_uses": [{"id": tuid, "name": name, "input": tool_input}],
            "assistant_content": [{"type": "tool_use", "id": tuid,
                                   "name": name, "input": tool_input}]}


def _final(text):
    return {"stop_reason": "end_turn", "text": text, "tool_uses": [],
            "assistant_content": [{"type": "text", "text": text}]}


# ── URL / name resolution ──────────────────────────────────────────────
class ResolveUrlTests(unittest.TestCase):
    def test_full_url_passes_through(self):
        self.assertEqual(tools.resolve_url("https://x.com"), ("https://x.com", False))

    def test_bare_domain_gets_scheme(self):
        self.assertEqual(tools.resolve_url("acme.com"), ("https://acme.com", False))

    def test_company_name_becomes_guessed_domain(self):
        url, guessed = tools.resolve_url("Acme Robotics")
        self.assertEqual(url, "https://acmerobotics.com")
        self.assertTrue(guessed)

    def test_empty_is_none(self):
        self.assertEqual(tools.resolve_url("   "), (None, False))


# ── Tools operate on the workspace ─────────────────────────────────────
class ResearchToolTests(unittest.TestCase):
    def test_research_populates_workspace_and_card(self):
        conv = Conversation()
        with mock.patch("chat.tools.research_company", return_value=_research_ok()) as rc:
            result = tools.execute("research_company", {"query": "acme.com"}, conv)
        rc.assert_called_once()
        self.assertEqual(result.workspace_updates["company"], "Acme")
        self.assertEqual(result.workspace_updates["research"]["status"], "ok")
        self.assertEqual(result.message.kind, RESEARCH)
        self.assertIn("warehouse robots", result.summary)

    def test_research_is_reused_not_rerun(self):
        conv = Conversation(workspace={"research": _research_ok(), "company": "Acme"})
        with mock.patch("chat.tools.research_company") as rc:
            result = tools.execute("research_company", {"query": "acme.com"}, conv)
        rc.assert_not_called()                       # cached -> no crawl
        self.assertIn("already on file", result.summary)

    def test_refresh_forces_rerun(self):
        conv = Conversation(workspace={"research": _research_ok(), "company": "Acme"})
        with mock.patch("chat.tools.research_company", return_value=_research_ok()) as rc:
            tools.execute("research_company", {"query": "acme.com", "refresh": True}, conv)
        rc.assert_called_once()

    def test_research_error_is_reported_not_raised(self):
        conv = Conversation()
        err = {"status": "error", "error": "Could not resolve that host name"}
        with mock.patch("chat.tools.research_company", return_value=err):
            result = tools.execute("research_company", {"query": "Nonexistent Co"}, conv)
        self.assertIn("Could not research", result.summary)
        self.assertNotIn("research", result.workspace_updates)


def _intel_ok(company="Acme"):
    return {
        "status": "ok", "company": company, "summary": "Acme does warehouse robots.",
        "findings": [{"text": "Raised Series B in 2025", "category": "funding",
                      "source_url": "https://news.com/acme", "recency": "recent",
                      "usefulness": 0.9}],
        "hooks": [{"text": "Congrats on the Series B", "category": "funding",
                   "source_url": "https://news.com/acme"}],
        "sources": [{"url": "https://acme.com", "provider": "firecrawl",
                     "title": "Acme", "kind": "website"}],
        "providers_used": ["firecrawl", "tavily"], "providers_missing": [],
    }


class DeepResearchToolTests(unittest.TestCase):
    def test_populates_intel_and_card(self):
        conv = Conversation()
        with mock.patch("chat.tools.orchestrator.research",
                        return_value=_intel_ok()) as r:
            result = tools.execute(
                "deep_research", {"company": "acme.com", "focus": "recent news"}, conv)
        r.assert_called_once()
        self.assertEqual(result.workspace_updates["intel"]["status"], "ok")
        self.assertEqual(result.message.kind, RESEARCH)
        self.assertIn("Series B", result.summary)          # findings fed to the model

    def test_uses_thread_company_when_no_input(self):
        conv = Conversation(workspace={"company": "Acme",
                                       "company_url": "https://acme.com"})
        with mock.patch("chat.tools.orchestrator.research",
                        return_value=_intel_ok()) as r:
            tools.execute("deep_research", {}, conv)
        _, kwargs = r.call_args
        self.assertEqual(kwargs.get("url"), "https://acme.com")

    def test_empty_is_reported_not_raised(self):
        conv = Conversation()
        empty = {"status": "empty", "company": "Acme", "summary": "",
                 "findings": [], "hooks": [], "sources": [],
                 "providers_used": [], "providers_missing": ["exa"]}
        with mock.patch("chat.tools.orchestrator.research", return_value=empty):
            result = tools.execute("deep_research", {"company": "Acme"}, conv)
        self.assertIn("nothing usable", result.summary)
        self.assertEqual(result.workspace_updates["intel"]["status"], "empty")

    def test_no_target_asks_user(self):
        conv = Conversation()
        with mock.patch("chat.tools.orchestrator.research") as r:
            result = tools.execute("deep_research", {}, conv)
        r.assert_not_called()
        self.assertIn("which company", result.summary.lower())


class WriteEmailToolTests(unittest.TestCase):
    def test_draft_uses_research_on_file(self):
        conv = Conversation(workspace={"research": _research_ok()})
        with mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            result = tools.execute("write_email", {}, conv)
        we.assert_called_once()
        # first draft -> no guidance, no current_email
        _, kwargs = we.call_args
        self.assertIsNone(kwargs.get("guidance"))
        self.assertIsNone(kwargs.get("current_email"))
        self.assertEqual(result.message.kind, EMAIL)
        self.assertEqual(result.workspace_updates["email"]["subject"],
                         "warehouse robots")

    def test_revision_passes_guidance_and_current_draft(self):
        conv = Conversation(workspace={"research": _research_ok(),
                                       "email": _email_ok()})
        with mock.patch("chat.tools.write_email",
                        return_value=_email_ok(body="shorter.")) as we:
            tools.execute("write_email", {"guidance": "make it shorter"}, conv)
        _, kwargs = we.call_args
        self.assertEqual(kwargs.get("guidance"), "make it shorter")
        self.assertIsNotNone(kwargs.get("current_email"))   # revise the existing draft

    def test_write_with_nothing_on_file_asks_for_a_company(self):
        # With no research, no intel, and no company label, there is nothing to
        # ground on — the writer isn't called and the agent is told to ask.
        conv = Conversation()
        with mock.patch("chat.tools.write_email") as we:
            result = tools.execute("write_email", {}, conv)
        we.assert_not_called()
        self.assertIn("company", result.summary.lower())

    def test_write_from_intel_only_uses_thin_mode(self):
        # No website research, but multi-source intel is on file -> write from it.
        conv = Conversation(workspace={"intel": _intel_ok(), "company": "Acme"})
        with mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            result = tools.execute("write_email", {}, conv)
        we.assert_called_once()
        _, kwargs = we.call_args
        self.assertTrue(kwargs.get("allow_thin"))          # thin (no full research)
        self.assertEqual(result.message.kind, EMAIL)

    def test_intel_augments_grounded_research(self):
        # With BOTH research and intel, intel hooks augment the grounded data but
        # research stays authoritative (not thin).
        conv = Conversation(workspace={"research": _research_ok(), "intel": _intel_ok()})
        captured = {}
        def capture(source, **kw):
            captured["source"] = source; captured["kw"] = kw
            return _email_ok()
        with mock.patch("chat.tools.write_email", side_effect=capture):
            tools.execute("write_email", {}, conv)
        self.assertFalse(captured["kw"].get("allow_thin"))          # grounded
        hooks = captured["source"]["data"].get("additional_hooks") or []
        self.assertTrue(any("Series B" in h for h in hooks))        # intel merged in

    def test_followup_uses_existing_email(self):
        conv = Conversation(workspace={"research": _research_ok(),
                                       "email": _email_ok()})
        with mock.patch("chat.tools.write_followup",
                        return_value=_email_ok(body="Still worth a look?")) as wf:
            result = tools.execute("write_email", {"mode": "follow_up"}, conv)
        wf.assert_called_once()
        self.assertEqual(result.message.kind, EMAIL)
        self.assertEqual(result.message.data["label"], "Follow-up")

    def test_followup_without_prior_email_is_graceful(self):
        conv = Conversation(workspace={"research": _research_ok()})   # no email yet
        with mock.patch("chat.tools.write_followup") as wf:
            result = tools.execute("write_email", {"mode": "follow_up"}, conv)
        wf.assert_not_called()
        self.assertIn("follow up", result.summary.lower())


_VARS = {"status": "ok", "company": "Acme", "to": "Bob", "variations": [
    {"label": "A", "angle": "logos", "subject": "s1", "body": "b1"},
    {"label": "B", "angle": "mission", "subject": "s2", "body": "b2"},
    {"label": "C", "angle": "craft", "subject": "s3", "body": "b3"}]}
_SEQ = {"status": "ok", "company": "Acme", "to": "Bob", "emails": [
    {"step": 1, "angle": "hook", "delay_days": 0, "subject": "s1", "body": "b1"},
    {"step": 2, "angle": "value", "delay_days": 3, "subject": "s2", "body": "b2"}]}


class ArtifactIdTests(unittest.TestCase):
    """Every generated artifact carries a STABLE, slot-based internal id (for
    future automation like 'edit version B' / 'rewrite email 3'). Ids live in
    the card data + workspace but are never exposed to the browser (see
    test_api: the API whitelist omits `id`)."""

    def test_draft_email_has_stable_id(self):
        conv = Conversation(workspace={"research": _research_ok()})
        with mock.patch("chat.tools.write_email", return_value=_email_ok()):
            r = tools.execute("write_email", {}, conv)
        self.assertEqual(r.message.data["id"], "email")
        self.assertEqual(r.workspace_updates["email"]["id"], "email")

    def test_variations_have_stable_slot_ids(self):
        conv = Conversation(workspace={"research": _research_ok()})
        with mock.patch("chat.tools.write_variations", return_value=_VARS):
            r = tools.execute("write_email", {"mode": "variations"}, conv)
        self.assertEqual([m.data["id"] for m in r.messages],
                         ["version-a", "version-b", "version-c"])
        self.assertEqual([v["id"] for v in r.workspace_updates["variations"]],
                         ["version-a", "version-b", "version-c"])
        self.assertEqual(r.workspace_updates["email"]["id"], "email")

    def test_sequence_has_stable_step_ids(self):
        conv = Conversation(workspace={"research": _research_ok()})
        with mock.patch("chat.tools.write_sequence", return_value=_SEQ):
            r = tools.execute("write_email", {"mode": "sequence"}, conv)
        self.assertEqual([m.data["id"] for m in r.messages], ["email-1", "email-2"])
        self.assertEqual([e["id"] for e in r.workspace_updates["sequence"]],
                         ["email-1", "email-2"])

    def test_followup_has_stable_id(self):
        conv = Conversation(workspace={"research": _research_ok(), "email": _email_ok()})
        with mock.patch("chat.tools.write_followup",
                        return_value=_email_ok(body="Worth another look?")):
            r = tools.execute("write_email", {"mode": "follow_up"}, conv)
        self.assertEqual(r.message.data["id"], "followup")

    def test_critique_of_pasted_email(self):
        conv = Conversation()                       # no research needed
        crit = {"status": "ok",
                "scores": {"hook": 4, "personalization": 3, "cta": 5, "clarity": 7,
                           "founder_voice": 4, "specificity": 3, "reply_likelihood": 4},
                "assessment": "Weak opener.", "suggestions": ["Open on a specific detail."]}
        with mock.patch("chat.tools.critique_email", return_value=crit) as ce:
            result = tools.execute(
                "write_email",
                {"mode": "critique", "email_text": "Hi, I hope you're well..."}, conv)
        ce.assert_called_once()
        self.assertIn("hook 4/10", result.summary)
        self.assertIn("Open on a specific detail", result.summary)

    def test_critique_without_text_asks_for_it(self):
        conv = Conversation()
        with mock.patch("chat.tools.critique_email") as ce:
            result = tools.execute("write_email", {"mode": "critique"}, conv)
        ce.assert_not_called()
        self.assertIn("paste", result.summary.lower())

    def test_sequence_returns_multiple_cards(self):
        conv = Conversation(workspace={"research": _research_ok()})
        seq = {"status": "ok", "company": "Acme", "to": "Bob", "emails": [
            {"step": 1, "angle": "offer", "delay_days": 0, "subject": "s1", "body": "b1"},
            {"step": 2, "angle": "proof", "delay_days": 3, "subject": "s2", "body": "b2"}]}
        with mock.patch("chat.tools.write_sequence", return_value=seq) as ws:
            result = tools.execute("write_email", {"mode": "sequence", "count": 2}, conv)
        ws.assert_called_once()
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.messages[0].data["label"], "Email 1 · day 0")
        self.assertEqual(result.workspace_updates["sequence"], seq["emails"])

    def test_revision_learns_style_and_keeps_history(self):
        conv = Conversation(workspace={"research": _research_ok(), "email": _email_ok()})
        with mock.patch("chat.tools.write_email",
                        return_value=_email_ok(body="tighter.")) as we:
            result = tools.execute(
                "write_email", {"guidance": "no emojis and never say 'circle back'"}, conv)
        # learned style is passed to the writer and persisted in the workspace
        _, kwargs = we.call_args
        self.assertIn("emojis", (kwargs.get("style_note") or ""))
        prof = result.workspace_updates["style_profile"]
        self.assertTrue(any("circle back" in a.lower() for a in prof["avoid"]))
        # previous draft kept for comparison
        self.assertTrue(result.workspace_updates["email_history"])

    def test_compare_shows_previous_vs_current(self):
        conv = Conversation(workspace={
            "email": {"status": "ok", "subject": "new", "body": "Short new body. Worth a look?"},
            "email_history": [{"subject": "old", "body": "A much longer old body with more words here. Worth a look?"}]})
        result = tools.execute("write_email", {"mode": "compare"}, conv)
        self.assertIn("Previous", result.summary)
        self.assertIn("Current", result.summary)

    def test_compare_without_history_is_graceful(self):
        conv = Conversation(workspace={"email": _email_ok()})
        result = tools.execute("write_email", {"mode": "compare"}, conv)
        self.assertIn("nothing to compare", result.summary.lower())


class RoadmapStubTests(unittest.TestCase):
    def test_stub_tools_registered_and_defer(self):
        conv = Conversation()
        for name in ("handle_replies", "linkedin_outreach"):
            self.assertIn(name, tools.REGISTRY)
            result = tools.execute(name, {}, conv)
            self.assertIn("not available yet", result.summary)

    def test_find_prospects_is_a_real_tool(self):
        # find_prospects graduated from the roadmap stub to the Prospect Discovery
        # Agent — with no ICP filters it asks for them rather than deferring.
        self.assertIn("find_prospects", tools.REGISTRY)
        result = tools.execute("find_prospects", {}, Conversation())
        self.assertNotIn("not available yet", result.summary)

    def test_send_email_is_no_longer_a_stub(self):
        self.assertIn("send_email", tools.REGISTRY)
        # it is a real capability now, not the "coming soon" placeholder
        conv = Conversation()
        self.assertNotIn("not available yet",
                         tools.execute("send_email", {}, conv).summary)

    def test_tool_specs_shape(self):
        specs = tools.tool_specs()
        names = {s["name"] for s in specs}
        self.assertIn("research_company", names)
        self.assertIn("write_email", names)
        for spec in specs:
            self.assertIn("input_schema", spec)
            self.assertIn("description", spec)


# ── Store round-trip ───────────────────────────────────────────────────
class StoreTests(unittest.TestCase):
    def test_save_load_list_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConversationStore(directory=d)
            conv = Conversation(title="Acme")
            conv.add_user("acme.com")
            conv.add(Message(role="assistant", kind=EMAIL, content="drafted",
                             data={"subject": "s", "body": "b"}))
            conv.workspace["research"] = _research_ok()
            store.save(conv)

            summaries = store.list_summaries()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["title"], "Acme")

            loaded = store.load(conv.id)
            self.assertEqual(loaded.title, "Acme")
            self.assertEqual(loaded.messages[1].kind, EMAIL)
            self.assertEqual(loaded.workspace["research"]["status"], "ok")

            store.delete(conv.id)
            self.assertEqual(store.list_summaries(), [])

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ConversationStore(directory=d).load("nope"))

    def test_conversation_reopens_after_restart(self):
        """The 'reopen a saved conversation' contract, end to end and offline.

        Regression guard for the reported "saved conversation won't reopen after
        refresh" symptom. A refresh throws away the in-memory activeId and reloads
        the thread list from disk, so the guarantee that must hold is: a brand-new
        store instance over the same directory (a fresh process / fresh page) can
        (1) list the thread in the sidebar and (2) load back the FULL renderable
        transcript — every message, in order, with card kinds and payloads intact.
        Uses only local-disk ConversationStore: zero Apollo/Exa/Tavily/Firecrawl/
        LLM calls, so it never spends API budget reproducing an intermittent UI bug.
        """
        with tempfile.TemporaryDirectory() as d:
            conv = Conversation(title="Linear")
            conv.add_user("Research linear.app as a prospect")
            conv.add(Message(role="assistant", kind=RESEARCH,
                             content="Here's what I found on Linear.",
                             data=_research_ok(company="Linear")))
            conv.add(Message(role="assistant", kind=EMAIL, content="Drafted an opener.",
                             data={"subject": "warehouse robots", "body": "Hey Bob,"}))
            conv.add(Message(role="assistant", kind=TEXT,
                             content="Want me to tweak the subject line?", data=None))
            ConversationStore(directory=d).save(conv)

            # A DIFFERENT instance over the same dir == the post-refresh reload.
            reopened_store = ConversationStore(directory=d)

            summaries = reopened_store.list_summaries()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["id"], conv.id)
            self.assertEqual(summaries[0]["title"], "Linear")
            self.assertEqual(summaries[0]["message_count"], 4)

            reopened = reopened_store.load(conv.id)
            self.assertIsNotNone(reopened)
            self.assertEqual([m.kind for m in reopened.messages],
                             [TEXT, RESEARCH, EMAIL, TEXT])
            self.assertEqual(reopened.messages[0].role, "user")
            # The cards the sidebar-reopened pane renders keep their payloads, so the
            # transcript is not a blank pane: research score + draft body survive.
            self.assertEqual(reopened.messages[1].data["research_score"], 72)
            self.assertEqual(reopened.messages[2].data["body"], "Hey Bob,")
            self.assertEqual(reopened.messages[3].content,
                             "Want me to tweak the subject line?")


# ── Agent loop (Claude tool-use mocked) ────────────────────────────────
class AgentLoopTests(unittest.TestCase):
    def test_first_message_researches_then_drafts(self):
        conv = Conversation()
        # model: research -> write_email -> final text
        script = [_tool_use("research_company", {"query": "acme.com"}),
                  _tool_use("write_email", {}, tuid="t2"),
                  _final("Drafted a first email for Acme.")]
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        side_effect=script), \
             mock.patch("chat.tools.research_company", return_value=_research_ok()) as rc, \
             mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            agent.respond(conv, "acme.com")

        rc.assert_called_once()
        we.assert_called_once()
        kinds = [m.kind for m in conv.messages]
        self.assertIn(RESEARCH, kinds)
        self.assertIn(EMAIL, kinds)
        self.assertEqual(conv.messages[-1].content, "Drafted a first email for Acme.")
        self.assertEqual(conv.title, "Acme")           # titled from research
        self.assertEqual(conv.workspace["research"]["status"], "ok")

    def test_followup_reuses_research_for_revision(self):
        # A thread that already has research + an email. "make it shorter" must
        # revise via write_email and must NOT trigger a new research crawl.
        conv = Conversation(title="Acme",
                            workspace={"research": _research_ok(), "email": _email_ok(),
                                       "company": "Acme", "company_url": "https://acme.com"})
        script = [_tool_use("write_email", {"guidance": "make it shorter"}),
                  _final("Shortened it.")]
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        side_effect=script), \
             mock.patch("chat.tools.research_company") as rc, \
             mock.patch("chat.tools.write_email",
                        return_value=_email_ok(body="short.")) as we:
            agent.respond(conv, "make it shorter")

        rc.assert_not_called()                          # research reused, not re-run
        we.assert_called_once()
        _, kwargs = we.call_args
        self.assertEqual(kwargs.get("guidance"), "make it shorter")

    def test_api_error_becomes_a_notice_not_a_crash(self):
        conv = Conversation()
        from services import claude_client
        with mock.patch("chat.agent.claude_client.call_with_tools",
                        side_effect=claude_client.ClaudeClientError("boom")):
            agent.respond(conv, "hi")
        self.assertEqual(conv.messages[-1].role, "assistant")
        self.assertIn("boom", conv.messages[-1].content)

    def test_history_starts_with_user_and_coalesces(self):
        conv = Conversation()
        conv.add(Message(role="assistant", content="hello"))   # leading assistant
        conv.add_user("acme.com")
        conv.add(Message(role="assistant", kind=EMAIL, content="drafted"))
        msgs = agent._history_to_messages(conv)
        self.assertEqual(msgs[0]["role"], "user")              # leading assistant dropped
        # no two adjacent same-role messages
        for a, b in zip(msgs, msgs[1:]):
            self.assertNotEqual(a["role"], b["role"])


# ── Context digests ────────────────────────────────────────────────────
class ContextTests(unittest.TestCase):
    def test_research_digest_lists_facts_and_missing(self):
        text = research_digest(_research_ok())
        self.assertIn("warehouse robots", text)
        self.assertIn("Trusted by DHL", text)
        self.assertIn("NOT found", text)               # pricing/metrics absent
        self.assertIn("pricing", text)

    def test_workspace_state_reuse_instruction(self):
        text = workspace_state_text({"research": _research_ok(), "email": _email_ok()})
        self.assertIn("reuse this", text.lower())
        self.assertIn("CURRENT EMAIL DRAFT", text)

    def test_empty_workspace_state(self):
        text = workspace_state_text({})
        self.assertIn("No research on file", text)


# ── Company resolution (name -> official website) ──────────────────────
class ResolverExtractionTests(unittest.TestCase):
    def test_single_official_site_resolves(self):
        results = [_sr("https://en.wikipedia.org/wiki/Stripe"),
                   _sr("https://stripe.com", "Stripe | Payments"),
                   _sr("https://www.linkedin.com/company/stripe")]
        with mock.patch("chat.resolver.search", return_value=results):
            out = resolver.resolve_company_name("Stripe")
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["url"], "https://stripe.com")

    def test_www_stripped_and_aggregators_excluded(self):
        results = [_sr("https://crunchbase.com/organization/notion"),
                   _sr("https://www.notion.so/product", "Notion")]
        with mock.patch("chat.resolver.search", return_value=results):
            out = resolver.resolve_company_name("Notion")
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["url"], "https://notion.so")   # www + path stripped

    def test_multiple_plausible_matches_ask(self):
        results = [_sr("https://clay.com", "Clay CRM"),
                   _sr("https://clay.earth", "Clay personal network")]
        with mock.patch("chat.resolver.search", return_value=results):
            out = resolver.resolve_company_name("Clay")
        self.assertEqual(out["status"], "choices")
        domains = {c["domain"] for c in out["choices"]}
        self.assertEqual(domains, {"clay.com", "clay.earth"})

    def test_no_provider_configured(self):
        with mock.patch("chat.resolver.search", return_value=None):
            self.assertEqual(resolver.resolve_company_name("X")["status"], "no_provider")

    def test_search_error_is_caught(self):
        with mock.patch("chat.resolver.search", side_effect=RuntimeError("boom")):
            self.assertEqual(resolver.resolve_company_name("X")["status"], "error")

    def test_no_results_is_none(self):
        with mock.patch("chat.resolver.search", return_value=[]):
            self.assertEqual(resolver.resolve_company_name("X")["status"], "none")

    def test_provider_selection_prefers_tavily(self):
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "t", "BRAVE_API_KEY": "b"}):
            self.assertEqual(resolver.provider(), "tavily")
        with mock.patch.dict(os.environ, {"BRAVE_API_KEY": "b"}, clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            self.assertEqual(resolver.provider(), "brave")


class ResolveToolTests(unittest.TestCase):
    def test_url_input_skips_lookup(self):
        conv = Conversation()
        with mock.patch("chat.resolver.resolve_company_name") as rcn:
            result = tools.execute("resolve_company", {"query": "stripe.com"}, conv)
        rcn.assert_not_called()                         # a URL -> no search
        self.assertEqual(result.workspace_updates["company_url"], "https://stripe.com")

    def test_resolves_name_and_caches(self):
        conv = Conversation()
        with mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "resolved",
                                      "url": "https://stripe.com"}) as rcn:
            result = tools.execute("resolve_company", {"query": "Stripe"}, conv)
        rcn.assert_called_once()
        self.assertEqual(result.workspace_updates["company_url"], "https://stripe.com")
        # apply the cache, then a second resolve must NOT search again
        conv.workspace.update(result.workspace_updates)
        with mock.patch("chat.resolver.resolve_company_name") as rcn2:
            again = tools.execute("resolve_company", {"query": "Stripe"}, conv)
        rcn2.assert_not_called()
        self.assertIn("cached", again.summary)

    def test_multiple_matches_ask_user(self):
        conv = Conversation()
        choices = {"status": "choices",
                   "choices": [{"domain": "clay.com", "url": "https://clay.com",
                                "title": "Clay CRM"},
                               {"domain": "clay.earth", "url": "https://clay.earth",
                                "title": "Clay network"}]}
        with mock.patch("chat.resolver.resolve_company_name", return_value=choices):
            result = tools.execute("resolve_company", {"query": "Clay"}, conv)
        self.assertIn("which one", result.summary.lower())
        self.assertIn("clay.com", result.summary)
        self.assertNotIn("company_url", result.workspace_updates)   # nothing committed

    def test_no_provider_degrades_to_guess(self):
        conv = Conversation()
        with mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "no_provider"}):
            result = tools.execute("resolve_company", {"query": "Acme Robotics"}, conv)
        self.assertIn("acmerobotics.com", result.summary)           # best-effort guess
        self.assertIn("TAVILY_API_KEY", result.summary)


class ResolveInAgentLoopTests(unittest.TestCase):
    def test_name_resolves_then_researches_then_drafts(self):
        conv = Conversation()
        script = [_tool_use("resolve_company", {"query": "Stripe"}),
                  _tool_use("research_company", {"query": "https://stripe.com"}, "t2"),
                  _tool_use("write_email", {}, "t3"),
                  _final("Researched Stripe and drafted an email.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.resolver.resolve_company_name",
                        return_value={"status": "resolved", "url": "https://stripe.com"}) as rcn, \
             mock.patch("chat.tools.research_company",
                        return_value=_research_ok("Stripe")) as rc, \
             mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            agent.respond(conv, "Stripe")

        rcn.assert_called_once()
        # research got the RESOLVED url, not a guessed domain
        rc.assert_called_once()
        self.assertEqual(rc.call_args.args[0], "https://stripe.com")
        we.assert_called_once()
        self.assertIn(EMAIL, [m.kind for m in conv.messages])


class PlanOutreachToolTests(unittest.TestCase):
    """The Strategy Agent, wired as a conversation-aware tool. It THINKS — it
    never writes or researches — and never adds a UI card."""

    def test_registered_and_specced(self):
        self.assertIn("plan_outreach", tools.REGISTRY)
        self.assertIn("plan_outreach", {s["name"] for s in tools.tool_specs()})

    def test_decides_from_workspace(self):
        from agents import strategy
        conv = Conversation(workspace={"research": _research_ok()})
        r = tools.execute("plan_outreach", {}, conv)
        self.assertIn(r.workspace_updates["strategy"]["recommended_action"],
                      strategy.ACTIONS)
        self.assertIn("STRATEGY", r.summary)          # internal guidance for the agent
        self.assertIsNone(r.message)                  # no card — no UI change

    def test_empty_workspace_recommends_research(self):
        from agents import strategy
        r = tools.execute("plan_outreach", {}, Conversation())
        self.assertEqual(r.workspace_updates["strategy"]["recommended_action"],
                         strategy.RESEARCH)


class QualifyLeadToolTests(unittest.TestCase):
    """The Lead Qualification Agent, wired as a conversation-aware tool. It JUDGES
    fit — it never writes/researches/sends — and never adds a UI card."""

    def test_registered_and_specced(self):
        self.assertIn("qualify_lead", tools.REGISTRY)
        self.assertIn("qualify_lead", {s["name"] for s in tools.tool_specs()})

    def test_qualifies_from_workspace(self):
        from agents import qualification
        conv = Conversation(workspace={"research": _research_ok(),
                                       "icp": {"industries": ["warehouse"]}})
        r = tools.execute("qualify_lead", {}, conv)
        self.assertIn(r.workspace_updates["qualification"]["recommendation"],
                      qualification.RECOMMENDATIONS)
        self.assertIn("QUALIFICATION", r.summary)     # internal guidance for the agent
        self.assertIsNone(r.message)                  # no card — no UI change

    def test_empty_workspace_recommends_research_more(self):
        from agents import qualification
        r = tools.execute("qualify_lead", {}, Conversation())
        self.assertEqual(r.workspace_updates["qualification"]["recommendation"],
                         qualification.RESEARCH_MORE)


class GuardToolTests(unittest.TestCase):
    """The Deliverability & Cost Guard, wired as a conversation-aware tool. It only
    inspects/scores — never rewrites, researches, or sends — and adds no card."""

    def test_registered_and_specced(self):
        self.assertIn("guard_check", tools.REGISTRY)
        self.assertIn("guard_check", {s["name"] for s in tools.tool_specs()})

    def test_clean_email_allows(self):
        ws = {"email": {"status": "ok", "subject": "Linear for Agents",
                        "body": "Saw you shipped Linear for Agents — the keyboard-first "
                                "flow is sharp. We help eng teams cut triage time "
                                "without losing that workflow; worth a look?",
                        "to": "karri@linear.app", "company": "Linear"}}
        r = tools.execute("guard_check", {}, Conversation(workspace=ws))
        self.assertEqual(r.workspace_updates["guard"]["decision"], "ALLOW")
        self.assertIn("SEND SAFETY", r.summary)
        self.assertIsNone(r.message)

    def test_spammy_email_flagged(self):
        ws = {"email": {"status": "ok", "subject": "ACT NOW!!! FREE",
                        "body": "CLICK HERE buy now!!! Risk free guarantee! Act now limited "
                                "time free offer!!! 100% free — click here!!!"}}
        r = tools.execute("guard_check", {}, Conversation(workspace=ws))
        self.assertIn(r.workspace_updates["guard"]["decision"], ("WARN", "BLOCK"))
        self.assertGreater(r.workspace_updates["guard"]["overallRisk"], 40)


class SendEmailToolTests(unittest.TestCase):
    """send_email really sends via the automation engine + user's Gmail token —
    and is scrupulously honest on every failure branch (never fakes success)."""

    def setUp(self):
        os.environ["AUTOMATION_FORCE_SQLITE"] = "1"
        os.environ["AUTOMATION_ENC_KEY"] = "chat-send-test-key"
        self._dir = tempfile.mkdtemp()
        os.environ["AUTOMATION_DB_PATH"] = os.path.join(self._dir, "auto.db")
        from automation import redis as _r, tokens as _t
        _r.configured = lambda: False                     # offline coordination
        self._tokens = _t.TokenStore(os.path.join(self._dir, "tok.db"))
        _t._default = self._tokens                        # send tool uses this store

    def tearDown(self):
        from automation import tokens as _t
        _t._default = None
        os.environ.pop("AUTOMATION_DB_PATH", None)

    def _conv(self, *, email=True, user="u_send", to=None):
        ws = {"company": "Acme"}
        if email:
            e = _email_ok()
            if to is not None:
                e["to"] = to
            ws["email"] = e
        c = Conversation(workspace=ws)
        c._user_id = user
        return c

    def _connect_gmail(self, user="u_send", email="me@acme.com"):
        import time
        self._tokens.upsert(user_id=user, provider="gmail", account_email=email,
                            access_token="AT", refresh_token="RT",
                            expires_at=time.time() + 9999)

    def test_no_draft_does_not_send(self):
        r = tools.execute("send_email", {}, self._conv(email=False))
        self.assertIn("no finished email", r.summary.lower())
        self.assertFalse(r.workspace_updates)          # nothing recorded, nothing sent

    def test_missing_recipient_asks(self):
        # draft's "to" is a name ("Bob"), not an address
        r = tools.execute("send_email", {}, self._conv())
        self.assertIn("address", r.summary.lower())

    def test_no_user_identity_is_safe(self):
        conv = self._conv(to="bob@acme.com")
        conv._user_id = None
        r = tools.execute("send_email", {}, conv)
        self.assertIn("couldn't confirm", r.summary.lower())

    def test_gmail_not_connected_tells_user_to_connect(self):
        r = tools.execute("send_email", {"to": "bob@acme.com"}, self._conv())
        self.assertIn("connect", r.summary.lower())
        self.assertIn("/connections.html", r.summary)

    def test_real_send_when_connected(self):
        self._connect_gmail()
        conv = self._conv()
        sent = mock.Mock(status_code=200,
                         json=lambda: {"id": "MID123", "threadId": "TID9"})
        with mock.patch("automation.providers.gmail.requests.post", return_value=sent):
            r = tools.execute("send_email", {"to": "bob@acme.com"}, conv)
        self.assertIn("really sent to bob@acme.com", r.summary.lower())
        self.assertEqual(r.workspace_updates["last_send"]["to"], "bob@acme.com")
        self.assertIn(r.workspace_updates["last_send"]["state"],
                      ("SENT", "WAITING", "COMPLETED"))

    def test_guard_blocks_spammy_email_before_send(self):
        # A dangerous email is never sent — the pre-send guard BLOCKs it, and the
        # Gmail provider is never called.
        self._connect_gmail()
        conv = self._conv()
        conv.workspace["email"] = {
            "status": "ok", "subject": "ACT NOW!!! FREE GUARANTEED",
            "body": "CLICK HERE buy now!!! Risk free guarantee! Act now limited time free "
                    "offer!!! 100% free special promotion — click here now!!!",
            "to": "bob@acme.com", "company": "Acme"}
        with mock.patch("automation.providers.gmail.requests.post") as post:
            r = tools.execute("send_email", {"to": "bob@acme.com"}, conv)
        self.assertIn("not sent", r.summary.lower())
        self.assertFalse(post.called)                 # never reached the provider
        self.assertEqual(r.workspace_updates["guard"]["decision"], "BLOCK")

    def test_guard_blocks_recontacting_a_replier_from_live_history(self):
        # Real send history (this user already got a reply from bob) must block a
        # re-contact — the pre-send guard reads it live from the store.
        self._connect_gmail()
        from automation import engine
        from automation.store import WorkflowStore
        st = WorkflowStore()                       # AUTOMATION_DB_PATH set in setUp
        wf = engine.create_workflow(st, "u_send", [
            {"subject": "hi", "body": "Hey Bob."},
            {"subject": "f", "body": "follow up", "delay_days": 3}], to_email="bob@acme.com")
        engine.advance_workflow(st.load(wf.id), st, now=wf.next_run_at,
                                credentials_provider=lambda u, p: None)
        engine.ingest_reply(st, message_id="m1", workflow_id=wf.id, user_id="u_send")
        conv = self._conv()
        conv.workspace["email"] = {
            "status": "ok", "subject": "following up again",
            "body": "Hi Bob, circling back once more on my earlier note about helping "
                    "your team move faster on triage.", "to": "bob@acme.com", "company": "Acme"}
        with mock.patch("automation.providers.gmail.requests.post") as post:
            r = tools.execute("send_email", {"to": "bob@acme.com"}, conv)
        self.assertIn("not sent", r.summary.lower())
        self.assertFalse(post.called)
        self.assertEqual(r.workspace_updates["guard"]["decision"], "BLOCK")

    def test_send_failure_is_reported_honestly(self):
        self._connect_gmail()
        conv = self._conv()
        # Gmail rejects with a permanent 400 -> engine marks it FAILED, tool is honest
        bad = mock.Mock(status_code=400, text="bad request")
        with mock.patch("automation.providers.gmail.requests.post", return_value=bad):
            r = tools.execute("send_email", {"to": "bob@acme.com"}, conv)
        self.assertIn("not sent", r.summary.lower())
        self.assertNotIn("really sent", r.summary.lower())


class StreamingTurnTests(unittest.TestCase):
    """respond_stream must surface the SAME transcript as respond(), but as live
    (event, data) tuples: real tool stages, each card, then a terminal done."""

    def test_stream_emits_steps_cards_and_a_terminal_done(self):
        from chat.tools import ToolResult
        script = [
            {"stop_reason": "tool_use", "text": "",
             "tool_uses": [{"id": "t1", "name": "find_prospects", "input": {}}],
             "assistant_content": [{"type": "text", "text": ""}]},
            {"stop_reason": "end_turn", "text": "HackerRank is the strongest.",
             "tool_uses": [], "assistant_content": [{"type": "text", "text": "x"}]},
        ]

        def fake_execute(name, inp, conv):
            # A tool that streams a REAL stage plus its reasoning (as discovery
            # does through discovery/narration.py) and returns a card.
            prog = getattr(conv, "_progress", None)
            if prog:
                prog("Searching Apollo's company database")
                prog("Most of these are recruiters, dropping them.", "thought")
            return ToolResult(summary="ok", message=Message(
                role="assistant", kind="prospects", content="Found 1.",
                data={"prospects": []}))

        conv = Conversation()
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.agent.tools.execute", side_effect=fake_execute):
            events = list(agent.respond_stream(conv, "find companies hiring an SDR"))

        kinds = [e for e, _ in events]
        self.assertIn("step", kinds)
        self.assertIn("message", kinds)
        self.assertEqual(kinds[-1], "done")            # always terminates cleanly
        labels = [d.get("label") for e, d in events if e == "step"]
        self.assertIn("Searching Apollo's company database", labels)  # tool stage streamed live
        # The WHY streams as its own event kind, so the UI can render it as
        # reasoning rather than as another checklist stage.
        self.assertIn("thought", kinds)
        self.assertIn("Most of these are recruiters, dropping them.",
                      [d.get("label") for e, d in events if e == "thought"])
        cards = [d["message"] for e, d in events
                 if e == "message" and d["message"].get("kind") == "prospects"]
        self.assertEqual(len(cards), 1)
        # The streamed transcript is exactly the persisted one (no drift).
        self.assertEqual([m.kind for m in conv.messages if m.role == "assistant"],
                         ["prospects", "text"])

    def test_stream_and_blocking_produce_the_same_messages(self):
        fake = {"stop_reason": "end_turn", "text": "Two founders stood out.",
                "tool_uses": [], "assistant_content": [{"type": "text", "text": "x"}]}
        with mock.patch("chat.agent.claude_client.call_with_tools", return_value=fake):
            a = Conversation()
            agent.respond(a, "hi")
            b = Conversation()
            list(agent.respond_stream(b, "hi"))
        self.assertEqual([(m.role, m.kind, m.content) for m in a.messages],
                         [(m.role, m.kind, m.content) for m in b.messages])


class DiscoveryRefinementTests(unittest.TestCase):
    """Conversation memory: 'only under 200 employees' refines the LAST search
    instead of starting a new one; a fresh named ICP does not inherit."""

    _LAST = {"raw": "find b2b founders hiring an ai video creator", "industry": "",
             "location": "", "employee_range": "", "funding_stage": "",
             "keywords": [], "exclude_keywords": []}

    def _capture(self, inp, workspace):
        from discovery.engine import DiscoveryResult
        captured = {}

        def fake_discover(owner, q, **kw):
            captured["raw"] = q.raw
            captured["employee_range"] = q.employee_range
            captured["location"] = q.location
            return DiscoveryResult("empty", reason="none", limit=q.limit)

        conv = Conversation()
        conv.workspace.update(workspace)
        conv.add_user(inp.get("query", ""))
        with mock.patch("chat.tools.discovery_engine.discover", side_effect=fake_discover):
            tools.execute("find_prospects", inp, conv)
        return captured

    def test_a_filter_only_turn_refines_the_prior_search(self):
        c = self._capture({"query": "only companies under 200 employees",
                           "employee_range": "<200"},
                          {"discovery_last": self._LAST})
        self.assertIn("ai video creator", c["raw"])     # inherited the role anchor
        self.assertEqual(c["employee_range"], "<200")   # plus the new filter

    def test_a_fresh_named_search_does_not_inherit(self):
        c = self._capture({"query": "find fintech companies hiring an SDR"},
                          {"discovery_last": self._LAST})
        self.assertNotIn("ai video", c["raw"])
        self.assertIn("sdr", c["raw"].lower())

    def test_no_prior_search_means_no_refinement(self):
        c = self._capture({"query": "companies in Europe", "location": "Europe"}, {})
        self.assertNotIn("ai video", c["raw"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
