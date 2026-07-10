"""End-to-end tests for the thin API layer (server/api.py).

The API is pure transport/serialization over the existing backend, so these
tests patch `server.api.respond` (the one backend call) to assert the API's
own contract: validation, curated serialization, security, and error handling.
No network or API key required.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

from starlette.testclient import TestClient  # noqa: E402

import server.api as api  # noqa: E402
from chat.models import Conversation, Message, EMAIL, RESEARCH  # noqa: E402


def _client(auth=True, user="user_test"):
    # Isolate the per-user store root per test (each user gets a subdirectory).
    api._STORE_BASE = tempfile.mkdtemp()
    api._BUCKETS.clear()
    # Auth is verified against Clerk in production; here we override the
    # dependency so the API's OWN contract can be tested without a live token.
    # Pass auth=False to exercise the real (rejecting) auth dependency.
    api.app.dependency_overrides.clear()
    if auth:
        api.app.dependency_overrides[api.require_user] = lambda: user
    return TestClient(api.app)


def _researched(conv, text, store=None, user_id=None):
    """Stand-in for chat.agent.respond: mutate the conversation like the real
    backend does (user msg + research + email + workspace)."""
    conv.add_user(text)
    conv.add(Message(role="assistant", kind=RESEARCH, content="Researched Acme.",
                     data={"company": "Acme", "what_they_do": "warehouse robots",
                           "research_score": 74, "pages_crawled": ["https://acme.com"],
                           "hooks": ["Trusted by DHL"], "stop_reason": "sufficient"}))
    conv.add(Message(role="assistant", kind=EMAIL, content="Drafted the email.",
                     data={"subject": "robots", "body": "Hey Bob.\n\nNice robots.",
                           "to": "Bob", "company": "Acme"}))
    conv.title = "Acme"
    conv.workspace = {"company": "Acme", "research": {
        "status": "ok", "research_score": 74,
        "pages_crawled": ["https://acme.com", "https://www.linkedin.com/company/acme"],
        "hooks": [{"text": "Trusted by DHL"}, {"text": "Series B in 2025"}],
        "data": {"company_name": "Acme", "what_they_do": "warehouse robots",
                 "primary_contact_name": "Bob Vance", "primary_contact_role": "CEO",
                 "notable_customers": ["DHL"]}}}
    if store is not None:      # real respond() persists; mirror that
        store.save(conv)
    return conv


class ApiTests(unittest.TestCase):
    def test_health(self):
        self.assertEqual(_client().get("/api/health").json(), {"ok": True})

    def test_create_get_delete(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        self.assertEqual(c.get(f"/api/conversations/{cid}").status_code, 200)
        self.assertEqual(c.delete(f"/api/conversations/{cid}").json(), {"ok": True})
        self.assertEqual(c.get(f"/api/conversations/{cid}").status_code, 404)

    def test_bad_id_is_404(self):
        c = _client()
        for bad in ("bad.id", "has space", "$x", "x" * 80):
            self.assertEqual(c.get(f"/api/conversations/{bad}").status_code, 404)

    def test_validation(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        self.assertEqual(c.post(f"/api/conversations/{cid}/messages", json={"text": "  "}).status_code, 422)
        self.assertEqual(c.post(f"/api/conversations/{cid}/messages", json={"text": "x" * 3000}).status_code, 422)
        r = c.post(f"/api/conversations/{cid}/messages", json={})
        self.assertEqual(r.status_code, 422)
        # 422s use the same {"error": "..."} contract as every other response —
        # never FastAPI's raw Pydantic error list (field paths/types/ctx).
        body = r.json()
        self.assertEqual(set(body), {"error"})
        self.assertIsInstance(body["error"], str)

    def test_send_returns_curated_conversation(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        with mock.patch("server.api.respond", side_effect=_researched):
            r = c.post(f"/api/conversations/{cid}/messages", json={"text": "Acme"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["title"], "Acme")
        kinds = [m["kind"] for m in data["messages"]]
        self.assertIn("research", kinds)
        self.assertIn("email", kinds)
        email = next(m for m in data["messages"] if m["kind"] == "email")
        self.assertEqual(email["data"]["subject"], "robots")
        # panel is curated from the research result
        panel = data["panel"]
        self.assertTrue(panel["has_research"])
        self.assertEqual(panel["company"], "Acme")
        self.assertEqual(panel["confidence"], 74)
        self.assertIn("Trusted by DHL", panel["evidence"])
        self.assertEqual(panel["contact"]["name"], "Bob Vance")
        self.assertTrue(panel["contact"]["linkedin"].startswith("https://www.linkedin.com/"))
        marks = {s["mark"] for s in panel["sources"]}
        self.assertIn("in", marks)   # linkedin.com -> "in"

    def test_send_never_leaks_internals(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        with mock.patch("server.api.respond", side_effect=_researched):
            body = c.post(f"/api/conversations/{cid}/messages", json={"text": "Acme"}).text
        # The raw research object / prompts / stop-internal fields never ship whole.
        self.assertNotIn("ANTHROPIC", body)
        self.assertNotIn("SYSTEM_PROMPT", body)
        self.assertNotIn("sk-ant", body)

    def test_error_path_is_friendly(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        with mock.patch("server.api.respond", side_effect=RuntimeError("boom secret trace")):
            r = c.post(f"/api/conversations/{cid}/messages", json={"text": "Acme"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("boom", r.text)          # no internal detail leaks
        self.assertIn("error", r.json())

    def test_rate_limit_agent_bucket(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        with mock.patch("server.api.respond", side_effect=_researched):
            codes = [c.post(f"/api/conversations/{cid}/messages", json={"text": "x"}).status_code
                     for _ in range(24)]
        self.assertIn(429, codes)                  # agent bucket = 20/min

    def test_docs_disabled(self):
        c = _client()
        self.assertEqual(c.get("/openapi.json").status_code, 404)
        self.assertEqual(c.get("/docs").status_code, 404)

    # ── Authentication ────────────────────────────────────────────────
    def test_protected_endpoints_require_auth(self):
        c = _client(auth=False)                       # real auth dependency
        self.assertEqual(c.get("/api/conversations").status_code, 401)
        self.assertEqual(c.post("/api/conversations").status_code, 401)
        self.assertEqual(c.get("/api/conversations/abc").status_code, 401)
        self.assertEqual(c.delete("/api/conversations/abc").status_code, 401)
        self.assertEqual(
            c.post("/api/conversations/abc/messages", json={"text": "hi"}).status_code, 401)

    def test_invalid_token_rejected(self):
        c = _client(auth=False)
        r = c.get("/api/conversations", headers={"Authorization": "Bearer not-a-jwt"})
        self.assertEqual(r.status_code, 401)

    def test_health_and_config_are_public(self):
        c = _client(auth=False)                       # no auth override
        self.assertEqual(c.get("/api/health").status_code, 200)
        r = c.get("/api/public-config")
        self.assertEqual(r.status_code, 200)
        self.assertIn("authEnabled", r.json())

    def test_public_config_never_leaks_secret(self):
        body = _client(auth=False).get("/api/public-config").text
        self.assertNotIn("CLERK_SECRET", body)
        self.assertNotIn("sk_test", body)
        self.assertNotIn("sk_live", body)

    def test_internal_artifact_id_is_not_exposed(self):
        # Artifacts carry a stable internal `id` in storage, but it must never
        # reach the browser (it's for future server-side automation only).
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        def add_email(conv, text, store=None, user_id=None):
            conv.add_user(text)
            conv.add(Message(role="assistant", kind=EMAIL, content="Drafted.",
                             data={"id": "version-b", "subject": "s", "body": "b",
                                   "to": "Bob", "company": "Acme", "label": "Version B"}))
            if store is not None:
                store.save(conv)
            return conv
        with mock.patch("server.api.respond", side_effect=add_email):
            data = c.post(f"/api/conversations/{cid}/messages", json={"text": "hi"}).json()
        email = next(m for m in data["messages"] if m["kind"] == "email")
        self.assertNotIn("id", email["data"])          # internal id stripped
        self.assertEqual(email["data"]["label"], "Version B")   # label still shown

    def test_rename_conversation(self):
        c = _client()
        cid = c.post("/api/conversations").json()["id"]
        r = c.patch(f"/api/conversations/{cid}", json={"title": "  Acme  outreach  "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["title"], "Acme outreach")   # trimmed/collapsed
        # empty title rejected
        self.assertEqual(c.patch(f"/api/conversations/{cid}", json={"title": "   "}).status_code, 422)
        # missing conversation
        self.assertEqual(c.patch("/api/conversations/nope", json={"title": "x"}).status_code, 404)

    def test_duplicate_conversation(self):
        c = _client()
        with mock.patch("server.api.respond", side_effect=_researched):
            src = c.post(f"/api/conversations/{c.post('/api/conversations').json()['id']}/messages",
                        json={"text": "Acme"}).json()
        dup = c.post(f"/api/conversations/{src['id']}/duplicate").json()
        self.assertNotEqual(dup["id"], src["id"])                 # new id
        self.assertTrue(dup["title"].endswith("(copy)"))
        self.assertEqual([m["kind"] for m in dup["messages"]],
                         [m["kind"] for m in src["messages"]])     # content copied
        # both now listed for this user
        ids = {x["id"] for x in c.get("/api/conversations").json()["conversations"]}
        self.assertIn(src["id"], ids); self.assertIn(dup["id"], ids)

    def test_conversations_are_isolated_per_user(self):
        # Two users share the SAME store root but must not see each other's data.
        api._STORE_BASE = tempfile.mkdtemp(); api._BUCKETS.clear()
        def client(user):
            api.app.dependency_overrides.clear()
            api.app.dependency_overrides[api.require_user] = lambda: user
            return TestClient(api.app)
        alice = client("user_alice"); a_cid = alice.post("/api/conversations").json()["id"]
        bob = client("user_bob")
        # Bob cannot see, open, rename, or delete Alice's conversation.
        self.assertEqual(bob.get("/api/conversations").json()["conversations"], [])
        self.assertEqual(bob.get(f"/api/conversations/{a_cid}").status_code, 404)
        self.assertEqual(bob.delete(f"/api/conversations/{a_cid}").json(), {"ok": True})  # no-op
        # Alice's conversation is still intact.
        self.assertEqual(client("user_alice").get(f"/api/conversations/{a_cid}").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
