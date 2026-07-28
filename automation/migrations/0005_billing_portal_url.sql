-- 0005_billing_portal_url.sql — persist the Lemon Squeezy customer-portal link.
--
-- Unlike Stripe (where the app creates a Billing Portal *session* over the API on
-- demand), Lemon Squeezy mints a per-subscription customer-portal URL and delivers
-- it on every subscription webhook (attributes.urls.customer_portal). We store it
-- so POST /api/billing/portal can hand it straight back — no API round-trip, and
-- it survives even after a cancellation (the user may want to fix a card or
-- resubscribe). Nullable; only ever written when an event carries a link.
--
-- Runs exactly once (the migration ledger guards re-application), so a plain
-- ADD COLUMN is portable across Postgres + SQLite without an IF NOT EXISTS clause.

ALTER TABLE billing_subscriptions ADD COLUMN portal_url TEXT;
