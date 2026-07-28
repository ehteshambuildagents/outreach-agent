"""Billing: plan resolution, Lemon Squeezy webhook handling (idempotent), and the
HTTP surface.

No network and no Lemon Squeezy keys: the one outbound call (checkout) is the only
thing mocked, and webhook signatures are built with the same HMAC the real LS uses
(``billing.lemonsqueezy_client.sign_payload``), so the whole
event -> durable-state -> enforced-limit path runs offline. This mirrors what the
live Test-mode verification in BILLING_RUNBOOK.md does with real keys.

Canonical plan ids are pro (50) / max (100); the public /pricing page markets them
as Starter/Growth, which are accepted as aliases.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate onto a temp SQLite DB BEFORE anything opens a connection.
os.environ["AUTOMATION_FORCE_SQLITE"] = "1"
_DB_FILE = os.path.join(tempfile.mkdtemp(), "billing_test.db")
os.environ["AUTOMATION_DB_PATH"] = _DB_FILE

from starlette.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
from billing import store as bstore  # noqa: E402
from billing import lemonsqueezy_client as ls_client  # noqa: E402
from billing import webhook  # noqa: E402
from automation import migrate  # noqa: E402
from automation.db import Database  # noqa: E402
from config import settings  # noqa: E402

_SECRET = "ls_whsec_testsecret"


def setUpModule():
    migrate.run(Database(), verbose=False)


def _reset_tables():
    db = Database()
    for t in ("billing_events", "billing_invoices", "billing_subscriptions",
              "billing_customers"):
        db.execute(f"DELETE FROM {t}")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


# ── Lemon Squeezy event fixtures (JSON:API body + meta.custom_data) ─────
def _sub_event(user_id, plan, status, *, sub_id="1", customer=100, variant="v_x",
               renews_at=None, ends_at=None, portal="https://portal.ls/x",
               etype="subscription_created"):
    attrs = {"status": status, "customer_id": customer, "variant_id": variant,
             "renews_at": renews_at, "ends_at": ends_at,
             "urls": {"customer_portal": portal}}
    custom = {"user_id": user_id}
    if plan is not None:
        custom["plan"] = plan
    return {"meta": {"event_name": etype, "custom_data": custom},
            "data": {"type": "subscriptions", "id": sub_id, "attributes": attrs}}


def _payment_event(user_id, *, inv_id="inv_1", sub_id="1", customer=100,
                   total=6500, etype="subscription_payment_failed"):
    return {"meta": {"event_name": etype, "custom_data": {"user_id": user_id}},
            "data": {"type": "subscription-invoices", "id": inv_id,
                     "attributes": {"subscription_id": sub_id, "customer_id": customer,
                                    "total": total, "currency": "usd", "status": "paid"}}}


# ── Plan resolution (canonical pro/max + marketing aliases) ────────────
class PlanModelTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def test_plan_limits_map(self):
        self.assertEqual(billing.plan_limit("free"), settings.FREE_PROSPECT_LIMIT)
        self.assertEqual(billing.plan_limit("pro"), 50)
        self.assertEqual(billing.plan_limit("max"), 100)
        self.assertEqual(billing.plan_limit("enterprise"), 300)
        # Unknown / junk falls back to Free, never opens up.
        self.assertEqual(billing.plan_limit("platinum"), settings.FREE_PROSPECT_LIMIT)
        self.assertEqual(billing.plan_limit(None), settings.FREE_PROSPECT_LIMIT)

    def test_marketing_aliases_normalize(self):
        # The public page sells pro/max as Starter/Growth; both must resolve.
        self.assertEqual(billing.normalize_plan("starter"), "pro")
        self.assertEqual(billing.normalize_plan("growth"), "max")
        self.assertEqual(billing.plan_limit("starter"), 50)
        self.assertEqual(billing.plan_limit("growth"), 100)

    def test_default_is_free(self):
        self.assertEqual(billing.plan_for_user("nobody"), "free")
        self.assertEqual(billing.limit_for_user("nobody"), settings.FREE_PROSPECT_LIMIT)

    def test_active_subscription_sets_plan(self):
        bstore.upsert_subscription("u1", "max", "active",
                                   provider_subscription_id="sub_x")
        self.assertEqual(billing.plan_for_user("u1"), "max")
        self.assertEqual(billing.limit_for_user("u1"), 100)

    def test_on_trial_is_entitling(self):
        # LS uses "on_trial" (not Stripe's "trialing") for an entitling trial.
        bstore.upsert_subscription("ut", "pro", "on_trial",
                                   provider_subscription_id="sub_t")
        self.assertEqual(billing.plan_for_user("ut"), "pro")
        self.assertEqual(billing.limit_for_user("ut"), 50)

    def test_cancelled_without_period_end_is_free(self):
        bstore.upsert_subscription("u2", "max", "cancelled",
                                   provider_subscription_id="sub_y")
        self.assertEqual(billing.plan_for_user("u2"), "free")
        self.assertEqual(billing.limit_for_user("u2"), settings.FREE_PROSPECT_LIMIT)

    def test_entitlements_shape(self):
        bstore.upsert_subscription("u3", "pro", "active",
                                   provider_subscription_id="sub_z",
                                   current_period_end=999.0)
        ent = billing.entitlements("u3", prospects_used=10)
        self.assertEqual(ent["plan"], "pro")
        self.assertEqual(ent["prospect_limit"], 50)
        self.assertEqual(ent["prospects_used"], 10)
        self.assertEqual(ent["prospects_remaining"], 40)
        self.assertEqual(ent["status"], "active")
        self.assertEqual(ent["current_period_end"], 999.0)


# ── Env-var name mapping (regression: Railway uses LEMON_SQUEEZY_*) ──────
class EnvNameMappingTests(unittest.TestCase):
    """The Railway variables are LEMON_SQUEEZY_* (with the underscore). Settings
    must read those exact names, so a real deploy is 'configured' and the webhook
    stops returning the 'billing not configured' 503. The older no-underscore
    LEMONSQUEEZY_* names remain valid as a fallback."""

    _KEYS = ["LEMON_SQUEEZY_API_KEY", "LEMON_SQUEEZY_STORE_ID",
             "LEMON_SQUEEZY_WEBHOOK_SECRET", "LEMONSQUEEZY_API_KEY",
             "LEMONSQUEEZY_STORE_ID", "LEMONSQUEEZY_WEBHOOK_SECRET"]

    def _snapshot_with(self, env):
        """Reload config.settings under ``env`` and return a snapshot of the
        resolved values (importlib.reload mutates the module in place, so we read
        while the env is applied, then restore the original env and reload back)."""
        import importlib
        saved = {k: os.environ.pop(k, None) for k in self._KEYS}
        try:
            os.environ.update(env)
            import config.settings as s
            importlib.reload(s)
            return {"api_key": s.LEMONSQUEEZY_API_KEY,
                    "store_id": s.LEMONSQUEEZY_STORE_ID,
                    "secret": s.LEMONSQUEEZY_WEBHOOK_SECRET,
                    "enabled": s.lemonsqueezy_enabled()}
        finally:
            for k in self._KEYS:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]
            import config.settings as s
            importlib.reload(s)

    def test_railway_underscore_names_configure_billing(self):
        snap = self._snapshot_with({"LEMON_SQUEEZY_API_KEY": "ls_test_railway",
                                    "LEMON_SQUEEZY_STORE_ID": "42",
                                    "LEMON_SQUEEZY_WEBHOOK_SECRET": "whsec_rw"})
        self.assertEqual(snap["api_key"], "ls_test_railway")
        self.assertEqual(snap["store_id"], "42")
        self.assertEqual(snap["secret"], "whsec_rw")
        self.assertTrue(snap["enabled"])

    def test_legacy_no_underscore_names_still_read(self):
        snap = self._snapshot_with({"LEMONSQUEEZY_API_KEY": "ls_legacy",
                                    "LEMONSQUEEZY_STORE_ID": "7"})
        self.assertEqual(snap["api_key"], "ls_legacy")
        self.assertEqual(snap["store_id"], "7")
        self.assertTrue(snap["enabled"])

    def test_unset_is_disabled(self):
        self.assertFalse(self._snapshot_with({})["enabled"])


# ── Webhook signature verification (LS: raw-body HMAC, hex, no timestamp) ─
class SignatureTests(unittest.TestCase):
    def test_roundtrip_verifies(self):
        payload = json.dumps({"meta": {"event_name": "ping"}}).encode()
        header = ls_client.sign_payload(payload, _SECRET)
        event = ls_client.verify_signature(payload, header, secret=_SECRET)
        self.assertEqual(event["meta"]["event_name"], "ping")

    def test_bad_signature_rejected(self):
        payload = b'{"meta":{"event_name":"ping"}}'
        header = ls_client.sign_payload(payload, "someoneelsesecret")
        with self.assertRaises(ValueError):
            ls_client.verify_signature(payload, header, secret=_SECRET)

    def test_tampered_payload_rejected(self):
        payload = b'{"data":{"attributes":{"total":100}}}'
        header = ls_client.sign_payload(payload, _SECRET)
        with self.assertRaises(ValueError):
            ls_client.verify_signature(
                b'{"data":{"attributes":{"total":999999}}}', header, secret=_SECRET)

    def test_missing_signature_rejected(self):
        with self.assertRaises(ValueError):
            ls_client.verify_signature(b'{}', "", secret=_SECRET)


# ── Webhook event handling (idempotent) ────────────────────────────────
class WebhookLogicTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def test_subscription_created_activates_plan(self):
        res = webhook.handle_event(_sub_event("buyer", "max", "active"))
        self.assertEqual(res["status"], "processed")
        self.assertEqual(billing.plan_for_user("buyer"), "max")
        # The customer mapping is remembered for later customer-only events.
        self.assertEqual(bstore.user_id_for_customer("100"), "buyer")

    def test_plan_derived_from_variant_when_custom_absent(self):
        # No plan in custom_data => derive it from the variant id via settings.
        settings.LEMONSQUEEZY_VARIANT_IDS["max_monthly"] = "v_max_m"
        try:
            webhook.handle_event(_sub_event("buyer2", None, "active",
                                            variant="v_max_m", sub_id="2"))
            self.assertEqual(billing.plan_for_user("buyer2"), "max")
        finally:
            settings.LEMONSQUEEZY_VARIANT_IDS["max_monthly"] = ""

    def test_unknown_variant_grants_no_access(self):
        # No custom plan AND an unrecognised variant => cannot name a paid plan, so
        # the buyer gets Free access, never a paid cap by accident.
        webhook.handle_event(_sub_event("nogrant", None, "active",
                                        variant="v_unknown", sub_id="u1"))
        self.assertEqual(billing.plan_for_user("nogrant"), "free")
        self.assertEqual(billing.limit_for_user("nogrant"), settings.FREE_PROSPECT_LIMIT)

    def test_subscription_updated_sets_period_and_status(self):
        webhook.handle_event(_sub_event("buyer", "max", "active"))
        webhook.handle_event(_sub_event(
            "buyer", "max", "active", sub_id="1",
            renews_at="2030-01-01T00:00:00.000000Z", etype="subscription_updated"))
        sub = bstore.active_subscription("buyer")
        self.assertEqual(sub["plan"], "max")
        self.assertIsNotNone(sub["current_period_end"])
        # The portal URL LS delivered is persisted for /api/billing/portal.
        self.assertEqual(bstore.portal_url_for_user("buyer"), "https://portal.ls/x")

    def test_cancel_retains_access_until_ends_at(self):
        webhook.handle_event(_sub_event("buyer", "max", "active"))
        self.assertEqual(billing.limit_for_user("buyer"), 100)
        future = datetime(2999, 1, 1, tzinfo=timezone.utc)
        webhook.handle_event(_sub_event("buyer", "max", "cancelled",
                                        ends_at=_iso(future),
                                        etype="subscription_cancelled"))
        # Still entitled during the paid-through grace window.
        self.assertEqual(billing.plan_for_user("buyer"), "max")
        self.assertEqual(billing.limit_for_user("buyer"), 100)
        # ...but not once ends_at has passed.
        self.assertIsNone(bstore.active_subscription("buyer",
                                                     now=future.timestamp() + 1))

    def test_subscription_expired_drops_to_free(self):
        webhook.handle_event(_sub_event("buyer", "pro", "active"))
        webhook.handle_event(_sub_event("buyer", "pro", "expired",
                                        etype="subscription_expired"))
        self.assertEqual(billing.plan_for_user("buyer"), "free")

    def test_payment_failed_marks_past_due(self):
        webhook.handle_event(_sub_event("buyer", "pro", "active"))
        webhook.handle_event(_payment_event("buyer"))
        # past_due is not an entitling status -> back to Free until they pay.
        self.assertEqual(billing.plan_for_user("buyer"), "free")
        rows = Database().query("SELECT status FROM billing_subscriptions "
                                "WHERE provider_subscription_id='1'")
        self.assertEqual(rows[0]["status"], "past_due")

    def test_payment_recovered_restores_active(self):
        webhook.handle_event(_sub_event("buyer", "pro", "active"))
        webhook.handle_event(_payment_event("buyer"))                 # -> past_due
        self.assertEqual(billing.plan_for_user("buyer"), "free")
        webhook.handle_event(_payment_event(
            "buyer", inv_id="inv_2", etype="subscription_payment_recovered"))
        self.assertEqual(billing.plan_for_user("buyer"), "pro")

    def test_replay_is_idempotent(self):
        event = _sub_event("buyer", "max", "active")
        sig = ls_client.sign_payload(json.dumps(event).encode(), _SECRET)
        first = webhook.handle_event(event, event_id=sig)
        second = webhook.handle_event(event, event_id=sig)   # exact redelivery
        self.assertEqual(first["status"], "processed")
        self.assertEqual(second["status"], "duplicate")
        # Still exactly one subscription row, still max.
        rows = Database().query("SELECT * FROM billing_subscriptions WHERE user_id='buyer'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(billing.plan_for_user("buyer"), "max")

    def test_invoice_replay_does_not_duplicate(self):
        webhook.handle_event(_sub_event("buyer", "pro", "active"))
        inv = _payment_event("buyer", inv_id="in_9",
                             etype="subscription_payment_success")
        sig = ls_client.sign_payload(json.dumps(inv).encode(), _SECRET)
        webhook.handle_event(inv, event_id=sig)
        webhook.handle_event(dict(inv), event_id=sig)   # replay
        rows = Database().query("SELECT * FROM billing_invoices WHERE provider_invoice_id='in_9'")
        self.assertEqual(len(rows), 1)

    def test_unattributed_subscription_is_deferred_then_recovered(self):
        # A subscription event that can't be attributed (no custom_data.user_id and
        # no customer map yet) must NOT be applied or ledgered, so a Resend after
        # the map exists recovers the paid plan instead of being deduped away.
        settings.LEMONSQUEEZY_VARIANT_IDS["pro_monthly"] = "v_pro_m"
        try:
            evt = {"meta": {"event_name": "subscription_created", "custom_data": {}},
                   "data": {"type": "subscriptions", "id": "s9",
                            "attributes": {"status": "active", "customer_id": 777,
                                           "variant_id": "v_pro_m",
                                           "urls": {"customer_portal": "https://p"}}}}
            res = webhook.handle_event(evt, event_id="sig9")
            self.assertEqual(res["status"], "deferred")
            self.assertEqual(billing.plan_for_user("late_user"), "free")
            self.assertFalse(bstore.event_processed("sig9"))   # recoverable
            # order_created records the customer<->user map.
            webhook.handle_event({"meta": {"event_name": "order_created",
                                           "custom_data": {"user_id": "late_user"}},
                                  "data": {"type": "orders", "id": "o1",
                                           "attributes": {"customer_id": 777}}})
            self.assertEqual(bstore.user_id_for_customer("777"), "late_user")
            # Resend the same subscription event: now attributable -> applied.
            res2 = webhook.handle_event(evt, event_id="sig9")
            self.assertEqual(res2["status"], "processed")
            self.assertEqual(billing.plan_for_user("late_user"), "pro")
        finally:
            settings.LEMONSQUEEZY_VARIANT_IDS["pro_monthly"] = ""

    def test_unhandled_event_is_ignored(self):
        res = webhook.handle_event({"meta": {"event_name": "license_key_created"},
                                    "data": {"id": "x", "attributes": {}}})
        self.assertEqual(res["status"], "ignored")


# ── Server-side enforcement (the chat research gate reads the plan cap) ──
class EnforcementGateTests(unittest.TestCase):
    """The proof that a purchased plan actually LIFTS the cap the product enforces.

    ``chat.agent`` caches ``billing.limit_for_user`` onto the conversation
    workspace as ``prospect_limit``; the research/write/send tools then gate new
    prospects through ``chat.tools._free_slot_blocked``. These tests drive that
    gate directly at Free (3), Pro (50) and Max (100) so the end-to-end claim —
    buying Pro/Max raises the enforced cap, not just the Settings card — is covered
    offline, not just by inspection."""

    def _conv(self, limit, used):
        from chat.models import Conversation
        c = Conversation()
        # This is exactly what chat.agent writes per turn: the resolved plan cap
        # and the per-user usage (distinct prospect keys already worked).
        c.workspace["prospect_limit"] = limit
        c.workspace["usage"] = {"prospects": list(used)}
        return c

    def test_gate_uses_cached_plan_limit(self):
        from chat import tools
        self.assertEqual(tools._plan_limit(self._conv(50, [])), 50)
        self.assertEqual(tools._plan_limit(self._conv(100, [])), 100)
        # Missing cache => Free fallback, never an open cap.
        c = self._conv(0, [])
        del c.workspace["prospect_limit"]
        self.assertEqual(tools._plan_limit(c), settings.FREE_PROSPECT_LIMIT)

    def test_free_user_blocked_at_cap_new_paid_user_not(self):
        from chat import tools
        # Free user at 3/3 worked prospects: a NEW company is blocked...
        free_used = ["a.com", "b.com", "c.com"]
        free = self._conv(settings.FREE_PROSPECT_LIMIT, free_used)
        self.assertTrue(tools._free_slot_blocked(free, "newco.com"))
        # ...but an already-worked one is still allowed (no new slot consumed).
        self.assertFalse(tools._free_slot_blocked(free, "a.com"))
        # Same usage, but on Max (100): the new company is NOT blocked.
        mx = self._conv(100, free_used)
        self.assertFalse(tools._free_slot_blocked(mx, "newco.com"))

    def test_max_user_blocked_at_101(self):
        from chat import tools
        used = [f"c{i}.com" for i in range(100)]        # Max exactly full at 100
        mx = self._conv(100, used)
        self.assertEqual(tools._remaining_prospects(mx), 0)
        self.assertTrue(tools._free_slot_blocked(mx, "c100.com"))   # the 101st
        # A Pro user (50) is already blocked at prospect 51.
        pro = self._conv(50, [f"c{i}.com" for i in range(50)])
        self.assertTrue(tools._free_slot_blocked(pro, "c50.com"))

    def test_end_to_end_subscription_lifts_the_enforced_cap(self):
        """A real webhook flips the plan, and the SAME number the gate enforces
        (``limit_for_user``) moves with it — Free 3 -> Max 100 -> back to Free."""
        from chat import tools
        _reset_tables()
        self.assertEqual(billing.limit_for_user("gate_user"), settings.FREE_PROSPECT_LIMIT)
        webhook.handle_event(_sub_event("gate_user", "max", "active", sub_id="g1"))
        self.assertEqual(billing.limit_for_user("gate_user"), 100)
        # 100 worked prospects are exactly the cap; the 101st is blocked.
        conv = self._conv(billing.limit_for_user("gate_user"),
                          [f"c{i}.com" for i in range(100)])
        self.assertTrue(tools._free_slot_blocked(conv, "c100.com"))
        # Cancel with no remaining period -> entitlement drops back to Free.
        webhook.handle_event(_sub_event("gate_user", "max", "cancelled",
                                        sub_id="g1", etype="subscription_cancelled"))
        self.assertEqual(billing.limit_for_user("gate_user"), settings.FREE_PROSPECT_LIMIT)


# ── HTTP surface ───────────────────────────────────────────────────────
class BillingApiTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()
        import server.api as api
        self.api = api
        api._STORE_BASE = tempfile.mkdtemp()
        api._BUCKETS.clear()
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides[api.require_user] = lambda: "member1"
        api.app.dependency_overrides[api.require_approved_user] = lambda: "member1"
        api.app.dependency_overrides[api.require_member_or_demo] = lambda: "member1"
        # Make LS "configured" for the checkout path (the call itself is mocked).
        settings.LEMONSQUEEZY_API_KEY = "ls_test_dummy"
        settings.LEMONSQUEEZY_STORE_ID = "1"
        settings.LEMONSQUEEZY_WEBHOOK_SECRET = _SECRET
        settings.LEMONSQUEEZY_VARIANT_IDS["pro_monthly"] = "v_pro_m"
        settings.LEMONSQUEEZY_VARIANT_IDS["max_monthly"] = "v_max_m"
        self.client = TestClient(api.app)

    def tearDown(self):
        self.api.app.dependency_overrides.clear()

    def test_get_billing_defaults_to_free(self):
        body = self.client.get("/api/billing").json()
        self.assertEqual(body["plan"], "free")
        self.assertEqual(body["prospect_limit"], settings.FREE_PROSPECT_LIMIT)

    def test_get_billing_reflects_paid_plan(self):
        bstore.upsert_subscription("member1", "max", "active",
                                   provider_subscription_id="sub_m1")
        body = self.client.get("/api/billing").json()
        self.assertEqual(body["plan"], "max")
        self.assertEqual(body["prospect_limit"], 100)
        self.assertEqual(body["status"], "active")

    def test_checkout_returns_url(self):
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout",
                               return_value={"url": "https://saqua.lemonsqueezy.com/x",
                                             "id": "chk_9"}) as m:
            resp = self.client.post("/api/billing/checkout", json={"plan": "max"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["url"], "https://saqua.lemonsqueezy.com/x")
        _, kwargs = m.call_args
        self.assertEqual(kwargs["user_id"], "member1")
        self.assertEqual(kwargs["plan"], "max")
        self.assertEqual(kwargs["variant_id"], "v_max_m")

    def test_checkout_accepts_marketing_alias(self):
        # "starter" (public name) normalizes to canonical "pro" + its variant.
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout",
                               return_value={"url": "u", "id": "i"}) as m:
            resp = self.client.post("/api/billing/checkout", json={"plan": "starter"})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["plan"], "pro")
        self.assertEqual(kwargs["variant_id"], "v_pro_m")

    def test_checkout_rejects_unknown_plan(self):
        resp = self.client.post("/api/billing/checkout", json={"plan": "platinum"})
        self.assertEqual(resp.status_code, 422)

    def test_checkout_503_when_billing_disabled(self):
        settings.LEMONSQUEEZY_API_KEY = ""
        resp = self.client.post("/api/billing/checkout", json={"plan": "pro"})
        self.assertEqual(resp.status_code, 503)

    def test_portal_returns_stored_url(self):
        bstore.upsert_subscription("member1", "max", "active",
                                   provider_subscription_id="sub_p1",
                                   portal_url="https://portal.ls/member1")
        resp = self.client.post("/api/billing/portal")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["url"], "https://portal.ls/member1")

    def test_portal_400_without_billing_account(self):
        resp = self.client.post("/api/billing/portal")
        self.assertEqual(resp.status_code, 400)

    def test_upgrade_pro_to_max_preserves_prospects_used(self):
        # Usage lives in the per-user store, independent of the plan; upgrading the
        # subscription must not reset it.
        self.api._store_for("member1").save_usage(
            {"prospects": ["a.com", "b.com", "c.com", "d.com", "e.com"]})
        webhook.handle_event(_sub_event("member1", "pro", "active", sub_id="up1"))
        b1 = self.client.get("/api/billing").json()
        self.assertEqual(b1["plan"], "pro")
        self.assertEqual(b1["prospect_limit"], 50)
        self.assertEqual(b1["prospects_used"], 5)
        # Upgrade pro -> max (same subscription id, updated).
        webhook.handle_event(_sub_event("member1", "max", "active", sub_id="up1",
                                        etype="subscription_updated"))
        b2 = self.client.get("/api/billing").json()
        self.assertEqual(b2["plan"], "max")
        self.assertEqual(b2["prospect_limit"], 100)
        self.assertEqual(b2["prospects_used"], 5)   # preserved across the upgrade

    def test_webhook_processes_signed_event_and_plan_flips(self):
        event = _sub_event("member1", "max", "active", sub_id="wh1")
        payload = json.dumps(event).encode()
        header = ls_client.sign_payload(payload, _SECRET)
        resp = self.client.post(
            "/api/billing/webhook", content=payload,
            headers={"x-signature": header, "x-event-name": "subscription_created"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["received"], True)
        # The enforced plan (what GET /api/billing reports) is now Max.
        self.assertEqual(self.client.get("/api/billing").json()["plan"], "max")

    def test_webhook_rejects_bad_signature(self):
        payload = b'{"meta":{"event_name":"subscription_created"}}'
        resp = self.client.post("/api/billing/webhook", content=payload,
                                headers={"x-signature": "deadbeef",
                                         "x-event-name": "subscription_created"})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
