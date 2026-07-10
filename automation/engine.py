"""The Automation Agent — the conductor.

It executes workflows; it never researches, writes, or rewrites. Each Step
already carries a writer-produced email. The engine only DECIDES what happens
next and does it: send when due, wait out the delay, check for a reply, follow
up or stop, retry with backoff, and survive restarts because the SQLite store is
the source of truth.

Idempotency is layered so nothing double-fires:
  * a step is only sent when its persisted status is not already SENT;
  * a per-workflow Redis lock serialises concurrent ticks/webhooks;
  * a per-user Redis rate-limit throttles sends;
  * inbound replies are de-duplicated on message id in the durable ledger.

Recovery: ``recover()`` re-queues any step caught mid-send by a crash; the tick
loop then reprocesses it. The dry-run/provider layer dedupes on the step's stable
idempotency key so a re-drive doesn't produce a second email.
"""

import logging
import time
import uuid

from automation import metrics, redis, scheduler, states
from automation.models import Step, Workflow
from automation.providers import ProviderNotConfigured, get_provider
from telemetry import record_event   # durable lifecycle telemetry; never raises
from config.settings import (
    AUTOMATION_LOCK_TTL_SECONDS,
    AUTOMATION_REPLY_WAIT_DAYS,
    AUTOMATION_SEND_RATE_PER_MIN,
    AUTOMATION_TICK_BATCH,
)

log = logging.getLogger("automation.engine")


# ── Creation ───────────────────────────────────────────────────────────
def create_workflow(store, user_id, step_specs, *, company="", to_email="",
                    provider="dryrun", conversation_id=None, timezone="UTC",
                    start_at=None, now=None) -> Workflow:
    """Build and persist a workflow from PRE-GENERATED email artifacts.

    step_specs: [{subject, body, to?, artifact_id?, delay_days?}, ...] — content
    the writer already produced. The engine schedules and sends them; it does not
    create content.
    """
    now = time.time() if now is None else now
    if not step_specs:
        raise ValueError("a workflow needs at least one email step")
    steps = []
    for i, spec in enumerate(step_specs):
        steps.append(Step(
            index=i,
            subject=str(spec.get("subject") or "").strip(),
            body=str(spec.get("body") or "").strip(),
            to=str(spec.get("to") or to_email or "").strip(),
            artifact_id=str(spec.get("artifact_id") or ""),
            delay_days=int(spec.get("delay_days") or (0 if i == 0
                          else AUTOMATION_REPLY_WAIT_DAYS)),
        ))
    scheduler.schedule_steps(steps, start_at or now, tz=timezone)
    wf = Workflow(user_id=user_id, company=company, to_email=to_email,
                  provider=provider, conversation_id=conversation_id,
                  timezone=timezone, steps=steps, current_index=0)
    wf.state = states.READY
    wf.next_run_at = steps[0].scheduled_at
    wf.state = states.QUEUED
    store.save(wf)
    store.add_event(wf.id, "created", f"{len(steps)} steps via {provider}")
    record_event("automation", "workflow_created", user_id=user_id, workflow_id=wf.id,
                 campaign_id=conversation_id, value=len(steps), detail=provider)
    metrics.incr("workflows_created")
    metrics.event(logging.INFO, wf.id, 0, provider, "create",
                  f"{len(steps)} steps for {to_email}")
    return wf


# ── The tick loop (a worker calls this on a schedule) ──────────────────
def tick(store, *, now=None, credentials_provider=None,
         batch=AUTOMATION_TICK_BATCH) -> int:
    """Advance every workflow whose next action is due. Returns how many were
    processed. Safe to call from multiple workers: each workflow is locked."""
    now = time.time() if now is None else now
    processed = 0
    for wf in store.due_workflows(now=now, limit=batch):
        token = uuid.uuid4().hex
        if not redis.acquire_lock(wf.id, token, ttl_seconds=AUTOMATION_LOCK_TTL_SECONDS):
            continue                       # another worker/tick owns it
        try:
            fresh = store.load(wf.id)      # re-read under lock (avoid stale writes)
            if fresh is None:
                continue
            advance_workflow(fresh, store, now=now,
                             credentials_provider=credentials_provider)
            processed += 1
        finally:
            redis.release_lock(wf.id, token)
    return processed


def advance_workflow(wf, store, *, now=None, credentials_provider=None) -> Workflow:
    """One deterministic step of the machine for a single workflow."""
    now = time.time() if now is None else now
    if states.is_terminal(wf.state):
        return wf
    if wf.reply_detected:                  # a reply always wins: stop the sequence
        return _stop_for_reply(wf, store)

    if wf.state in (states.QUEUED, states.READY, states.FAILED):
        return _try_send_current(wf, store, now, credentials_provider)
    if wf.state == states.WAITING:
        if wf.next_run_at and now >= wf.next_run_at:
            return _after_wait(wf, store, now)
    return wf


