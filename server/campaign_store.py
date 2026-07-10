"""Durable campaign preview persistence.

Stores launch-ready campaign previews and their events in the same database
abstraction as automation/discovery: Postgres/Supabase when ``DATABASE_URL`` is
set, SQLite locally. The store owns persistence only; orchestration lives in
``server.campaign_api``.
"""

import json
import time
import uuid

from automation import db as _db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    name            TEXT,
    status          TEXT NOT NULL,
    request_json    TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    workflow_ids    TEXT NOT NULL DEFAULT '[]',
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL,
    UNIQUE(owner, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_campaigns_owner ON campaigns(owner, updated_at);

CREATE TABLE IF NOT EXISTS campaign_events (
    id          %(serial_pk)s,
    campaign_id TEXT NOT NULL,
    owner       TEXT NOT NULL,
    ts          DOUBLE PRECISION NOT NULL,
    type        TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_campaign_events ON campaign_events(campaign_id, id);
"""


def new_campaign_id() -> str:
    return "cmp_" + uuid.uuid4().hex[:16]


class CampaignStore:
    def __init__(self, db=None):
        self.db = db or _db.default()
        self._ensure()

    def _ensure(self) -> None:
        serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if self.db.backend == "sqlite" else "BIGSERIAL PRIMARY KEY"
        self.db.executescript(_SCHEMA % {"serial_pk": serial})

    def create(self, *, owner: str, idempotency_key: str, name: str,
               request: dict, result: dict, status: str) -> dict:
        now = time.time()
        row = {
            "id": new_campaign_id(),
            "owner": owner,
            "idempotency_key": idempotency_key,
            "name": name,
            "status": status,
            "request": request,
            "result": result,
            "workflow_ids": [],
            "created_at": now,
            "updated_at": now,
        }
        self.db.execute(
            "INSERT INTO campaigns (id, owner, idempotency_key, name, status, "
            "request_json, result_json, workflow_ids, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["id"], owner, idempotency_key, name, status,
             json.dumps(request, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False), "[]", now, now),
        )
        return row

    def get(self, owner: str, campaign_id: str) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM campaigns WHERE owner=? AND id=?",
            (owner, campaign_id),
        )
        return self._row(row) if row else None

    def get_by_idempotency_key(self, owner: str, key: str) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM campaigns WHERE owner=? AND idempotency_key=?",
            (owner, key),
        )
        return self._row(row) if row else None

    def list_for_owner(self, owner: str, limit: int = 100, offset: int = 0) -> list:
        rows = self.db.query(
            "SELECT * FROM campaigns WHERE owner=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (owner, limit, offset),
        )
        return [self._row(row) for row in rows]

    def update(self, owner: str, campaign_id: str, *, status: str | None = None,
               result: dict | None = None, workflow_ids: list[str] | None = None) -> dict | None:
        current = self.get(owner, campaign_id)
        if current is None:
            return None
        status = status or current["status"]
        result = result if result is not None else current["result"]
        workflow_ids = workflow_ids if workflow_ids is not None else current["workflow_ids"]
        now = time.time()
        self.db.execute(
            "UPDATE campaigns SET status=?, result_json=?, workflow_ids=?, updated_at=? "
            "WHERE owner=? AND id=?",
            (status, json.dumps(result, ensure_ascii=False),
             json.dumps(workflow_ids, ensure_ascii=False), now, owner, campaign_id),
        )
        return self.get(owner, campaign_id)

    def add_event(self, owner: str, campaign_id: str, type_: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO campaign_events (campaign_id, owner, ts, type, detail) "
            "VALUES (?,?,?,?,?)",
            (campaign_id, owner, time.time(), type_, detail),
        )

    def events_for(self, owner: str, campaign_id: str, limit: int = 300) -> list:
        rows = self.db.query(
            "SELECT ts,type,detail FROM campaign_events WHERE owner=? AND campaign_id=? "
            "ORDER BY id ASC LIMIT ?",
            (owner, campaign_id, limit),
        )
        return [{"ts": r["ts"], "type": r["type"], "detail": r["detail"]} for r in rows]

    def _row(self, row) -> dict:
        data = dict(row)
        return {
            "id": data["id"],
            "owner": data["owner"],
            "idempotency_key": data["idempotency_key"],
            "name": data.get("name") or "",
            "status": data["status"],
            "request": json.loads(data.get("request_json") or "{}"),
            "result": json.loads(data.get("result_json") or "{}"),
            "workflow_ids": json.loads(data.get("workflow_ids") or "[]"),
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }
