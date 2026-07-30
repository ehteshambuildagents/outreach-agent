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


def _email_ok(subject="warehouse robots", body=None, company="Acme", to="Bob"):
    if body is None:
        body = ("Hey Bob, saw Acme's warehouse robots focus on logistics teams. "
                "Turning that pilot detail into specific account-by-account outreach "
                "feels useful for warehouse buyers; worth a quick look?")
    return {"status": "ok", "subject": subject, "body": body,
            "company": company, "to": to, "used_reveal": False}


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

    def test_a_different_company_is_researched_not_reused(self):
        # #7/#12: HackerRank is on file; "research Apple" must actually research
        # Apple, not reuse HackerRank's research and keep talking about it.
        conv = Conversation(workspace={
            "research": _research_ok("HackerRank"), "company": "HackerRank",
            "company_url": "https://hackerrank.com"})
        with mock.patch("chat.tools.research_company",
                        return_value=_research_ok("Apple")) as rc:
            result = tools.execute("research_company", {"query": "Apple"}, conv)
        rc.assert_called_once()                              # Apple was researched
        self.assertEqual(result.workspace_updates["company"], "Apple")

    def test_switching_back_reuses_cached_research(self):
        # After researching Apple over a HackerRank thread, going BACK to HackerRank
        # reuses the cached research instead of paying to crawl it again.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com",
            "research_cache": {"hackerrank.com": _research_ok("HackerRank")}})
        with mock.patch("chat.tools.research_company") as rc:
            result = tools.execute("research_company",
                                   {"query": "hackerrank.com"}, conv)
        rc.assert_not_called()                               # served from cache
        self.assertEqual(result.workspace_updates["company"], "HackerRank")

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

    # A realistic writer stand-in: the draft it returns is for the company named in
    # the research SOURCE it was handed (exactly like agents.writer.write_email,
    # which reads company_name off the source data). This is what makes the target
    # invariant meaningful in tests — a wrong source produces a wrong-company draft.
    @staticmethod
    def _writer_for_source():
        def _w(source, **kw):
            name = ((source or {}).get("data") or {}).get("company_name") or "Unknown"
            return _email_ok(subject=f"{name} hook", company=name,
                             to=(source["data"].get("primary_contact_name") or "there"))
        return _w

    def test_research_apple_then_email_anthropic_refuses(self):
        # #29: research Apple, then ask for an email to Anthropic (never researched).
        # The writer must NOT run; no wrong-company draft; agent told to research.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com"})
        with mock.patch("chat.tools.write_email") as we:
            result = tools.execute("write_email", {"company": "Anthropic"}, conv)
        we.assert_not_called()
        self.assertIsNone(result.message)
        self.assertIn("anthropic", result.summary.lower())
        self.assertIn("research", result.summary.lower())
        self.assertEqual(conv.workspace["company"], "Apple")   # unchanged

    def test_write_retargets_to_a_named_cached_company(self):
        # The user asks to write for Anthropic while Apple is the active company.
        # Anthropic was researched earlier (cached), so the writer is given
        # ANTHROPIC's research, the draft is for Anthropic, and the active company
        # switches — the "asked for Anthropic, drafted Apple" bug.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com",
            "intel": _intel_ok(), "email": _email_ok(),
            "research_cache": {"anthropic.com": _research_ok("Anthropic")}})
        captured = {}
        def capture(source, **kw):
            captured["source"] = source
            return self._writer_for_source()(source, **kw)
        with mock.patch("chat.tools.write_email", side_effect=capture) as we:
            result = tools.execute("write_email", {"company": "Anthropic"}, conv)
        we.assert_called_once()
        self.assertEqual(captured["source"]["data"]["company_name"], "Anthropic")
        self.assertEqual(result.message.kind, EMAIL)
        self.assertEqual(result.message.data["company"], "Anthropic")   # card matches
        # The switch persists and stale artifacts from Apple are cleared.
        self.assertEqual(result.workspace_updates["company"], "Anthropic")
        self.assertIsNone(result.workspace_updates["intel"])
        self.assertEqual(conv.workspace["company_url"], "https://anthropic.com")

    def test_research_anthropic_switch_to_apple_then_anthropic_again(self):
        # #30: research Anthropic, switch active to Apple, then ask for Anthropic
        # again. Both are cached; the second ask must retarget back to Anthropic and
        # draft for Anthropic, never for the currently-active Apple.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com",
            "research_cache": {"anthropic.com": _research_ok("Anthropic"),
                               "apple.com": _research_ok("Apple")}})
        with mock.patch("chat.tools.write_email",
                        side_effect=self._writer_for_source()):
            result = tools.execute("write_email", {"company": "Anthropic"}, conv)
        self.assertEqual(result.message.data["company"], "Anthropic")
        self.assertEqual(conv.workspace["company"], "Anthropic")

    def test_cached_target_switching_draws_the_right_research(self):
        # Cached target switching: the writer receives the CACHED research for the
        # requested company, not the active one's.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com",
            "research_cache": {"anthropic.com": _research_ok("Anthropic")}})
        captured = {}
        def capture(source, **kw):
            captured["name"] = source["data"]["company_name"]
            return self._writer_for_source()(source, **kw)
        with mock.patch("chat.tools.write_email", side_effect=capture):
            tools.execute("write_email", {"company": "anthropic.com"}, conv)
        self.assertEqual(captured["name"], "Anthropic")

    def test_write_refuses_a_company_that_was_never_researched(self):
        # Unknown company: named target isn't active AND has no research on file: the
        # writer must NOT be called (no wrong-company draft); agent told to research.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com"})
        with mock.patch("chat.tools.write_email") as we:
            result = tools.execute("write_email", {"company": "Netflix"}, conv)
        we.assert_not_called()
        self.assertIsNone(result.message)
        self.assertIn("netflix", result.summary.lower())
        self.assertIn("research", result.summary.lower())
        self.assertEqual(conv.workspace["company"], "Apple")   # unchanged

    def test_write_reports_writer_failure_without_a_draft(self):
        # Research failure path from the writer: a non-ok result yields no card and
        # never claims success.
        conv = Conversation(workspace={"research": _research_ok("Acme"),
                                       "company": "Acme",
                                       "company_url": "https://acme.com"})
        with mock.patch("chat.tools.write_email",
                        return_value={"status": "insufficient",
                                      "reason": "not enough detail"}):
            result = tools.execute("write_email", {"company": "acme.com"}, conv)
        self.assertIsNone(result.message)
        self.assertIn("could not", result.summary.lower())

    def test_explicit_company_overrides_stale_active_company(self):
        # #34: explicit company with a stale active_company. Active is Apple but the
        # user names Anthropic (cached) — the explicit target wins.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com", "email": _email_ok(company="Apple"),
            "research_cache": {"anthropic.com": _research_ok("Anthropic")}})
        with mock.patch("chat.tools.write_email",
                        side_effect=self._writer_for_source()):
            result = tools.execute("write_email", {"company": "Anthropic"}, conv)
        self.assertEqual(result.message.data["company"], "Anthropic")
        self.assertEqual(result.workspace_updates["email"]["company"], "Anthropic")

    def test_narration_summary_names_the_actual_draft_company(self):
        # #35: the narration handed to the model names the ACTUAL drafted company, so
        # the prose cannot claim a different one than the card shows.
        conv = Conversation(workspace={
            "research": _research_ok("Apple"), "company": "Apple",
            "company_url": "https://apple.com",
            "research_cache": {"anthropic.com": _research_ok("Anthropic")}})
        with mock.patch("chat.tools.write_email",
                        side_effect=self._writer_for_source()):
            result = tools.execute("write_email", {"company": "Anthropic"}, conv)
        self.assertIn("Anthropic", result.summary)
        self.assertNotIn("Apple", result.summary)

    def test_invariant_blocks_a_wrong_company_draft(self):
        # #37: if the writer somehow returns a draft for a DIFFERENT company than the
        # target, the tool must emit NO email card and preserve research.
        conv = Conversation(workspace={"research": _research_ok("Acme"),
                                       "company": "Acme",
                                       "company_url": "https://acme.com"})
        # Writer returns an Apple draft while the target is Acme (a writer bug).
        with mock.patch("chat.tools.write_email",
                        return_value=_email_ok(company="Apple")):
            result = tools.execute("write_email", {"company": "acme.com"}, conv)
        self.assertIsNone(result.message)
        self.assertNotIn("email", result.summary.lower().split("wrong-company")[0])
        self.assertIn("blocked", result.summary.lower())
        # Research preserved; only the bad draft dropped.
        self.assertIsNone(result.workspace_updates.get("email"))
        self.assertEqual(conv.workspace["research"]["data"]["company_name"], "Acme")

    def test_write_with_matching_company_drafts_normally(self):
        # Passing the company that is ALREADY on file must not trigger a retarget.
        conv = Conversation(workspace={"research": _research_ok("Acme"),
                                       "company": "Acme",
                                       "company_url": "https://acme.com"})
        with mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            result = tools.execute("write_email", {"company": "acme.com"}, conv)
        we.assert_called_once()
        self.assertEqual(result.message.kind, EMAIL)


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

    def test_repetitive_sequence_steps_are_auto_repaired(self):
        # #9 gap: a repetitive multi-email batch must be REPAIRED, not just warned
        # about. write_sequence returns two identical steps; the repeated one is
        # regenerated (via write_email) into a distinct draft, so the final batch
        # is no longer flagged as repetitive.
        from agents import writer_review
        rep = {"status": "ok", "company": "Acme", "to": "Bob", "emails": [
            {"step": 1, "angle": "hook", "delay_days": 0, "subject": "s1",
             "body": "Hey Bob, saw Acme's warehouse robots for logistics teams. "
                     "Worth a quick look at how we help?"},
            {"step": 2, "angle": "value", "delay_days": 3, "subject": "s2",
             "body": "Hey Bob, saw Acme's warehouse robots for logistics teams. "
                     "Worth a quick look at how we help?"}]}
        distinct = _email_ok(
            body="Following up with a different angle: the pilot numbers you shared "
                 "point to a real timing window this quarter. Open to comparing notes?")
        conv = Conversation(workspace={"research": _research_ok()})
        with mock.patch("chat.tools.write_sequence", return_value=rep), \
             mock.patch("chat.tools.write_email", return_value=distinct) as we:
            r = tools.execute("write_email", {"mode": "sequence"}, conv)
        we.assert_called()                                   # repair regenerated a step
        bodies = [e["body"] for e in r.workspace_updates["sequence"]]
        _worst, pair = writer_review.batch_distinctiveness(bodies)
        self.assertIsNone(pair, "sequence still repetitive after auto-repair")

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
             mock.patch("chat.tools.write_email",
                        return_value=_email_ok(company="Stripe")) as we:
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
                prog("Searching company databases")
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
        self.assertIn("Searching company databases", labels)  # tool stage streamed live
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