# ── Sending ────────────────────────────────────────────────────────────
def _try_send_current(wf, store, now, credentials_provider):
    step = wf.current_step()
    if step is None:
        return _complete(wf, store)
    if step.scheduled_at and now < step.scheduled_at:
        wf.next_run_at = step.scheduled_at            # not due yet
        _transition(wf, store, states.QUEUED, "wait_until_scheduled")
        return wf
    # Idempotency: never send a step that already went out.
    if step.status == states.STEP_SENT:
        return _after_send(wf, store, now)

    # Per-user send rate limit (Redis fixed window). Over limit -> defer briefly.
    if redis.rate_limited(f"send:{wf.user_id}", AUTOMATION_SEND_RATE_PER_MIN, 60):
        wf.next_run_at = now + 30
        store.save(wf)
        metrics.event(logging.INFO, wf.id, step.index, wf.provider, "rate_deferred")
        return wf

    provider = _provider_for(wf, credentials_provider)
    # Normalise to QUEUED first (READY/FAILED -> QUEUED are legal) so the only
    # edge into SENDING is the single, explicit QUEUED -> SENDING.
    if wf.state != states.QUEUED:
        _transition(wf, store, states.QUEUED, "queued for send")
    _transition(wf, store, states.SENDING, f"sending step {step.index}")
    step.status = states.STEP_SENDING
    started = time.time()
    try:
        result = provider.send(to=step.to, subject=step.subject, body=step.body,
                               idempotency_key=step.idempotency_key,
                               thread_id=step.provider_thread_id)
    except ProviderNotConfigured as exc:
        return _fail(wf, step, store, str(exc), permanent=True)
    except Exception as exc:  # noqa: BLE001 - transient provider/network error
        retryable = getattr(exc, "retryable", True)
        return _fail(wf, step, store, f"{type(exc).__name__}: {exc}",
                     permanent=not retryable, now=now)

    metrics.observe_send_latency((time.time() - started) * 1000)
    step.status = states.STEP_SENT
    step.sent_at = now
    step.provider_message_id = result.message_id
    step.provider_thread_id = result.thread_id
    step.last_error = None
    metrics.incr("emails_sent")
    _transition(wf, store, states.SENT, f"sent step {step.index}")
    store.add_event(wf.id, "sent", f"step {step.index} id={result.message_id}")
    record_event("email", "sent", user_id=wf.user_id, workflow_id=wf.id,
                 campaign_id=wf.conversation_id, entity_id=result.message_id,
                 value=(time.time() - started) * 1000)
    metrics.event(logging.INFO, wf.id, step.index, wf.provider, "sent",
                  f"message_id={result.message_id}")
    return _after_send(wf, store, now)


def _after_send(wf, store, now):
    """After a successful send: either wait for the next step, or complete."""
    if wf.current_index + 1 < len(wf.steps):
        nxt = wf.steps[wf.current_index + 1]
        wf.next_run_at = nxt.scheduled_at or (now + AUTOMATION_REPLY_WAIT_DAYS * 86400)
        _transition(wf, store, states.WAITING,
                    f"waiting until {int(wf.next_run_at)} for step {nxt.index}")
        return wf
    return _complete(wf, store)


def _after_wait(wf, store, now):
    """The inter-email delay elapsed: check for a reply, then follow up or stop."""
    _transition(wf, store, states.CHECKING_REPLY, "delay elapsed")
    if wf.reply_detected:
        return _stop_for_reply(wf, store)
    # No reply -> advance to the next email.
    wf.current_index += 1
    _transition(wf, store, states.FOLLOW_UP, f"advancing to step {wf.current_index}")
    step = wf.current_step()
    if step is None:
        return _complete(wf, store)
    wf.next_run_at = max(now, step.scheduled_at or now)
    _transition(wf, store, states.QUEUED, f"queued step {step.index}")
    return wf


