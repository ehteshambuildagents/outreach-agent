"""Scheduling maths — pure, deterministic, timezone-aware.

No side effects and no hidden clock: every function takes ``now`` (epoch seconds)
so it is trivially testable and reproducible. Nothing about timing is hard-coded
in the engine; it all comes from here and ``config.settings``.
"""

import random
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

from config.settings import (
    AUTOMATION_BACKOFF_BASE_SECONDS,
    AUTOMATION_BACKOFF_MAX_SECONDS,
    AUTOMATION_MAX_RETRIES,
)

_DAY = 86400.0


def _tz(name: str):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown tz -> treat as UTC
        return None


def send_at(now: float, *, delay_days: float = 0, at_epoch: float = None) -> float:
    """When a step should send. An explicit ``at_epoch`` wins (send-later);
    otherwise ``now`` plus the inter-email delay. Never returns a past time for a
    positive delay."""
    if at_epoch is not None:
        return max(float(at_epoch), now)
    return now + max(0.0, float(delay_days)) * _DAY


def schedule_steps(steps, now: float, *, tz: str = "UTC") -> None:
    """Assign each step a ``scheduled_at`` cumulatively from its ``delay_days``.

    Step 0 sends at ``now`` (unless it carries its own delay); each later step is
    its delay after the PRIOR step's scheduled time — so a [0, 3, 4] sequence
    lands at now, +3d, +7d. Mutates the steps in place.
    """
    cursor = now
    for i, step in enumerate(steps):
        delay = getattr(step, "delay_days", 0) or 0
        cursor = cursor + delay * _DAY if i > 0 else now + delay * _DAY
        step.scheduled_at = cursor


def backoff_delay(retry_count: int, *, jitter: bool = True,
                  base: float = None, cap: float = None) -> float:
    """Exponential backoff (base * 2**retry) capped, with optional ±25% jitter."""
    base = AUTOMATION_BACKOFF_BASE_SECONDS if base is None else base
    cap = AUTOMATION_BACKOFF_MAX_SECONDS if cap is None else cap
    delay = min(cap, base * (2 ** max(0, retry_count)))
    if jitter:
        delay += random.uniform(0, delay * 0.25)
    return delay


def should_retry(retry_count: int, *, max_retries: int = None) -> bool:
    max_retries = AUTOMATION_MAX_RETRIES if max_retries is None else max_retries
    return retry_count < max_retries


def local_time(epoch: float, tz: str = "UTC") -> datetime:
    """The wall-clock time at ``epoch`` in the given timezone (for display/logs)."""
    zone = _tz(tz)
    dt = datetime.fromtimestamp(epoch, zone) if zone else datetime.utcfromtimestamp(epoch)
    return dt


def within_send_window(epoch: float, tz: str = "UTC",
                       start_hour: int = None, end_hour: int = None) -> bool:
    """Optional business-hours guard: True if the local hour is inside
    [start_hour, end_hour). When bounds are None, always True (no restriction)."""
    if start_hour is None or end_hour is None:
        return True
    hour = local_time(epoch, tz).hour
    return start_hour <= hour < end_hour


def next_business_time(epoch: float, tz: str = "UTC",
                       start_hour: int = 8, end_hour: int = 18) -> float:
    """Nudge a send time forward into the next local business window. Deterministic."""
    zone = _tz(tz)
    dt = datetime.fromtimestamp(epoch, zone) if zone else datetime.utcfromtimestamp(epoch)
    if dt.hour < start_hour:
        dt = dt.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    elif dt.hour >= end_hour:
        dt = (dt + timedelta(days=1)).replace(hour=start_hour, minute=0,
                                              second=0, microsecond=0)
    return dt.timestamp()
