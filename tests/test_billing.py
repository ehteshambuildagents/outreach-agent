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
import time
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
              "billing_customers", "prospect_usage"):
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

    def test_plan_catalog_is_the_canonical_price_source(self):
        # The upgrade UI reads price/name/limit from here — never hardcoded.
        cat = billing.plan_catalog()
        by_plan = {p["plan"]: p for p in cat}
        self.assertEqual({"pro", "max"}, set(by_plan))
        self.assertEqual(by_plan["pro"]["name"], "Pro")
        self.assertEqual(by_plan["pro"]["price"], 65)
        self.assertEqual(by_plan["pro"]["interval"], "monthly")
        self.assertEqual(by_plan["pro"]["prospect_limit"], 50)
        self.assertEqual(by_plan["max"]["price"], 100)
        # Yearly is the two-months-free price.
        self.assertEqual(
            {p["plan"]: p["price"] for p in billing.plan_catalog("yearly")},
            {"pro": 650, "max": 1000})

    def test_entitlements_carry_upgrade_fields(self):
        # Free user: not paid, pitched Pro, catalog present.
        free = billing.entitlements("u_free", prospects_used=1)
        self.assertFalse(free["is_paid"])
        self.assertEqual(free["recommended_upgrade"], "pro")
        self.assertEqual(free["plan_name"], "Free")
        self.assertTrue(free["catalog"])
        # Pro user: paid, pitched Max.
        bstore.upsert_subscription("u_pro", "pro", "active",
                                   provider_subscription_id="sub_p")
        pro = billing.entitlements("u_pro", prospects_used=0)
        self.assertTrue(pro["is_paid"])
        self.assertEqual(pro["recommended_upgrade"], "max")

    def test_open_subscription_flags_live_but_not_dead_subscriptions(self):
        # The duplicate-subscription guard rests on this: a LIVE subscription blocks
        # a second checkout; a cancelled/expired one does not (it won't re-bill).
        bstore.upsert_subscription("uo", "pro", "active",
                                   provider_subscription_id="o1")
        self.assertIsNotNone(bstore.open_subscription("uo"))
        bstore.set_subscription_status("o1", "past_due")   # still live, LS retries
        self.assertIsNotNone(bstore.open_subscription("uo"))
        bstore.set_subscription_status("o1", "paused")     # still live, can resume
        self.assertIsNotNone(bstore.open_subscription("uo"))
        bstore.set_subscription_status("o1", "cancelled")  # won't re-bill => safe
        self.assertIsNone(bstore.open_subscription("uo"))
        bstore.set_subscription_status("o1", "expired")    # gone
        self.assertIsNone(bstore.open_subscription("uo"))


