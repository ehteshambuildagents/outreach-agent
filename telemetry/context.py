"""Ambient telemetry context — attributes a datapoint without threading params.

Low-level code (the LLM client) records tokens/cost but has no idea which user,
workflow, campaign, or agent it's serving. Rather than plumbing those through
every signature, higher layers open a ``scope`` and the client reads the current
context when it emits. Built on :mod:`contextvars`, so it is correct across async
tasks and threads and nests cleanly (an inner scope overlays the outer one).
"""

import contextvars
from contextlib import contextmanager

_FIELDS = ("user_id", "workspace_id", "workflow_id", "campaign_id", "agent")
_ctx = contextvars.ContextVar("telemetry_ctx", default={})


def current() -> dict:
    return dict(_ctx.get())


def get(field: str):
    return _ctx.get().get(field)


@contextmanager
def scope(**fields):
    """Overlay the given (non-None) fields for the duration of the block."""
    overlay = {k: v for k, v in fields.items() if k in _FIELDS and v is not None}
    token = _ctx.set({**_ctx.get(), **overlay})
    try:
        yield
    finally:
        _ctx.reset(token)
