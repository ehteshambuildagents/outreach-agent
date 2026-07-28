-- 0004_billing_events.sql — webhook idempotency ledger (portable).
--
-- Lemon Squeezy delivers every event at least once and WILL redeliver on any
-- non-2xx (or on a manual "Resend" from the dashboard). Recording each processed
-- event key here lets the webhook handler treat a redelivery as a no-op, so a
-- replayed subscription_created can never double-apply a plan change or
-- double-count an invoice. The key is the LS X-Signature (an HMAC of the raw body,
-- so an identical redelivery has an identical key). Idempotent DDL; Postgres+SQLite.

CREATE TABLE IF NOT EXISTS billing_events (
    event_id     TEXT PRIMARY KEY,   -- LS X-Signature (or "<event>:<resource_id>")
    type         TEXT NOT NULL,      -- e.g. "subscription_created"
    received_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bill_evt_type ON billing_events(type);
