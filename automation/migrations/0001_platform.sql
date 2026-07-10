-- 0001_platform.sql — forward-looking platform schema for Saqua.
--
-- Portable DDL: valid on BOTH Postgres (production) and SQLite (local fallback).
-- Ids are application-generated TEXT (Clerk user ids / uuid-style), timestamps are
-- epoch seconds (DOUBLE PRECISION), and JSON payloads are TEXT. The operational
-- automation tables (workflows / events / processed / oauth_accounts) are created
-- by automation.db.ensure_core_schema(); this migration adds the tables the wider
-- product grows into. Every statement is idempotent (IF NOT EXISTS).

-- People & tenancy ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,           -- Clerk user id (auth stays with Clerk)
    email       TEXT,
    name        TEXT,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    name          TEXT NOT NULL,
    created_at    DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ws_owner ON workspaces(owner_user_id);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL,
    created_at   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);

-- Conversations (chat threads; the live chat store is JSON files today, this is
-- the durable home it migrates into). --------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    workspace_id TEXT,
    title        TEXT,
    created_at   DOUBLE PRECISION NOT NULL,
    updated_at   DOUBLE PRECISION NOT NULL,
    data         TEXT
);
CREATE INDEX IF NOT EXISTS ix_conv_user ON conversations(user_id);

-- Normalized automation steps (the engine keeps steps embedded in the workflow
-- JSON; this projection is for reporting / future queries, one row per step). -
CREATE TABLE IF NOT EXISTS automation_steps (
    id                  TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    step_index          INTEGER NOT NULL,
    status              TEXT NOT NULL,
    subject             TEXT,
    to_email            TEXT,
    scheduled_at        DOUBLE PRECISION,
    sent_at             DOUBLE PRECISION,
    provider_message_id TEXT,
    provider_thread_id  TEXT,
    retry_count         INTEGER NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_step_wf ON automation_steps(workflow_id, step_index);

-- Writer artifacts (the emails the writer produced), stable-id addressable. --
CREATE TABLE IF NOT EXISTS email_artifacts (
    id              TEXT PRIMARY KEY,       -- stable slot id (email / version-b / email-3)
    user_id         TEXT NOT NULL,
    conversation_id TEXT,
    company         TEXT,
    subject         TEXT,
    body            TEXT,
    to_email        TEXT,
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_art_user ON email_artifacts(user_id);

-- Sent messages + inbound replies. -----------------------------------------
CREATE TABLE IF NOT EXISTS email_messages (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    workflow_id         TEXT,
    provider            TEXT NOT NULL,
    provider_message_id TEXT,
    provider_thread_id  TEXT,
    to_email            TEXT,
    subject             TEXT,
    sent_at             DOUBLE PRECISION,
    created_at          DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_user ON email_messages(user_id);
CREATE INDEX IF NOT EXISTS ix_msg_thread ON email_messages(provider_thread_id);

CREATE TABLE IF NOT EXISTS email_replies (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    workflow_id         TEXT,
    provider            TEXT NOT NULL,
    provider_message_id TEXT,
    provider_thread_id  TEXT,
    from_email          TEXT,
    received_at         DOUBLE PRECISION,
    created_at          DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reply_wf ON email_replies(workflow_id);

-- Observability: rolled-up metrics, audit trail, usage events. --------------
CREATE TABLE IF NOT EXISTS metrics_daily (
    id         TEXT PRIMARY KEY,            -- e.g. "<user>:<day>:<metric>"
    user_id    TEXT,
    day        TEXT NOT NULL,               -- YYYY-MM-DD (UTC)
    metric     TEXT NOT NULL,
    value      DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_metrics_day ON metrics_daily(day, metric);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_logs(user_id, created_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    kind       TEXT NOT NULL,               -- research / email / send / reply ...
    quantity   DOUBLE PRECISION NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_user ON usage_events(user_id, created_at);
