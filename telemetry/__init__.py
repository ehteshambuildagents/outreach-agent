"""Telemetry & Observability — the single source of truth for how Saqua runs.

Records, durably and asynchronously, every AI request (tokens/cost/latency),
agent execution, and email/automation lifecycle event, then exposes read-only
services to query spend, tokens, reliability, guard stats, and queue/worker health.

Design guarantees: deterministic, never estimates when the provider returns exact
values, never fabricates a metric (unavailable is recorded as unavailable), never
raises into the product, idempotent, and negligible overhead (enqueue + a single
background writer thread). Reuses ``automation.db`` (no new connection layer) and
``automation.health``/``metrics``; adds only the three tables nothing else fits.

Public API:
    scope(...) / track_agent(...) / llm_span(...)   — instrumentation
    record_ai_request / record_agent_run / record_event
    query.*                                          — observability
    flush() / stop()                                 — lifecycle (tests/shutdown)
"""

from telemetry import query
from telemetry.context import current, scope
from telemetry.instrument import llm_span, track_agent
from telemetry.recorder import record_agent_run, record_ai_request, record_event
from telemetry.sink import flush, stop

__all__ = [
    "scope", "current", "track_agent", "llm_span",
    "record_ai_request", "record_agent_run", "record_event",
    "query", "flush", "stop",
]
