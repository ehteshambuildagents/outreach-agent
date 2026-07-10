"""Telemetry schema — the three durable tables telemetry needs, plus reuse notes.

Only three NEW tables are created (nothing existing fits their shape):
  * ``ai_requests``      — one row per LLM/API call (tokens, cost, latency, …).
  * ``agent_runs``       — one row per agent/tool execution (duration, success, …).
  * ``telemetry_events`` — an append-only lifecycle event stream (email / automation
    / campaign events like queued / blocked / duplicate-prevented / stopped).

Everything else is REUSED rather than duplicated:
  * cost/token DAILY ROLLUPS -> the existing ``metrics_daily`` table;
  * workflow events -> the existing ``automation.events`` table + WorkflowStore;
  * worker/queue health -> ``automation.health`` / ``automation.metrics``.

Provisioned idempotently at sink startup via :func:`ensure` (the same self-provision
pattern the automation stores use for their core schema), so telemetry works with
or without the migration runner. Portable DDL: valid on Postgres and SQLite.
"""

from automation.db import Database

DDL = """
CREATE TABLE IF NOT EXISTS ai_requests (
    id                TEXT PRIMARY KEY,          -- request_id (idempotent)
    ts                DOUBLE PRECISION NOT NULL,
    user_id           TEXT,
    workspace_id      TEXT,
    workflow_id       TEXT,
    campaign_id       TEXT,
    agent             TEXT,
    provider          TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,                   -- NULL = unavailable (never faked)
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    estimated_cost    DOUBLE PRECISION,
    cost_basis        TEXT,                      -- provider_tokens | unavailable
    latency_ms        DOUBLE PRECISION,
    retries           INTEGER,
    success           INTEGER NOT NULL,          -- 1 ok / 0 failed
    failure_reason    TEXT,
    cache_hit         INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ai_user_ts   ON ai_requests(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_ai_agent     ON ai_requests(agent);
CREATE INDEX IF NOT EXISTS ix_ai_model     ON ai_requests(model);
CREATE INDEX IF NOT EXISTS ix_ai_campaign  ON ai_requests(campaign_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT PRIMARY KEY,               -- run_id (idempotent)
    ts           DOUBLE PRECISION NOT NULL,
    agent        TEXT NOT NULL,
    user_id      TEXT,
    workflow_id  TEXT,
    campaign_id  TEXT,
    started_at   DOUBLE PRECISION,
    finished_at  DOUBLE PRECISION,
    duration_ms  DOUBLE PRECISION,
    success      INTEGER NOT NULL,
    retries      INTEGER,
    skipped      INTEGER,
    warnings     INTEGER,
    decision     TEXT,                           -- deterministic decision, if any
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_run_agent_ts ON agent_runs(agent, ts);
CREATE INDEX IF NOT EXISTS ix_run_user     ON agent_runs(user_id);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id           TEXT PRIMARY KEY,               -- event_id (idempotent)
    ts           DOUBLE PRECISION NOT NULL,
    category     TEXT NOT NULL,                  -- email | automation | campaign | agent | ai
    event        TEXT NOT NULL,                  -- sent | blocked | queued | stopped | ...
    user_id      TEXT,
    workspace_id TEXT,
    workflow_id  TEXT,
    campaign_id  TEXT,
    entity_id    TEXT,
    value        DOUBLE PRECISION,               -- optional numeric (wait/latency/count)
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_ev_cat_ts  ON telemetry_events(category, event, ts);
CREATE INDEX IF NOT EXISTS ix_ev_wf      ON telemetry_events(workflow_id);
CREATE INDEX IF NOT EXISTS ix_ev_user_ts ON telemetry_events(user_id, ts);
"""

_ensured = False


def ensure(db: Database = None, *, force: bool = False) -> None:
    """Create the telemetry tables if absent. Idempotent + cheap; never raises."""
    global _ensured
    if _ensured and not force:
        return
    try:
        (db or Database()).executescript(DDL)
        _ensured = True
    except Exception:  # noqa: BLE001 - telemetry provisioning must never break prod
        pass


def reset_ensured() -> None:      # test hook
    global _ensured
    _ensured = False