# ── Env-var name mapping (regression: Railway uses LEMON_SQUEEZY_*) ──────
class EnvNameMappingTests(unittest.TestCase):
    """The Railway variables are LEMON_SQUEEZY_* (with the underscore). Settings
    must read those exact names, so a real deploy is 'configured' and the webhook
    stops returning the 'billing not configured' 503. The older no-underscore
    LEMONSQUEEZY_* names remain valid as a fallback."""

    _KEYS = ["LEMON_SQUEEZY_API_KEY", "LEMON_SQUEEZY_STORE_ID",
             "LEMON_SQUEEZY_WEBHOOK_SECRET", "LEMONSQUEEZY_API_KEY",
             "LEMONSQUEEZY_STORE_ID", "LEMONSQUEEZY_WEBHOOK_SECRET",
             "LEMON_SQUEEZY_MODE", "LEMONSQUEEZY_MODE",
             "LEMON_SQUEEZY_API_KEY_LIVE", "LEMON_SQUEEZY_API_KEY_TEST",
             # Deployment-environment vars: cleared so each snapshot resolves the
             # billing mode hermetically (production vs. not is part of the policy).
             "RAILWAY_ENVIRONMENT", "ENVIRONMENT", "APP_ENV"]

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
                    "mode": s.LEMONSQUEEZY_MODE,
                    "enabled": s.lemonsqueezy_enabled(),
                    "mode_resolved": s.billing_mode_resolved(),
                    "config_error": s.billing_config_error(),
                    "config_error_pro": s.billing_config_error("pro", "monthly")}
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

    def test_mode_selects_the_suffixed_credentials(self):
        # Both sets configured; the mode switch decides which is live. Test mode
        # picks the _TEST key even though a _LIVE key is also present.
        snap = self._snapshot_with({
            "LEMON_SQUEEZY_MODE": "test",
            "LEMON_SQUEEZY_API_KEY_TEST": "ls_key_test",
            "LEMON_SQUEEZY_API_KEY_LIVE": "ls_key_live",
            "LEMON_SQUEEZY_STORE_ID": "7", "LEMON_SQUEEZY_WEBHOOK_SECRET": "whsec"})
        self.assertEqual(snap["mode"], "test")
        self.assertEqual(snap["api_key"], "ls_key_test")

    def test_live_mode_prefers_live_suffix_then_plain(self):
        snap = self._snapshot_with({
            "LEMON_SQUEEZY_MODE": "live",
            "LEMON_SQUEEZY_API_KEY_LIVE": "ls_key_live",
            "LEMON_SQUEEZY_API_KEY": "ls_key_plain",
            "LEMON_SQUEEZY_STORE_ID": "7", "LEMON_SQUEEZY_WEBHOOK_SECRET": "whsec"})
        self.assertEqual(snap["mode"], "live")
        self.assertEqual(snap["api_key"], "ls_key_live")

    def test_unset_mode_off_production_defaults_to_test_not_live(self):
        # No mode set, only plain names, NOT production: resolves to Test (safe),
        # never Live. Credentials still read from the plain names (backward compat).
        snap = self._snapshot_with({"LEMON_SQUEEZY_API_KEY": "ls_plain",
                                    "LEMON_SQUEEZY_STORE_ID": "1",
                                    "LEMON_SQUEEZY_WEBHOOK_SECRET": "whsec"})
        self.assertEqual(snap["mode"], "test")   # was "live" — unsafe silent fallback
        self.assertEqual(snap["api_key"], "ls_plain")
        self.assertTrue(snap["enabled"])


