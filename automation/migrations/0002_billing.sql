-- 0002_billing.sql — billing-ready tables (portable; Postgres + SQLite).
--
-- Not wired to any payment provider yet — this is the durable shape billing will
-- use (Stripe-style), so introducing billing later is a data-only change, not a
-- schema migration during launch. Idempotent.

CREATE TABLE IF NOT EXISTS billing_customers (
    id                  TEXT PRIMARY KEY,   -- internal id
    user_id             TEXT NOT NULL,
    provider            TEXT,               -- e.g. "stripe"
    provider_customer_id TEXT,
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL,
    UNIQUE(user_id, provider)
);
CREATE INDEX IF NOT EXISTS ix_bill_cust_user ON billing_customers(user_id);

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    plan                     TEXT NOT NULL,          -- starter / pro / enterprise
    status                   TEXT NOT NULL,          -- trialing / active / past_due / canceled
    provider                 TEXT,
    provider_subscription_id TEXT,
    current_period_end       DOUBLE PRECISION,
    created_at               DOUBLE PRECISION NOT NULL,
    updated_at               DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bill_sub_user ON billing_subscriptions(user_id, status);

CREATE TABLE IF NOT EXISTS billing_invoices (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    subscription_id     TEXT,
    amount_cents        INTEGER NOT NULL,
    currency            TEXT NOT NULL,
    status              TEXT NOT NULL,      -- draft / open / paid / void
    provider_invoice_id TEXT,
    created_at          DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bill_inv_user ON billing_invoices(user_id, status);
