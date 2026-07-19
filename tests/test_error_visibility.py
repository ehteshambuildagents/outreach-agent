"""Tests for error visibility (server/error_log.py) + the internal admin API.

Offline, temp SQLite. Covers durable capture of unhandled errors, the agent-turn
(502) capture path, and the token-guarded admin endpoints for errors / usage /
access.

    python -m unittest tests.test_error_visibility
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

from starlette.testclient import TestClient  # noqa: E402

import server.api as api  # noqa: E402
from server import error_log  # noqa: E402
import access  # noqa: E402
from access import store as access_store  # noqa: E402
from limits import store as limits_store  # noqa: E402


class ErrorLogUnitTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTOMATION_DB_PATH"] = tempfile.mktemp(suffix=".db")
        error_log.reset_ensured()

    def test_record_and_recent(self):
        error_log.record_error(path="/api/x", method="POST", status=500,
                               error_type="ValueError", message="boom",
                               tb="trace", notify=False)
        rows = error_log.recent(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error_type"], "ValueError")
        self.assertEqual(rows[0]["path"], "/api/x")

    def test_count_since(self):
        for _ in range(3):
            error_log.record_error(error_type="E", message="m", notify=False)
        self.assertEqual(error_log.count_since(time.time() - 3600), 3)
        self.assertEqual(error_log.count_since(time.time() + 3600), 0)


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTOMATION_DB_PATH"] = tempfile.mktemp(suffix=".db")
        error_log.reset_ensured()
        access_store.reset_ensured()
        limits_store.reset_ensured()
        api._STORE_BASE = tempfile.mkdtemp()
        api._BUCKETS.clear()
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides[api.require_user] = lambda: "user_test"
        api.app.dependency_overrides[api.require_approved_user] = lambda: "user_test"
        self.client = TestClient(api.app, raise_server_exceptions=False)

    def tearDown(self):
        api.app.dependency_overrides.clear()

    def test_unhandled_500_is_captured(self):
        # Force an unhandled error inside an endpoint -> global handler -> capture.
        with mock.patch("server.api._conversation_public", side_effect=RuntimeError("kaboom")):
            r = self.client.post("/api/conversations")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["error"], "Something went wrong. Please try again.")
        rows = error_log.recent(10)
        self.assertTrue(any(e["error_type"] == "RuntimeError"
                            and e["path"] == "/api/conversations" for e in rows))

    def test_agent_turn_failure_is_captured(self):
        cid = self.client.post("/api/conversations").json()["id"]
        with mock.patch("server.api.respond", side_effect=RuntimeError("agent blew up")):
            r = self.client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        self.assertEqual(r.status_code, 502)
        rows = error_log.recent(10)
        self.assertTrue(any(e["status"] == 502 and e["user_id"] == "user_test"
                            for e in rows))

    def test_admin_errors_requires_token(self):
        error_log.record_error(error_type="E", message="m", notify=False)
        # Disabled entirely when no admin token is configured.
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": ""}, clear=False):
            self.assertEqual(self.client.get("/api/admin/errors").status_code, 404)
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret"}, clear=False):
            self.assertEqual(
                self.client.get("/api/admin/errors",
                                headers={"X-Admin-Token": "wrong"}).status_code, 403)
            r = self.client.get("/api/admin/errors", headers={"X-Admin-Token": "s3cret"})
            self.assertEqual(r.status_code, 200)
            self.assertGreaterEqual(len(r.json()["errors"]), 1)

    def test_admin_access_approve_flow(self):
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret",
                                          "ACCESS_GATING": "1"}, clear=False):
            h = {"X-Admin-Token": "s3cret"}
            access.check_access("pending_guy")          # lands in pending
            pend = self.client.get("/api/admin/access/pending", headers=h).json()["pending"]
            self.assertTrue(any(p["user_id"] == "pending_guy" for p in pend))
            r = self.client.post("/api/admin/access/approve", headers=h,
                                 json={"user_id": "pending_guy"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(access.is_approved("pending_guy"))

    def test_admin_usage_endpoint(self):
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret"}, clear=False):
            r = self.client.get("/api/admin/usage", headers={"X-Admin-Token": "s3cret"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("users", r.json())

    def test_echo_ip_requires_token(self):
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": ""}, clear=False):
            self.assertEqual(self.client.get("/api/admin/echo-ip").status_code, 404)
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret"}, clear=False):
            self.assertEqual(
                self.client.get("/api/admin/echo-ip",
                                headers={"X-Admin-Token": "wrong"}).status_code, 403)

    def test_echo_ip_reports_chain_and_what_the_limiter_would_key_on(self):
        """The whole point of the endpoint: show the raw chain next to the address
        the limiter derives from it, so the two can be compared against a known
        caller instead of guessed at."""
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret",
                                          "TRUSTED_PROXY_HOPS": "1"}, clear=False):
            r = self.client.get("/api/admin/echo-ip",
                                headers={"X-Admin-Token": "s3cret",
                                         "X-Forwarded-For": "9.9.9.9, 10.0.0.5"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["xff_parts"], ["9.9.9.9", "10.0.0.5"])
        self.assertEqual(body["xff_len"], 2)
        self.assertEqual(body["trusted_proxy_hops"], 1)
        # With one trusted hop the limiter takes the rightmost entry. This is the
        # behaviour that produced infrastructure-keyed buckets in production; the
        # endpoint's job is to make that visible rather than to be correct itself.
        self.assertEqual(body["client_ip_computed"], "10.0.0.5")
        self.assertEqual(body["client_ip_headers"]["x-forwarded-for"],
                         "9.9.9.9, 10.0.0.5")

    def test_echo_ip_never_echoes_credential_headers(self):
        """It reports unknown headers by NAME so a platform header can be spotted.
        Names only: whoever holds the admin token must not get an Authorization or
        Cookie value reflected back at them."""
        with mock.patch.dict(os.environ, {"SAQUA_ADMIN_TOKEN": "s3cret"}, clear=False):
            r = self.client.get("/api/admin/echo-ip",
                                headers={"X-Admin-Token": "s3cret",
                                         "Authorization": "Bearer super-secret-value",
                                         "Cookie": "session=super-secret-cookie"})
        body = r.json()
        blob = json.dumps(body)
        self.assertNotIn("super-secret-value", blob)
        self.assertNotIn("super-secret-cookie", blob)
        lowered = [h.lower() for h in body["other_header_names"]]
        self.assertIn("authorization", lowered)
        self.assertIn("cookie", lowered)


if __name__ == "__main__":
    unittest.main()
