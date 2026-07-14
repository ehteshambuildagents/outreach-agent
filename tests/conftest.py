"""Test-suite defaults: keep every test offline and deterministic.

The automation stores select Postgres when DATABASE_URL is set — but the suite is
designed to run with no external services, so we force the local SQLite backend
here (this survives a later ``load_dotenv`` because db.is_postgres() checks the
flag, not just the URL). A stable encryption key keeps token ciphertext
reproducible. Individual modules still force in-memory Redis as they do today.
"""

import os

os.environ["AUTOMATION_FORCE_SQLITE"] = "1"
# Belt-and-suspenders: even if the flag above is ever flipped, the suite must not
# be able to SEE a production DATABASE_URL. We set it empty rather than pop it,
# because config.env.load_env() calls load_dotenv(override=False) — a popped key
# would be re-loaded from .env, but a present-but-empty key is left untouched.
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("AUTOMATION_ENC_KEY", "test-fixed-key")
# Telemetry writes synchronously in tests (no background thread) so recorded
# datapoints are queryable immediately and deterministically.
os.environ["TELEMETRY_SYNC"] = "1"
