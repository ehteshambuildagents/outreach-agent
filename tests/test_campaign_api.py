"""Campaign orchestration API tests.

Offline and deterministic: provider/search/model work is mocked, while the HTTP
surface, persistence, gates, idempotency, launch, and per-user isolation are
exercised through the real FastAPI app.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["AUTOMATION_FORCE_SQLITE"] = "1"

from starlette.testclient import TestClient  # noqa: E402

from automation import db as automation_db  # noqa: E402
from discovery.engine import DiscoveryResult  # noqa: E402
from discovery.models import Prospect  # noqa: E402
import server.api as api  # noqa: E402
import server.campaign_api as campaign_api  # noqa: E402


def _client(user="u_test"):
    api.app.dependency_overrides.clear()
    api.app.dependency_overrides[api.require_user] = lambda: user
    # /api/prospects authorizes via the demo-aware identity dependency; simulate
    # an authenticated member (demo isolation is covered in test_demo_session).
    api.app.dependency_overrides[api.demo_auth.require_identity_or_demo] = lambda: user
    return TestClient(api.app)


def _prospect(domain="acme.com"):
    return Prospect(
        company_name="Acme",
        website=f"https://{domain}",
        domain=domain,
        confidence=0.91,
        why_it_matches="Matches ICP",
        discovery_source="test",
    )


def _research(email="founder@acme.com"):
    return {
        "status": "ok",
        "research_score": 88,
        "pages_crawled": ["https://acme.com", "https://acme.com/about"],
        "hooks": [{"text": "Acme helps warehouse teams automate picking"}],
        "data": {
            "company_name": "Acme",
            "what_they_do": "Acme helps warehouse teams automate picking.",
            "target_customer": "warehouse operators",
            "unique_hook": "Acme focuses on warehouse picking automation.",
            "additional_hooks": ["They publish a warehouse automation guide."],
            "primary_contact_name": "Ada Lane",
            "primary_contact_role": "Founder",
            "public_contact_email": email,
            "recipient_route": email or "https://acme.com/contact",
            "has_enough_detail": True,
        },
    }


def _guard(decision="ALLOW"):
    return {
        "decision": decision,
        "overallRisk": 5 if decision == "ALLOW" else 85,
        "cost": {"risk": "LOW", "issues": [], "recommendations": []},
        "deliverability": {
            "risk": "LOW" if decision == "ALLOW" else "CRITICAL",
            "issues": [] if decision == "ALLOW" else ["bad email"],
            "recommendations": [],
        },
    }


class CampaignApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "AUTOMATION_FORCE_SQLITE": "1",
            "AUTOMATION_DB_PATH": os.path.join(self.tmp, "campaigns.db"),
        })
        self.env.start()
        automation_db.reset_default()
        api._BUCKETS.clear()
        campaign_api._campaigns = None
        campaign_api._prospects = None
        campaign_api._workflows = None

    def tearDown(self):
        api.app.dependency_overrides.clear()
        api._BUCKETS.clear()
        campaign_api._campaigns = None
        campaign_api._prospects = None
        campaign_api._workflows = None
        automation_db.reset_default()
        self.env.stop()

    def _patch_happy(self):
        return mock.patch.multiple(
            "server.campaign_api",
            discover=mock.Mock(return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], page=0, limit=1,
                returned=1, has_more=False, providers={"test": True})),
            research_company=mock.Mock(return_value=_research()),
            guard_assess=mock.Mock(return_value=_guard("ALLOW")),
        )

    def test_create_campaign_preview_and_launch_are_separate(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "Warehouse ops", "body": "Hi Ada,\n\nAcme's warehouse picking focus stood out. Could Saqua help test a small founder-led outbound motion tied to that exact ops audience?\n\nOpen to a quick look next week?"}):
            r = c.post("/api/campaigns", json={
                "name": "Warehouse SaaS",
                "idempotency_key": "same-key",
                "icp": {"raw": "warehouse SaaS founders", "keywords": ["warehouse"]},
                "limit": 1,
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["summary"]["launchable"], 1)
        self.assertEqual(data["workflows"], [])
        self.assertEqual(c.get("/api/automation/workflows").json()["workflows"], [])

        launched = c.post(f"/api/campaigns/{data['id']}/launch",
                          json={"provider": "dryrun"}).json()
        self.assertEqual(launched["status"], "launched")
        self.assertEqual(len(launched["workflow_ids"]), 1)
        self.assertEqual(len(launched["workflows"]), 1)

    def test_idempotency_returns_existing_campaign_without_rerunning(self):
        c = _client("alice")
        discover_mock = mock.Mock(return_value=DiscoveryResult(
            "ok", prospects=[_prospect()], returned=1, limit=1,
            providers={"test": True}))
        with mock.patch("server.campaign_api.discover", discover_mock), \
                mock.patch("server.campaign_api.research_company", return_value=_research()), \
                mock.patch("server.campaign_api.guard_assess", return_value=_guard("ALLOW")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This is a specific complete email body for Acme and its warehouse automation work."}):
            first = c.post("/api/campaigns", json={"name": "A", "idempotency_key": "idem", "icp": {"raw": "x"}}).json()
            second = c.post("/api/campaigns", json={"name": "A", "idempotency_key": "idem", "icp": {"raw": "x"}}).json()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(discover_mock.call_count, 1)

    def test_reject_stops_before_writer_and_launch(self):
        c = _client("alice")
        writer_mock = mock.Mock()
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], returned=1, providers={"test": True})), \
                mock.patch("server.campaign_api.research_company", return_value=_research()), \
                mock.patch("server.campaign_api.qualification.qualify",
                           return_value=mock.Mock(to_dict=lambda: {
                               "recommendation": "reject",
                               "qualification_score": 10,
                               "next_best_action": "Pass",
                           })), \
                mock.patch("server.campaign_api.writer.write_email", writer_mock):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["prospects"][0]["final_status"], "qualification_blocked")
        writer_mock.assert_not_called()
        self.assertEqual(c.post(f"/api/campaigns/{data['id']}/launch", json={}).status_code, 400)

    def test_route_only_contact_does_not_become_launchable(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["summary"]["route_only"], 1)
        self.assertEqual(data["summary"]["launchable"], 0)
        self.assertEqual(c.post(f"/api/campaigns/{data['id']}/launch", json={"provider": "dryrun"}).status_code, 400)

    def test_adding_valid_manual_recipient_makes_route_only_launchable_after_guard_pass(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()

        with mock.patch("server.campaign_api.guard_assess", return_value=_guard("ALLOW")):
            updated = c.post(
                f"/api/campaigns/{data['id']}/prospects/acme.com/recipient",
                json={"name": "Ada Lane", "email": "ada@acme.com"},
            )
        self.assertEqual(updated.status_code, 200)
        body = updated.json()
        prospect = body["result"]["prospects"][0]
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["summary"]["launchable"], 1)
        self.assertEqual(body["summary"]["route_only"], 0)
        self.assertEqual(prospect["final_status"], "sendable")
        self.assertEqual(prospect["email"]["to"], "ada@acme.com")
        self.assertEqual(prospect["email"]["recipient"]["email"], "ada@acme.com")
        self.assertTrue(prospect["email"]["recipient"]["manual"])

    def test_invalid_manual_recipient_email_rejected(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()

        r = c.post(
            f"/api/campaigns/{data['id']}/prospects/acme.com/recipient",
            json={"name": "Ada Lane", "email": "not-an-email"},
        )
        self.assertEqual(r.status_code, 422)
        unchanged = c.get(f"/api/campaigns/{data['id']}").json()
        self.assertEqual(unchanged["summary"]["launchable"], 0)

    def test_guessed_placeholder_manual_recipient_email_rejected(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()

        for email in ("founder@acme.com", "john.doe@acme.com", "ada@example.com"):
            r = c.post(
                f"/api/campaigns/{data['id']}/prospects/acme.com/recipient",
                json={"name": "Ada Lane", "email": email},
            )
            self.assertEqual(r.status_code, 422, email)

    def test_launch_creates_workflow_only_for_valid_manual_recipient(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()

        with mock.patch("server.campaign_api.guard_assess", return_value=_guard("ALLOW")):
            updated = c.post(
                f"/api/campaigns/{data['id']}/prospects/acme.com/recipient",
                json={"name": "Ada Lane", "email": "ada@acme.com"},
            ).json()
        launched = c.post(f"/api/campaigns/{updated['id']}/launch", json={"provider": "dryrun"}).json()
        self.assertEqual(launched["status"], "launched")
        self.assertEqual(len(launched["workflow_ids"]), 1)
        self.assertEqual(launched["workflows"][0]["to"], "ada@acme.com")

    def test_cadence_warning_distinguishes_causes_and_skips_healthy(self):
        from server.campaign_api import _cadence_warning
        base = {"final_status": "sendable", "guard": {"decision": "ALLOW"},
                "email": {"status": "ok", "to": "a@acme.com", "recipient": {"email": "a@acme.com"}}}
        # manual recipient + no follow-ups -> code gap
        manual = {**base, "manual_recipient": {"email": "a@acme.com"}, "followups": []}
        self.assertEqual(_cadence_warning(manual)["code"], "manual_recipient_no_followups")
        self.assertEqual(_cadence_warning(manual)["planned_steps"], 3)
        # auto-sendable + no follow-ups -> dropped at create time
        self.assertEqual(_cadence_warning({**base, "followups": []})["code"],
                         "followups_dropped_at_create")
        # has follow-ups -> no warning
        self.assertIsNone(_cadence_warning({**base, "followups": [{"slot": 2}]}))
        # not launchable (guard WARN, or route_only) -> no warning
        self.assertIsNone(_cadence_warning({**base, "guard": {"decision": "WARN"}, "followups": []}))
        self.assertIsNone(_cadence_warning({**base, "final_status": "route_only", "followups": []}))

    def test_result_with_warnings_enriches_only_incomplete_and_is_nondestructive(self):
        from server.campaign_api import _result_with_warnings
        good = {"final_status": "sendable", "guard": {"decision": "ALLOW"}, "domain": "good.com",
                "email": {"status": "ok", "to": "a@x.com", "recipient": {"email": "a@x.com"}},
                "followups": [{"slot": 2}]}
        bad = {"final_status": "sendable", "guard": {"decision": "ALLOW"}, "domain": "bad.com",
               "email": {"status": "ok", "to": "b@x.com", "recipient": {"email": "b@x.com"}},
               "followups": []}
        out = _result_with_warnings({"prospects": [good, bad]})
        self.assertNotIn("cadence_warning", out["prospects"][0])
        self.assertEqual(out["prospects"][1]["cadence_warning"]["code"], "followups_dropped_at_create")
        self.assertNotIn("cadence_warning", good)          # originals untouched
        self.assertNotIn("cadence_warning", bad)

    def test_launch_flags_manual_recipient_incomplete_cadence(self):
        # A manual-recipient prospect launches with zero follow-ups (3-step cadence);
        # the API must surface cadence_warning + launched_with_warnings and record a
        # launched_incomplete event so the UI can warn.
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.research_company", return_value=_research(email="")), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s",
                                         "body": "This email has enough body words and a specific Acme warehouse automation detail to pass preview."}):
            data = c.post("/api/campaigns", json={"name": "A", "icp": {"raw": "x"}}).json()
        with mock.patch("server.campaign_api.guard_assess", return_value=_guard("ALLOW")):
            updated = c.post(f"/api/campaigns/{data['id']}/prospects/acme.com/recipient",
                             json={"name": "Ada Lane", "email": "ada@acme.com"}).json()
        # Warning is visible in the detail view before launch.
        warned = updated["result"]["prospects"][0]["cadence_warning"]
        self.assertEqual(warned["code"], "manual_recipient_no_followups")

        launched = c.post(f"/api/campaigns/{updated['id']}/launch", json={"provider": "dryrun"}).json()
        self.assertEqual(len(launched["workflow_ids"]), 1)
        warns = launched["result"]["launched_with_warnings"]
        self.assertEqual([w["code"] for w in warns], ["manual_recipient_no_followups"])
        self.assertEqual(warns[0]["domain"], "acme.com")
        self.assertTrue(any(e["type"] == "launched_incomplete" for e in launched["events"]))

    def test_empty_discovery_returns_clean_no_valid_prospects_result(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "empty", prospects=[], returned=0, providers={"test": True},
                reason="No matching companies found for those filters.")):
            r = c.post("/api/campaigns", json={"name": "Bad broad ICP", "icp": {"raw": "tv show wiki pages"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["status"], "no_valid_prospects")
        self.assertEqual(data["summary"]["launchable"], 0)
        self.assertEqual(data["result"]["prospects"], [])

    def test_all_research_failures_return_needs_more_input_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], returned=1, providers={"test": True})), \
                mock.patch("server.campaign_api.research_company",
                           return_value={"status": "error", "reason": "Website could not be loaded."}), \
                mock.patch("server.campaign_api.writer.write_email") as writer_mock:
            r = c.post("/api/campaigns", json={"name": "Research fail", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["status"], "needs_more_input")
        self.assertEqual(data["summary"]["research_ok"], 0)
        self.assertEqual(data["summary"]["launchable"], 0)
        writer_mock.assert_not_called()

    def test_event_persistence_failure_does_not_hide_saved_campaign_result(self):
        c = _client("alice")
        with self._patch_happy(), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This is a specific complete email body for Acme and its warehouse automation work."}), \
                mock.patch.object(campaign_api.CampaignStore, "add_event", side_effect=RuntimeError("event db down")):
            r = c.post("/api/campaigns", json={"name": "A", "idempotency_key": "events", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["idempotent"], False)
        self.assertEqual(data["summary"]["launchable"], 1)
        self.assertEqual(data["events"], [])

    def test_discovery_exception_returns_clean_provider_failed_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover",
                        side_effect=RuntimeError("Tavily 503")):
            r = c.post("/api/campaigns", json={"name": "Provider down", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["status"], "provider_failed")
        self.assertTrue(data["result"]["reason"])
        self.assertEqual(data["result"]["prospects"], [])
        self.assertEqual(data["summary"]["launchable"], 0)

    def test_provider_timeout_returns_clean_provider_failed_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover",
                        side_effect=TimeoutError("read timed out")):
            r = c.post("/api/campaigns", json={"name": "Slow provider", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["result"]["status"], "provider_failed")
        self.assertTrue(data["result"]["reason"])

    def test_research_exception_returns_clean_research_failed_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], returned=1, providers={"test": True})), \
                mock.patch("server.campaign_api.research_company",
                           side_effect=RuntimeError("browser render failed")):
            r = c.post("/api/campaigns", json={"name": "Research boom", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["status"], "needs_more_input")
        self.assertEqual(data["result"]["prospects"][0]["final_status"], "research_failed")
        self.assertTrue(data["result"]["prospects"][0]["reason"])
        self.assertEqual(data["summary"]["research_ok"], 0)
        self.assertEqual(data["summary"]["launchable"], 0)

    def test_writer_exception_returns_clean_writer_failed_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], returned=1, providers={"test": True})), \
                mock.patch("server.campaign_api.research_company", return_value=_research()), \
                mock.patch("server.campaign_api.writer.write_email",
                           side_effect=RuntimeError("Anthropic 529 overloaded")):
            r = c.post("/api/campaigns", json={"name": "Writer boom", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["result"]["prospects"][0]["final_status"], "writer_failed")
        self.assertTrue(data["result"]["prospects"][0]["reason"])
        self.assertEqual(data["summary"]["launchable"], 0)

    def test_guard_exception_returns_clean_guard_blocked_not_500(self):
        c = _client("alice")
        with mock.patch("server.campaign_api.discover", return_value=DiscoveryResult(
                "ok", prospects=[_prospect()], returned=1, providers={"test": True})), \
                mock.patch("server.campaign_api.research_company", return_value=_research()), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This is a specific complete email body for Acme and its warehouse automation work."}), \
                mock.patch("server.campaign_api.guard_assess",
                           side_effect=RuntimeError("guard model exploded")):
            r = c.post("/api/campaigns", json={"name": "Guard boom", "icp": {"raw": "x"}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["result"]["prospects"][0]["final_status"], "guard_blocked")
        self.assertEqual(data["result"]["prospects"][0]["guard"]["decision"], "BLOCK")
        self.assertEqual(data["summary"]["launchable"], 0)

    def test_campaigns_are_isolated_per_user(self):
        with self._patch_happy(), \
                mock.patch("server.campaign_api.writer.write_email",
                           return_value={"status": "ok", "subject": "s", "body": "This is a specific complete email body for Acme and its warehouse automation work."}):
            created = _client("alice").post(
                "/api/campaigns", json={"name": "A", "idempotency_key": "one", "icp": {"raw": "x"}}
            ).json()
        self.assertEqual(_client("bob").get(f"/api/campaigns/{created['id']}").status_code, 404)
        self.assertEqual(_client("bob").get("/api/campaigns").json()["campaigns"], [])


class ProspectConcurrencyTests(unittest.TestCase):
    """The prospect phase used to run one company at a time, so a 3-prospect
    campaign measured 383s against a 300s platform limit, 94% of it sat in
    research, and a single unreachable host accounted for 250s of that."""

    @staticmethod
    def _prospects(n):
        return [_prospect(f"co{i}.com") for i in range(n)]

    def _run(self, work, prospects=None, deadline=None):
        """Run the phase with `work(prospect) -> dict` standing in for the real
        research/write/guard chain."""
        prospects = prospects or self._prospects(3)
        patches = [mock.patch.object(campaign_api, "_process_prospect",
                                     lambda user, p, icp, event, trace_id="": work(p))]
        if deadline is not None:
            patches.append(mock.patch.object(
                campaign_api, "CAMPAIGN_PROSPECT_DEADLINE_SECONDS", deadline))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return campaign_api._process_prospects(
            "u", prospects, {}, lambda *a: None, trace_id="t")

    def test_prospects_run_concurrently(self):
        """Independent I/O-bound work must overlap, not queue."""
        started = time.time()
        out = self._run(lambda p: (time.sleep(0.6),
                                   {"domain": p.domain, "final_status": "sendable"})[1])
        elapsed = time.time() - started
        self.assertEqual(len(out), 3)
        # Sequential would be >=1.8s; overlapped is barely more than one unit.
        self.assertLess(elapsed, 1.2, f"prospects did not overlap ({elapsed:.2f}s)")

    def test_result_order_matches_discovery_order(self):
        """Rank order is the product's output; finishing order must not change it."""
        delays = {"co0.com": 0.5, "co1.com": 0.1, "co2.com": 0.3}

        def work(p):
            time.sleep(delays[p.domain])
            return {"domain": p.domain, "final_status": "sendable"}

        out = self._run(work)
        self.assertEqual([r["domain"] for r in out],
                         ["co0.com", "co1.com", "co2.com"])

    def test_slow_prospect_is_bounded_and_the_rest_survive(self):
        """One unreachable host must not spend the whole request budget."""
        def work(p):
            if p.domain == "co1.com":
                time.sleep(30)                      # never finishes in time
            return {"domain": p.domain, "final_status": "sendable"}

        started = time.time()
        out = self._run(work, deadline=1)
        elapsed = time.time() - started
        self.assertLess(elapsed, 5, "the deadline did not bound the phase")
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1]["final_status"], "timed_out")
        self.assertIn("time limit", out[1]["reason"])
        # ...and the prospects that DID finish are still returned.
        self.assertEqual(out[0]["final_status"], "sendable")
        self.assertEqual(out[2]["final_status"], "sendable")

    def test_a_crashing_prospect_does_not_end_the_campaign(self):
        def work(p):
            if p.domain == "co1.com":
                raise RuntimeError("boom")
            return {"domain": p.domain, "final_status": "sendable"}

        out = self._run(work)
        self.assertEqual(out[1]["final_status"], "research_failed")
        self.assertEqual(out[0]["final_status"], "sendable")
        self.assertEqual(out[2]["final_status"], "sendable")

    def test_no_prospects_is_not_an_error(self):
        self.assertEqual(campaign_api._process_prospects(
            "u", [], {}, lambda *a: None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
