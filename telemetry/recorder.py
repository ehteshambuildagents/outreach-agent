"""Recorder — the public write API. Fill defaults, attribute via context, emit.

Every function is best-effort and NEVER raises: a telemetry failure must never
affect the product. Rows carry a stable ``id`` (caller-supplied or a uuid) so the
sink's ``ON CONFLICT DO NOTHING`` makes a double-record idempotent. Cost/token
DAILY totals are aggregated on read (query.py) straight from ``ai_requests`` — we
don't duplicate them into a rollup table.
"""

import time
import uuid

from telemetry import context, pricing
from telemetry.sink import emit


def _id(prefix: str, given=None) -> str:
    return str(given) if given else f"{prefix}_{uuid.uuid4().hex}"


def _ctx(overrides: dict) -> dict:
    merged = context.current()
    merged.update({k: v for k, v in overrides.items() if v is not None})
    return merged


def record_ai_request(*, provider, model, prompt_tokens=None, completion_tokens=None,
                      latency_ms=None, retries=0, success=True, failure_reason=None,
                      cache_hit=None, request_id=None, **ctx_overrides) -> str:
    """Record one LLM/API call. Tokens are the provider's real counts (or None)."""
    try:
        c = _ctx(ctx_overrides)
        total = None
        if prompt_tokens is not None or completion_tokens is not None:
            total = int(prompt_tokens or 0) + int(completion_tokens or 0)
        cost = pricing.cost(model, prompt_tokens, completion_tokens)
        rid = _id("air", request_id)
        emit("ai_requests", {
            "id": rid, "ts": time.time(),
            "user_id": c.get("user_id"), "workspace_id": c.get("workspace_id"),
            "workflow_id": c.get("workflow_id"), "campaign_id": c.get("campaign_id"),
            "agent": c.get("agent"), "provider": provider, "model": model,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": total,
            "estimated_cost": cost,
            "cost_basis": "provider_tokens" if cost is not None else "unavailable",
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "retries": int(retries or 0), "success": 1 if success else 0,
            "failure_reason": failure_reason,
            "cache_hit": None if cache_hit is None else (1 if cache_hit else 0),
        })
        return rid
    except Exception:  # noqa: BLE001 - telemetry never breaks the caller
        return ""


def record_agent_run(*, agent, started_at=None, finished_at=None, duration_ms=None,
                     success=True, retries=0, skipped=0, warnings=0, decision=None,
                     detail=None, run_id=None, **ctx_overrides) -> str:
    try:
        c = _ctx(ctx_overrides)
        if duration_ms is None and started_at is not None and finished_at is not None:
            duration_ms = round((finished_at - started_at) * 1000, 2)
        rid = _id("run", run_id)
        emit("agent_runs", {
            "id": rid, "ts": time.time(), "agent": agent,
            "user_id": c.get("user_id"), "workflow_id": c.get("workflow_id"),
            "campaign_id": c.get("campaign_id"),
            "started_at": started_at, "finished_at": finished_at,
            "duration_ms": duration_ms, "success": 1 if success else 0,
            "retries": int(retries or 0), "skipped": 1 if skipped else 0,
            "warnings": int(warnings or 0),
            "decision": str(decision) if decision is not None else None,
            "detail": (str(detail)[:500] if detail else None),
        })
        return rid
    except Exception:  # noqa: BLE001
        return ""


def record_event(category, event, *, value=None, entity_id=None, detail=None,
                 event_id=None, **ctx_overrides) -> str:
    """Record a lifecycle event (email / automation / campaign / agent)."""
    try:
        c = _ctx(ctx_overrides)
        eid = _id("evt", event_id)
        emit("telemetry_events", {
            "id": eid, "ts": time.time(), "category": category, "event": event,
            "user_id": c.get("user_id"), "workspace_id": c.get("workspace_id"),
            "workflow_id": c.get("workflow_id"), "campaign_id": c.get("campaign_id"),
            "entity_id": entity_id,
            "value": float(value) if value is not None else None,
            "detail": (str(detail)[:500] if detail else None),
        })
        return eid
    except Exception:  # noqa: BLE001
        return ""
