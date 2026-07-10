"""Push-notification reply detection — Gmail Pub/Sub + Graph change notifications.

Providers are faked (no network); the store and token store are real. Proves the
notification is decoded, deduplicated, matched to a workflow by thread, and stops
the sequence idempotently — plus that our own outbound messages never look like a
reply and that a bad clientState is rejected.
"""

import base64
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AUTOMATION_ENC_KEY"] = "unit-test-fixed-key"

from automation import engine, push, redis, states  # noqa: E402

redis.configured = lambda: False

from automation.store import WorkflowStore  # noqa: E402
from automation.tokens import TokenStore  # noqa: E402


def _stores():
    d = tempfile.mkdtemp()
    return WorkflowStore(path=os.path.join(d, "wf.db")), TokenStore(path=os.path.join(d, "tok.db"))


def _pubsub(email, history_id):
    data = base64.b64encode(json.dumps(
        {"emailAddress": email, "historyId": history_id}).encode()).decode()
    return {"message": {"data": data}}


class _FakeGmail:
    def __init__(self, messages, history_id="200"):
        self._messages, self._history_id = messages, history_id
    def list_history(self, start):
        return {"history_id": self._history_id, "messages": self._messages}
    def watch(self, *, user_id):
        return {"history_id": "1", "expiration": time.time() + 999999}


class _FakeOutlook:
    def __init__(self, msg):
        self._msg = msg
    def get_message(self, mid):
        return self._msg


def _waiting_workflow(store, user, thread_id):
    """A 2-step workflow with step 0 sent (thread known) and waiting for step 1."""
    wf = engine.create_workflow(store, user, [
        {"subject": "a", "body": "a"},
        {"subject": "b", "body": "b", "delay_days": 3}], to_email="p@y.com")
    wf.steps[0].provider_thread_id = thread_id
    wf.steps[0].status = states.STEP_SENT
    wf.current_index = 0
    wf.state = states.WAITING
    wf.next_run_at = wf.next_run_at
    store.save(wf)
    return wf


class GmailPushTests(unittest.TestCase):
    def setUp(self):
        self.store, self.tokens = _stores()
        self.tokens.upsert(user_id="u", provider="gmail", account_email="me@x.com",
                           access_token="AT", refresh_token="RT",
                           expires_at=time.time() + 9999)

    def test_decode_pubsub(self):
        self.assertEqual(push.decode_pubsub(_pubsub("a@x.com", "5")),
                         {"email": "a@x.com", "history_id": "5"})
        self.assertEqual(push.decode_pubsub({}), {})
        self.assertEqual(push.decode_pubsub({"message": {"data": "!!notb64"}}), {})

    def test_reply_stops_workflow(self):
        wf = _waiting_workflow(self.store, "u", "TID")
        fake = _FakeGmail([{"message_id": "M1", "thread_id": "TID", "labels": ["INBOX"]}])
        res = push.handle_gmail_pubsub(self.store, self.tokens, _pubsub("me@x.com", "100"),
                                       provider_factory=lambda tok: fake)
        self.assertEqual(res["stopped"], 1)
        self.assertEqual(self.store.load(wf.id).state, states.STOPPED)

    def test_duplicate_pubsub_is_noop(self):
        _waiting_workflow(self.store, "u", "TID")
        fake = _FakeGmail([{"message_id": "M1", "thread_id": "TID", "labels": ["INBOX"]}])
        env = _pubsub("me@x.com", "100")
        push.handle_gmail_pubsub(self.store, self.tokens, env, provider_factory=lambda t: fake)
        second = push.handle_gmail_pubsub(self.store, self.tokens, env,
                                          provider_factory=lambda t: fake)
        self.assertTrue(second["duplicate"])

    def test_own_sent_message_is_not_a_reply(self):
        wf = _waiting_workflow(self.store, "u", "TID")
        fake = _FakeGmail([{"message_id": "M2", "thread_id": "TID", "labels": ["SENT"]}])
        res = push.handle_gmail_pubsub(self.store, self.tokens, _pubsub("me@x.com", "101"),
                                       provider_factory=lambda t: fake)
        self.assertEqual(res["stopped"], 0)
        self.assertEqual(self.store.load(wf.id).state, states.WAITING)

    def test_no_token_skips_quietly(self):
        self.tokens.mark_reconnect("u", "gmail", "me@x.com")
        res = push.handle_gmail_pubsub(self.store, self.tokens, _pubsub("me@x.com", "102"),
                                       provider_factory=lambda t: _FakeGmail([]))
        self.assertEqual(res["stopped"], 0)

    def test_enable_gmail_watch_persists_state(self):
        res = push.enable_gmail_watch(self.tokens, "u", "me@x.com",
                                      provider_factory=lambda t: _FakeGmail([]))
        self.assertTrue(res["ok"])
        self.assertIn("history_id", self.tokens.get("u", "gmail")["watch_state"])


class GraphPushTests(unittest.TestCase):
    def setUp(self):
        self.store, self.tokens = _stores()
        self.tokens.upsert(user_id="u", provider="outlook", account_email="me@x.com",
                           access_token="AT", refresh_token="RT",
                           expires_at=time.time() + 9999)
        self.tokens.set_watch_state("u", "outlook", "me@x.com", {"subscription_id": "sub1"})

    def _body(self, client_state="cs"):
        return {"value": [{"resourceData": {"id": "MSG"}, "clientState": client_state,
                           "subscriptionId": "sub1"}]}

    def test_reply_stops_workflow(self):
        wf = _waiting_workflow(self.store, "u", "CID")
        fake = _FakeOutlook({"message_id": "MSG", "thread_id": "CID", "from": "p@y.com"})
        res = push.handle_graph_notifications(self.store, self.tokens, self._body(),
                                              expected_client_state="cs",
                                              provider_factory=lambda t: fake)
        self.assertEqual(res["stopped"], 1)
        self.assertEqual(self.store.load(wf.id).state, states.STOPPED)

    def test_bad_client_state_rejected(self):
        wf = _waiting_workflow(self.store, "u", "CID")
        fake = _FakeOutlook({"message_id": "MSG", "thread_id": "CID", "from": "p@y.com"})
        res = push.handle_graph_notifications(self.store, self.tokens,
                                              self._body(client_state="WRONG"),
                                              expected_client_state="cs",
                                              provider_factory=lambda t: fake)
        self.assertEqual(res["stopped"], 0)
        self.assertEqual(self.store.load(wf.id).state, states.WAITING)

    def test_duplicate_notification_is_noop(self):
        _waiting_workflow(self.store, "u", "CID")
        fake = _FakeOutlook({"message_id": "MSG", "thread_id": "CID", "from": "p@y.com"})
        push.handle_graph_notifications(self.store, self.tokens, self._body(),
                                        expected_client_state="cs",
                                        provider_factory=lambda t: fake)
        # message MSG already processed -> second delivery does nothing
        res = push.handle_graph_notifications(self.store, self.tokens, self._body(),
                                              expected_client_state="cs",
                                              provider_factory=lambda t: fake)
        self.assertEqual(res["stopped"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
