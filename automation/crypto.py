"""At-rest encryption for OAuth secrets (refresh/access tokens).

OAuth refresh tokens are long-lived credentials that can send mail as the user —
they must never sit in the database in plaintext. Every token is encrypted with
Fernet (AES-128-CBC + HMAC-SHA256, authenticated) before it touches SQLite and
decrypted only in memory when a send needs it.

Key material comes from the environment (``AUTOMATION_ENC_KEY``); a passphrase of
any length is accepted and stretched to a 32-byte Fernet key via SHA-256, so ops
can set a human-chosen secret. Multiple comma-separated keys enable **key
rotation**: the first encrypts, all decrypt (MultiFernet), so a new key can be
rolled in and the old retired without a flag-day re-encrypt.

If no key is configured a process-stable **development** key is derived and a
warning is logged once — real encryption still happens (so nothing is ever stored
plaintext), but production must set ``AUTOMATION_ENC_KEY``.
"""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("automation.crypto")

_DEV_PASSPHRASE = "saqua-dev-insecure-key-set-AUTOMATION_ENC_KEY-in-prod"
_warned = False


def _fernet_key(passphrase: str) -> bytes:
    """Stretch an arbitrary passphrase into a valid 32-byte urlsafe Fernet key."""
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _cipher() -> MultiFernet:
    global _warned
    raw = (os.environ.get("AUTOMATION_ENC_KEY") or "").strip()
    if not raw:
        if not _warned:
            log.warning("AUTOMATION_ENC_KEY not set — using a development key. "
                        "Set AUTOMATION_ENC_KEY before storing real OAuth tokens.")
            _warned = True
        raw = _DEV_PASSPHRASE
    keys = [_fernet_key(k.strip()) for k in raw.split(",") if k.strip()]
    return MultiFernet([Fernet(k) for k in keys])


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning urlsafe-base64 ciphertext safe to store as TEXT."""
    if plaintext is None:
        plaintext = ""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt ciphertext produced by :func:`encrypt`. Raises ValueError if the
    ciphertext is corrupt or was written under a key no longer configured."""
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("could not decrypt token (bad key or corrupt data)") from exc


def rotate(token: str) -> str:
    """Re-encrypt an existing ciphertext under the PRIMARY key (key-rotation sweep)."""
    return encrypt(decrypt(token))
