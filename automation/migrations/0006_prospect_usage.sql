-- 0006_prospect_usage.sql — durable, billing-period-scoped prospect usage.
--
-- Replaces the old ephemeral conversations/<user>/_usage.json (local disk, wiped
-- on every Railway redeploy, and lifetime-not-period scoped) as the SOURCE OF
-- TRUTH for the researched-prospect quota. Portable DDL (Postgres + SQLite) and
-- fully idempotent: every statement here uses IF NOT EXISTS, so re-running is a
-- safe no-op even outside the migration ledger.
--
-- One row = one distinct prospect a user researched inside one billing period.
-- Design that makes the quota rules fall out of the schema:
--   * UNIQUE(user_id, period_anchor, prospect_key) makes re-researching the same
--     company a no-op INSERT — deduplication is enforced by the database, not by
--     application logic that could race. So a duplicate never consumes quota.
--   * COUNT(*) over (user_id, period_anchor) IS the "prospects used this period".
--   * period_anchor identifies the billing cycle (the subscription's current period
--     start, epoch seconds; 0 for the Free lifetime trial). A renewal advances the
--     anchor, so a new period automatically begins at zero used WITHOUT deleting any
--     historical rows — the old rows simply belong to a past anchor (audit trail).
-- The append-only shape is deliberate: history is never mutated, only added to.
--
-- The companion column billing_subscriptions.current_period_start is added
-- idempotently in code (automation.migrate.ensure_schema_columns), because SQLite
-- has no "ALTER TABLE ... ADD COLUMN IF NOT EXISTS" and a raw ALTER in a portable
-- .sql file cannot be made conditional on both engines. The code path checks the
-- catalog first, so it is a genuine add-if-missing on Postgres AND SQLite.
CREATE TABLE IF NOT EXISTS prospect_usage (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    period_anchor DOUBLE PRECISION NOT NULL,   -- epoch: billing-period start (0 = free/lifetime)
    prospect_key  TEXT NOT NULL,               -- normalized domain / company key
    created_at    DOUBLE PRECISION NOT NULL,
    UNIQUE(user_id, period_anchor, prospect_key)
);
CREATE INDEX IF NOT EXISTS ix_prospect_usage_period ON prospect_usage(user_id, period_anchor);