# ── Mode safety: fail closed on an ambiguous Lemon Squeezy mode ──────────
class BillingModeSafetyTests(EnvNameMappingTests):
    """The mode must be EXPLICIT in production; a missing/invalid mode must never
    silently select Live. Off production an unset mode safely defaults to Test, and
    an invalid mode fails closed everywhere. Reuses EnvNameMappingTests' hermetic
    snapshot (which now also clears the deployment-environment vars)."""

    _CREDS = {"LEMON_SQUEEZY_API_KEY": "k", "LEMON_SQUEEZY_STORE_ID": "7",
              "LEMON_SQUEEZY_WEBHOOK_SECRET": "whsec"}

    def test_production_missing_mode_blocks_checkout(self):
        snap = self._snapshot_with({**self._CREDS, "ENVIRONMENT": "production"})
        self.assertEqual(snap["mode"], "")            # unresolved, NOT "live"
        self.assertFalse(snap["mode_resolved"])
        self.assertFalse(snap["enabled"])
        self.assertIn("mode is not explicitly set", snap["config_error"])

    def test_production_invalid_mode_blocks_checkout(self):
        snap = self._snapshot_with({**self._CREDS, "ENVIRONMENT": "production",
                                    "LEMON_SQUEEZY_MODE": "prod"})
        self.assertEqual(snap["mode"], "")
        self.assertFalse(snap["enabled"])
        self.assertTrue(snap["config_error"])

    def test_production_explicit_live_is_enabled(self):
        snap = self._snapshot_with({**self._CREDS, "ENVIRONMENT": "production",
                                    "LEMON_SQUEEZY_MODE": "live"})
        self.assertEqual(snap["mode"], "live")
        self.assertTrue(snap["enabled"])
        self.assertEqual(snap["config_error"], "")    # mode+creds complete

    def test_non_production_missing_mode_defaults_to_test(self):
        snap = self._snapshot_with({**self._CREDS})
        self.assertEqual(snap["mode"], "test")
        self.assertTrue(snap["enabled"])

    def test_invalid_mode_fails_closed_off_production_too(self):
        snap = self._snapshot_with({**self._CREDS, "LEMON_SQUEEZY_MODE": "bogus"})
        self.assertEqual(snap["mode"], "")
        self.assertFalse(snap["enabled"])

    def test_explicit_test_mode_is_enabled(self):
        snap = self._snapshot_with({**self._CREDS, "LEMON_SQUEEZY_MODE": "test"})
        self.assertEqual(snap["mode"], "test")
        self.assertTrue(snap["enabled"])

    def test_incomplete_credentials_block_even_with_valid_mode(self):
        snap = self._snapshot_with({"LEMON_SQUEEZY_MODE": "live",
                                    "ENVIRONMENT": "production"})
        self.assertEqual(snap["mode"], "live")
        self.assertFalse(snap["enabled"])             # no api key / store id
        self.assertIn("API key or", snap["config_error"])

    def test_missing_variant_reported_for_plan(self):
        # Mode + creds are fine, but no variant is configured for the plan.
        snap = self._snapshot_with({**self._CREDS, "LEMON_SQUEEZY_MODE": "live",
                                    "ENVIRONMENT": "production"})
        self.assertEqual(snap["config_error"], "")            # base config OK
        self.assertTrue(snap["config_error_pro"])             # plan not purchasable


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

    def test_get_billing_reports_checkout_enabled_for_member(self):
        # LS is configured and the caller is a member, so a real checkout is on.
        body = self.client.get("/api/billing").json()
        self.assertTrue(body["checkout_enabled"])
        self.assertIn("catalog", body)

    def test_get_billing_disables_checkout_for_demo_visitor(self):
        # A demo visitor can never transact: the upgrade UI must route them to
        # /pricing instead of a checkout that would 401.
        self.api.app.dependency_overrides[self.api.require_member_or_demo] = \
            lambda: "demo_" + "a" * 32
        body = self.client.get("/api/billing").json()
        self.assertFalse(body["checkout_enabled"])

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

    def test_active_subscriber_cannot_start_a_second_checkout(self):
        # The core duplicate-subscription defence: an already-subscribed user is
        # refused a new checkout (409) and pointed at the portal. LS would otherwise
        # create a SECOND subscription and bill both.
        bstore.upsert_subscription("member1", "pro", "active",
                                   provider_subscription_id="sub_live")
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout") as m:
            resp = self.client.post("/api/billing/checkout", json={"plan": "max"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("Manage Billing", resp.json()["error"])
        m.assert_not_called()   # no checkout was ever created with the provider

    def test_checkout_allowed_again_after_subscription_ends(self):
        # A cancelled/expired subscription will not re-bill, so a fresh checkout is
        # allowed (re-subscribe) — the guard only blocks LIVE subscriptions.
        bstore.upsert_subscription("member1", "pro", "expired",
                                   provider_subscription_id="sub_dead")
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout",
                               return_value={"url": "https://saqua.lemonsqueezy.com/y",
                                             "id": "chk_y"}):
            resp = self.client.post("/api/billing/checkout", json={"plan": "pro"})
        self.assertEqual(resp.status_code, 200)

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
        # Usage is durable and scoped to the billing PERIOD, independent of which
        # plan the period is on; changing plan within the same period must not reset
        # it. Start on pro (opens the paid period), consume 5, then upgrade pro->max
        # (same subscription id => same period) and confirm the 5 are preserved.
        from billing import usage
        webhook.handle_event(_sub_event("member1", "pro", "active", sub_id="up1"))
        for k in ("a.com", "b.com", "c.com", "d.com", "e.com"):
            usage.record_prospect_use("member1", k, limit=50)
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
        # The brief's required GET /api/billing fields are all present.
        for key in ("prospects_used", "prospect_limit", "period_start",
                    "period_end", "remaining"):
            self.assertIn(key, b2)
        self.assertEqual(b2["remaining"], 95)       # 100 - 5

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


# ── New-customer on-ramp: pay first, access follows (never the reverse) ─────
class NewCustomerCheckoutAccessTests(unittest.TestCase):
    """A brand-new account created through the paid funnel must be able to PAY even
    though the soft-launch gate hasn't approved it (buying is the on-ramp), must get
    NO product access while unpaid, and must be granted access the moment its
    subscription goes active."""

    def setUp(self):
        _reset_tables()
        import server.api as api
        import access
        self.api = api
        self.access = access
        api._STORE_BASE = tempfile.mkdtemp()
        api._BUCKETS.clear()
        api.app.dependency_overrides.clear()
        # A verified-but-UNAPPROVED identity. Deliberately do NOT override
        # require_approved_user, so the real soft-launch gate runs against "newbie".
        api.app.dependency_overrides[api.require_user] = lambda: "newbie"
        # Force the access gate ON in this process, and start from a clean slate.
        self._prev_gating = os.environ.get("ACCESS_GATING")
        os.environ["ACCESS_GATING"] = "1"
        access.store.reset_ensured()
        self._clear_access()
        settings.LEMONSQUEEZY_API_KEY = "ls_test_dummy"
        settings.LEMONSQUEEZY_STORE_ID = "1"
        settings.LEMONSQUEEZY_WEBHOOK_SECRET = _SECRET
        settings.LEMONSQUEEZY_VARIANT_IDS["pro_monthly"] = "v_pro_m"
        settings.LEMONSQUEEZY_VARIANT_IDS["max_monthly"] = "v_max_m"
        self.client = TestClient(api.app)

    def tearDown(self):
        self.api.app.dependency_overrides.clear()
        if self._prev_gating is None:
            os.environ.pop("ACCESS_GATING", None)
        else:
            os.environ["ACCESS_GATING"] = self._prev_gating
        self._clear_access()

    @staticmethod
    def _clear_access():
        try:
            Database().execute("DELETE FROM pending_users")
        except Exception:  # noqa: BLE001
            pass

    def test_new_unapproved_member_can_start_checkout(self):
        # Sanity: the gate really would deny this user product access...
        allowed, status = self.access.check_access("newbie")
        self.assertFalse(allowed)
        self.assertEqual(status, "pending")
        # ...yet checkout (the on-ramp) must still succeed for them.
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout",
                               return_value={"url": "https://saqua.lemonsqueezy.com/n",
                                             "id": "chk_n"}) as m:
            resp = self.client.post("/api/billing/checkout", json={"plan": "pro"})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["user_id"], "newbie")
        self.assertEqual(kwargs["plan"], "pro")   # Starter → pro

    def test_growth_alias_from_new_member_maps_to_max(self):
        import server.billing_api as bapi
        with mock.patch.object(bapi.ls_client, "create_checkout",
                               return_value={"url": "u", "id": "i"}) as m:
            resp = self.client.post("/api/billing/checkout", json={"plan": "growth"})
        self.assertEqual(resp.status_code, 200)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["plan"], "max")    # Growth → max
        self.assertEqual(kwargs["variant_id"], "v_max_m")

    def test_unpaid_new_account_has_no_paid_access(self):
        # No subscription ⇒ Free tier, not paid, and not approved for the app.
        ent = billing.entitlements("newbie", 0)
        self.assertEqual(ent["plan"], "free")
        self.assertFalse(ent.get("is_paid"))
        self.assertEqual(ent["prospect_limit"], settings.FREE_PROSPECT_LIMIT)
        self.assertFalse(self.access.is_approved("newbie"))

    def test_active_subscription_grants_access_and_paid_plan(self):
        # The webhook that activates a subscription auto-approves the buyer, so a
        # paying customer is never stranded behind the soft-launch gate.
        webhook.handle_event(_sub_event("newbie", "pro", "active", sub_id="ns1"))
        self.assertTrue(self.access.is_approved("newbie"))
        ent = billing.entitlements("newbie", 0)
        self.assertEqual(ent["plan"], "pro")
        self.assertTrue(ent.get("is_paid"))


