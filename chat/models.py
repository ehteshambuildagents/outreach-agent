"""Conversation data model (plain dataclasses, JSON-serialisable).

A ``Conversation`` is one company's chat thread. Its ``workspace`` is the shared
state the tools read and write — crucially the CACHED research and current email
— which is how the agent reuses prior research instead of re-crawling a site.
Messages are the visible transcript; rich ones (an email, a research summary)
carry a structured ``data`` payload the UI renders as a card.
"""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional

# Message kinds the UI knows how to render. "text" is plain markdown; the others
# carry a structured payload in Message.data.
TEXT = "text"
EMAIL = "email"
RESEARCH = "research"
NOTICE = "notice"
PROSPECTS = "prospects"     # a scored, browsable list (collapsed preview + expand)
CHANNEL = "channel"         # a safe-channel draft (X/Reddit/HN reply, contact form)
# Co-founder cards — the user's REAL operating state (grounded in live automation
# data, never fabricated). See chat/tools.py get_stats / summarize_replies /
# list_campaigns.
STATS = "stats"             # the user's outreach analytics (sent / replies / rate)
REPLIES = "replies"         # prospects who replied across the user's sequences
CAMPAIGNS = "campaigns"     # the user's campaigns + where each one stands


@dataclass
class Message:
    role: str                       # "user" | "assistant"
    content: str                    # markdown shown in the bubble
    kind: str = TEXT                # TEXT | EMAIL | RESEARCH | NOTICE
    data: Optional[dict] = None     # structured payload for rich rendering
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(role=d["role"], content=d.get("content", ""),
                   kind=d.get("kind", TEXT), data=d.get("data"),
                   ts=d.get("ts", time.time()))


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Conversation:
    id: str = field(default_factory=new_id)
    title: str = "New conversation"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: List[Message] = field(default_factory=list)
    # Shared tool state. Keys used by the built-in tools:
    #   "company"      -> best label for the thread (name or host)
    #   "company_url"  -> the URL research ran against
    #   "research"     -> the full research_company() result (reused, not re-run)
    #   "email"        -> the current write_email() result ({subject, body, ...})
    workspace: dict = field(default_factory=dict)
    # Canonical research-trail events (chat.research_trail), persisted so a restored
    # thread still shows the honest, evidence-backed trail of what the agent did.
    # Capped to the most recent events; never replayed as if live.
    research_trail: List[dict] = field(default_factory=list)

    # ── transcript helpers ─────────────────────────────────────────────
    def add(self, message: Message) -> Message:
        self.messages.append(message)
        self.updated_at = time.time()
        return message

    def add_trail_event(self, evt: dict, *, cap: int = 60) -> dict:
        """Append a canonical research-trail event, keeping only the newest ``cap``.
        Does not bump ``updated_at`` — the turn's messages already do, and a trail
        event must not reorder the sidebar on its own."""
        self.research_trail.append(evt)
        if len(self.research_trail) > cap:
            del self.research_trail[:-cap]
        return evt

    def add_user(self, text: str) -> Message:
        return self.add(Message(role="user", content=text))

    def add_assistant(self, text: str, kind: str = TEXT, data: dict = None) -> Message:
        return self.add(Message(role="assistant", content=text, kind=kind, data=data))

    def is_empty(self) -> bool:
        return not self.messages

    # ── (de)serialisation ──────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
            "workspace": self.workspace,
            "research_trail": self.research_trail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        return cls(
            id=d.get("id") or new_id(),
            title=d.get("title", "New conversation"),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
            workspace=d.get("workspace") or {},
            research_trail=d.get("research_trail") or [],
        )
