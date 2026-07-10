"""Canonical environment loading — one place, used by every entrypoint.

The project keeps secrets in two git-ignored files at the repo root:
    .env.local   (e.g. Clerk keys)
    .env         (model key, DATABASE_URL, provider keys, …)

Every process — the API server, the background worker, the migration runner, the
integration verifier, and ``config.settings`` — MUST load the same two files in
the same order so they all agree on configuration (this fixes the bug where
``python -m automation.migrate`` saw SQLite while the verifier saw Postgres).

Order: ``.env.local`` first, then ``.env``. ``load_dotenv`` does not override
variables already set, so an explicit shell/OS variable and ``.env.local`` win
over ``.env`` — matching the long-standing server behaviour. Idempotent.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_loaded = False


def load_env(force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    load_dotenv(_ROOT / ".env.local")
    load_dotenv(_ROOT / ".env")
    _loaded = True


def project_root() -> Path:
    return _ROOT