# ── Durable, billing-period-scoped prospect usage (the quota system) ───────
class ProspectUsageDurableTests(unittest.TestCase):
    """The production prospect-quota store: durable (Postgres/SQLite, not the old
    ephemeral _usage.json), billing-period scoped, atomic, and deduped. Covers every
    behavior the launch brief requires."""

    def setUp(self):
        _reset_tables()
        from billing import usage
        self.usage = usage

    def _activate_pro(self, user, *, renews_at=None, created_at=None, sub_id="s1"):
        """Give ``user`` an active pro subscription via the webhook (so the period
        timestamps come through the real LS path)."""
        ev = _sub_event(user, "pro", "active", sub_id=sub_id,
                        renews_at=renews_at)
        if created_at is not None:
            ev["data"]["attributes"]["created_at"] = created_at
        webhook.handle_event(ev)

    def test_starter_blocks_at_50(self):
        self._activate_pro("starter_u")
        self.assertEqual(billing.limit_for_user("starter_u"), 50)
        for i in range(50):
            r = self.usage.record_prospect_use("starter_u", f"c{i}.com", limit=50)
            self.assertTrue(r["allowed"])
        self.assertEqual(self.usage.prospects_used("starter_u"), 50)
        # The 51st distinct prospect is blocked and NOT recorded.
        r = self.usage.record_prospect_use("starter_u", "c50.com", limit=50)
        self.assertFalse(r["allowed"])
        self.assertEqual(self.usage.prospects_used("starter_u"), 50)

    def test_growth_blocks_at_100(self):
        self._activate_pro("growth_u")   # start pro...
        webhook.handle_event(_sub_event("growth_u", "max", "active", sub_id="s1",
                                        etype="subscription_updated"))  # ...upgrade to max
        self.assertEqual(billing.limit_for_user("growth_u"), 100)
        for i in range(100):
            self.assertTrue(
                self.usage.record_prospect_use("growth_u", f"c{i}.com", limit=100)["allowed"])
        self.assertEqual(self.usage.prospects_used("growth_u"), 100)
        self.assertFalse(
            self.usage.record_prospect_use("growth_u", "c100.com", limit=100)["allowed"])

    def test_duplicate_company_does_not_consume_twice(self):
        self._activate_pro("dup_u")
        r1 = self.usage.record_prospect_use("dup_u", "acme.com", limit=50)
        self.assertTrue(r1["allowed"])
        self.assertFalse(r1["duplicate"])
        self.assertEqual(r1["used"], 1)
        # Re-research the SAME company: allowed, flagged duplicate, no new slot.
        r2 = self.usage.record_prospect_use("dup_u", "acme.com", limit=50)
        self.assertTrue(r2["allowed"])
        self.assertTrue(r2["duplicate"])
        self.assertEqual(self.usage.prospects_used("dup_u"), 1)
        # The normalized key means www / trailing junk map to the same prospect.
        self.assertEqual(self.usage.prospect_key(url="https://www.acme.com/pricing"),
                         self.usage.prospect_key(url="http://acme.com"))

    def test_durable_across_app_restart(self):
        # "Restart" = a brand-new Database handle (new connection), same file.
        self._activate_pro("persist_u")
        self.usage.record_prospect_use("persist_u", "x.com", limit=50)
        self.usage.record_prospect_use("persist_u", "y.com", limit=50)
        # A fresh Database handle == a new process/connection over the same file.
        from automation.db import Database as FreshDB
        self.assertEqual(self.usage.prospects_used("persist_u", db=FreshDB()), 2)

    def test_monthly_period_rollover_resets_to_zero(self):
        # Period 1: end at T1. Consume the whole allowance.
        now = time.time()
        t1 = _iso(datetime.fromtimestamp(now + 10 * 86400, tz=timezone.utc))
        self._activate_pro("roll_u", renews_at=t1,
                           created_at=_iso(datetime.fromtimestamp(now - 20 * 86400,
                                                                  tz=timezone.utc)))
        for i in range(50):
            self.usage.record_prospect_use("roll_u", f"c{i}.com", limit=50)
        self.assertEqual(self.usage.prospects_used("roll_u"), 50)
        anchor1 = self.usage.usage_period("roll_u")["anchor"]
        # Renewal advances the period end -> a NEW billing period (updated event).
        t2 = _iso(datetime.fromtimestamp(now + 40 * 86400, tz=timezone.utc))
        webhook.handle_event(_sub_event("roll_u", "pro", "active", sub_id="s1",
                                        renews_at=t2, etype="subscription_updated"))
        anchor2 = self.usage.usage_period("roll_u")["anchor"]
        self.assertNotEqual(anchor1, anchor2)             # the cycle rolled
        self.assertEqual(self.usage.prospects_used("roll_u"), 0)   # fresh allowance
        # The new period start == the previous period end (carry-forward), and the
        # historical rows are NOT deleted (audit trail preserved).
        rows = Database().query(
            "SELECT COUNT(*) AS n FROM prospect_usage WHERE user_id=?", ("roll_u",))
        self.assertEqual(int(rows[0]["n"]), 50)           # history intact

    def test_concurrent_requests_cannot_exceed_quota(self):
        import threading
        self._activate_pro("race_u")
        # 30 threads each try to claim a DISTINCT new prospect, cap is 10.
        results = []
        lock = threading.Lock()

        def claim(i):
            r = self.usage.record_prospect_use("race_u", f"r{i}.com", limit=10)
            with lock:
                results.append(r["allowed"])

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly the cap is granted; the store never exceeds it.
        self.assertEqual(sum(1 for a in results if a), 10)
        self.assertEqual(self.usage.prospects_used("race_u"), 10)

    def test_same_clerk_account_across_sessions_keeps_usage(self):
        # Usage is keyed on the Clerk user id, not a cookie/session/browser — a new
        # session (any request) reads the same durable count.
        self._activate_pro("clerk_1")
        self.usage.record_prospect_use("clerk_1", "a.com", limit=50)
        self.usage.record_prospect_use("clerk_1", "b.com", limit=50)
        # Different Database handle == different "session"; same user id.
        from automation.db import Database as S2
        self.assertEqual(self.usage.prospects_used("clerk_1", db=S2()), 2)
        # A different user id is fully isolated (no cross-account leakage).
        self.assertEqual(self.usage.prospects_used("clerk_2"), 0)

    def test_expired_subscription_gets_no_paid_quota(self):
        # Active pro: 50 cap, usage recorded in the paid period.
        self._activate_pro("exp_u")
        for i in range(3):
            self.usage.record_prospect_use("exp_u", f"c{i}.com", limit=50)
        self.assertEqual(billing.limit_for_user("exp_u"), 50)
        # Subscription expires -> the user drops to Free, gets the FREE cap only,
        # and the expired period's usage does not count against the free window.
        webhook.handle_event(_sub_event("exp_u", "pro", "expired", sub_id="s1",
                                        etype="subscription_expired"))
        self.assertEqual(billing.limit_for_user("exp_u"), settings.FREE_PROSPECT_LIMIT)
        self.assertFalse(self.usage.usage_period("exp_u")["paid"])
        # No paid allowance: the free window starts at zero used.
        self.assertEqual(self.usage.prospects_used("exp_u"), 0)

    def test_cancelled_in_grace_period_keeps_access_and_usage(self):
        # Cancelled but still inside the paid period (ends_at in the future) keeps the
        # plan AND the usage counted in that period.
        future = _iso(datetime.fromtimestamp(time.time() + 20 * 86400, tz=timezone.utc))
        self._activate_pro("grace_u", renews_at=future)
        self.usage.record_prospect_use("grace_u", "a.com", limit=50)
        webhook.handle_event(_sub_event("grace_u", "pro", "cancelled", sub_id="s1",
                                        ends_at=future, etype="subscription_cancelled"))
        self.assertEqual(billing.limit_for_user("grace_u"), 50)      # access preserved
        self.assertTrue(self.usage.usage_period("grace_u")["paid"])
        self.assertEqual(self.usage.prospects_used("grace_u"), 1)    # usage preserved

    def test_legacy_usage_json_import_is_idempotent(self):
        # Migration: surviving _usage.json keys are imported into the current period
        # (counts imported, not reset), and re-importing is a no-op.
        keys = ["old1.com", "old2.com", "old3.com"]
        self.assertEqual(self.usage.import_legacy_keys("legacy_u", keys), 3)
        self.assertEqual(self.usage.prospects_used("legacy_u"), 3)
        # Re-running imports nothing new (UNIQUE dedupe).
        self.assertEqual(self.usage.import_legacy_keys("legacy_u", keys), 0)
        self.assertEqual(self.usage.prospects_used("legacy_u"), 3)

    def test_free_user_has_lifetime_window_at_anchor_zero(self):
        # A user with no subscription is on the Free lifetime trial (anchor 0).
        period = self.usage.usage_period("free_u")
        self.assertFalse(period["paid"])
        self.assertEqual(period["anchor"], 0.0)
        for i in range(settings.FREE_PROSPECT_LIMIT):
            self.assertTrue(
                self.usage.record_prospect_use("free_u", f"c{i}.com",
                                               limit=settings.FREE_PROSPECT_LIMIT)["allowed"])
        # Beyond the free cap is blocked (when the cap is enabled).
        if settings.FREE_PROSPECT_LIMIT > 0:
            self.assertFalse(
                self.usage.record_prospect_use(
                    "free_u", "over.com", limit=settings.FREE_PROSPECT_LIMIT)["allowed"])


