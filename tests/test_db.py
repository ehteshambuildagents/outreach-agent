"""Database abstraction + migration runner tests.

The SQLite path is exercised for real; the Postgres path is verified where it can
be without a live server (backend selection, ``?``->``%s`` adaptation, statement
splitting). Migrations are applied to a temp SQLite DB and the resulting schema
(tables, indexes), reads/writes, transactions and idempotency are asserted.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation import migrate  # noqa: E402
from automation.db import Database, _split_statements  # noqa: E402


def _db():
    return Database(sqlite_path=os.path.join(tempfile.mkdtemp(), "t.db"))


class BackendSelectionTests(unittest.TestCase):
    def test_defaults_to_sqlite_offline(self):
        self.assertEqual(_db().backend, "sqlite")

    def test_database_url_selects_postgres(self):
        # Backend is decided from DATABASE_URL; construction must not connect.
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://h/db",
                                          "AUTOMATION_FORCE_SQLITE": ""}):
            self.assertEqual(Database().backend, "postgres")

    def test_force_sqlite_overrides_database_url(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://h/db",
                                          "AUTOMATION_FORCE_SQLITE": "1"}):
            self.assertEqual(Database().backend, "sqlite")


class PoolerTests(unittest.TestCase):
    def test_detects_supabase_transaction_pooler(self):
        from automation import db
        self.assertTrue(db._is_pooler(
            "postgresql://postgres.ref:pw@aws-1-ap-south-1.pooler.supabase.com:6543/postgres"))

    def test_direct_connection_is_not_pooler(self):
        from automation import db
        self.assertFalse(db._is_pooler(
            "postgresql://postgres:pw@db.ref.supabase.co:5432/postgres"))


class DialectAdaptationTests(unittest.TestCase):
    def test_sqlite_keeps_qmark(self):
        d = _db()
        self.assertEqual(d._adapt("SELECT ? , ?"), "SELECT ? , ?")

    def test_postgres_translates_to_percent_s(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://h/db",
                                          "AUTOMATION_FORCE_SQLITE": ""}):
            d = Database()
        self.assertEqual(d._adapt("WHERE a=? AND b=?"), "WHERE a=%s AND b=%s")

    def test_statement_splitter(self):
        stmts = _split_statements("CREATE TABLE a(x TEXT);\n-- comment\nCREATE INDEX i ON a(x);")
        self.assertEqual(len(stmts), 2)
        self.assertTrue(stmts[0].startswith("CREATE TABLE a"))


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.db.executescript("CREATE TABLE t (id TEXT PRIMARY KEY);")

    def test_commit_persists(self):
        with self.db.tx() as tx:
            tx.execute("INSERT INTO t (id) VALUES (?)", ("a",))
        self.assertIsNotNone(self.db.query_one("SELECT 1 AS x FROM t WHERE id=?", ("a",)))

    def test_rollback_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.db.tx() as tx:
                tx.execute("INSERT INTO t (id) VALUES (?)", ("b",))
                raise RuntimeError("boom")
        self.assertIsNone(self.db.query_one("SELECT 1 AS x FROM t WHERE id=?", ("b",)))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = _db()

    def test_run_creates_full_schema(self):
        migrate.run(self.db, verbose=False)
        tables = {r["name"] for r in self.db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("workflows", "events", "processed", "oauth_accounts", "users",
                  "workspaces", "conversations", "automation_steps", "email_artifacts",
                  "email_messages", "email_replies", "metrics_daily", "audit_logs",
                  "usage_events", "billing_customers", "billing_subscriptions",
                  "schema_migrations"):
            self.assertIn(t, tables)

    def test_indexes_created(self):
        migrate.run(self.db, verbose=False)
        idx = {r["name"] for r in self.db.query(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")}
        self.assertIn("ix_wf_user", idx)
        self.assertIn("ix_tok_user", idx)
        self.assertIn("ix_conv_user", idx)

    def test_idempotent(self):
        first = migrate.run(self.db, verbose=False)
        second = migrate.run(self.db, verbose=False)
        self.assertTrue(first)          # applied on first run
        self.assertEqual(second, [])    # nothing pending on second

    def test_status_reports_applied_and_pending(self):
        st_before = migrate.status(self.db)
        self.assertTrue(st_before["pending"])
        migrate.run(self.db, verbose=False)
        st_after = migrate.status(self.db)
        self.assertEqual(st_after["pending"], [])
        self.assertIn("0001_platform.sql", st_after["applied"])


class StoreOnAbstractionTests(unittest.TestCase):
    """The two real stores work through the abstraction with a shared handle."""

    def test_stores_share_one_database(self):
        from automation.store import WorkflowStore
        from automation.tokens import TokenStore
        db = _db()
        ws = WorkflowStore(db=db)
        ts = TokenStore(db=db)
        # both schemas live in the one DB
        self.assertTrue(ws.mark_processed("k1"))
        self.assertFalse(ws.mark_processed("k1"))          # durable dedup
        ts.upsert(user_id="u", provider="gmail", account_email="a@x.com",
                  access_token="AT", refresh_token="RT", expires_at=9e12)
        self.assertEqual(ts.get("u", "gmail")["access_token"], "AT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
