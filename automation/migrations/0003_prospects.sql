-- Prospect Discovery Agent: discovered companies (leads), per user.
-- Portable DDL (runs on Postgres/Supabase and SQLite). A prospect is unique per
-- (owner, domain) so re-discovering the same company is a no-op.
CREATE TABLE IF NOT EXISTS prospects (
    id               TEXT PRIMARY KEY,
    owner            TEXT NOT NULL,
    domain           TEXT NOT NULL,
    company_name     TEXT,
    website          TEXT,
    industry         TEXT,
    location         TEXT,
    company_size     TEXT,
    stage            TEXT,
    confidence       DOUBLE PRECISION,
    why_it_matches   TEXT,
    discovery_source TEXT,
    basic_signals    TEXT,
    query            TEXT,
    status           TEXT NOT NULL DEFAULT 'new',
    created_at       DOUBLE PRECISION NOT NULL,
    UNIQUE(owner, domain)
);
CREATE INDEX IF NOT EXISTS ix_prospects_owner ON prospects(owner, created_at);
CREATE INDEX IF NOT EXISTS ix_prospects_status ON prospects(owner, status);
