"""Per-user OAuth credential storage — encrypted, multi-account, refresh-aware.

Where a user's Gmail / Outlook connection lives after they consent, so the engine
can send as them. Backed by Postgres or SQLite via :mod:`automation.db`.

  * **Encrypted at rest** — access & refresh tokens are stored only as ciphertext
    (:mod:`automation.crypto`); plaintext exists only transiently in memory.
  * **Per-user & multi-account** — keyed by (user_id, provider, account_email); no
    user can read another's tokens.
  * **Expiry tracking + auto-refresh** — :meth:`valid_access_token` returns a live
    token, transparently refreshing (and persisting the possibly-rotated refresh
    token) shortly before expiry.
  * **Reconnect detection** — a failed refresh flags the account
    ``reconnect_required`` so the UI can prompt a re-consent.
"""

import json
import logging
import time

from automation import crypto
from automation.db import Database
from config.settings import AUTOMATION_TOKEN_REFRESH_SKEW

log = logging.getLogger("automation.tokens")

STATUS_CONNECTED = "connected"
STATUS_RECONNECT = "reconnect_required"
STATUS_REVOKED = "revoked"


class TokenStore:
    def __init__(self, path: str = None, db: Database = None):
        self.db = db or Database(sqlite_path=path)
        self.db.ensure_core_schema()

    # ── writes ─────────────────────────────────────────────────────────
    def upsert(self, *, user_id, provider, account_email, access_token,
               refresh_token=None, expires_at=None, scopes="") -> str:
        """Store (or update) a connected account. A missing refresh_token on update
        keeps the existing one (Google omits it on refresh); a present one rotates."""
        now = time.time()
        acct_id = f"acct_{provider}_{abs(hash((user_id, provider, account_email))) & 0xffffffff:x}"
        access_enc = crypto.encrypt(access_token) if access_token else None
        with self.db.tx() as tx:
            existing = tx.execute(
                "SELECT id, refresh_enc FROM oauth_accounts "
                "WHERE user_id=? AND provider=? AND account_email=?",
                (user_id, provider, account_email)).fetchone()
            if refresh_token:
                refresh_enc = crypto.encrypt(refresh_token)
            elif existing:
                refresh_enc = existing["refresh_enc"]     # keep prior refresh token
            else:
                refresh_enc = None
            if existing:
                acct_id = existing["id"]
                tx.execute(
                    "UPDATE oauth_accounts SET access_enc=?, refresh_enc=?, "
                    "expires_at=?, scopes=?, status=?, updated_at=? WHERE id=?",
                    (access_enc, refresh_enc, expires_at, scopes,
                     STATUS_CONNECTED, now, acct_id))
            else:
                tx.execute(
                    "INSERT INTO oauth_accounts (id,user_id,provider,account_email,"
                    "access_enc,refresh_enc,expires_at,scopes,status,watch_state,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (acct_id, user_id, provider, account_email, access_enc,
                     refresh_enc, expires_at, scopes, STATUS_CONNECTED, None, now, now))
        return acct_id

    def set_status(self, user_id, provider, account_email, status) -> None:
        self.db.execute(
            "UPDATE oauth_accounts SET status=?, updated_at=? "
            "WHERE user_id=? AND provider=? AND account_email=?",
            (status, time.time(), user_id, provider, account_email))

    def mark_reconnect(self, user_id, provider, account_email) -> None:
        self.set_status(user_id, provider, account_email, STATUS_RECONNECT)

    def set_watch_state(self, user_id, provider, account_email, state: dict) -> None:
        self.db.execute(
            "UPDATE oauth_accounts SET watch_state=?, updated_at=? "
            "WHERE user_id=? AND provider=? AND account_email=?",
            (json.dumps(state), time.time(), user_id, provider, account_email))

    def delete(self, user_id, provider, account_email) -> None:
        self.db.execute(
            "DELETE FROM oauth_accounts WHERE user_id=? AND provider=? AND account_email=?",
            (user_id, provider, account_email))

    # ── reads ──────────────────────────────────────────────────────────
    def _row(self, user_id, provider, account_email=None):
        if account_email:
            return self.db.query_one(
                "SELECT * FROM oauth_accounts WHERE user_id=? AND provider=? AND account_email=?",
                (user_id, provider, account_email))
        # default account for a provider: most recently updated connected one
        return self.db.query_one(
            "SELECT * FROM oauth_accounts WHERE user_id=? AND provider=? "
            "ORDER BY (status='connected') DESC, updated_at DESC LIMIT 1",
            (user_id, provider))

    def get(self, user_id, provider, account_email=None) -> dict:
        """Full record with tokens DECRYPTED (in-memory only). None if absent."""
        row = self._row(user_id, provider, account_email)
        return self._decode(row) if row else None

    def _decode(self, row) -> dict:
        return {
            "id": row["id"], "user_id": row["user_id"], "provider": row["provider"],
            "account_email": row["account_email"],
            "access_token": crypto.decrypt(row["access_enc"]) if row["access_enc"] else "",
            "refresh_token": crypto.decrypt(row["refresh_enc"]) if row["refresh_enc"] else "",
            "expires_at": row["expires_at"], "scopes": row["scopes"] or "",
            "status": row["status"],
            "watch_state": json.loads(row["watch_state"]) if row["watch_state"] else {},
            "updated_at": row["updated_at"],
        }

    def _decode_rows(self, rows) -> list:
        """Decode a batch of rows, SKIPPING any whose token can't be decrypted (a
        corrupt row, or one written under a retired/foreign key) instead of letting
        it abort the whole read. One poisoned row must not take down a bulk sweep —
        e.g. watch renewal for every OTHER account. The raw provider/email columns
        are safe to log; only the encrypted blob is undecodable."""
        out = []
        for r in rows:
            try:
                out.append(self._decode(r))
            except Exception as exc:  # noqa: BLE001 - one bad row must not poison the batch
                log.warning("skipping undecodable oauth row provider=%s email=%s: %s",
                            r["provider"], r["account_email"], exc)
        return out

    def list_accounts(self, user_id) -> list:
        """Connected accounts for the settings UI — NO token material exposed."""
        rows = self.db.query(
            "SELECT provider,account_email,status,expires_at,scopes,updated_at "
            "FROM oauth_accounts WHERE user_id=? ORDER BY provider, updated_at DESC",
            (user_id,))
        return [{"provider": r["provider"], "account_email": r["account_email"],
                 "status": r["status"], "expires_at": r["expires_at"],
                 "scopes": r["scopes"] or "",
                 "expired": bool(r["expires_at"] and r["expires_at"] < time.time())}
                for r in rows]

    def due_for_refresh(self, now=None, skew=AUTOMATION_TOKEN_REFRESH_SKEW) -> list:
        now = time.time() if now is None else now
        rows = self.db.query(
            "SELECT user_id,provider,account_email FROM oauth_accounts "
            "WHERE status=? AND expires_at IS NOT NULL AND expires_at <= ?",
            (STATUS_CONNECTED, now + skew))
        return [{"user_id": r["user_id"], "provider": r["provider"],
                 "account_email": r["account_email"]} for r in rows]

    def accounts_by_email(self, provider, account_email) -> list:
        """All connected accounts (any user) for a mailbox — used to route an
        inbound push notification back to the owning user(s)."""
        rows = self.db.query(
            "SELECT * FROM oauth_accounts WHERE provider=? AND account_email=? AND status=?",
            (provider, account_email, STATUS_CONNECTED))
        return self._decode_rows(rows)

    def with_watch(self, provider) -> list:
        rows = self.db.query(
            "SELECT * FROM oauth_accounts WHERE provider=? AND status=? "
            "AND watch_state IS NOT NULL", (provider, STATUS_CONNECTED))
        return self._decode_rows(rows)

    def connected_without_watch(self, provider) -> list:
        """Connected accounts that have NO watch yet (watch_state IS NULL) — the
        complement of ``with_watch``. The worker uses this to self-heal any account
        missing reply-detection so it doesn't silently stay dark."""
        rows = self.db.query(
            "SELECT * FROM oauth_accounts WHERE provider=? AND status=? "
            "AND watch_state IS NULL", (provider, STATUS_CONNECTED))
        return self._decode_rows(rows)

    # ── the important one: a guaranteed-fresh access token ─────────────
    def valid_access_token(self, user_id, provider, account_email=None, *, now=None):
        """Return a live access token, refreshing (and rotating) if near expiry.

        Returns "" when there is no account, and raises no exception on refresh
        failure — instead it flags the account ``reconnect_required`` and returns
        "" so the engine treats it as a (permanent) not-configured send.
        """
        now = time.time() if now is None else now
        rec = self.get(user_id, provider, account_email)
        if rec is None or rec["status"] != STATUS_CONNECTED:
            return ""
        fresh_enough = rec["expires_at"] and rec["expires_at"] > now + AUTOMATION_TOKEN_REFRESH_SKEW
        if rec["access_token"] and fresh_enough:
            return rec["access_token"]
        if not rec["refresh_token"]:
            self.mark_reconnect(user_id, provider, rec["account_email"])
            return ""
        from automation import oauth  # lazy: avoids import cycle
        try:
            new = oauth.refresh(provider, rec["refresh_token"])
        except oauth.OAuthError:
            self.mark_reconnect(user_id, provider, rec["account_email"])
            return ""
        self.upsert(user_id=user_id, provider=provider,
                    account_email=rec["account_email"],
                    access_token=new["access_token"],
                    refresh_token=new.get("refresh_token"),  # rotation-aware
                    expires_at=now + new.get("expires_in", 3600),
                    scopes=new.get("scope", rec["scopes"]))
        return new["access_token"]


# A module-level default store + a credentials_provider the engine can consume.
_default = None


def default_store() -> "TokenStore":
    global _default
    if _default is None:
        _default = TokenStore()
    return _default


def credentials_provider(user_id, provider):
    """Adapter matching engine's ``credentials_provider(user_id, provider)`` hook:
    returns a live access token string (or "" -> provider raises NotConfigured)."""
    if provider in ("gmail", "outlook"):
        return default_store().valid_access_token(user_id, provider)
    return None
