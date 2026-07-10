"""The workflow state machine — explicit states and explicit transitions.

A workflow moves through these states as it processes each email in a sequence:

    DRAFT -> READY -> QUEUED -> SENDING -> SENT -> WAITING -> CHECKING_REPLY
                                                     |              |
                                                     |          FOLLOW_UP -> QUEUED (next email)
                                                     |              |
                                              (reply) STOPPED    COMPLETED (no more emails)

PAUSED / CANCELLED can be reached from the live states; FAILED is retryable
(back to QUEUED) or terminal (STOPPED/CANCELLED). Every transition below is
enumerated — nothing is implicit, which is what makes recovery safe: a state
loaded from disk can only advance along a known edge.
"""

# ── States (plain strings so they serialise straight to JSON / SQLite) ──
DRAFT = "DRAFT"                    # created, artifacts not attached yet
READY = "READY"                   # artifacts attached, ready to schedule
QUEUED = "QUEUED"                 # a step is scheduled and due/pending
SENDING = "SENDING"               # a send is in flight
SENT = "SENT"                     # the step was accepted by the provider
WAITING = "WAITING"              # waiting out the inter-email delay
CHECKING_REPLY = "CHECKING_REPLY"  # delay elapsed; deciding follow-up vs stop
FOLLOW_UP = "FOLLOW_UP"          # advancing to the next email
COMPLETED = "COMPLETED"          # all emails sent, no reply required to end
STOPPED = "STOPPED"              # a reply arrived — sequence halted on purpose
FAILED = "FAILED"                # a step failed (retryable or terminal)
CANCELLED = "CANCELLED"          # user cancelled
PAUSED = "PAUSED"                # user paused; resumes to the prior phase

ALL = (DRAFT, READY, QUEUED, SENDING, SENT, WAITING, CHECKING_REPLY, FOLLOW_UP,
       COMPLETED, STOPPED, FAILED, CANCELLED, PAUSED)

# Terminal states never transition again.
TERMINAL = frozenset({COMPLETED, STOPPED, CANCELLED})

# States from which a user may pause; resume returns to the recorded prior phase.
PAUSABLE = frozenset({READY, QUEUED, WAITING})

# The explicit transition table. A transition not listed here is illegal.
TRANSITIONS = {
    DRAFT:          {READY, CANCELLED, FAILED},
    READY:          {QUEUED, PAUSED, CANCELLED},
    QUEUED:         {SENDING, PAUSED, CANCELLED, FAILED, COMPLETED},
    SENDING:        {SENT, FAILED, QUEUED},   # QUEUED = a transient-failure retry
    SENT:           {WAITING, COMPLETED, STOPPED},
    WAITING:        {CHECKING_REPLY, PAUSED, CANCELLED, STOPPED},
    CHECKING_REPLY: {FOLLOW_UP, COMPLETED, STOPPED, WAITING},
    FOLLOW_UP:      {QUEUED, COMPLETED},
    FAILED:         {QUEUED, CANCELLED, STOPPED, FAILED},
    PAUSED:         {READY, QUEUED, WAITING, CANCELLED},
    COMPLETED:      set(),
    STOPPED:        set(),
    CANCELLED:      set(),
}


class InvalidTransition(Exception):
    """Raised when code attempts a transition the machine does not allow."""


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, set())


def assert_transition(src: str, dst: str) -> None:
    if not can_transition(src, dst):
        raise InvalidTransition(f"{src} -> {dst} is not allowed")


def is_terminal(state: str) -> bool:
    return state in TERMINAL


# ── Step-level status (each email in the sequence has its own small lifecycle) ──
STEP_PENDING = "pending"
STEP_QUEUED = "queued"
STEP_SENDING = "sending"
STEP_SENT = "sent"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"          # not sent because a reply stopped the sequence
STEP_STATES = (STEP_PENDING, STEP_QUEUED, STEP_SENDING, STEP_SENT, STEP_FAILED,
               STEP_SKIPPED)
