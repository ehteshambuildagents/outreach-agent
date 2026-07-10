"""The guard's decision engine — combine cost + deliverability into one verdict.

Returns exactly the contract the spec defines:

    {
      "decision": "ALLOW | WARN | BLOCK",
      "overallRisk": 0-100,
      "cost":          {"risk", "issues", "recommendations"},
      "deliverability":{"risk", "issues", "recommendations"},
    }

Rules honoured: never rewrite, never research, never execute — only evaluate; and
when uncertain, choose the safer outcome (any hard block in either guard blocks
the whole run; two moderate risks compound rather than cancel).
"""

from guard import cost, deliverability
from guard.models import ALLOW, BLOCK, WARN, clamp, risk_level

# A section at/above this score forces at least a WARN even with no hard block.
_WARN_SCORE = 25
_WARN_OVERALL = 40
# CRITICAL overall risk (>=80) blocks even without a specific hard-block flag —
# "extremely dangerous" copy/behaviour must never be sent (safer outcome wins).
_BLOCK_OVERALL = 80


def _section(findings) -> dict:
    return {
        "risk": risk_level(clamp(findings.score)),
        "issues": list(findings.issues),
        "recommendations": list(findings.recommendations),
    }


def assess(inp: dict) -> dict:
    inp = inp or {}
    cost_f = cost.evaluate(inp)
    deliv_f = deliverability.evaluate(inp)

    cs, ds = clamp(cost_f.score), clamp(deliv_f.score)
    # Compound: the larger risk plus a quarter of the smaller, so two medium
    # problems don't read as "fine".
    overall = clamp(max(cs, ds) + 0.25 * min(cs, ds))

    if cost_f.block or deliv_f.block:
        decision = BLOCK
        overall = max(overall, 85)
    elif overall >= _BLOCK_OVERALL:
        decision = BLOCK          # CRITICAL risk — do not send, even absent a hard flag
    elif (overall >= _WARN_OVERALL or cost_f.warn or deliv_f.warn
          or cs >= _WARN_SCORE or ds >= _WARN_SCORE):
        decision = WARN
    else:
        decision = ALLOW

    return {
        "decision": decision,
        "overallRisk": overall,
        "cost": _section(cost_f),
        "deliverability": _section(deliv_f),
    }
