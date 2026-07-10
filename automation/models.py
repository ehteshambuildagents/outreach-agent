"""Workflow + Step data models (plain dataclasses, JSON-safe).

These are the durable record of an automation. Everything needed to RECOVER a
workflow after a crash lives here and is persisted by ``store``: which step we're
on, when the next action is due, provider message ids, retry counts, the last
error, and whether a reply has stopped the sequence.

The Automation Agent does NOT generate content — each Step carries the email a
writer already produced (subject/body/to) plus that artifact's stable id, so a
future agent can say "resend step 2" or "reuse the hook from version A".
"""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from automation import states


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Step:
    """One email in the sequence — content is pre-generated, never invented here."""
    index: int
    subject: str
    body: str
    to: str = ""
    artifact_id: str = ""                      # stable id of the writer artifact
    delay_days: int = 0                        # wait before THIS step (0 = first)
    status: str = states.STEP_PENDING
    scheduled_at: Optional[float] = None       # epoch when this step should send
    sent_at: Optional[float] = None
    provider_message_id: Optional[str] = None  # id the provider returned
    provider_thread_id: Optional[str] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    # Stable, unique key so a retried/duplicated send is a no-op at the provider
    # and in our store. Set once when the step is created.
    idempotency_key: str = field(default_factory=lambda: new_id("idem"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        known = {f: d.get(f) for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)


@dataclass
class Workflow:
    """A scheduled outreach sequence for one prospect, owned by one user."""
    user_id: str
    id: str = field(default_factory=lambda: new_id("wf"))
    state: str = states.DRAFT
    paused_from: Optional[str] = None          # state to resume to after PAUSED
    company: str = ""
    to_email: str = ""
    provider: str = "dryrun"                   # gmail | outlook | dryrun
    conversation_id: Optional[str] = None      # link back to the chat thread
    steps: List[Step] = field(default_factory=list)
    current_index: int = 0                      # step we're processing
    timezone: str = "UTC"
    next_run_at: Optional[float] = None         # when the engine should next act
    reply_detected: bool = False
    reply_at: Optional[float] = None
    reply_message_id: Optional[str] = None
    retry_count: int = 0                         # workflow-level failure count
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    # ── convenience ────────────────────────────────────────────────────
    def current_step(self) -> Optional[Step]:
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return None

    def has_more_steps(self) -> bool:
        return self.current_index < len(self.steps)

    def touch(self) -> None:
        self.updated_at = time.time()

    # ── (de)serialisation ──────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Workflow":
        steps = [Step.from_dict(s) for s in (d.get("steps") or [])]
        known = {f: d.get(f) for f in cls.__dataclass_fields__
                 if f in d and f != "steps"}
        return cls(steps=steps, **known)