class WriteEmailInvariantTests(unittest.TestCase):
    """#8: when the assistant PROMISES an email, the turn must hand back a draft
    (or a concrete ask), never a promise with nothing behind it."""

    def test_promised_email_is_fulfilled_when_model_forgets_the_tool(self):
        # The model answers directly, committing to write, but never calls the tool.
        conv = Conversation(workspace={"research": _research_ok()})
        script = [_final("Sure. Let me draft the email for you so you can see it.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            agent.respond(conv, "write a cold email for Acme")
        we.assert_called_once()                         # invariant drove the writer
        self.assertIn(EMAIL, [m.kind for m in conv.messages])   # user got the draft

    def test_bare_promise_fulfils_when_user_asked_for_an_email(self):
        # The exact reported phrasing: "let me write it anyway" with no email word,
        # but the user's message this turn was clearly about an email.
        conv = Conversation(workspace={"research": _research_ok()})
        script = [_final("Let me write it anyway so you can see what it looks like.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            agent.respond(conv, "draft the cold email")
        we.assert_called_once()
        self.assertIn(EMAIL, [m.kind for m in conv.messages])

    def test_promised_email_without_a_company_asks_instead_of_going_silent(self):
        conv = Conversation()                           # nothing to write about
        script = [_final("Okay, let me write the email now.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.tools.write_email") as we:
            agent.respond(conv, "draft the email")
        we.assert_not_called()                          # no source -> writer not run
        self.assertNotIn(EMAIL, [m.kind for m in conv.messages])
        self.assertIn("which company", conv.messages[-1].content.lower())

    def test_offer_to_draft_is_not_a_surprise_email(self):
        # A QUESTION offering to draft must not auto-trigger the writer.
        conv = Conversation(workspace={"research": _research_ok()})
        script = [_final("Acme looks like a strong fit. Want me to draft an opener?")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.tools.write_email") as we:
            agent.respond(conv, "is acme a good prospect?")
        we.assert_not_called()
        self.assertNotIn(EMAIL, [m.kind for m in conv.messages])

    def test_email_written_via_tool_does_not_double_write(self):
        # When the model DOES call write_email, the invariant must not fire again.
        conv = Conversation(workspace={"research": _research_ok()})
        script = [_tool_use("write_email", {}),
                  _final("Drafted the email. Want a shorter version?")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.tools.write_email", return_value=_email_ok()) as we:
            agent.respond(conv, "draft the email")
        we.assert_called_once()                         # exactly one draft, not two
        self.assertEqual([m.kind for m in conv.messages].count(EMAIL), 1)


class DiscoveryBandTests(unittest.TestCase):
    """#6: discovered cards carry an honest Strong/Possible/Weak band, and a
    company hiring for a DIFFERENT role than asked is never Strong or recommended."""

    def _entry(self, confidence, *, tier="company", hiring=None):
        from chat.research_pipeline import discovery_entries
        p = {"company_name": "Acme", "website": "https://acme.com",
             "confidence": confidence, "tier": tier, "hiring": hiring}
        return discovery_entries([p])[0]

    def test_low_confidence_is_weak_not_recommended(self):
        e = self._entry(0.22)
        self.assertEqual(e["band"], "weak")
        self.assertFalse(e["recommended"])

    def test_high_confidence_role_match_is_strong(self):
        e = self._entry(0.72, hiring={"verified": True, "match": "role"})
        self.assertEqual(e["band"], "strong")
        self.assertTrue(e["recommended"])

    def test_hiring_a_different_role_caps_at_possible(self):
        # Exactly the reported bug: a strong-looking company whose only hiring
        # signal is a non-matching role must not be presented as a strong match.
        e = self._entry(0.72, hiring={"verified": True, "match": "any"})
        self.assertEqual(e["band"], "possible")
        self.assertFalse(e["recommended"])

    def test_fallback_source_is_always_weak(self):
        e = self._entry(0.9, tier="fallback")
        self.assertEqual(e["band"], "weak")
        self.assertFalse(e["recommended"])

    def test_off_role_hiring_is_rejected_as_a_signal_not_recommended(self):
        # #4: a company whose only hiring signal is a DIFFERENT role must not be
        # recommended and must not be labelled Strong (the signal is rejected).
        e = self._entry(0.6, hiring={"verified": True, "match": "any",
                                     "summary": "Hiring a content creator"})
        self.assertFalse(e["recommended"])
        self.assertNotEqual(e["band"], "strong")

    def test_malformed_prospects_are_dropped(self):
        # Scraped job-posting fragments / nameless rows must never render as a
        # prospect. Only the one real company survives.
        from chat.research_pipeline import discovery_entries
        rows = [
            {"company_name": "We're hiring a Senior SDR", "website": "https://x.com",
             "confidence": 0.6},                       # a job posting, not a company
            {"company_name": "", "website": "https://y.com", "confidence": 0.6},  # no name
            {"company_name": "Acme", "website": "", "confidence": 0.6},           # no site
            {"company_name": "Acme", "website": "not-a-url", "confidence": 0.6},  # junk site
            {"company_name": "RealCo", "website": "https://realco.com",
             "confidence": 0.6},                       # the only valid one
        ]
        entries = discovery_entries(rows)
        self.assertEqual([e["company"] for e in entries], ["RealCo"])

    def test_live_qa_junk_rows_are_dropped(self):
        # Exact regression for the two malformed rows found during live QA: a
        # category phrase and a job title, each with a valid-looking domain and a
        # short name. Both must be dropped; only the real company survives.
        from chat.research_pipeline import discovery_entries
        rows = [
            {"company_name": "B2B SaaS",
             "website": "https://dover.com/careers/123", "confidence": 0.6},
            {"company_name": "Senior SDR Software Engineer",
             "website": "https://tcibr.com/job/45", "confidence": 0.6},
            {"company_name": "Acme", "website": "https://acme.com",
             "confidence": 0.6},
        ]
        entries = discovery_entries(rows)
        self.assertEqual([e["company"] for e in entries], ["Acme"])

    def test_category_and_role_phrases_are_rejected(self):
        # Generic category names, role titles, job-board headings and query
        # fragments are not companies, regardless of a valid domain.
        from chat.research_pipeline import _malformed_prospect
        for name in ["B2B SaaS", "SaaS companies", "Software company",
                     "Startups hiring SDRs", "Tech startups", "AI companies",
                     "Senior SDR Software Engineer",
                     "Sales Development Representative", "Account Executive",
                     "Growth Marketing Manager", "Software Engineer",
                     "Careers at Google"]:
            self.assertTrue(
                _malformed_prospect({"company_name": name,
                                     "website": "https://example.com/x"}),
                f"expected {name!r} to be rejected as a non-company")

    def test_legit_companies_are_not_over_rejected(self):
        # A distinctive/brand token, or a legal-entity suffix, keeps a real
        # company even when the name CONTAINS Careers/Hiring/Software/Sales.
        from chat.research_pipeline import _malformed_prospect
        for name in ["Software AG", "Career Karma", "Greenhouse Software",
                     "Sales Layer", "HubSpot", "Salesforce", "Outreach",
                     "Acme Inc", "Digital Ocean", "The Trade Desk", "Gong",
                     "Salesloft", "Front"]:
            self.assertFalse(
                _malformed_prospect({"company_name": name,
                                     "website": "https://example.com"}),
                f"expected {name!r} to be kept as a real company")


class DecisionMakerTests(unittest.TestCase):
    """#14: a person is surfaced as a decision-maker ONLY when their role matches an
    approved buying persona. A named employee is never treated as authority just by
    existing; an irrelevant role or a missing role yields None."""

    def _dm(self, data):
        from chat.research_pipeline import _decision_maker
        return _decision_maker(data)

    def test_relevant_founder(self):
        self.assertEqual(
            self._dm({"primary_contact_name": "Jane Doe",
                      "primary_contact_role": "Co-Founder"}),
            {"name": "Jane Doe", "role": "Co-Founder"})

    def test_relevant_sales_leader(self):
        self.assertEqual(
            self._dm({"team_members": [{"name": "Sam Ray", "role": "VP of Sales"}]}),
            {"name": "Sam Ray", "role": "VP of Sales"})

    def test_irrelevant_engineer_is_not_a_decision_maker(self):
        self.assertIsNone(
            self._dm({"primary_contact_name": "Ada L", "primary_contact_role": "Staff Software Engineer"}))

    def test_recruiter_is_not_a_decision_maker(self):
        # "Sales Recruiter" brushes the sales pattern but must be excluded.
        self.assertIsNone(
            self._dm({"team_members": [{"name": "Rick R", "role": "Sales Recruiter"}]}))

    def test_missing_role_is_not_verified(self):
        self.assertIsNone(self._dm({"primary_contact_name": "Bob Vance"}))

    def test_picks_the_relevant_person_not_the_first_listed(self):
        dm = self._dm({"team_members": [
            {"name": "Ada L", "role": "Software Engineer"},
            {"name": "Grace H", "role": "Head of Sales"}]})
        self.assertEqual(dm, {"name": "Grace H", "role": "Head of Sales"})

    def test_no_verified_person_returns_none(self):
        self.assertIsNone(self._dm({"what_they_do": "robots"}))

    def test_ceo_primary_contact_qualifies(self):
        self.assertEqual(
            self._dm({"primary_contact_name": "Bob Vance", "primary_contact_role": "CEO"}),
            {"name": "Bob Vance", "role": "CEO"})


class ActiveTargetIndicatorTests(unittest.TestCase):
    """#12 A/B: the conversation payload exposes the TRUE active target, and it
    updates the instant the target changes (so the 'Researching: X' chip is tied to
    real state, not guessed from messages)."""

    def test_active_company_is_exposed_and_updates_on_target_change(self):
        from server.api import _conversation_public
        conv = Conversation(workspace={"company": "HackerRank"})
        self.assertEqual(_conversation_public(conv)["active_company"], "HackerRank")
        conv.workspace["company"] = "Apple"          # explicit switch
        self.assertEqual(_conversation_public(conv)["active_company"], "Apple")

    def test_active_company_is_none_before_any_target(self):
        from server.api import _conversation_public
        self.assertIsNone(_conversation_public(Conversation())["active_company"])


class TurnResilienceTests(unittest.TestCase):
    """#5: one failing tool must never leave the turn hanging after 'thinking'.
    The turn always reaches a terminal message, prior work survives, and the
    conversation is persisted so a refresh doesn't lose it."""

    def test_a_tool_that_raises_does_not_sink_the_turn(self):
        conv = Conversation()
        script = [_tool_use("research_company", {"query": "acme.com"}),
                  _final("Here's what I can do without that step.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.agent.tools.execute",
                        side_effect=RuntimeError("boom")):
            agent.respond(conv, "research acme.com")     # must NOT raise
        # Ends on the model's clean wrap-up, with a visible notice about the failure.
        self.assertEqual(conv.messages[-1].content,
                         "Here's what I can do without that step.")
        self.assertTrue(any(m.kind == "notice" for m in conv.messages))

    def test_stream_terminates_with_done_even_when_a_tool_raises(self):
        conv = Conversation()
        script = [_tool_use("research_company", {"query": "acme.com"}),
                  _final("Wrapped up.")]
        with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
             mock.patch("chat.agent.tools.execute", side_effect=RuntimeError("boom")):
            events = list(agent.respond_stream(conv, "research acme.com"))
        self.assertEqual(events[-1][0], "done")          # always terminates cleanly
        notices = [d["message"] for e, d in events
                   if e == "message" and d["message"].get("kind") == "notice"]
        self.assertTrue(notices)                         # the failure was surfaced

    def test_turn_is_persisted_even_when_a_tool_raises(self):
        with tempfile.TemporaryDirectory() as d:
            store = ConversationStore(directory=d)
            conv = Conversation()
            script = [_tool_use("research_company", {"query": "acme.com"}),
                      _final("Saved anyway.")]
            with mock.patch("chat.agent.claude_client.call_with_tools", side_effect=script), \
                 mock.patch("chat.agent.tools.execute", side_effect=RuntimeError("boom")):
                agent.respond(conv, "research acme.com", store=store)
            reloaded = store.load(conv.id)               # survives a "refresh"
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.messages[-1].content, "Saved anyway.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
