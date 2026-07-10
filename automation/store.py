"""Durable workflow persistence — the recovery source of truth.

Backed by Postgres in production (``DATABASE_URL``) or SQLite locally, chosen
transparently by :mod:`automation.db`. Everything needed to resume a workflow
after a crash is committed here transactionally.

Guarantees (identical on both backends):
  * per-user isolation — every load/list/delete filters by ``user_id`` when one is
    supplied, so no user can reach another's workflow;
  * a durable ``processed`` ledger for crash-proof idempotency (a redelivered
    webhook or retried send is a no-op even if Redis was flushed);
  * transactional writes so a partially-applied change can never be observed.
"""

import json
import time

from automation import states
from automation.db import Database
from automation.models import Workflow


class WorkflowStore:
    def __init__(self, path: str = None, db: Database = None):
        # ``path`` keeps the old SQLite-file constructor working (tests pass a temp
        # path); it is ignored when DATABASE_URL selects Postgres.
        self.db = db or Database(sqlite_path=path)
        self.db.ensure_core_schema()

    # ── workflow CRUD (transactional) ──────────────────────────────────
    def save(self, wf: Workflow) -> Workflow:
        wf.touch()
        payload = json.dumps(wf.to_dict(), ensure_ascii=False)
        with self.db.tx() as tx:
            tx.execute(
                "INSERT INTO workflows (id,user_id,state,next_run_at,updated_at,data) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "next_run_at=excluded.next_run_at, updated_at=excluded.updated_at, "
                "data=excluded.data",
                (wf.id, wf.user_id, wf.state, wf.next_run_at, wf.updated_at, payload))
        return wf

    def load(self, workflow_id: str, user_id: str = None):
        if user_id is not None:
            row = self.db.query_one(
                "SELECT data FROM workflows WHERE id=? AND user_id=?",
                (workflow_id, user_id))
        else:
            row = self.db.query_one("SELECT data FROM workflows WHERE id=?",
                                    (workflow_id,))
        return Workflow.from_dict(json.loads(row["data"])) if row else None

    def list_for_user(self, user_id: str) -> list:
        rows = self.db.query(
            "SELECT data FROM workflows WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,))
        return [Workflow.from_dict(json.loads(r["data"])) for r in rows]

    def due_workflows(self, now: float = None, limit: int = 100) -> list:
        """Active workflows whose next action is due — the tick loop's work list."""
        now = time.time() if now is None else now
        placeholders = ",".join(["?"] * len(states.TERMINAL))
        rows = self.db.query(
            f"SELECT data FROM workflows WHERE state NOT IN ({placeholders}) "
            "AND next_run_at IS NOT NULL AND next_run_at <= ? "
            "ORDER BY next_run_at ASC LIMIT ?",
            (*states.TERMINAL, now, limit))
        return [Workflow.from_dict(json.loads(r["data"])) for r in rows]

    def workflows_in_state(self, state: str, limit: int = 10000) -> list:
        rows = self.db.query(
            "SELECT data FROM workflows WHERE state=? ORDER BY updated_at ASC LIMIT ?",
            (state, limit))
        return [Workflow.from_dict(json.loads(r["data"])) for r in rows]

    def delete(self, workflow_id: str, user_id: str = None) -> None:
        with self.db.tx() as tx:
            if user_id is not None:
                tx.execute("DELETE FROM workflows WHERE id=? AND user_id=?",
                           (workflow_id, user_id))
            else:
                tx.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
            tx.execute("DELETE FROM events WHERE workflow_id=?", (workflow_id,))

    # ── events (observability / audit) ─────────────────────────────────
    def add_event(self, workflow_id: str, type_: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO events (workflow_id,ts,type,detail) VALUES (?,?,?,?)",
            (workflow_id, time.time(), type_, detail))

    def events_for(self, workflow_id: str, limit: int = 200) -> list:
        rows = self.db.query(
            "SELECT ts,type,detail FROM events WHERE workflow_id=? "
            "ORDER BY id ASC LIMIT ?", (workflow_id, limit))
        return [{"ts": r["ts"], "type": r["type"], "detail": r["detail"]} for r in rows]

    # ── durable idempotency ledger ─────────────────────────────────────
    def mark_processed(self, key: str) -> bool:
        """Record an idempotency key. Returns True if NEW (first time), False if it
        was already processed (duplicate → caller should no-op). Uses an atomic
        INSERT … ON CONFLICT DO NOTHING so it is race-safe on both backends."""
        changed = self.db.execute(
            "INSERT INTO processed (key,ts) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
            (key, time.time()))
        return changed == 1

    def was_processed(self, key: str) -> bool:
        return self.db.query_one("SELECT 1 AS one FROM processed WHERE key=?",
                                 (key,)) is not None

    # ── counts for metrics ─────────────────────────────────────────────
    def count_by_state(self) -> dict:
        rows = self.db.query("SELECT state, COUNT(*) AS n FROM workflows GROUP BY state")
        return {r["state"]: r["n"] for r in rows}
