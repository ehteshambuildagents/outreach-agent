"""Deliverability & Cost Guard Agent.

Its ONLY job is to protect the user before an email is sent or an AI workflow
runs: it inspects, scores, warns, blocks, and recommends. It never writes,
rewrites, researches, changes strategy, or executes automation — it only
evaluates risk and returns a decision.

    guard.assess(input) -> {
        "decision": "ALLOW|WARN|BLOCK",
        "overallRisk": 0-100,
        "cost":          {"risk", "issues", "recommendations"},
        "deliverability":{"risk", "issues", "recommendations"},
    }

Fully deterministic (no model call): the same input always yields the same
verdict. When information is missing it errs toward the SAFER outcome.
"""

from guard.engine import assess

__all__ = ["assess"]
