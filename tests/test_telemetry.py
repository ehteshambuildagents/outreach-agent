"""Telemetry & Observability tests — unit, integration, failure, concurrency, idempotency.

Runs offline with synchronous telemetry (conftest sets TELEMETRY_SYNC=1) against a
fresh temp SQLite DB per test. Asserts the guarantees the spec demands: exact
provider-token cost (never estimated), never-raises on failure, idempotent inserts,
and correctness under concurrency.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telemetry  # noqa: E402
from telemetry import pricing, query, recorder  # noqa: E402
from telemetry import schema as tschema  # noqa: E402
from telemetry import sink as tsink  # noqa: E402


def _fresh_db():
    os.environ["AUTOMATION_FORCE_SQLITE"] = "1"
    os.environ["TELEMETRY_SYNC"] = "1"
    os.environ.pop("TELEMETRY_DISABLED", None)
    os.environ["AUTOMATION_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "tele.db")
    tsink._sink = None            # rebind the sink to the new DB path
    tschema.reset_ensured()


class PricingTests(unittest.TestCase):
    def test_cost_from_real_tokens(self):
        # opus: 1M input @ $15 + 1M output @ $75
        self.assertAlmostEqual(pricing.cost("claude-opus-4-8", 1_000_000, 1_000_000), 90.0, 4)

    def test_cost_unavailable_when_no_tokens(self):
        self.assertIsNone(pricing.cost("claude-opus", None, None))

    def test_model_matched_by_substring(self):
        self.assertEqual(pricing.rate("claude-sonnet-4-6"), (3.0, 15.0))


class RecorderQueryTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_ai_request_cost_and_tokens(self):
        with telemetry.scope(user_id="u1", agent="writer", campaign_id="c1"):
            recorder.record_ai_request(provider="anthropic", model="claude-opus-4-8",
                                       prompt_tokens=1000, completion_tokens=500,
                                       latency_ms=1200)
        self.assertGreater(query.daily_spend("u1"), 0)
        self.assertEqual(query.total_tokens("u1"), 1500)
        self.assertEqual(query.agent_cost("writer"), query.daily_spend("u1"))
        self.assertEqual(query.campaign_cost("c1"), query.daily_spend("u1"))

    def test_unavailable_tokens_recorded_not_faked(self):
        recorder.record_ai_request(provider="anthropic", model="claude-haiku",
                                   success=False, failure_reason="RateLimitError")
        db = query._db()
        row = db.query_one("SELECT total_tokens, cost_basis, success FROM ai_requests")
        self.assertIsNone(row["total_tokens"])
        self.assertEqual(row["cost_basis"], "unavailable")
        self.assertEqual(row["success"], 0)

    def test_failure_and_retry_rates(self):
        recorder.record_ai_request(provider="anthropic", model="m", prompt_tokens=1,
                                   completion_tokens=1, retries=2, success=True)
        recorder.record_ai_request(provider="anthropic", model="m", success=False)
        self.assertEqual(query.failure_rate(), 0.5)
        self.assertEqual(query.retry_rate(), 0.5)

    def test_avg_latency(self):
        recorder.record_ai_request(provider="a", model="m", prompt_tokens=1,
                                   completion_tokens=1, latency_ms=100)
        recorder.record_ai_request(provider="a", model="m", prompt_tokens=1,
                                   completion_tokens=1, latency_ms=300)
        self.assertEqual(query.avg_latency(), 200.0)

    def test_agent_run_and_events(self):
        with telemetry.track_agent("qualify_lead", user_id="u1"):
            pass
        db = query._db()
        self.assertEqual(int(query._scalar(db, "SELECT COUNT(*) FROM agent_runs")), 1)
        recorder.record_event("email", "sent", user_id="u1")
        recorder.record_event("email", "replied", user_id="u1")
        self.assertEqual(query.reply_rate("u1"), 1.0)

    def test_guard_stats(self):
        recorder.record_event("email", "guard_blocked", user_id="u1")
        recorder.record_event("email", "sent", user_id="u1")
        self.assertEqual(query.guard_stats("u1"), {"blocked": 1, "duplicate_prevented": 0, "sent": 1})

    def test_summary_shape(self):
        with telemetry.scope(user_id="u1"):
            recorder.record_ai_request(provider="a", model="m", prompt_tokens=10,
                                       completion_tokens=10, latency_ms=50)
        s = query.summary("u1")
        for k in ("daily_spend", "monthly_spend", "total_tokens", "avg_latency_ms",
                  "failure_rate", "retry_rate", "guard", "reply_rate", "bounce_rate"):
            self.assertIn(k, s)


class ContextTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_scope_attributes_and_nests(self):
        with telemetry.scope(user_id="u1", agent="chat"):
            self.assertEqual(telemetry.current()["agent"], "chat")
            with telemetry.scope(agent="writer"):
                self.assertEqual(telemetry.current()["agent"], "writer")
                self.assertEqual(telemetry.current()["user_id"], "u1")   # inherited
            self.assertEqual(telemetry.current()["agent"], "chat")       # restored


class LlmSpanTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_span_reads_usage_off_response(self):
        span = telemetry.llm_span("anthropic", "claude-opus-4-8")
        span.counted(lambda: None)()          # one attempt
        fake = mock.Mock(model="claude-opus-4-8",
                         usage=mock.Mock(input_tokens=200, output_tokens=100,
                                         cache_read_input_tokens=0))
        span.done(fake)
        row = query._db().query_one("SELECT prompt_tokens, completion_tokens, success FROM ai_requests")
        self.assertEqual((row["prompt_tokens"], row["completion_tokens"], row["success"]), (200, 100, 1))

    def test_span_failure_recorded(self):
        span = telemetry.llm_span("anthropic", "claude-opus-4-8")
        span.failed(RuntimeError("boom"))
        row = query._db().query_one("SELECT success, failure_reason FROM ai_requests")
        self.assertEqual((row["success"], row["failure_reason"]), (0, "RuntimeError"))


class FailurePathTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_recorder_never_raises_on_sink_error(self):
        with mock.patch("telemetry.recorder.emit", side_effect=RuntimeError("db down")):
            # must swallow and return "" rather than propagate
            self.assertEqual(recorder.record_ai_request(provider="a", model="m"), "")
            self.assertEqual(recorder.record_event("email", "sent"), "")

    def test_disabled_flag_drops_everything(self):
        with mock.patch.dict(os.environ, {"TELEMETRY_DISABLED": "1"}):
            recorder.record_ai_request(provider="a", model="m", prompt_tokens=5,
                                       completion_tokens=5)
            self.assertEqual(int(query._scalar(query._db(), "SELECT COUNT(*) FROM ai_requests")), 0)

    def test_schema_ensure_never_raises(self):
        broken = mock.Mock()
        broken.executescript.side_effect = RuntimeError("no db")
        tschema.reset_ensured()
        tschema.ensure(broken)     # must not raise


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_same_request_id_recorded_once(self):
        for _ in range(3):
            recorder.record_ai_request(provider="a", model="m", prompt_tokens=1,
                                       completion_tokens=1, request_id="fixed-id")
        n = int(query._scalar(query._db(), "SELECT COUNT(*) FROM ai_requests"))
        self.assertEqual(n, 1)      # ON CONFLICT DO NOTHING


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_concurrent_records_all_land(self):
        n = 40

        def worker(i):
            recorder.record_ai_request(provider="a", model="m", prompt_tokens=1,
                                       completion_tokens=1, request_id=f"r{i}")
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        count = int(query._scalar(query._db(), "SELECT COUNT(*) FROM ai_requests"))
        self.assertEqual(count, n)


class EngineIntegrationTests(unittest.TestCase):
    """Real automation send flows telemetry end-to-end (no mocks of the recorder)."""

    def setUp(self):
        _fresh_db()
        from automation import redis
        redis.configured = lambda: False

    def test_send_and_reply_emit_email_events(self):
        from automation import engine
        from automation.store import WorkflowStore
        from automation.providers.dryrun import DryRunProvider
        DryRunProvider.reset()
        st = WorkflowStore()
        wf = engine.create_workflow(st, "u_e", [
            {"subject": "hi", "body": "Hey."},
            {"subject": "f", "body": "f", "delay_days": 3}], to_email="bob@x.com")
        engine.advance_workflow(st.load(wf.id), st, now=wf.next_run_at,
                                credentials_provider=lambda u, p: None)
        engine.ingest_reply(st, message_id="m1", workflow_id=wf.id, user_id="u_e")
        telemetry.flush()
        self.assertEqual(query.guard_stats("u_e")["sent"], 1)
        self.assertEqual(query.reply_rate("u_e"), 1.0)   # 1 reply / 1 sent


if __name__ == "__main__":
    unittest.main(verbosity=2)
