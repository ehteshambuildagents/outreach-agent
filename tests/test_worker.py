"""Background worker + health tests — offline, deterministic.

The worker is exercised via ``run_once`` (the whole loop body without a thread)
plus a real start/stop to prove graceful shutdown. Redis is in-memory; sends use
the dry-run provider; token refresh is mocked.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Self-protect against writing to a real DATABASE_URL when run OUTSIDE pytest
# (conftest.py only loads under pytest). Must precede any automation import.
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"
os.environ["AUTOMATION_ENC_KEY"] = "unit-test-fixed-key"

from automation import engine, health, redis, states  # noqa: E402

redis.configured = lambda: False

from automation.providers.dryrun import DryRunProvider  # noqa: E402
from automation.store import WorkflowStore  # noqa: E402
from automation.tokens import TokenStore  # noqa: E402
from automation.worker import Worker  # noqa: E402

_DAY = 86400.0


def _stores():
    d = tempfile.mkdtemp()
    return WorkflowStore(path=os.path.join(d, "wf.db")), TokenStore(path=os.path.join(d, "tok.db"))


def _steps(n=2):
    return [{"subject": f"e{i}", "body": f"b{i}", "delay_days": 0 if i == 0 else 3}
            for i in range(n)]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        DryRunProvider.reset()
        self.store, self.tokens = _stores()

    def _worker(self, **kw):
        return Worker(self.store, self.tokens, credentials_provider=lambda u, p: None, **kw)

    def test_run_once_advances_due_workflows(self):
        wf = engine.create_workflow(self.store, "u", _steps(2), to_email="b@x.com")
        n = self._worker().run_once(now=wf.next_run_at)
        self.assertEqual(n, 1)
        self.assertEqual(self.store.load(wf.id).state, states.WAITING)
        self.assertEqual(len(DryRunProvider.sent), 1)

    def test_run_once_noop_when_nothing_due(self):
        engine.create_workflow(self.store, "u", _steps(2), to_email="b@x.com")
        self.assertEqual(self._worker().run_once(now=0), 0)   # scheduled in the future

    def test_startup_recovers_crashed_send(self):
        wf = engine.create_workflow(self.store, "u", _steps(1), to_email="b@x.com")
        wf.state = states.SENDING
        wf.steps[0].status = states.STEP_SENDING
        self.store.save(wf)
        w = self._worker().start()
        try:
            # start() runs recover() synchronously before the thread loops
            self.assertIn(self.store.load(wf.id).state, (states.QUEUED, states.WAITING,
                                                         states.COMPLETED))
        finally:
            w.stop()

    def test_graceful_start_stop(self):
        w = self._worker(tick_interval=0.05).start()
        self.assertTrue(w.running)
        time.sleep(0.12)                      # let it tick a couple of times
        w.stop(timeout=5)
        self.assertFalse(w.running)

    def test_maintenance_refreshes_expiring_tokens(self):
        self.tokens.upsert(user_id="u", provider="gmail", account_email="a@x.com",
                           access_token="OLD", refresh_token="RT",
                           expires_at=time.time() - 5)
        w = self._worker(maint_interval=0)     # maintenance every beat
        with mock.patch("automation.oauth.refresh",
                        return_value={"access_token": "NEW", "expires_in": 3600}) as ref:
            w.run_once(now=time.time())
            ref.assert_called_once()
        self.assertEqual(self.tokens.get("u", "gmail")["access_token"], "NEW")

    def test_heartbeat_updates(self):
        w = self._worker()
        w.run_once(now=1234.0)
        self.assertEqual(w.last_tick_at, 1234.0)

    def test_maintenance_arms_missing_gmail_watch(self):
        # Self-heal: a connected Gmail account with NO watch_state gets armed on the
        # maintenance sweep (so an account left dark comes back on its own).
        self.tokens.upsert(user_id="u", provider="gmail", account_email="a@x.com",
                           access_token="AT", refresh_token="RT",
                           expires_at=time.time() + 9999)
        self.assertEqual(len(self.tokens.connected_without_watch("gmail")), 1)
        w = self._worker(maint_interval=0)     # maintenance every beat
        with mock.patch.dict(os.environ, {"GMAIL_PUBSUB_TOPIC": "projects/p/topics/t"}), \
                mock.patch("automation.push.enable_gmail_watch",
                           return_value={"ok": True, "watch": {}}) as arm:
            w.run_once(now=time.time())
        arm.assert_called_once()
        self.assertEqual(arm.call_args.args[1:], ("u", "a@x.com"))

    def test_maintenance_skips_arming_without_pubsub_topic(self):
        # Without a Pub/Sub topic there's nothing to arm against — don't even try
        # (avoids a guaranteed-fail watch() call for every account each sweep).
        self.tokens.upsert(user_id="u", provider="gmail", account_email="a@x.com",
                           access_token="AT", refresh_token="RT",
                           expires_at=time.time() + 9999)
        w = self._worker(maint_interval=0)
        with mock.patch.dict(os.environ, {}), \
                mock.patch("automation.push.enable_gmail_watch") as arm:
            os.environ.pop("GMAIL_PUBSUB_TOPIC", None)
            w.run_once(now=time.time())
        arm.assert_not_called()

    def test_already_watched_account_not_re_armed_as_missing(self):
        # An account that already has a watch is not treated as "missing".
        self.tokens.upsert(user_id="u", provider="gmail", account_email="a@x.com",
                           access_token="AT", refresh_token="RT",
                           expires_at=time.time() + 9999)
        self.tokens.set_watch_state("u", "gmail", "a@x.com", {"history_id": "1"})
        self.assertEqual(self.tokens.connected_without_watch("gmail"), [])
        w = self._worker(maint_interval=0)
        with mock.patch.dict(os.environ, {"GMAIL_PUBSUB_TOPIC": "projects/p/topics/t"}), \
                mock.patch("automation.push.enable_gmail_watch") as arm:
            w.run_once(now=time.time())
        arm.assert_not_called()


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.store, self.tokens = _stores()

    def test_snapshot_reports_all_dependencies(self):
        snap = health.snapshot(store=self.store, token_store=self.tokens, worker=None)
        self.assertIn(snap["status"], ("ok", "degraded"))
        for key in ("database", "redis", "gmail", "outlook", "worker"):
            self.assertIn(key, snap["checks"])

    def test_database_and_redis_ok(self):
        snap = health.snapshot(store=self.store, token_store=self.tokens)
        self.assertEqual(snap["checks"]["database"]["status"], "ok")
        self.assertEqual(snap["checks"]["redis"]["status"], "ok")
        self.assertEqual(snap["checks"]["redis"]["mode"], "in-memory")

    def test_providers_unconfigured_without_oauth_env(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
                                          "MICROSOFT_CLIENT_ID": "", "MICROSOFT_CLIENT_SECRET": ""}):
            snap = health.snapshot(store=self.store, token_store=self.tokens)
        self.assertEqual(snap["checks"]["gmail"]["status"], "unconfigured")
        self.assertEqual(snap["checks"]["outlook"]["status"], "unconfigured")

    def test_worker_states(self):
        self.assertEqual(health._worker_health(None)["status"], "unknown")
        running = mock.Mock(running=True, last_tick_at=0.0)
        self.assertEqual(health._worker_health(running)["status"], "starting")
        fresh = mock.Mock(running=True, last_tick_at=time.time(), tick_interval=15)
        self.assertEqual(health._worker_health(fresh)["status"], "ok")
        stale = mock.Mock(running=True, last_tick_at=time.time() - 9999, tick_interval=15)
        self.assertEqual(health._worker_health(stale)["status"], "stale")
        stopped = mock.Mock(running=False)
        self.assertEqual(health._worker_health(stopped)["status"], "stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
