"""Live production context for the guard — read-only assembly from real state.

Turns the durable application state (a user's workflow + send history, held in
Postgres/SQLite behind the EXISTING ``WorkflowStore``) into the guard's input dict,
so the guard scores *real* deliverability signals instead of thread-provided
guesses:

  * whether THIS recipient already replied or bounced in a prior send (so we never
    re-contact them — a hard block),
  * the mailbox's real reply / bounce rate and how many were actually sent today,
  * duplicate recipients in a batch,
  * copy that repeats what was genuinely sent before (template-blast detection).

It reuses ``WorkflowStore.list_for_user`` — no new repository, no new query — and
is strictly READ-ONLY and side-effect free: it never writes, sends, researches, or
executes anything. It only gathers inputs; the guard alone scores them.

Cost is now LIVE (integrated 2026-07-07): the ``usage`` section (real daily &
monthly AI spend) comes from the telemetry query service — ``telemetry.query`` —
compared against the configured budgets, so the Cost Guard warns/blocks on real
production spend. The richer telemetry (tokens, latency, failure/retry rate,
campaign spend) is attached as an INFORMATIONAL ``telemetry`` block that the guard
does not score, so decisions stay conservative.

Deliberately NOT synthesised here (see the integration report):
  * per-run retry / queue / duplicate-worker counters as guard BLOCK inputs — the
    only counters that exist are process-global (``automation.metrics``) or rates,
    not per-run/per-user, so feeding them as run-scoped values would risk FALSE
    blocks. Queue/worker health is surfaced in the informational block only.

Omitting a section is safe: the guard evaluates only the sections it's given and
treats absence conservatively, so accuracy improves without ever inventing a value.
Everything here is READ-ONLY and NON-BLOCKING: a telemetry hiccup omits the
section (with a reason) rather than failing the guard check.
"""

import logging
import os
import time
from datetime import datetime, timezone

from automation import states

log = logging.getLogger("guard.context")

# Bound how many prior bodies we diff against (most-recent first) so a large send
# history can't make a guard check slow. 20 is plenty to catch a repeated template.
_MAX_PRIOR_BODIES = 20
# Below a handful of sends, reply/bounce "rates" are noise — don't report them.
_MIN_SENDS_FOR_RATES = 5


def _budget(env_name: str, default_attr: str) -> float:
    """A configured AI budget (USD): env var wins, else config.settings, else 0.
    0 (or unset) disables that budget check — the guard then omits it (no block)."""
    raw = os.environ.get(env_name)
    if raw is None:
        try:
            from config import settings
            raw = getattr(settings, default_attr, 0)
        except Exception:  # noqa: BLE001
            raw = 0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def live_cost_context(user_id, *, campaign_id=None, now=None, db=None):
    """LIVE AI-cost sections from telemetry — reuses ``telemetry.query`` (no new
    cost logic). Returns ``(usage, telemetry_info, reason)``:

      * ``usage``  -> {daily_spend, monthly_spend, [daily_budget], [monthly_budget]}
        the Cost Guard scores this for budget warn/block.
      * ``telemetry_info`` -> informational only (tokens/latency/failure/retry/
        campaign spend/queue), NEVER scored — surfaced for logging/UI/report.
      * ``reason`` -> why a section was omitted (empty string when all is well).

    Read-only and non-blocking: any telemetry error yields ``(None, {}, reason)``
    so the guard check proceeds without a cost section rather than failing.
    """
    if not user_id:
        return None, {}, "no authenticated user"
    try:
        from telemetry import query as tq
    except Exception:  # noqa: BLE001
        return None, {}, "telemetry module unavailable"
    try:
        daily = float(tq.daily_spend(user_id, day=now, db=db))
        monthly = float(tq.monthly_spend(user_id, when=now, db=db))
    except Exception as exc:  # noqa: BLE001
        log.info("telemetry cost unavailable: %s", type(exc).__name__)
        return None, {}, f"telemetry query failed ({type(exc).__name__})"

    usage = {"daily_spend": round(daily, 6), "monthly_spend": round(monthly, 6)}
    daily_budget = _budget("GUARD_DAILY_BUDGET_USD", "GUARD_DAILY_BUDGET_USD")
    monthly_budget = _budget("GUARD_MONTHLY_BUDGET_USD", "GUARD_MONTHLY_BUDGET_USD")
    if daily_budget > 0:
        usage["daily_budget"] = daily_budget
    if monthly_budget > 0:
        usage["monthly_budget"] = monthly_budget

    info = {}
    try:
        info = {
            "total_tokens": tq.total_tokens(user_id, db=db),
            "avg_latency_ms": tq.avg_latency(db=db),
            "failure_rate": tq.failure_rate(db=db),
            "retry_rate": tq.retry_rate(db=db),
        }
        if campaign_id:
            info["campaign_spend"] = tq.campaign_cost(campaign_id, db=db)
        info["queue"] = tq.queue_health()
    except Exception:  # noqa: BLE001 - informational only, never fatal
        pass
    return usage, info, ""


