"""Automation Agent tests — offline, deterministic (no network, no real email).

Every timing input is injected as ``now`` so the state machine is reproducible.
Redis is forced to its in-memory mode and the provider is the dry-run one, so the
full conductor — schedule, send, wait, reply-stop, retry, recover, idempotency,
per-user isolation — is exercised end-to-end without external services.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Self-protect against writing to a real DATABASE_URL when run OUTSIDE pytest
# (conftest.py only loads under pytest). Must precede any automation import.
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

from automation import engine, metrics, redis, scheduler, states  # noqa: E402

# Force in-memory Redis for the whole module. config.settings re-loads .env on
# import (re-setting Upstash creds), so we override `configured` AFTER import
# rather than relying on popping env vars — deterministic and network-free.
redis.configured = lambda: False
from automation.models import Step, Workflow  # noqa: E402
from automation.providers import get_provider  # noqa: E402
from automation.providers.base import ProviderNotConfigured  # noqa: E402
from automation.providers.dryrun import DryRunProvider  # noqa: E402
from automation.store import WorkflowStore  # noqa: E402

_DAY = 86400.0


def _store():
    return WorkflowStore(path=os.path.join(tempfile.mkdtemp(), "wf.db"))


def _steps(n=3):
    return [{"subject": f"e{i}", "body": f"body {i}",
             "delay_days": 0 if i == 0 else 3} for i in range(n)]


class StateMachineTests(unittest.TestCase):
    def test_explicit_transitions_only(self):
        self.assertTrue(states.can_transition(states.QUEUED, states.SENDING))
        self.assertFalse(states.can_transition(states.SENT, states.SENDING))
        with self.assertRaises(states.InvalidTransition):
            states.assert_transition(states.COMPLETED, states.QUEUED)

    def test_terminal_states_have_no_exits(self):
        for t in states.TERMINAL:
            self.assertEqual(states.TRANSITIONS[t], set())


class SchedulerTests(unittest.TestCase):
    def test_schedule_is_cumulative(self):
        steps = [Step(index=i, subject="s", body="b", delay_days=d)
                 for i, d in enumerate([0, 3, 4])]
        scheduler.schedule_steps(steps, 1000.0)
        self.assertEqual(steps[0].scheduled_at, 1000.0)
        self.assertEqual(steps[1].scheduled_at, 1000.0 + 3 * _DAY)
        self.assertEqual(steps[2].scheduled_at, 1000.0 + 7 * _DAY)

    def test_backoff_grows_and_caps(self):
        d0 = scheduler.backoff_delay(0, jitter=False)
        d3 = scheduler.backoff_delay(3, jitter=False)
        self.assertLess(d0, d3)
        self.assertLessEqual(scheduler.backoff_delay(50, jitter=False),
                             scheduler.backoff_delay(50, jitter=False) + 1)

    def test_send_at_respects_explicit_time(self):
        self.assertEqual(scheduler.send_at(100, at_epoch=500), 500)
        self.assertEqual(scheduler.send_at(100, delay_days=1), 100 + _DAY)

    def test_timezone_window(self):
        # 2021-01-01 12:00 UTC -> hour 12 is inside 8..18
        noon = 1609502400
        self.assertTrue(scheduler.within_send_window(noon, "UTC", 8, 18))
        self.assertFalse(scheduler.within_send_window(noon, "UTC", 13, 18))


class RedisFallbackTests(unittest.TestCase):
    def test_set_get_ttl_incr(self):
        redis.set("k", "v", ex=30)
        self.assertEqual(redis.get("k"), "v")
        self.assertTrue(0 < redis.ttl("k") <= 30)
        redis.delete("k")
        self.assertIsNone(redis.get("k"))

    def test_lock_is_exclusive(self):
        self.assertTrue(redis.acquire_lock("wfX", "t1", 30))
        self.assertFalse(redis.acquire_lock("wfX", "t2", 30))
        redis.release_lock("wfX", "t1")
        self.assertTrue(redis.acquire_lock("wfX", "t3", 30))

    def test_dedup_first_wins(self):
        key = "evt-" + os.urandom(4).hex()
        self.assertFalse(redis.seen_before(key))   # first time
        self.assertTrue(redis.seen_before(key))    # duplicate

    def test_rate_limit(self):
        b = "rl-" + os.urandom(4).hex()
        hits = [redis.rate_limited(b, 3, 60) for _ in range(5)]
        self.assertEqual(hits, [False, False, False, True, True])

    def test_rate_limit_arms_ttl_without_a_separate_expire(self):
        """Regression: the counter's TTL must be set atomically with the increment,
        NOT via a second call. Sabotage the separate EXPIRE (simulating a dropped or
        failed round-trip after a successful INCR) and confirm the very first hit
        still leaves a key that carries a TTL — never a stuck, never-expiring counter.
        On the old INCR-then-EXPIRE code this key would have no TTL (ttl == -1)."""
        b = "ttl-" + os.urandom(4).hex()
        key = f"rl:{b}"
        with mock.patch.object(redis, "expire",
                               side_effect=AssertionError("must not rely on a separate EXPIRE")):
            self.assertFalse(redis.rate_limited(b, 3, 60))
        self.assertGreater(redis.ttl(key), 0)

    def test_rate_limit_recovers_after_window(self):
        """A caller that hit the limit is not locked out forever: the key carries a
        TTL, so once the window elapses the counter is gone and sends are allowed
        again."""
        b = "recover-" + os.urandom(4).hex()
        key = f"rl:{b}"
        hits = [redis.rate_limited(b, 3, 60) for _ in range(5)]
        self.assertEqual(hits, [False, False, False, True, True])
        self.assertGreater(redis.ttl(key), 0)          # it WILL expire on its own
        redis.delete(key)                              # simulate the window elapsing
        self.assertFalse(redis.rate_limited(b, 3, 60))  # allowed again — not stuck


class ProviderTests(unittest.TestCase):
    def setUp(self):
        DryRunProvider.reset()

    def test_dryrun_is_idempotent(self):
        p = get_provider("dryrun")
        r1 = p.send(to="a@b.com", subject="s", body="b", idempotency_key="k1")
        r2 = p.send(to="a@b.com", subject="s", body="b", idempotency_key="k1")
        self.assertEqual(r1.message_id, r2.message_id)
        self.assertEqual(len(DryRunProvider.sent), 1)      # only one real send

    def test_gmail_outlook_unconfigured_without_token(self):
        for name in ("gmail", "outlook"):
            p = get_provider(name)
            self.assertEqual(p.health()["status"], "unconfigured")
            with self.assertRaises(ProviderNotConfigured):
                p.send(to="a@b.com", subject="s", body="b", idempotency_key="k")


class EngineFlowTests(unittest.TestCase):
    def setUp(self):
        DryRunProvider.reset()
        metrics.reset()
        redis.reset()          # isolate the per-user send-rate window across tests
        self.s = _store()

    def test_full_sequence_then_complete(self):
        wf = engine.create_workflow(self.s, "u", _steps(2), to_email="b@x.com")
        t0 = wf.next_run_at
        engine.tick(self.s, now=t0)                        # send step 0
        self.assertEqual(self.s.load(wf.id).state, states.WAITING)
        engine.tick(self.s, now=t0 + 3 * _DAY + 1)         # follow-up -> queue step 1
        engine.tick(self.s, now=t0 + 3 * _DAY + 2)         # send step 1 -> complete
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.COMPLETED)
        self.assertEqual(len(DryRunProvider.sent), 2)

    def test_reply_stops_and_no_more_sends(self):
        wf = engine.create_workflow(self.s, "u", _steps(3), to_email="b@x.com")
        t0 = wf.next_run_at
        engine.tick(self.s, now=t0)                        # send step 0
        engine.ingest_reply(self.s, message_id="m1", workflow_id=wf.id, user_id="u")
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.STOPPED)
        engine.tick(self.s, now=t0 + 10 * _DAY)            # must not send more
        self.assertEqual(len(DryRunProvider.sent), 1)
        self.assertEqual(metrics.snapshot()["replies"], 1)

    def _send_next_touch(self, wf_id):
        """Drive the two ticks that carry one follow-up: WAITING -> QUEUED (the
        delay elapses) then QUEUED -> SENT. Times are read back from the workflow so
        the re-anchor-off-actual-send arithmetic never has to be duplicated here."""
        due = self.s.load(wf_id).next_run_at
        engine.tick(self.s, now=due + 1)                   # delay elapsed -> queue it
        due = self.s.load(wf_id).next_run_at
        engine.tick(self.s, now=due + 1)                   # send it

    def test_reply_between_followups_blocks_next_send(self):
        """The exact failure mode: two follow-ups have already gone out, then a reply
        lands. The scheduled THIRD follow-up must never fire — enforced by the
        existing terminal / reply-stop guards, not any new stopping logic."""
        wf = engine.create_workflow(self.s, "u", _steps(5), to_email="b@x.com")
        engine.tick(self.s, now=wf.next_run_at)            # touch 0 (initial)
        self._send_next_touch(wf.id)                       # touch 1 (follow-up 1)
        self._send_next_touch(wf.id)                       # touch 2 (follow-up 2)
        self.assertEqual(len(DryRunProvider.sent), 3)
        self.assertEqual(self.s.load(wf.id).current_index, 2)

        # A reply arrives in the gap before the scheduled third follow-up.
        due_before_reply = self.s.load(wf.id).next_run_at  # when touch 3 WOULD send
        engine.ingest_reply(self.s, message_id="r", workflow_id=wf.id, user_id="u")
        self.assertEqual(self.s.load(wf.id).state, states.STOPPED)

        # Advance to and well past that scheduled time — nothing more may send.
        engine.tick(self.s, now=due_before_reply + 1)
        engine.tick(self.s, now=due_before_reply + 10 * _DAY)
        final = self.s.load(wf.id)
        self.assertEqual(len(DryRunProvider.sent), 3)      # touch 3 never fired
        self.assertEqual(final.steps[3].status, states.STEP_SKIPPED)
        self.assertEqual(final.steps[4].status, states.STEP_SKIPPED)
        self.assertEqual(final.state, states.STOPPED)

    def test_five_touches_no_reply_completes(self):
        """With no reply, all five touches send and the workflow finishes on its own
        (COMPLETED) with nothing left scheduled — it does not sit open forever."""
        wf = engine.create_workflow(self.s, "u", _steps(5), to_email="b@x.com")
        engine.tick(self.s, now=wf.next_run_at)            # touch 0
        for _ in range(4):                                 # touches 1..4
            self._send_next_touch(wf.id)
        final = self.s.load(wf.id)
        self.assertEqual(len(DryRunProvider.sent), 5)
        self.assertEqual(final.state, states.COMPLETED)
        self.assertIsNone(final.next_run_at)
        # A far-future tick finds nothing due and schedules nothing further.
        engine.tick(self.s, now=9_999_999_999)
        self.assertEqual(len(DryRunProvider.sent), 5)
        self.assertEqual(self.s.load(wf.id).state, states.COMPLETED)

    def test_duplicate_reply_webhook_ignored(self):
        wf = engine.create_workflow(self.s, "u", _steps(2), to_email="b@x.com")
        engine.tick(self.s, now=wf.next_run_at)
        engine.ingest_reply(self.s, message_id="dup", workflow_id=wf.id, user_id="u")
        second = engine.ingest_reply(self.s, message_id="dup", workflow_id=wf.id,
                                     user_id="u")
        self.assertIsNone(second)                          # idempotent no-op
        self.assertEqual(metrics.snapshot()["replies"], 1)

    def test_no_match_does_not_burn_idempotency_key(self):
        """Regression: a reply that matches NO workflow (e.g. the wrong connected
        account for a shared mailbox is handled first) must not mark the message
        processed — otherwise the real owner's call short-circuits as a duplicate
        and the sequence is never stopped."""
        wf = engine.create_workflow(self.s, "u", _steps(2), to_email="b@x.com")
        engine.tick(self.s, now=wf.next_run_at)             # send step 0 -> sets thread id
        tid = self.s.load(wf.id).steps[0].provider_thread_id
        self.assertTrue(tid)
        # Wrong user: no match. Under the old order this still claimed "reply:m".
        self.assertIsNone(
            engine.ingest_reply(self.s, message_id="m", user_id="other", thread_id=tid))
        # Right user, SAME message id: the no-match above must not have burned it.
        engine.ingest_reply(self.s, message_id="m", user_id="u", thread_id=tid)
        stopped = self.s.load(wf.id)
        self.assertEqual(stopped.state, states.STOPPED)
        self.assertTrue(stopped.reply_detected)
        self.assertEqual(stopped.reply_message_id, "m")

    def test_force_recovers_completed_workflow(self):
        """Regression: a sequence that ran to COMPLETED because its reply was missed
        must still be recoverable. Default ingest_reply no-ops on a terminal state
        (that's why the recovery tool reported no change); force=True records the
        reply and stops the finished sequence."""
        wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com")
        engine.tick(self.s, now=wf.next_run_at)            # send the only step
        self.assertEqual(self.s.load(wf.id).state, states.COMPLETED)
        tid = self.s.load(wf.id).steps[0].provider_thread_id
        # Default path: terminal guard -> silent no-op.
        engine.ingest_reply(self.s, message_id="r1", workflow_id=wf.id, user_id="u", thread_id=tid)
        still = self.s.load(wf.id)
        self.assertEqual(still.state, states.COMPLETED)
        self.assertFalse(still.reply_detected)
        # Recovery path: force=True records the reply and stops it.
        engine.ingest_reply(self.s, message_id="r2", workflow_id=wf.id, user_id="u",
                            thread_id=tid, force=True)
        r = self.s.load(wf.id)
        self.assertEqual(r.state, states.STOPPED)
        self.assertTrue(r.reply_detected)
        self.assertEqual(r.reply_message_id, "r2")
        self.assertTrue(any(e["type"] == "stopped" for e in self.s.events_for(r.id)))

    def test_retry_then_success(self):
        wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com")
        DryRunProvider.fail_times = 2
        engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.QUEUED)
        self.assertEqual(wf.steps[0].retry_count, 1)
        engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        wf = self.s.load(wf.id)
        engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.COMPLETED)
        self.assertEqual(metrics.snapshot()["retries"], 2)

    def test_permanent_failure_is_terminal_failed(self):
        wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com",
                                    provider="gmail")  # no token -> NotConfigured
        engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.FAILED)
        self.assertEqual(wf.steps[0].retry_count, 1)       # not retried (permanent)

    def test_recover_requeues_sending(self):
        wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com")
        wf.state = states.SENDING
        wf.steps[0].status = states.STEP_SENDING
        self.s.save(wf)
        self.assertEqual(engine.recover(self.s), 1)
        self.assertEqual(self.s.load(wf.id).state, states.QUEUED)

    def test_rate_limit_defers_send(self):
        with mock.patch("automation.engine.AUTOMATION_SEND_RATE_PER_MIN", 0):
            wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com")
            engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        self.assertEqual(len(DryRunProvider.sent), 0)      # deferred, not sent

    def test_cancel_pause_resume(self):
        wf = engine.create_workflow(self.s, "u", _steps(3), to_email="b@x.com")
        engine.pause(self.s, "u", wf.id)
        self.assertEqual(self.s.load(wf.id).state, states.PAUSED)
        engine.resume(self.s, "u", wf.id)
        self.assertIn(self.s.load(wf.id).state, (states.QUEUED, states.READY,
                                                 states.WAITING))
        engine.cancel(self.s, "u", wf.id)
        self.assertEqual(self.s.load(wf.id).state, states.CANCELLED)

    def test_per_user_isolation(self):
        wf = engine.create_workflow(self.s, "alice", _steps(1), to_email="b@x.com")
        self.assertIsNone(self.s.load(wf.id, user_id="bob"))
        # bob's control calls no-op on alice's workflow
        self.assertIsNone(engine.cancel(self.s, "bob", wf.id))
        self.assertEqual(self.s.load(wf.id).state, states.QUEUED)  # untouched


class ApiTests(unittest.TestCase):
    """The HTTP surface: auth-gated, per-user, validated. Uses the real app."""

    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient
        import server.api as api
        cls.api = api
        cls.TestClient = TestClient

    def _client(self, user):
        self.api.app.dependency_overrides[self.api.require_user] = lambda: user
        return self.TestClient(self.api.app)

    def _uid(self):
        return "u_" + os.urandom(5).hex()

    def test_create_list_get_cancel(self):
        u = self._uid()
        c = self._client(u)
        r = c.post("/api/automation/workflows", json={
            "to_email": "bob@acme.com", "company": "Acme", "provider": "dryrun",
            "steps": [{"subject": "hi", "body": "there"}]})
        self.assertEqual(r.status_code, 200)
        wid = r.json()["id"]
        self.assertEqual(r.json()["state"], "QUEUED")
        self.assertIn(wid, [w["id"] for w in c.get("/api/automation/workflows").json()["workflows"]])
        self.assertEqual(c.get(f"/api/automation/workflows/{wid}").status_code, 200)
        self.assertEqual(c.post(f"/api/automation/workflows/{wid}/cancel").json()["state"],
                         "CANCELLED")

    def test_validation_rejects_bad_email_and_empty(self):
        c = self._client(self._uid())
        self.assertEqual(c.post("/api/automation/workflows", json={
            "to_email": "not-an-email", "steps": [{"subject": "s", "body": "b"}]}
        ).status_code, 422)
        self.assertEqual(c.post("/api/automation/workflows", json={
            "to_email": "a@b.com", "steps": []}).status_code, 400)

    def test_per_user_isolation(self):
        a, b = self._uid(), self._uid()
        wid = self._client(a).post("/api/automation/workflows", json={
            "to_email": "x@y.com", "steps": [{"subject": "s", "body": "b"}]}).json()["id"]
        # b cannot read or cancel a's workflow
        self.assertEqual(self._client(b).get(f"/api/automation/workflows/{wid}").status_code, 404)
        self.assertEqual(self._client(b).post(f"/api/automation/workflows/{wid}/cancel").status_code, 404)

    def test_reply_webhook_requires_secret(self):
        c = self._client(self._uid())
        # not configured -> 503 (never an open endpoint)
        self.assertEqual(c.post("/api/automation/reply-webhook",
                                json={"message_id": "m"}).status_code, 503)

    def test_health_endpoint_reports_checks(self):
        c = self._client(self._uid())
        r = c.get("/api/automation/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("checks", r.json())
        self.assertIn("database", r.json()["checks"])

    def test_metrics_endpoint_exposes_rates(self):
        c = self._client(self._uid())
        m = c.get("/api/automation/metrics").json()["metrics"]
        for k in ("reply_rate", "stop_rate", "retry_count", "oauth_failures"):
            self.assertIn(k, m)

    def test_force_complete_via_api(self):
        u = self._uid()
        c = self._client(u)
        wid = c.post("/api/automation/workflows", json={
            "to_email": "b@x.com", "steps": [{"subject": "s", "body": "b"},
                                             {"subject": "s2", "body": "b2"}]}).json()["id"]
        r = c.post(f"/api/automation/workflows/{wid}/force-complete")
        self.assertEqual(r.json()["state"], "COMPLETED")

    def test_dead_letter_and_force_retry_per_user(self):
        a, b = self._uid(), self._uid()
        # a creates a gmail workflow (no token) and drives it to FAILED via run
        wid = self._client(a).post("/api/automation/workflows", json={
            "to_email": "b@x.com", "provider": "gmail",
            "steps": [{"subject": "s", "body": "b"}]}).json()["id"]
        self._client(a).post(f"/api/automation/workflows/{wid}/run")
        self.assertIn(wid, [w["id"] for w in
                            self._client(a).get("/api/automation/dead-letter").json()["workflows"]])
        # b cannot force-retry a's workflow
        self.assertEqual(
            self._client(b).post(f"/api/automation/workflows/{wid}/force-retry").status_code, 404)

    def test_oauth_login_unconfigured_is_503(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            self.assertEqual(c.get("/api/oauth/gmail/login").status_code, 503)

    def test_oauth_login_returns_url_when_configured(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "gid",
                                          "GOOGLE_CLIENT_SECRET": "sec"}):
            r = c.get("/api/oauth/gmail/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("accounts.google.com", r.json()["url"])

    def test_oauth_callback_rejects_bad_state(self):
        c = self._client(self._uid())
        r = c.get("/api/oauth/gmail/callback?code=x&state=forged",
                  follow_redirects=False)
        self.assertEqual(r.status_code, 400)

    def test_oauth_accounts_empty_for_new_user(self):
        c = self._client(self._uid())
        self.assertEqual(c.get("/api/oauth/accounts").json()["accounts"], [])

    def test_unknown_provider_404(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "g", "GOOGLE_CLIENT_SECRET": "s"}):
            self.assertEqual(c.get("/api/oauth/myspace/login").status_code, 404)

    def test_graph_webhook_validation_echo(self):
        c = self._client(self._uid())
        r = c.post("/api/webhooks/graph?validationToken=abc123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "abc123")

    def tearDown(self):
        self.api.app.dependency_overrides.clear()


class AdminRecoveryTests(unittest.TestCase):
    def setUp(self):
        DryRunProvider.reset()
        metrics.reset()
        redis.reset()          # isolate the per-user send-rate window across tests
        self.s = _store()

    def _fail_to_terminal(self, provider="gmail"):
        wf = engine.create_workflow(self.s, "u", _steps(1), to_email="b@x.com",
                                    provider=provider)   # gmail w/o token -> permanent
        engine.advance_workflow(wf, self.s, now=wf.next_run_at)
        return self.s.load(wf.id)

    def test_dead_letter_lists_failed(self):
        wf = self._fail_to_terminal()
        self.assertEqual(wf.state, states.FAILED)
        dl = engine.dead_letter(self.s, "u")
        self.assertIn(wf.id, [w.id for w in dl])
        self.assertEqual(engine.dead_letter(self.s, "other"), [])   # per-user

    def test_force_retry_requeues(self):
        wf = self._fail_to_terminal()
        engine.force_retry(self.s, "u", wf.id)
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.QUEUED)
        self.assertEqual(wf.steps[0].status, states.STEP_QUEUED)
        self.assertIsNone(wf.last_error)

    def test_force_complete_stops_and_completes(self):
        wf = engine.create_workflow(self.s, "u", _steps(3), to_email="b@x.com")
        engine.force_complete(self.s, "u", wf.id)
        wf = self.s.load(wf.id)
        self.assertEqual(wf.state, states.COMPLETED)
        self.assertTrue(all(s.status in (states.STEP_SKIPPED, states.STEP_SENT)
                            for s in wf.steps))

    def test_admin_actions_are_per_user(self):
        wf = self._fail_to_terminal()
        self.assertIsNone(engine.force_retry(self.s, "intruder", wf.id))
        self.assertIsNone(engine.force_complete(self.s, "intruder", wf.id))
        self.assertEqual(self.s.load(wf.id).state, states.FAILED)   # untouched


class StoreTests(unittest.TestCase):
    def test_durable_idempotency_ledger(self):
        s = _store()
        self.assertTrue(s.mark_processed("x"))     # first
        self.assertFalse(s.mark_processed("x"))    # duplicate
        self.assertTrue(s.was_processed("x"))

    def test_due_workflows_excludes_terminal(self):
        s = _store()
        wf = engine.create_workflow(s, "u", _steps(1), to_email="b@x.com")
        engine.cancel(s, "u", wf.id)
        self.assertEqual(s.due_workflows(now=wf.next_run_at + 1), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
