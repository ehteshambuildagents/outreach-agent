"""OAuth foundation tests — crypto, token storage, and the OAuth flow.

Offline and deterministic: Redis is forced to in-memory, a fixed encryption key
is set so ciphertext is stable, and every network call (token exchange/refresh,
userinfo, revoke) is mocked. No real provider is contacted.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AUTOMATION_ENC_KEY"] = "unit-test-fixed-key"   # stable ciphertext
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"                # never hit live DB in tests

from automation import crypto, oauth, redis  # noqa: E402

redis.configured = lambda: False                           # in-memory coordination

from automation import tokens  # noqa: E402
from automation.tokens import STATUS_CONNECTED, STATUS_RECONNECT, TokenStore  # noqa: E402


def _tok_store():
    return TokenStore(path=os.path.join(tempfile.mkdtemp(), "tok.db"))


class CryptoTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(crypto.decrypt(crypto.encrypt("hello-token")), "hello-token")

    def test_ciphertext_is_not_plaintext(self):
        ct = crypto.encrypt("super-secret-refresh")
        self.assertNotIn("super-secret-refresh", ct)

    def test_empty_values(self):
        self.assertEqual(crypto.decrypt(""), "")
        self.assertEqual(crypto.decrypt(crypto.encrypt("")), "")   # empty round-trips

    def test_corrupt_ciphertext_raises(self):
        with self.assertRaises(ValueError):
            crypto.decrypt("not-a-valid-fernet-token")

    def test_rotate_reencrypts(self):
        ct = crypto.encrypt("v")
        rotated = crypto.rotate(ct)
        self.assertEqual(crypto.decrypt(rotated), "v")


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.ts = _tok_store()

    def test_upsert_and_get_decrypts(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="AT", refresh_token="RT", expires_at=9e12)
        rec = self.ts.get("u1", "gmail")
        self.assertEqual((rec["access_token"], rec["refresh_token"]), ("AT", "RT"))

    def test_multi_account_and_default_pick(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        self.ts.upsert(user_id="u1", provider="gmail", account_email="b@x.com",
                       access_token="B", refresh_token="r", expires_at=9e12)
        self.assertEqual(len(self.ts.list_accounts("u1")), 2)
        self.assertEqual(self.ts.get("u1", "gmail", "b@x.com")["access_token"], "B")

    def test_per_user_isolation(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        self.assertIsNone(self.ts.get("u2", "gmail"))
        self.assertEqual(self.ts.list_accounts("u2"), [])

    def test_list_never_leaks_tokens(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        for a in self.ts.list_accounts("u1"):
            self.assertNotIn("access_token", a)
            self.assertNotIn("refresh_token", a)

    def test_upsert_keeps_refresh_when_omitted(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A1", refresh_token="RT", expires_at=9e12)
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A2", refresh_token=None, expires_at=9e12)
        self.assertEqual(self.ts.get("u1", "gmail")["refresh_token"], "RT")

    def test_valid_token_fresh_is_returned_directly(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="FRESH", refresh_token="r", expires_at=time.time() + 9999)
        with mock.patch("automation.oauth.refresh") as ref:
            self.assertEqual(self.ts.valid_access_token("u1", "gmail"), "FRESH")
            ref.assert_not_called()

    def test_valid_token_refreshes_when_expired(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="OLD", refresh_token="RT", expires_at=time.time() - 10)
        with mock.patch("automation.oauth.refresh",
                        return_value={"access_token": "NEW", "expires_in": 3600,
                                      "refresh_token": "RT2"}) as ref:
            self.assertEqual(self.ts.valid_access_token("u1", "gmail"), "NEW")
            ref.assert_called_once()
        # rotation persisted
        self.assertEqual(self.ts.get("u1", "gmail")["refresh_token"], "RT2")

    def test_refresh_failure_flags_reconnect(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="OLD", refresh_token="RT", expires_at=time.time() - 10)
        with mock.patch("automation.oauth.refresh", side_effect=oauth.OAuthError("revoked")):
            self.assertEqual(self.ts.valid_access_token("u1", "gmail"), "")
        self.assertEqual(self.ts.get("u1", "gmail")["status"], STATUS_RECONNECT)

    def test_no_refresh_token_flags_reconnect(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="OLD", refresh_token=None, expires_at=time.time() - 10)
        self.assertEqual(self.ts.valid_access_token("u1", "gmail"), "")
        self.assertEqual(self.ts.get("u1", "gmail")["status"], STATUS_RECONNECT)

    def test_due_for_refresh(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=time.time() + 5)
        self.ts.upsert(user_id="u1", provider="outlook", account_email="o@x.com",
                       access_token="A", refresh_token="r", expires_at=time.time() + 9999)
        due = self.ts.due_for_refresh()
        self.assertEqual([d["provider"] for d in due], ["gmail"])

    def test_delete_removes_account(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        self.ts.delete("u1", "gmail", "a@x.com")
        self.assertIsNone(self.ts.get("u1", "gmail"))

    def test_watch_state_round_trip(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        self.ts.set_watch_state("u1", "gmail", "a@x.com", {"history_id": "42"})
        self.assertEqual(self.ts.get("u1", "gmail")["watch_state"], {"history_id": "42"})

    def test_connected_without_watch_is_complement_of_with_watch(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        # No watch yet -> shows up as "without watch", not in "with_watch".
        self.assertEqual([r["account_email"] for r in self.ts.connected_without_watch("gmail")],
                         ["a@x.com"])
        self.assertEqual(self.ts.with_watch("gmail"), [])
        # Arm it -> it flips to the other side.
        self.ts.set_watch_state("u1", "gmail", "a@x.com", {"history_id": "1"})
        self.assertEqual(self.ts.connected_without_watch("gmail"), [])
        self.assertEqual([r["account_email"] for r in self.ts.with_watch("gmail")], ["a@x.com"])

    def test_accounts_by_email_routes_to_users(self):
        self.ts.upsert(user_id="u1", provider="gmail", account_email="team@x.com",
                       access_token="A", refresh_token="r", expires_at=9e12)
        self.ts.upsert(user_id="u2", provider="gmail", account_email="team@x.com",
                       access_token="B", refresh_token="r", expires_at=9e12)
        self.assertEqual({a["user_id"] for a in self.ts.accounts_by_email("gmail", "team@x.com")},
                         {"u1", "u2"})

    def test_credentials_provider_adapter(self):
        tokens._default = self.ts
        self.ts.upsert(user_id="u1", provider="gmail", account_email="a@x.com",
                       access_token="LIVE", refresh_token="r", expires_at=time.time() + 9999)
        self.assertEqual(tokens.credentials_provider("u1", "gmail"), "LIVE")
        self.assertIsNone(tokens.credentials_provider("u1", "dryrun"))
        tokens._default = None


class OAuthFlowTests(unittest.TestCase):
    def test_gmail_uses_railway_google_env_names(self):
        cfg = oauth.PROVIDERS["gmail"]
        self.assertEqual(cfg["client_id_env"], "GOOGLE_CLIENT_ID")
        self.assertEqual(cfg["client_secret_env"], "GOOGLE_CLIENT_SECRET")
        self.assertEqual(cfg["redirect_env"], "GOOGLE_REDIRECT_URI")

    def test_gmail_redirect_uri_prefers_google_redirect_uri_env(self):
        with mock.patch.dict(
            os.environ,
            {"GOOGLE_REDIRECT_URI": "http://localhost:8000/api/oauth/gmail/callback"},
        ):
            redirect = oauth.redirect_uri("gmail", default="https://derived.example/callback")
        self.assertEqual(redirect, "http://localhost:8000/api/oauth/gmail/callback")

    def test_authorize_url_google(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "gid"}):
            url = oauth.build_authorize_url("gmail", "STATE", "https://app/cb")
        self.assertIn("accounts.google.com", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("state=STATE", url)
        self.assertIn("gmail.send", url)

    def test_gmail_requests_metadata_not_full_read(self):
        """Reply detection reads only message/thread IDs + labels (history.list), so
        we request the narrow gmail.metadata scope. gmail.readonly (full body access,
        a Google RESTRICTED scope) must NOT be requested — keep this locked so a
        future edit can't silently re-broaden it and re-trigger a security review."""
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "gid"}):
            url = oauth.build_authorize_url("gmail", "S", "https://app/cb")
        self.assertIn("gmail.metadata", url)
        self.assertNotIn("gmail.readonly", url)

    def test_authorize_url_microsoft(self):
        with mock.patch.dict(os.environ, {"MICROSOFT_CLIENT_ID": "mid"}):
            url = oauth.build_authorize_url("outlook", "S", "https://app/cb")
        self.assertIn("login.microsoftonline.com", url)
        self.assertIn("offline_access", url)

    def test_configured_reflects_env(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"}):
            self.assertTrue(oauth.configured("gmail"))
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            self.assertFalse(oauth.configured("gmail"))

    def test_state_is_single_use_and_user_bound(self):
        state = oauth.make_state("user-123", "gmail", "/app.html")
        ctx = oauth.consume_state(state)
        self.assertEqual(ctx["user_id"], "user-123")
        self.assertEqual(ctx["provider"], "gmail")
        self.assertIsNone(oauth.consume_state(state))   # replay rejected

    def test_unknown_state_rejected(self):
        self.assertIsNone(oauth.consume_state("never-issued"))
        self.assertIsNone(oauth.consume_state(""))

    def test_exchange_code_parses_token(self):
        resp = mock.Mock(status_code=200,
                         json=lambda: {"access_token": "AT", "refresh_token": "RT",
                                       "expires_in": 3600})
        with mock.patch("automation.oauth.requests.post", return_value=resp), \
                mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s"}):
            tok = oauth.exchange_code("gmail", "code", "https://app/cb")
        self.assertEqual(tok["access_token"], "AT")

    def test_exchange_code_error_is_terse(self):
        resp = mock.Mock(status_code=400, text="secret-echo")
        with mock.patch("automation.oauth.requests.post", return_value=resp), \
                mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s"}):
            with self.assertRaises(oauth.OAuthError) as ctx:
                oauth.exchange_code("gmail", "bad", "https://app/cb")
        self.assertNotIn("secret-echo", str(ctx.exception))   # never echo provider body

    def test_refresh_microsoft_includes_scope(self):
        captured = {}

        def _post(url, data=None, **kw):
            captured.update(data)
            return mock.Mock(status_code=200,
                             json=lambda: {"access_token": "N", "expires_in": 3600})
        with mock.patch("automation.oauth.requests.post", side_effect=_post), \
                mock.patch.dict(os.environ, {"MICROSOFT_CLIENT_ID": "i",
                                             "MICROSOFT_CLIENT_SECRET": "s"}):
            oauth.refresh("outlook", "RT")
        self.assertIn("scope", captured)
        self.assertEqual(captured["grant_type"], "refresh_token")

    def test_account_email_lookup(self):
        resp = mock.Mock(status_code=200, json=lambda: {"email": "Me@X.com"})
        with mock.patch("automation.oauth.requests.get", return_value=resp):
            self.assertEqual(oauth.account_email("gmail", "AT"), "me@x.com")

    def test_revoke_best_effort(self):
        with mock.patch("automation.oauth.requests.post",
                        return_value=mock.Mock(status_code=200)):
            self.assertTrue(oauth.revoke("gmail", "RT"))
        # outlook has no revoke endpoint -> False, no exception
        self.assertFalse(oauth.revoke("outlook", "RT"))

    def test_unknown_provider_raises(self):
        with self.assertRaises(oauth.OAuthError):
            oauth.build_authorize_url("myspace", "s", "cb")


if __name__ == "__main__":
    unittest.main(verbosity=2)