# ── Fail-CLOSED enforcement when the durable store is unavailable ──────────
class ProspectQuotaFailClosedTests(unittest.TestCase):
    """A database outage must NEVER open the quota: research is blocked, no unmetered
    slot is granted, and a duplicate cannot slip through either. Only off production
    is there an explicit dev fallback."""

    def setUp(self):
        _reset_tables()
        from billing import usage
        self.usage = usage

    def test_record_fails_closed_in_production_on_db_error(self):
        # Simulate the durable store being unreachable (period resolution raises).
        from billing import store as bstore
        with mock.patch.object(bstore, "active_subscription",
                               side_effect=RuntimeError("db down")), \
             mock.patch.object(settings, "is_production", return_value=True):
            res = self.usage.record_prospect_use("out_u", "acme.com", limit=50)
        self.assertFalse(res["allowed"])       # blocked
        self.assertTrue(res["error"])          # flagged as an infra failure
        self.assertFalse(res.get("fallback"))  # NOT a dev fallback
        # And nothing was recorded — no unmetered quota was granted.
        self.assertEqual(self.usage.prospects_used("out_u"), 0)

    def test_duplicate_cannot_bypass_during_outage(self):
        # A real prior use exists...
        self.usage.record_prospect_use("dupout_u", "acme.com", limit=50)
        self.assertEqual(self.usage.prospects_used("dupout_u"), 1)
        # ...but during an outage even a duplicate is not confirmed => blocked in prod.
        from billing import store as bstore
        with mock.patch.object(bstore, "active_subscription",
                               side_effect=RuntimeError("db down")), \
             mock.patch.object(settings, "is_production", return_value=True):
            res = self.usage.record_prospect_use("dupout_u", "acme.com", limit=50)
        self.assertFalse(res["allowed"])
        self.assertTrue(res["error"])

    def test_dev_fallback_only_off_production(self):
        from billing import store as bstore
        with mock.patch.object(bstore, "active_subscription",
                               side_effect=RuntimeError("db down")), \
             mock.patch.object(settings, "is_production", return_value=False):
            res = self.usage.record_prospect_use("dev_u", "acme.com", limit=50)
        self.assertTrue(res["allowed"])        # dev is not blocked by a missing DB
        self.assertTrue(res.get("fallback"))   # ...but it is explicitly a fallback

    def test_tool_blocks_research_before_provider_call_on_outage(self):
        # The end-to-end guarantee: when the durable store is unavailable in
        # production, chat.tools._tool_research must NOT call the paid research
        # provider and must return a temporary-unavailable result.
        from chat import tools
        from chat.models import Conversation
        conv = Conversation()
        conv._user_id = "toolout_u"
        conv.workspace["prospect_limit"] = 50
        unavailable = {"allowed": False, "duplicate": False, "used": None,
                       "limit": 50, "anchor": None, "error": True}
        with mock.patch("chat.tools.research_company") as m_research, \
             mock.patch("billing.usage.record_prospect_use", return_value=unavailable):
            res = tools._tool_research({"query": "acme.com"}, conv)
        m_research.assert_not_called()                       # no paid provider call
        self.assertIn("temporarily unavailable", res.summary.lower())

    def test_claim_gate_meters_and_signals_states_when_healthy(self):
        # Control: with a healthy store the gate claims a slot (metered durably),
        # calls a duplicate free, and blocks past the cap.
        from chat import tools
        from chat.models import Conversation
        conv = Conversation()
        conv._user_id = "gateok_u"
        conv.workspace["prospect_limit"] = 1                 # tiny cap for the test
        self.assertEqual(tools._claim_prospect(conv, "acme.com"), "ok")
        self.assertEqual(self.usage.prospects_used("gateok_u"), 1)     # metered
        self.assertEqual(tools._claim_prospect(conv, "acme.com"), "duplicate")  # free
        self.assertEqual(self.usage.prospects_used("gateok_u"), 1)     # no double count
        self.assertEqual(tools._claim_prospect(conv, "other.com"), "blocked")   # over cap


