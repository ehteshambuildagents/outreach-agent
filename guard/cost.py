"""Part 1 — API Cost Guard.

Protects the user's AI budget: it estimates what a run will cost, compares that to
the remaining budget, and detects the runaway patterns that quietly burn money
(duplicate executions, retry loops, duplicate workers, an unhealthy queue). It
BLOCKS when money would clearly be wasted and WARNS as budget fills up.

Deterministic and read-only — it never runs a workflow or an AI call, it only
judges whether one should be allowed.
"""

from guard.models import Findings, as_bool, as_float, as_int, get

# Approximate blended prices in USD per 1M tokens (input+output averaged). Only
# used when the caller doesn't supply an explicit estimated cost.
_MODEL_PRICE_PER_MTOK = {
    "claude-opus": 30.0, "claude-sonnet": 6.0,
    "claude-haiku-4-5": 2.0, "claude-haiku": 1.5,
    "gpt-4o": 7.5, "gpt-4o-mini": 0.4, "gpt-4": 30.0, "default": 6.0,
}
_DEFAULT_TOKENS_PER_CALL = 4000        # when only a call count is known
_DEFAULT_CALL_COST = 0.03              # last-resort per-call estimate (USD)

# Thresholds (deterministic; conservative on the side of protecting budget).
_EXPENSIVE_ABS_USD = 5.0               # a single run this large is "unusually expensive"
_EXPENSIVE_FRACTION = 0.25             # …or more than this share of remaining budget
_LONG_CONTEXT_TOKENS = 50000           # a prompt/context this long is flagged
_EXPECTED_CALLS_PER_RECIPIENT = 5      # research+qualify+strategy+write+review ≈ 5
_DUPLICATE_WARN = 1                    # any duplicate call is worth a warning
_DUPLICATE_BLOCK = 3                   # this many duplicates = repeated execution
_INFINITE_RETRY = 25                   # this many retries = a loop
_FAILED_RETRY_BLOCK = 10               # this many *failed* retries = a loop


def _price(model: str) -> float:
    m = (model or "").lower()
    for key, price in _MODEL_PRICE_PER_MTOK.items():
        if key in m:
            return price
    return _MODEL_PRICE_PER_MTOK["default"]


def estimate_cost(campaign: dict):
    """Best-effort deterministic cost estimate (USD), or None if unknowable."""
    explicit = as_float(get(campaign, "est_cost"))
    if explicit is not None:
        return explicit
    model = get(campaign, "model", default="")
    tokens = as_int(get(campaign, "tokens"))
    if tokens:
        return round(tokens / 1_000_000 * _price(model), 4)
    calls = as_int(get(campaign, "ai_calls"))
    if calls:
        per = as_int(get(campaign, "avg_tokens_per_call")) or _DEFAULT_TOKENS_PER_CALL
        if model:
            return round(calls * per / 1_000_000 * _price(model), 4)
        return round(calls * _DEFAULT_CALL_COST, 4)
    return None


def _remaining(usage: dict):
    rems = []
    for spend_k, budget_k in (("daily_spend", "daily_budget"),
                              ("monthly_spend", "monthly_budget")):
        spend, budget = as_float(get(usage, spend_k)), as_float(get(usage, budget_k))
        if spend is not None and budget is not None:
            rems.append(budget - spend)
    return min(rems) if rems else None