def _fail(wf, step, store, reason, *, permanent=False, now=None):
    now = time.time() if now is None else now
    step.retry_count += 1
    step.last_error = reason
    wf.last_error = reason
    metrics.incr("failed_sends")
    if not permanent and scheduler.should_retry(step.retry_count):
        metrics.incr("retries")
        delay = scheduler.backoff_delay(step.retry_count)
        wf.next_run_at = now + delay
        step.status = states.STEP_QUEUED
        _transition(wf, store, states.QUEUED,
                    f"retry {step.retry_count} in {int(delay)}s: {reason}")
        metrics.event(logging.WARNING, wf.id, step.index, wf.provider, "retry",
                      error=reason)
        return wf
    step.status = states.STEP_FAILED
    wf.next_run_at = None          # park it: the tick loop must not re-pick a dead letter
    record_event("automation", "job_failed", user_id=wf.user_id, workflow_id=wf.id,
                 campaign_id=wf.conversation_id, detail=reason[:200])
    _transition(wf, store, states.FAILED, f"gave up: {reason}")
    metrics.event(logging.ERROR, wf.id, step.index, wf.provider, "failed",
                  error=reason)
    return wf


def _complete(wf, store):
    for s in wf.steps:
        if s.status == states.STEP_PENDING:
            s.status = states.STEP_SENT if s.sent_at else s.status
    wf.next_run_at = None
    _transition(wf, store, states.COMPLETED, "all steps sent")
    metrics.incr("workflows_completed")
    return wf


# ── Replies ────────────────────────────────────────────────────────────
def ingest_reply(store, *, message_id, workflow_id=None, user_id=None,
                 thread_id=None, now=None) -> Workflow:
    """Record an inbound reply and STOP the workflow. Idempotent on message id:
    a duplicate webhook is a no-op. Never sends another follow-up afterwards."""
    now = time.time() if now is None else now
    # Durable idempotency: a redelivered notification is ignored.
    if message_id and not store.mark_processed(f"reply:{message_id}"):
        log.info("duplicate reply webhook ignored: %s", message_id)
        return None

    wf = None
    if workflow_id:
        wf = store.load(workflow_id, user_id=user_id)
    elif thread_id and user_id:
        for cand in store.list_for_user(user_id):
            if any(s.provider_thread_id == thread_id for s in cand.steps):
                wf = cand
                break
    if wf is None:
        log.info("reply had no matching workflow: mid=%s thread=%s", message_id, thread_id)
        return None

    if wf.reply_detected or states.is_terminal(wf.state):
        return wf                          # already stopped
    wf.reply_detected = True
    wf.reply_at = now
    wf.reply_message_id = message_id
    metrics.incr("replies")
    store.add_event(wf.id, "reply", f"message_id={message_id}")
    record_event("email", "replied", user_id=wf.user_id, workflow_id=wf.id,
                 campaign_id=wf.conversation_id, entity_id=message_id)
    return _stop_for_reply(wf, store)


def _stop_for_reply(wf, store):
    for s in wf.steps:
        if s.status in (states.STEP_PENDING, states.STEP_QUEUED):
            s.status = states.STEP_SKIPPED
    wf.next_run_at = None
    # STOPPED is reachable from the live states; force it directly (a reply is an
    # out-of-band, always-legal halt).
    prev = wf.state
    wf.state = states.STOPPED
    wf.touch()
    store.save(wf)
    store.add_event(wf.id, "stopped", f"reply halted from {prev}")
    record_event("email", "sequence_stopped", user_id=wf.user_id, workflow_id=wf.id,
                 campaign_id=wf.conversation_id)
    metrics.incr("workflows_stopped")
    metrics.event(logging.INFO, wf.id, wf.current_index, wf.provider, "stopped",
                  "reply received")
    return wf


# ── User controls ──────────────────────────────────────────────────────
def cancel(store, user_id, workflow_id) -> Workflow:
    wf = store.load(workflow_id, user_id=user_id)
    if wf is None or states.is_terminal(wf.state):
        return wf
    for s in wf.steps:
        if s.status in (states.STEP_PENDING, states.STEP_QUEUED):
            s.status = states.STEP_SKIPPED
    wf.next_run_at = None
    wf.state = states.CANCELLED
    store.save(wf)
    store.add_event(wf.id, "cancelled", "")
    metrics.incr("workflows_cancelled")
    return wf


def pause(store, user_id, workflow_id) -> Workflow:
    wf = store.load(workflow_id, user_id=user_id)
    if wf is None or wf.state not in states.PAUSABLE:
        return wf
    wf.paused_from = wf.state
    _transition(wf, store, states.PAUSED, "paused by user")
    return wf


def resume(store, user_id, workflow_id, *, now=None) -> Workflow:
    now = time.time() if now is None else now
    wf = store.load(workflow_id, user_id=user_id)
    if wf is None or wf.state != states.PAUSED:
        return wf
    back = wf.paused_from or states.QUEUED
    wf.paused_from = None
    if wf.next_run_at is None:
        wf.next_run_at = now
    _transition(wf, store, back, "resumed by user")
    return wf