# ── Legacy migration never consumes a paid period ──────────────────────────
class LegacyImportPeriodTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()
        from billing import usage
        self.usage = usage

    def test_legacy_does_not_consume_active_paid_period(self):
        # User is on an active paid plan (paid period != anchor 0)...
        webhook.handle_event(_sub_event("paid_u", "pro", "active", sub_id="s1"))
        self.assertTrue(self.usage.usage_period("paid_u")["paid"])
        # ...importing legacy keys lands them at the free/lifetime anchor 0, NOT the
        # paid period, so the paid period still begins at zero used.
        self.usage.import_legacy_keys("paid_u", ["old1.com", "old2.com", "old3.com"])
        self.assertEqual(self.usage.prospects_used("paid_u"), 0)           # paid period
        self.assertEqual(self.usage.prospects_used("paid_u",
                                                   anchor=self.usage.LEGACY_ANCHOR), 3)

    def test_fresh_paid_period_begins_at_zero_after_free_usage(self):
        # Free user consumes their trial at anchor 0...
        for i in range(3):
            self.usage.record_prospect_use("upgr_u", f"c{i}.com",
                                           limit=settings.FREE_PROSPECT_LIMIT)
        self.assertEqual(self.usage.prospects_used("upgr_u"), 3)
        # ...then upgrades: the new paid period starts at zero, free history intact.
        webhook.handle_event(_sub_event("upgr_u", "pro", "active", sub_id="s1"))
        self.assertEqual(self.usage.prospects_used("upgr_u"), 0)           # paid period
        self.assertEqual(self.usage.prospects_used("upgr_u", anchor=0.0), 3)  # free history


# ── Migration idempotency + startup schema guard ───────────────────────────
class MigrationIdempotencyTests(unittest.TestCase):
    def test_migration_reruns_safely(self):
        from automation import migrate
        db = Database()
        # Already applied in setUpModule; a re-run applies nothing and does not error.
        self.assertEqual(migrate.run(db, verbose=False), [])
        # ensure_schema_columns is a no-op when the column already exists.
        self.assertEqual(migrate.ensure_schema_columns(db), [])
        # The quota-critical schema verifies healthy.
        report = migrate.verify_schema(db)
        self.assertTrue(report["ok"])
        self.assertEqual(report["missing_tables"], [])
        self.assertEqual(report["missing_columns"], [])

    def test_verify_schema_reports_missing_table(self):
        from automation import migrate
        # A DB handle whose table check always says "absent" reports unhealthy.
        with mock.patch.object(migrate, "_table_exists", return_value=False):
            report = migrate.verify_schema(Database())
        self.assertFalse(report["ok"])
        self.assertIn("prospect_usage", report["missing_tables"])


if __name__ == "__main__":
    unittest.main()