def evaluate(inp: dict) -> Findings:
    f = Findings()
    usage = get(inp, "usage", default={}) or {}
    campaign = get(inp, "campaign", default={}) or {}
    execution = get(inp, "execution", default={}) or {}

    # ── budget usage (warn ladder: >50 / >80 / >95, block at/over 100) ──
    for label, spend_k, budget_k in (("Daily", "daily_spend", "daily_budget"),
                                     ("Monthly", "monthly_spend", "monthly_budget")):
        spend, budget = as_float(get(usage, spend_k)), as_float(get(usage, budget_k))
        if spend is None or budget is None or budget <= 0:
            continue
        pct = spend / budget
        if pct >= 1.0:
            f.block_now(f"{label} AI budget exceeded ({spend:.2f}/{budget:.2f}).",
                        "Stop AI runs until the budget resets or is raised.")
        elif pct >= 0.95:
            f.add(30, f"{label} budget over 95% used ({pct*100:.0f}%).",
                  "Pause non-essential AI runs; budget nearly gone.")
            f.warn_now(None)
        elif pct >= 0.80:
            f.add(18, f"{label} budget over 80% used ({pct*100:.0f}%).",
                  "Slow down AI runs; budget is running low.")
            f.warn_now(None)
        elif pct >= 0.50:
            # Half the budget used is informational, not an alarm on every send —
            # it raises the score but doesn't force a WARN decision by itself.
            f.add(8, f"{label} budget over 50% used ({pct*100:.0f}%).")

    # ── this run's cost vs remaining budget ──
    est = estimate_cost(campaign)
    remaining = _remaining(usage)
    if est is not None and remaining is not None:
        if est > remaining:
            f.block_now(
                f"Estimated run cost ${est:.2f} exceeds remaining budget ${remaining:.2f}.",
                "Reduce the batch size or raise the budget before running.")
        elif remaining > 0 and est > _EXPENSIVE_FRACTION * remaining:
            f.add(14, f"Run would consume {est/remaining*100:.0f}% of remaining budget.",
                  "Consider a smaller batch to preserve budget headroom.")
    if est is not None and est > _EXPENSIVE_ABS_USD:
        f.add(10, f"Unusually expensive run (~${est:.2f}).",
              "Confirm the batch size and model are intentional.")

    # ── too many / duplicate AI calls ──
    calls = as_int(get(campaign, "ai_calls"))
    recipients = as_int(get(campaign, "recipients"))
    if calls and recipients and recipients > 0:
        if calls > recipients * _EXPECTED_CALLS_PER_RECIPIENT:
            f.add(16, f"{calls} AI calls for {recipients} prospects looks excessive.",
                  "Check for repeated research/writer/qualification work; reuse cached results.")
    dup = as_int(get(execution, "duplicate_calls")) or 0
    if dup >= _DUPLICATE_BLOCK:
        f.block_now(f"Repeated duplicate execution detected ({dup} duplicate calls).",
                    "De-duplicate the job; a step is being executed more than once.")
    elif dup >= _DUPLICATE_WARN:
        f.add(12, f"{dup} duplicate AI call(s) detected.",
              "Cache/reuse prior results instead of regenerating.")

    # ── retry loops ──
    retries = as_int(get(execution, "retries")) or 0
    failed = as_int(get(execution, "failed_retries")) or 0
    max_retries = as_int(get(execution, "max_retries"))
    if as_bool(get(execution, "retry_loop"), False):
        f.block_now("Infinite retry loop detected.",
                    "Stop the job and fix the failing step before retrying.")
    elif retries >= _INFINITE_RETRY or failed >= _FAILED_RETRY_BLOCK or (
            max_retries and retries > max_retries * 3):
        f.block_now(f"Runaway retries ({retries} tries, {failed} failed).",
                    "Halt retries; this is looping, not recovering.")
    elif failed > 0:
        f.add(6, f"{failed} failed retr{'y' if failed == 1 else 'ies'} so far.")

    # ── background workers / queue health ──
    workers = as_int(get(execution, "duplicate_workers"))
    if workers and workers > 1:
        f.block_now(f"Duplicate background worker detected ({workers} running).",
                    "Run a single worker; duplicates double every send and cost.")
    if as_bool(get(execution, "queue_healthy"), True) is False:
        f.block_now("Job queue is unhealthy.",
                    "Recover the queue before enqueuing more work.")
    worker_failures = as_int(get(execution, "worker_failures")) or 0
    if worker_failures > 0:
        f.add(6, f"{worker_failures} worker failure(s) recently.",
              "Investigate worker stability.")

    # ── long context / token bloat ──
    ctx = as_int(get(campaign, "avg_tokens_per_call")) or as_int(get(campaign, "max_context_tokens"))
    if ctx and ctx > _LONG_CONTEXT_TOKENS:
        f.add(8, f"Unusually large context (~{ctx} tokens/call).",
              "Trim the prompt/context or use a cheaper model for this step.")

    return f