# ── Admin recovery (operator controls for stuck / failed workflows) ────
def dead_letter(store, user_id=None) -> list:
    """Workflows that exhausted retries and are parked in FAILED with no next
    action — the dead-letter queue an operator can inspect and re-drive."""
    wfs = [w for w in store.workflows_in_state(states.FAILED) if w.next_run_at is None]
    return [w for w in wfs if user_id is None or w.user_id == user_id]


def force_retry(store, user_id, workflow_id, *, now=None) -> Workflow:
    """Operator override: push a FAILED workflow back into the queue for another
    attempt (resets the current step to queued; the provider still dedupes)."""
    now = time.time() if now is None else now
    wf = store.load(workflow_id, user_id=user_id)
    if wf is None or wf.state != states.FAILED:
        return wf
    step = wf.current_step()
    if step and step.status == states.STEP_FAILED:
        step.status = states.STEP_QUEUED
    wf.last_error = None
    wf.next_run_at = now
    _transition(wf, store, states.QUEUED, "admin force-retry")
    metrics.event(logging.INFO, wf.id, wf.current_index, wf.provider, "force_retry")
    return wf


def force_complete(store, user_id, workflow_id) -> Workflow:
    """Operator override: mark a live/failed workflow COMPLETED and stop it.
    An always-legal, out-of-band halt (like a reply), so it bypasses the table."""
    wf = store.load(workflow_id, user_id=user_id)
    if wf is None or states.is_terminal(wf.state):
        return wf
    for s in wf.steps:
        if s.status in (states.STEP_PENDING, states.STEP_QUEUED):
            s.status = states.STEP_SKIPPED
    wf.next_run_at = None
    prev = wf.state
    wf.state = states.COMPLETED
    wf.touch()
    store.save(wf)
    store.add_event(wf.id, "completed", f"admin force-complete from {prev}")
    metrics.incr("workflows_completed")
    metrics.event(logging.INFO, wf.id, wf.current_index, wf.provider, "force_complete")
    return wf


# ── Recovery ───────────────────────────────────────────────────────────
def recover(store, *, now=None) -> int:
    """Repair workflows caught mid-flight by a crash. A step left in SENDING is
    re-queued for a retry (the provider dedupes on the idempotency key, so no
    double-send). Returns the number of workflows repaired."""
    now = time.time() if now is None else now
    repaired = 0
    # A step left in SENDING means a crash between "send in flight" and recording
    # the result. Re-queue it; the provider dedupes on the idempotency key.
    for wf in store.workflows_in_state(states.SENDING):
        step = wf.current_step()
        if step and step.status == states.STEP_SENDING:
            step.status = states.STEP_QUEUED
        wf.state = states.QUEUED
        wf.next_run_at = now
        store.save(wf)
        store.add_event(wf.id, "recovered", "re-queued after crash")
        record_event("automation", "recovered", user_id=wf.user_id, workflow_id=wf.id)
        repaired += 1
    return repaired


# ── Helpers ────────────────────────────────────────────────────────────
def _provider_for(wf, credentials_provider):
    creds = None
    if credentials_provider is not None:
        try:
            creds = credentials_provider(wf.user_id, wf.provider)
        except Exception:  # noqa: BLE001
            creds = None
    return get_provider(wf.provider, credentials=creds)


def _transition(wf, store, dst, detail=""):
    """Apply and persist a state transition, validating it against the machine."""
    if wf.state != dst:
        states.assert_transition(wf.state, dst)
        wf.state = dst
    wf.touch()
    store.save(wf)
    if detail:
        store.add_event(wf.id, dst.lower(), detail)
    return wf


# ── Observability ──────────────────────────────────────────────────────
def status(wf) -> dict:
    """A safe, user-facing view of a workflow (no secrets, no raw provider data)."""
    step = wf.current_step()
    nxt = wf.steps[wf.current_index + 1] if wf.current_index + 1 < len(wf.steps) else None
    return {
        "id": wf.id,
        "state": wf.state,
        "company": wf.company,
        "provider": wf.provider,
        "to": wf.to_email,
        "current_step": wf.current_index,
        "total_steps": len(wf.steps),
        "next_step": (nxt.index if nxt else None),
        "next_run_at": wf.next_run_at,
        "reply_detected": wf.reply_detected,
        "retry_count": (step.retry_count if step else 0),
        "last_error": wf.last_error,
        "last_execution": wf.updated_at,
        "steps": [{"index": s.index, "status": s.status, "subject": s.subject,
                   "sent_at": s.sent_at, "provider_message_id": s.provider_message_id,
                   "retry_count": s.retry_count} for s in wf.steps],
    }
