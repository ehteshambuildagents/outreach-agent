"""Metrics + structured logging for the Automation Agent.

Counters are process-local and cheap (observability, not billing). ``event()``
emits a single structured log line per meaningful action so failures always carry
workflow id / step / provider / operation / error — never a bare print.
"""

import json
import logging
import threading
import time

log = logging.getLogger("automation")

_lock = threading.Lock()
_counters = {
    "emails_sent": 0,
    "replies": 0,
    "failed_sends": 0,
    "retries": 0,
    "workflows_created": 0,
    "workflows_completed": 0,
    "workflows_stopped": 0,      # stopped by a reply
    "workflows_cancelled": 0,
    "oauth_failures": 0,         # token exchange/refresh failures
    "provider_failures": 0,      # watch/subscription failures
    "send_latency_ms_total": 0,
    "send_latency_count": 0,
}


def incr(name: str, by: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + by


def observe_send_latency(ms: float) -> None:
    with _lock:
        _counters["send_latency_ms_total"] += ms
        _counters["send_latency_count"] += 1


def snapshot() -> dict:
    with _lock:
        c = dict(_counters)
    sent = c["emails_sent"]
    replies = c["replies"]
    created = c["workflows_created"]
    count = c["send_latency_count"] or 1
    c["reply_rate"] = round(replies / sent, 4) if sent else 0.0
    c["stop_rate"] = round(c["workflows_stopped"] / created, 4) if created else 0.0
    c["retry_count"] = c["retries"]
    c["avg_send_latency_ms"] = round(c["send_latency_ms_total"] / count, 1)
    return c


def reset() -> None:  # test helper
    with _lock:
        for k in _counters:
            _counters[k] = 0


def event(level: int, workflow_id: str, step, provider: str, operation: str,
          message: str = "", error: str = "") -> None:
    """One structured log line. Always machine-parseable; no secrets."""
    payload = {
        "ts": round(time.time(), 3),
        "workflow_id": workflow_id,
        "step": step,
        "provider": provider,
        "op": operation,
    }
    if message:
        payload["msg"] = message
    if error:
        payload["error"] = error
    log.log(level, "automation %s", json.dumps(payload, ensure_ascii=False))
