"""Prospect persistence — Postgres/Supabase in prod, SQLite locally.

Talks only to ``automation.db`` so it inherits the same backend selection,
per-thread connections, transactions, and portable placeholders. Prospects are
unique per (owner, domain): saving an already-discovered company is a durable
no-op, which is how "don't return the same lead twice" survives restarts.
"""

import json
import logging

from automation import db as _db
from discovery.models import Prospect

log = logging.getLogger("discovery.store")

_SCHEMA = """
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
"""


class ProspectStore:
    def __init__(self, db=None):
        self.db = db or _db.default()
        self._ensure()

    def _ensure(self):
        # Idempotent; makes local/test runs work without invoking the migrator.
        self.db.executescript(_SCHEMA)

    def _row_to_prospect(self, r) -> Prospect:
        d = dict(r)
        d["estimated_company_size"] = d.pop("company_size", None) or "unknown"
        d["estimated_stage"] = d.pop("stage", None) or "unknown"
        try:
            d["basic_signals"] = json.loads(d.get("basic_signals") or "[]")
        except (TypeError, ValueError):
            d["basic_signals"] = []
        return Prospect.from_dict(d)

    def save_many(self, prospects) -> int:
        """Insert new prospects; existing (owner, domain) rows are left untouched.
        Returns how many were newly inserted."""
        inserted = 0
        for p in prospects:
            n = self.db.execute(
                "INSERT INTO prospects (id, owner, domain, company_name, website, "
                "industry, location, company_size, stage, confidence, why_it_matches, "
                "discovery_source, basic_signals, query, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (owner, domain) DO NOTHING",
                (p.id, p.owner, p.domain, p.company_name, p.website, p.industry,
                 p.location, p.estimated_company_size, p.estimated_stage,
                 p.confidence, p.why_it_matches, p.discovery_source,
                 json.dumps(p.basic_signals), p.query, p.status, p.created_at))
            inserted += int(n or 0)
        return inserted

    def list_for_owner(self, owner: str, limit: int = 100, offset: int = 0) -> list:
        rows = self.db.query(
            "SELECT * FROM prospects WHERE owner=? ORDER BY confidence DESC, "
            "created_at DESC LIMIT ? OFFSET ?", (owner, limit, offset))
        return [self._row_to_prospect(r) for r in rows]

    def seen_domains(self, owner: str) -> set:
        rows = self.db.query("SELECT domain FROM prospects WHERE owner=?", (owner,))
        return {dict(r)["domain"] for r in rows}

    def count_for_owner(self, owner: str) -> int:
        r = self.db.query_one("SELECT COUNT(*) AS n FROM prospects WHERE owner=?",
                              (owner,))
        return int(dict(r)["n"]) if r else 0

    def mark_researched(self, owner: str, domain: str) -> None:
        self.db.execute(
            "UPDATE prospects SET status='researched' WHERE owner=? AND domain=?",
            (owner, domain))

    def delete_for_owner(self, owner: str) -> None:   # test/cleanup helper
        self.db.execute("DELETE FROM prospects WHERE owner=?", (owner,))
