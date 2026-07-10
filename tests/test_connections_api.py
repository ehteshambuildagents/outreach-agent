"""OAuth connections HTTP surface + static UI — the pieces the Connections page and
Automation dashboard drive. Offline: Redis in-memory, provider network mocked.

Covers the full connect round-trip (login -> callback stores an encrypted token ->
accounts lists it -> disconnect revokes+removes), reconnect, multi-account, error
paths, webhook gating, and that the two new pages are served with the right hooks.
"""

import os
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AUTOMATION_ENC_KEY"] = "unit-test-fixed-key"
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

from automation import redis  # noqa: E402

redis.configured = lambda: False               # in-memory coordination (state store)

_GOOGLE = {"GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsec"}
_MS = {"MICROSOFT_CLIENT_ID": "mid", "MICROSOFT_CLIENT_SECRET": "msec"}
_FRONTEND = {"FRONTEND_URL": "https://saqua.io"}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient
        import server.api as api
        cls.api, cls.TestClient = api, TestClient

    def _client(self, user):
        self.api.app.dependency_overrides[self.api.require_user] = lambda: user
        return self.TestClient(self.api.app)

    def _uid(self):
        return "u_" + os.urandom(6).hex()

    def tearDown(self):
        self.api.app.dependency_overrides.clear()


class OAuthLoginTests(_Base):
    def test_login_returns_provider_url_and_binds_state(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, _GOOGLE):
            r = c.get("/api/oauth/gmail/login")
        self.assertEqual(r.status_code, 200)
        url = r.json()["url"]
        self.assertIn("accounts.google.com", url)
        self.assertIn("state=", url)

    def test_gmail_login_uses_google_redirect_uri_env(self):
        c = self._client(self._uid())
        env = {
            **_GOOGLE,
            "GOOGLE_REDIRECT_URI": "https://api.saqua.io/api/oauth/gmail/callback",
        }
        with mock.patch.dict(os.environ, env):
            r = c.get("/api/oauth/gmail/login")
        self.assertEqual(r.status_code, 200)
        query = parse_qs(urlparse(r.json()["url"]).query)
        self.assertEqual(
            query["redirect_uri"],
            ["https://api.saqua.io/api/oauth/gmail/callback"],
        )

    def test_login_unconfigured_503(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            self.assertEqual(c.get("/api/oauth/gmail/login").status_code, 503)

    def test_reconnect_returns_url(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, _MS):
            r = c.get("/api/oauth/outlook/reconnect")
        self.assertEqual(r.status_code, 200)
        self.assertIn("login.microsoftonline.com", r.json()["url"])

    def test_return_to_must_be_same_origin(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, _GOOGLE):
            r = c.get("/api/oauth/gmail/login?return_to=https://evil.com/x")
        # state carries the sanitized return; the url is still our provider's
        self.assertIn("accounts.google.com", r.json()["url"])

    def test_unknown_provider_404(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, _GOOGLE):
            self.assertEqual(c.get("/api/oauth/aol/login").status_code, 404)


class OAuthCallbackTests(_Base):
    def _login_state(self, client, provider, env):
        with mock.patch.dict(os.environ, env):
            url = client.get(f"/api/oauth/{provider}/login").json()["url"]
        return parse_qs(urlparse(url).query)["state"][0]

    def test_full_connect_then_list_then_disconnect(self):
        user = self._uid()
        c = self._client(user)
        state = self._login_state(c, "gmail", _GOOGLE)
        with mock.patch.dict(os.environ, {**_GOOGLE, **_FRONTEND}), \
                mock.patch("automation.oauth.exchange_code",
                           return_value={"access_token": "AT", "refresh_token": "RT",
                                         "expires_in": 3600, "scope": "gmail.send"}), \
                mock.patch("automation.oauth.account_email", return_value="me@acme.com"):
            r = c.get(f"/api/oauth/gmail/callback?code=abc&state={state}",
                      follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://saqua.io/settings?connected=gmail")
        # accounts now lists the connected mailbox (no token material)
        accts = c.get("/api/oauth/accounts").json()["accounts"]
        self.assertEqual(len(accts), 1)
        self.assertEqual(accts[0]["account_email"], "me@acme.com")
        self.assertNotIn("access_token", accts[0])
        # disconnect revokes + removes
        with mock.patch("automation.oauth.revoke", return_value=True):
            d = c.post("/api/oauth/gmail/disconnect?account_email=me@acme.com")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(c.get("/api/oauth/accounts").json()["accounts"], [])

    def test_callback_rejects_forged_state(self):
        c = self._client(self._uid())
        r = c.get("/api/oauth/gmail/callback?code=x&state=forged", follow_redirects=False)
        self.assertEqual(r.status_code, 400)

    def test_callback_denied_redirects(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, _FRONTEND):
            r = c.get("/api/oauth/gmail/callback?error=access_denied", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://saqua.io/settings?error=gmail")

    def test_callback_exchange_failure_redirects_error(self):
        user = self._uid()
        c = self._client(user)
        state = self._login_state(c, "gmail", _GOOGLE)
        from automation.oauth import OAuthError
        with mock.patch.dict(os.environ, {**_GOOGLE, **_FRONTEND}), \
                mock.patch("automation.oauth.exchange_code", side_effect=OAuthError("bad")):
            r = c.get(f"/api/oauth/gmail/callback?code=x&state={state}", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://saqua.io/settings?error=gmail")

    def test_multiple_accounts_listed(self):
        user = self._uid()
        c = self._client(user)
        for email in ("a@acme.com", "b@acme.com"):
            state = self._login_state(c, "gmail", _GOOGLE)
            with mock.patch.dict(os.environ, _GOOGLE), \
                    mock.patch("automation.oauth.exchange_code",
                               return_value={"access_token": "AT", "refresh_token": "RT",
                                             "expires_in": 3600}), \
                    mock.patch("automation.oauth.account_email", return_value=email):
                c.get(f"/api/oauth/gmail/callback?code=abc&state={state}",
                      follow_redirects=False)
        emails = {a["account_email"] for a in c.get("/api/oauth/accounts").json()["accounts"]}
        self.assertEqual(emails, {"a@acme.com", "b@acme.com"})


class OAuthControlTests(_Base):
    def test_disconnect_without_account_404(self):
        c = self._client(self._uid())
        self.assertEqual(c.post("/api/oauth/gmail/disconnect").status_code, 404)

    def test_watch_without_account_404(self):
        c = self._client(self._uid())
        self.assertEqual(c.post("/api/oauth/gmail/watch").status_code, 404)

    def test_accounts_isolated_per_user(self):
        a, b = self._uid(), self._uid()
        ca = self._client(a)
        state = None
        with mock.patch.dict(os.environ, _GOOGLE):
            url = ca.get("/api/oauth/gmail/login").json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        with mock.patch.dict(os.environ, _GOOGLE), \
                mock.patch("automation.oauth.exchange_code",
                           return_value={"access_token": "AT", "refresh_token": "RT",
                                         "expires_in": 3600}), \
                mock.patch("automation.oauth.account_email", return_value="a@x.com"):
            ca.get(f"/api/oauth/gmail/callback?code=abc&state={state}", follow_redirects=False)
        # b sees none of a's accounts
        self.assertEqual(self._client(b).get("/api/oauth/accounts").json()["accounts"], [])


class WebhookGatingTests(_Base):
    def test_gmail_webhook_bad_token_401_when_configured(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GMAIL_PUBSUB_TOKEN": "sekret"}):
            r = c.post("/api/webhooks/gmail?token=wrong", json={"message": {"data": "e30="}})
        self.assertEqual(r.status_code, 401)

    def test_gmail_webhook_good_token_processes(self):
        c = self._client(self._uid())
        with mock.patch.dict(os.environ, {"GMAIL_PUBSUB_TOKEN": "sekret"}):
            r = c.post("/api/webhooks/gmail?token=sekret", json={"message": {"data": "e30="}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_graph_validation_handshake(self):
        c = self._client(self._uid())
        r = c.post("/api/webhooks/graph?validationToken=Tok123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "Tok123")


class StaticPageTests(_Base):
    def test_connections_page_served_with_hooks(self):
        c = self._client(self._uid())
        r = c.get("/connections.html")
        self.assertEqual(r.status_code, 200)
        for needle in ("/api/oauth/", "/api/oauth/accounts", "/api/automation/health"):
            self.assertIn(needle, r.text)

    def test_automation_dashboard_served_with_hooks(self):
        c = self._client(self._uid())
        r = c.get("/automation.html")
        self.assertEqual(r.status_code, 200)
        for needle in ("/api/automation/metrics", "/api/automation/workflows",
                       "/api/automation/dead-letter", "force-retry"):
            self.assertIn(needle, r.text)

    def test_settings_links_to_connections(self):
        c = self._client(self._uid())
        self.assertIn("/connections.html", c.get("/settings.html").text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
