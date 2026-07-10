"""Guard live-context integration (guard/context.py).

Proves the guard scores REAL production state: it reads a user's durable send
history (via the existing WorkflowStore) and turns it into guard input — prospect
replied/bounced, mailbox reply/bounce/volume, repeated templates — then the guard
blocks the dangerous cases. Offline: in-memory Redis, dry-run provider, temp DB.
The context builder is asserted to be strictly read-only.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# Force the local SQLite backend even when run directly (conftest sets this too).
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation import engine, redis, states  # noqa: E402

redis.configured = lambda: False

from automation.providers.dryrun import DryRunProvider  # noqa: E402
from automation.store import WorkflowStore  # noqa: E402
from guard import assess  # noqa: E402
from guard.context import build_context  # noqa: E402
from guard.models import ALLOW, BLOCK  # noqa: E402

_U = "ctx_user"
_DRAFT = {"subject": "quick note",
          "body": "Hi there, following up on my earlier note about helping teams like "
                  "yours ship faster. Worth a short chat next week?",
          "company": "Acme"}


def _store():
    return WorkflowStore(path=os.path.join(tempfile.mkdtemp(), "ctx.db"))


def _send(store, to, body="Hey, saw your launch — congrats.", steps=2):
    DryRunProvider.reset()
    specs = [{"subject": "hi", "body": body}]
    if steps > 1:
        specs.append({"subject": "f", "body": "follow up", "delay_days": 3})
    wf = engine.create_workflow(store, _U, specs, to_email=to)
    engine.advance_workflow(store.load(wf.id), store, now=wf.next_run_at,
                            credentials_provider=lambda u, p: None)
    return store.load(wf.id)


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def _ctx(self, to, **kw):
        return build_context(_U, email={**_DRAFT, "to": to}, store=self.store, **kw)

    def test_no_user_is_passthrough(self):
        ctx = build_context(None, email={**_DRAFT, "to": "x@y.com"}, store=self.store)
        self.assertEqual(set(ctx.keys()), {"email"})

    def test_replied_prospect_detected(self):
        wf = _send(self.store, "bob@acme.com")
        engine.ingest_reply(self.store, message_id="m1", workflow_id=wf.id, user_id=_U)
        self.assertEqual(self._ctx("bob@acme.com")["prospect"], {"replied": True})

    def test_bounced_prospect_detected(self):
        wf = _send(self.store, "bad@acme.com")
        wf.steps[0].status = states.STEP_FAILED
        wf.steps[0].last_error = "550 hard bounce: no such user"
        self.store.save(wf)
        self.assertTrue(self._ctx("bad@acme.com")["prospect"].get("bounced"))

    def test_unknown_prospect_has_no_state(self):
        _send(self.store, "someone@acme.com")
        self.assertIsNone(self._ctx("stranger@nowhere.com").get("prospect"))

    def test_mailbox_daily_volume_counts_today(self):
        _send(self.store, "a@x.com", steps=1)
        _send(self.store, "b@x.com", steps=1)
        self.assertGreaterEqual(self._ctx("c@x.com")["mailbox"]["daily_volume"], 2)

    def test_rates_reported_above_threshold(self):
        for i in range(6):
            _send(self.store, f"p{i}@x.com", steps=1)
        mb = self._ctx("new@x.com")["mailbox"]
        self.assertIn("reply_rate", mb)
        self.assertIn("bounce_rate", mb)

    def test_rates_hidden_below_threshold(self):
        _send(self.store, "only@x.com", steps=1)
        self.assertNotIn("reply_rate", self._ctx("new@x.com")["mailbox"])

    def test_prior_bodies_captured_for_repetition(self):
        _send(self.store, "bob@acme.com", body="Hey Bob, this exact body was sent before.")
        ctx = self._ctx("bob@acme.com")
        self.assertTrue(ctx["sequence"]["prior_bodies"])

    def test_current_workflow_not_counted_against_itself(self):
        wf = _send(self.store, "bob@acme.com")
        engine.ingest_reply(self.store, message_id="m1", workflow_id=wf.id, user_id=_U)
        # passing the same workflow as "current" excludes it -> no replied flag
        ctx = build_context(_U, email={**_DRAFT, "to": "bob@acme.com"},
                            workflow=self.store.load(wf.id), store=self.store)
        self.assertIsNone(ctx.get("prospect"))

    def test_context_builder_is_read_only(self):
        _send(self.store, "bob@acme.com")
        before = self.store.count_by_state()
        build_context(_U, email={**_DRAFT, "to": "bob@acme.com"}, store=self.store)
        self.assertEqual(self.store.count_by_state(), before)   # no writes


class LiveGuardIntegrationTests(unittest.TestCase):
    """End-to-end: guard.assess on a context built from real history."""

    def setUp(self):
        self.store = _store()

    def test_recontacting_replier_is_blocked(self):
        wf = _send(self.store, "bob@acme.com")
        engine.ingest_reply(self.store, message_id="m1", workflow_id=wf.id, user_id=_U)
        ctx = build_context(_U, email={**_DRAFT, "to": "bob@acme.com"}, store=self.store)
        r = assess(ctx)
        self.assertEqual(r["decision"], BLOCK)
        self.assertTrue(any("replied" in i.lower() for i in r["deliverability"]["issues"]))

    def test_sending_to_bounced_is_blocked(self):
        wf = _send(self.store, "bad@acme.com")
        wf.steps[0].status = states.STEP_FAILED
        wf.steps[0].last_error = "550 hard bounce"
        self.store.save(wf)
        ctx = build_context(_U, email={**_DRAFT, "to": "bad@acme.com"}, store=self.store)
        self.assertEqual(assess(ctx)["decision"], BLOCK)

    def test_repeated_template_raises_risk(self):
        body = "Hey there, this is a specific note about your recent product launch."
        _send(self.store, "bob@acme.com", body=body)
        ctx = build_context(_U, email={"subject": "hi", "body": body, "to": "bob@acme.com"},
                            store=self.store)
        # identical to a prior send -> repetition flagged (and prospect not replied)
        self.assertTrue(any("previous email" in i.lower()
                            for i in assess(ctx)["deliverability"]["issues"]))

    def test_fresh_prospect_clean_copy_allows(self):
        _send(self.store, "history@x.com", steps=1)   # some history, different person
        ctx = build_context(_U, email={
            "subject": "Linear for Agents",
            "body": "Saw you shipped Linear for Agents — keeping the keyboard-first flow "
                    "while adding agent handoffs is sharp. We help eng teams cut triage "
                    "time; worth a look?", "to": "new@prospect.com",
            "company": "Linear"}, store=self.store)
        self.assertEqual(assess(ctx)["decision"], ALLOW)


class TelemetryCostIntegrationTests(unittest.TestCase):
    """LIVE AI cost flows from telemetry.query into the guard's cost verdict.

    The telemetry query layer is mocked so budget thresholds are exact and
    offline; these prove build_context wires real spend into ``usage`` and that
    the guard warns/blocks accordingly — and never blocks on missing data.
    """

    _CLEAN = {"subject": "the picking number",
              "body": "Hey Jane, saw Acme cut picking time 40% in your pilot. We help "
                      "3PLs turn that into a sales story for warehouse buyers; worth a quick look?",
              "to": "jane@acme.com", "company": "Acme"}

    def _ctx(self, *, daily=0.0, monthly=0.0, fail=False, budget_daily="10",
             budget_monthly="200"):
        patches = {
            "daily_spend": mock.Mock(side_effect=RuntimeError) if fail
            else mock.Mock(return_value=daily),
            "monthly_spend": mock.Mock(side_effect=RuntimeError) if fail
            else mock.Mock(return_value=monthly),
            "total_tokens": mock.Mock(return_value=1234),
            "avg_latency": mock.Mock(return_value=800.0),
            "failure_rate": mock.Mock(return_value=0.0),
            "retry_rate": mock.Mock(return_value=0.0),
            "campaign_cost": mock.Mock(return_value=0.0),
            "queue_health": mock.Mock(return_value={"status": "ok"}),
        }
        env = {"GUARD_DAILY_BUDGET_USD": budget_daily,
               "GUARD_MONTHLY_BUDGET_USD": budget_monthly}
        from guard.context import build_context
        with mock.patch.dict(os.environ, env), \
                mock.patch.multiple("telemetry.query", **patches):
            return build_context("u1", email=dict(self._CLEAN),
                                 store=WorkflowStore(
                                     path=os.path.join(tempfile.mkdtemp(), "c.db")))

    def test_live_usage_is_populated(self):
        ctx = self._ctx(daily=3.0, monthly=40.0)
        self.assertEqual(ctx["usage"]["daily_spend"], 3.0)
        self.assertEqual(ctx["usage"]["daily_budget"], 10.0)
        self.assertIn("telemetry", ctx)                       # informational block
        self.assertEqual(ctx["telemetry"]["total_tokens"], 1234)

    def test_daily_budget_warning(self):
        self.assertEqual(assess(self._ctx(daily=8.5))["decision"], "WARN")

    def test_monthly_budget_warning(self):
        self.assertEqual(assess(self._ctx(monthly=185))["decision"], "WARN")

    def test_budget_exceeded_blocks(self):
        r = assess(self._ctx(daily=12))
        self.assertEqual(r["decision"], "BLOCK")
        self.assertEqual(r["cost"]["risk"], "CRITICAL")

    def test_campaign_cost_risk_uses_live_remaining(self):
        # $8 of $10 spent -> $2 remaining; a $5 batch estimate must block.
        ctx = self._ctx(daily=8.0)
        ctx["campaign"] = {"est_cost": 5.0}
        self.assertEqual(assess(ctx)["decision"], "BLOCK")

    def test_telemetry_unavailable_fallback(self):
        ctx = self._ctx(fail=True)
        self.assertNotIn("usage", ctx)                        # omitted, not fabricated
        self.assertIn("cost_omitted", ctx.get("telemetry", {}))
        self.assertEqual(assess(ctx)["decision"], "ALLOW")    # clean email still allowed

    def test_no_false_block_with_zero_spend(self):
        r = assess(self._ctx(daily=0.0, monthly=0.0))
        self.assertEqual(r["decision"], "ALLOW")
        self.assertEqual(r["cost"]["risk"], "LOW")

    def test_budget_disabled_when_zero(self):
        ctx = self._ctx(daily=999, budget_daily="0", budget_monthly="0")
        self.assertNotIn("daily_budget", ctx.get("usage", {}))   # no budget -> no check
        self.assertEqual(assess(ctx)["decision"], "ALLOW")


class SendGateBudgetTests(unittest.TestCase):
    """The send_email pre-send gate must refuse to send on a BLOCK verdict
    (including a cost/budget block), and never claim it sent."""

    def _conv(self):
        from chat.models import Conversation
        c = Conversation(workspace={"email": {"status": "ok", "subject": "hi",
                         "body": "Hey Jane, quick note.", "to": "jane@acme.com"}})
        c._user_id = "gate_user"
        return c

    def test_budget_block_stops_send(self):
        import chat.tools as tools
        block = {"decision": "BLOCK", "overallRisk": 90,
                 "cost": {"risk": "CRITICAL", "issues": ["Daily AI budget exceeded."],
                          "recommendations": []},
                 "deliverability": {"risk": "LOW", "issues": [], "recommendations": []}}
        with mock.patch("chat.tools._guard_assess", return_value=block), \
                mock.patch("chat.tools._tele_event"):
            r = tools.execute("send_email", {"to": "jane@acme.com"}, self._conv())
        self.assertIn("NOT sent", r.summary)
        self.assertEqual(r.workspace_updates["guard"]["decision"], "BLOCK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