def cost_summary(user_id, *, db=None) -> dict:
    """A one-call live cost/reliability snapshot for logging/UI/report. Thin
    passthrough over ``telemetry.query.summary`` (reuse, no new logic)."""
    try:
        from telemetry import query as tq
        return tq.summary(user_id, db=db)
    except Exception:  # noqa: BLE001
        return {}


def _norm(email) -> str:
    return (email or "").strip().lower()


def _day_start(now: float) -> float:
    return datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()


def _is_bounce(step) -> bool:
    return step.status == states.STEP_FAILED and "bounce" in (step.last_error or "").lower()


def build_context(user_id, *, email=None, sequence=None, workflow=None,
                  recipients=None, campaign_id=None, store=None, now=None) -> dict:
    """Assemble the guard's input from live, durable send history + telemetry.

    ``email`` is the draft under consideration ({subject, body, to}); ``workflow``
    (optional) is the current Workflow so we don't count it against itself.
    ``campaign_id`` (optional) surfaces that campaign's live spend. Returns a dict
    with only the sections we could source from real data. Read-only.
    """
    now = time.time() if now is None else now
    ctx = {}
    if email:
        ctx["email"] = email
    if recipients is not None:
        ctx["recipients"] = recipients

    if not user_id:
        # No authenticated owner -> nothing live to add; caller input passes through.
        if sequence:
            ctx["sequence"] = sequence
        return ctx

    # ── LIVE AI cost/budget from telemetry (drives the Cost Guard) ──────
    # Independent of send history and fully non-blocking: a telemetry hiccup omits
    # the section (with a reason) rather than failing the guard check.
    usage, tele_info, reason = live_cost_context(
        user_id, campaign_id=campaign_id, now=now)
    if usage:
        ctx["usage"] = usage
    if tele_info:
        ctx["telemetry"] = tele_info            # informational; the guard never scores it
    elif reason:
        ctx.setdefault("telemetry", {})["cost_omitted"] = reason

    from automation.store import WorkflowStore   # lazy: keep pure guard.assess light
    store = store or WorkflowStore()
    workflows = store.list_for_user(user_id)      # reuse existing repository method

    current_id = getattr(workflow, "id", None)
    to = _norm((email or {}).get("to")) or _norm(getattr(workflow, "to_email", None))

    # ── prospect send-state from real history (never re-contact) ──
    prospect = {}
    for wf in workflows:
        if current_id and wf.id == current_id:
            continue
        if to and _norm(wf.to_email) == to:
            # STOPPED only ever results from a detected reply, so it counts as
            # "replied" even for single-step sends whose reply arrived post-complete.
            if wf.reply_detected or wf.state == states.STOPPED:
                prospect["replied"] = True
            if any(_is_bounce(s) for s in wf.steps):
                prospect["bounced"] = True
    if prospect:
        ctx["prospect"] = prospect

    # ── mailbox reputation derived from real sends ──
    sent = [(wf, s) for wf in workflows for s in wf.steps if s.status == states.STEP_SENT]
    if sent:
        day0 = _day_start(now)
        mailbox = {"daily_volume": sum(1 for _wf, s in sent if (s.sent_at or 0) >= day0)}
        if len(sent) >= _MIN_SENDS_FOR_RATES:
            replies = sum(1 for wf in workflows if wf.reply_detected)
            bounces = sum(1 for wf in workflows for s in wf.steps if _is_bounce(s))
            mailbox["reply_rate"] = round(replies / len(sent), 4)
            mailbox["bounce_rate"] = round(bounces / len(sent), 4)
        ctx["mailbox"] = mailbox

    # ── repeated templates: what was ACTUALLY sent (to this recipient, else anyone) ──
    scoped = [(wf, s) for wf, s in sent if not to or _norm(wf.to_email) == to]
    pool = scoped if scoped else sent
    prior_bodies = [s.body for _wf, s in reversed(pool) if s.body][:_MAX_PRIOR_BODIES]
    if prior_bodies or sequence:
        seq = dict(sequence) if isinstance(sequence, dict) else {}
        merged = list(seq.get("prior_bodies") or []) + prior_bodies
        if merged:
            seq["prior_bodies"] = merged
        if seq:
            ctx["sequence"] = seq

    return ctx
