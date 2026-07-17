"""Tests for request-access gating (access/).

Offline, temp SQLite. Covers: gating off by config, first-seen users landing in
pending, approval granting full access, denial, the anonymous/dev bypass, and the
auto-approve bootstrap.

    python -m unittest tests.test_access
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

import access  # noqa: E402
from access import store  # noqa: E402


def _env(**kw):
    base = {"ACCESS_GATING": "1", "ACCESS_AUTO_APPROVE": "", "CLERK_PUBLISHABLE_KEY": ""}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=False)


class AccessTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTOMATION_DB_PATH"] = tempfile.mktemp(suffix=".db")
        store.reset_ensured()

    def test_gating_disabled_allows_everyone(self):
        with _env(ACCESS_GATING="0"):
            allowed, status = access.check_access("someone")
        self.assertTrue(allowed)
        self.assertEqual(status, "approved")

    def test_first_seen_is_pending_then_approved(self):
        with _env():
            allowed, status = access.check_access("u1", "u1@example.com")
            self.assertFalse(allowed)
            self.assertEqual(status, "pending")
            # Re-checking doesn't flip it, and it shows up in the pending list.
            self.assertFalse(access.check_access("u1")[0])
            self.assertTrue(any(r["user_id"] == "u1" for r in access.list_pending()))
            # Admin approves -> full access immediately.
            access.approve("u1")
            allowed, status = access.check_access("u1")
        self.assertTrue(allowed)
        self.assertEqual(status, "approved")

    def test_deny_blocks(self):
        with _env():
            access.check_access("u2")
            access.deny("u2")
            allowed, status = access.check_access("u2")
        self.assertFalse(allowed)
        self.assertEqual(status, "denied")

    def test_anonymous_is_never_gated(self):
        with _env():
            allowed, status = access.check_access("anonymous")
        self.assertTrue(allowed)

    def test_auto_approve_bootstrap(self):
        with _env(ACCESS_AUTO_APPROVE="founder@x.com, admin_id"):
            by_email = access.check_access("random_id", "founder@x.com")
            by_id = access.check_access("admin_id")
            other = access.check_access("nobody")
        self.assertEqual(by_email, (True, "approved"))
        self.assertEqual(by_id, (True, "approved"))
        self.assertEqual(other[1], "pending")

    def test_create_pending_is_idempotent(self):
        self.assertTrue(store.create_pending("dup"))
        self.assertFalse(store.create_pending("dup"))       # already exists
        self.assertEqual(len([r for r in store.list_by_status() if r["user_id"] == "dup"]), 1)

    def test_gating_auto_follows_clerk(self):
        # "auto" (default): enabled only when Clerk is configured.
        with mock.patch.dict(os.environ, {"ACCESS_GATING": "auto",
                                          "CLERK_PUBLISHABLE_KEY": ""}, clear=False):
            self.assertFalse(access.gating_enabled())
        with mock.patch.dict(os.environ, {"ACCESS_GATING": "auto",
                                          "CLERK_PUBLISHABLE_KEY": "pk_test_x"}, clear=False):
            self.assertTrue(access.gating_enabled())


if __name__ == "__main__":
    unittest.main()
