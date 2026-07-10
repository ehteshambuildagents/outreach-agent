"""Instrumentation helpers — thin wrappers call sites use to record telemetry.

  * ``llm_span`` wraps one provider call: it times it, counts retries, and on
    completion reads the REAL token usage off the response (never estimated) and
    records an ``ai_requests`` row. Both success and failure are recorded.
  * ``track_agent`` wraps one agent/tool execution: it binds the ambient context
    (so LLM calls inside are attributed to that agent) and records an
    ``agent_runs`` row with duration + success on exit.

Both are defensive — a telemetry failure can never propagate into the caller.
"""

import time
from contextlib import contextmanager

from telemetry import context, recorder


class LlmSpan:
    """Wrap a single provider request (may involve internal retries)."""

    def __init__(self, provider: str, model_hint: str):
        self.provider = provider
        self.model_hint = model_hint
        self._t0 = time.perf_counter()
        self._attempts = 0

    def counted(self, fn):
        """Wrap the create()-lambda so each (re)try increments the attempt count."""
        def _inner():
            self._attempts += 1
            return fn()
        return _inner

    @property
    def _retries(self) -> int:
        return max(0, self._attempts - 1)

    def _latency_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def done(self, response):
        try:
            usage = getattr(response, "usage", None)
            pt = getattr(usage, "input_tokens", None) if usage is not None else None
            ct = getattr(usage, "output_tokens", None) if usage is not None else None
            cache_read = getattr(usage, "cache_read_input_tokens", None) if usage is not None else None
            model = getattr(response, "model", None) or self.model_hint
            recorder.record_ai_request(
                provider=self.provider, model=model,
                prompt_tokens=pt, completion_tokens=ct,
                latency_ms=self._latency_ms(), retries=self._retries, success=True,
                cache_hit=(bool(cache_read) if cache_read is not None else None))
        except Exception:  # noqa: BLE001
            pass
        return response

    def failed(self, exc):
        try:
            recorder.record_ai_request(
                provider=self.provider, model=self.model_hint,
                latency_ms=self._latency_ms(), retries=self._retries, success=False,
                failure_reason=type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass


def llm_span(provider: str, model_hint: str) -> LlmSpan:
    return LlmSpan(provider, model_hint)


class RunHandle:
    """Optional handle a tracked agent can enrich (decision/warnings/skipped)."""
    __slots__ = ("success", "retries", "skipped", "warnings", "decision", "detail")

    def __init__(self):
        self.success = True
        self.retries = 0
        self.skipped = False
        self.warnings = 0
        self.decision = None
        self.detail = None


@contextmanager
def track_agent(agent: str, *, decision=None, detail=None, **ctx):
    """Time + record an agent execution, binding context for nested LLM calls."""
    started = time.time()
    handle = RunHandle()
    ok = True
    with context.scope(agent=agent, **ctx):
        try:
            yield handle
        except Exception:
            ok = False
            raise
        finally:
            recorder.record_agent_run(
                agent=agent, started_at=started, finished_at=time.time(),
                success=ok and handle.success, retries=handle.retries,
                skipped=handle.skipped, warnings=handle.warnings,
                decision=handle.decision if handle.decision is not None else decision,
                detail=handle.detail if handle.detail is not None else detail)
