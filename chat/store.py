"""Conversation persistence — one JSON file per thread on local disk.

Deliberately simple and dependency-free: the sidebar's "previous conversations"
must survive an app restart, so threads live as files under CHAT_STORE_DIR
(git-ignored). Writes are atomic (temp file + replace) so an interrupted save
never corrupts a thread. Swappable later for a real DB behind the same API.
"""

import json
import os
import tempfile

from config.settings import CHAT_STORE_DIR
from chat.models import Conversation


class ConversationStore:
    def __init__(self, directory: str = CHAT_STORE_DIR):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, conversation_id: str) -> str:
        # Guard against path traversal from an unexpected id.
        safe = "".join(c for c in conversation_id if c.isalnum() or c in "-_")
        return os.path.join(self.directory, f"{safe}.json")

    def save(self, conversation: Conversation) -> None:
        path = self._path(conversation.id)
        payload = json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2)
        # Atomic write: never leave a half-written thread on a crash.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def load(self, conversation_id: str):
        path = self._path(conversation_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return Conversation.from_dict(json.load(fh))
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        if os.path.exists(path):
            os.remove(path)

    # ── Per-user writing-style profile (not a conversation) ────────────
    # Stored beside the threads under a reserved, underscore-prefixed name so it
    # is never mistaken for a conversation by list_summaries/load.
    _PROFILE_NAME = "_style_profile.json"

    def load_profile(self) -> dict:
        path = os.path.join(self.directory, self._PROFILE_NAME)
        if not os.path.exists(path):
            return {"avoid": [], "prefer": {}, "edits": 0}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {"avoid": [], "prefer": {}, "edits": 0}
        except (json.JSONDecodeError, OSError):
            return {"avoid": [], "prefer": {}, "edits": 0}

    def save_profile(self, profile: dict) -> None:
        if not isinstance(profile, dict):
            return
        self._write_reserved(self._PROFILE_NAME, profile)

    # ── Per-user company profile (the SENDER's own company details) ────
    # The user's company identity, set from Settings and injected into every
    # chat so the agent researches and drafts on their behalf. Stored beside the
    # threads under a reserved, underscore-prefixed name (skipped by
    # list_summaries) so it is never mistaken for a conversation.
    _COMPANY_NAME = "_company_profile.json"

    def load_company(self) -> dict:
        path = os.path.join(self.directory, self._COMPANY_NAME)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save_company(self, company: dict) -> None:
        if not isinstance(company, dict):
            return
        self._write_reserved(self._COMPANY_NAME, company)

    # ── Per-user free-tier usage (distinct prospects worked) ───────────
    # A dependency-free entitlement counter: the set of prospect keys (domains /
    # normalized company names) this user has researched, used to enforce the
    # Free plan's prospect cap. Reserved, underscore-prefixed (skipped by
    # list_summaries). Swap for the billing/entitlement DB when one exists.
    _USAGE_NAME = "_usage.json"

    def load_usage(self) -> dict:
        path = os.path.join(self.directory, self._USAGE_NAME)
        if not os.path.exists(path):
            return {"prospects": []}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return {"prospects": []}
            if not isinstance(data.get("prospects"), list):
                data["prospects"] = []
            return data
        except (json.JSONDecodeError, OSError):
            return {"prospects": []}

    def save_usage(self, usage: dict) -> None:
        if not isinstance(usage, dict):
            return
        self._write_reserved(self._USAGE_NAME, usage)

    def _write_reserved(self, name: str, data: dict) -> None:
        """Atomic write for a reserved (non-conversation) per-user JSON file."""
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, os.path.join(self.directory, name))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def list_summaries(self) -> list:
        """Lightweight thread list for the sidebar, newest first."""
        summaries = []
        for name in os.listdir(self.directory):
            if not name.endswith(".json") or name.startswith("_"):
                continue                       # skip reserved files (style profile)
            try:
                with open(os.path.join(self.directory, name), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            summaries.append({
                "id": data.get("id"),
                "title": data.get("title", "Conversation"),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        summaries.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return summaries
